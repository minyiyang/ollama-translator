import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from book_agent.glossary import GlossarySource, GlossarySourceKind
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredGenerationResult,
)
from book_agent.schemas import GlossaryCategory, GlossaryEntry, GlossaryResult
from book_agent.series_glossary import (
    SeriesGlossaryConflictChoice,
    SeriesGlossaryConflictChoiceSet,
    build_series_glossary,
    resolve_series_glossary_conflicts,
    synchronize_book_glossaries,
    write_series_book_overlays,
    write_series_glossary,
)


class _FakeConflictClient:
    def __init__(self, results: list[SeriesGlossaryConflictChoiceSet]) -> None:
        self.results = list(results)
        self.prompts: list[str] = []
        self.schemas: list[type] = []
        self.context_minimums: list[int | None] = []
        self.context_maximums: list[int | None] = []
        self.progress_labels: list[str] = []
        self.progress_events = []

    def report_progress(self, event) -> None:
        self.progress_events.append(event)

    def generate_structured(self, prompt: str, schema: type, **kwargs: Any):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self.context_minimums.append(kwargs.get("context_minimum"))
        self.context_maximums.append(kwargs.get("context_maximum"))
        self.progress_labels.append(kwargs.get("progress_label", ""))
        if not self.results:
            raise AssertionError("fake conflict result queue is empty")
        result = self.results.pop(0)
        validated = schema.model_validate(result.model_dump(mode="python"))
        return StructuredGenerationResult(
            value=validated,
            generation=GenerationResult(
                content=validated.model_dump_json(),
                thinking="",
                metrics=GenerationMetrics(done_reason="stop"),
            ),
        )


def _entry(
    english: str,
    chinese: str,
    *,
    category: GlossaryCategory = GlossaryCategory.PERSON,
    aliases: list[str] | None = None,
    note: str = "",
) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        category=category,
        aliases=aliases or [],
        note=note,
    )


def _source(name: str, *entries: GlossaryEntry) -> GlossarySource:
    return GlossarySource(name, GlossarySourceKind.SERIES, entries)


class SeriesGlossaryTests:
    def test_obfuscated_resolved_series_defers_generic_relevance(self) -> None:
        sources = [
            _source(
                "qel-01",
                _entry(
                    "qelm",
                    "\u5947\u5c14",
                    category=GlossaryCategory.TERM,
                ),
            ),
            _source(
                "qel-02",
                _entry(
                    "qelm",
                    "\u5947\u5c14",
                    category=GlossaryCategory.TERM,
                ),
            ),
        ]

        prepared = build_series_glossary(sources, defer_generic_terms=True)
        approved = build_series_glossary(sources)

        assert prepared.glossary.entries == []
        assert prepared.report.conflicts[0].reason == "relevance_review_required"
        assert [entry.english for entry in approved.glossary.entries] == ["qelm"]

    def test_obfuscated_series_canon_synchronizes_review_overlays(self) -> None:
        sources = [
            _source(
                "qel-01",
                _entry("Vrax", chr(0x6C83) + chr(0x62C9)),
                _entry("Nul", chr(0x7EBD) + chr(0x5C14)),
            ),
            _source(
                "qel-02",
                _entry("Vrax", chr(0x6C83) + chr(0x62C9)),
                _entry("Zor", chr(0x4F50) + chr(0x5C14)),
            ),
        ]
        build = build_series_glossary(sources)

        overlays, reports = synchronize_book_glossaries(sources, build.glossary)

        assert overlays["qel-01"].entries[0].english == "Nul"
        vrax = next(
            entry for entry in overlays["qel-01"].entries if entry.english == "Vrax"
        )
        assert vrax.evidence == ["series:qel-01", "series:qel-02"]
        assert reports[0].canonicalized_term_count == 1
        with tempfile.TemporaryDirectory() as directory:
            written, report_path = write_series_book_overlays(
                overlays, directory, reports
            )
            assert set(written) == {"qel-01", "qel-02"}
            assert report_path.is_file()
            assert json.loads(report_path.read_text(encoding="utf-8"))["book_count"] == 2

    def test_llm_conflict_option_is_variant_scoped_bounded_and_checkpointed(self) -> None:
        sources = [
            _source("qel-01", _entry("Vrax", "沃拉克斯", note="Qelm envoy")),
            _source("qel-02", _entry("Vrax", "弗拉克斯", note="Qelm envoy")),
        ]
        build = build_series_glossary(sources)
        invalid = SeriesGlossaryConflictChoiceSet(
            decisions=[
                SeriesGlossaryConflictChoice(
                    conflict_id="C00001",
                    selected_chinese="维拉克斯",
                    rationale="unreported guess",
                )
            ]
        )
        valid = SeriesGlossaryConflictChoiceSet(
            decisions=[
                SeriesGlossaryConflictChoice(
                    conflict_id="C00001",
                    selected_chinese="沃拉克斯",
                    rationale="matches the recurring role note",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            checkpoints = Path(directory, "checkpoints")
            client = _FakeConflictClient([invalid, valid])
            resolved = resolve_series_glossary_conflicts(
                build,
                sources,
                client,
                model="qwen3.8:latest",
                checkpoint_directory=checkpoints,
            )

            assert resolved.glossary.entries[0].chinese == "沃拉克斯"
            assert resolved.report.conflict_count == 0
            assert resolved.report.llm_attempted_conflict_count == 1
            assert resolved.report.llm_resolved_conflict_count == 1
            assert len(client.prompts) == 2
            assert "listed conflict variant" in client.prompts[1]
            assert client.context_minimums == client.context_maximums
            assert all(value <= 32_768 for value in client.context_maximums)
            assert checkpoints.joinpath("series-conflict-00001.json").is_file()

            resumed_client = _FakeConflictClient([])
            resumed = resolve_series_glossary_conflicts(
                build,
                sources,
                resumed_client,
                model="qwen3.8:latest",
                checkpoint_directory=checkpoints,
            )
            assert resumed.glossary == resolved.glossary
            assert resumed_client.prompts == []

    def test_llm_conflict_option_can_leave_ambiguous_term_unresolved(self) -> None:
        sources = [
            _source("qel-01", _entry("Vrax", "沃拉克斯")),
            _source("qel-02", _entry("Vrax", "弗拉克斯")),
        ]
        build = build_series_glossary(sources)
        client = _FakeConflictClient(
            [
                SeriesGlossaryConflictChoiceSet(
                    decisions=[
                        SeriesGlossaryConflictChoice(
                            conflict_id="C00001",
                            selected_chinese=None,
                            rationale="insufficient evidence",
                        )
                    ]
                )
            ]
        )
        resolved = resolve_series_glossary_conflicts(
            build, sources, client, model="qwen3.8:latest"
        )
        assert resolved.glossary.entries == []
        assert resolved.report.conflict_count == 1
        assert resolved.report.llm_resolved_conflict_count == 0

    def test_llm_conflict_option_splits_obfuscated_conflicts_into_small_batches(self) -> None:
        sources = [
            _source(
                "qel-01",
                _entry("Qelm", "奇尔"),
                _entry("Vrax", "沃拉克斯"),
            ),
            _source(
                "qel-02",
                _entry("Qelm", "卡姆"),
                _entry("Vrax", "弗拉克斯"),
            ),
        ]
        build = build_series_glossary(sources)
        client = _FakeConflictClient(
            [
                SeriesGlossaryConflictChoiceSet(
                    decisions=[
                        SeriesGlossaryConflictChoice(
                            conflict_id="C00001",
                            selected_chinese="奇尔",
                            rationale="canonical transliteration",
                        )
                    ]
                ),
                SeriesGlossaryConflictChoiceSet(
                    decisions=[
                        SeriesGlossaryConflictChoice(
                            conflict_id="C00002",
                            selected_chinese="沃拉克斯",
                            rationale="canonical transliteration",
                        )
                    ]
                ),
            ]
        )
        resolved = resolve_series_glossary_conflicts(
            build,
            sources,
            client,
            model="qwen3.8:latest",
            chunk_tokens=1,
        )
        assert len(client.prompts) == 2
        assert len(resolved.glossary.entries) == 2
        assert (all(
                "batch=" in label and "mode=conflict" in label
                for label in client.progress_labels
            ))

    def test_obfuscated_series_conflicts_retry_omitted_id_and_restore_terms(self) -> None:
        sources = [
            _source("qel-01", _entry("Qelm", "奇尔"), _entry("Vrax", "沃拉克斯")),
            _source("qel-02", _entry("Qelm", "卡姆"), _entry("Vrax", "弗拉克斯")),
        ]
        build = build_series_glossary(sources)
        incomplete = SeriesGlossaryConflictChoiceSet(
            decisions=[
                SeriesGlossaryConflictChoice(
                    conflict_id="C00001",
                    selected_chinese="奇尔",
                    rationale="跨卷一致",
                )
            ]
        )
        complete = SeriesGlossaryConflictChoiceSet(
            decisions=[
                SeriesGlossaryConflictChoice(
                    conflict_id="C00001",
                    selected_chinese="奇尔",
                    rationale="跨卷一致",
                ),
                SeriesGlossaryConflictChoice(
                    conflict_id="C00002",
                    selected_chinese="沃拉克斯",
                    rationale="跨卷一致",
                ),
            ]
        )
        client = _FakeConflictClient([incomplete, complete])

        resolved = resolve_series_glossary_conflicts(
            build, sources, client, model="qwen3.8:latest"
        )

        assert len(client.prompts) == 2
        assert "every supplied conflict_id" in client.prompts[1]
        assert [entry.english for entry in resolved.glossary.entries] == ["Qelm", "Vrax"]
        assert '"english"' not in json.dumps(client.schemas[0].model_json_schema())

    def test_obfuscated_series_conflicts_stop_repeated_incomplete_response(self) -> None:
        sources = [
            _source("qel-01", _entry("Qelm", "奇尔"), _entry("Vrax", "沃拉克斯")),
            _source("qel-02", _entry("Qelm", "卡姆"), _entry("Vrax", "弗拉克斯")),
        ]
        build = build_series_glossary(sources)
        incomplete = SeriesGlossaryConflictChoiceSet(
            decisions=[
                SeriesGlossaryConflictChoice(
                    conflict_id="C00001",
                    selected_chinese="奇尔",
                    rationale="证据不足",
                )
            ]
        )
        client = _FakeConflictClient([incomplete, incomplete, incomplete, incomplete])

        with pytest.raises(ValueError, match="repeated an identical invalid structured response"):
            resolve_series_glossary_conflicts(
                build, sources, client, model="qwen3.8:latest"
            )

        assert len(client.prompts) == 2

    def test_promotes_only_recurring_terms_with_exact_consensus(self) -> None:
        build = build_series_glossary(
            [
                _source(
                    "qel-01",
                    _entry("Vrax", "沃拉克斯"),
                    _entry("Zor Beacon", "佐尔信标", category=GlossaryCategory.TERM),
                ),
                _source("qel-02", _entry("vrax", "沃拉克斯")),
            ]
        )

        assert [entry.english for entry in build.glossary.entries] == ["Vrax"]
        assert build.report.recurring_term_count == 1
        assert build.report.excluded_below_minimum_count == 1
        assert build.glossary.entries[0].confidence == 1.0
        assert build.glossary.entries[0].evidence == ["series:qel-01", "series:qel-02"]

    def test_preserves_most_consistent_approved_sense_note(self) -> None:
        build = build_series_glossary(
            [
                _source("qel-01", _entry("Vrax", "沃拉克斯", note="Zor rank title")),
                _source("qel-02", _entry("Vrax", "沃拉克斯", note="Zor rank title")),
                _source("qel-03", _entry("Vrax", "沃拉克斯", note="Nul speaker")),
            ]
        )

        assert build.glossary.entries[0].note == "Zor rank title"

    def test_excludes_translation_conflict_by_default(self) -> None:
        build = build_series_glossary(
            [
                _source("qel-01", _entry("Vrax", "沃拉克斯")),
                _source("qel-02", _entry("Vrax", "弗拉克斯")),
                _source("qel-03", _entry("Vrax", "沃拉克斯")),
            ]
        )

        assert build.glossary.entries == []
        assert build.report.conflict_count == 1
        assert build.report.conflicts[0].reason == "below_consensus"
        assert build.report.conflicts[0].source_count == 3

    def test_opt_in_majority_threshold_can_promote_two_of_three(self) -> None:
        build = build_series_glossary(
            [
                _source("qel-01", _entry("Vrax", "沃拉克斯")),
                _source("qel-02", _entry("Vrax", "弗拉克斯")),
                _source("qel-03", _entry("Vrax", "沃拉克斯")),
            ],
            consensus_ratio=0.66,
        )

        assert build.glossary.entries[0].chinese == "沃拉克斯"
        assert build.glossary.entries[0].confidence == 0.666667

    def test_excludes_term_with_ambiguous_translation_inside_one_book(self) -> None:
        build = build_series_glossary(
            [
                _source(
                    "qel-01",
                    _entry("Vrax", "沃拉克斯"),
                    _entry("Vrax", "弗拉克斯"),
                ),
                _source("qel-02", _entry("Vrax", "沃拉克斯")),
            ]
        )

        assert build.glossary.entries == []
        assert build.report.conflicts[0].reason == "ambiguous_source"

    def test_reports_category_disagreement_without_discarding_translation(self) -> None:
        build = build_series_glossary(
            [
                _source(
                    "qel-01",
                    _entry("Vrax", "沃拉克斯", category=GlossaryCategory.PERSON),
                ),
                _source(
                    "qel-02",
                    _entry("Vrax", "沃拉克斯", category=GlossaryCategory.TERM),
                ),
            ]
        )

        assert build.glossary.entries[0].category == GlossaryCategory.PERSON
        assert build.report.category_conflict_count == 1

    def test_removes_alias_that_collides_with_different_canonical_term(self) -> None:
        build = build_series_glossary(
            [
                _source(
                    "qel-01",
                    _entry("Vrax", "沃拉克斯", aliases=["Zor"]),
                    _entry("Zor", "佐尔"),
                ),
                _source(
                    "qel-02",
                    _entry("Vrax", "沃拉克斯", aliases=["Zor"]),
                    _entry("Zor", "佐尔"),
                ),
            ]
        )

        vrax = next(entry for entry in build.glossary.entries if entry.english == "Vrax")
        assert vrax.aliases == []

    def test_removes_shared_alias_with_different_targets(self) -> None:
        build = build_series_glossary(
            [
                _source(
                    "qel-01",
                    _entry("Vrax", "沃拉克斯", aliases=["Nul"]),
                    _entry("Zor", "佐尔", aliases=["Nul"]),
                ),
                _source(
                    "qel-02",
                    _entry("Vrax", "沃拉克斯", aliases=["Nul"]),
                    _entry("Zor", "佐尔", aliases=["Nul"]),
                ),
            ]
        )

        assert all("Nul" not in entry.aliases for entry in build.glossary.entries)

    def test_writes_canonical_report_and_legacy_artifacts(self) -> None:
        build = build_series_glossary(
            [
                _source("qel-01", _entry("Vrax", "沃拉克斯")),
                _source("qel-02", _entry("Vrax", "沃拉克斯")),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            output, report, legacy = write_series_glossary(
                build,
                Path(directory, "qel-series.json"),
            )

            parsed = GlossaryResult.model_validate_json(output.read_text(encoding="utf-8"))
            assert parsed.entries[0].english == "Vrax"
            assert json.loads(report.read_text(encoding="utf-8"))["conflict_count"] == 0
            assert "Vrax:沃拉克斯" in legacy.read_text(encoding="utf-8")

            with pytest.raises(ValueError, match="distinct"):
                write_series_glossary(
                    build,
                    Path(directory, "same.json"),
                    report_path=Path(directory, "same.json"),
                )

