"""Resumable deterministic and selective semantic translation-audit stage."""

from __future__ import annotations

from dataclasses import asdict

from ..atomic_io import atomic_write_text
from ..audit import (
    AuditCategory,
    AuditIssue,
    AuditRiskTag,
    AuditSeverity,
    DocumentAudit,
    SemanticAuditResult,
    TranslationAuditReport,
    audit_translated_document,
    build_semantic_audit_batches,
    build_semantic_audit_prompt,
    merge_audit_issues,
    validate_semantic_audit_scope,
)
from ..config import AppConfig
from ..content_policy import is_intentionally_preserved
from ..hashing import hash_named_values, sha256_file
from ..ollama_client import OllamaClient, StructuredOutputError, estimate_request_tokens
from ..stage_progress import (
    group_by_context_bucket,
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
from ..stage_artifacts import list_active_stage_artifacts, require_unique_items
from ..workspace import JobWorkspace
from ..quantities import (
    QuantityAuditDecision,
    QuantityAuditResult,
    QuantityComparison,
    QuantityKind,
    QuantityMismatchKind,
    build_quantity_audit_prompt,
    validate_quantity_audit_scope,
)
from .preprocess import load_preprocessed_documents
from .translate import load_translated_documents, load_translation_report


AUDIT_STAGE_VERSION = "20"


def run_translation_audit_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> TranslationAuditReport:
    """Audit translated documents and semantically review only selected risky segments."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        preprocessing = _require_completed(connection, WorkflowStage.PREPROCESS)
        translation = _require_completed(connection, WorkflowStage.TRANSLATE)
        input_hash = build_stage_input_hash(
            {
                "preprocessing": str(preprocessing["output_hash"]),
                "translation": str(translation["output_hash"]),
                "audit": config.audit.model_dump_json(),
                "model": config.audit.model if config.audit.semantic_enabled else "disabled",
                "stage_version": AUDIT_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.AUDIT_TRANSLATION,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_translation_audit_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.AUDIT_TRANSLATION.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.AUDIT_TRANSLATION)
            previous = get_stage_status(connection, WorkflowStage.AUDIT_TRANSLATION.value)
        retire_stage_artifacts(connection, WorkflowStage.AUDIT_TRANSLATION.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.AUDIT_TRANSLATION.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        sources = load_preprocessed_documents(workspace)
        translated = {item.manifest_id: item for item in load_translated_documents(workspace)}
        translation_report = load_translation_report(workspace, connection=connection)
        deferred_segment_ids = set(translation_report.deferred_segment_ids)
        if {item.manifest_id for item in sources} != set(translated):
            raise ValueError("preprocessed and translated document sets differ")
        if (config.audit.semantic_enabled or config.audit.quantity.enabled) and client is None:
            raise ValueError(
                "an Ollama client is required when semantic or quantity audit is enabled"
            )

        stage_root = workspace.directory(f"audited/{input_hash[:16]}")
        batch_root = stage_root / "semantic"
        batch_root.mkdir(parents=True, exist_ok=True)
        quantity_root = stage_root / "quantity"
        quantity_root.mkdir(parents=True, exist_ok=True)
        semantic_context_hard_maximum = (
            config.audit.semantic_max_num_ctx or config.audit.max_num_ctx
        )
        semantic_context_preferred_maximum = min(
            semantic_context_hard_maximum,
            config.ollama.model_num_ctx_caps.get(
                config.audit.model,
                semantic_context_hard_maximum,
            ),
        )
        audit_plans = []
        for source in sources:
            target = translated[source.manifest_id]
            deterministic = audit_translated_document(source, target, config.audit)
            deterministic = _include_deferred_translation_findings(
                source,
                target,
                deterministic,
                deferred_segment_ids,
            )
            candidates = (
                deterministic.semantic_candidate_ids if config.audit.semantic_enabled else []
            )
            batches = build_semantic_audit_batches(
                source,
                target,
                candidates,
                config.audit.semantic_batch_source_tokens,
                max_request_context=semantic_context_hard_maximum,
                max_candidates_per_batch=(
                    config.audit.semantic_max_candidates_per_batch
                ),
            )
            audit_plans.append((source, target, deterministic, batches))

        semantic_results = {}
        semantic_tasks = []
        segment_contexts: dict[str, int] = {}
        for document_index, (source, target, _deterministic, batches) in enumerate(
            audit_plans, start=1
        ):
            for batch_number, candidate_ids in enumerate(batches, start=1):
                allowed_ids = candidate_ids
                unit_id = f"audit-{source.order:04d}-{batch_number:05d}"
                prompt = build_semantic_audit_prompt(
                    source,
                    target,
                    candidate_ids,
                    max_request_context=semantic_context_hard_maximum,
                )
                unit_input_hash = hash_named_values(
                    {"stage": input_hash, "prompt": prompt, "ids": "\n".join(candidate_ids)}
                )
                path = batch_root / f"{unit_id}.json"
                existing = get_work_unit(
                    connection, unit_id, WorkflowStage.AUDIT_TRANSLATION.value
                )
                result = _load_current_semantic_result(existing, path, unit_input_hash)
                if not config.ollama.adaptive_num_ctx:
                    bucket = semantic_context_hard_maximum
                elif (
                    estimate_request_tokens(
                        prompt,
                        SemanticAuditResult.model_json_schema(),
                    )
                    + 4_096
                    <= semantic_context_preferred_maximum
                ):
                    bucket = semantic_context_preferred_maximum
                else:
                    bucket = request_context_bucket(
                        prompt,
                        minimum=min(
                            config.ollama.min_num_ctx,
                            semantic_context_hard_maximum,
                        ),
                        maximum=semantic_context_hard_maximum,
                        schema=SemanticAuditResult,
                    )
                for segment_id in allowed_ids:
                    segment_contexts[segment_id] = bucket
                task = {
                    "source": source,
                    "target": target,
                    "document_index": document_index,
                    "candidate_ids": candidate_ids,
                    "allowed_ids": allowed_ids,
                    "unit_id": unit_id,
                    "prompt": prompt,
                    "unit_input_hash": unit_input_hash,
                    "path": path,
                    "existing": existing,
                    "bucket": bucket,
                }
                if result is None:
                    semantic_tasks.append(task)
                else:
                    semantic_results[(source.manifest_id, batch_number)] = result
                    _record_file(connection, workspace, path, "semantic_audit_batch")

        quantity_results: dict[tuple[str, str], QuantityAuditResult] = {}
        quantity_tasks = []
        quantity_escalation_tasks = []
        if config.audit.quantity.enabled:
            for document_index, (source, target, deterministic, _batches) in enumerate(
                audit_plans, start=1
            ):
                source_by_id = {
                    item.segment_id: item.processed_text for item in source.segments
                }
                target_by_id = {
                    item.segment_id: item.translated_text for item in target.segments
                }
                ordered_ids = [item.segment_id for item in source.segments]
                comparisons = {
                    item.segment_id: item for item in deterministic.quantity_comparisons
                }
                for candidate_index, segment_id in enumerate(
                    deterministic.quantity_candidate_ids, start=1
                ):
                    position = ordered_ids.index(segment_id)
                    prompt = build_quantity_audit_prompt(
                        segment_id,
                        source_by_id[segment_id],
                        target_by_id[segment_id],
                        comparisons[segment_id],
                        preceding_source=(
                            source_by_id[ordered_ids[position - 1]]
                            if position
                            else "(none)"
                        ),
                        following_source=(
                            source_by_id[ordered_ids[position + 1]]
                            if position + 1 < len(ordered_ids)
                            else "(none)"
                        ),
                    )
                    unit_id = f"quantity-{source.order:04d}-{candidate_index:05d}"
                    unit_input_hash = hash_named_values(
                        {
                            "stage": input_hash,
                            "prompt": prompt,
                            "segment_id": segment_id,
                            "model": config.audit.quantity.model,
                            "escalation_model": (
                                config.audit.quantity.escalation_model or ""
                            ),
                        }
                    )
                    path = quantity_root / f"{unit_id}.json"
                    existing = get_work_unit(
                        connection, unit_id, WorkflowStage.AUDIT_TRANSLATION.value
                    )
                    result = _load_current_quantity_result(
                        existing, path, unit_input_hash
                    )
                    bucket = (
                        config.audit.quantity.max_num_ctx
                        if not config.ollama.adaptive_num_ctx
                        else request_context_bucket(
                            prompt,
                            minimum=min(
                                config.ollama.min_num_ctx,
                                config.audit.quantity.max_num_ctx,
                            ),
                            maximum=config.audit.quantity.max_num_ctx,
                            schema=QuantityAuditResult,
                        )
                    )
                    segment_contexts[segment_id] = max(
                        segment_contexts.get(segment_id, 0), bucket
                    )
                    task = {
                        "source": source,
                        "target": target,
                        "document_index": document_index,
                        "segment_id": segment_id,
                        "source_text": source_by_id[segment_id],
                        "target_text": target_by_id[segment_id],
                        "comparison": comparisons[segment_id],
                        "unit_id": unit_id,
                        "prompt": prompt,
                        "unit_input_hash": unit_input_hash,
                        "path": path,
                        "existing": existing,
                        "bucket": bucket,
                    }
                    if result is None:
                        quantity_tasks.append(task)
                    elif _stored_quantity_requires_escalation(existing, config):
                        quantity_escalation_tasks.append(task)
                    else:
                        quantity_results[(source.manifest_id, segment_id)] = result
                        _record_file(
                            connection, workspace, path, "quantity_audit_decision"
                        )

        if config.audit.quantity.enabled:
            report_stage_plan(
                client,
                model=config.audit.quantity.model,
                stage=f"{WorkflowStage.AUDIT_TRANSLATION.value}:quantity",
                prescreened=sum(
                    len(item[2].quantity_comparisons) for item in audit_plans
                ),
                llm_tasks=len(quantity_tasks),
                skipped=sum(
                    len(item[2].quantity_comparisons) for item in audit_plans
                ) - len(quantity_tasks),
                context_buckets=(task["bucket"] for task in quantity_tasks),
                role="audit.quantity.base",
            )
            ordered_quantity_tasks = group_by_context_bucket(
                quantity_tasks, lambda task: int(task["bucket"])
            )
            for task_index, task in enumerate(ordered_quantity_tasks, start=1):
                source = task["source"]
                result = _run_quantity_task(
                    connection,
                    task,
                    config,
                    client,
                    task_index=task_index,
                    total_tasks=len(ordered_quantity_tasks),
                    total_documents=len(audit_plans),
                    model=config.audit.quantity.model,
                    defer_escalation=_has_distinct_quantity_escalation(config),
                )
                _record_file(
                    connection,
                    workspace,
                    task["path"],
                    "quantity_audit_decision",
                )
                if _quantity_requires_escalation(
                    result, config, task["comparison"]
                ):
                    quantity_escalation_tasks.append(task)
                else:
                    quantity_results[(source.manifest_id, task["segment_id"])] = result

        report_stage_plan(
            client,
            model=config.audit.model,
            stage=WorkflowStage.AUDIT_TRANSLATION.value,
            prescreened=sum(item[2].segment_count for item in audit_plans),
            llm_tasks=len(semantic_tasks),
            skipped=(
                sum(item[2].segment_count for item in audit_plans)
                - sum(len(task["candidate_ids"]) for task in semantic_tasks)
            ),
            context_buckets=(task["bucket"] for task in semantic_tasks),
            role="audit.semantic",
        )
        ordered_tasks = group_by_context_bucket(
            semantic_tasks, lambda task: int(task["bucket"])
        )
        total_semantic_batches = len(ordered_tasks)
        for semantic_batch_index, task in enumerate(ordered_tasks, start=1):
            source = task["source"]
            target = task["target"]
            result = _run_semantic_batch(
                connection,
                task["unit_id"],
                source.manifest_id,
                task["unit_input_hash"],
                task["path"],
                task["prompt"],
                task["allowed_ids"],
                {
                    item.segment_id: item.processed_text
                    for item in source.segments
                    if item.segment_id in set(task["allowed_ids"])
                },
                {
                    item.segment_id: item.translated_text
                    for item in target.segments
                    if item.segment_id in set(task["allowed_ids"])
                },
                config,
                client,
                existing_attempts=(
                    int(task["existing"]["attempts"]) if task["existing"] else 0
                ),
                batch_index=semantic_batch_index,
                total_batches=total_semantic_batches,
                document_index=task["document_index"],
                total_documents=len(audit_plans),
                context_bucket=int(task["bucket"]),
            )
            batch_number = int(str(task["unit_id"]).rsplit("-", 1)[1])
            semantic_results[(source.manifest_id, batch_number)] = result
            _record_file(
                connection, workspace, task["path"], "semantic_audit_batch"
            )

        if quantity_escalation_tasks:
            escalation_model = str(config.audit.quantity.escalation_model)
            ordered_escalations = group_by_context_bucket(
                quantity_escalation_tasks, lambda task: int(task["bucket"])
            )
            report_stage_plan(
                client,
                model=escalation_model,
                stage=f"{WorkflowStage.AUDIT_TRANSLATION.value}:quantity-escalation",
                prescreened=len(quantity_escalation_tasks),
                llm_tasks=len(quantity_escalation_tasks),
                skipped=0,
                context_buckets=(task["bucket"] for task in quantity_escalation_tasks),
                role="audit.quantity.escalation",
            )
            for task_index, task in enumerate(ordered_escalations, start=1):
                source = task["source"]
                result = _run_quantity_task(
                    connection,
                    task,
                    config,
                    client,
                    task_index=task_index,
                    total_tasks=len(ordered_escalations),
                    total_documents=len(audit_plans),
                    model=escalation_model,
                    independent_escalation=True,
                )
                quantity_results[(source.manifest_id, task["segment_id"])] = result
                _record_file(
                    connection,
                    workspace,
                    task["path"],
                    "quantity_audit_decision",
                )

        documents: list[DocumentAudit] = []
        segment_result_index = 0
        segment_result_total = sum(
            len(source.segments)
            for source, _target, _deterministic, _batches in audit_plans
        )
        for document_index, (source, target, deterministic, batches) in enumerate(
            audit_plans, start=1
        ):
            semantic_issues = []
            for batch_number, candidate_ids in enumerate(batches, start=1):
                result = semantic_results[(source.manifest_id, batch_number)]
                semantic_issues.extend(result.issues)

            quantity_issues: list[AuditIssue] = []
            comparisons = list(deterministic.quantity_comparisons)
            comparison_index = {
                item.segment_id: index for index, item in enumerate(comparisons)
            }
            for segment_id in deterministic.quantity_candidate_ids:
                result = quantity_results[(source.manifest_id, segment_id)]
                decision = result.decisions[0]
                index = comparison_index[segment_id]
                comparisons[index] = comparisons[index].model_copy(
                    update={
                        "status": decision.status,
                        "reason": decision.message,
                        "adjudicated": True,
                    }
                )
                issue = _quantity_decision_issue(
                    decision,
                    comparisons[index],
                    config.audit.quantity.mode,
                )
                if issue is not None:
                    quantity_issues.append(issue)

            adjudicated_quantity_ids = set(deterministic.quantity_candidate_ids)
            deterministic_issues = [
                issue
                for issue in deterministic.issues
                if not (
                    issue.source == "quantity-deterministic"
                    and issue.segment_id in adjudicated_quantity_ids
                )
            ]
            merged = merge_audit_issues(
                deterministic_issues,
                [*semantic_issues, *quantity_issues],
            )
            document = deterministic.model_copy(
                update={
                    "issues": merged,
                    "quantity_comparisons": comparisons,
                    "passed": not any(
                        item.severity.rank >= AuditSeverity.MEDIUM.rank for item in merged
                    ),
                }
            )
            path = stage_root / f"{source.order:04d}-{source.manifest_id}.audit.json"
            atomic_write_text(path, document.model_dump_json(indent=2))
            _record_file(connection, workspace, path, "document_audit")
            document_input_hash = hash_named_values(
                {"stage": input_hash, "document": source.source_sha256}
            )
            set_work_unit_status(
                connection,
                f"document:{source.manifest_id}",
                WorkflowStage.AUDIT_TRANSLATION.value,
                "document",
                StageStatus.COMPLETED,
                attempts=1,
                input_hash=document_input_hash,
                output_hash=sha256_file(path),
                validation={"passed": document.passed, "issue_count": len(merged)},
            )
            documents.append(document)
            issues_by_id = {}
            for issue in merged:
                issues_by_id.setdefault(issue.segment_id, []).append(issue)
            semantic_ids = {
                segment_id for batch in batches for segment_id in batch
            }
            quantity_ids = set(deterministic.quantity_candidate_ids)
            for segment in source.segments:
                segment_result_index += 1
                issues = issues_by_id.get(segment.segment_id, [])
                blocking = any(
                    item.severity.rank >= AuditSeverity.MEDIUM.rank for item in issues
                )
                report_segment_result(
                    client,
                    model=(
                        config.audit.quantity.model
                        if segment.segment_id in quantity_ids
                        else config.audit.model
                    ),
                    stage=WorkflowStage.AUDIT_TRANSLATION.value,
                    segment_id=segment.segment_id,
                    result="failed" if blocking else "passed",
                    mode=(
                        "quantity"
                        if segment.segment_id in quantity_ids
                        else "semantic"
                        if segment.segment_id in semantic_ids
                        else "deterministic-prescreen"
                    ),
                    issue_count=len(issues),
                    context_bucket=segment_contexts.get(segment.segment_id, 0),
                    result_index=segment_result_index,
                    result_total=segment_result_total,
                    role=(
                        "audit.quantity"
                        if segment.segment_id in quantity_ids
                        else "audit.semantic"
                        if segment.segment_id in semantic_ids
                        else "audit.deterministic"
                    ),
                )

        review_ids = sorted(
            {
                issue.segment_id
                for document in documents
                for issue in document.issues
                if issue.severity.rank >= AuditSeverity.MEDIUM.rank
            }
        )
        report = TranslationAuditReport(
            direction=config.translation.direction,
            document_count=len(documents),
            segment_count=sum(item.segment_count for item in documents),
            passed=not review_ids,
            issue_count=sum(len(item.issues) for item in documents),
            review_segment_ids=review_ids,
            quantity_checked_segment_count=sum(
                len(item.quantity_comparisons) for item in documents
            ),
            quantity_mismatch_count=sum(
                comparison.status == "mismatch"
                for item in documents
                for comparison in item.quantity_comparisons
            ),
            quantity_uncertain_count=sum(
                comparison.status == "uncertain"
                for item in documents
                for comparison in item.quantity_comparisons
            ),
        )
        report_path = stage_root / "translation-audit.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "translation_audit_report")
        set_job_metadata(
            connection,
            "translation_audit_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "audited_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.AUDIT_TRANSLATION)
        set_stage_status(
            connection,
            WorkflowStage.AUDIT_TRANSLATION.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def _include_deferred_translation_findings(
    source,
    target,
    deterministic: DocumentAudit,
    deferred_segment_ids: set[str],
) -> DocumentAudit:
    """Force exhausted first-draft passages through audit and repair."""
    ordered_ids = [segment.segment_id for segment in source.segments]
    source_by_id = {item.segment_id: item for item in source.segments}
    target_by_id = {item.segment_id: item for item in target.segments}
    local_deferred = [
        item
        for item in ordered_ids
        if item in deferred_segment_ids
        and item in target_by_id
        and not is_intentionally_preserved(
            source_by_id[item].processed_text,
            target_by_id[item].translated_text,
            target.direction,
            source.relevant_glossary,
        )
    ]
    if not local_deferred:
        return deterministic
    deferred = set(local_deferred)
    issues = []
    covered: set[str] = set()
    for issue in deterministic.issues:
        if (
            issue.segment_id in deferred
            and issue.category is AuditCategory.UNTRANSLATED
        ):
            issues.append(
                issue.model_copy(
                    update={
                        "severity": AuditSeverity.HIGH,
                        "message": (
                            "first-draft translation retries exhausted; source text was "
                            "retained for downstream repair"
                        ),
                        "suggested_fix": "Translate the complete source segment.",
                        "source": "translation-deferred",
                    }
                )
            )
            covered.add(issue.segment_id)
        else:
            issues.append(issue)
    for segment_id in local_deferred:
        if segment_id in covered:
            continue
        issues.append(
            AuditIssue(
                segment_id=segment_id,
                category=AuditCategory.UNTRANSLATED,
                severity=AuditSeverity.HIGH,
                message=(
                    "first-draft translation retries exhausted; source text was retained "
                    "for downstream repair"
                ),
                suggested_fix="Translate the complete source segment.",
                source="translation-deferred",
            )
        )
    candidates = set(deterministic.semantic_candidate_ids) | deferred
    return deterministic.model_copy(
        update={
            "passed": False,
            "issues": issues,
            "semantic_candidate_ids": [
                segment_id for segment_id in ordered_ids if segment_id in candidates
            ],
        }
    )


def load_document_audits(workspace: JobWorkspace) -> list[DocumentAudit]:
    """Load published document audits in document order."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.AUDIT_TRANSLATION.value,
            root_metadata_key="audited_root",
            report_metadata_key="translation_audit_report",
        )
        documents = []
        for artifact in artifacts:
            if artifact["kind"] != "document_audit":
                continue
            path = workspace.directory(str(artifact["path"]))
            documents.append(DocumentAudit.model_validate_json(path.read_text(encoding="utf-8")))
        order = {
            item.manifest_id: item.order for item in load_preprocessed_documents(workspace)
        }
        documents = require_unique_items(
            documents,
            lambda item: item.document_id,
            label="document audit",
        )
        return sorted(documents, key=lambda item: order[item.document_id])
    finally:
        connection.close()


def load_translation_audit_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> TranslationAuditReport:
    """Load the published whole-book translation audit report."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "translation_audit_report")
        if not relative:
            raise FileNotFoundError("translation audit report is not recorded")
        path = workspace.directory(relative)
        return TranslationAuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def _run_semantic_batch(
    connection,
    unit_id,
    document_id,
    input_hash,
    path,
    prompt,
    allowed_ids,
    source_text_by_id,
    translation_text_by_id,
    config,
    client,
    *,
    existing_attempts,
    batch_index,
    total_batches,
    document_index,
    total_documents,
    context_bucket=None,
):
    attempts = config.workflow.max_retries + 1
    last_error = ""
    last_failure_was_structured = False
    # A resumed failed unit has already consumed bounded attempts. Start it with
    # the expanded allowance instead of replaying the smallest cap again.
    structured_failure_count = min(existing_attempts, 4)
    for offset in range(1, attempts + 1):
        attempt = existing_attempts + offset
        request_prompt = prompt
        if last_error:
            request_prompt += (
                "\n\nThe previous structured result was rejected. Return a complete corrected "
                f"result. Validation error: {last_error}"
            )
        try:
            # A syntactically incomplete structured response usually means the
            # model exhausted num_predict.  Repeating the same capped request
            # only reproduces the truncation, so give each structured retry
            # progressively more room while staying inside the configured
            # schema limit.  Scope-validation retries do not expand the cap.
            output_token_limit = min(
                8_192,
                config.audit.semantic_max_output_tokens
                * (2 ** structured_failure_count),
            )
            generated = client.generate_structured(
                request_prompt,
                SemanticAuditResult,
                model=config.audit.model,
                context_maximum=(context_bucket or config.audit.max_num_ctx),
                think=config.audit.thinking,
                progress_label=(
                    f"batch={batch_index}/{total_batches} id={unit_id} "
                    f"document={document_index}/{total_documents} "
                    f"attempt={offset}/{attempts}"
                ),
                **llm_role_kwargs(client, "audit.semantic"),
                max_output_tokens=output_token_limit,
                max_attempts=1,
                **(
                    {"allow_model_context_cap_override": True}
                    if isinstance(client, OllamaClient)
                    and context_bucket is not None
                    and context_bucket
                    > config.ollama.model_num_ctx_caps.get(
                        config.audit.model,
                        context_bucket,
                    )
                    else {}
                ),
            )
        except StructuredOutputError as error:
            last_error = str(error)
            last_failure_was_structured = True
            structured_failure_count += 1
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.AUDIT_TRANSLATION.value,
                attempt,
                StageStatus.FAILED,
                message=last_error,
            )
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.AUDIT_TRANSLATION.value,
                "semantic_batch",
                StageStatus.FAILED,
                parent_id=document_id,
                attempts=attempt,
                input_hash=input_hash,
                message=last_error,
            )
            continue
        try:
            result = validate_semantic_audit_scope(
                generated.value,
                allowed_ids,
                source_text_by_id,
                translation_text_by_id,
                quantity_enabled=config.audit.quantity.enabled,
            )
        except ValueError as error:
            last_error = str(error)
            last_failure_was_structured = False
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.AUDIT_TRANSLATION.value,
                attempt,
                StageStatus.FAILED,
                message=last_error,
                metrics=asdict(generated.generation.metrics),
            )
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.AUDIT_TRANSLATION.value,
                "semantic_batch",
                StageStatus.FAILED,
                parent_id=document_id,
                attempts=attempt,
                input_hash=input_hash,
                message=last_error,
            )
            continue
        atomic_write_text(path, result.model_dump_json(indent=2))
        set_work_unit_status(
            connection,
            unit_id,
            WorkflowStage.AUDIT_TRANSLATION.value,
            "semantic_batch",
            StageStatus.COMPLETED,
            parent_id=document_id,
            attempts=attempt,
            input_hash=input_hash,
            output_hash=sha256_file(path),
            validation={
                "scope_valid": True,
                "issue_count": len(result.issues),
                "discarded_issue_count": (
                    len(generated.value.issues) - len(result.issues)
                ),
            },
        )
        record_attempt(
            connection,
            unit_id,
            WorkflowStage.AUDIT_TRANSLATION.value,
            attempt,
            StageStatus.COMPLETED,
            metrics=asdict(generated.generation.metrics),
        )
        record_validation(
            connection,
            unit_id,
            "semantic_audit_scope",
            True,
            details={
                "allowed_ids": allowed_ids,
                "issue_count": len(result.issues),
                "discarded_issue_count": (
                    len(generated.value.issues) - len(result.issues)
                ),
            },
        )
        return result
    if last_failure_was_structured:
        result = SemanticAuditResult(
            issues=[
                AuditIssue(
                    segment_id=segment_id,
                    category=AuditCategory.MISTRANSLATION,
                    severity=AuditSeverity.HIGH,
                    message=(
                        "Semantic audit could not produce a complete structured decision "
                        f"after {attempts} attempts; this segment requires conservative "
                        "source-grounded repair and independent verification."
                    ),
                    suggested_fix=(
                        "Recheck and, if necessary, repair the complete segment against "
                        "the source without adding, omitting, or changing any atomic fact."
                    ),
                    source="semantic",
                    source_quote=source_text_by_id.get(segment_id, "")[:240],
                    translation_quote=translation_text_by_id.get(segment_id, "")[:240],
                )
                for segment_id in allowed_ids
            ]
        )
        atomic_write_text(path, result.model_dump_json(indent=2))
        set_work_unit_status(
            connection,
            unit_id,
            WorkflowStage.AUDIT_TRANSLATION.value,
            "semantic_batch",
            StageStatus.COMPLETED,
            parent_id=document_id,
            attempts=existing_attempts + attempts,
            input_hash=input_hash,
            output_hash=sha256_file(path),
            message=(
                "structured audit exhausted; all batch segments escalated for "
                "repair and verification"
            ),
            validation={
                "scope_valid": False,
                "fallback_escalation": True,
                "issue_count": len(result.issues),
                "discarded_issue_count": 0,
            },
        )
        record_validation(
            connection,
            unit_id,
            "semantic_audit_scope",
            False,
            details={
                "allowed_ids": allowed_ids,
                "fallback_escalation": True,
                "error": last_error,
            },
        )
        return result
    raise ValueError(f"semantic audit batch {unit_id} failed validation: {last_error}")


def _run_quantity_task(
    connection,
    task,
    config,
    client,
    *,
    task_index,
    total_tasks,
    total_documents,
    model,
    defer_escalation=False,
    independent_escalation=False,
) -> QuantityAuditResult:
    """Run one model phase of extraction-first quantity adjudication."""
    quantity = config.audit.quantity
    existing = get_work_unit(
        connection, task["unit_id"], WorkflowStage.AUDIT_TRANSLATION.value
    )
    existing_attempts = int(existing["attempts"]) if existing else 0
    attempt = existing_attempts
    last_error = ""
    accepted: QuantityAuditResult | None = None
    accepted_model = ""
    generated_result: QuantityAuditResult | None = None
    for retry in range(1, config.workflow.max_retries + 2):
        attempt += 1
        request_prompt = task["prompt"]
        if independent_escalation:
            request_prompt += (
                "\n\nThis is an independent escalation. Re-extract the facts from raw "
                "SOURCE and TRANSLATION; do not assume the earlier model was correct."
            )
        if last_error:
            request_prompt += (
                "\n\nThe previous structured response was invalid. Return a complete "
                f"corrected result. Validation error: {last_error}"
            )
        try:
            generated = client.generate_structured(
                request_prompt,
                QuantityAuditResult,
                model=model,
                context_maximum=int(task["bucket"]),
                think=quantity.thinking,
                progress_label=(
                    f"quantity={task_index}/{total_tasks} id={task['unit_id']} "
                    f"document={task['document_index']}/{total_documents} "
                    f"model={model} attempt={retry}/{config.workflow.max_retries + 1}"
                ),
                **llm_role_kwargs(
                    client,
                    "audit.quantity.escalation"
                    if independent_escalation
                    else "audit.quantity.base",
                ),
                max_output_tokens=1_024,
                max_attempts=1,
            )
            generated_result = validate_quantity_audit_scope(
                generated.value,
                task["segment_id"],
                task["source_text"],
                task["target_text"],
            )
        except (StructuredOutputError, ValueError) as error:
            last_error = str(error)
            record_attempt(
                connection,
                task["unit_id"],
                WorkflowStage.AUDIT_TRANSLATION.value,
                attempt,
                StageStatus.FAILED,
                message=last_error,
            )
            continue
        record_attempt(
            connection,
            task["unit_id"],
            WorkflowStage.AUDIT_TRANSLATION.value,
            attempt,
            StageStatus.COMPLETED,
            metrics=asdict(generated.generation.metrics),
        )
        break
    if generated_result is not None:
        accepted = generated_result
        accepted_model = model

    if accepted is None:
        accepted = QuantityAuditResult(
            decisions=[
                QuantityAuditDecision(
                    segment_id=task["segment_id"],
                    status="uncertain",
                    message=(
                        "Quantity adjudication could not produce a grounded structured "
                        "decision and requires conservative review."
                    ),
                    confidence=0.0,
                )
            ]
        )
        accepted_model = "fallback"

    atomic_write_text(task["path"], accepted.model_dump_json(indent=2))
    decision = accepted.decisions[0]
    requires_escalation = defer_escalation and _quantity_requires_escalation(
        accepted, config, task["comparison"]
    )
    set_work_unit_status(
        connection,
        task["unit_id"],
        WorkflowStage.AUDIT_TRANSLATION.value,
        "quantity_decision",
        StageStatus.COMPLETED,
        parent_id=task["source"].manifest_id,
        attempts=attempt,
        input_hash=task["unit_input_hash"],
        output_hash=sha256_file(task["path"]),
        validation={
            "scope_valid": accepted_model != "fallback",
            "status": decision.status,
            "confidence": decision.confidence,
            "model": accepted_model,
            "requires_escalation": requires_escalation,
        },
    )
    record_validation(
        connection,
        task["unit_id"],
        "quantity_audit_scope",
        accepted_model != "fallback",
        details={
            "segment_id": task["segment_id"],
            "status": decision.status,
            "confidence": decision.confidence,
            "model": accepted_model,
            "requires_escalation": requires_escalation,
            "error": last_error,
        },
    )
    return accepted


def _has_distinct_quantity_escalation(config: AppConfig) -> bool:
    escalation_model = config.audit.quantity.escalation_model
    return bool(
        escalation_model and escalation_model != config.audit.quantity.model
    )


def _quantity_requires_escalation(
    result: QuantityAuditResult,
    config: AppConfig,
    comparison: QuantityComparison | None = None,
) -> bool:
    if not _has_distinct_quantity_escalation(config):
        return False
    decision = result.decisions[0]
    return (
        decision.status != "match"
        or decision.confidence < config.audit.quantity.min_decision_confidence
        or _has_high_risk_exact_quantity_change(comparison)
    )


def _has_high_risk_exact_quantity_change(
    comparison: QuantityComparison | None,
) -> bool:
    """Require the stronger model for exact relation/value changes.

    Missing and added natural-language facts are commonly cross-language parser
    ambiguity.  A typed value, unit, range, polarity, comparator, or identifier
    change carries enough objective risk to justify the slower escalation even
    when the first model reports a confident match.
    """
    if comparison is None or comparison.status != "mismatch":
        return False
    exact_change_kinds = {
        QuantityMismatchKind.VALUE_CHANGE,
        QuantityMismatchKind.UNIT_CHANGE,
        QuantityMismatchKind.RANGE_CHANGE,
        QuantityMismatchKind.COMPARATOR_CHANGE,
        QuantityMismatchKind.APPROXIMATION_CHANGE,
        QuantityMismatchKind.POLARITY_CHANGE,
        QuantityMismatchKind.ATTACHMENT_CHANGE,
        QuantityMismatchKind.RELATION_CHANGE,
    }
    return any(
        mismatch.kind in exact_change_kinds
        or (
            mismatch.source_fact is not None
            and mismatch.source_fact.kind is QuantityKind.IDENTIFIER
        )
        or (
            mismatch.target_fact is not None
            and mismatch.target_fact.kind is QuantityKind.IDENTIFIER
        )
        for mismatch in comparison.mismatches
    )


def _stored_quantity_requires_escalation(existing, config: AppConfig) -> bool:
    if not existing or not _has_distinct_quantity_escalation(config):
        return False
    validation = existing.get("validation") or {}
    return bool(validation.get("requires_escalation"))


def _quantity_decision_issue(
    decision: QuantityAuditDecision,
    comparison: QuantityComparison,
    mode: str,
) -> AuditIssue | None:
    if mode == "shadow" or decision.status == "match":
        return None
    severity = AuditSeverity.HIGH if mode == "enforce" else AuditSeverity.LOW
    prefix = (
        "quantity audit found a concrete mismatch"
        if decision.status == "mismatch"
        else "quantity audit remains uncertain"
    )
    return AuditIssue(
        segment_id=decision.segment_id,
        category=AuditCategory.MISTRANSLATION,
        severity=severity,
        message=f"{prefix}: {decision.message}",
        suggested_fix=(
            "Recheck the complete quantity-bearing relation against the source, "
            "including value, unit, range, approximation, comparator, and attachment."
        ),
        source="quantity-semantic",
        source_quote=decision.source_quote,
        translation_quote=decision.translation_quote,
        risk_tags={AuditRiskTag.QUANTITY, AuditRiskTag.RELATION},
        quantity_mismatches=comparison.mismatches,
    )


def _load_current_semantic_result(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return SemanticAuditResult.model_validate_json(path.read_text(encoding="utf-8"))


def _load_current_quantity_result(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return QuantityAuditResult.model_validate_json(path.read_text(encoding="utf-8"))


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.AUDIT_TRANSLATION.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
    )


def _require_completed(connection, stage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, error) -> None:
    previous = get_stage_status(connection, WorkflowStage.AUDIT_TRANSLATION.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.AUDIT_TRANSLATION.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
