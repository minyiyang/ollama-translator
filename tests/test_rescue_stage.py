import tempfile
from pathlib import Path

from book_agent.config import AppConfig
from book_agent.stages.audit import run_translation_audit_stage
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.rescue import (
    load_rescue_report,
    run_translation_rescue_stage,
)
from book_agent.stages.translate import (
    load_translated_documents,
    run_translation_stage,
)
from book_agent.state import connect_state, get_stage_status
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_translate_stage import FakeTranslationClient


MULTI_CHUNK_CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Zelm</title></head>
<body><p>Vrax nel tor quist zorb kelm drav.</p>
<p>Yorn vek tal prax lim dor fen.</p><p>Qist bar nom zel tur vim qax.</p></body></html>"""


class RescueStageTests:
    def prepare(self, base, config, chapter=MULTI_CHUNK_CHAPTER):
        epub = make_epub(base / "fixture.epub", chapter=chapter)
        workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        return workspace

    def settings(self, **overrides):
        base = {
            "budget": {"source_tokens": 10},
            "workflow": {"max_retries": 0},
            "audit": {"semantic_enabled": False},
        }
        base.update(overrides)
        return base

    def test_rescue_is_a_no_op_without_a_configured_fallback(self) -> None:
        config = AppConfig.model_validate(self.settings())
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare(Path(directory), config)
            translation = run_translation_stage(
                workspace, config, FakeTranslationClient(invalid_calls=1)
            )
            assert translation.deferred_chunk_count > 0

            report = run_translation_rescue_stage(workspace, config, None)

            assert report.candidate_chunk_count == 0
            assert report.rescued_chunk_count == 0
            # The first-draft generation stays published for downstream stages.
            assert report.deferred_segment_count == translation.deferred_segment_count
            documents = load_translated_documents(workspace)
            assert len(documents) == translation.document_count
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "rescue_translation")
                assert stage["status"] == "completed"
            finally:
                connection.close()

    def test_rescue_redrafts_only_deferred_chunks_and_harmonizes_them(self) -> None:
        config = AppConfig.model_validate(
            self.settings(translation={"fallback_models": ["gemma4:31b"]})
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare(Path(directory), config)
            translation = run_translation_stage(
                workspace, config, FakeTranslationClient(invalid_calls=1)
            )
            assert translation.deferred_chunk_count == 1

            client = FakeTranslationClient()
            report = run_translation_rescue_stage(workspace, config, client)

            assert report.candidate_chunk_count == 1
            assert report.rescued_chunk_count == 1
            assert report.deferred_segment_count == 0
            assert report.harmonized_chunk_count == 1
            # The fallback model drafts, then the primary model restyles it: one
            # contiguous phase per model, and healthy chunks are never redrafted.
            assert client.models == ["gemma4:31b", "qwen3.8:latest"]
            documents = load_translated_documents(workspace)
            assert len(documents) == translation.document_count
            assert all(
                segment.translated_text.strip()
                for document in documents
                for segment in document.segments
            )

    def test_rescue_republishes_documents_for_downstream_stages(self) -> None:
        config = AppConfig.model_validate(
            self.settings(translation={"fallback_models": ["gemma4:31b"]})
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare(Path(directory), config)
            run_translation_stage(
                workspace, config, FakeTranslationClient(invalid_calls=1)
            )
            before = [
                segment.translated_text
                for document in load_translated_documents(workspace)
                for segment in document.segments
            ]
            assert any(not text.strip() or "Vrax" in text for text in before)

            run_translation_rescue_stage(workspace, config, FakeTranslationClient())

            after = [
                segment.translated_text
                for document in load_translated_documents(workspace)
                for segment in document.segments
            ]
            assert before != after
            assert not any("Vrax" in text for text in after)

    def test_rescue_skips_a_draft_the_audit_already_consumed(self) -> None:
        config = AppConfig.model_validate(
            self.settings(translation={"fallback_models": ["gemma4:31b"]})
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare(Path(directory), config)
            run_translation_stage(
                workspace, config, FakeTranslationClient(invalid_calls=1)
            )
            run_translation_audit_stage(workspace, config)

            client = FakeTranslationClient()
            report = run_translation_rescue_stage(workspace, config, client)

            # Swapping the draft under an audited book would strand the repairs
            # that were built from it, so the rescue stands down.
            assert client.models == []
            assert report.candidate_chunk_count == 0

    def test_completed_rescue_is_resumable_without_new_model_calls(self) -> None:
        config = AppConfig.model_validate(
            self.settings(translation={"fallback_models": ["gemma4:31b"]})
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare(Path(directory), config)
            run_translation_stage(
                workspace, config, FakeTranslationClient(invalid_calls=1)
            )
            first = run_translation_rescue_stage(
                workspace, config, FakeTranslationClient()
            )

            repeat_client = FakeTranslationClient()
            second = run_translation_rescue_stage(workspace, config, repeat_client)

            assert repeat_client.models == []
            assert first == second
            assert load_rescue_report(workspace) == first
