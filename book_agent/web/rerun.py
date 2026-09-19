"""What rerunning a completed stage would reset, for the dashboard's warning dialog.

A rerun is ``book-agent retry --stage X --resume``: stage X and every stage after it
lose their checkpoints and completed work units, then run again.
"""

from __future__ import annotations

from typing import Any

from ..pipeline_state import WorkflowStage, downstream_stages
from ..state import StageStatus, connect_state, get_job_metadata
from ..workflow import workflow_status
from ..workspace import JobWorkspace
from .estimate import stage_seconds

_ORDER = list(WorkflowStage)
# Paused here means waiting for a person (Glossary / Final review tabs), not interrupted.
_HUMAN_GATES = {WorkflowStage.APPROVE_GLOSSARY, WorkflowStage.COMPILE}
_RERUNNABLE = {StageStatus.COMPLETED.value, StageStatus.FAILED.value, StageStatus.PAUSED.value}


def parse_stage(name: str) -> WorkflowStage:
    try:
        return WorkflowStage(name)
    except ValueError:
        raise ValueError(f"unknown workflow stage: {name}") from None


def _at_or_before(stage: WorkflowStage, limit: WorkflowStage) -> bool:
    return _ORDER.index(stage) <= _ORDER.index(limit)


def rerun_preview(workspace: JobWorkspace, name: str) -> dict[str, Any]:
    """List the stages a rerun resets and what would be lost with them."""
    stage = parse_stage(name)
    statuses = {str(item["name"]): item for item in workflow_status(workspace)["stages"]}
    current = statuses.get(stage.value)
    status = str(current["status"]) if current else "unknown"
    if status not in _RERUNNABLE:
        raise ValueError(
            f"only a completed, failed, or paused stage can be rerun; {stage.value} is {status}"
        )
    if status == StageStatus.PAUSED.value and stage in _HUMAN_GATES:
        raise ValueError(
            f"{stage.value} is waiting for your review; finish it on its tab instead"
        )
    seconds = stage_seconds(workspace)
    affected = [stage, *downstream_stages(stage)]
    stages = [
        {
            "name": item.value,
            "status": str(statuses.get(item.value, {}).get("status", "pending")),
            "seconds": round(seconds[item.value]) if item.value in seconds else None,
        }
        for item in affected
    ]

    connection = connect_state(workspace.state_file)
    try:
        approved_for = get_job_metadata(connection, "final_review_approved_for")
    finally:
        connection.close()
    validate = statuses.get(WorkflowStage.VALIDATE_REPAIRED.value, {})
    manual_work = bool(approved_for) or str(validate.get("message", "")).startswith(
        "manual review resolved"
    ) or (workspace.root / "reports" / "final-human-review.resolutions.json").is_file()

    warnings: list[dict[str, str]] = []
    if _at_or_before(stage, WorkflowStage.APPROVE_GLOSSARY):
        warnings.append({
            "code": "glossary_approval",
            "message": "The glossary must be reviewed and approved again; the run pauses at that gate.",
        })
    if _at_or_before(stage, WorkflowStage.TRANSLATE):
        warnings.append({
            "code": "full_translation",
            "message": "The whole book is translated again. This is the most expensive rerun.",
        })
    if _at_or_before(stage, WorkflowStage.VALIDATE_REPAIRED) and manual_work:
        warnings.append({
            "code": "manual_review",
            "message": (
                "Manual review decisions and the final-draft approval are discarded; "
                "the review queue is rebuilt from the new draft."
            ),
        })
    if statuses.get(WorkflowStage.COMPILE.value, {}).get("status") == StageStatus.COMPLETED.value:
        warnings.append({
            "code": "compiled_epub",
            "message": "The compiled EPUB is replaced by the new output.",
        })
    known = [item["seconds"] for item in stages if item["seconds"] is not None]
    return {
        "stage": stage.value,
        "status": status,
        "stages": stages,
        "warnings": warnings,
        "previous_seconds": sum(known) if known else None,
    }
