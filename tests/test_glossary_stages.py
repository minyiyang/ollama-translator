import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredGenerationResult,
)
from book_agent.schemas import (
    GlossaryApprovalAction,
    GlossaryApprovalDecision,
    GlossaryApprovalReport,
    GlossaryApprovalResult,
    GlossaryCategory,
    GlossaryEntry,
    GlossaryResolutionDecision,
    GlossaryResolutionResult,
    GlossaryResult,
    build_glossary_extraction_schema,
)
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.glossary import (
    GlossaryApprovalRequired,
    load_configured_glossary_sources,
    run_glossary_approval_stage,
    run_glossary_extraction_stage,
    run_glossary_resolution_stage,
)
from book_agent.state import (
    connect_state,
    get_job_metadata,
    get_stage_status,
    get_work_unit,
    list_attempts,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub


class FakeGlossaryClient:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.prompts: list[str] = []
        self.schemas: list[type] = []
        self.models: list[str | None] = []
        self.thinking: list[bool | None] = []
        self.context_minimums: list[int | None] = []
        self.context_maximums: list[int | None] = []
        self.context_multipliers: list[float | None] = []
        self.max_attempts: list[int | None] = []
        self.progress_labels: list[str] = []
        self.progress_events = []

    def report_progress(self, event) -> None:
        self.progress_events.append(event)

    def generate_structured(self, prompt: str, schema: type, **kwargs: Any) -> StructuredGenerationResult:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        self.models.append(kwargs.get("model"))
        self.thinking.append(kwargs.get("think"))
        self.context_minimums.append(kwargs.get("context_minimum"))
        self.context_maximums.append(kwargs.get("context_maximum"))
        self.context_multipliers.append(kwargs.get("context_multiplier"))
        self.max_attempts.append(kwargs.get("max_attempts"))
        self.progress_labels.append(kwargs.get("progress_label", ""))
        if not self.results:
            raise AssertionError("fake glossary result queue is empty")
        result = self.results.pop(0)
        return StructuredGenerationResult(
            value=result,
            generation=GenerationResult(
                content=result.model_dump_json(),
                thinking="",
                metrics=GenerationMetrics(
                    done_reason="stop", prompt_eval_count=100, eval_count=20
                ),
            ),
        )


def entry(
    english: str,
    chinese: str,
    evidence: list[str] | None = None,
    category: GlossaryCategory = GlossaryCategory.PERSON,
) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        note="测试",
        category=category,
        evidence=evidence or [],
    )


def resolution_result(
    *decisions: tuple[str, str, GlossaryCategory],
) -> GlossaryResolutionResult:
    return GlossaryResolutionResult(
        decisions=[
            GlossaryResolutionDecision(
                term_id=term_id,
                chinese=chinese,
                note="测试",
                category=category,
            )
            for term_id, chinese, category in decisions
        ]
    )


def approval_result(
    *decisions: tuple[str, GlossaryApprovalAction, str | None],
) -> GlossaryApprovalResult:
    return GlossaryApprovalResult(
        decisions=[
            GlossaryApprovalDecision(
                term_id=term_id,
                action=action,
                chinese=chinese,
                reason="测试审定",
            )
            for term_id, action, chinese in decisions
        ]
    )


class GlossaryStageTests:
    def make_workspace(self, base: Path):
        epub = make_epub(base / "fixture.epub")
        workspace = create_job_workspace(
            epub, base / "runs", AppConfig(), job_id="fixture"
        )
        run_decompile_stage(workspace)
        return workspace

    def test_extraction_stage_records_candidates_metrics_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            candidate = GlossaryResult(
                entries=[entry("Aster", "阿斯特", ["D0000-S000001"])]
            )
            client = FakeGlossaryClient([candidate])
            config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 100}}
            )
            first = run_glossary_extraction_stage(workspace, config, client)
            second = run_glossary_extraction_stage(workspace, config, client)
            assert first == second
            assert len(client.prompts) == 1
            assert client.models == ["qwen3.8:27b"]
            assert client.thinking == [False]
            assert client.context_minimums == [16_384]
            assert client.context_maximums == [16_384]
            assert client.context_multipliers == [2.0]
            assert client.max_attempts == [1]
            assert client.progress_labels == ["chunk=1/1 id=glossary-00001 attempt=1/4"]
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "extract_glossary")
                assert stage["status"] == "completed"
                unit = get_work_unit(connection, "glossary-00001", "extract_glossary")
                assert unit["validation"]["evidence"] == "passed"
                assert list_attempts(connection, "glossary-00001")[0]["metrics"]["eval_count"] == 20
            finally:
                connection.close()

    def test_disabled_extraction_reuses_configured_glossary_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            reviewed = base / "reviewed.json"
            reviewed.write_text(
                GlossaryResult(
                    entries=[entry("Aster", "阿斯特")]
                ).model_dump_json(indent=2),
                encoding="utf-8",
            )
            config = AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_enabled": False,
                        "book_glossaries": [str(reviewed)],
                    },
                    "workflow": {"require_glossary_review": False},
                }
            )

            candidates = run_glossary_extraction_stage(workspace, config, None)
            draft = run_glossary_resolution_stage(workspace, config, None)

            assert candidates.entries == []
            assert [(item.english, item.chinese) for item in draft.entries] == [("Aster", "阿斯特")]
            connection = connect_state(workspace.state_file)
            try:
                assert get_job_metadata(connection, "glossary_extraction_mode") == "disabled"
                assert get_stage_status(connection, "extract_glossary")["status"] == "completed"
            finally:
                connection.close()

    def test_obfuscated_extraction_prescan_groups_contexts_before_inference(self) -> None:
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Zelm</title></head>
<body><p>Vrax nel tor.</p><p>Quist zorb kelm.</p><p>Drav yorn vek.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "qelm.epub", chapter=chapter)
            workspace = create_job_workspace(
                epub, base / "runs", AppConfig(), job_id="qelm"
            )
            run_decompile_stage(workspace)
            config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 5}}
            )
            client = FakeGlossaryClient([GlossaryResult(entries=[])] * 20)
            planned = []

            def assign_bucket(*_args, **_kwargs):
                planned.append(len(planned))
                return 32_768 if len(planned) == 1 else 16_384

            with patch(
                "book_agent.stages.glossary.request_context_bucket",
                side_effect=assign_bucket,
            ):
                run_glossary_extraction_stage(workspace, config, client)

            assert len(client.progress_labels) > 1
            assert "id=glossary-00001" not in client.progress_labels[0]
            assert "id=glossary-00001" in client.progress_labels[-1]
            assert client.context_minimums == sorted(client.context_minimums)
            assert client.context_minimums == client.context_maximums
            plans = [event for event in client.progress_events if event.kind == "stage_plan"]
            assert len(plans) == 1
            assert "llm_tasks=" in plans[0].message

    def test_extraction_retries_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            invalid = GlossaryResult(entries=[entry("Aster", "阿斯特", ["UNKNOWN"])])
            valid = GlossaryResult(entries=[entry("Aster", "阿斯特", ["D0000-S000001"])])
            client = FakeGlossaryClient([invalid, valid])
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"max_retries": 1},
                }
            )
            result = run_glossary_extraction_stage(workspace, config, client)
            assert result.entries[0].english == "Aster"
            assert len(client.prompts) == 2
            assert "chunk=1/1" in client.progress_labels[0]
            assert "attempt=2/2" in client.progress_labels[1]
            connection = connect_state(workspace.state_file)
            try:
                assert [attempt["status"] for attempt in list_attempts(connection, "glossary-00001")] == ["failed", "completed"]
            finally:
                connection.close()

    def test_extraction_discards_only_invalid_provisional_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            extraction_schema = build_glossary_extraction_schema(
                200, 3, ["D0000-S000001"]
            )
            generated = extraction_schema.model_validate(
                {
                    "entries": [
                        {
                            "english": "Mira",
                            "chinese": "\u7c73\u62c9",
                            "note": "name",
                            "category": GlossaryCategory.PERSON,
                            "evidence": ["D0000-S000001"],
                        },
                        {
                            "english": "Riverstone",
                            "chinese": "Riverstone",
                            "note": "untranslated name",
                            "category": GlossaryCategory.PERSON,
                            "evidence": ["D0000-S000001"],
                        },
                    ]
                }
            )
            client = FakeGlossaryClient([generated])
            config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 100}}
            )
            result = run_glossary_extraction_stage(workspace, config, client)
            assert [item.english for item in result.entries] == ["Mira"]
            assert len(client.prompts) == 1
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(
                    connection, "glossary-00001", "extract_glossary"
                )
                assert unit["validation"]["discarded_invalid_entry_count"] == 1
                assert unit["validation"]["discarded_invalid_terms"] == ["Riverstone"]
            finally:
                connection.close()

    def test_changing_extraction_model_invalidates_and_reruns_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            first_result = GlossaryResult(
                entries=[entry("Aster", "\u7231\u4e3d\u4e1d", ["D0000-S000001"])]
            )
            second_result = GlossaryResult(
                entries=[entry("Aster", "\u827e\u4e3d\u4e1d", ["D0000-S000001"])]
            )
            first_client = FakeGlossaryClient([first_result])
            second_client = FakeGlossaryClient([second_result])
            base = {"glossary": {"extraction_chunk_tokens": 100}}
            run_glossary_extraction_stage(
                workspace, AppConfig.model_validate(base), first_client
            )
            changed = AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_chunk_tokens": 100,
                        "extraction_model": "gemma4:custom",
                    }
                }
            )
            result = run_glossary_extraction_stage(workspace, changed, second_client)
            assert result.entries[0].chinese == "\u827e\u4e3d\u4e1d"
            assert second_client.models == ["gemma4:custom"]

    def test_extraction_failure_marks_chunk_and_stage_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            invalid = GlossaryResult(entries=[entry("Aster", "阿斯特", ["UNKNOWN"])])
            client = FakeGlossaryClient([invalid])
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"max_retries": 0},
                }
            )
            with pytest.raises(Exception):
                run_glossary_extraction_stage(workspace, config, client)
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "extract_glossary")["status"] == "failed"
                assert get_work_unit(connection, "glossary-00001", "extract_glossary")["status"] == "failed"
            finally:
                connection.close()

    def test_resolution_applies_precedence_and_auto_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            extracted = GlossaryResult(
                entries=[
                    entry("Aster", "阿斯塔", ["D0000-S000001"]),
                    entry("Heron", "兔子", ["D0000-S000002"]),
                ]
            )
            extraction_client = FakeGlossaryClient([extracted])
            extraction_config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 100}}
            )
            run_glossary_extraction_stage(workspace, extraction_config, extraction_client)

            seed = base / "seed.txt"
            series = base / "series.txt"
            book = base / "book.txt"
            seed.write_text("--人名--\nAster:阿斯特拉:seed\n", encoding="utf-8")
            series.write_text("--人名--\nAster:阿斯特:series\n", encoding="utf-8")
            book.write_text("--人名--\nAster:阿斯提:book\n", encoding="utf-8")
            config = AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_chunk_tokens": 100,
                        "seed_glossaries": [str(seed)],
                        "series_glossaries": [str(series)],
                        "book_glossaries": [str(book)],
                    },
                    "workflow": {"require_glossary_review": False},
                }
            )
            resolution_client = FakeGlossaryClient(
                [
                    resolution_result(
                        ("T00001", "石鹭", GlossaryCategory.PERSON)
                    )
                ]
            )
            draft = run_glossary_resolution_stage(workspace, config, resolution_client)
            assert [(item.english, item.chinese) for item in draft.entries] == [("Aster", "阿斯提"), ("Heron", "石鹭")]
            assert '"english":"Aster"' not in resolution_client.prompts[0]
            assert resolution_client.models == ["qwen3.8:latest"]
            assert resolution_client.context_multipliers == [2.0]
            assert resolution_client.max_attempts == [1]
            assert resolution_client.context_minimums == [16_384]
            assert resolution_client.context_maximums == [16_384]
            assert "batch=1/1 id=glossary-resolution-local-00001 mode=local" in resolution_client.progress_labels[0]
            assert "attempt=1/4" in resolution_client.progress_labels[0]
            approved = run_glossary_approval_stage(workspace, config)
            assert approved == draft
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "approve_glossary")["status"] == "completed"
            finally:
                connection.close()

    def test_human_approval_pauses_then_accepts_reviewed_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            candidate = GlossaryResult(entries=[entry("Aster", "阿斯特", ["D0000-S000001"])])
            config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 100}}
            )
            run_glossary_extraction_stage(workspace, config, FakeGlossaryClient([candidate]))
            run_glossary_resolution_stage(
                workspace,
                config,
                FakeGlossaryClient(
                    [
                        resolution_result(
                            ("T00001", "阿斯特", GlossaryCategory.PERSON)
                        )
                    ]
                ),
            )
            with pytest.raises(GlossaryApprovalRequired):
                run_glossary_approval_stage(workspace, config)
            reviewed = base / "reviewed.txt"
            reviewed.write_text("--人名--\nAster:阿斯特:人工审定\n", encoding="utf-8")
            approved = run_glossary_approval_stage(
                workspace, config, reviewed_file=reviewed
            )
            assert approved.entries[0].note == "人工审定"

    def test_llm_approval_uses_qwen_reviews_scope_and_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"llm_glossary_review": True},
                }
            )
            candidate = GlossaryResult(
                entries=[entry("Qelwright", "奎尔赖特", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidate])
            )
            run_glossary_resolution_stage(
                workspace,
                config,
                FakeGlossaryClient(
                    [
                        GlossaryResolutionResult(
                            decisions=[
                                GlossaryResolutionDecision(
                                    term_id="T00001",
                                    chinese="奎尔赖特",
                                    note="测试",
                                    category=GlossaryCategory.PERSON,
                                    confidence=0.7,
                                )
                            ]
                        )
                    ]
                ),
            )
            reviewer = FakeGlossaryClient(
                [
                    approval_result(
                        ("A00001", GlossaryApprovalAction.REVISE, "奎尔莱特")
                    )
                ]
            )
            approved = run_glossary_approval_stage(
                workspace, config, client=reviewer
            )
            assert approved.entries[0].english == "Qelwright"
            assert approved.entries[0].chinese == "奎尔莱特"
            assert reviewer.models == ["qwen3.8:latest"]
            assert reviewer.thinking == [False]
            assert reviewer.context_minimums == [16_384]
            assert reviewer.context_maximums == [16_384]
            assert reviewer.context_multipliers == [2.0]
            assert reviewer.max_attempts == [1]
            assert "batch=1/1 id=glossary-llm-review-00001 mode=review" in reviewer.progress_labels[0]
            assert "attempt=1/4" in reviewer.progress_labels[0]
            assert "deltas only" in reviewer.prompts[0]
            assert '"term_id":"A00001"' in reviewer.prompts[0]
            assert "exactly one decision" in reviewer.prompts[0]
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "approve_glossary")["status"] == "completed"
                assert get_job_metadata(connection, "glossary_review_mode") == "llm"
                attempt = list_attempts(
                    connection, "glossary-llm-review-00001"
                )[0]
                assert attempt["status"] == "completed"
                report_path = workspace.directory(
                    get_job_metadata(connection, "glossary_approval_report")
                )
                report = GlossaryApprovalReport.model_validate_json(
                    report_path.read_text(encoding="utf-8")
                )
                assert report.revised_count == 1
                assert report.records[0].english == "Qelwright"
            finally:
                connection.close()

    def test_llm_approval_reviews_external_overlay_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {"glossary": {"extraction_chunk_tokens": 100}}
            )
            extracted = GlossaryResult(
                entries=[entry("Qel Spindle", "奎尔轴", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([extracted])
            )
            run_glossary_resolution_stage(
                workspace,
                config,
                FakeGlossaryClient(
                    [
                        GlossaryResolutionResult(
                            decisions=[
                                GlossaryResolutionDecision(
                                    term_id="T00001",
                                    chinese="奎尔轴",
                                    note="样例",
                                    category=GlossaryCategory.TECHNOLOGY,
                                    confidence=0.5,
                                )
                            ]
                        )
                    ]
                ),
            )
            overlay_entry = entry(
                "Qel Spindle",
                "奇尔纺轴",
                ["D0001-S000002"],
                GlossaryCategory.TECHNOLOGY,
            ).model_copy(update={"confidence": 0.5})
            overlay = base / "qel-volume.glossary.review.json"
            overlay.write_text(
                GlossaryResult(entries=[overlay_entry]).model_dump_json(indent=2),
                encoding="utf-8",
            )
            reviewer = FakeGlossaryClient(
                [
                    approval_result(
                        ("A00001", GlossaryApprovalAction.REVISE, "奇尔纺锤")
                    )
                ]
            )

            approved = run_glossary_approval_stage(
                workspace,
                config,
                reviewed_file=overlay,
                llm_review=True,
                client=reviewer,
            )

            assert approved.entries[0].chinese == "奇尔纺锤"
            assert approved.entries[0].evidence[0] == "D0001-S000002"
            assert "奇尔纺轴" in reviewer.prompts[0]
            assert "奎尔轴" not in reviewer.prompts[0]
            connection = connect_state(workspace.state_file)
            try:
                assert get_job_metadata(connection, "glossary_review_mode") == "llm"
                assert get_job_metadata(connection, "glossary_review_source") == "external"
            finally:
                connection.close()

    def test_obfuscated_llm_approval_uses_checkpointed_bounded_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_chunk_tokens": 100,
                        "approval_chunk_tokens": 1,
                    },
                    "workflow": {"llm_glossary_review": True},
                }
            )
            candidates = GlossaryResult(
                entries=[
                    entry("Qelm", "奇尔", ["D0000-S000001"]),
                    entry("Vrax", "弗拉克斯", ["D0000-S000002"]),
                ]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidates])
            )
            run_glossary_resolution_stage(
                workspace,
                config,
                FakeGlossaryClient(
                    [
                        GlossaryResolutionResult(
                            decisions=[
                                GlossaryResolutionDecision(
                                    term_id="T00001",
                                    chinese="奇尔",
                                    note="测试",
                                    category=GlossaryCategory.PERSON,
                                    confidence=0.7,
                                ),
                                GlossaryResolutionDecision(
                                    term_id="T00002",
                                    chinese="弗拉克斯",
                                    note="测试",
                                    category=GlossaryCategory.PERSON,
                                    confidence=0.7,
                                ),
                            ]
                        )
                    ]
                ),
            )
            reviewer = FakeGlossaryClient(
                [
                    approval_result(
                        ("A00001", GlossaryApprovalAction.APPROVE, None)
                    ),
                    approval_result(
                        ("A00002", GlossaryApprovalAction.REJECT, None)
                    ),
                ]
            )

            approved = run_glossary_approval_stage(
                workspace, config, client=reviewer
            )

            assert [item.english for item in approved.entries] == ["Qelm"]
            assert len(reviewer.prompts) == 2
            assert all(value <= 65_536 for value in reviewer.context_maximums)
            assert reviewer.context_minimums == reviewer.context_maximums
            assert all("mode=review" in label for label in reviewer.progress_labels)
            outcomes = [
                event
                for event in reviewer.progress_events
                if event.kind == "segment_result"
            ]
            assert any("result=rejected" in event.message for event in outcomes)

    def test_obfuscated_llm_approval_skips_credible_entries_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"llm_glossary_review": True},
                }
            )
            candidate = GlossaryResult(
                entries=[entry("Qelm Spindle", "奇尔姆纺锤", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidate])
            )
            run_glossary_resolution_stage(
                workspace,
                config,
                FakeGlossaryClient(
                    [
                        resolution_result(
                            ("T00001", "奇尔姆纺锤", GlossaryCategory.TECHNOLOGY)
                        )
                    ]
                ),
            )
            reviewer = FakeGlossaryClient([])

            approved = run_glossary_approval_stage(
                workspace, config, client=reviewer
            )

            assert approved.entries[0].english == "Qelm Spindle"
            assert len(reviewer.prompts) == 0
            plans = [
                event for event in reviewer.progress_events if event.kind == "stage_plan"
            ]
            assert len(plans) == 1
            assert "llm_tasks=0" in plans[0].message
            outcomes = [
                event
                for event in reviewer.progress_events
                if event.kind == "segment_result"
            ]
            assert len(outcomes) == 1
            assert "result=approved" in outcomes[0].message
            assert "mode=deterministic" in outcomes[0].message

    def test_obfuscated_llm_approval_stops_repeated_pending_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {
                        "llm_glossary_review": True,
                        "max_retries": 3,
                    },
                }
            )
            candidate = GlossaryResult(
                entries=[entry("Quist Engine", "奎斯特引擎", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidate])
            )
            uncertain = GlossaryResolutionResult(
                decisions=[
                    GlossaryResolutionDecision(
                        term_id="T00001",
                        chinese="奎斯特引擎",
                        note="测试",
                        category=GlossaryCategory.TECHNOLOGY,
                        confidence=0.5,
                    )
                ]
            )
            run_glossary_resolution_stage(
                workspace, config, FakeGlossaryClient([uncertain])
            )
            pending = approval_result(
                ("A00001", GlossaryApprovalAction.PENDING, None)
            )
            reviewer = FakeGlossaryClient([pending, pending, pending, pending])

            with pytest.raises(ValueError, match="repeated an identical invalid structured response"):
                run_glossary_approval_stage(workspace, config, client=reviewer)

            assert len(reviewer.prompts) == 2

    def test_load_configured_sources_returns_content_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "series.txt")
            path.write_text("--人名--\nAster:阿斯特:series\n", encoding="utf-8")
            config = AppConfig.model_validate(
                {"glossary": {"series_glossaries": [str(path)]}}
            )
            sources, hashes = load_configured_glossary_sources(config)
            assert len(sources) == 1
            assert sources[0].kind.value == "series"
            assert len(next(iter(hashes.values()))) == 64

    def test_resolution_retries_invented_term_id_with_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"max_retries": 1, "require_glossary_review": False},
                }
            )
            candidate = GlossaryResult(
                entries=[entry("Qelwright", "奎尔赖特", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidate])
            )
            client = FakeGlossaryClient(
                [
                    resolution_result(
                        ("T99999", "虚构", GlossaryCategory.PERSON)
                    ),
                    resolution_result(
                        ("T00001", "奎尔赖特", GlossaryCategory.PERSON)
                    ),
                ]
            )
            resolved = run_glossary_resolution_stage(workspace, config, client)
            assert resolved.entries[0].english == "Qelwright"
            assert len(client.prompts) == 2
            assert "previous response failed validation" in client.prompts[1]

    def test_obfuscated_resolution_stops_repeated_invalid_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {"extraction_chunk_tokens": 100},
                    "workflow": {"max_retries": 3, "require_glossary_review": False},
                }
            )
            candidate = GlossaryResult(
                entries=[entry("Vraxwright", "弗拉克斯赖特", ["D0000-S000001"])]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidate])
            )
            invalid = resolution_result(
                ("T99999", "虚构", GlossaryCategory.PERSON)
            )
            client = FakeGlossaryClient([invalid, invalid, invalid, invalid])

            with pytest.raises(ValueError, match="repeated an identical invalid structured response"):
                run_glossary_resolution_stage(workspace, config, client)

            assert len(client.prompts) == 2

    def test_obfuscated_resolution_runs_compact_conflict_only_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            config = AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_chunk_tokens": 100,
                        "resolution_chunk_tokens": 8_000,
                    },
                    "workflow": {"require_glossary_review": False},
                }
            )
            candidates = GlossaryResult(
                entries=[
                    entry("Qelm", "奇尔", ["D0000-S000001"]),
                    entry("Qelm", "卡姆", ["D0000-S000002"]),
                    entry("Vrax", "弗拉克斯", ["D0000-S000003"]),
                ]
            )
            run_glossary_extraction_stage(
                workspace, config, FakeGlossaryClient([candidates])
            )
            client = FakeGlossaryClient(
                [
                    resolution_result(
                        ("T00001", "奇尔", GlossaryCategory.PERSON),
                        ("T00001", "卡姆", GlossaryCategory.PERSON),
                        ("T00002", "弗拉克斯", GlossaryCategory.PERSON),
                    ),
                    resolution_result(
                        ("T00001", "奇尔", GlossaryCategory.PERSON)
                    ),
                ]
            )

            resolved = run_glossary_resolution_stage(workspace, config, client)

            assert [(item.english, item.chinese) for item in resolved.entries] == [("Qelm", "奇尔"), ("Vrax", "弗拉克斯")]
            assert len(client.prompts) == 2
            assert "remaining conflicting glossary entries" in client.prompts[1]
            assert "mode=local" in client.progress_labels[0]
            assert "mode=conflict" in client.progress_labels[1]
            assert all(value <= 32_768 for value in client.context_maximums)

