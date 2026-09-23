"""Final human-review operations behind the dashboard's review page.

Nothing is edited directly: drafts are saved as the standard
``final-human-review.resolutions.json`` worksheet, and applying them goes
through the same stale-safe ``resolve-review`` validation as the CLI.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_text
from .manual_review import (
    ManualReviewDecision,
    ManualReviewWorksheet,
    load_manual_review_resolution_file,
    preview_manual_resolution,
    resolve_manual_review,
)
from .pipeline_state import WorkflowStage
from .stages.compile import load_compiled_epub_path
from .stages.validate_repaired import load_repaired_validation_report
from .state import connect_state, get_stage_status
from .text_edits import current_draft_revision
from .workflow import (
    ProgressEvent,
    approve_final_draft,
    load_workspace_config,
    run_workflow,
    workflow_status,
)
from .workspace import JobWorkspace

WORKSHEET_NAME = "final-human-review.decisions.json"
DRAFT_NAME = "final-human-review.resolutions.json"
SEGMENTS_NAME = "unresolved-review-segments.json"


class ReviewSession:
    """Workspace-bound review operations shared by all HTTP requests."""

    def __init__(self, workspace: JobWorkspace) -> None:
        self.workspace = workspace
        self.reports = workspace.root / "reports"
        self.draft_path = self.reports / DRAFT_NAME
        self._lock = threading.Lock()
        self._compile: dict[str, Any] = {"state": "idle", "events": [], "result": None}

    def payload(self) -> dict[str, Any]:
        """Merge the worksheet, segment context, any saved draft, and job status."""
        worksheet = _read_json(self.reports / WORKSHEET_NAME)
        segments = _read_json(self.reports / SEGMENTS_NAME) or {}
        current_hash = self._validated_draft_hash()
        stale = bool(worksheet) and worksheet.get("draft_output_hash") != current_hash
        draft = _read_json(self.draft_path)
        if draft and worksheet and draft.get("draft_output_hash") == worksheet.get(
            "draft_output_hash"
        ):
            saved = {item["segment_id"]: item for item in draft.get("resolutions", [])}
            for item in worksheet["resolutions"]:
                if item["segment_id"] in saved:
                    item.update(
                        {
                            key: saved[item["segment_id"]][key]
                            for key in ("decision", "translated_text", "replacements", "reason")
                            if key in saved[item["segment_id"]]
                        }
                    )
        context = {item["segment_id"]: item for item in segments.get("segments", [])}
        return {
            "worksheet": worksheet,
            "context": context,
            "stale": stale,
            "draft_saved": bool(draft),
            "status": _jsonable(workflow_status(self.workspace)),
            "compile": self.compile_state(),
            "compile_limit": self._compile_limit(),
            "paths": {
                "workspace": str(self.workspace.root),
                "draft": str(self.draft_path),
            },
        }

    def save(self, worksheet: dict[str, Any]) -> ManualReviewWorksheet:
        """Validate and atomically persist an in-progress worksheet."""
        original = _read_json(self.reports / WORKSHEET_NAME)
        if not original:
            raise ValueError("no final-review worksheet exists for this job")
        if worksheet.get("draft_output_hash") != original.get("draft_output_hash"):
            raise ValueError("worksheet belongs to an older draft; reload the page")
        if worksheet.get("draft_output_hash") != self._validated_draft_hash():
            raise ValueError("worksheet belongs to an older draft; reload the page")
        model = ManualReviewWorksheet.model_validate(worksheet)
        with self._lock:
            atomic_write_text(self.draft_path, model.model_dump_json(indent=2))
        return model

    def check(self, segment_id: str, decision: str, text: str | None) -> dict[str, Any]:
        """Preview the deterministic issues that would block one decision."""
        issues = preview_manual_resolution(
            self.workspace, segment_id, None if decision == "accept" else text
        )
        return {"segment_id": segment_id, "blocking": issues}

    def apply(
        self, worksheet: dict[str, Any], approve_final: bool, partial: bool = False
    ) -> dict[str, Any]:
        """Save, then resolve the queue exactly as ``resolve-review`` does.

        ``partial`` applies only the decided segments and leaves the pending ones
        unresolved, which the compile limit must allow.  Approval is granted once
        the remaining queue fits within that limit.
        """
        model = self.save(worksheet)
        limit = self._compile_limit()
        with self._lock:
            if partial:
                pending = [
                    item.segment_id
                    for item in model.resolutions
                    if item.decision is ManualReviewDecision.PENDING
                ]
                if len(pending) > limit:
                    raise ValueError(
                        f"{len(pending)} segment(s) are still pending; the compile "
                        f"limit allows at most {limit} unresolved"
                    )
                if model.draft_output_hash != self._validated_draft_hash():
                    raise ValueError("worksheet belongs to an older draft; reload the page")
                decided = model.model_copy(
                    update={
                        "resolutions": [
                            item
                            for item in model.resolutions
                            if item.decision is not ManualReviewDecision.PENDING
                        ]
                    }
                )
                report = (
                    resolve_manual_review(
                        self.workspace, decided.completed_resolution_set()
                    )
                    if decided.resolutions
                    else load_repaired_validation_report(self.workspace)
                )
            else:
                resolution_set = load_manual_review_resolution_file(
                    self.workspace, self.draft_path
                )
                report = resolve_manual_review(self.workspace, resolution_set)
            approved = False
            if approve_final and len(report.review_segment_ids) <= limit:
                approve_final_draft(self.workspace)
                approved = True
        return {"report": json.loads(report.model_dump_json()), "approved": approved}

    def start_compile(self) -> dict[str, Any]:
        """Resume the workflow (compile + validate_epub) on a background thread."""
        with self._lock:
            if self._compile["state"] == "running":
                raise ValueError("compilation is already running")
            self._compile = {"state": "running", "events": [], "result": None}
        threading.Thread(target=self._run_compile, daemon=True).start()
        return self.compile_state()

    def compile_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._compile["state"],
                "events": list(self._compile["events"][-50:]),
                "result": self._compile["result"],
            }

    def _run_compile(self) -> None:
        def progress(event: ProgressEvent) -> None:
            with self._lock:
                self._compile["events"].append(
                    {"stage": event.stage, "status": event.status, "message": event.message}
                )

        try:
            result = run_workflow(self.workspace, progress=progress)
            outcome = {
                "result": result.result,
                "exit_code": int(result.exit_code),
                "stage": result.stage,
                "message": result.message,
                "output": _compiled_output(self.workspace),
            }
            state = "done"
        except Exception as error:  # surfaced to the reviewer, not swallowed
            outcome = {"result": "failed", "message": str(error)}
            state = "failed"
        with self._lock:
            self._compile["state"] = state
            self._compile["result"] = outcome

    def _compile_limit(self) -> int:
        config = load_workspace_config(self.workspace)
        return config.workflow.compile_max_unresolved_review_segments

    def _validated_draft_hash(self) -> str:
        connection = connect_state(self.workspace.state_file)
        try:
            record = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
            return (
                current_draft_revision(self.workspace, str(record["output_hash"]))
                if record
                else ""
            )
        finally:
            connection.close()


def _compiled_output(workspace: JobWorkspace) -> str:
    try:
        return load_compiled_epub_path(workspace)
    except (FileNotFoundError, ValueError):
        return ""


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
