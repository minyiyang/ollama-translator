"""Loopback-only job dashboard: setup, progress, glossary gate, and final review.

Long-running work is launched as the regular CLI (``run``/``resume``/``approve``)
in a child process, so checkpoints, logs, and summaries are exactly what a
terminal run produces and a job keeps running if the server stops.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .. import series as series_api
from ..languages import TranslationDirection
from ..schemas import GlossaryCategory
from ..review_ui import ReviewSession
from .rerun import parse_stage, rerun_preview
from ..workspace import validate_job_id
from ..state import StageStatus
from ..workflow import (
    ExitCode,
    clear_pause_request,
    load_workspace_config,
    mark_running_stages,
    pause_requested,
    request_pause,
    workflow_status,
)
from . import drafts
from .book_info import allowed_book, book_cover, book_info
from .estimate import estimate as estimate_job
from . import setup as setup_api
from .glossary_view import glossary_payload, write_reviewed_glossary
from ..stages.compile import load_compiled_epub_path
from .jobs import ProgressReader, draft_direction, job_direction, job_path, list_jobs, open_job

_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
_PAGES = {"config", "progress", "glossary", "review"}
_DOWNLOAD_TYPES = {".epub": "application/epub+zip", ".rtf": "application/rtf"}
_ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_EXIT_OUTCOMES = {
    int(ExitCode.COMPLETE): "completed",
    int(ExitCode.PAUSED): "paused",
    int(ExitCode.CANCELLED): "cancelled",
}


def _series_process(series_id: str) -> str:
    """Process key for a series' background run; job ids never start with a dot."""
    return f".series-{series_id}"


class UiApp:
    """State shared by every request: paths, launched processes, sessions."""

    def __init__(
        self,
        runs: Path,
        config_dir: Path,
        sample_dirs: list[Path],
        template: Path | None = None,
    ) -> None:
        self.runs = runs.resolve()
        self.config_dir = config_dir.resolve()
        self.sample_dirs = sample_dirs
        self.template = template.resolve() if template else None
        self.token = secrets.token_urlsafe(24)
        self.progress = ProgressReader()
        self._processes: dict[str, dict[str, Any]] = {}
        self._reviews: dict[str, ReviewSession] = {}
        self._lock = threading.Lock()

    # -- child processes -------------------------------------------------

    def launch(self, job_id: str, args: list[str], label: str) -> dict[str, Any]:
        """Start one CLI command for a job unless one is already running."""
        with self._lock:
            current = self._processes.get(job_id)
            if current and current["process"].poll() is None:
                raise ValueError(f"{current['label']} is already running for this job")
            launch_dir = self.runs / ".ui-launch"
            launch_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            output = launch_dir / f"{job_id}-{stamp}-{args[0]}.out"
            env = {
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(
                    filter(None, [str(_PACKAGE_ROOT), os.environ.get("PYTHONPATH", "")])
                ),
            }
            handle = output.open("wb")
            process = subprocess.Popen(
                [sys.executable, "-m", "book_agent.cli", *args, "--plain"],
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                # Keep Ctrl+C on the server from interrupting the job.
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            handle.close()
            self._processes[job_id] = {
                "process": process,
                "label": label,
                "output": output,
                "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            return self.process_state(job_id)

    def process_state(self, job_id: str) -> dict[str, Any] | None:
        entry = self._processes.get(job_id)
        if entry is None:
            return None
        code = entry["process"].poll()
        # CLI exit codes (workflow.ExitCode): 2 = paused at a gate, 130 = cancelled
        # or stopped. Only the rest are failures worth showing output for.
        outcome = (
            "running" if code is None
            else _EXIT_OUTCOMES.get(code, "failed")
        )
        text = entry["output"].read_text(encoding="utf-8", errors="replace") if code is not None else ""
        if outcome == "paused" and "usage: book-agent" in text:
            outcome = "failed"  # argparse also exits 2 on a usage error
        tail = "\n".join(text.splitlines()[-25:]) if outcome == "failed" else ""
        return {
            "label": entry["label"],
            "running": code is None,
            "exit_code": code,
            "outcome": outcome,
            "started": entry["started"],
            "output_tail": tail,
        }

    def review(self, job_id: str) -> ReviewSession:
        with self._lock:
            if job_id not in self._reviews:
                self._reviews[job_id] = ReviewSession(open_job(self.runs, job_id))
            return self._reviews[job_id]

    # -- API ---------------------------------------------------------------

    def setup(self) -> dict[str, Any]:
        self._settle_drafts()
        return {
            "configs": setup_api.list_configs(self.config_dir),
            "config_dir": str(self.config_dir),
            "runs": str(self.runs),
            "template": str(self.template) if self.template and self.template.is_file() else "",
            "jobs": list_jobs(self.runs, self.config_dir),
        }

    def book_roots(self) -> list[Path]:
        # Uploads and every job's captured source live under runs.
        return [*self.sample_dirs, self.runs]

    def _settle_drafts(self) -> None:
        """Return a draft to "draft" when its start ended without creating a workspace."""
        for draft in drafts.list_drafts(self.runs):
            if not draft.get("launched") or (self.runs / draft["job_id"]).exists():
                continue
            process = self.process_state(draft["job_id"])
            if process is None or not process["running"]:
                draft["launched"] = ""
                drafts.save_draft(self.runs, draft)

    def _workspace_or_none(self, job_id: str):
        try:
            return open_job(self.runs, job_id)
        except ValueError:
            return None

    def job_info(self, job_id: str) -> dict[str, Any]:
        """What the job header needs: kind, status, and which controls apply."""
        self._settle_drafts()
        process = self.process_state(job_id)
        process_running = bool(process and process["running"])
        workspace = self._workspace_or_none(job_id)
        if workspace is not None:
            status = workflow_status(workspace)
            overall = str(status["overall"])
            return {
                "job_id": job_id,
                "kind": "job",
                "overall": overall,
                "source": workspace.source_file.name,
                "source_path": str(workspace.source_file),
                "config": Path(status["configuration"]["source_path"] or "").name,
                "direction": job_direction(workspace),
                "downloadable": overall == "complete",
                "series": series_api.series_of_job(self.runs, job_id),
                "stages": status["stages"],
                "running": overall == "running" or process_running,
                "pause_requested": pause_requested(workspace),
                "can_stop": process_running,
                "process": process,
            }
        draft = drafts.load_draft(self.runs, job_id)
        if draft is None:
            raise ValueError(f"no job or draft named {job_id}")
        return {
            "job_id": job_id,
            "kind": "draft",
            "overall": "starting" if process_running else "draft",
            "source": Path(draft["source"]).name,
            "source_path": draft["source"],
            "config": draft["config"],
            "direction": draft_direction(self.config_dir, draft["config"]),
            "downloadable": False,
            "series": series_api.series_of_job(self.runs, job_id),
            "validated": drafts.is_validated(self.config_dir, draft),
            "stages": [],
            "running": process_running,
            "pause_requested": False,
            "can_stop": process_running,
            "process": process,
        }

    def create_job(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a draft job; with ``series_id`` it also joins that series as its next volume."""
        series_id = str(body.get("series_id") or "")
        if series_id:
            series_api.load_manifest(self.runs, series_id)  # refuse before creating anything
        draft = drafts.create_draft(
            self.runs,
            self.config_dir,
            source=body.get("source", ""),
            config_name=body.get("config", ""),
            job_id=body.get("job_id", ""),
            template=self.template,
        )
        if series_id:
            try:
                series_api.add_books(
                    self.runs,
                    series_id,
                    [draft["job_id"]],
                    drafts={draft["job_id"]: draft_direction(self.config_dir, draft["config"])},
                )
            except ValueError:
                drafts.delete_draft(self.runs, draft["job_id"])
                raise
        return draft

    def job_config(self, job_id: str) -> dict[str, Any]:
        """Draft: the editable config file. Started job: the captured, read-only config."""
        draft = drafts.load_draft(self.runs, job_id)
        workspace = self._workspace_or_none(job_id)
        if workspace is None and draft is not None:
            return {
                "editable": True,
                "name": draft["config"],
                "text": setup_api.read_config(self.config_dir, draft["config"]),
                "validated": drafts.is_validated(self.config_dir, draft),
            }
        if workspace is None:
            raise ValueError(f"no job or draft named {job_id}")
        captured = json.loads(workspace.config_file.read_text(encoding="utf-8"))
        return {
            "editable": False,
            "name": Path(workflow_status(workspace)["configuration"]["source_path"] or "").name,
            "text": setup_api.dump_values(captured),
            "validated": True,
        }

    def validate_job(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Save the draft's config, then run every pre-start check against it."""
        draft = drafts.load_draft(self.runs, job_id)
        if draft is None:
            raise ValueError("only a job that has not started can be validated")
        setup_api.save_config(self.config_dir, draft["config"], body["text"])
        text = setup_api.read_config(self.config_dir, draft["config"])
        check = setup_api.validate_setup(text, self.config_dir, self.runs, draft["source"], job_id)
        check = self._check_series_rules(job_id, text, check)
        draft["validated_hash"] = drafts.config_hash(self.config_dir, draft["config"]) if check["ok"] else ""
        drafts.save_draft(self.runs, draft)
        return {**check, "validated": check["ok"]}

    def _check_series_rules(self, job_id: str, text: str, check: dict[str, Any]) -> dict[str, Any]:
        """A series book must match the series direction and pause at its glossary gate.

        Without the pause an automatic approval would skip the series glossary.
        """
        membership = series_api.series_of_job(self.runs, job_id)
        if membership is None:
            return check
        try:
            config = setup_api.parse_config(text, self.config_dir)
        except ValueError:
            return check  # the syntax/schema problem is already reported
        series = series_api.load_manifest(self.runs, str(membership["series_id"]))
        problems = []
        if config.translation.direction is not series.direction:
            problems.append(
                f"series {series.name} translates {series.direction.value}; set translation.direction to "
                f"{series.direction.value}"
            )
        if not config.workflow.require_glossary_review or config.workflow.llm_glossary_review:
            problems.append(
                f"a book in series {series.name} must pause at its glossary gate so it can approve the series "
                "glossary: set workflow.require_glossary_review: true and workflow.llm_glossary_review: false"
            )
        if not problems:
            return check
        return {**check, "ok": False, "problems": [*check.get("problems", []), *problems]}

    def start_job(self, job_id: str) -> dict[str, Any]:
        draft = drafts.load_draft(self.runs, job_id)
        if draft is None:
            raise ValueError("this job has already started; use Resume")
        if not drafts.is_validated(self.config_dir, draft):
            raise ValueError("validate the configuration on the Config tab first")
        state = self.launch(
            job_id,
            [
                "run", draft["source"],
                "--config", str(setup_api.config_path(self.config_dir, draft["config"])),
                "--runs", str(self.runs),
                "--job-id", job_id,
            ],
            "run",
        )
        draft["launched"] = datetime.now().astimezone().isoformat(timespec="seconds")
        drafts.save_draft(self.runs, draft)
        return state

    def pause_job(self, job_id: str) -> dict[str, Any]:
        request_pause(open_job(self.runs, job_id))
        return {"pause_requested": True}

    def stop_job(self, job_id: str) -> dict[str, Any]:
        """Kill a dashboard-launched run now; the interrupted stage is left paused for resume."""
        entry = self._processes.get(job_id)
        if entry is None or entry["process"].poll() is not None:
            raise ValueError("no run started from this dashboard is active; use Pause instead")
        entry["process"].terminate()
        try:
            entry["process"].wait(timeout=15)
        except subprocess.TimeoutExpired:
            entry["process"].kill()
            entry["process"].wait(timeout=15)
        workspace = self._workspace_or_none(job_id)
        if workspace is not None:
            clear_pause_request(workspace)
            mark_running_stages(workspace, StageStatus.PAUSED, "stopped from the dashboard; resume to continue")
        return {"stopped": True}

    # -- series glossaries (docs/SERIES_GLOSSARY_UI.md) ---------------------

    def series_detail(self, series_id: str) -> dict[str, Any]:
        """Series status, the jobs that could join it, and any LLM suggestion run."""
        return {
            **series_api.series_status(self.runs, series_id),
            "addable": series_api.addable_jobs(self.runs, series_id),
            "process": self.process_state(_series_process(series_id)),
        }

    def series_suggest(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """LLM suggestions run as a CLI child process, like every other model call."""
        from ..series_llm import TASKS

        task = str(body.get("task", ""))
        if task not in TASKS:
            raise ValueError(f"unknown suggestion task: {task}")
        series_api.load_manifest(self.runs, series_id)  # refuses unknown series
        return self.launch(
            _series_process(series_id),
            ["series", "suggest", "--runs", str(self.runs), series_id, "--task", task],
            f"LLM {task}",
        )

    def series_log(self, series_id: str, lines: int = 400) -> dict[str, Any]:
        """The output of the series' latest LLM run: this server's, else the newest on disk."""
        series_api.load_manifest(self.runs, series_id)
        key = _series_process(series_id)
        entry = self._processes.get(key)
        path = entry["output"] if entry else None
        if path is None:
            candidates = sorted((self.runs / ".ui-launch").glob(f"{key}-*.out"))
            path = candidates[-1] if candidates else None
        if path is None or not Path(path).is_file():
            return {"file": None, "lines": [], "process": None}
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return {
            "file": Path(path).name,
            "lines": text.splitlines()[-lines:],
            "process": self.process_state(key),
        }

    def series_suggestions(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from ..series_llm import accept_suggestions

        term_ids = [str(item) for item in body.get("term_ids", [])]
        if not term_ids:
            raise ValueError("choose at least one suggestion")
        accept_suggestions(self.runs, series_id, term_ids, bool(body.get("accept")))
        return self.series_workbench(series_id)

    def create_series(self, body: dict[str, Any]) -> dict[str, Any]:
        manifest = series_api.create_series(
            self.runs, str(body.get("series_id", "")), str(body.get("name", "")), str(body.get("direction", ""))
        )
        return manifest.model_dump(mode="json")

    def series_add_books(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        job_ids = [str(item) for item in body.get("job_ids", [])]
        if not job_ids:
            raise ValueError("choose at least one job")
        series_api.add_books(self.runs, series_id, job_ids)
        return self.series_detail(series_id)

    def series_remove_book(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        series_api.remove_book(self.runs, series_id, str(body.get("job_id", "")))
        return self.series_detail(series_id)

    def series_build(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Deterministic and quick (no LLM), so it runs in the request."""
        series_api.build_workbench(
            self.runs,
            series_id,
            minimum_books=int(body.get("minimum_books", 2)),
            consensus_ratio=float(body.get("consensus_ratio", 1.0)),
        )
        return self.series_detail(series_id)

    def series_workbench(self, series_id: str) -> dict[str, Any]:
        view = series_api.workbench_view(self.runs, series_id)
        if view is None:
            raise ValueError("no candidate yet; build it on the Books tab")
        # Categories are stored as schema values; the UI shows English names.
        return {**view, "category_labels": {c.value: c.name.title() for c in GlossaryCategory}}

    def series_decide(self, series_id: str, body: dict[str, Any]) -> dict[str, Any]:
        chinese = body.get("chinese")
        series_api.decide_terms(
            self.runs,
            series_id,
            [str(item) for item in body.get("term_ids", [])],
            str(body.get("decision", "")),
            reason=str(body.get("reason", "")),
            chinese=str(chinese) if chinese else None,
            category=str(body["category"]) if body.get("category") else None,
            unlock=bool(body.get("unlock")),
        )
        return self.series_workbench(series_id)

    def series_publish(self, series_id: str) -> dict[str, Any]:
        record = series_api.publish_workbench(self.runs, series_id)
        return {"published": record.model_dump(mode="json"), **self.series_detail(series_id)}

    def series_version(self, series_id: str, version: str) -> dict[str, Any]:
        manifest = series_api.load_manifest(self.runs, series_id)
        if version not in {item.version for item in manifest.versions}:
            raise ValueError(f"series {series_id} has no version {version}")
        path = series_api.version_glossary_path(self.runs, series_id, version)
        report = path.with_name(f"{version}.report.json")
        return {
            "version": version,
            "glossary": json.loads(path.read_text(encoding="utf-8")),
            "report": json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None,
        }

    def job_output(self, job_id: str) -> Path:
        """The translated book of a completed job."""
        workspace = open_job(self.runs, job_id)
        if workflow_status(workspace)["overall"] != "complete":
            raise ValueError("the job has not completed; there is no translated book yet")
        return Path(load_compiled_epub_path(workspace))

    def rerun_preview(self, job_id: str, stage: str) -> dict[str, Any]:
        return rerun_preview(open_job(self.runs, job_id), stage)

    def rerun_job(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Reset a stage and everything after it, then resume."""
        stage = parse_stage(str(body.get("stage", "")))
        if self.job_info(job_id)["running"]:
            raise ValueError("the job is running; pause or stop it before rerunning a stage")
        self.rerun_preview(job_id, stage.value)  # refuses pending, running, and review gates
        return self.launch(
            job_id,
            ["retry", str(job_path(self.runs, job_id)), "--stage", stage.value, "--resume"],
            "rerun",
        )

    def discard_draft(self, job_id: str) -> dict[str, Any]:
        if drafts.load_draft(self.runs, job_id) is None:
            raise ValueError("only a job that has not started can be discarded")
        membership = series_api.series_of_job(self.runs, job_id)
        if membership is not None:
            series_api.remove_book(self.runs, str(membership["series_id"]), job_id)
        drafts.delete_draft(self.runs, job_id)
        return {"discarded": True}

    def job_progress(self, job_id: str, after: int, log_name: str) -> dict[str, Any]:
        snapshot = self.progress.snapshot(open_job(self.runs, job_id), after, log_name)
        return {**snapshot, "process": self.process_state(job_id)}

    def glossary_state(self, job_id: str) -> dict[str, Any]:
        """Glossary payload plus whether an approval is in flight (then nothing is editable).

        A book at its gate in a series with a published version reviews its glossary
        synchronized to that version instead of the raw draft.
        """
        payload = glossary_payload(open_job(self.runs, job_id))
        overlay = series_api.overlay_for_job(self.runs, job_id) if payload["editable"] else None
        if overlay is not None:
            payload["entries"] = overlay["entries"]
        payload["series_overlay"] = (
            {key: overlay[key] for key in ("series_id", "name", "version")} if overlay else None
        )
        process = self.process_state(job_id)
        running = payload["approve_status"] == "running" or bool(
            process and process["running"] and process["label"] == "glossary approval"
        )
        return {
            **payload,
            "process": process,
            "approval_running": running,
            "editable": payload["editable"] and not running,
        }

    def approve_glossary(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        workspace = open_job(self.runs, job_id)
        overlay = series_api.overlay_for_job(self.runs, job_id)
        args = ["approve", str(workspace.root)]
        if body.get("entries") is not None:
            path = write_reviewed_glossary(workspace, body["entries"])
            args += ["--glossary", str(path)]
        elif body.get("llm") and overlay is not None:
            # The LLM reviews the series-synchronized candidate, not the raw draft.
            args += ["--glossary", str(overlay["path"])]
        if body.get("llm"):
            args.append("--llm-glossary")
        if len(args) == 2:
            raise ValueError("submit reviewed entries, request LLM review, or both")
        args.append("--resume")
        if overlay is not None:
            # Pin before the run so its preprocessing uses the series version.
            series_api.bind_book(self.runs, str(overlay["series_id"]), job_id, str(overlay["version"]))
        return self.launch(job_id, args, "glossary approval")


def make_handler(app: UiApp, port_ref: list[int]) -> type[BaseHTTPRequestHandler]:
    """Build the request handler; POSTs require the per-server token.

    The React build in ``static/`` is a single-page app: every page route gets
    ``index.html`` and the client router renders the view.
    """
    static = files("book_agent.web").joinpath("static")

    def job_routes(job_id: str) -> dict[tuple[str, str], Callable[[dict, dict], Any]]:
        workspace = lambda: open_job(app.runs, job_id)  # noqa: E731
        return {
            ("GET", "progress"): lambda q, b: app.job_progress(
                job_id, int(q.get("after", ["0"])[0]), q.get("log", [""])[0]
            ),
            ("GET", "info"): lambda q, b: app.job_info(job_id),
            ("GET", "estimate"): lambda q, b: estimate_job(
                open_job(app.runs, job_id), app.runs, load_workspace_config(open_job(app.runs, job_id))
            ),
            ("GET", "config"): lambda q, b: app.job_config(job_id),
            ("POST", "validate"): lambda q, b: app.validate_job(job_id, b),
            ("POST", "start"): lambda q, b: app.start_job(job_id),
            ("POST", "pause"): lambda q, b: app.pause_job(job_id),
            ("POST", "stop"): lambda q, b: app.stop_job(job_id),
            ("POST", "discard"): lambda q, b: app.discard_draft(job_id),
            ("POST", "resume"): lambda q, b: app.launch(
                job_id, ["resume", str(job_path(app.runs, job_id))], "resume"
            ),
            ("GET", "rerun"): lambda q, b: app.rerun_preview(job_id, q.get("stage", [""])[0]),
            ("POST", "rerun"): lambda q, b: app.rerun_job(job_id, b),
            ("GET", "glossary"): lambda q, b: app.glossary_state(job_id),
            ("POST", "glossary/approve"): lambda q, b: app.approve_glossary(job_id, b),
            ("GET", "review"): lambda q, b: app.review(job_id).payload(),
            ("POST", "review/save"): lambda q, b: {"saved": bool(app.review(job_id).save(b["worksheet"]))},
            ("POST", "review/check"): lambda q, b: app.review(job_id).check(
                b["segment_id"], b.get("decision", "replace"), b.get("text")
            ),
            ("POST", "review/apply"): lambda q, b: app.review(job_id).apply(
                b["worksheet"], bool(b.get("approve_final")), bool(b.get("partial"))
            ),
            ("GET", "review/compile"): lambda q, b: app.review(job_id).compile_state(),
            ("POST", "review/compile"): lambda q, b: app.review(job_id).start_compile(),
        }

    def series_routes(series_id: str) -> dict[tuple[str, str], Callable[[dict, dict], Any]]:
        return {
            ("GET", "detail"): lambda q, b: app.series_detail(series_id),
            ("POST", "books"): lambda q, b: app.series_add_books(series_id, b),
            ("POST", "books/remove"): lambda q, b: app.series_remove_book(series_id, b),
            ("POST", "build"): lambda q, b: app.series_build(series_id, b),
            ("GET", "workbench"): lambda q, b: app.series_workbench(series_id),
            ("POST", "workbench/decide"): lambda q, b: app.series_decide(series_id, b),
            ("POST", "publish"): lambda q, b: app.series_publish(series_id),
            ("POST", "suggest"): lambda q, b: app.series_suggest(series_id, b),
            ("POST", "workbench/suggestions"): lambda q, b: app.series_suggestions(series_id, b),
            ("GET", "term"): lambda q, b: series_api.term_evidence(app.runs, series_id, q.get("id", [""])[0]),
            ("GET", "log"): lambda q, b: app.series_log(series_id),
            ("GET", "version"): lambda q, b: app.series_version(series_id, q.get("v", [""])[0]),
        }

    global_routes: dict[tuple[str, str], Callable[[dict, dict], Any]] = {
        ("GET", "series"): lambda q, b: {
            "series": series_api.series_summaries(app.runs),
            "directions": [item.value for item in TranslationDirection],
        },
        ("POST", "series"): lambda q, b: app.create_series(b),
        # Same-origin only: cross-site pages cannot read this response (no CORS),
        # and the Host check below stops DNS rebinding.
        ("GET", "session"): lambda q, b: {"token": app.token},
        ("GET", "setup"): lambda q, b: app.setup(),
        ("GET", "config/schema"): lambda q, b: setup_api.config_schema(),
        ("GET", "models"): lambda q, b: {
            "installed": setup_api.installed_models(q.get("host", ["http://localhost:11434"])[0])
        },
        ("POST", "config/parse"): lambda q, b: setup_api.parse_for_editor(b["text"]),
        ("POST", "config/dump"): lambda q, b: {"text": setup_api.dump_values(b["values"])},
        ("POST", "config/check"): lambda q, b: {
            "errors": setup_api.check_config(b["text"], app.config_dir)
        },
        ("GET", "config"): lambda q, b: {
            "name": q["name"][0],
            "text": setup_api.read_config(app.config_dir, q["name"][0]),
        },
        ("POST", "config/validate"): lambda q, b: setup_api.validate_setup(
            b["text"], app.config_dir, app.runs, b.get("source", ""), b.get("job_id", "")
        ),
        ("POST", "config/save"): lambda q, b: {
            "path": setup_api.save_config(app.config_dir, b["name"], b["text"])
        },
        ("POST", "jobs/new"): lambda q, b: app.create_job(b),
        ("GET", "book"): lambda q, b: book_info(allowed_book(q.get("path", [""])[0], app.book_roots())),
        ("GET", "jobs/suggest"): lambda q, b: drafts.suggest_names(q.get("source", [""])[0]),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            self._safely("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._safely("POST")

        def _safely(self, method: str) -> None:
            # Last resort: always answer instead of dropping the connection with a traceback.
            try:
                self._dispatch(method)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser went away mid-response
            except Exception as error:
                try:
                    self._json({"error": f"internal error: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                except OSError:
                    pass

        def _dispatch(self, method: str) -> None:
            host = self.headers.get("Host", "")
            # Reject DNS-rebinding requests that reach loopback under a foreign name.
            if host not in {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}:
                return self._json({"error": "forbidden host"}, HTTPStatus.FORBIDDEN)
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if method == "GET" and (not parts or parts[0] in {"jobs", "series"}):
                return self._page(parts)
            if method == "GET" and len(parts) == 2 and parts[0] == "assets":
                return self._asset(parts[1])
            if not parts or parts[0] != "api":
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            body: dict[str, Any] = {}
            if method == "GET" and parts == ["api", "book", "cover"]:
                try:
                    data, media_type = book_cover(allowed_book(parse_qs(url.query).get("path", [""])[0], app.book_roots()))
                except (ValueError, KeyError, OSError) as error:
                    return self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                return self._send(data, media_type)
            if method == "GET" and len(parts) == 4 and parts[1] == "jobs" and parts[3] == "output":
                try:
                    validate_job_id(parts[2])
                    output = app.job_output(parts[2])
                    data = output.read_bytes()
                except (ValueError, OSError) as error:
                    return self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                media_type = _DOWNLOAD_TYPES.get(output.suffix.lower(), "application/octet-stream")
                return self._send(data, media_type, download=output.name)
            if method == "POST" and parts == ["api", "uploads"]:
                return self._upload(parse_qs(url.query).get("name", [""])[0])
            if method == "POST":
                if self.headers.get("X-UI-Token") != app.token:
                    return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                length = int(self.headers.get("Content-Length") or 0)
                if length > _MAX_BODY_BYTES:
                    return self._json({"error": "request too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:  # JSONDecodeError and UnicodeDecodeError (e.g. a binary body)
                    return self._json({"error": "request body must be UTF-8 JSON"}, HTTPStatus.BAD_REQUEST)
                if not isinstance(body, dict):
                    return self._json({"error": "request body must be a JSON object"}, HTTPStatus.BAD_REQUEST)
            query = parse_qs(url.query)
            try:
                if len(parts) >= 4 and parts[1] == "jobs":
                    validate_job_id(parts[2])
                    action = job_routes(parts[2]).get((method, "/".join(parts[3:])))
                elif len(parts) >= 4 and parts[1] == "series":
                    series_api.series_root(app.runs, parts[2])  # validates the id
                    action = series_routes(parts[2]).get((method, "/".join(parts[3:])))
                else:
                    action = global_routes.get((method, "/".join(parts[1:])))
                if action is None:
                    return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                self._json(action(query, body))
            except Exception as error:
                self._json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)

        def _upload(self, name: str) -> None:
            """Raw-body upload of a source book picked or dropped in the browser."""
            if self.headers.get("X-UI-Token") != app.token:
                return self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= _MAX_UPLOAD_BYTES:
                return self._json({"error": "file is empty or larger than 1 GB"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                path = drafts.store_upload(app.runs, name, self.rfile, length)
            except ValueError as error:
                return self._json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            self._json({"path": str(path)})

        def _page(self, parts: list[str]) -> None:
            name = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
            valid = (
                not parts
                or (parts[0] == "jobs" and len(parts) == 3 and parts[2] in _PAGES and name.fullmatch(parts[1]))
                or (parts[0] == "series" and (len(parts) == 1 or (len(parts) == 2 and name.fullmatch(parts[1]))))
            )
            if not valid:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            index = static.joinpath("index.html")
            if not index.is_file():
                return self._json(
                    {"error": "UI build missing; run `npm run build` in frontend/"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            self._send(index.read_bytes(), "text/html; charset=utf-8")

        def _asset(self, name: str) -> None:
            content_type = _ASSET_TYPES.get(Path(name).suffix)
            asset = static.joinpath("assets").joinpath(name)
            if not content_type or not re.fullmatch(r"[A-Za-z0-9._-]+", name) or not asset.is_file():
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            self._send(asset.read_bytes(), content_type)

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            self._send(data, "application/json; charset=utf-8", status)

        def _send(
            self,
            data: bytes,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
            download: str = "",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if download:
                # ASCII fallback plus the exact UTF-8 name (RFC 6266 / 5987).
                fallback = re.sub(r'[^A-Za-z0-9._ -]', "_", download)
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(download)}",
                )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve_ui(
    runs: Path,
    config_dir: Path,
    *,
    sample_dirs: list[Path] | None = None,
    template: Path | None = None,
    port: int = 8765,
    open_browser: bool = True,
    start_path: str = "/",
) -> None:
    """Serve the dashboard on loopback until interrupted."""
    app = UiApp(runs, config_dir, sample_dirs or [], template)
    port_ref = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref[0] = server.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}{start_path}"
    print(f"Translator UI: {url}  (Ctrl+C to stop; launched jobs keep running)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
