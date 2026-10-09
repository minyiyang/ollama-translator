import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.state import StageStatus, connect_state, set_stage_status
from book_agent.web import estimate as est
from book_agent.web.jobs import StageActivity
from tests.test_web_ui import LOG, paused_glossary_workspace


@pytest.fixture
def job():
    with tempfile.TemporaryDirectory() as directory:
        workspace, config = paused_glossary_workspace(Path(directory))
        yield workspace, config


def write_log(workspace, text: str, name: str = "s.log") -> None:
    logs = workspace.root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / name).write_text(text, encoding="utf-8")


def set_status(workspace, stage: str, status: StageStatus) -> None:
    connection = connect_state(workspace.state_file)
    try:
        set_stage_status(connection, stage, status)
    finally:
        connection.close()


def enabled(**sections) -> AppConfig:
    return AppConfig.model_validate(sections)


class TimeTests:
    def test_unreadable_times_count_as_no_time(self):
        assert est._seconds("2026-01-01T00:00:00+00:00", "2026-01-01T00:01:30+00:00") == 90.0
        assert est._seconds("2026-01-01T00:01:30+00:00", "2026-01-01T00:00:00+00:00") == 0.0
        assert est._seconds("yesterday", "2026-01-01T00:00:00+00:00") == 0.0

    def test_stage_time_adds_up_across_session_logs(self, job):
        workspace, _ = job
        assert est.stage_seconds(workspace) == {}
        write_log(workspace, LOG)
        write_log(workspace, LOG.replace("23:11:17", "23:11:34"), "t.log")
        # 23:11:04 to 23:11:17 in one session, to 23:11:34 in the other.
        assert est.stage_seconds(workspace) == {"translate": 43.0, "audit_translation": 0.0}


class SegmentCountTests:
    def test_counts_the_source_segments_once(self, job):
        workspace, _ = job
        count = est.segment_count(workspace)
        assert count and count > 0
        with patch.object(est, "load_decompile_manifest", side_effect=AssertionError("read again")):
            assert est.segment_count(workspace) == count

    def test_a_job_not_yet_decompiled_has_none(self, job):
        workspace, _ = job
        with patch.object(est, "load_decompile_manifest", side_effect=FileNotFoundError("no manifest")):
            assert est.segment_count(workspace) is None


class HistoryTests:
    def test_rates_come_from_the_completed_stages_of_other_jobs(self, job):
        workspace, _ = job
        runs = workspace.root.parent
        segments = est.segment_count(workspace)
        write_log(
            workspace,
            "[2026-01-01T00:00:00+00:00] [stage=extract_glossary] [session=s] [running]: result=pending\n"
            "[2026-01-01T00:01:40+00:00] [stage=extract_glossary] [session=s] [completed]\n"
            # Not completed, so not a sample.
            "[2026-01-01T00:02:00+00:00] [stage=translate] [session=s] [running]: result=pending\n"
            "[2026-01-01T00:03:00+00:00] [stage=translate] [session=s] [running]: result=pending\n",
        )
        (runs / ".drafts").mkdir(exist_ok=True)
        (runs / "not-a-job").mkdir()
        (runs / "notes.txt").write_text("x", encoding="utf-8")

        rates, jobs = est._history_rates(runs, runs / "another-job")
        assert rates == {"extract_glossary": pytest.approx(100 / segments)} and jobs == 1
        # The job being estimated is no evidence for itself.
        assert est._history_rates(runs, workspace.root) == ({}, 0)
        assert est._history_rates(runs / "missing", workspace.root) == ({}, 0)

    def test_a_job_without_segments_or_logged_stages_is_no_sample(self, job):
        workspace, _ = job
        runs = workspace.root.parent
        assert est._history_rates(runs, runs / "another-job") == ({}, 0)  # nothing logged
        with patch.object(est, "segment_count", return_value=None):
            assert est._history_rates(runs, runs / "another-job") == ({}, 0)

    def test_rates_are_kept_between_polls(self, job):
        workspace, _ = job
        runs = workspace.root.parent
        with patch.object(est, "_history_rates", return_value=({"translate": 1.5}, 3)) as compute:
            first = est.history_rates(runs, runs / "another-job")
            second = est.history_rates(runs, runs / "another-job")
        assert first == second == ({"translate": 1.5}, 3)
        compute.assert_called_once()


class StageEnabledTests:
    def test_optional_stages_follow_the_config(self):
        default = AppConfig()
        assert est._stage_enabled("translate", default)
        assert not est._stage_enabled("rescue_translation", default)
        assert est._stage_enabled(
            "rescue_translation", enabled(translation={"fallback_models": ["qwen3.8:27b"]})
        )
        for stage, off in (
            ("reprose_translation", {"reprose": {"enabled": False}}),
            ("audit_consistency", {"consistency": {"enabled": False}}),
            ("build_story_context", {"consistency": {"story_context": {"enabled": False}}}),
        ):
            assert not est._stage_enabled(stage, enabled(**off)), stage
        # The config refuses to switch extraction off without a supplied glossary, so set it past the check.
        supplied = default.model_copy(update={"glossary": default.glossary.model_copy(update={"extraction_enabled": False})})
        assert not est._stage_enabled("extract_glossary", supplied)
        assert not est._stage_enabled("resolve_glossary", supplied)
        assert est._stage_enabled("reprose_translation", enabled(reprose={"enabled": True}))
        assert est._stage_enabled("extract_glossary", enabled(glossary={"extraction_enabled": True}))


class CurrentStageTests:
    def test_a_stage_that_has_not_counted_its_work_has_no_estimate(self):
        assert est._current_stage_estimate("translate", None, "2026-01-01T00:00:00+00:00") is None
        assert est._current_stage_estimate("translate", StageActivity(), "2026-01-01T00:00:00+00:00") is None
        # Counted, but nothing to measure a pace from yet.
        silent = StageActivity(counter=(1, 6), first="2026-01-01T00:00:00+00:00")
        assert est._current_stage_estimate("translate", silent, "2026-01-01T00:00:10+00:00") is None

    @pytest.mark.parametrize("status", [StageStatus.RUNNING, StageStatus.PAUSED])
    def test_the_stage_in_progress_is_extrapolated_from_its_log(self, job, status):
        workspace, config = job
        set_status(workspace, "approve_glossary", StageStatus.COMPLETED)
        set_status(workspace, "preprocess", StageStatus.COMPLETED)
        set_status(workspace, "translate", status)
        write_log(workspace, LOG)
        with patch.object(est, "history_rates", return_value=({"audit_translation": 2.0}, 1)), \
             patch.object(est, "segment_count", return_value=10):
            result = est.estimate(workspace, workspace.root.parent, config)
        current = result["current"]
        assert (current["stage"], current["done"], current["total"]) == ("translate", 1, 14)
        assert current["running"] is (status is StageStatus.RUNNING)
        if status is StageStatus.PAUSED:
            # Measured up to the last log line: one second for the first chunk, twelve into the second.
            assert current["seconds_per_unit"] == 1.0 and current["remaining_seconds"] == 1
        pending = {item["stage"]: item["seconds"] for item in result["pending"]}
        assert pending == {"audit_translation": 20} and "translate" not in result["unknown_stages"]
        assert result["remaining_seconds"] == current["remaining_seconds"] + 20
        assert result["elapsed_seconds"] == 13 and not result["complete"]

    def test_nothing_known_gives_no_remaining_time(self, job):
        workspace, config = job
        with patch.object(est, "history_rates", return_value=({}, 0)):
            result = est.estimate(workspace, workspace.root.parent, config)
        assert result["current"] is None and result["pending"] == []
        assert result["remaining_seconds"] is None and "translate" in result["unknown_stages"]
        assert "decompile" not in result["unknown_stages"] and "compile" not in result["unknown_stages"]
