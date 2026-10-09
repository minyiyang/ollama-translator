import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.review_ui import WORKSHEET_NAME, ReviewSession
from book_agent.stages.compile import FinalDraftApprovalRequired, run_epub_compile_stage
from book_agent.text_edits import apply_edit
from book_agent.web.text_view import text_chapter, text_outline
from book_agent.workflow import _final_review_is_required

CONFIG = {"audit": {"semantic_sample_every": 2}, "workflow": {"max_retries": 0}}


def paused_workspace(directory: str):
    from tests.test_compile_stages import CompileStageTests

    config = AppConfig.model_validate(CONFIG)
    workspace = CompileStageTests().prepare_workspace(
        Path(directory), config, unresolved=True
    )
    with pytest.raises(RuntimeError):
        run_epub_compile_stage(workspace, config)
    return workspace


class ReviewSessionTests:
    def test_draft_round_trips_and_accept_clears_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            payload = session.payload()
            assert not payload["stale"]
            worksheet = payload["worksheet"]
            item = worksheet["resolutions"][0]
            assert item["segment_id"] in payload["context"]

            item["reason"] = "Draft note."
            session.save(worksheet)
            assert session.payload()["worksheet"]["resolutions"][0]["reason"] == "Draft note."

            assert session.check(item["segment_id"], "accept", None)["blocking"] == []
            item["decision"] = "accept"
            item["reason"] = "Verified against source; finding is a false positive."
            result = session.apply(worksheet, approve_final=True)

            assert result["report"]["passed"]
            assert result["approved"]
            # Applying the worksheet creates an edit-log revision. The submitted
            # worksheet is now stale, while approval is bound to the new revision.
            assert session.payload()["stale"]
            config = AppConfig.model_validate_json(
                session.workspace.config_file.read_text(encoding="utf-8")
            )
            config.workflow.require_final_review = True
            assert not _final_review_is_required(session.workspace, config)

            chapter = text_outline(session.workspace)["chapters"][0]
            other = next(
                segment
                for segment in text_chapter(
                    session.workspace, chapter["document_id"]
                )["segments"]
                if segment["segment_id"] != item["segment_id"]
            )
            apply_edit(
                session.workspace,
                segment_id=other["segment_id"],
                text="后续人工修改。",
                reason="Changed after final approval.",
                base_target_sha256=other["base_target_sha256"],
                expected_event_id=other["edit_revision"],
            )
            assert _final_review_is_required(session.workspace, config)
            with pytest.raises(FinalDraftApprovalRequired):
                run_epub_compile_stage(session.workspace, config)

    def test_save_rejects_a_worksheet_after_a_text_tab_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            worksheet = session.payload()["worksheet"]
            chapter = text_outline(session.workspace)["chapters"][0]
            segment = next(
                item
                for item in text_chapter(
                    session.workspace, chapter["document_id"]
                )["segments"]
                if not item["in_review_queue"]
            )
            apply_edit(
                session.workspace,
                segment_id=segment["segment_id"],
                text="文本页中的新修改。",
                reason="Make the open worksheet stale.",
                base_target_sha256=segment["base_target_sha256"],
                expected_event_id=segment["edit_revision"],
            )
            with pytest.raises(ValueError, match="older draft"):
                session.save(worksheet)

    def test_check_reports_blocking_issue_for_untranslated_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            segment_id = session.payload()["worksheet"]["resolutions"][0]["segment_id"]
            blocking = session.check(
                segment_id, "replace", "This passage was left entirely in English words."
            )["blocking"]
            assert blocking

    def test_save_rejects_completed_decision_without_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            worksheet = session.payload()["worksheet"]
            worksheet["resolutions"][0]["decision"] = "accept"
            with pytest.raises(ValueError, match="reason"):
                session.save(worksheet)
            assert not session.draft_path.exists()


class ApprovalWithoutAQueueTests:
    """The final draft approved on its own: there is no decision to apply it with."""

    def test_a_draft_nothing_is_queued_for_is_approved_and_again_after_its_title_is_corrected(self):
        from book_agent.book_edits import TITLE_ID, apply_book_edit
        from book_agent.hashing import sha256_text
        from tests.test_epub_notes import CONFIG as BOOK, _translated_job

        config = AppConfig.model_validate({**BOOK, "workflow": {"require_final_review": True}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = _translated_job(Path(directory), config)
            session = ReviewSession(workspace)
            # No passage was queued, so no worksheet was written: the draft still waits for a person.
            payload = session.payload()
            assert payload["worksheet"] is None and payload["approval_required"]
            with pytest.raises(FinalDraftApprovalRequired):
                run_epub_compile_stage(workspace, config)
            assert session.approve() == {"approved": True}
            assert not session.payload()["approval_required"]
            run_epub_compile_stage(workspace, config)
            # Its title corrected on the Text tab, it waits again, and is approved the same way.
            apply_book_edit(
                workspace, item_id=TITLE_ID, text="四签名之谜", reason="as the series names it", base_target_sha256=sha256_text("四签名"),
            )
            assert session.payload()["approval_required"]
            session.approve()
            assert not session.payload()["approval_required"]
            run_epub_compile_stage(workspace, config)

    def test_a_job_that_asks_for_no_approval_is_not_waiting_for_one(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            assert not session.payload()["approval_required"]

    def test_a_draft_with_more_unresolved_than_the_compile_allows_is_not_offered_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = paused_workspace(directory)
            config = AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
            config.workflow.require_final_review = True
            workspace.config_file.write_text(config.model_dump_json(), encoding="utf-8")
            session = ReviewSession(workspace)
            # The queue comes first: its decisions are applied, and the draft approved, together.
            assert not session.payload()["approval_required"]
            with pytest.raises(ValueError, match="resolve them before final approval"):
                session.approve()


def raise_compile_limit(workspace, limit):
    config = AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
    config.workflow.compile_max_unresolved_review_segments = limit
    workspace.config_file.write_text(config.model_dump_json(), encoding="utf-8")


class PartialApplyTests:
    def test_pending_segments_beyond_the_compile_limit_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            payload = session.payload()
            assert payload["compile_limit"] == 0
            with pytest.raises(ValueError, match="compile limit"):
                session.apply(payload["worksheet"], approve_final=True, partial=True)

    def test_pending_segments_within_the_limit_are_left_and_the_draft_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = paused_workspace(directory)
            raise_compile_limit(workspace, 1)
            session = ReviewSession(workspace)
            payload = session.payload()
            assert payload["compile_limit"] == 1
            pending_id = payload["worksheet"]["resolutions"][0]["segment_id"]

            result = session.apply(payload["worksheet"], approve_final=True, partial=True)

            assert result["approved"]
            assert result["report"]["review_segment_ids"] == [pending_id]

    def test_full_apply_still_requires_every_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = paused_workspace(directory)
            raise_compile_limit(workspace, 1)
            session = ReviewSession(workspace)
            with pytest.raises(ValueError, match="pending"):
                session.apply(session.payload()["worksheet"], approve_final=True)


class SaveRefusalTests:
    def test_a_job_without_a_worksheet_has_nothing_to_save(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            worksheet = session.payload()["worksheet"]
            (session.reports / WORKSHEET_NAME).unlink()
            with pytest.raises(ValueError, match="no final-review worksheet exists"):
                session.save(worksheet)

    def test_a_worksheet_for_another_draft_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            worksheet = session.payload()["worksheet"]
            with pytest.raises(ValueError, match="older draft; reload the page"):
                session.save({**worksheet, "draft_output_hash": "0" * 64})
            assert not session.draft_path.exists()

    def test_saved_decisions_for_another_draft_are_not_shown(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            worksheet = session.payload()["worksheet"]
            worksheet["resolutions"][0]["reason"] = "Left from an earlier draft."
            session.draft_path.write_text(
                json.dumps({**worksheet, "draft_output_hash": "0" * 64}), encoding="utf-8"
            )
            payload = session.payload()
            assert payload["draft_saved"]
            assert payload["worksheet"]["resolutions"][0]["reason"] != "Left from an earlier draft."


class CompileFromTheReviewPageTests:
    def result(self, **fields):
        return SimpleNamespace(
            **{"result": "complete", "exit_code": 0, "stage": "validate_epub", "message": "", **fields}
        )

    def wait(self, session, state):
        for _ in range(200):
            if session.compile_state()["state"] == state:
                return session.compile_state()
            time.sleep(0.02)
        raise AssertionError(f"compile never became {state}: {session.compile_state()}")

    def test_compile_runs_once_at_a_time_and_reports_its_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            assert session.compile_state() == {"state": "idle", "events": [], "result": None}
            release = threading.Event()

            def workflow(workspace, progress):
                progress(SimpleNamespace(stage="compile", status="running", message="writing the book"))
                release.wait(10)
                return self.result()

            with patch("book_agent.review_ui.run_workflow", side_effect=workflow), \
                 patch("book_agent.review_ui.load_compiled_epub_path", return_value="output/book.epub"):
                assert session.start_compile()["state"] == "running"
                with pytest.raises(ValueError, match="already running"):
                    session.start_compile()
                release.set()
                state = self.wait(session, "done")
            assert state["events"] == [{"stage": "compile", "status": "running", "message": "writing the book"}]
            assert state["result"] == {
                "result": "complete", "exit_code": 0, "stage": "validate_epub", "message": "",
                "output": "output/book.epub",
            }

    def test_a_book_not_compiled_yet_has_no_output_to_name(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            paused = self.result(result="paused", exit_code=2, stage="compile", message="approval needed")
            with patch("book_agent.review_ui.run_workflow", return_value=paused), \
                 patch("book_agent.review_ui.load_compiled_epub_path", side_effect=FileNotFoundError("no book")):
                session.start_compile()
                state = self.wait(session, "done")
            assert state["result"]["output"] == "" and state["result"]["message"] == "approval needed"

    def test_a_crash_is_shown_to_the_reviewer_and_compile_can_be_tried_again(self):
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession(paused_workspace(directory))
            with patch("book_agent.review_ui.run_workflow", side_effect=RuntimeError("disk full")):
                session.start_compile()
                state = self.wait(session, "failed")
                assert state["result"] == {"result": "failed", "message": "disk full"}
                session.start_compile()
                self.wait(session, "failed")
