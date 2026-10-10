"""What rerunning a completed stage would reset, for the dashboard's warning dialog.

A rerun is ``book-agent retry --stage X --resume``: stage X and every stage after it
lose their checkpoints and completed work units, then run again.
"""

from __future__ import annotations

from typing import Any

from ..pipeline_state import WorkflowStage, downstream_stages
from ..state import StageStatus, connect_state, get_job_metadata
from ..text_edits import active_edit_texts
from ..workflow import workflow_status
from ..workspace import JobWorkspace
from .estimate import stage_seconds
from .jobs import shown_stages
from .messages import UserError, rerun_warning

_ORDER = list(WorkflowStage)
# Paused here means waiting for a person (Glossary / Final review tabs), not interrupted.
_HUMAN_GATES = {WorkflowStage.APPROVE_GLOSSARY, WorkflowStage.COMPILE}
_RERUNNABLE = {StageStatus.COMPLETED.value, StageStatus.FAILED.value, StageStatus.PAUSED.value}


def parse_stage(name: str) -> WorkflowStage:
    try:
        return WorkflowStage(name)
    except ValueError:
        raise UserError("stage_unknown", stage=name) from None


def _at_or_before(stage: WorkflowStage, limit: WorkflowStage) -> bool:
    return _ORDER.index(stage) <= _ORDER.index(limit)


def rerun_preview(workspace: JobWorkspace, name: str, also: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """List the stages a rerun resets and what would be lost with them.

    `also` names further stages reset with it: stages on another branch of
    the pipeline, which a rerun of `name` alone would not redo (a changed
    config can reach both the glossary and the story summaries)."""
    stage = parse_stage(name)
    others = [parse_stage(item) for item in also if item and item != name]
    statuses = {str(item["name"]): item for item in workflow_status(workspace)["stages"]}
    for item in (stage, *others):
        current = statuses.get(item.value)
        status = str(current["status"]) if current else "unknown"
        if status not in _RERUNNABLE:
            raise UserError("stage_not_rerunnable", stage=item.value, status=status)
        if status == StageStatus.PAUSED.value and item in _HUMAN_GATES:
            raise UserError("stage_waiting_review", stage=item.value)
    status = str(statuses[stage.value]["status"])
    seconds = stage_seconds(workspace)
    reset = {candidate for item in (stage, *others) for candidate in (item, *downstream_stages(item))}
    affected = [candidate for candidate in _ORDER if candidate in reset]
    # What is lost is told by the earliest stage reset.
    stage_asked, stage = stage, affected[0]
    stages = [
        {
            "name": item.value,
            "status": str(statuses.get(item.value, {}).get("status", "pending")),
            "seconds": round(seconds[item.value]) if item.value in seconds else None,
        }
        for item in affected
    ]
    stages = shown_stages(workspace.source_file, stages)

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
        warnings.append(rerun_warning("glossary_approval"))
    if _at_or_before(stage, WorkflowStage.TRANSLATE):
        warnings.append(rerun_warning("full_translation"))
    if _at_or_before(stage, WorkflowStage.VALIDATE_REPAIRED) and manual_work:
        warnings.append(rerun_warning("manual_review"))
    if statuses.get(WorkflowStage.COMPILE.value, {}).get("status") == StageStatus.COMPLETED.value:
        warnings.append(rerun_warning("compiled_epub"))
    if _at_or_before(stage, WorkflowStage.VALIDATE_REPAIRED) and active_edit_texts(workspace):
        warnings.append(rerun_warning("text_edits"))
    known = [item["seconds"] for item in stages if item["seconds"] is not None]
    return {
        "stage": stage_asked.value,
        "also": [item.value for item in others],
        "status": status,
        "stages": stages,
        "warnings": warnings,
        "previous_seconds": sum(known) if known else None,
    }
