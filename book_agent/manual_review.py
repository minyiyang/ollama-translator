"""Apply explicit human resolutions to the current repaired-review queue."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .atomic_io import atomic_write_text
from .audit import (
    AuditCategory,
    AuditSeverity,
    audit_translated_document,
    reapply_quantity_adjudications,
)
from .config import AppConfig
from .hashing import sha256_file
from .pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
)
from .repair import (
    RepairDisposition,
    RepairedDocument,
    RepairedDocumentValidation,
    RepairedValidationReport,
    SegmentRepair,
)
from .state import (
    StageStatus,
    connect_state,
    get_artifact,
    get_job_metadata,
    get_stage_status,
    list_artifacts,
    record_artifact,
    set_job_metadata,
    set_stage_status,
)
from .stage_artifacts import list_active_stage_artifacts
from .translation import render_translated_document
from .workspace import JobWorkspace
from .stages.preprocess import load_preprocessed_documents
from .stages.audit import load_document_audits
from .stages.validate_repaired import (
    VALIDATE_REPAIRED_STAGE_VERSION,
    load_repaired_document_validations,
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


_NON_OVERRIDABLE_MANUAL_CATEGORIES = {
    AuditCategory.STRUCTURE,
    AuditCategory.EMPTY,
    AuditCategory.UNTRANSLATED,
    AuditCategory.DUPLICATION,
    AuditCategory.PUNCTUATION,
}


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
        current_hash = str(stage["output_hash"])
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
    """Apply reviewed resolutions and publish a deterministically checked draft."""
    config = AppConfig.model_validate_json(
        workspace.config_file.read_text(encoding="utf-8")
    )
    sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
    documents = {
        item.document.manifest_id: item for item in load_validated_repaired_documents(workspace)
    }
    validations = {
        item.document_id: item for item in load_repaired_document_validations(workspace)
    }
    current_report = load_repaired_validation_report(workspace)
    resolution_by_id = {
        item.segment_id: item for item in resolution_set.resolutions
    }
    requested_ids = {item.segment_id for item in resolution_set.resolutions}
    unresolved_ids = set(current_report.review_segment_ids)
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
    for document_id, repaired in documents.items():
        for segment in repaired.document.segments:
            if segment.segment_id in requested_ids:
                segment_owner[segment.segment_id] = document_id
    missing = sorted(requested_ids - set(segment_owner))
    if missing:
        raise ValueError("manual resolution segments are absent: " + ", ".join(missing))

    changed_documents: set[str] = set()
    verdict_rows: list[dict[str, object]] = []
    for segment_id, resolution in resolution_by_id.items():
        document_id = segment_owner[segment_id]
        repaired = documents[document_id]
        current_text = next(
            item.translated_text
            for item in repaired.document.segments
            if item.segment_id == segment_id
        )
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
        changed = replacement != current_text
        source_text = next(
            item.source_text
            for item in repaired.document.segments
            if item.segment_id == segment_id
        )
        verdict_rows.append(
            {
                "segment_id": segment_id,
                "source_text": source_text,
                "previous_translation": current_text,
                "final_translation": replacement,
                "decision": "replace" if changed else "accept",
                "reason": resolution.reason,
                "deterministic_override": resolution.override_deterministic_findings,
            }
        )
        repaired.document = repaired.document.model_copy(
            update={
                "segments": [
                    item.model_copy(update={"translated_text": replacement})
                    if item.segment_id == segment_id
                    else item
                    for item in repaired.document.segments
                ]
            }
        )
        found_repair = False
        updated_repairs = []
        for repair in repaired.repairs:
            if repair.segment_id != segment_id:
                updated_repairs.append(repair)
                continue
            found_repair = True
            updated_repairs.append(
                repair.model_copy(
                    update={
                        "disposition": (
                            RepairDisposition.REPAIRED
                            if changed
                            else RepairDisposition.ACCEPTED
                        ),
                        "repaired_translation": replacement if changed else "",
                        "message": f"manual review resolved: {resolution.reason}",
                    }
                )
            )
        if not found_repair:
            prior_issues = [
                issue
                for issue in validations[document_id].deterministic_audit.issues
                if issue.segment_id == segment_id
            ]
            updated_repairs.append(
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=(
                        RepairDisposition.REPAIRED
                        if changed
                        else RepairDisposition.ACCEPTED
                    ),
                    original_translation=current_text,
                    repaired_translation=replacement if changed else "",
                    issues=prior_issues,
                    attempts=0,
                    message=f"manual review resolved: {resolution.reason}",
                )
            )
        repaired.repairs = updated_repairs
        changed_documents.add(document_id)

    initial_audits = {
        item.document_id: item for item in load_document_audits(workspace)
    }
    deterministic_by_document = {
        document_id: reapply_quantity_adjudications(
            audit_translated_document(
                sources[document_id], repaired.document, config.audit
            ),
            initial_audits.get(document_id),
        )
        for document_id, repaired in documents.items()
    }
    overridden_ids = {
        segment_id
        for segment_id, resolution in resolution_by_id.items()
        if resolution.override_deterministic_findings
    }
    unsafe: list[str] = []
    for segment_id in requested_ids:
        audit = deterministic_by_document[segment_owner[segment_id]]
        if any(
            issue.segment_id == segment_id
            and issue.severity.rank >= AuditSeverity.MEDIUM.rank
            and not (
                segment_id in overridden_ids
                and issue.category not in _NON_OVERRIDABLE_MANUAL_CATEGORIES
            )
            for issue in audit.issues
        ):
            unsafe.append(segment_id)
    if unsafe:
        raise ValueError(
            "manual resolutions failed deterministic validation: "
            + ", ".join(sorted(unsafe))
        )

    remaining_all: set[str] = set()
    remaining_all_defects: set[str] = set()
    remaining_all_approvals: set[str] = set()
    updated_validations: dict[str, RepairedDocumentValidation] = {}
    for document_id, validation in validations.items():
        deterministic = deterministic_by_document[document_id]
        remaining_defects = set(validation.defect_segment_ids) - requested_ids
        remaining_approvals = set(validation.approval_segment_ids) - requested_ids
        if (
            validation.review_segment_ids
            and not validation.defect_segment_ids
            and not validation.approval_segment_ids
        ):
            remaining_defects.update(
                set(validation.review_segment_ids) - requested_ids
            )
        remaining_defects.update(
            issue.segment_id
            for issue in deterministic.issues
            if issue.severity.rank >= AuditSeverity.MEDIUM.rank
            and not (
                issue.segment_id in overridden_ids
                and issue.category not in _NON_OVERRIDABLE_MANUAL_CATEGORIES
            )
        )
        remaining_approvals.difference_update(remaining_defects)
        remaining = remaining_defects | remaining_approvals
        semantic = [
            item.model_copy(
                update={
                    "passed": True,
                    "current_acceptable": True,
                    "message": "Resolved by explicit human review.",
                }
            )
            if item.segment_id in requested_ids
            else item
            for item in validation.semantic_verifications
        ]
        updated_validations[document_id] = validation.model_copy(
            update={
                "deterministic_audit": deterministic,
                "semantic_verifications": semantic,
                "passed": not remaining,
                "review_segment_ids": sorted(remaining),
                "defect_segment_ids": sorted(remaining_defects),
                "approval_segment_ids": sorted(remaining_approvals),
            }
        )
        remaining_all.update(remaining)
        remaining_all_defects.update(remaining_defects)
        remaining_all_approvals.update(remaining_approvals)

    report = RepairedValidationReport(
        document_count=current_report.document_count,
        segment_count=current_report.segment_count,
        passed=not remaining_all,
        remaining_issue_count=len(remaining_all),
        review_segment_ids=sorted(remaining_all),
        remaining_defect_count=len(remaining_all_defects),
        approval_required_count=len(remaining_all_approvals),
        defect_segment_ids=sorted(remaining_all_defects),
        approval_segment_ids=sorted(remaining_all_approvals),
    )

    connection = connect_state(workspace.state_file)
    try:
        stage_name = WorkflowStage.VALIDATE_REPAIRED.value
        stage = get_stage_status(connection, stage_name)
        if stage is None or stage["status"] != StageStatus.COMPLETED.value:
            raise RuntimeError("validate_repaired must be complete before manual resolution")
        repair_review_stage = get_stage_status(
            connection, WorkflowStage.REPAIR_REVIEW.value
        )
        if (
            repair_review_stage is None
            or repair_review_stage["status"] != StageStatus.COMPLETED.value
        ):
            raise RuntimeError("repair_review must be complete before manual resolution")
        current_input_hash = build_stage_input_hash(
            {
                "repair_review": str(repair_review_stage["output_hash"]),
                "audit": config.audit.model_dump_json(),
                "model": config.audit.verifier_model or config.audit.model,
                "stage_version": VALIDATE_REPAIRED_STAGE_VERSION,
            }
        )
        report_relative = get_job_metadata(connection, "repaired_validation_report")
        if not report_relative:
            raise FileNotFoundError("repaired validation report is not recorded")
        report_path = workspace.directory(report_relative)

        artifacts = list_active_stage_artifacts(
            connection,
            stage_name,
            root_metadata_key="validated_repaired_root",
            report_metadata_key="repaired_validation_report",
        )
        document_paths: dict[str, Path] = {}
        validation_paths: dict[str, Path] = {}
        for artifact in artifacts:
            path = workspace.directory(str(artifact["path"]))
            if artifact["kind"] == "validated_repaired_document_json":
                item = RepairedDocument.model_validate_json(path.read_text(encoding="utf-8"))
                document_paths[item.document.manifest_id] = path
            elif artifact["kind"] == "repaired_document_validation":
                item = RepairedDocumentValidation.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                validation_paths[item.document_id] = path

        for document_id in changed_documents:
            json_path = document_paths[document_id]
            text_path = json_path.with_suffix(".txt")
            atomic_write_text(json_path, documents[document_id].model_dump_json(indent=2))
            atomic_write_text(
                text_path, render_translated_document(documents[document_id].document)
            )
            _record_existing_artifact(connection, workspace, json_path)
            _record_existing_artifact(connection, workspace, text_path)

        for document_id, validation in updated_validations.items():
            path = validation_paths[document_id]
            atomic_write_text(path, validation.model_dump_json(indent=2))
            _record_existing_artifact(connection, workspace, path)

        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_existing_artifact(connection, workspace, report_path)
        resolution_path = report_path.parent / "manual-review-resolutions.json"
        atomic_write_text(resolution_path, resolution_set.model_dump_json(indent=2))
        record_artifact(
            connection,
            resolution_path.relative_to(workspace.root).as_posix(),
            stage_name,
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
            sorted(remaining_all),
        )
        invalidate_stage_and_dependents(connection, WorkflowStage.COMPILE)
        output_hash = build_stage_output_hash(connection, WorkflowStage.VALIDATE_REPAIRED)
        set_stage_status(
            connection,
            stage_name,
            StageStatus.COMPLETED,
            attempts=int(stage["attempts"]),
            input_hash=current_input_hash,
            output_hash=output_hash,
            message=(
                f"{len(remaining_all)} segment(s) require human review"
                if remaining_all
                else "manual review resolved"
            ),
        )
        return report
    finally:
        connection.close()


def _record_existing_artifact(connection, workspace: JobWorkspace, path: Path) -> None:
    relative = path.relative_to(workspace.root).as_posix()
    existing = get_artifact(connection, relative)
    if existing is None:
        raise FileNotFoundError(f"artifact is not recorded: {relative}")
    record_artifact(
        connection,
        relative,
        str(existing["stage"]),
        str(existing["kind"]),
        sha256_file(path),
        path.stat().st_size,
    )


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
