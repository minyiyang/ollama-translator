"""Remaining-time estimate for a job, from its own pace and earlier finished jobs.

* The running stage is extrapolated from its measured time per unit (chunk,
  batch, ...) in the current session; before the first unit finishes it falls
  back to the average LLM call time.
* Stages that have not planned their work yet are estimated from how long the
  same stage took per source segment in previously completed jobs, scaled by
  this book's segment count.
* Time spent waiting at a human review gate is never counted, and stages the
  config disables are skipped.
"""

from __future__ import annotations

import statistics
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..pipeline_state import WorkflowStage
from ..stages.decompile import load_decompile_manifest
from ..workflow import workflow_status
from ..workspace import JobWorkspace, open_job_workspace
from .jobs import LogTracker

_lock = threading.Lock()
_trackers: dict[Path, LogTracker] = {}
_segments: dict[Path, int | None] = {}
_history: dict[tuple[Path, Path], tuple[float, tuple[dict[str, float], int]]] = {}
_HISTORY_TTL_SECONDS = 60.0


def _seconds(start: str, end: str) -> float:
    try:
        return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except ValueError:
        return 0.0


def _tracked_logs(workspace: JobWorkspace) -> list[LogTracker]:
    """Every session log of a job, parsed incrementally and cached across polls."""
    trackers = []
    with _lock:
        for path in sorted((workspace.root / "logs").glob("*.log")):
            tracker = _trackers.setdefault(path, LogTracker(path))
            tracker.advance()
            trackers.append(tracker)
    return trackers


def segment_count(workspace: JobWorkspace) -> int | None:
    """Source text segments, the unit that scales stage time between books."""
    with _lock:
        if _segments.get(workspace.root):
            return _segments[workspace.root]
    try:
        manifest = load_decompile_manifest(workspace)
    except (FileNotFoundError, ValueError):
        return None
    count = sum(len(document.segments) for document in manifest.documents) or None
    with _lock:
        _segments[workspace.root] = count
    return count


def stage_seconds(workspace: JobWorkspace) -> dict[str, float]:
    """Active time per stage across all sessions (gaps between sessions excluded)."""
    totals: dict[str, float] = {}
    for tracker in _tracked_logs(workspace):
        for name, activity in tracker.stages.items():
            totals[name] = totals.get(name, 0.0) + _seconds(activity.first, activity.last)
    return totals


def history_rates(runs: Path, exclude: Path) -> tuple[dict[str, float], int]:
    """Median seconds per source segment for each stage, cached briefly across polls."""
    key = (runs.resolve(), exclude.resolve())
    now = time.monotonic()
    with _lock:
        cached = _history.get(key)
        if cached and now - cached[0] < _HISTORY_TTL_SECONDS:
            return cached[1]
    result = _history_rates(runs, exclude)
    with _lock:
        _history[key] = (now, result)
    return result


def _history_rates(runs: Path, exclude: Path) -> tuple[dict[str, float], int]:
    """Median seconds per source segment for each stage, from other jobs' completed stages."""
    samples: dict[str, list[float]] = {}
    jobs = 0
    for path in runs.iterdir() if runs.is_dir() else []:
        if not path.is_dir() or path.name.startswith(".") or path.resolve() == exclude.resolve():
            continue
        try:
            workspace = open_job_workspace(path)
            status = workflow_status(workspace)
        except (ValueError, OSError):
            continue
        segments = segment_count(workspace)
        if not segments:
            continue
        completed = {s["name"] for s in status["stages"] if s["status"] == "completed"}
        durations = stage_seconds(workspace)
        used = False
        for name in completed:
            if name in durations:
                samples.setdefault(name, []).append(durations[name] / segments)
                used = True
        jobs += used
    return {name: statistics.median(values) for name, values in samples.items()}, jobs


def _stage_enabled(stage: str, config: AppConfig) -> bool:
    if stage == WorkflowStage.RESCUE_TRANSLATION.value:
        return bool(config.translation.fallback_models)
    if stage == WorkflowStage.REPROSE_TRANSLATION.value:
        return config.reprose.enabled
    if stage in {WorkflowStage.EXTRACT_GLOSSARY.value, WorkflowStage.RESOLVE_GLOSSARY.value}:
        return config.glossary.extraction_enabled
    return True


# Deterministic stages that take seconds; without history they count as zero, not "unknown".
_QUICK_STAGES = {"decompile", "preprocess", "compile", "validate_epub", "validate_repaired"}


def _is_human_gate(stage: str, config: AppConfig) -> bool:
    return stage == WorkflowStage.APPROVE_GLOSSARY.value and (
        config.workflow.require_glossary_review and not config.workflow.llm_glossary_review
    )


def _current_stage_estimate(name: str, activity, now: str, history_seconds: float | None = None) -> dict[str, Any] | None:
    """Extrapolate the active stage: own measured pace, else past jobs, else LLM call time."""
    if activity is None or activity.counter is None:
        return None
    index, total = activity.counter  # `index` is the unit in progress (1-based)
    done = index - 1
    if done >= 1 and activity.unit_started:
        per_unit = _seconds(activity.first, activity.unit_started) / done
        into_current = _seconds(activity.unit_started, now)
        remaining = max(per_unit * 0.1, per_unit * (total - done) - into_current)
        basis = f"{per_unit:.0f}s per unit over {done} finished"
    elif history_seconds:
        per_unit = history_seconds / total
        remaining = history_seconds * (total - done) / total
        basis = "previous jobs, until a unit finishes here"
    elif activity.llm_calls:
        per_unit = activity.call_seconds / activity.llm_calls
        remaining = per_unit * (total - done)
        basis = f"average LLM call {per_unit:.0f}s"
    else:
        return None
    return {
        "stage": name,
        "done": done,
        "total": total,
        "seconds_per_unit": round(per_unit, 1),
        "remaining_seconds": round(remaining),
        "basis": basis,
    }


def estimate(workspace: JobWorkspace, runs: Path, config: AppConfig) -> dict[str, Any]:
    status = workflow_status(workspace)
    stages = status["stages"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    logs = _tracked_logs(workspace)
    latest = logs[-1] if logs else None
    rates, jobs = history_rates(runs, workspace.root)
    segments = segment_count(workspace)

    current = None
    # The stage in progress, or one that was paused/stopped part-way (not a human gate).
    active = next(
        (s for s in stages if s["status"] == "running"),
        next(
            (s for s in stages if s["status"] in {"paused", "failed", "cancelled"} and not _is_human_gate(s["name"], config)),
            None,
        ),
    )
    if active and latest is not None and (activity := latest.stages.get(active["name"])) is not None:
        # A stopped stage is measured up to where it stopped, so pause time is not counted.
        clock = now if active["status"] == "running" else activity.last
        history = rates[active["name"]] * segments if active["name"] in rates and segments else None
        current = _current_stage_estimate(active["name"], activity, clock, history)
        if current is not None:
            current["running"] = active["status"] == "running"

    pending: list[dict[str, Any]] = []
    unknown: list[str] = []
    gates: list[str] = []
    for stage in stages:
        name = stage["name"]
        if stage["status"] == "completed" or (current and name == current["stage"]):
            continue
        if not _stage_enabled(name, config):
            continue
        if _is_human_gate(name, config):
            gates.append(name)
            continue
        if name in rates and segments:
            pending.append({"stage": name, "seconds": round(rates[name] * segments)})
        elif name not in _QUICK_STAGES:
            unknown.append(name)
    if status["stages"] and any(s["name"] == "compile" for s in stages):
        gates.append("final review (only if segments need you)")

    remaining = (current["remaining_seconds"] if current else 0) + sum(p["seconds"] for p in pending)
    elapsed = sum(stage_seconds(workspace).values())
    return {
        "elapsed_seconds": round(elapsed),
        "current": current,
        "pending": pending,
        "unknown_stages": unknown,
        "remaining_seconds": round(remaining) if (current or pending) else None,
        "history_jobs": jobs,
        "segments": segments,
        "excludes": gates,
        "complete": status["overall"] == "complete",
    }
