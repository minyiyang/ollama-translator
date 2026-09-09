"""Independent review pass over repaired translation drafts."""

from __future__ import annotations

from dataclasses import asdict

from ..atomic_io import atomic_write_text
from ..audit import AuditRiskTag, AuditSeverity, audit_translated_document
from ..config import AppConfig
from ..hashing import hash_named_values, sha256_file
from ..ollama_client import OllamaClient, StructuredOutputError
from ..stage_progress import (
    group_by_model_then_context,
    llm_role_kwargs,
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
from ..repair import (
    PairwiseRepairVerificationResult,
    RepairDisposition,
    RepairVerification,
    RepairVerificationResult,
    RepairedValidationReport,
    build_repair_verification_batches,
    build_repair_verification_prompt,
    map_pairwise_repair_verification,
    repair_candidate_first,
    repair_verification_output_token_limit,
    validate_repair_verification_scope,
    validate_pairwise_repair_verification_scope,
)
from ..state import (
    StageStatus,
    connect_state,
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
from ..stage_artifacts import list_active_stage_artifacts
from ..workspace import JobWorkspace
from .preprocess import load_preprocessed_documents
from .reprose import load_post_repair_documents


REVIEW_REPAIRED_STAGE_VERSION = "15"


def run_repaired_review_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient,
) -> RepairedValidationReport:
    """Verify all semantic repairs with Gemma without changing draft text."""
    stage = WorkflowStage.REVIEW_REPAIRED
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        reprose_stage = get_stage_status(
            connection, WorkflowStage.REPROSE_TRANSLATION.value
        )
        if reprose_stage and reprose_stage["status"] == StageStatus.COMPLETED.value:
            repair_stage = reprose_stage
        elif config.reprose.enabled:
            repair_stage = _require_completed(
                connection, WorkflowStage.REPROSE_TRANSLATION
            )
        else:
            repair_stage = _require_completed(
                connection, WorkflowStage.REPAIR_TRANSLATION
            )
        input_hash = build_stage_input_hash(
            {
                "repair": str(repair_stage["output_hash"]),
                "audit": config.audit.model_dump_json(),
                "reprose": config.reprose.model_dump_json(),
                "model": config.audit.verifier_model or config.audit.model,
                "stage_version": REVIEW_REPAIRED_STAGE_VERSION,
            }
        )
        if stage_is_current(connection, stage, input_hash, artifact_root=workspace.root):
            return load_repaired_review_report(workspace, connection=connection)
        previous = get_stage_status(connection, stage.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, stage)
            previous = get_stage_status(connection, stage.value)
        else:
            # Also invalidates completed downstream stages in workspaces created before
            # this split stage existed.
            invalidate_stage_and_dependents(connection, stage, include_stage=False)
        retire_stage_artifacts(connection, stage.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection, stage.value, StageStatus.RUNNING,
            attempts=attempts, input_hash=input_hash,
        )

        sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
        repaired_documents = load_post_repair_documents(workspace, config)
        stage_root = workspace.directory(f"repaired/{input_hash[:16]}-review")
        stage_root.mkdir(parents=True, exist_ok=True)
        review_plans = []
        verification_tasks = []
        verification_results = {}
        segment_contexts: dict[str, int] = {}
        segment_models: dict[str, str] = {}
        for repaired in repaired_documents:
            source = sources[repaired.document.manifest_id]
            deterministic = audit_translated_document(
                source, repaired.document, config.audit
            )
            blocking_by_id: dict[str, list[str]] = {}
            for issue in deterministic.issues:
                if issue.severity.rank >= AuditSeverity.MEDIUM.rank:
                    blocking_by_id.setdefault(issue.segment_id, []).append(issue.message)
            ids = [
                repair.segment_id
                for repair in repaired.repairs
                if repair.disposition in {
                    RepairDisposition.REPAIRED,
                    RepairDisposition.REVIEW,
                }
                and any(
                    issue.source in {"semantic", "reprose"}
                    or (
                        config.audit.quantity.verify_repairs
                        and AuditRiskTag.QUANTITY in issue.risk_tags
                    )
                    for issue in repair.issues
                )
                and repair.segment_id not in blocking_by_id
            ]
            tasks = []
            if ids:
                repairs_by_id = {
                    repair.segment_id: repair for repair in repaired.repairs
                }
                prose_ids = [
                    segment_id
                    for segment_id in ids
                    if any(
                        issue.source == "reprose"
                        for issue in repairs_by_id[segment_id].issues
                    )
                ]
                prose_set = set(prose_ids)
                standard_ids = [
                    segment_id for segment_id in ids if segment_id not in prose_set
                ]
                groups = []
                if standard_ids:
                    groups.append(
                        (
                            "standard",
                            standard_ids,
                            config.audit.verifier_model or config.audit.model,
                            config.audit.thinking,
                            config.audit.verifier_max_num_ctx or config.audit.max_num_ctx,
                        )
                    )
                if prose_ids:
                    groups.append(
                        (
                            "reprose",
                            prose_ids,
                            config.reprose.verifier_model,
                            config.reprose.verifier_thinking,
                            config.reprose.verifier_max_num_ctx,
                        )
                    )
                for role, group_ids, model, thinking, verifier_context_maximum in groups:
                    batches = build_repair_verification_batches(
                        source,
                        repaired,
                        group_ids,
                        max_request_context=verifier_context_maximum,
                    )
                    for batch_number, batch_ids in enumerate(batches, start=1):
                        candidate_first = repair_candidate_first(batch_ids)
                        prompt = build_repair_verification_prompt(
                            source,
                            repaired,
                            batch_ids,
                            candidate_first=candidate_first,
                        )
                        unit_id = f"review-{source.order:04d}-{source.manifest_id}"
                        if role == "reprose":
                            unit_id += "-reprose"
                        if len(batches) > 1:
                            unit_id += f"-{batch_number:05d}"
                        unit_hash = hash_named_values(
                            {
                                "stage": input_hash,
                                "prompt": prompt,
                                "ids": "\n".join(batch_ids),
                                "model": model,
                                "thinking": str(thinking),
                            }
                        )
                        path = stage_root / f"{unit_id}.json"
                        existing = get_work_unit(connection, unit_id, stage.value)
                        result = _load_current(existing, path, unit_hash)
                        bucket = (
                            verifier_context_maximum
                            if not config.ollama.adaptive_num_ctx
                            else request_context_bucket(
                                prompt,
                                minimum=config.audit.repair_min_num_ctx,
                                maximum=verifier_context_maximum,
                                schema=PairwiseRepairVerificationResult,
                            )
                        )
                        for segment_id in batch_ids:
                            segment_contexts[segment_id] = bucket
                            segment_models[segment_id] = model
                        task = {
                            "source": source,
                            "ids": batch_ids,
                            "prompt": prompt,
                            "unit_id": unit_id,
                            "unit_hash": unit_hash,
                            "path": path,
                            "existing": existing,
                            "bucket": bucket,
                            "candidate_first": candidate_first,
                            "model": model,
                            "thinking": thinking,
                            "require_candidate_win": role == "reprose",
                            "usage_role": f"review_repaired.{role}",
                        }
                        tasks.append(task)
                        if result is None:
                            verification_tasks.append(task)
                        else:
                            verification_results[unit_id] = result
                            _record_file(
                                connection, workspace, path,
                                "repaired_review_verification", stage,
                            )
            review_plans.append(
                (repaired, source, deterministic, blocking_by_id, ids, tasks)
            )

        report_stage_plan(
            client,
            model=(
                config.reprose.verifier_model
                if verification_tasks
                and all(task["model"] == config.reprose.verifier_model for task in verification_tasks)
                else config.audit.verifier_model or config.audit.model
            ),
            stage=stage.value,
            prescreened=sum(len(item[1].segments) for item in review_plans),
            llm_tasks=len(verification_tasks),
            skipped=(
                sum(len(item[1].segments) for item in review_plans)
                - sum(len(item[4]) for item in review_plans)
            ),
            context_buckets=(task["bucket"] for task in verification_tasks),
        )
        ordered_tasks = group_by_model_then_context(
            verification_tasks,
            lambda task: str(task["model"]),
            lambda task: int(task["bucket"]),
        )
        for index, task in enumerate(ordered_tasks, start=1):
            source = task["source"]
            result = _run_verification(
                connection, stage, task["unit_id"], source.manifest_id,
                task["unit_hash"], task["path"], task["prompt"], task["ids"],
                config, client,
                existing_attempts=(
                    int(task["existing"]["attempts"]) if task["existing"] else 0
                ),
                index=index, total=len(ordered_tasks),
                context_bucket=int(task["bucket"]),
                candidate_first=bool(task["candidate_first"]),
                model=str(task["model"]),
                thinking=bool(task["thinking"]),
                require_candidate_win=bool(task["require_candidate_win"]),
                usage_role=str(task["usage_role"]),
            )
            verification_results[task["unit_id"]] = result
            _record_file(
                connection, workspace, task["path"],
                "repaired_review_verification", stage,
            )

        review_ids: set[str] = set()
        segment_count = 0
        segment_result_index = 0
        segment_result_total = sum(
            len(repaired.repairs)
            for repaired, _source, _deterministic, _blocking, _ids, _tasks in review_plans
        )
        for repaired, source, deterministic, blocking_by_id, ids, tasks in review_plans:
            segment_count += len(source.segments)
            result = RepairVerificationResult(
                verifications=[
                    verification
                    for task in tasks
                    for verification in verification_results.get(
                        task["unit_id"], RepairVerificationResult(verifications=[])
                    ).verifications
                ]
            )
            deterministic_failures = [
                RepairVerification(
                    segment_id=segment_id,
                    passed=False,
                    current_acceptable=False,
                    message=(
                        "Deterministic validation failed before semantic review: "
                        + "; ".join(messages)
                    ),
                )
                for segment_id, messages in blocking_by_id.items()
            ]
            if deterministic_failures:
                result = RepairVerificationResult(
                    verifications=[*result.verifications, *deterministic_failures]
                )
            result_path = stage_root / f"{source.order:04d}-{source.manifest_id}.review.json"
            atomic_write_text(result_path, result.model_dump_json(indent=2))
            _record_file(connection, workspace, result_path, "repaired_review_result", stage)
            review_ids.update(item.segment_id for item in result.verifications if not item.passed)
            review_ids.update(
                repair.segment_id for repair in repaired.repairs
                if repair.disposition is RepairDisposition.REVIEW
                and (
                    repair.segment_id in blocking_by_id
                    or any(
                        issue.source in {"semantic", "reprose"}
                        for issue in repair.issues
                    )
                )
                and not any(
                    item.segment_id == repair.segment_id and item.passed
                    for item in result.verifications
                )
            )
            decisions = {item.segment_id: item for item in result.verifications}
            for repair in repaired.repairs:
                segment_result_index += 1
                decision = decisions.get(repair.segment_id)
                if repair.segment_id in blocking_by_id:
                    outcome, mode, count = "failed", "deterministic-prescreen", len(
                        blocking_by_id[repair.segment_id]
                    )
                elif decision is not None:
                    outcome, mode, count = (
                        "passed" if decision.passed else "failed",
                        "semantic-review",
                        0 if decision.passed else 1,
                    )
                elif repair.disposition is RepairDisposition.REVIEW:
                    outcome, mode, count = "pending", "prior-review", len(repair.issues)
                else:
                    outcome, mode, count = "skipped", "not-problematic", 0
                report_segment_result(
                    client,
                    model=segment_models.get(
                        repair.segment_id,
                        config.audit.verifier_model or config.audit.model,
                    ),
                    stage=stage.value,
                    segment_id=repair.segment_id,
                    result=outcome,
                    mode=mode,
                    issue_count=count,
                    context_bucket=segment_contexts.get(repair.segment_id, 0),
                    result_index=segment_result_index,
                    result_total=segment_result_total,
                    role=(
                        "review_repaired.reprose"
                        if repair.segment_id in {
                            segment_id
                            for task in tasks
                            if task.get("require_candidate_win")
                            for segment_id in task["ids"]
                        }
                        else "review_repaired.standard"
                    ),
                )

        report = RepairedValidationReport(
            document_count=len(repaired_documents),
            segment_count=segment_count,
            passed=not review_ids,
            remaining_issue_count=len(review_ids),
            review_segment_ids=sorted(review_ids),
        )
        report_path = stage_root / "repaired-review.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "repaired_review_report", stage)
        set_job_metadata(
            connection, "repaired_review_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "repaired_review_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, stage)
        set_stage_status(
            connection, stage.value, StageStatus.COMPLETED,
            attempts=attempts, input_hash=input_hash, output_hash=output_hash,
            message=(f"{len(review_ids)} segment(s) queued for repair" if review_ids else ""),
        )
        return report
    except Exception as error:
        _mark_failed(connection, stage, error)
        raise
    finally:
        connection.close()


def load_repaired_review_results(
    workspace: JobWorkspace,
) -> dict[str, RepairVerificationResult]:
    """Load initial Gemma decisions keyed by document manifest ID."""
    connection = connect_state(workspace.state_file)
    try:
        results: dict[str, RepairVerificationResult] = {}
        for artifact in list_active_stage_artifacts(
            connection,
            WorkflowStage.REVIEW_REPAIRED.value,
            root_metadata_key="repaired_review_root",
            report_metadata_key="repaired_review_report",
        ):
            if artifact["kind"] != "repaired_review_result":
                continue
            path = workspace.directory(str(artifact["path"]))
            name = path.name
            manifest_id = name.split("-", 1)[1].removesuffix(".review.json")
            results[manifest_id] = RepairVerificationResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        return results
    finally:
        connection.close()


def load_repaired_review_report(workspace: JobWorkspace, *, connection=None):
    owns = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        from ..state import get_job_metadata
        relative = get_job_metadata(active, "repaired_review_report")
        if not relative:
            raise FileNotFoundError("repaired review report is not recorded")
        return RepairedValidationReport.model_validate_json(
            workspace.directory(relative).read_text(encoding="utf-8")
        )
    finally:
        if owns:
            active.close()


def _run_verification(
    connection, stage, unit_id, document_id, input_hash, path, prompt,
    expected_ids, config, client, *, existing_attempts, index, total,
    context_bucket=None, model=None, thinking=None, usage_role="review_repaired.standard",
    candidate_first=False, require_candidate_win=False,
):
    allowed = min(config.workflow.max_retries + 1, config.audit.repair_max_attempts)
    last_error = ""
    for offset in range(1, allowed + 1):
        attempt = existing_attempts + offset
        generated = None
        request = prompt
        if last_error:
            request += (
                "\n\nThe previous verification was rejected as invalid. Return every required "
                "decision using the exact Allowed IDs. Keep every message to one "
                "sentence of at most 40 words and close all JSON arrays and objects."
            )
        try:
            generated = client.generate_structured(
                request, PairwiseRepairVerificationResult,
                model=model or config.audit.verifier_model or config.audit.model,
                think=config.audit.thinking if thinking is None else thinking,
                progress_label=(
                    f"review={index}/{total} id={unit_id} attempt={offset}/{allowed}"
                ),
                **llm_role_kwargs(client, usage_role),
                context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
                context_maximum=(context_bucket or config.audit.max_num_ctx),
                max_output_tokens=repair_verification_output_token_limit(
                    len(expected_ids)
                ),
                max_attempts=1,
            )
            if isinstance(generated.value, RepairVerificationResult):
                # Compatibility for older injected clients and saved test doubles.
                result = validate_repair_verification_scope(
                    generated.value, expected_ids
                )
            else:
                pairwise = validate_pairwise_repair_verification_scope(
                    generated.value, expected_ids
                )
                result = map_pairwise_repair_verification(
                    pairwise, candidate_first=candidate_first
                )
                if require_candidate_win:
                    candidate_winner = "a" if candidate_first else "b"
                    winners = {
                        item.segment_id: item.winner
                        for item in pairwise.verifications
                    }
                    result = RepairVerificationResult(
                        verifications=[
                            item.model_copy(
                                update={
                                    "passed": (
                                        item.passed
                                        and winners[item.segment_id]
                                        == candidate_winner
                                    ),
                                    "message": (
                                        item.message
                                        if winners[item.segment_id]
                                        == candidate_winner
                                        else "Prose rewrite was not materially better; "
                                        "the last-known-good translation is retained."
                                    ),
                                }
                            )
                            for item in result.verifications
                        ]
                    )
        except (StructuredOutputError, ValueError) as error:
            last_error = str(error)
            record_attempt(
                connection, unit_id, stage.value, attempt, StageStatus.FAILED,
                message=last_error,
                metrics=(
                    asdict(generated.generation.metrics)
                    if generated is not None
                    else {}
                ),
            )
            continue
        atomic_write_text(path, result.model_dump_json(indent=2))
        set_work_unit_status(
            connection, unit_id, stage.value, "repair_review_verification",
            StageStatus.COMPLETED, parent_id=document_id, attempts=attempt,
            input_hash=input_hash, output_hash=sha256_file(path),
            validation={"scope_valid": True, "all_passed": all(x.passed for x in result.verifications)},
        )
        record_attempt(
            connection, unit_id, stage.value, attempt, StageStatus.COMPLETED,
            metrics=asdict(generated.generation.metrics),
        )
        record_validation(
            connection, unit_id, "repair_review_scope", True,
            details={"expected_ids": expected_ids},
        )
        return result
    result = RepairVerificationResult(
        verifications=[
            RepairVerification(
                segment_id=segment_id,
                passed=False,
                current_acceptable=False,
                message=(
                    "The verifier exhausted its structured-output attempts; "
                    "this segment requires human review."
                ),
            )
            for segment_id in expected_ids
        ]
    )
    atomic_write_text(path, result.model_dump_json(indent=2))
    set_work_unit_status(
        connection, unit_id, stage.value, "repair_review_verification",
        StageStatus.COMPLETED, parent_id=document_id,
        attempts=existing_attempts + allowed, input_hash=input_hash,
        output_hash=sha256_file(path),
        message="structured verification exhausted; escalated to human review",
        validation={
            "scope_valid": True,
            "all_passed": False,
            "fallback_escalation": True,
        },
    )
    record_validation(
        connection, unit_id, "repair_review_scope", True,
        details={
            "expected_ids": expected_ids,
            "fallback_escalation": True,
            "error": last_error,
        },
    )
    return result


def _load_current(existing, path, input_hash):
    if not (
        existing and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return RepairVerificationResult.model_validate_json(path.read_text(encoding="utf-8"))


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
