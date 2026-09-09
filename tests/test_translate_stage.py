import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.languages import TranslationDirection
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredGenerationResult,
    StructuredOutputError,
)
from book_agent.pipeline_state import WorkflowStage
from book_agent.preprocessing import PreprocessedDocument
from book_agent.schemas import GlossaryCategory, GlossaryEntry
from book_agent.stages.audit import load_document_audits, run_translation_audit_stage
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.translate import (
    _build_chunk_prompt,
    _chunk_relevant_glossary,
    load_translated_documents,
    load_translation_report,
    run_translation_stage,
)
from book_agent.state import (
    connect_state,
    get_stage_status,
    get_work_unit,
    list_attempts,
    list_validations,
)
from book_agent.translation import (
    InlineMarkerPlacement,
    InlineMarkerPlacementResult,
    InlineMarkerSelection,
    InlineMarkerSelectionResult,
    SingleInlineMarkerSelection,
    SingleInlineMarkerSelectionResult,
    TranslationOutputError,
    TranslationChunk,
    TranslationChunkPiece,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import CHAPTER, make_epub
from tests.test_preprocess_stage import publish_approved_glossary


class FakeTranslationClient:
    def __init__(
        self,
        *,
        invalid_calls=0,
        omit_inline_calls=0,
        truncate_calls=0,
        translated_text="译文。",
    ):
        self.invalid_calls = invalid_calls
        self.omit_inline_calls = omit_inline_calls
        self.truncate_calls = truncate_calls
        self.translated_text = translated_text
        self.prompts = []
        self.thinking = []
        self.models = []
        self.progress_labels = []
        self.context_maximums = []
        self.context_minimums = []
        self.context_multipliers = []
        self.progress_events = []

    def report_progress(self, event):
        self.progress_events.append(event)

    def generate_text(
        self, prompt, *, stream=True, think=None, model=None, progress_label="",
        context_minimum=None, context_maximum=None, context_multiplier=None
    ):
        self.prompts.append(prompt)
        self.thinking.append(think)
        self.models.append(model)
        self.progress_labels.append(progress_label)
        self.context_minimums.append(context_minimum)
        self.context_maximums.append(context_maximum)
        self.context_multipliers.append(context_multiplier)
        if "Style-harmonization task:" in prompt:
            content = prompt.split("Fallback draft:\n", 1)[1]
        elif len(self.prompts) <= self.invalid_calls:
            content = "invalid model response"
        else:
            source = prompt.split("Source:\n", 1)[1]
            pieces = re.findall(
                r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", source, flags=re.DOTALL
            )
            rendered = []
            for item, source_text in pieces:
                inline = re.findall(r"</?I\d{3}>", source_text)
                translated = self.translated_text
                if inline:
                    first_close = next(
                        (index for index, marker in enumerate(inline) if marker.startswith("</")),
                        len(inline),
                    )
                    translated = (
                        "".join(inline[:first_close])
                        + self.translated_text
                        + "".join(inline[first_close:])
                    )
                rendered.append(f"<{item}>{translated}</{item}>")
            content = "\n".join(rendered)
            if len(self.prompts) <= self.truncate_calls:
                last_id = pieces[-1][0]
                content = "\n".join(rendered[:-1]) + f"\n<{last_id}>译文未完"
            if len(self.prompts) <= self.omit_inline_calls:
                content = content.replace("<I000>", "", 1).replace("</I000>", "", 1)
        return GenerationResult(
            content=content,
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20),
        )


class MarkerPlacementClient(FakeTranslationClient):
    def __init__(self):
        super().__init__(omit_inline_calls=1)
        self.placement_prompts = []
        self.placement_max_attempts = []

    def generate_structured(
        self, prompt, schema, *, think=None, model=None, progress_label="",
        context_minimum=None, context_maximum=None, context_multiplier=None,
        max_attempts=None
    ):
        self.placement_prompts.append(prompt)
        self.placement_max_attempts.append(max_attempts)
        self.context_minimums.append(context_minimum)
        self.context_maximums.append(context_maximum)
        self.context_multipliers.append(context_multiplier)
        placements = []
        if schema is InlineMarkerSelectionResult:
            selection_pattern = re.compile(
                r"Reference: (?P<id>[^\n]+)\n"
                r"Marker ID: (?P<marker>I\d{3})\n"
                r"Source emphasized text: .*?\n"
                r"Source context: .*?\n"
                r"Immutable translation: (?P<translation>.*?)"
                r"(?=\n\nReference:|\Z)",
                flags=re.DOTALL,
            )
            for match in selection_pattern.finditer(prompt):
                placements.append(
                    InlineMarkerSelection(
                        reference_id=match.group("id"),
                        marker_id=match.group("marker"),
                        emphasized=match.group("translation"),
                        occurrence=1,
                    )
                )
            value = schema(selections=placements)
            return StructuredGenerationResult(
                value=value,
                generation=GenerationResult(
                    content=value.model_dump_json(),
                    thinking="",
                    metrics=GenerationMetrics(prompt_eval_count=50, eval_count=10),
                ),
            )
        if schema is SingleInlineMarkerSelectionResult:
            single_pattern = re.compile(
                r"Reference: (?P<id>[^\n]+)\n"
                r"Source emphasized text: .*?\n"
                r"Source context: .*?\n"
                r"Immutable translation: (?P<translation>.*?)"
                r"(?=\n\nReference:|\Z)",
                flags=re.DOTALL,
            )
            for match in single_pattern.finditer(prompt):
                placements.append(
                    SingleInlineMarkerSelection(
                        reference_id=match.group("id"),
                        emphasized=match.group("translation"),
                        occurrence=1,
                    )
                )
            value = schema(selections=placements)
            return StructuredGenerationResult(
                value=value,
                generation=GenerationResult(
                    content=value.model_dump_json(),
                    thinking="",
                    metrics=GenerationMetrics(prompt_eval_count=50, eval_count=10),
                ),
            )
        pattern = re.compile(
            r"Reference: (?P<id>[^\n]+)\n"
            r"Source context: .*?\n"
            r"Immutable translation: (?P<translation>.*?)\n"
            r"Required marker IDs in order: (?P<markers>.*?)\n",
            flags=re.DOTALL,
        )
        for match in pattern.finditer(prompt):
            marker_ids = [
                item.strip() for item in match.group("markers").split(",") if item.strip()
            ]
            parts = [""] * (len(marker_ids) * 2 + 1)
            parts[1] = match.group("translation")
            placements.append(
                InlineMarkerPlacement(
                    reference_id=match.group("id"),
                    parts=parts,
                )
            )
        value = schema(placements=placements)
        return StructuredGenerationResult(
            value=value,
            generation=GenerationResult(
                content=value.model_dump_json(),
                thinking="",
                metrics=GenerationMetrics(prompt_eval_count=50, eval_count=10),
            ),
        )


class ExactSpanFallbackClient(MarkerPlacementClient):
    def __init__(self):
        super().__init__()
        self.schemas = []

    def generate_structured(self, prompt, schema, **kwargs):
        self.schemas.append(schema)
        if schema is InlineMarkerSelectionResult:
            self.placement_prompts.append(prompt)
            self.placement_max_attempts.append(kwargs.get("max_attempts"))
            raise StructuredOutputError("obfuscated exact-span shape mismatch")
        return super().generate_structured(prompt, schema, **kwargs)


class RepeatedMarkerFailureClient(FakeTranslationClient):
    def __init__(self):
        super().__init__(omit_inline_calls=10)
        self.structured_calls = 0

    def generate_structured(self, prompt, schema, **kwargs):
        self.structured_calls += 1
        raise StructuredOutputError("obfuscated marker selection mismatch")


class TranslateStageTests:
    def test_adjacent_boundary_context_is_read_only_and_unmarked(self):
        document = PreprocessedDocument.model_validate(
            {
                "order": 1,
                "manifest_id": "chapter.xhtml",
                "archive_path": "chapter.xhtml",
                "source_sha256": "1" * 64,
                "segments": [
                    {
                        "segment_id": "D0001-S000001",
                        "original_text": "previous source",
                        "processed_text": "previous source",
                        "occurrences": [],
                    },
                    {
                        "segment_id": "D0001-S000002",
                        "original_text": "target source",
                        "processed_text": "target source",
                        "occurrences": [],
                    },
                    {
                        "segment_id": "D0001-S000003",
                        "original_text": "next source",
                        "processed_text": "next source",
                        "occurrences": [],
                    },
                ],
                "relevant_glossary": [],
            }
        )
        chunk = TranslationChunk(
            chunk_id="translate-0001-00001",
            document_id=document.manifest_id,
            document_order=document.order,
            pieces=[
                TranslationChunkPiece(
                    reference_id="D0001-S000002",
                    segment_id="D0001-S000002",
                    part_number=1,
                    source_text="target source",
                )
            ],
            estimated_source_tokens=3,
        )
        config = AppConfig.model_validate(
            {"translation": {"boundary_context": "adjacent-read-only"}}
        )

        prompt = _build_chunk_prompt(document, chunk, [], config)

        target, context = prompt.split("Read-only boundary context follows.", 1)
        assert "<D0001-S000002>target source</D0001-S000002>" in target
        assert "previous source" in context
        assert "next source" in context
        assert "<D0001-S000001>" not in context
        assert "<D0001-S000003>" not in context
        assert "no boundary-context marker" in context

    def test_chunk_glossary_excludes_unrelated_and_case_mismatched_terms(self):
        glossary = [
            GlossaryEntry(
                english="VRX",
                chinese="\u7ef4\u5c14\u514b\u65af",
                category=GlossaryCategory.TECHNOLOGY,
            ),
            GlossaryEntry(
                english="qel drive",
                chinese="\u51ef\u5c14\u9a71\u52a8",
                category=GlossaryCategory.TECHNOLOGY,
            ),
            GlossaryEntry(
                english="Nul Harbor",
                chinese="\u52aa\u5c14\u6e2f",
                category=GlossaryCategory.PLACE,
            ),
        ]
        document = PreprocessedDocument(
            order=1,
            manifest_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            source_sha256="a" * 64,
            segments=[],
            relevant_glossary=glossary,
        )
        chunk = TranslationChunk(
            chunk_id="translate-obfuscated",
            document_id="obfuscated",
            document_order=1,
            pieces=[
                TranslationChunkPiece(
                    reference_id="D0001-S000001",
                    segment_id="D0001-S000001",
                    part_number=1,
                    source_text="His vrx arm reached the Qel drive.",
                )
            ],
            estimated_source_tokens=12,
        )
        config = AppConfig.model_validate(
            {"translation": {"direction": TranslationDirection.EN_TO_ZH.value}}
        )
        selected = _chunk_relevant_glossary(document, chunk, config)
        assert [item.english for item in selected] == ["qel drive"]

    def prepare_workspace(self, base, *, config=None, chapter=CHAPTER):
        config = config or AppConfig()
        epub = make_epub(base / "fixture.epub", chapter=chapter)
        workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        return workspace

    def test_chinese_to_english_stage_uses_reverse_direction(self):
        chapter = CHAPTER.replace(
            b"Hello <em>small</em> world.",
            "阿斯特遇见了<em>石鹭</em>。".encode("utf-8"),
        )
        config = AppConfig.model_validate({"translation": {"direction": "zh-en"}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(
                Path(directory), config=config, chapter=chapter
            )
            client = FakeTranslationClient(translated_text="Translated passage.")
            report = run_translation_stage(workspace, config, client)
            assert report.direction.value == "zh-en"
            assert "from Simplified Chinese into English" in client.prompts[0]
            assert "polished literary prose" in client.prompts[0]
            assert "literary Chinese" not in client.prompts[0]
            documents = load_translated_documents(workspace)
            assert all("Translated passage." in item.translated_text for item in documents[0].segments)

    def test_stage_translates_checkpoints_loads_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient()
            first = run_translation_stage(workspace, AppConfig(), client)
            second = run_translation_stage(workspace, AppConfig(), client)
            assert first == second
            assert first.document_count == 1
            assert first.segment_count == 4
            assert first.chunk_count == 1
            assert first.generation_attempts == 1
            assert len(client.prompts) == 1
            assert client.thinking == [False]
            assert client.models == ["qwen3.8:latest"]
            assert client.context_maximums == [16_384]
            assert client.context_minimums == [16_384]
            assert client.context_multipliers == [3.0]
            assert "Naturalness overlay" in client.prompts[0]
            documents = load_translated_documents(workspace)
            assert len(documents) == 1
            assert len(documents[0].segments) == 4
            assert (all(
                    client.translated_text in item.translated_text
                    for item in documents[0].segments
                ))
            assert load_translation_report(workspace) == first
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, WorkflowStage.TRANSLATE.value)
                assert stage["status"] == "completed"
                unit = get_work_unit(connection, "translate-0000-00001", "translate")
                assert unit["validation"]["passed"]
                assert len(list_attempts(connection, "translate-0000-00001")) == 1
                assert list_validations(connection, "translate-0000-00001")[0]["passed"]
            finally:
                connection.close()

    def test_obfuscated_translation_prescan_groups_contexts_and_restores_order(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Zelm</title></head>
<body><p>Vrax nel tor quist zorb kelm drav.</p>
<p>Yorn vek tal prax lim dor fen.</p><p>Qist bar nom zel tur vim qax.</p></body></html>"""
        config = AppConfig.model_validate({"budget": {"source_tokens": 10}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(
                Path(directory), config=config, chapter=chapter
            )
            client = FakeTranslationClient(translated_text="泽尔译文。")
            planned = []

            def assign_bucket(*_args, **_kwargs):
                planned.append(len(planned))
                return 32_768 if len(planned) == 1 else 16_384

            with patch(
                "book_agent.stages.translate.request_context_bucket",
                side_effect=assign_bucket,
            ):
                report = run_translation_stage(workspace, config, client)

            assert report.chunk_count > 1
            assert "id=translate-0000-00001" not in client.progress_labels[0]
            assert "id=translate-0000-00001" in client.progress_labels[-1]
            assert client.context_minimums == sorted(client.context_minimums)
            assert client.context_minimums == client.context_maximums
            documents = load_translated_documents(workspace)
            assert [item.order for item in documents] == [0]
            assert [segment.segment_id for segment in documents[0].segments] == sorted(segment.segment_id for segment in documents[0].segments)
            plans = [event for event in client.progress_events if event.kind == "stage_plan"]
            assert len(plans) == 1
            assert f"llm_tasks={report.chunk_count}" in plans[0].message

    def test_stage_retries_rejected_marker_output_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(invalid_calls=1)
            report = run_translation_stage(workspace, AppConfig(), client)
            assert report.generation_attempts == 2
            assert len(client.prompts) == 2
            assert "previous response was rejected" in client.prompts[1]
            assert client.models == ["qwen3.8:latest", "qwen3.8:latest"]
            connection = connect_state(workspace.state_file)
            try:
                attempts = list_attempts(connection, "translate-0000-00001")
                assert [item["status"] for item in attempts] == ["failed", "completed"]
            finally:
                connection.close()

    def test_stage_retries_only_passages_with_missing_inline_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(omit_inline_calls=1)
            report = run_translation_stage(workspace, AppConfig(), client)
            assert report.generation_attempts == 2
            first_source = client.prompts[0].split("Source:\n", 1)[1]
            retry_source = client.prompts[1].split("Source:\n", 1)[1]
            first_ids = re.findall(r"<(D[A-Za-z0-9_-]+)>", first_source)
            retry_ids = re.findall(r"<(D[A-Za-z0-9_-]+)>", retry_source)
            assert len(first_ids) > 1
            assert len(retry_ids) == 1
            assert "<I000>" in retry_source
            assert client.models == ["qwen3.8:latest", "qwen3.8:latest"]
            assert "chunk=1/1 id=translate-0000-00001" in client.progress_labels[0]
            assert "attempt=1/4 mode=full" in client.progress_labels[0]
            assert "chunk=1/1 id=translate-0000-00001" in client.progress_labels[1]
            assert "attempt=2/4 mode=focused passages=1" in client.progress_labels[1]
            assert "after=protected_marker_mismatch" in client.progress_labels[1]
            failures = [
                event
                for event in client.progress_events
                if event.kind == "validation_failed"
            ]
            assert len(failures) == 1
            assert "retrying" in failures[0].message

    def test_stage_retries_only_unfinished_passages_after_truncated_output(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(truncate_calls=1)
            report = run_translation_stage(workspace, AppConfig(), client)
            assert report.generation_attempts == 2
            first_source = client.prompts[0].split("Source:\n", 1)[1]
            retry_source = client.prompts[1].split("Source:\n", 1)[1]
            first_ids = re.findall(r"<(D[A-Za-z0-9_-]+)>", first_source)
            retry_ids = re.findall(r"<(D[A-Za-z0-9_-]+)>", retry_source)
            assert len(first_ids) > 1
            assert retry_ids == [first_ids[-1]]
            assert "mode=focused passages=1" in client.progress_labels[1]
            assert "after=marker_contract" in client.progress_labels[1]

    def test_retry_reuses_longest_safe_prefix_from_saved_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            first_config = AppConfig.model_validate(
                {
                    "workflow": {
                        "max_retries": 0,
                        "defer_failed_translation_segments": False,
                    }
                }
            )
            with pytest.raises(TranslationOutputError):
                run_translation_stage(
                    workspace,
                    first_config,
                    FakeTranslationClient(truncate_calls=1),
                )

            retry_config = AppConfig.model_validate(
                {"workflow": {"max_retries": 1}}
            )
            retry_client = FakeTranslationClient()
            report = run_translation_stage(workspace, retry_config, retry_client)
            assert report.generation_attempts == 1
            assert len(retry_client.prompts) == 1
            retry_source = retry_client.prompts[0].split("Source:\n", 1)[1]
            retry_ids = re.findall(r"<(D[A-Za-z0-9_-]+)>", retry_source)
            assert len(retry_ids) == 1

    def test_stage_repairs_missing_markers_with_structured_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = MarkerPlacementClient()
            report = run_translation_stage(workspace, AppConfig(), client)
            assert report.generation_attempts == 2
            assert len(client.prompts) == 1
            assert len(client.placement_prompts) == 1
            assert client.placement_max_attempts == [1]
            assert "Immutable translation" in client.placement_prompts[0]
            documents = load_translated_documents(workspace)
            marked = [
                segment.translated_text
                for segment in documents[0].segments
                if "<I000>" in segment.source_text
            ]
            assert marked
            assert all("<I000>" in text for text in marked)

    def test_stage_falls_back_when_obfuscated_exact_span_shape_is_rejected(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Z</title></head>
<body><p><em>Vrax nel</em> tor <strong>quist zorb</strong>.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), chapter=chapter)
            client = ExactSpanFallbackClient()

            report = run_translation_stage(workspace, AppConfig(), client)

            assert report.deferred_segment_count == 0
            assert client.schemas == [InlineMarkerSelectionResult, InlineMarkerPlacementResult]
            assert client.placement_max_attempts == [1, 1]
            assert "exact-span selection task" in client.placement_prompts[0]
            assert "Required parts count" in client.placement_prompts[1]
            translated = load_translated_documents(workspace)[0].segments[0]
            assert re.findall(r"</?I\d{3}>", translated.translated_text) == ["<I000>", "</I000>", "<I001>", "</I001>"]

    def test_stage_retries_obfuscated_marker_repair_after_focused_retry(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Z</title></head>
<body><p>Vrax <em>nel tor</em> quist.</p></body></html>"""
        config = AppConfig.model_validate(
            {
                "workflow": {
                    "max_retries": 1,
                    "defer_failed_translation_segments": False,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(
                Path(directory), config=config, chapter=chapter
            )
            client = RepeatedMarkerFailureClient()

            with pytest.raises(TranslationOutputError):
                run_translation_stage(workspace, config, client)

            assert client.structured_calls == 2
            assert len(client.prompts) == 2

    def test_stage_repairs_obfuscated_nested_markers_with_structured_placement(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Z</title></head>
<body><p><em>Vrax <strong>nel</strong></em> tor.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), chapter=chapter)
            client = MarkerPlacementClient()

            report = run_translation_stage(workspace, AppConfig(), client)

            assert report.generation_attempts == 2
            assert len(client.prompts) == 1
            assert len(client.placement_prompts) == 1
            assert "Required marker sequence: <I000>, <I001>, </I001>, </I000>" in client.placement_prompts[0]
            marked = (
                load_translated_documents(workspace)[0].segments[0].translated_text
            )
            assert "<I000>" in marked
            assert "<I001>" in marked
            assert marked.index("<I000>") < marked.index("<I001>")
            assert marked.index("</I001>") < marked.index("</I000>")

    def test_stage_exact_span_repair_ignores_valid_empty_markers_obfuscated(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Z</title></head>
<body><p>Vrax <em>nel</em> tor.</p><p><em></em>Quist zorb.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), chapter=chapter)
            client = MarkerPlacementClient()

            report = run_translation_stage(workspace, AppConfig(), client)

            assert report.generation_attempts == 2
            assert len(client.prompts) == 1
            assert len(client.placement_prompts) == 1
            assert "exact-span selection task" in client.placement_prompts[0]
            assert "Required parts count" not in client.placement_prompts[0]
            documents = load_translated_documents(workspace)
            marked = {
                segment.source_text: segment.translated_text
                for segment in documents[0].segments
                if "<I000>" in segment.source_text
            }
            assert "Vrax <I000>nel</I000> tor." in marked
            assert "<I000></I000>Quist zorb." in marked
            assert "<I000>" in marked["Vrax <I000>nel</I000> tor."]
            assert "<I000>" in marked["<I000></I000>Quist zorb."]

    def test_stage_restores_obfuscated_empty_nested_marker_without_llm(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Z</title></head>
<body><p><em>Vrax <strong></strong> nel tor.</em></p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), chapter=chapter)
            client = MarkerPlacementClient()

            report = run_translation_stage(workspace, AppConfig(), client)

            assert report.generation_attempts == 1
            assert len(client.prompts) == 1
            assert client.placement_prompts == []
            restoration_events = [
                event
                for event in client.progress_events
                if event.kind == "segment_result"
                and "marker-restoration=deterministic" in event.label
            ]
            assert len(restoration_events) == 1
            assert "result=succeeded" in restoration_events[0].message
            marked = load_translated_documents(workspace)[0].segments[0].translated_text
            assert re.findall(r"</?I\d{3}>", marked) == ["<I000>", "<I001>", "</I001>", "</I000>"]

    def test_stage_failure_is_persisted_after_bounded_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(invalid_calls=10)
            config = AppConfig.model_validate(
                {
                    "workflow": {
                        "max_retries": 1,
                        "defer_failed_translation_segments": False,
                    }
                }
            )
            with pytest.raises(TranslationOutputError):
                run_translation_stage(workspace, config, client)
            assert len(client.prompts) == 2
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "translate")
                unit = get_work_unit(connection, "translate-0000-00001", "translate")
                assert stage["status"] == "failed"
                assert unit["status"] == "failed"
                assert unit["attempts"] == 2
            finally:
                connection.close()

    def test_failed_primary_attempts_fall_back_and_preserve_raw_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(invalid_calls=10)
            config = AppConfig.model_validate(
                {
                    "translation": {"fallback_models": ["gemma4:31b"]},
                    "workflow": {
                        "max_retries": 3,
                        "defer_failed_translation_segments": False,
                    },
                }
            )
            with pytest.raises(TranslationOutputError):
                run_translation_stage(workspace, config, client)
            assert client.models == ["qwen3.8:latest", "qwen3.8:latest", "gemma4:31b", "gemma4:31b"]
            attempt_files = list((workspace.root / "translated").glob("*/attempts/*.txt"))
            assert len(attempt_files) == 4
            assert any("gemma4-31b" in path.name for path in attempt_files)

    def test_exhausted_obfuscated_passage_is_deferred_and_later_chunks_continue(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Zelm</title></head>
<body><p>Vrax nel tor quist zorb kelm drav.</p>
<p>Yorn vek tal prax lim dor fen.</p><p>Qist bar nom zel tur vim qax.</p></body></html>"""
        config = AppConfig.model_validate(
            {
                "budget": {"source_tokens": 10},
                "workflow": {"max_retries": 0},
                "audit": {"semantic_enabled": False},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(
                Path(directory), config=config, chapter=chapter
            )
            client = FakeTranslationClient(
                invalid_calls=1,
                translated_text="\u8bd1\u6587\u3002",
            )

            report = run_translation_stage(workspace, config, client)

            assert report.chunk_count > 1
            assert report.deferred_chunk_count > 0
            assert report.deferred_chunk_count < report.chunk_count
            assert report.deferred_segment_count > 0
            assert len(client.prompts) > 1
            assert (any(
                    event.kind == "segment_result"
                    and "result=pending-repair" in event.message
                    for event in client.progress_events
                ))
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "translate")
                unit = get_work_unit(connection, "translate-0000-00001", "translate")
                assert stage["status"] == "completed"
                assert unit["status"] == "completed"
                assert not unit["validation"]["passed"]
            finally:
                connection.close()

            audit = run_translation_audit_stage(workspace, config)
            assert not audit.passed
            deferred_findings = [
                issue
                for document in load_document_audits(workspace)
                for issue in document.issues
                if issue.source == "translation-deferred"
            ]
            assert {issue.segment_id for issue in deferred_findings} == set(report.deferred_segment_ids)

    def test_valid_fallback_is_optionally_harmonized_by_primary_model(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory))
            client = FakeTranslationClient(invalid_calls=2)
            config = AppConfig.model_validate(
                {
                    "translation": {"fallback_models": ["gemma4:31b"]},
                    "workflow": {"max_retries": 2},
                }
            )
            report = run_translation_stage(workspace, config, client)
            assert client.models == ["qwen3.8:latest", "qwen3.8:latest", "gemma4:31b", "qwen3.8:latest"]
            assert client.thinking == [False, False, False, False]
            assert report.generation_attempts == 4
            attempt_files = list((workspace.root / "translated").glob("*/attempts/*.txt"))
            assert any("harmonized-qwen3-8-latest" in path.name for path in attempt_files)
            connection = connect_state(workspace.state_file)
            try:
                harmonization = list_attempts(
                    connection, "translate-0000-00001:harmonize"
                )
                assert harmonization[0]["status"] == "completed"
                assert harmonization[0]["metrics"]["purpose"] == "fallback_style_harmonization"
            finally:
                connection.close()

    def test_stage_requires_preprocessing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="fixture")
            with pytest.raises(RuntimeError, match="preprocess"):
                run_translation_stage(workspace, AppConfig(), FakeTranslationClient())

