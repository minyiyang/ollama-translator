"""Resumable selective prose-rewrite stage with deterministic rollback."""

from __future__ import annotations

from dataclasses import asdict

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..hashing import hash_named_values, sha256_file
from ..ollama_client import OllamaClient, StructuredOutputError
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..prose_rewrite import (
    ProseRewriteBatch,
    ProseRewriteReport,
    apply_validated_prose_decision,
    build_prose_rewrite_prompt,
    select_prose_rewrite_candidates,
    validate_prose_rewrite_batch,
)
from ..stage_progress import (
    group_by_context_bucket,
    llm_role_kwargs,
    report_segment_result,
    report_stage_plan,
    request_context_bucket,
)
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    get_work_unit,
    initialize_state,
    list_artifacts,
    record_artifact,
    retire_stage_artifacts,
    record_attempt,
    record_validation,
    set_job_metadata,
    set_stage_status,
    set_work_unit_status,
)
from ..stage_artifacts import (
    list_active_stage_artifacts,
    require_document_totals,
    require_unique_items,
)
from ..translation import render_translated_document
from ..workspace import JobWorkspace
from .preprocess import load_preprocessed_documents
from .repair import load_repaired_documents


REPROSE_STAGE_VERSION = "2"


def run_prose_rewrite_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> ProseRewriteReport:
    """Rewrite selected substantial prose and retain unsafe originals byte-for-byte."""
    stage = WorkflowStage.REPROSE_TRANSLATION
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        repair_stage = _require_completed(connection, WorkflowStage.REPAIR_TRANSLATION)
        input_hash = build_stage_input_hash(
            {
                "repair": str(repair_stage["output_hash"]),
                "config": config.reprose.model_dump_json(),
                "translation": config.translation.model_dump_json(),
                "quantity": config.audit.quantity.model_dump_json(),
                "stage_version": REPROSE_STAGE_VERSION,
            }
        )
        if stage_is_current(connection, stage, input_hash, artifact_root=workspace.root):
            return load_prose_rewrite_report(workspace, connection=connection)
        previous = get_stage_status(connection, stage.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, stage)
            previous = get_stage_status(connection, stage.value)
        else:
            invalidate_stage_and_dependents(connection, stage, include_stage=False)
        retire_stage_artifacts(connection, stage.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            stage.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        sources = {
            item.manifest_id: item for item in load_preprocessed_documents(workspace)
        }
        repaired_documents = load_repaired_documents(workspace)
        stage_root = workspace.directory(f"reprosed/{input_hash[:16]}")
        stage_root.mkdir(parents=True, exist_ok=True)
        plans: list[dict[str, object]] = []
        candidate_total = 0
        for repaired in repaired_documents:
            source = sources[repaired.document.manifest_id]
            ids = (
                select_prose_rewrite_candidates(source, repaired, config)
                if config.reprose.enabled
                else []
            )
            candidate_total += len(ids)
            batches = [
                ids[offset : offset + config.reprose.batch_size]
                for offset in range(0, len(ids), config.reprose.batch_size)
            ]
            for batch_number, batch_ids in enumerate(batches, start=1):
                prompt = build_prose_rewrite_prompt(
                    source, repaired.document, batch_ids, config
                )
                unit_id = (
                    f"reprose-{source.order:04d}-{source.manifest_id}-"
                    f"{batch_number:05d}"
                )
                unit_hash = hash_named_values(
                    {
                        "stage": input_hash,
                        "ids": "\n".join(batch_ids),
                        "prompt": prompt,
                    }
                )
                path = stage_root / f"{unit_id}.json"
                existing = get_work_unit(connection, unit_id, stage.value)
                result = _load_current(existing, path, unit_hash)
                bucket = (
                    config.reprose.max_num_ctx
                    if not config.ollama.adaptive_num_ctx
                    else request_context_bucket(
                        prompt,
                        minimum=config.reprose.min_num_ctx,
                        maximum=config.reprose.max_num_ctx,
                        schema=ProseRewriteBatch,
                    )
                )
                plans.append(
                    {
                        "source": source,
                        "repaired": repaired,
                        "document_id": source.manifest_id,
                        "ids": batch_ids,
                        "prompt": prompt,
                        "unit_id": unit_id,
                        "unit_hash": unit_hash,
                        "path": path,
                        "existing": existing,
                        "result": result,
                        "bucket": bucket,
                    }
                )
                if result is not None:
                    _record_file(
                        connection, workspace, path, "prose_rewrite_decisions", stage
                    )

        pending = [item for item in plans if item["result"] is None]
        report_stage_plan(
            client,
            model=config.reprose.model,
            stage=stage.value,
            prescreened=sum(len(item.document.segments) for item in repaired_documents),
            llm_tasks=len(pending),
            skipped=(
                sum(len(item.document.segments) for item in repaired_documents)
                - candidate_total
            ),
            context_buckets=(int(item["bucket"]) for item in pending),
            role="reprose.rewrite",
        )
        if pending and client is None:
            raise ValueError("an Ollama client is required when reprose is enabled")
        failures: list[str] = []
        for index, task in enumerate(
            group_by_context_bucket(pending, lambda item: int(item["bucket"])),
            start=1,
        ):
            result = _run_batch(
                connection,
                stage,
                task,
                config,
                client,
                index=index,
                total=len(pending),
            )
            task["result"] = result
            if result is None:
                failures.extend(task["ids"])
            else:
                _record_file(
                    connection,
                    workspace,
                    task["path"],
                    "prose_rewrite_decisions",
                    stage,
                )

        by_document = {
            item.document.manifest_id: item for item in repaired_documents
        }
        proposed = 0
        changed: list[str] = []
        rejected: list[str] = []
        segment_index = 0
        for task in plans:
            result = task["result"]
            if result is None:
                for segment_id in task["ids"]:
                    segment_index += 1
                    report_segment_result(
                        client,
                        model=config.reprose.model,
                        stage=stage.value,
                        segment_id=segment_id,
                        result="skipped",
                        mode="invalid-decision-fallback",
                        issue_count=1,
                        result_index=segment_index,
                        result_total=candidate_total,
                        role="reprose.rewrite",
                    )
                continue
            current = by_document[task["document_id"]]
            source = task["source"]
            for decision in result.decisions:
                segment_index += 1
                if decision.action == "rewrite":
                    proposed += 1
                current, applied, error = apply_validated_prose_decision(
                    source, current, decision, config
                )
                if decision.action == "rewrite":
                    record_validation(
                        connection,
                        task["unit_id"],
                        f"prose_candidate_{decision.segment_id}",
                        not bool(error),
                        details={
                            "segment_id": decision.segment_id,
                            "safe_original_retained": bool(error),
                            "message": error,
                        },
                    )
                if applied:
                    changed.append(decision.segment_id)
                elif error:
                    rejected.append(decision.segment_id)
                report_segment_result(
                    client,
                    model=config.reprose.model,
                    stage=stage.value,
                    segment_id=decision.segment_id,
                    result=(
                        "repaired" if applied else "rejected" if error else "passed"
                    ),
                    mode="prose-rewrite" if applied else "safe-original-retained",
                    issue_count=1 if error else 0,
                    context_bucket=int(task["bucket"]),
                    message=error,
                    result_index=segment_index,
                    result_total=candidate_total,
                    role="reprose.rewrite",
                )
            by_document[task["document_id"]] = current

        for current in sorted(by_document.values(), key=lambda item: item.document.order):
            source = sources[current.document.manifest_id]
            json_path = stage_root / f"{source.order:04d}-{source.manifest_id}.reprosed.json"
            text_path = stage_root / f"{source.order:04d}-{source.manifest_id}.reprosed.txt"
            atomic_write_text(json_path, current.model_dump_json(indent=2))
            atomic_write_text(text_path, render_translated_document(current.document))
            _record_file(connection, workspace, json_path, "reprosed_document_json", stage)
            _record_file(connection, workspace, text_path, "reprosed_document_text", stage)

        report = ProseRewriteReport(
            document_count=len(repaired_documents),
            candidate_segment_count=candidate_total,
            proposed_rewrite_count=proposed,
            applied_rewrite_count=len(changed),
            rejected_rewrite_count=len(rejected),
            changed_segment_ids=sorted(changed),
            rejected_segment_ids=sorted(rejected),
            decision_failure_segment_ids=sorted(failures),
        )
        report_path = stage_root / "prose-rewrite.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "prose_rewrite_report", stage)
        set_job_metadata(
            connection,
            "prose_rewrite_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "reprosed_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, stage)
        set_stage_status(
            connection,
            stage.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
            message=(
                f"{len(report.rejected_segment_ids) + len(report.decision_failure_segment_ids)} "
                "segment(s) retained after rejection"
                if report.rejected_segment_ids or report.decision_failure_segment_ids
                else ""
            ),
        )
        return report
    except Exception as error:
        _mark_failed(connection, stage, error)
        raise
    finally:
        connection.close()


def load_reprosed_documents(workspace: JobWorkspace) -> list:
    """Load post-repair prose drafts in spine order."""
    connection = connect_state(workspace.state_file)
    try:
        from ..repair import RepairedDocument

        items = []
        for artifact in list_active_stage_artifacts(
            connection,
            WorkflowStage.REPROSE_TRANSLATION.value,
            root_metadata_key="reprosed_root",
            report_metadata_key="prose_rewrite_report",
        ):
            if artifact["kind"] != "reprosed_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            items.append(
                RepairedDocument.model_validate_json(path.read_text(encoding="utf-8"))
            )
        if not items:
            raise FileNotFoundError("reprosed documents are not recorded")
        items = require_unique_items(
            items,
            lambda item: item.document.manifest_id,
            label="reprosed document",
        )
        report = load_prose_rewrite_report(workspace, connection=connection)
        items = require_document_totals(
            items,
            expected_documents=report.document_count,
            label="reprosed document",
        )
        return sorted(items, key=lambda item: item.document.order)
    finally:
        connection.close()


def load_post_repair_documents(
    workspace: JobWorkspace,
    config: AppConfig,
):
    """Load reprosed drafts, with a disabled-stage compatibility fallback."""
    connection = connect_state(workspace.state_file)
    try:
        stage = get_stage_status(connection, WorkflowStage.REPROSE_TRANSLATION.value)
        complete = bool(stage and stage["status"] == StageStatus.COMPLETED.value)
    finally:
        connection.close()
    if complete:
        return load_reprosed_documents(workspace)
    if config.reprose.enabled:
        raise RuntimeError(
            f"required stage is not complete: {WorkflowStage.REPROSE_TRANSLATION.value}"
        )
    return load_repaired_documents(workspace)


def load_prose_rewrite_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> ProseRewriteReport:
    """Load the published prose-rewrite report."""
    owns = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "prose_rewrite_report")
        if not relative:
            raise FileNotFoundError("prose rewrite report is not recorded")
        return ProseRewriteReport.model_validate_json(
            workspace.directory(relative).read_text(encoding="utf-8")
        )
    finally:
        if owns:
            active.close()


def _run_batch(connection, stage, task, config, client, *, index, total):
    last_error = ""
    existing = task["existing"]
    existing_attempts = int(existing["attempts"]) if existing else 0
    for offset in range(1, config.reprose.max_attempts + 1):
        attempt = existing_attempts + offset
        prompt = build_prose_rewrite_prompt(
            task["source"],
            task["repaired"].document,
            task["ids"],
            config,
            rejection_feedback=last_error,
        ) if last_error else task["prompt"]
        generated = None
        try:
            generated = client.generate_structured(
                prompt,
                ProseRewriteBatch,
                model=config.reprose.model,
                think=config.reprose.thinking,
                progress_label=(
                    f"reprose={index}/{total} id={task['unit_id']} "
                    f"attempt={offset}/{config.reprose.max_attempts}"
                ),
                **llm_role_kwargs(client, "reprose.rewrite"),
                context_minimum=int(task["bucket"]),
                context_maximum=int(task["bucket"]),
                max_output_tokens=8_192,
                max_attempts=1,
            )
            result = validate_prose_rewrite_batch(generated.value, task["ids"])
        except (StructuredOutputError, ValueError) as error:
            last_error = str(error)
            record_attempt(
                connection,
                task["unit_id"],
                stage.value,
                attempt,
                StageStatus.FAILED,
                message=last_error,
                metrics=(asdict(generated.generation.metrics) if generated else {}),
            )
            continue
        atomic_write_text(task["path"], result.model_dump_json(indent=2))
        set_work_unit_status(
            connection,
            task["unit_id"],
            stage.value,
            "prose_rewrite_batch",
            StageStatus.COMPLETED,
            parent_id=task["document_id"],
            attempts=attempt,
            input_hash=task["unit_hash"],
            output_hash=sha256_file(task["path"]),
            validation={"scope_valid": True},
        )
        record_attempt(
            connection,
            task["unit_id"],
            stage.value,
            attempt,
            StageStatus.COMPLETED,
            metrics=asdict(generated.generation.metrics),
        )
        record_validation(
            connection,
            task["unit_id"],
            "prose_rewrite_scope",
            True,
            details={"expected_ids": task["ids"]},
        )
        return result
    set_work_unit_status(
        connection,
        task["unit_id"],
        stage.value,
        "prose_rewrite_batch",
        StageStatus.COMPLETED,
        parent_id=task["document_id"],
        attempts=existing_attempts + config.reprose.max_attempts,
        input_hash=task["unit_hash"],
        output_hash="",
        validation={"scope_valid": False, "safe_fallback": True},
        message=last_error,
    )
    return None


def _load_current(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return ProseRewriteBatch.model_validate_json(path.read_text(encoding="utf-8"))


def _record_file(connection, workspace, path, kind, stage):
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        stage.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
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
        connection,
        stage.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
