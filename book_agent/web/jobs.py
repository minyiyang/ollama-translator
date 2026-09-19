"""Job discovery and live progress derived from state and plain session logs.

Progress is read from the same artifacts a terminal run writes (``state.sqlite3``
and ``logs/*.log``), so runs started from the CLI and from the UI look identical.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..pipeline_state import WorkflowStage
from ..workflow import load_workspace_config, workflow_status
from ..workspace import JobWorkspace, open_job_workspace, validate_job_id

_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] \[stage=(?P<stage>[^\]]+)\] (?P<rest>.*)$")
_SCHEDULE_RE = re.compile(r"\[schedule\].*?llm_tasks=(\d+)")
_COUNTER_RE = re.compile(r"\[(chunk|batch|repair|reprose|review|feedback-repair)=(\d+)/(\d+)")
_SEGMENT_RE = re.compile(r"\[segment=(\d+)/(\d+)")
_LLM_DONE_RE = re.compile(r"\[llm\] (?P<model>\S+): completed.*?output ([\d,]+) tokens, ([\d.]+) tok/s")
_LLM_RE = re.compile(r"\[llm\] (?P<text>.*)$")
_ELAPSED_RE = re.compile(r"elapsed ([\d.]+)(ms|s|m|h)\b")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
_TAIL_LINES = 400


def job_path(runs: Path, job_id: str) -> Path:
    """Resolve a job id to a directory directly inside the runs root."""
    validate_job_id(job_id)
    path = (runs / job_id).resolve()
    if path.parent != runs.resolve():
        raise ValueError("job path escapes the runs directory")
    return path


def open_job(runs: Path, job_id: str) -> JobWorkspace:
    return open_job_workspace(job_path(runs, job_id))


def job_direction(workspace: JobWorkspace) -> str:
    """The job's translation direction (``en-zh``), or "" when unreadable."""
    try:
        return load_workspace_config(workspace).translation.direction.value
    except (OSError, ValueError):
        return ""


def draft_direction(config_dir: Path | None, name: str) -> str:
    """A draft's direction from its config file, or "" when unset or invalid."""
    if config_dir is None:
        return ""
    from .setup import parse_config, read_config  # local: setup imports cli

    try:
        return parse_config(read_config(config_dir, name), config_dir).translation.direction.value
    except (OSError, ValueError):
        return ""


def list_jobs(runs: Path, config_dir: Path | None = None) -> list[dict[str, Any]]:
    """Summarize every valid workspace, newest first."""
    jobs = []
    if not runs.is_dir():
        return jobs
    for path in runs.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            workspace = open_job_workspace(path)
            status = workflow_status(workspace)
        except (ValueError, OSError):
            continue
        stages = status["stages"]
        current = next(
            (s for s in stages if s["status"] in {"running", "paused", "failed"}),
            next((s for s in stages if s["status"] == "pending"), None),
        )
        jobs.append(
            {
                "job_id": path.name,
                "overall": status["overall"],
                "source": workspace.source_file.name,
                "direction": job_direction(workspace),
                "downloadable": status["overall"] == "complete",
                "current_stage": current["name"] if current else "",
                "current_message": str(current.get("message") or "") if current else "",
                "completed": sum(1 for s in stages if s["status"] == "completed"),
                "total": len(stages),
                "updated": max((str(s.get("updated_at") or "") for s in stages), default=""),
            }
        )
    from .drafts import delete_draft, list_drafts  # local: drafts imports setup, which imports cli

    started = {job["job_id"] for job in jobs}
    for draft in list_drafts(runs):
        if draft["job_id"] in started:
            delete_draft(runs, draft["job_id"])  # the run created its workspace
            continue
        jobs.append(
            {
                "job_id": draft["job_id"],
                "overall": "starting" if draft.get("launched") else "draft",
                "source": Path(draft["source"]).name,
                "direction": draft_direction(config_dir, draft["config"]),
                "downloadable": False,
                "current_stage": "",
                "current_message": "",
                "completed": 0,
                "total": len(WorkflowStage),
                "updated": draft.get("launched") or draft["created"],
            }
        )
    jobs.sort(key=lambda job: job["updated"], reverse=True)
    return jobs


@dataclass
class StageActivity:
    tasks: int = 0
    counter: tuple[int, int] | None = None
    segments: tuple[int, int] | None = None
    llm_calls: int = 0
    output_tokens: int = 0
    call_seconds: float = 0.0
    first: str = ""
    last: str = ""
    unit_started: str = ""  # when the current counter value first appeared


@dataclass
class LogTracker:
    """Incrementally parse one append-only session log."""

    path: Path
    offset: int = 0
    stages: dict[str, StageActivity] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    line_count: int = 0
    last_llm: str = ""
    last_rate: float = 0.0

    def advance(self) -> None:
        size = self.path.stat().st_size
        if size < self.offset:  # rotated or rewritten: start over
            self.__init__(self.path)  # type: ignore[misc]
        if size == self.offset:
            return
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read(size - self.offset)
        complete = chunk.rfind(b"\n") + 1
        if complete == 0:
            return
        self.offset += complete
        for raw in chunk[:complete].decode("utf-8", errors="replace").splitlines():
            self._parse(raw)

    def _parse(self, line: str) -> None:
        self.line_count += 1
        self.lines.append(line)
        if len(self.lines) > _TAIL_LINES * 2:
            del self.lines[: len(self.lines) - _TAIL_LINES]
        match = _LINE_RE.match(line)
        if not match:
            return
        stage = self.stages.setdefault(match["stage"], StageActivity())
        stage.first = stage.first or match["ts"]
        stage.last = match["ts"]
        rest = match["rest"]
        if schedule := _SCHEDULE_RE.search(rest):
            stage.tasks = int(schedule[1])
        if counter := _COUNTER_RE.search(rest):
            value = (int(counter[2]), int(counter[3]))
            if value != stage.counter:
                stage.unit_started = match["ts"]
            stage.counter = value
        if segment := _SEGMENT_RE.search(rest):
            stage.segments = (int(segment[1]), int(segment[2]))
        if done := _LLM_DONE_RE.search(rest):
            stage.llm_calls += 1
            stage.output_tokens += int(done[2].replace(",", ""))
            self.last_rate = float(done[3])
            if elapsed := _ELAPSED_RE.search(rest):
                stage.call_seconds += float(elapsed[1]) * _UNIT_SECONDS[elapsed[2]]
        if llm := _LLM_RE.search(rest):
            self.last_llm = f"{match['ts']}  {match['stage']}  {llm['text']}"


class ProgressReader:
    """Keep one tracker per job so polling costs only the newly written bytes."""

    def __init__(self) -> None:
        self._trackers: dict[Path, LogTracker] = {}
        self._lock = threading.Lock()

    def snapshot(
        self, workspace: JobWorkspace, after_line: int = 0, log_name: str = ""
    ) -> dict[str, Any]:
        status = workflow_status(workspace)
        logs = sorted((workspace.root / "logs").glob("*.log"))
        progress: dict[str, Any] = {"log": None, "stages": {}, "lines": [], "line_count": 0}
        if logs:
            with self._lock:
                tracker = self._trackers.get(logs[-1])
                if tracker is None:
                    tracker = self._trackers[logs[-1]] = LogTracker(logs[-1])
                tracker.advance()
                if log_name != logs[-1].name:  # the client was following an older session
                    after_line = 0
                start = max(after_line, tracker.line_count - min(len(tracker.lines), _TAIL_LINES))
                progress = {
                    "log": logs[-1].name,
                    "line_count": tracker.line_count,
                    "lines": tracker.lines[len(tracker.lines) - (tracker.line_count - start):]
                    if tracker.line_count > start
                    else [],
                    "last_llm": tracker.last_llm,
                    "last_rate": tracker.last_rate,
                    "stages": {
                        name: {
                            "tasks": activity.tasks,
                            "counter": activity.counter,
                            "segments": activity.segments,
                            "llm_calls": activity.llm_calls,
                            "output_tokens": activity.output_tokens,
                            "avg_call_seconds": (
                                round(activity.call_seconds / activity.llm_calls, 1) if activity.llm_calls else None
                            ),
                            "first": activity.first,
                            "last": activity.last,
                        }
                        for name, activity in tracker.stages.items()
                    },
                }
        return {"status": status, "progress": progress, "source": workspace.source_file.name}
