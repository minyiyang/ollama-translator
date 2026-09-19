import tempfile
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.review_ui import ReviewSession
from book_agent.stages.compile import run_epub_compile_stage

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
            assert session.payload()["stale"]

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
