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
