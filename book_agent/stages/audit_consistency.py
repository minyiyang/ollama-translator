"""audit_consistency: book-level consistency findings for repair (docs/BOOK_CONSISTENCY.md, 8.2).

Runs after ``audit_translation`` on the same text repair will work on (the
translation, or its rescued generation), with the latest human edits taking
their place as the reference rendering. Deterministic: no model calls.

Findings are stored per document beside a report that is always written, and
``repair_translation`` merges them into what it repairs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..atomic_io import atomic_write_text
from ..audit import AuditIssue, AuditSeverity
from ..config import AppConfig
from ..consistency import book_consistency_issues, book_segments
from ..hashing import hash_named_values, sha256_file, sha256_text
from ..ollama_client import OllamaClient
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..stage_artifacts import list_active_stage_artifacts
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    initialize_state,
    record_artifact,
    retire_stage_artifacts,
    set_job_metadata,
    set_stage_status,
)
from ..style_sheet import enabled_style_sheet
from ..text_edits import latest_human_texts
from ..workspace import JobWorkspace
from .translate import load_translated_documents

CONSISTENCY_STAGE_VERSION = "1"
_STAGE = WorkflowStage.AUDIT_CONSISTENCY


class DocumentConsistency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    issues: list[AuditIssue] = Field(default_factory=list)


class ConsistencyAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    repair_count: int = Field(ge=0, description="Findings at or above the repair severity.")
    by_severity: dict[str, int] = Field(default_factory=dict)
    documents_with_issues: dict[str, int] = Field(default_factory=dict)


def run_consistency_audit_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> ConsistencyAuditReport:
    """Find book-level consistency drift in the translation repair is about to work on."""
    del client  # deterministic
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        audit = _require_completed(connection, WorkflowStage.AUDIT_TRANSLATION)
        translation = _require_completed(connection, WorkflowStage.TRANSLATE)
        rescue = get_stage_status(connection, WorkflowStage.RESCUE_TRANSLATION.value)
        rescued = bool(
            rescue
            and rescue["status"] == StageStatus.COMPLETED.value
            and get_job_metadata(connection, "rescued_root")
        )
        human_texts = latest_human_texts(workspace)
        hash_fields = {
            "audit": str(audit["output_hash"]),
            "translation": str(translation["output_hash"]),
            "consistency": config.consistency.model_dump_json(),
            "direction": config.translation.direction.value,
            "repair_min_severity": config.audit.repair_min_severity,
            "human_edits": hash_named_values(human_texts),
            **_style_fields(workspace, config),
            "stage_version": CONSISTENCY_STAGE_VERSION,
        }
        if rescued:
            hash_fields["rescue"] = str(rescue["output_hash"])
        input_hash = build_stage_input_hash(hash_fields)
        if stage_is_current(connection, _STAGE, input_hash, artifact_root=workspace.root):
            return load_consistency_audit_report(workspace)
        previous = get_stage_status(connection, _STAGE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, _STAGE)
            previous = get_stage_status(connection, _STAGE.value)
        retire_stage_artifacts(connection, _STAGE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection, _STAGE.value, StageStatus.RUNNING, attempts=attempts, input_hash=input_hash
        )

        documents = load_translated_documents(workspace)
        segments = book_segments(documents, human_texts=human_texts)
        issues: list[AuditIssue] = []
        if config.consistency.enabled:
            settings = config.consistency.settings(config.translation.direction.target_language.value)
            issues = book_consistency_issues(
                segments, settings, enabled_style_sheet(workspace, config)
            )

        stage_root = workspace.directory(f"consistency/{input_hash[:16]}")
        owner = {segment.segment_id: segment.document_id for segment in segments}
        by_document: dict[str, list[AuditIssue]] = {}
        for issue in issues:
            by_document.setdefault(owner[issue.segment_id], []).append(issue)
        order = {document.manifest_id: document.order for document in documents}
        for document_id, document_issues in sorted(by_document.items(), key=lambda item: order[item[0]]):
            path = stage_root / f"{order[document_id]:04d}-{document_id}.consistency.json"
            atomic_write_text(
                path,
                DocumentConsistency(document_id=document_id, issues=document_issues).model_dump_json(indent=2),
            )
            _record_file(connection, workspace, path, "document_consistency")

        minimum = AuditSeverity(config.audit.repair_min_severity).rank
        severities: dict[str, int] = {}
        for issue in issues:
            severities[issue.severity.value] = severities.get(issue.severity.value, 0) + 1
        report = ConsistencyAuditReport(
            enabled=config.consistency.enabled,
            document_count=len(documents),
            segment_count=len(segments),
            issue_count=len(issues),
            repair_count=sum(1 for issue in issues if issue.severity.rank >= minimum),
            by_severity=severities,
            documents_with_issues={key: len(value) for key, value in by_document.items()},
        )
        report_path = stage_root / "consistency.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "consistency_report")
        set_job_metadata(connection, "consistency_report", report_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "consistency_root", stage_root.relative_to(workspace.root).as_posix())
        set_stage_status(
            connection,
            _STAGE.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=build_stage_output_hash(connection, _STAGE),
            message=_message(report),
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def _style_fields(workspace: JobWorkspace, config: AppConfig) -> dict[str, str]:
    style = enabled_style_sheet(workspace, config)
    return {"style_sheet": sha256_text(style.model_dump_json())} if style is not None else {}


def _message(report: ConsistencyAuditReport) -> str:
    if not report.enabled:
        return "consistency checks disabled"
    if not report.issue_count:
        return "no consistency drift found"
    listed = report.issue_count - report.repair_count
    tail = f"; {listed} listed only" if listed else ""
    return f"{report.repair_count} consistency finding(s) sent to repair{tail}"


def consistency_findings_current(connection) -> bool:
    """Whether this stage completed and published findings that repair should merge."""
    record = get_stage_status(connection, _STAGE.value)
    return bool(
        record
        and record["status"] == StageStatus.COMPLETED.value
        and record["output_hash"]
        and get_job_metadata(connection, "consistency_root")
    )


def load_consistency_issues(workspace: JobWorkspace) -> dict[str, list[AuditIssue]]:
    """document id -> consistency findings; empty when the stage has not published any."""
    connection = connect_state(workspace.state_file)
    try:
        if not consistency_findings_current(connection):
            return {}
        artifacts = list_active_stage_artifacts(
            connection,
            _STAGE.value,
            root_metadata_key="consistency_root",
            report_metadata_key="consistency_report",
        )
        findings: dict[str, list[AuditIssue]] = {}
        for artifact in artifacts:
            if artifact["kind"] != "document_consistency":
                continue
            path = workspace.directory(str(artifact["path"]))
            item = DocumentConsistency.model_validate_json(path.read_text(encoding="utf-8"))
            findings[item.document_id] = item.issues
        return findings
    finally:
        connection.close()


def load_consistency_audit_report(workspace: JobWorkspace) -> ConsistencyAuditReport:
    connection = connect_state(workspace.state_file)
    try:
        path = get_job_metadata(connection, "consistency_report")
    finally:
        connection.close()
    if not path:
        raise RuntimeError("the consistency audit report has not been published")
    return ConsistencyAuditReport.model_validate_json(
        workspace.directory(path).read_text(encoding="utf-8")
    )


def _record_file(connection, workspace: JobWorkspace, path, kind: str) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        _STAGE.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
    )


def _require_completed(connection, stage: WorkflowStage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, error: Exception) -> None:
    previous = get_stage_status(connection, _STAGE.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        _STAGE.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
