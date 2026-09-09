"""Qwen-only batch repair of failures collected by repaired-draft review."""

from __future__ import annotations

from ..atomic_io import atomic_write_text
from ..audit import (
    AuditCategory,
    AuditIssue,
    AuditSeverity,
    audit_translated_document,
)
from ..config import AppConfig
from ..hashing import sha256_file
from ..ollama_client import OllamaClient
from ..stage_progress import (
    group_by_context_bucket,
    report_segment_result,
    report_stage_plan,
    request_context_bucket,
)
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..repair import RepairDisposition, RepairedDocument, TranslationRepairReport
from ..state import (
    StageStatus,
    connect_state,
    get_stage_status,
    initialize_state,
    list_artifacts,
    record_artifact,
    retire_stage_artifacts,
    set_job_metadata,
    set_stage_status,
)
from ..stage_artifacts import list_active_stage_artifacts, require_unique_items
from ..translation import render_translated_document
from ..workspace import JobWorkspace
from .preprocess import load_preprocessed_documents
from .reprose import load_post_repair_documents
from .review_repaired import load_repaired_review_results
from .validate_repaired import (
    _build_feedback_repair_prompt,
    _replace_document_repair,
    _retain_accepted_originals,
    _run_feedback_repair,
)


REPAIR_REVIEW_STAGE_VERSION = "16"


class _SemanticRepairRequired(RuntimeError):
    """Internal control flow used by the deterministic pre-screen."""


class _PreScreenClient:
    def generate_text(self, *args, **kwargs):
        raise _SemanticRepairRequired


def run_review_repair_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient,
) -> TranslationRepairReport:
    """Repair every initial review failure in one Qwen-homogeneous stage."""
    stage = WorkflowStage.REPAIR_REVIEW
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        review_stage = _require_completed(connection, WorkflowStage.REVIEW_REPAIRED)
        input_hash = build_stage_input_hash(
            {
                "review": str(review_stage["output_hash"]),
                "translation": config.translation.model_dump_json(),
                "model": config.ollama.model,
                "stage_version": REPAIR_REVIEW_STAGE_VERSION,
            }
        )
        if stage_is_current(connection, stage, input_hash, artifact_root=workspace.root):
            return load_review_repair_report(workspace, connection=connection)
        previous = get_stage_status(connection, stage.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, stage)
            previous = get_stage_status(connection, stage.value)
        retire_stage_artifacts(connection, stage.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection, stage.value, StageStatus.RUNNING,
            attempts=attempts, input_hash=input_hash,
        )

        sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
        reviewed = load_repaired_review_results(workspace)
        documents = load_post_repair_documents(workspace, config)
        stage_root = workspace.directory(f"repaired/{input_hash[:16]}-review-repair")
        stage_root.mkdir(parents=True, exist_ok=True)
        by_document = {item.document.manifest_id: item for item in documents}
        targets: list[dict[str, object]] = []
        prescreen_results: list[tuple[str, str]] = []
        for repaired in documents:
            document_id = repaired.document.manifest_id
            current = by_document[document_id]
            review_result = reviewed.get(document_id)
            failed = {
                item.segment_id: item
                for item in (review_result.verifications if review_result else [])
                if not item.passed
            }
            repairs_by_id = {
                item.segment_id: item for item in current.repairs
            }
            accepted_current_ids = {
                segment_id
                for segment_id, verification in failed.items()
                if verification.current_acceptable
                and _original_is_safe_to_reinstate(repairs_by_id.get(segment_id))
            }
            if accepted_current_ids:
                current = _retain_accepted_originals(current, accepted_current_ids)
                by_document[document_id] = current
                for segment_id in sorted(accepted_current_ids):
                    prescreen_results.append(
                        (segment_id, "accepted-current-prescreen")
                    )
                failed = {
                    segment_id: verification
                    for segment_id, verification in failed.items()
                    if segment_id not in accepted_current_ids
                }

            deterministic = audit_translated_document(
                sources[document_id], current.document, config.audit
            )
            blocking_ids = {
                issue.segment_id
                for issue in deterministic.issues
                if issue.severity.rank >= AuditSeverity.MEDIUM.rank
            }
            resolved_deterministic_ids = {
                repair.segment_id
                for repair in current.repairs
                if repair.disposition is RepairDisposition.REVIEW
                and repair.segment_id not in blocking_ids
                and not any(
                    issue.source in {"semantic", "reprose"}
                    for issue in repair.issues
                )
            }
            if resolved_deterministic_ids:
                current = _retain_accepted_originals(
                    current, resolved_deterministic_ids
                )
                by_document[document_id] = current
                for segment_id in sorted(resolved_deterministic_ids):
                    prescreen_results.append(
                        (segment_id, "deterministic-prescreen")
                    )

            for repair in current.repairs:
                verification = failed.get(repair.segment_id)
                if repair.disposition is RepairDisposition.REVIEW or verification is not None:
                    issues = list(repair.issues)
                    feedback = repair.message or "The first repair exhausted validation attempts."
                    if verification is not None:
                        feedback = verification.message
                        issues.append(
                            AuditIssue(
                                segment_id=repair.segment_id,
                                category=AuditCategory.MISTRANSLATION,
                                severity=AuditSeverity.HIGH,
                                message=f"Repair verification failed: {verification.message}",
                                source="semantic",
                            )
                        )
                    prompt = _build_feedback_repair_prompt(
                        sources[document_id], current, repair, issues, feedback, config
                    )
                    bucket = (
                        config.audit.max_num_ctx
                        if not config.ollama.adaptive_num_ctx
                        else request_context_bucket(
                            prompt,
                            minimum=config.audit.repair_min_num_ctx,
                            maximum=config.audit.max_num_ctx,
                        )
                    )
                    targets.append(
                        {
                            "original": current,
                            "repair": repair,
                            "feedback": feedback,
                            "issues": issues,
                            "prompt": prompt,
                            "bucket": bucket,
                        }
                    )

        repaired_count = 0
        review_ids: list[str] = []
        targeted_count = len(targets)
        segment_result_index = 0
        segment_result_total = len(prescreen_results) + targeted_count
        for segment_id, mode in prescreen_results:
            segment_result_index += 1
            report_segment_result(
                client,
                model=config.ollama.model,
                stage=stage.value,
                segment_id=segment_id,
                result="passed",
                mode=mode,
                result_index=segment_result_index,
                result_total=segment_result_total,
                role="repair_review.prescreen",
            )
        pending_targets = []
        for task in targets:
            original = task["original"]
            repair = task["repair"]
            source = sources[original.document.manifest_id]
            try:
                corrected = _run_feedback_repair(
                    connection, workspace, stage_root, source, original, repair,
                    task["issues"], task["feedback"], config, _PreScreenClient(),
                    mode="review-batch", feedback_index=0,
                    total_feedback=len(targets), document_index=source.order + 1,
                    total_documents=len(documents), stage_input_hash=input_hash,
                    workflow_stage=stage, prepared_prompt=task["prompt"],
                )
            except _SemanticRepairRequired:
                pending_targets.append(task)
                continue
            current = by_document[original.document.manifest_id]
            by_document[original.document.manifest_id] = _replace_document_repair(
                current, corrected
            )
            if corrected.disposition is RepairDisposition.REPAIRED:
                repaired_count += 1
            elif corrected.disposition is RepairDisposition.REVIEW:
                review_ids.append(corrected.segment_id)
            segment_result_index += 1
            report_segment_result(
                client,
                model=config.ollama.model,
                stage=stage.value,
                segment_id=corrected.segment_id,
                result=(
                    "repaired"
                    if corrected.disposition is RepairDisposition.REPAIRED
                    else "passed"
                    if corrected.disposition is RepairDisposition.ACCEPTED
                    else "pending"
                ),
                mode="deterministic-prescreen",
                issue_count=(
                    0 if corrected.disposition is not RepairDisposition.REVIEW
                    else len(corrected.issues)
                ),
                result_index=segment_result_index,
                result_total=segment_result_total,
                role="repair_review.prescreen",
            )
        targets = pending_targets
        report_stage_plan(
            client,
            model=config.ollama.model,
            stage=stage.value,
            prescreened=sum(len(item.repairs) for item in documents),
            llm_tasks=len(targets),
            skipped=sum(len(item.repairs) for item in documents) - len(targets),
            context_buckets=(task["bucket"] for task in targets),
            role="repair_review.feedback",
        )
        ordered_targets = group_by_context_bucket(
            targets, lambda task: int(task["bucket"])
        )
        for index, task in enumerate(ordered_targets, start=1):
            original = task["original"]
            repair = task["repair"]
            feedback = task["feedback"]
            issues = task["issues"]
            current = by_document[original.document.manifest_id]
            source = sources[original.document.manifest_id]
            corrected = _run_feedback_repair(
                connection, workspace, stage_root, source, original, repair,
                issues, feedback, config, client,
                mode="review-batch", feedback_index=index,
                total_feedback=len(ordered_targets), document_index=source.order + 1,
                total_documents=len(documents), stage_input_hash=input_hash,
                workflow_stage=stage,
                prepared_prompt=task["prompt"],
                context_bucket=int(task["bucket"]),
            )
            by_document[original.document.manifest_id] = _replace_document_repair(
                current, corrected
            )
            if corrected.disposition is RepairDisposition.REPAIRED:
                repaired_count += 1
            elif corrected.disposition is RepairDisposition.REVIEW:
                review_ids.append(corrected.segment_id)
            segment_result_index += 1
            report_segment_result(
                client,
                model=config.ollama.model,
                stage=stage.value,
                segment_id=corrected.segment_id,
                result=(
                    "repaired"
                    if corrected.disposition is RepairDisposition.REPAIRED
                    else "passed"
                    if corrected.disposition is RepairDisposition.ACCEPTED
                    else "pending"
                ),
                mode=(
                    "accepted-prescreen"
                    if corrected.disposition is RepairDisposition.ACCEPTED
                    else "feedback-repair"
                ),
                issue_count=(
                    0 if corrected.disposition is not RepairDisposition.REVIEW
                    else len(corrected.issues)
                ),
                context_bucket=int(task["bucket"]),
                message=(
                    "feedback-repair-unresolved"
                    if corrected.disposition is RepairDisposition.REVIEW
                    else ""
                ),
                result_index=segment_result_index,
                result_total=segment_result_total,
                role="repair_review.feedback",
            )

        for current in sorted(by_document.values(), key=lambda item: item.document.order):
            source = sources[current.document.manifest_id]
            json_path = stage_root / f"{source.order:04d}-{source.manifest_id}.review-repaired.json"
            text_path = stage_root / f"{source.order:04d}-{source.manifest_id}.review-repaired.txt"
            atomic_write_text(json_path, current.model_dump_json(indent=2))
            atomic_write_text(text_path, render_translated_document(current.document))
            _record_file(connection, workspace, json_path, "review_repaired_document_json", stage)
            _record_file(connection, workspace, text_path, "review_repaired_document_text", stage)

        report = TranslationRepairReport(
            document_count=len(documents), targeted_segment_count=targeted_count,
            repaired_segment_count=repaired_count, review_segment_ids=sorted(review_ids),
        )
        report_path = stage_root / "review-repair.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "review_repair_report", stage)
        set_job_metadata(
            connection, "review_repair_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "review_repaired_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, stage)
        set_stage_status(
            connection, stage.value, StageStatus.COMPLETED,
            attempts=attempts, input_hash=input_hash, output_hash=output_hash,
            message=(f"{len(review_ids)} segment(s) require human review" if review_ids else ""),
        )
        return report
    except Exception as error:
        _mark_failed(connection, stage, error)
        raise
    finally:
        connection.close()


def _original_is_safe_to_reinstate(repair) -> bool:
    """Never waive a high-severity finding by restoring the already-audited draft."""
    if repair is None:
        return False
    return not any(
        issue.severity is AuditSeverity.HIGH for issue in repair.issues
    )


def load_review_repaired_documents(workspace: JobWorkspace) -> list[RepairedDocument]:
    connection = connect_state(workspace.state_file)
    try:
        items = []
        for artifact in list_active_stage_artifacts(
            connection,
            WorkflowStage.REPAIR_REVIEW.value,
            root_metadata_key="review_repaired_root",
            report_metadata_key="review_repair_report",
        ):
            if artifact["kind"] != "review_repaired_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            items.append(RepairedDocument.model_validate_json(path.read_text(encoding="utf-8")))
        if not items:
            raise FileNotFoundError("review-repaired documents are not recorded")
        items = require_unique_items(
            items,
            lambda item: item.document.manifest_id,
            label="review-repaired document",
        )
        return sorted(items, key=lambda item: item.document.order)
    finally:
        connection.close()


def load_review_repair_report(workspace: JobWorkspace, *, connection=None):
    owns = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        from ..state import get_job_metadata
        relative = get_job_metadata(active, "review_repair_report")
        if not relative:
            raise FileNotFoundError("review repair report is not recorded")
        return TranslationRepairReport.model_validate_json(
            workspace.directory(relative).read_text(encoding="utf-8")
        )
    finally:
        if owns:
            active.close()


def _record_file(connection, workspace, path, kind, stage):
    record_artifact(
        connection, path.relative_to(workspace.root).as_posix(), stage.value,
        kind, sha256_file(path), path.stat().st_size,
    )


def _require_completed(connection, stage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, stage, error):
    previous = get_stage_status(connection, stage.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection, stage.value, StageStatus.FAILED,
        attempts=int(previous["attempts"]), message=str(error),
        input_hash=str(previous["input_hash"]),
    )
