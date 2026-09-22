"""Apply explicit human resolutions to the current repaired-review queue.

Each resolution becomes an ``edit`` event in the same tracked edit log the
Text tab writes to (``book_agent.text_edits``, docs/FULL_TEXT_REVIEW.md), so
Final review and manual text edits share one history and one compile
overlay. validate_repaired's stored draft is never rewritten here; a
resolved segment is one with an active edit, computed dynamically by
``unresolved_review_gate`` rather than by mutating the stored validation
report.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .atomic_io import atomic_write_text
from .hashing import sha256_file, sha256_text
from .pipeline_state import WorkflowStage, invalidate_stage_and_dependents
from .repair import RepairedValidationReport
from .state import (
    StageStatus,
    connect_state,
    get_stage_status,
    record_artifact,
    set_job_metadata,
)
from .text_edits import (
    SegmentEditRequest,
    active_edit_texts,
    apply_edit_batch,
    classify_check,
    current_draft_revision,
    events_by_segment,
    preview_manual_resolution,
    unresolved_review_gate,
)
from .workspace import JobWorkspace
from .stages.validate_repaired import (
    load_repaired_validation_report,
    load_validated_repaired_documents,
)


class ManualTextReplacement(BaseModel):
    """One bounded exact replacement against the current reviewed translation."""

    model_config = ConfigDict(extra="forbid")

    old_span: str = Field(min_length=1)
    new_span: str


class ManualReviewResolution(BaseModel):
    """One reviewed acceptance or source-grounded replacement."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment_id: str = Field(min_length=1)
    translated_text: str | None = None
    replacements: list[ManualTextReplacement] = Field(default_factory=list)
    reason: str = Field(min_length=3)
    override_deterministic_findings: bool = False

    @model_validator(mode="after")
    def reject_mixed_replacement_modes(self) -> "ManualReviewResolution":
        if self.translated_text is not None and self.replacements:
            raise ValueError("use translated_text or replacements, not both")
        old_spans = [item.old_span for item in self.replacements]
        if len(old_spans) != len(set(old_spans)):
            raise ValueError("manual replacement old_span values must be unique")
        return self


class ManualReviewResolutionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolutions: list[ManualReviewResolution] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> "ManualReviewResolutionSet":
        ids = [item.segment_id for item in self.resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError("manual review resolution IDs must be unique")
        return self


class ManualReviewDecision(str, Enum):
    """Explicit state used by the editable final-review worksheet."""

    PENDING = "pending"
    ACCEPT = "accept"
    REPLACE = "replace"


class ManualReviewWorksheetResolution(BaseModel):
    """Self-contained reviewer case with an editable, fail-closed verdict."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    source_text: str
    current_translation: str
    findings: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    decision: ManualReviewDecision = ManualReviewDecision.PENDING
    translated_text: str | None = None
    replacements: list[ManualTextReplacement] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def validate_verdict(self) -> "ManualReviewWorksheetResolution":
        if self.translated_text is not None and self.replacements:
            raise ValueError("use translated_text or replacements, not both")
        if self.decision is ManualReviewDecision.PENDING:
            return self
        if len(self.reason.strip()) < 3:
            raise ValueError("completed worksheet decisions require a review reason")
        if self.decision is ManualReviewDecision.ACCEPT:
            if self.translated_text is not None or self.replacements:
                raise ValueError("accept decisions cannot contain translation edits")
        elif self.translated_text is None and not self.replacements:
            raise ValueError(
                "replace decisions require translated_text or exact replacements"
            )
        return self


class ManualReviewWorksheet(BaseModel):
    """Editable companion to the consolidated human-review report."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    workspace: str
    draft_output_hash: str = Field(min_length=1)
    instructions: list[str] = Field(default_factory=list)
    resolutions: list[ManualReviewWorksheetResolution] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> "ManualReviewWorksheet":
        ids = [item.segment_id for item in self.resolutions]
        if len(ids) != len(set(ids)):
            raise ValueError("manual review worksheet IDs must be unique")
        return self

    def completed_resolution_set(self) -> ManualReviewResolutionSet:
        pending = [
            item.segment_id
            for item in self.resolutions
            if item.decision is ManualReviewDecision.PENDING
        ]
        if pending:
            raise ValueError(
                "manual review worksheet still has pending decisions: "
                + ", ".join(pending)
            )
        return ManualReviewResolutionSet(
            resolutions=[
                ManualReviewResolution(
                    segment_id=item.segment_id,
                    translated_text=(
                        item.translated_text
                        if item.decision is ManualReviewDecision.REPLACE
                        else None
                    ),
                    replacements=(
                        item.replacements
                        if item.decision is ManualReviewDecision.REPLACE
                        else []
                    ),
                    reason=item.reason,
                    override_deterministic_findings=(
                        item.decision is ManualReviewDecision.ACCEPT
                    ),
                )
                for item in self.resolutions
            ]
        )


def load_manual_review_resolution_file(
    workspace: JobWorkspace,
    path: str | Path,
) -> ManualReviewResolutionSet:
    """Load a legacy resolution set or a completed stale-safe worksheet."""
    payload = Path(path).read_text(encoding="utf-8")
    raw = json.loads(payload)
    if "draft_output_hash" not in raw:
        return ManualReviewResolutionSet.model_validate(raw)

    worksheet = ManualReviewWorksheet.model_validate(raw)
    connection = connect_state(workspace.state_file)
    try:
        stage = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if stage is None or stage["status"] != StageStatus.COMPLETED.value:
            raise RuntimeError(
                "validate_repaired must be complete before resolving its worksheet"
            )
        current_hash = current_draft_revision(workspace, str(stage["output_hash"]))
    finally:
        connection.close()
    if worksheet.draft_output_hash != current_hash:
        raise ValueError(
            "manual review worksheet is stale; regenerate it from the current "
            "validated draft"
        )

    current = {
        segment.segment_id: segment
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }
    stale_segments = sorted(
        item.segment_id
        for item in worksheet.resolutions
        if item.segment_id not in current
        or item.source_text != current[item.segment_id].source_text
        or item.current_translation != current[item.segment_id].translated_text
    )
    if stale_segments:
        raise ValueError(
            "manual review worksheet text no longer matches the validated draft: "
            + ", ".join(stale_segments)
        )
    return worksheet.completed_resolution_set()


def resolve_manual_review(
    workspace: JobWorkspace,
    resolution_set: ManualReviewResolutionSet,
) -> RepairedValidationReport:
    """Apply reviewed resolutions as tracked edit-log events.

    validate_repaired's stored draft is never rewritten: each resolution is
    checked (the same deterministic check an edit uses) and, if it passes,
    appended to the edit log. A segment counts as resolved for the review
    queue as soon as it has an active edit; see ``unresolved_review_gate``.
    """
    documents = {
        item.document.manifest_id: item for item in load_validated_repaired_documents(workspace)
    }
    current_report = load_repaired_validation_report(workspace)
    resolution_by_id = {
        item.segment_id: item for item in resolution_set.resolutions
    }
    requested_ids = {item.segment_id for item in resolution_set.resolutions}
    gate_before = unresolved_review_gate(workspace, current_report)
    unresolved_ids = set(gate_before.unresolved_review_ids)
    unsafe_acceptances = sorted(
        segment_id
        for segment_id in requested_ids - unresolved_ids
        if resolution_by_id[segment_id].translated_text is None
        and not resolution_by_id[segment_id].replacements
    )
    if unsafe_acceptances:
        raise ValueError(
            "out-of-queue manual resolutions must contain an explicit edit: "
            + ", ".join(unsafe_acceptances)
        )

    segment_owner: dict[str, str] = {}
    pipeline_text_by_id: dict[str, str] = {}
    for document_id, repaired in documents.items():
        for segment in repaired.document.segments:
            pipeline_text_by_id[segment.segment_id] = segment.translated_text
            if segment.segment_id in requested_ids:
                segment_owner[segment.segment_id] = document_id
    missing = sorted(requested_ids - set(segment_owner))
    if missing:
        raise ValueError("manual resolution segments are absent: " + ", ".join(missing))

    active_before = active_edit_texts(workspace)
    event_groups = events_by_segment(workspace)

    # Phase 1: compute and check every proposed replacement before writing anything,
    # so the set is applied atomically (all pass, or none are appended).
    plans: dict[str, tuple[str, str, str, str]] = {}
    verdict_rows: list[dict[str, object]] = []
    for segment_id, resolution in resolution_by_id.items():
        current_text = active_before.get(segment_id, pipeline_text_by_id[segment_id])
        replacement = (
            resolution.translated_text
            if resolution.translated_text is not None
            else current_text
        )
        for edit in resolution.replacements:
            occurrences = replacement.count(edit.old_span)
            if occurrences != 1:
                raise ValueError(
                    f"manual replacement for {segment_id} requires exactly one "
                    f"old_span occurrence, found {occurrences}"
                )
            replacement = replacement.replace(edit.old_span, edit.new_span, 1)
        override_reason = (
            resolution.reason if resolution.override_deterministic_findings else ""
        )
        classification = classify_check(workspace, segment_id, replacement)
        if classification["hard"]:
            messages = "; ".join(item["message"] for item in classification["hard"])
            raise ValueError(
                "manual resolutions failed deterministic validation: "
                f"{segment_id} ({messages})"
            )
        if classification["overridable"] and not override_reason:
            messages = "; ".join(item["message"] for item in classification["overridable"])
            raise ValueError(
                "manual resolutions failed deterministic validation: "
                f"{segment_id} ({messages})"
            )
        source_text = next(
            item.source_text
            for item in documents[segment_owner[segment_id]].document.segments
            if item.segment_id == segment_id
        )
        verdict_rows.append(
            {
                "segment_id": segment_id,
                "source_text": source_text,
                "previous_translation": current_text,
                "final_translation": replacement,
                "decision": "replace" if replacement != current_text else "accept",
                "reason": resolution.reason,
                "deterministic_override": resolution.override_deterministic_findings,
            }
        )
        prior_events = event_groups.get(segment_id, [])
        expected_event_id = prior_events[-1].event_id if prior_events else ""
        plans[segment_id] = (
            replacement,
            pipeline_text_by_id[segment_id],
            override_reason,
            expected_event_id,
        )

    # Phase 2: commit every resolution under one edit-log lock. A concurrent
    # writer or any revalidation failure rejects the whole batch before bytes
    # are appended.
    apply_edit_batch(
        workspace,
        [
            SegmentEditRequest(
                segment_id=segment_id,
                text=plans[segment_id][0],
                reason=resolution.reason,
                base_target_sha256=sha256_text(plans[segment_id][1]),
                override_reason=plans[segment_id][2],
                expected_event_id=plans[segment_id][3],
            )
            for segment_id, resolution in resolution_by_id.items()
        ],
    )

    gate_after = unresolved_review_gate(workspace, current_report)
    remaining_defects = set(current_report.defect_segment_ids) & set(
        gate_after.unresolved_review_ids
    )
    remaining_approvals = set(current_report.approval_segment_ids) & set(
        gate_after.unresolved_review_ids
    )
    report = RepairedValidationReport(
        document_count=current_report.document_count,
        segment_count=current_report.segment_count,
        passed=not gate_after.unresolved_review_ids,
        remaining_issue_count=len(gate_after.unresolved_review_ids),
        review_segment_ids=gate_after.unresolved_review_ids,
        remaining_defect_count=gate_after.defect_count,
        approval_required_count=gate_after.approval_count,
        defect_segment_ids=sorted(remaining_defects),
        approval_segment_ids=sorted(remaining_approvals),
    )

    connection = connect_state(workspace.state_file)
    try:
        stage = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if stage is None or stage["status"] != StageStatus.COMPLETED.value:
            raise RuntimeError("validate_repaired must be complete before manual resolution")
        resolution_path = workspace.directory("reports") / "manual-review-resolutions.json"
        atomic_write_text(resolution_path, resolution_set.model_dump_json(indent=2))
        record_artifact(
            connection,
            resolution_path.relative_to(workspace.root).as_posix(),
            WorkflowStage.VALIDATE_REPAIRED.value,
            "manual_review_resolutions",
            sha256_file(resolution_path),
            resolution_path.stat().st_size,
        )
        set_job_metadata(
            connection,
            "manual_review_resolutions",
            resolution_path.relative_to(workspace.root).as_posix(),
        )
        _write_manual_review_verdict(
            connection,
            workspace,
            verdict_rows,
            gate_after.unresolved_review_ids,
        )
        # So `--resume` re-attempts compile: its input hash already changes with
        # the edit log, but its stored stage status would otherwise stay
        # "completed" and be skipped by the workflow loop.
        invalidate_stage_and_dependents(connection, WorkflowStage.COMPILE)
        return report
    finally:
        connection.close()


def _write_manual_review_verdict(
    connection,
    workspace: JobWorkspace,
    verdict_rows: list[dict[str, object]],
    remaining_ids: list[str],
) -> None:
    """Publish the reviewer-authored hand-off used for final approval."""
    report = {
        "schema_version": 1,
        "workspace": str(workspace.root),
        "resolved_count": len(verdict_rows),
        "remaining_count": len(remaining_ids),
        "remaining_segment_ids": remaining_ids,
        "ready_for_final_approval": not remaining_ids,
        "verdicts": verdict_rows,
    }
    root = workspace.directory("reports")
    json_path = root / "final-human-review-verdict.json"
    markdown_path = root / "final-human-review-verdict.md"
    atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Final human-review verdict",
        "",
        f"- Resolved cases: {len(verdict_rows)}",
        f"- Remaining cases: {len(remaining_ids)}",
        "- Ready for final approval: " + ("yes" if not remaining_ids else "no"),
        "",
    ]
    for index, row in enumerate(verdict_rows, start=1):
        lines.extend(
            [
                f"## {index}. {row['segment_id']}",
                "",
                f"- Verdict: `{row['decision']}`",
                "- Deterministic finding override: "
                f"`{str(row['deterministic_override']).lower()}`",
                f"- Reason: {row['reason']}",
                "",
                "### Source",
                "",
                *[f"    {line}" for line in str(row["source_text"]).splitlines()],
                "",
                "### Previous translation",
                "",
                *[
                    f"    {line}"
                    for line in str(row["previous_translation"]).splitlines()
                ],
                "",
                "### Approved translation",
                "",
                *[
                    f"    {line}"
                    for line in str(row["final_translation"]).splitlines()
                ],
                "",
            ]
        )
    if remaining_ids:
        lines.extend(
            [
                "## Remaining cases",
                "",
                *[f"- `{segment_id}`" for segment_id in remaining_ids],
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Approval command",
                "",
                "```powershell",
                "python -m book_agent.cli approve `",
                f"    \"{workspace.root}\" `",
                "    --final --resume",
                "```",
                "",
            ]
        )
    atomic_write_text(markdown_path, "\n".join(lines))
    for path, kind in (
        (json_path, "manual_review_verdict_json"),
        (markdown_path, "manual_review_verdict_markdown"),
    ):
        record_artifact(
            connection,
            path.relative_to(workspace.root).as_posix(),
            WorkflowStage.VALIDATE_REPAIRED.value,
            kind,
            sha256_file(path),
            path.stat().st_size,
        )
    set_job_metadata(
        connection,
        "manual_review_verdict_report",
        markdown_path.relative_to(workspace.root).as_posix(),
    )
