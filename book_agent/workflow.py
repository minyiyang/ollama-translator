"""Operational orchestration for the resumable translation workflow."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from .config import AppConfig
from .hashing import sha256_file
from .ollama_client import GenerationCancelled, GenerationProgressEvent, OllamaClient
from .pipeline_state import (
    WorkflowStage,
    initialize_pipeline_stages,
    invalidate_failed_stage_units_and_dependents,
    invalidate_stage_and_dependents,
)
from .state import (
    StageStatus,
    connect_state,
    get_stage_status,
    get_job_metadata,
    list_artifacts,
    list_job_metadata,
    list_stage_statuses,
    list_work_units,
    set_job_metadata,
    set_stage_status,
)
from .workspace import JobWorkspace
from .stages.audit import run_translation_audit_stage
from .stages.compile import (
    run_document_compile_stage,
    write_unresolved_review_report,
)
from .stages.decompile import run_decompile_stage
from .stages.glossary import (
    GlossaryApprovalRequired,
    run_glossary_approval_stage,
    run_glossary_extraction_stage,
    run_glossary_resolution_stage,
)
from .stages.preprocess import run_preprocessing_stage
from .stages.repair import run_translation_repair_stage
from .stages.reprose import run_prose_rewrite_stage
from .stages.repair_review import run_review_repair_stage
from .stages.review_repaired import run_repaired_review_stage
from .stages.translate import run_translation_stage
from .stages.validate_epub import run_document_validation_stage
from .stages.validate_repaired import (
    load_repaired_validation_report,
    run_repaired_validation_stage,
)


class ExitCode(IntEnum):
    """Stable process outcomes used by the CLI."""

    COMPLETE = 0
    FAILED = 1
    PAUSED = 2
    CANCELLED = 130


class WorkflowResult(str):
    """Named workflow result values stored in summaries."""

    COMPLETE = "complete"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProgressEvent:
    """One user-visible workflow progress transition."""

    stage: str
    status: str
    message: str = ""


ProgressCallback = Callable[[ProgressEvent], None]
StageRunner = Callable[[JobWorkspace, AppConfig, OllamaClient | None], object]


@dataclass(frozen=True)
class WorkflowRunResult:
    """Terminal workflow outcome returned without terminating the process."""

    result: str
    exit_code: ExitCode
    stage: str = ""
    message: str = ""


def load_workspace_config(workspace: JobWorkspace) -> AppConfig:
    """Load the immutable resolved configuration captured in a workspace."""
    return AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))


def default_stage_runners() -> dict[WorkflowStage, StageRunner]:
    """Return adapters from workflow stages to their existing typed stage APIs."""
    return {
        WorkflowStage.DECOMPILE: lambda workspace, config, client: run_decompile_stage(workspace),
        WorkflowStage.EXTRACT_GLOSSARY: lambda workspace, config, client: run_glossary_extraction_stage(
            workspace, config, client
        ),
        WorkflowStage.RESOLVE_GLOSSARY: lambda workspace, config, client: run_glossary_resolution_stage(
            workspace, config, client
        ),
        WorkflowStage.APPROVE_GLOSSARY: lambda workspace, config, client: run_glossary_approval_stage(
            workspace,
            config,
            llm_review=config.workflow.llm_glossary_review,
            client=client,
        ),
        WorkflowStage.PREPROCESS: lambda workspace, config, client: run_preprocessing_stage(
            workspace, config
        ),
        WorkflowStage.TRANSLATE: lambda workspace, config, client: run_translation_stage(
            workspace, config, _require_client(client)
        ),
        WorkflowStage.AUDIT_TRANSLATION: lambda workspace, config, client: run_translation_audit_stage(
            workspace, config, client
        ),
        WorkflowStage.REPAIR_TRANSLATION: lambda workspace, config, client: run_translation_repair_stage(
            workspace, config, client
        ),
        WorkflowStage.REPROSE_TRANSLATION: lambda workspace, config, client: run_prose_rewrite_stage(
            workspace, config, client
        ),
        WorkflowStage.REVIEW_REPAIRED: lambda workspace, config, client: run_repaired_review_stage(
            workspace, config, _require_client(client)
        ),
        WorkflowStage.REPAIR_REVIEW: lambda workspace, config, client: run_review_repair_stage(
            workspace, config, _require_client(client)
        ),
        WorkflowStage.VALIDATE_REPAIRED: lambda workspace, config, client: run_repaired_validation_stage(
            workspace, config, client
        ),
        WorkflowStage.COMPILE: lambda workspace, config, client: run_document_compile_stage(
            workspace, config
        ),
        WorkflowStage.VALIDATE_EPUB: lambda workspace, config, client: run_document_validation_stage(
            workspace, config
        ),
    }


def run_workflow(
    workspace: JobWorkspace,
    config: AppConfig | None = None,
    *,
    client: OllamaClient | None = None,
    progress: ProgressCallback | None = None,
    generation_progress: Callable[[GenerationProgressEvent], None] | None = None,
    stage_runners: Mapping[WorkflowStage, StageRunner] | None = None,
) -> WorkflowRunResult:
    """Run pending stages in order, resuming completed work and honoring review gates."""
    resolved_config = config or load_workspace_config(workspace)
    migration_connection = connect_state(workspace.state_file)
    try:
        initialize_pipeline_stages(migration_connection)
    finally:
        migration_connection.close()
    runners = dict(default_stage_runners() if stage_runners is None else stage_runners)
    missing = [stage.value for stage in WorkflowStage if stage not in runners]
    if missing:
        raise ValueError(f"missing stage runners: {', '.join(missing)}")
    callback = progress or (lambda event: None)
    shared_client = client
    models_validated = client is not None

    try:
        for stage in WorkflowStage:
            connection = connect_state(workspace.state_file)
            try:
                record = get_stage_status(connection, stage.value)
            finally:
                connection.close()
            if record is None:
                return WorkflowRunResult(
                    WorkflowResult.FAILED, ExitCode.FAILED, stage.value, "stage state is missing"
                )
            if record["status"] == StageStatus.COMPLETED.value:
                callback(
                    ProgressEvent(
                        stage.value,
                        "skipped",
                        "result=skipped; already complete",
                    )
                )
                continue
            if stage is WorkflowStage.COMPILE:
                unresolved_message = _unresolved_compile_review_message(
                    workspace, resolved_config
                )
                if unresolved_message:
                    review_path = write_unresolved_review_report(
                        workspace,
                        compile_limit=(
                            resolved_config.workflow.compile_max_unresolved_review_segments
                        ),
                    )
                    unresolved_message += f" Review worksheet: {review_path}"
                    _pause_stage(workspace, stage, unresolved_message)
                    callback(
                        ProgressEvent(
                            stage.value,
                            StageStatus.PAUSED.value,
                            f"result=pending; {unresolved_message}",
                        )
                    )
                    return WorkflowRunResult(
                        WorkflowResult.PAUSED,
                        ExitCode.PAUSED,
                        stage.value,
                        unresolved_message,
                    )
            if stage is WorkflowStage.COMPILE and _final_review_is_required(
                workspace, resolved_config
            ):
                _pause_stage(workspace, stage, "final draft approval required")
                callback(
                    ProgressEvent(
                        stage.value,
                        StageStatus.PAUSED.value,
                        "result=pending; final draft approval required",
                    )
                )
                return WorkflowRunResult(
                    WorkflowResult.PAUSED,
                    ExitCode.PAUSED,
                    stage.value,
                    "final draft approval required",
                )
            if _stage_uses_ollama(stage, resolved_config) and shared_client is None:
                shared_client = OllamaClient(
                    resolved_config.ollama,
                    progress=generation_progress,
                )
            if _stage_uses_ollama(stage, resolved_config) and not models_validated:
                _validate_models(shared_client, resolved_config)
                models_validated = True
            callback(
                ProgressEvent(
                    stage.value,
                    StageStatus.RUNNING.value,
                    "result=pending",
                )
            )
            try:
                stage_result = runners[stage](workspace, resolved_config, shared_client)
            except GlossaryApprovalRequired as error:
                callback(
                    ProgressEvent(
                        stage.value,
                        StageStatus.PAUSED.value,
                        f"result=pending; {error}",
                    )
                )
                return WorkflowRunResult(
                    WorkflowResult.PAUSED, ExitCode.PAUSED, stage.value, str(error)
                )
            callback(
                ProgressEvent(
                    stage.value,
                    StageStatus.COMPLETED.value,
                    _stage_result_message(stage, stage_result),
                )
            )
    except (GenerationCancelled, KeyboardInterrupt) as error:
        message = str(error) or "workflow cancelled"
        if 'stage' in locals():
            _pause_stage(workspace, stage, message)
        callback(
            ProgressEvent(
                stage.value if 'stage' in locals() else "",
                WorkflowResult.CANCELLED,
                f"result=cancelled; {message}",
            )
        )
        return WorkflowRunResult(
            WorkflowResult.CANCELLED,
            ExitCode.CANCELLED,
            stage.value if 'stage' in locals() else "",
            message,
        )
    except Exception as error:
        if 'stage' in locals():
            _fail_stage(workspace, stage, str(error))
        callback(
            ProgressEvent(
                stage.value if 'stage' in locals() else "",
                WorkflowResult.FAILED,
                f"result=failed; {error}",
            )
        )
        return WorkflowRunResult(
            WorkflowResult.FAILED,
            ExitCode.FAILED,
            stage.value if 'stage' in locals() else "",
            str(error),
        )
    return WorkflowRunResult(WorkflowResult.COMPLETE, ExitCode.COMPLETE)


def _stage_result_message(stage: WorkflowStage, result: object) -> str:
    """Describe execution and quality outcome without exposing document content."""
    review_ids = list(getattr(result, "review_segment_ids", []) or [])
    review_count = len(review_ids)

    if stage is WorkflowStage.TRANSLATE:
        deferred_chunks = int(getattr(result, "deferred_chunk_count", 0) or 0)
        deferred_segments = int(getattr(result, "deferred_segment_count", 0) or 0)
        state = "pending-repair" if deferred_segments else "passed"
        return (
            f"result={state}; deferred_chunks={deferred_chunks}; "
            f"deferred_segments={deferred_segments}"
        )

    if stage is WorkflowStage.AUDIT_TRANSLATION:
        issue_count = int(getattr(result, "issue_count", 0) or 0)
        state = "passed" if bool(getattr(result, "passed", False)) else "issues-found"
        return (
            f"result={state}; issues={issue_count}; "
            f"review_segments={review_count}"
        )

    if stage is WorkflowStage.REPROSE_TRANSLATION:
        candidates = int(getattr(result, "candidate_segment_count", 0) or 0)
        proposed = int(getattr(result, "proposed_rewrite_count", 0) or 0)
        applied = int(getattr(result, "applied_rewrite_count", 0) or 0)
        rejected = int(getattr(result, "rejected_rewrite_count", 0) or 0)
        decision_failures = len(
            list(getattr(result, "decision_failure_segment_ids", []) or [])
        )
        return (
            f"result=passed; candidates={candidates}; proposed={proposed}; "
            f"applied={applied}; rejected={rejected}; "
            f"decision_failures={decision_failures}"
        )

    if stage in {WorkflowStage.REPAIR_TRANSLATION, WorkflowStage.REPAIR_REVIEW}:
        state = "succeeded" if not review_ids else "pending-review"
        targeted = int(getattr(result, "targeted_segment_count", 0) or 0)
        repaired = int(getattr(result, "repaired_segment_count", 0) or 0)
        return (
            f"result={state}; targeted={targeted}; repaired={repaired}; "
            f"review_segments={review_count}"
        )

    if stage is WorkflowStage.REVIEW_REPAIRED:
        state = "passed" if bool(getattr(result, "passed", False)) else "repair-required"
        remaining = int(getattr(result, "remaining_issue_count", review_count) or 0)
        return (
            f"result={state}; remaining={remaining}; "
            f"review_segments={review_count}"
        )

    if stage is WorkflowStage.VALIDATE_REPAIRED:
        state = "passed" if bool(getattr(result, "passed", False)) else "pending-review"
        remaining = int(getattr(result, "remaining_issue_count", review_count) or 0)
        return (
            f"result={state}; remaining={remaining}; "
            f"review_segments={review_count}"
        )

    if stage is WorkflowStage.VALIDATE_EPUB:
        passed = getattr(result, "passed", None)
        state = "passed" if passed is True else "unknown"
        errors = len(list(getattr(result, "errors", []) or []))
        return f"result={state}; errors={errors}"

    passed = getattr(result, "passed", None)
    if passed is True:
        return "result=passed"
    if passed is False:
        return "result=failed"
    return "result=succeeded"


def approve_glossary(
    workspace: JobWorkspace,
    reviewed_file: str | Path | None = None,
    config: AppConfig | None = None,
    *,
    llm_review: bool = False,
    generation_progress: Callable[[GenerationProgressEvent], None] | None = None,
) -> None:
    """Approve a human- or LLM-reviewed glossary and leave stages ready to resume."""
    resolved = config or load_workspace_config(workspace)
    client = None
    if llm_review:
        client = OllamaClient(resolved.ollama, progress=generation_progress)
        client.validate_model_context(
            resolved.ollama.num_ctx,
            model=resolved.ollama.model,
        )
    run_glossary_approval_stage(
        workspace,
        resolved,
        reviewed_file=reviewed_file,
        llm_review=llm_review,
        client=client,
    )


def approve_final_draft(workspace: JobWorkspace) -> str:
    """Approve the current repaired-validation artifact for compilation."""
    config = load_workspace_config(workspace)
    unresolved_message = _unresolved_compile_review_message(workspace, config)
    if unresolved_message:
        raise ValueError(unresolved_message)
    connection = connect_state(workspace.state_file)
    try:
        record = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if record is None or record["status"] != StageStatus.COMPLETED.value:
            raise ValueError("the repaired draft has not completed validation")
        output_hash = str(record["output_hash"])
        if not output_hash:
            raise ValueError("the repaired validation output hash is missing")
        set_job_metadata(connection, "final_review_approved_for", output_hash)
        compile_record = get_stage_status(connection, WorkflowStage.COMPILE.value)
        if compile_record and compile_record["status"] == StageStatus.PAUSED.value:
            set_stage_status(connection, WorkflowStage.COMPILE.value, StageStatus.PENDING)
        return output_hash
    finally:
        connection.close()


def retry_from_stage(workspace: JobWorkspace, stage: WorkflowStage) -> list[WorkflowStage]:
    """Reset one stage and its dependents so the next resume reruns them."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_pipeline_stages(connection)
        if get_stage_status(connection, stage.value) is None:
            raise ValueError(f"unknown workflow stage: {stage.value}")
        return invalidate_stage_and_dependents(connection, stage)
    finally:
        connection.close()


def retry_failed_from_stage(
    workspace: JobWorkspace, stage: WorkflowStage
) -> list[WorkflowStage]:
    """Reset failed work units only, retaining completed checkpoints in the stage."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_pipeline_stages(connection)
        if get_stage_status(connection, stage.value) is None:
            raise ValueError(f"unknown workflow stage: {stage.value}")
        failed = [
            unit
            for unit in list_work_units(connection, stage=stage.value)
            if unit["status"] == StageStatus.FAILED.value
        ]
        if not failed:
            return []
        return invalidate_failed_stage_units_and_dependents(connection, stage)
    finally:
        connection.close()


def workflow_status(workspace: JobWorkspace) -> dict[str, Any]:
    """Return a stable machine-readable status snapshot for one job."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_pipeline_stages(connection)
        stages = list_stage_statuses(connection)
        order = {stage.value: index for index, stage in enumerate(WorkflowStage)}
        stages.sort(key=lambda item: order.get(str(item["name"]), len(order)))
        units = list_work_units(connection)
        counts: dict[str, int] = {}
        for unit in units:
            status = str(unit["status"])
            counts[status] = counts.get(status, 0) + 1
        overall = _overall_status(stages)
        config_source = get_job_metadata(connection, "config_source")
        original_source_hash = get_job_metadata(connection, "config_source_sha256")
        current_source_hash = ""
        source_exists = False
        if config_source:
            source_path = Path(config_source)
            source_exists = source_path.is_file()
            if source_exists:
                current_source_hash = sha256_file(source_path)
        config_status = {
            "captured_sha256": get_job_metadata(connection, "config_sha256") or "",
            "source_path": config_source or "",
            "source_sha256": original_source_hash or "",
            "current_source_sha256": current_source_hash,
            "source_exists": source_exists,
            "drifted": bool(
                config_source
                and (
                    not source_exists
                    or not original_source_hash
                    or current_source_hash != original_source_hash
                )
            ),
            "production_profile": (
                get_job_metadata(connection, "production_profile") or ""
            ),
            "production_profile_version": int(
                get_job_metadata(connection, "production_profile_version") or 0
            ),
            "semantic_verification_policy": (
                get_job_metadata(connection, "semantic_verification_policy") or ""
            ),
        }
        return {
            "workspace": str(workspace.root),
            "job_id": get_job_metadata(connection, "job_id") or workspace.root.name,
            "overall": overall,
            "stages": stages,
            "work_units": {"total": len(units), "by_status": counts},
            "configuration": config_status,
        }
    finally:
        connection.close()


def workflow_report(workspace: JobWorkspace) -> dict[str, Any]:
    """Return status, metadata, artifact inventory, and report paths."""
    connection = connect_state(workspace.state_file)
    try:
        status = workflow_status(workspace)
        artifacts = list_artifacts(connection)
        return {
            **status,
            "metadata": list_job_metadata(connection),
            "artifacts": artifacts,
            "reports": [
                artifact for artifact in artifacts
                if artifact["kind"].endswith("report") or "/reports/" in f"/{artifact['path']}"
            ],
        }
    finally:
        connection.close()


def format_status_plain(snapshot: Mapping[str, Any]) -> str:
    """Render a compact terminal status table without optional dependencies."""
    lines = [
        f"Job: {snapshot['job_id']}",
        f"Overall: {snapshot['overall']}",
        f"Configuration drift: "
        f"{'yes' if snapshot.get('configuration', {}).get('drifted') else 'no'}",
        "",
        "Stage                      Status      Attempts  Message",
        "-------------------------  ----------  --------  -------",
    ]
    for stage in snapshot["stages"]:
        lines.append(
            f"{str(stage['name']):25}  {str(stage['status']):10}  "
            f"{int(stage['attempts']):8}  {str(stage['message'])}"
        )
    return "\n".join(lines).rstrip()


def format_json(value: object) -> str:
    """Serialize operational output as deterministic readable JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _stage_uses_ollama(stage: WorkflowStage, config: AppConfig) -> bool:
    if stage in {WorkflowStage.EXTRACT_GLOSSARY, WorkflowStage.RESOLVE_GLOSSARY}:
        return config.glossary.extraction_enabled
    if stage in {
        WorkflowStage.TRANSLATE,
        WorkflowStage.REPAIR_TRANSLATION,
        WorkflowStage.REVIEW_REPAIRED,
        WorkflowStage.REPAIR_REVIEW,
        WorkflowStage.VALIDATE_REPAIRED,
    }:
        return True
    if stage is WorkflowStage.REPROSE_TRANSLATION:
        return config.reprose.enabled
    if stage is WorkflowStage.APPROVE_GLOSSARY:
        return config.workflow.llm_glossary_review
    return stage is WorkflowStage.AUDIT_TRANSLATION and (
        config.audit.semantic_enabled or config.audit.quantity.enabled
    )


def _require_client(client: OllamaClient | None) -> OllamaClient:
    if client is None:
        raise ValueError("an Ollama client is required for this stage")
    return client


def _validate_models(client: OllamaClient | None, config: AppConfig) -> None:
    checked = _require_client(client)
    model_contexts = {
        config.ollama.model: config.ollama.num_ctx,
    }
    if config.glossary.extraction_enabled:
        model_contexts[
            config.glossary.extraction_model
        ] = config.glossary.extraction_max_num_ctx
    for model in config.translation.fallback_models:
        model_contexts[model] = max(
            model_contexts.get(model, 0), config.translation.max_num_ctx
        )
    if config.audit.semantic_enabled:
        model_contexts[config.audit.model] = max(
            model_contexts.get(config.audit.model, 0), config.audit.max_num_ctx
        )
        verifier_model = config.audit.verifier_model or config.audit.model
        model_contexts[verifier_model] = max(
            model_contexts.get(verifier_model, 0), config.audit.max_num_ctx
        )
    if config.audit.quantity.enabled:
        model_contexts[config.audit.quantity.model] = max(
            model_contexts.get(config.audit.quantity.model, 0),
            config.audit.quantity.max_num_ctx,
        )
        if config.audit.quantity.escalation_model:
            escalation = config.audit.quantity.escalation_model
            model_contexts[escalation] = max(
                model_contexts.get(escalation, 0),
                config.audit.quantity.max_num_ctx,
            )
    if config.reprose.enabled:
        model_contexts[config.reprose.model] = max(
            model_contexts.get(config.reprose.model, 0),
            config.reprose.max_num_ctx,
        )
        model_contexts[config.reprose.verifier_model] = max(
            model_contexts.get(config.reprose.verifier_model, 0),
            config.reprose.verifier_max_num_ctx,
        )
    for model, context in model_contexts.items():
        checked.validate_model_context(context, model=model)


def _final_review_is_required(workspace: JobWorkspace, config: AppConfig) -> bool:
    if not config.workflow.require_final_review:
        return False
    connection = connect_state(workspace.state_file)
    try:
        validated = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if validated is None or validated["status"] != StageStatus.COMPLETED.value:
            return False
        return get_job_metadata(connection, "final_review_approved_for") != validated["output_hash"]
    finally:
        connection.close()


def _unresolved_compile_review_message(
    workspace: JobWorkspace, config: AppConfig
) -> str:
    """Return an actionable compile blocker before asking for final approval."""
    connection = connect_state(workspace.state_file)
    try:
        validated = get_stage_status(
            connection, WorkflowStage.VALIDATE_REPAIRED.value
        )
        report_path = get_job_metadata(connection, "repaired_validation_report")
    finally:
        connection.close()
    if (
        validated is None
        or validated["status"] != StageStatus.COMPLETED.value
        or not report_path
    ):
        return ""
    report = load_repaired_validation_report(workspace)
    count = len(report.review_segment_ids)
    limit = config.workflow.compile_max_unresolved_review_segments
    if count <= limit:
        return ""
    defect_count = report.remaining_defect_count
    approval_count = report.approval_required_count
    if count and defect_count == 0 and approval_count == 0:
        defect_count = count
    ids = ", ".join(report.review_segment_ids)
    return (
        f"{defect_count} unresolved defect(s) and {approval_count} approval-only "
        f"segment(s) ({count} total) exceed the compile limit of {limit}; "
        f"resolve them before final approval. IDs: {ids}"
    )


def _pause_stage(workspace: JobWorkspace, stage: WorkflowStage, message: str) -> None:
    connection = connect_state(workspace.state_file)
    try:
        current = get_stage_status(connection, stage.value)
        set_stage_status(
            connection,
            stage.value,
            StageStatus.PAUSED,
            attempts=int(current["attempts"]) if current else 0,
            message=message,
            input_hash=str(current["input_hash"]) if current else "",
            output_hash=str(current["output_hash"]) if current else "",
        )
    finally:
        connection.close()


def _fail_stage(workspace: JobWorkspace, stage: WorkflowStage, message: str) -> None:
    connection = connect_state(workspace.state_file)
    try:
        current = get_stage_status(connection, stage.value)
        if current and current["status"] == StageStatus.FAILED.value:
            return
        set_stage_status(
            connection,
            stage.value,
            StageStatus.FAILED,
            attempts=int(current["attempts"]) if current else 0,
            message=message,
            input_hash=str(current["input_hash"]) if current else "",
            output_hash=str(current["output_hash"]) if current else "",
        )
    finally:
        connection.close()


def _overall_status(stages: list[dict[str, object]]) -> str:
    statuses = {str(stage["status"]) for stage in stages}
    if statuses == {StageStatus.COMPLETED.value}:
        return WorkflowResult.COMPLETE
    if StageStatus.FAILED.value in statuses:
        return WorkflowResult.FAILED
    if StageStatus.PAUSED.value in statuses:
        return WorkflowResult.PAUSED
    if StageStatus.RUNNING.value in statuses:
        return StageStatus.RUNNING.value
    return StageStatus.PENDING.value
