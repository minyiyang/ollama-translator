import tempfile
from pathlib import Path
from unittest.mock import patch

from book_agent.config import AppConfig
from book_agent.hashing import sha256_text
from book_agent.text_edits import apply_edit, apply_revert
from book_agent.web.text_view import text_chapter, text_outline
from tests.test_compile_stages import CompileStageTests, _retarget_pipeline_text


class TextOutlineTests:
    def test_unavailable_before_a_translation_exists(self):
        from book_agent.stages.decompile import run_decompile_stage
        from book_agent.stages.preprocess import run_preprocessing_stage
        from book_agent.workspace import create_job_workspace
        from tests.epub_fixture import make_epub
        from tests.test_preprocess_stage import publish_approved_glossary

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(source, base / "runs", config, job_id="fixture")
            run_decompile_stage(workspace)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)

            outline = text_outline(workspace)
            assert outline == {
                "available": False,
                "editable": False,
                "chapters": [],
                "totals": {
                    "documents": 0,
                    "segments": 0,
                    "flagged": 0,
                    "in_review_queue": 0,
                    "edited": 0,
                    "conflicts": 0,
                },
                "uncompiled_edit_count": 0,
            }

    def test_outline_and_chapter_after_a_clean_validated_draft(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)

            outline = text_outline(workspace)
            assert outline["available"]
            assert outline["editable"]
            assert outline["uncompiled_edit_count"] == 0
            assert len(outline["chapters"]) == 1
            chapter = outline["chapters"][0]
            assert chapter["title"] == "Chapter One"
            assert chapter["segment_count"] == outline["totals"]["segments"] > 0
            assert chapter["edited_count"] == 0
            assert chapter["conflict_count"] == 0

            detail = text_chapter(workspace, chapter["document_id"])
            assert detail["title"] == "Chapter One"
            assert len(detail["segments"]) == chapter["segment_count"]
            for segment in detail["segments"]:
                assert segment["state"] == "pipeline"
                assert segment["edit_revision"] == ""
                assert segment["text"] == segment["pipeline_text"]
                assert segment["base_target_sha256"] == sha256_text(segment["pipeline_text"])
                assert segment["source"]
                assert segment["pipeline_text"]
                assert segment["last_edit"] is None

    def test_unresolved_segments_are_flagged_and_queued(self):
        config = AppConfig.model_validate(
            {"audit": {"semantic_sample_every": 2}, "workflow": {"max_retries": 0}}
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )

            outline = text_outline(workspace)
            assert outline["editable"]
            assert outline["totals"]["in_review_queue"] > 0
            chapter = outline["chapters"][0]
            assert chapter["in_review_queue_count"] > 0

            detail = text_chapter(workspace, chapter["document_id"])
            queued = [s for s in detail["segments"] if s["in_review_queue"]]
            assert queued
            assert outline["totals"]["flagged"] <= outline["totals"]["in_review_queue"]

    def test_active_edit_clears_dynamic_flagged_state(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            chapter = text_outline(workspace)["chapters"][0]
            initial = text_chapter(workspace, chapter["document_id"])
            candidate = initial["segments"][0]
            finding = {
                candidate["segment_id"]: [
                    {"category": "verification", "severity": "high", "message": "Review it."}
                ]
            }
            with patch("book_agent.web.text_view._document_findings", return_value=finding):
                flagged = text_chapter(workspace, chapter["document_id"])["segments"][0]
                assert flagged["flagged"]
                assert text_outline(workspace)["totals"]["flagged"] == 1
            apply_edit(
                workspace,
                segment_id=candidate["segment_id"],
                text="第一章",
                reason="Reviewed this flagged segment.",
                base_target_sha256=candidate["base_target_sha256"],
                expected_event_id=candidate["edit_revision"],
            )

            with patch("book_agent.web.text_view._document_findings", return_value=finding):
                resolved = text_chapter(workspace, chapter["document_id"])["segments"][0]
                assert not resolved["flagged"]
                assert text_outline(workspace)["totals"]["flagged"] == 0

    def test_edited_segment_is_reflected_in_outline_and_chapter(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            chapter = text_outline(workspace)["chapters"][0]
            detail = text_chapter(workspace, chapter["document_id"])
            assert detail["editable"] is True
            segment = next(s for s in detail["segments"] if s["source"] == "Chapter One")

            apply_edit(
                workspace,
                segment_id=segment["segment_id"],
                text="第一章",
                reason="Testing the edit overlay.",
                base_target_sha256=segment["base_target_sha256"],
            )

            outline = text_outline(workspace)
            chapter = outline["chapters"][0]
            assert chapter["edited_count"] == 1
            assert chapter["conflict_count"] == 0
            assert outline["totals"]["edited"] == 1

            detail = text_chapter(workspace, chapter["document_id"])
            edited = next(s for s in detail["segments"] if s["segment_id"] == segment["segment_id"])
            assert edited["state"] == "edited"
            assert edited["edit_revision"] == "E000001"
            assert edited["text"] == "第一章"
            assert edited["pipeline_text"] == segment["pipeline_text"]
            assert edited["last_edit"]["reason"] == "Testing the edit overlay."
            assert edited["last_edit"]["action"] == "edit"

    def test_conflict_when_pipeline_text_changes_under_an_edit(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            chapter = text_outline(workspace)["chapters"][0]
            detail = text_chapter(workspace, chapter["document_id"])
            segment = next(s for s in detail["segments"] if s["source"] == "Chapter One")

            apply_edit(
                workspace,
                segment_id=segment["segment_id"],
                text="第一章",
                reason="Testing conflict detection.",
                base_target_sha256=segment["base_target_sha256"],
            )
            _retarget_pipeline_text(workspace, segment["segment_id"], "全新翻译。")

            outline = text_outline(workspace)
            chapter = outline["chapters"][0]
            assert chapter["conflict_count"] == 1
            assert chapter["edited_count"] == 0
            assert outline["totals"]["conflicts"] == 1

            detail = text_chapter(workspace, chapter["document_id"])
            conflict = next(s for s in detail["segments"] if s["segment_id"] == segment["segment_id"])
            assert conflict["state"] == "conflict"
            assert conflict["text"] == "第一章"
            assert conflict["pipeline_text"] == "全新翻译。"
            # The three-way conflict view (docs/FULL_TEXT_REVIEW.md section 8) needs
            # the text the edit was based on, distinct from the new pipeline text.
            assert conflict["last_edit"]["based_on"] == segment["pipeline_text"]
            assert conflict["last_edit"]["based_on"] != conflict["pipeline_text"]

    def test_conflict_base_is_pipeline_text_after_multiple_manual_edits(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            chapter = text_outline(workspace)["chapters"][0]
            segment = next(
                item
                for item in text_chapter(workspace, chapter["document_id"])["segments"]
                if item["source"] == "Chapter One"
            )
            first = apply_edit(
                workspace,
                segment_id=segment["segment_id"],
                text="第一章初稿",
                reason="First manual wording.",
                base_target_sha256=segment["base_target_sha256"],
                expected_event_id="",
            )
            apply_edit(
                workspace,
                segment_id=segment["segment_id"],
                text="第一章定稿",
                reason="Second manual wording.",
                base_target_sha256=segment["base_target_sha256"],
                expected_event_id=first.event_id,
            )
            _retarget_pipeline_text(workspace, segment["segment_id"], "全新翻译。")

            conflict = next(
                item
                for item in text_chapter(workspace, chapter["document_id"])["segments"]
                if item["segment_id"] == segment["segment_id"]
            )
            assert conflict["last_edit"]["based_on"] == segment["pipeline_text"]
            assert conflict["last_edit"]["based_on"] != "第一章初稿"

    def test_uncompiled_edit_count_tracks_compile_against_the_edit_log(self):
        from book_agent.stages.compile import run_epub_compile_stage

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            # No compile yet: never show the badge, even with edits pending.
            chapter = text_outline(workspace)["chapters"][0]
            detail = text_chapter(workspace, chapter["document_id"])
            segment = next(s for s in detail["segments"] if s["source"] == "Chapter One")
            apply_edit(
                workspace,
                segment_id=segment["segment_id"],
                text="第一章",
                reason="Testing the uncompiled-edit badge.",
                base_target_sha256=segment["base_target_sha256"],
            )
            assert text_outline(workspace)["uncompiled_edit_count"] == 0

            # A compile that includes this edit clears the badge.
            run_epub_compile_stage(workspace, config)
            outline = text_outline(workspace)
            assert outline["uncompiled_edit_count"] == 0

            # A further edit after that compile raises the badge again.
            other = next(s for s in detail["segments"] if s["source"] == "Nested paragraph.")
            apply_edit(
                workspace,
                segment_id=other["segment_id"],
                text="另一段。",
                reason="A second edit after the compile.",
                base_target_sha256=other["base_target_sha256"],
            )
            outline = text_outline(workspace)
            assert outline["uncompiled_edit_count"] == 1

            run_epub_compile_stage(workspace, config)
            assert text_outline(workspace)["uncompiled_edit_count"] == 0

            latest = next(
                item
                for item in text_chapter(workspace, chapter["document_id"])["segments"]
                if item["segment_id"] == other["segment_id"]
            )
            apply_revert(
                workspace,
                segment_id=other["segment_id"],
                reason="Reverting after compilation.",
                expected_event_id=latest["edit_revision"],
            )
            assert text_outline(workspace)["uncompiled_edit_count"] == 1

    def test_chapter_rejects_an_unknown_document(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            try:
                text_chapter(workspace, "does-not-exist")
                assert False, "expected ValueError"
            except ValueError:
                pass
