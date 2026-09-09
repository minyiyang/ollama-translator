"""Revalidate repaired drafts and publish the final review queue."""

from __future__ import annotations

from dataclasses import asdict
import re

from ..atomic_io import atomic_write_text
from ..audit import (
    AuditCategory,
    AuditIssue,
    AuditRiskTag,
    AuditSeverity,
    audit_translated_document,
    is_preference_only_audit_issue,
    reapply_quantity_adjudications,
)
from ..config import AppConfig
from ..hashing import hash_named_values, sha256_file
from ..ollama_client import OllamaClient, StructuredOutputError
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
from ..repair import (
    PairwiseRepairVerificationResult,
    RepairDisposition,
    RepairedDocument,
    RepairedDocumentValidation,
    RepairedValidationReport,
    RepairVerification,
    RepairVerificationResult,
    SegmentRepair,
    _apply_exact_glossary_replacements,
    _deduplicate_adjacent_marker_text,
    _feedback_requests_deduplication,
    apply_segment_repairs,
    build_repair_prompt,
    build_repair_verification_prompt,
    map_pairwise_repair_verification,
    repair_candidate_first,
    repair_verification_output_token_limit,
    validate_repair_output,
    validate_repair_verification_scope,
    validate_pairwise_repair_verification_scope,
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
from ..workspace import JobWorkspace
from ..translation import render_translated_document
from ..translation import (
    InlineMarkerPlacementResult,
    SingleInlineMarkerPlacementResult,
    TranslationChunk,
    TranslationChunkPiece,
    apply_inline_marker_placements,
    apply_single_inline_marker_placements,
    build_inline_marker_placement_prompt,
    build_single_inline_marker_placement_prompt,
    supports_single_inline_marker_placement,
)
from .preprocess import load_preprocessed_documents
from .audit import load_document_audits


VALIDATE_REPAIRED_STAGE_VERSION = "26"


_HIGH_RISK_SEMANTIC_RELATION = re.compile(
    r"(?:"
    r"\b(?:numeric|quantity|ratio|percentage|percent|multiplier|specific number|"
    r"specific measurement)\b"
    r"|\b(?:dozen|score|tendays?)\b"
    r"|\bspatial (?:relationship|position|state|direction)\b"
    r"|\b(?:incorrectly|wrongly) (?:identifies|renders|describes|places|converts)"
    r"[^.!?;]{0,100}\b(?:direction|above|below|inside|outside|across|towards?|within)\b"
    r"|\b(?:incorrectly|wrongly) (?:identifies|attributes|assigns) (?:the )?"
    r"(?:subject|object|agent)\b"
    r"|\b(?:subject|object|agent) of (?:the )?(?:action|verb|clause|trade)\b"
    r"|\battributes? (?:the )?(?:action|speech|thought|movement)"
    r"[^.!?;]{0,80}\bto\b"
    r"|\b(?:negation|negative polarity|semantic reversal|meaning reversal)\b"
    r"|\brevers(?:al|es|ed|ing) (?:the )?(?:meaning|direction|relationship|"
    r"subject|object|agent)\b"
    r")",
    flags=re.IGNORECASE,
)


def run_repaired_validation_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> RepairedValidationReport:
    """Re-audit repaired drafts and semantically verify repaired semantic findings."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        repair_stage = _require_completed(connection, WorkflowStage.REPAIR_REVIEW)
        input_hash = build_stage_input_hash(
            {
                "repair_review": str(repair_stage["output_hash"]),
                "audit": config.audit.model_dump_json(),
                "model": config.audit.verifier_model or config.audit.model,
                "stage_version": VALIDATE_REPAIRED_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.VALIDATE_REPAIRED,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_repaired_validation_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.VALIDATE_REPAIRED)
            previous = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        retire_stage_artifacts(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.VALIDATE_REPAIRED.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
        from .reprose import load_post_repair_documents
        from .repair_review import load_review_repaired_documents
        from .review_repaired import load_repaired_review_results

        repaired_documents = load_review_repaired_documents(workspace)
        initial_audits = {
            item.document_id: item for item in load_document_audits(workspace)
        }
        initial_documents = {
            item.document.manifest_id: item
            for item in load_post_repair_documents(workspace, config)
        }
        initial_reviews = load_repaired_review_results(workspace)
        final_review_targets: dict[str, set[str]] = {}
        manual_semantic_targets: dict[str, dict[str, RepairVerification]] = {}
        for document_id, initial in initial_documents.items():
            review = initial_reviews.get(document_id)
            review_decisions = {
                item.segment_id: item
                for item in (review.verifications if review else [])
            }
            manual_decisions = {
                repair.segment_id: review_decisions[repair.segment_id]
                for repair in initial.repairs
                if repair.segment_id in review_decisions
                and _requires_human_high_risk_change(
                    repair,
                    review_decisions[repair.segment_id],
                    config,
                )
            }
            manual_semantic_targets[document_id] = manual_decisions
            target_ids = {
                item.segment_id
                for item in (review.verifications if review else [])
                if not item.passed
                and not item.message.startswith(
                    "Deterministic validation failed before semantic review:"
                )
            }
            target_ids.update(
                repair.segment_id
                for repair in initial.repairs
                if repair.disposition is RepairDisposition.REVIEW
                and any(
                    issue.source in {"semantic", "reprose"}
                    or (
                        config.audit.quantity.verify_repairs
                        and AuditRiskTag.QUANTITY in issue.risk_tags
                    )
                    for issue in repair.issues
                )
            )
            target_ids.update(manual_decisions)
            final_review_targets[document_id] = target_ids

        deterministic_preflight = {}
        for item in repaired_documents:
            document_id = item.document.manifest_id
            deterministic_preflight[document_id] = reapply_quantity_adjudications(
                audit_translated_document(
                    sources[document_id], item.document, config.audit
                ),
                initial_audits.get(document_id),
            )
        final_semantic_ids: dict[str, list[str]] = {}
        for item in repaired_documents:
            document_id = item.document.manifest_id
            target_ids = final_review_targets.get(document_id, set())
            quantity_verifiable_ids = _quantity_verifiable_repair_ids(
                item, target_ids, config
            )
            blocked_ids = {
                issue.segment_id
                for issue in deterministic_preflight[document_id].issues
                if issue.severity.rank >= AuditSeverity.MEDIUM.rank
                and not (
                    issue.source == "quantity-deterministic"
                    and issue.segment_id in quantity_verifiable_ids
                )
            }
            final_semantic_ids[document_id] = [
                repair.segment_id
                for repair in item.repairs
                if repair.segment_id in target_ids
                and repair.segment_id
                not in manual_semantic_targets.get(document_id, {})
                and repair.segment_id not in blocked_ids
                and repair.disposition is RepairDisposition.REPAIRED
            ]
        semantic_needed = any(final_semantic_ids.values())
        if semantic_needed and client is None:
            raise ValueError("an Ollama client is required to verify semantic repairs")

        stage_root = workspace.directory(f"repaired/{input_hash[:16]}-validation")
        stage_root.mkdir(parents=True, exist_ok=True)
        validation_plans = []
        verification_tasks = []
        verification_results = {}
        segment_contexts: dict[str, int] = {}
        for repaired in repaired_documents:
            source = sources[repaired.document.manifest_id]
            current = repaired
            target_ids = final_review_targets.get(source.manifest_id, set())
            deterministic_before = deterministic_preflight[source.manifest_id]
            quantity_verifiable_ids = _quantity_verifiable_repair_ids(
                current, target_ids, config
            )
            deterministic_blocked_ids = {
                issue.segment_id
                for issue in deterministic_before.issues
                if issue.segment_id in target_ids
                and issue.severity.rank >= AuditSeverity.MEDIUM.rank
                and not (
                    issue.source == "quantity-deterministic"
                    and issue.segment_id in quantity_verifiable_ids
                )
            }
            if deterministic_blocked_ids:
                current = _reject_repairs(
                    current,
                    deterministic_blocked_ids,
                    "final deterministic validation failed; original retained",
                )
            semantic_ids = final_semantic_ids[source.manifest_id]
            task = None
            if semantic_ids:
                candidate_first = repair_candidate_first(semantic_ids)
                prompt = build_repair_verification_prompt(
                    source,
                    current,
                    semantic_ids,
                    candidate_first=candidate_first,
                )
                unit_id = f"verify-final-{source.order:04d}-{source.manifest_id}"
                unit_input_hash = hash_named_values(
                    {
                        "stage": input_hash,
                        "prompt": prompt,
                        "ids": "\n".join(semantic_ids),
                    }
                )
                path = stage_root / f"{unit_id}.json"
                existing = get_work_unit(
                    connection, unit_id, WorkflowStage.VALIDATE_REPAIRED.value
                )
                result = _load_current_verification(existing, path, unit_input_hash)
                verifier_context_maximum = (
                    config.audit.verifier_max_num_ctx or config.audit.max_num_ctx
                )
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
                for segment_id in semantic_ids:
                    segment_contexts[segment_id] = bucket
                task = {
                    "source": source,
                    "semantic_ids": semantic_ids,
                    "prompt": prompt,
                    "unit_id": unit_id,
                    "unit_input_hash": unit_input_hash,
                    "path": path,
                    "existing": existing,
                    "bucket": bucket,
                    "candidate_first": candidate_first,
                }
                if result is None:
                    verification_tasks.append(task)
                else:
                    verification_results[source.manifest_id] = result
                    _record_file(
                        connection, workspace, path, "repair_final_verification"
                    )
            validation_plans.append(
                (
                    repaired,
                    source,
                    current,
                    target_ids,
                    deterministic_before,
                    deterministic_blocked_ids,
                    semantic_ids,
                    task,
                )
            )

        report_stage_plan(
            client,
            model=config.audit.verifier_model or config.audit.model,
            stage=WorkflowStage.VALIDATE_REPAIRED.value,
            prescreened=sum(len(source.segments) for source in sources.values()),
            llm_tasks=len(verification_tasks),
            skipped=(
                sum(len(source.segments) for source in sources.values())
                - sum(len(task["semantic_ids"]) for task in verification_tasks)
            ),
            context_buckets=(task["bucket"] for task in verification_tasks),
            role="validate_repaired.final_verification",
        )
        ordered_tasks = group_by_context_bucket(
            verification_tasks, lambda task: int(task["bucket"])
        )
        for verification_index, task in enumerate(ordered_tasks, start=1):
            source = task["source"]
            result = _run_verification(
                connection,
                task["unit_id"],
                source.manifest_id,
                task["unit_input_hash"],
                task["path"],
                task["prompt"],
                task["semantic_ids"],
                config,
                client,
                existing_attempts=(
                    int(task["existing"]["attempts"]) if task["existing"] else 0
                ),
                verification_index=verification_index,
                total_verifications=len(ordered_tasks),
                context_bucket=int(task["bucket"]),
                candidate_first=bool(task["candidate_first"]),
            )
            verification_results[source.manifest_id] = result
            _record_file(
                connection, workspace, task["path"], "repair_final_verification"
            )

        validations: list[RepairedDocumentValidation] = []
        all_review_ids: set[str] = set()
        all_defect_ids: set[str] = set()
        all_approval_ids: set[str] = set()
        segment_result_index = 0
        segment_result_total = sum(
            len(target_ids)
            for (
                _repaired,
                _source,
                _current,
                target_ids,
                _deterministic_before,
                _deterministic_blocked_ids,
                _semantic_ids,
                _task,
            ) in validation_plans
        )
        for (
            repaired, source, current, target_ids, deterministic_before,
            deterministic_blocked_ids, semantic_ids, task,
        ) in validation_plans:
            result = verification_results.get(
                source.manifest_id, RepairVerificationResult(verifications=[])
            )
            semantic_verifications = result.verifications
            repairs_by_id = {
                repair.segment_id: repair for repair in current.repairs
            }
            quantity_accepted_ids = {
                decision.segment_id
                for decision in [
                    *semantic_verifications,
                    *manual_semantic_targets.get(source.manifest_id, {}).values(),
                ]
                if (decision.passed or decision.current_acceptable)
                and _repair_has_quantity_trigger(
                    repairs_by_id.get(decision.segment_id)
                )
            }
            manual_verifications = [
                decision.model_copy(
                    update={
                        "passed": False,
                        "message": (
                            "High-risk semantic change requires explicit human review; "
                            "the independent model decision remains advisory."
                        ),
                    }
                )
                for decision in manual_semantic_targets.get(
                    source.manifest_id, {}
                ).values()
            ]
            final_manual_ids = {
                decision.segment_id
                for decision in semantic_verifications
                if decision.segment_id in repairs_by_id
                and _requires_human_high_risk_change(
                    repairs_by_id[decision.segment_id],
                    decision,
                    config,
                )
            }
            if final_manual_ids:
                manual_verifications.extend(
                    decision.model_copy(
                        update={
                            "passed": False,
                            "message": (
                                "High-risk semantic change requires explicit human "
                                "review; the independent model decision remains advisory."
                            ),
                        }
                    )
                    for decision in semantic_verifications
                    if decision.segment_id in final_manual_ids
                )
                semantic_verifications = [
                    decision
                    for decision in semantic_verifications
                    if decision.segment_id not in final_manual_ids
                ]

            accepted_current_ids = {
                item.segment_id
                for item in semantic_verifications
                if not item.passed
                and item.current_acceptable
            }
            if accepted_current_ids:
                current = _retain_accepted_originals(current, accepted_current_ids)
                semantic_verifications = [
                    item.model_copy(
                        update={
                            "passed": True,
                            "message": (
                                "Final independent review rejected the candidate and confirmed "
                                "the last accepted translation; the prior diagnosis was not "
                                "reproduced."
                            ),
                        }
                    )
                    if item.segment_id in accepted_current_ids
                    else item
                    for item in semantic_verifications
                ]

            failed_final_ids = {
                item.segment_id for item in semantic_verifications if not item.passed
            }
            if failed_final_ids:
                current = _reject_repairs(
                    current,
                    failed_final_ids,
                    "final independent verification failed; original retained",
                )

            deterministic = reapply_quantity_adjudications(
                audit_translated_document(source, current.document, config.audit),
                initial_audits.get(source.manifest_id),
            )
            deterministic = _accept_verified_quantity_repairs(
                deterministic, quantity_accepted_ids
            )
            current = _accept_unreproduced_deterministic_reviews(
                current, deterministic
            )

            defect_ids = {
                item.segment_id
                for item in current.repairs
                if item.disposition is RepairDisposition.REVIEW
            }
            defect_ids.update(
                issue.segment_id
                for issue in deterministic.issues
                if issue.severity.rank >= AuditSeverity.MEDIUM.rank
            )
            defect_ids.update(
                item.segment_id for item in semantic_verifications if not item.passed
            )
            approval_ids = set(
                manual_semantic_targets.get(source.manifest_id, {})
            )
            approval_ids.update(final_manual_ids)
            # A segment with a concrete remaining defect belongs in the defect
            # queue even if the proposed high-risk change also needs approval.
            approval_ids.difference_update(defect_ids)
            review_ids = defect_ids | approval_ids
            validation = RepairedDocumentValidation(
                document_id=source.manifest_id,
                deterministic_audit=deterministic,
                semantic_verifications=[
                    *semantic_verifications,
                    *manual_verifications,
                ],
                passed=not review_ids,
                review_segment_ids=sorted(review_ids),
                defect_segment_ids=sorted(defect_ids),
                approval_segment_ids=sorted(approval_ids),
            )
            path = stage_root / f"{source.order:04d}-{source.manifest_id}.validation.json"
            atomic_write_text(path, validation.model_dump_json(indent=2))
            _record_file(connection, workspace, path, "repaired_document_validation")
            final_json_path = stage_root / f"{source.order:04d}-{source.manifest_id}.validated.json"
            final_text_path = stage_root / f"{source.order:04d}-{source.manifest_id}.validated.txt"
            atomic_write_text(final_json_path, current.model_dump_json(indent=2))
            atomic_write_text(final_text_path, render_translated_document(current.document))
            _record_file(connection, workspace, final_json_path, "validated_repaired_document_json")
            _record_file(connection, workspace, final_text_path, "validated_repaired_document_text")
            validations.append(validation)
            all_review_ids.update(review_ids)
            all_defect_ids.update(defect_ids)
            all_approval_ids.update(approval_ids)
            decisions = {
                item.segment_id: item
                for item in [*semantic_verifications, *manual_verifications]
            }
            for segment_id in sorted(target_ids):
                segment_result_index += 1
                decision = decisions.get(segment_id)
                if segment_id in deterministic_blocked_ids:
                    outcome, mode, count = "failed", "deterministic-prescreen", 1
                elif decision is not None:
                    outcome, mode, count = (
                        "passed" if decision.passed else "failed",
                        "semantic-validation",
                        0 if decision.passed else 1,
                    )
                elif segment_id in review_ids:
                    outcome, mode, count = "pending", "unresolved", 1
                else:
                    outcome, mode, count = "skipped", "not-required", 0
                report_segment_result(
                    client,
                    model=config.audit.verifier_model or config.audit.model,
                    stage=WorkflowStage.VALIDATE_REPAIRED.value,
                    segment_id=segment_id,
                    result=outcome,
                    mode=mode,
                    issue_count=count,
                    context_bucket=segment_contexts.get(segment_id, 0),
                    result_index=segment_result_index,
                    result_total=segment_result_total,
                    role=(
                        "validate_repaired.final_verification"
                        if decision is not None
                        else "validate_repaired.deterministic"
                    ),
                )

        report = RepairedValidationReport(
            document_count=len(validations),
            segment_count=sum(
                item.deterministic_audit.segment_count for item in validations
            ),
            passed=not all_review_ids,
            remaining_issue_count=len(all_review_ids),
            review_segment_ids=sorted(all_review_ids),
            remaining_defect_count=len(all_defect_ids),
            approval_required_count=len(all_approval_ids),
            defect_segment_ids=sorted(all_defect_ids),
            approval_segment_ids=sorted(all_approval_ids),
        )
        report_path = stage_root / "repaired-validation.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "repaired_validation_report")
        set_job_metadata(
            connection,
            "repaired_validation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "validated_repaired_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.VALIDATE_REPAIRED)
        set_stage_status(
            connection,
            WorkflowStage.VALIDATE_REPAIRED.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
            message=(f"{len(all_review_ids)} segment(s) require human review" if all_review_ids else ""),
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def load_repaired_document_validations(
    workspace: JobWorkspace,
) -> list[RepairedDocumentValidation]:
    """Load final repaired-document validations in document order."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.VALIDATE_REPAIRED.value,
            root_metadata_key="validated_repaired_root",
            report_metadata_key="repaired_validation_report",
        )
        items = []
        for artifact in artifacts:
            if artifact["kind"] != "repaired_document_validation":
                continue
            path = workspace.directory(str(artifact["path"]))
            items.append(
                RepairedDocumentValidation.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            )
        order = {
            item.manifest_id: item.order for item in load_preprocessed_documents(workspace)
        }
        return sorted(items, key=lambda item: order[item.document_id])
    finally:
        connection.close()


def load_validated_repaired_documents(workspace: JobWorkspace) -> list[RepairedDocument]:
    """Load the revalidated drafts selected for EPUB compilation."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.VALIDATE_REPAIRED.value,
            root_metadata_key="validated_repaired_root",
            report_metadata_key="repaired_validation_report",
        )
        items = []
        for artifact in artifacts:
            if artifact["kind"] != "validated_repaired_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            items.append(
                RepairedDocument.model_validate_json(path.read_text(encoding="utf-8"))
            )
        if not items:
            raise FileNotFoundError("validated repaired documents are not recorded")
        items = require_unique_items(
            items,
            lambda item: item.document.manifest_id,
            label="validated repaired document",
        )
        report = load_repaired_validation_report(workspace, connection=connection)
        items = require_document_totals(
            items,
            expected_documents=report.document_count,
            expected_segments=report.segment_count,
            segment_count=lambda item: len(item.document.segments),
            label="validated repaired document",
        )
        return sorted(items, key=lambda item: item.document.order)
    finally:
        connection.close()


def load_repaired_validation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> RepairedValidationReport:
    """Load the final repaired-draft validation report."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "repaired_validation_report")
        if not relative:
            raise FileNotFoundError("repaired validation report is not recorded")
        path = workspace.directory(relative)
        return RepairedValidationReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def _replace_document_repair(
    repaired: RepairedDocument,
    replacement: SegmentRepair,
) -> RepairedDocument:
    repairs = [
        replacement if item.segment_id == replacement.segment_id else item
        for item in repaired.repairs
    ]
    if replacement.segment_id not in {item.segment_id for item in repaired.repairs}:
        repairs.append(replacement)
    document = apply_segment_repairs(repaired.document, [replacement])
    return RepairedDocument(document=document, repairs=repairs)


def _retain_accepted_originals(
    repaired: RepairedDocument,
    segment_ids: set[str],
) -> RepairedDocument:
    """Restore immutable accepted text when an independent diagnosis is not reproduced."""
    originals = {
        item.segment_id: item.original_translation
        for item in repaired.repairs
        if item.segment_id in segment_ids
    }
    return repaired.model_copy(
        update={
            "document": repaired.document.model_copy(
                update={
                    "segments": [
                        item.model_copy(
                            update={
                                "translated_text": originals.get(
                                    item.segment_id, item.translated_text
                                )
                            }
                        )
                        for item in repaired.document.segments
                    ]
                }
            ),
            "repairs": [
                item.model_copy(
                    update={
                        "disposition": RepairDisposition.ACCEPTED,
                        "repaired_translation": "",
                        "message": "accepted unchanged: prior diagnosis not reproduced",
                    }
                )
                if item.segment_id in segment_ids
                else item
                for item in repaired.repairs
            ],
        }
    )


def _accept_unreproduced_deterministic_reviews(
    repaired: RepairedDocument,
    deterministic: "DocumentAudit",
) -> RepairedDocument:
    """Retire review state whose deterministic diagnosis no longer reproduces.

    Repair-review can wrap a deterministic rejection as a semantic issue. Such a
    wrapper is not independent semantic evidence and must not make review state
    permanent after the underlying validator or glossary policy is corrected.
    """
    active_ids = {
        issue.segment_id
        for issue in deterministic.issues
        if issue.severity.rank >= AuditSeverity.MEDIUM.rank
    }
    resolved_ids = {
        repair.segment_id
        for repair in repaired.repairs
        if repair.disposition is RepairDisposition.REVIEW
        and repair.segment_id not in active_ids
        and repair.issues
        and any(_is_deterministic_derivative(issue) for issue in repair.issues)
        and not any(
            issue.source == "semantic"
            and issue.severity is AuditSeverity.HIGH
            and not _is_deterministic_derivative(issue)
            for issue in repair.issues
        )
    }
    if not resolved_ids:
        return repaired
    return _retain_accepted_originals(repaired, resolved_ids)


def _is_deterministic_derivative(issue: AuditIssue) -> bool:
    """Recognize deterministic evidence and semantic wrappers around it."""
    if issue.source == "deterministic" or issue.source.endswith("-deterministic"):
        return True
    normalized = " ".join(issue.message.casefold().split())
    return (
        issue.source == "semantic"
        and "deterministic validation failed before semantic review:" in normalized
    )


def _reject_repairs(
    repaired: RepairedDocument,
    segment_ids: set[str],
    message: str,
) -> RepairedDocument:
    """Restore last-known-good text and make rejected candidates review-only."""
    originals = {
        item.segment_id: item.original_translation
        for item in repaired.repairs
        if item.segment_id in segment_ids
    }
    return repaired.model_copy(
        update={
            "document": repaired.document.model_copy(
                update={
                    "segments": [
                        item.model_copy(
                            update={
                                "translated_text": originals.get(
                                    item.segment_id, item.translated_text
                                )
                            }
                        )
                        for item in repaired.document.segments
                    ]
                }
            ),
            "repairs": [
                item.model_copy(
                    update={
                        "disposition": RepairDisposition.REVIEW,
                        "repaired_translation": "",
                        "message": message,
                    }
                )
                if item.segment_id in segment_ids
                else item
                for item in repaired.repairs
            ],
        }
    )


def _requires_human_high_risk_change(
    repair: SegmentRepair,
    decision: RepairVerification,
    config: AppConfig,
) -> bool:
    """Keep only concrete high-risk relation changes advisory for a human."""
    if config.audit.semantic_verification_policy != "human-high-risk":
        return False
    if not decision.passed or decision.current_acceptable:
        return False
    structured_high_risk = {
        AuditRiskTag.QUANTITY,
        AuditRiskTag.SPATIAL,
        AuditRiskTag.POLARITY,
        AuditRiskTag.ATTACHMENT,
        AuditRiskTag.RELATION,
    }
    if any(
        issue.severity is AuditSeverity.HIGH
        and bool(issue.risk_tags & structured_high_risk)
        for issue in repair.issues
    ):
        return True
    semantic_issues = [issue for issue in repair.issues if issue.source == "semantic"]
    for issue in semantic_issues:
        if issue.severity is not AuditSeverity.HIGH:
            continue
        if issue.category is AuditCategory.OMISSION:
            return True
        if issue.category is AuditCategory.ADDITION and re.search(
            r"\b(?:unsupported|invented|not present|absent from the source)\b",
            issue.message,
            flags=re.IGNORECASE,
        ):
            return True
        if (
            issue.category is AuditCategory.MISTRANSLATION
            and _HIGH_RISK_SEMANTIC_RELATION.search(issue.message)
        ):
            return True
    return False


def _repair_has_quantity_trigger(repair: SegmentRepair | None) -> bool:
    return bool(
        repair
        and any(AuditRiskTag.QUANTITY in issue.risk_tags for issue in repair.issues)
    )


def _quantity_verifiable_repair_ids(
    repaired: RepairedDocument,
    target_ids: set[str],
    config: AppConfig,
) -> set[str]:
    """Let the configured independent verifier judge changed quantity repairs."""
    if not config.audit.quantity.verify_repairs:
        return set()
    return {
        repair.segment_id
        for repair in repaired.repairs
        if repair.segment_id in target_ids
        and repair.disposition is RepairDisposition.REPAIRED
        and _repair_has_quantity_trigger(repair)
    }


def _accept_verified_quantity_repairs(
    audit,
    accepted_ids: set[str],
):
    """Replace raw parser candidates accepted by independent semantic review."""
    if not accepted_ids:
        return audit
    comparisons = [
        comparison.model_copy(
            update={
                "status": "match",
                "reason": "Independent repair verification accepted the quantity relation.",
                "adjudicated": True,
            }
        )
        if comparison.segment_id in accepted_ids
        else comparison
        for comparison in audit.quantity_comparisons
    ]
    issues = [
        issue
        for issue in audit.issues
        if not (
            issue.source == "quantity-deterministic"
            and issue.segment_id in accepted_ids
        )
    ]
    return audit.model_copy(
        update={
            "quantity_comparisons": comparisons,
            "issues": issues,
            "passed": not any(
                issue.severity.rank >= AuditSeverity.MEDIUM.rank
                for issue in issues
            ),
        }
    )


def _build_feedback_repair_prompt(
    source,
    repaired,
    previous_repair,
    issues,
    feedback,
    config,
):
    """Build the immutable per-segment feedback prompt used for pre-scheduling."""
    prompt = build_repair_prompt(
        source, repaired.document, previous_repair.segment_id, issues, config
    )
    return prompt + (
        "\n\nIndependent validation rejected the previous repair. The localized edit was "
        "insufficient. Retranslate the entire target segment from SOURCE when necessary; "
        "do not preserve rejected wording merely because it appears in CURRENT or in an "
        "earlier suggested fix. Preserve meaning, surrounding voice, and every marker. "
        "Inline markers encode semantic emphasis, not mandatory word-for-word alignment. "
        "If literal marker placement would make target-language grammar unnatural, "
        "restructure the complete clause and mark the natural expression carrying the same "
        "emphasis. Read the complete marked sentence for grammatical fluency before "
        "returning it. "
        f"The remaining defect that must be eliminated is: {feedback}"
        + _feedback_repair_examples(config.translation.direction)
    )


def _run_feedback_repair(
    connection,
    workspace,
    stage_root,
    source,
    repaired,
    previous_repair,
    issues,
    feedback,
    config,
    client,
    *,
    mode,
    feedback_index,
    total_feedback,
    document_index,
    total_documents,
    stage_input_hash,
    model=None,
    thinking=None,
    workflow_stage=WorkflowStage.VALIDATE_REPAIRED,
    prepared_prompt=None,
    context_bucket=None,
    usage_role="",
) -> SegmentRepair:
    segment_id = previous_repair.segment_id
    if not usage_role:
        usage_role = (
            "repair_review.feedback"
            if workflow_stage is WorkflowStage.REPAIR_REVIEW
            else "validate_repaired.feedback"
        )
    unit_id = f"feedback-{mode}-{segment_id}"
    prompt = prepared_prompt or _build_feedback_repair_prompt(
        source, repaired, previous_repair, issues, feedback, config
    )
    input_hash = hash_named_values(
        {"stage": stage_input_hash, "prompt": prompt, "segment": segment_id}
    )
    feedback_root = stage_root / "feedback"
    feedback_root.mkdir(parents=True, exist_ok=True)
    path = feedback_root / f"{unit_id}.json"
    existing = get_work_unit(
        connection, unit_id, workflow_stage.value
    )
    cached = _load_current_feedback(existing, path, input_hash)
    if cached is not None:
        return cached

    source_text = next(
        item.processed_text for item in source.segments
        if item.segment_id == segment_id
    )
    original_translation = next(
        item.translated_text for item in repaired.document.segments
        if item.segment_id == segment_id
    )
    if _is_nonblocking_preference(issues):
        result = SegmentRepair(
            segment_id=segment_id,
            disposition=RepairDisposition.ACCEPTED,
            original_translation=original_translation,
            repaired_translation="",
            issues=issues,
            attempts=0,
            message="accepted unchanged: preference-only finding",
        )
        atomic_write_text(path, result.model_dump_json(indent=2))
        set_work_unit_status(
            connection, unit_id, workflow_stage.value, "feedback_repair",
            StageStatus.COMPLETED, parent_id=source.manifest_id, attempts=0,
            input_hash=input_hash, output_hash=sha256_file(path),
            validation={"passed": True, "disposition": result.disposition.value},
            message=result.message,
        )
        record_validation(
            connection, unit_id, "preference_only_finding", True,
            details={"accepted_unchanged": True},
        )
        _record_file(
            connection, workspace, path, "repair_feedback_result",
            workflow_stage=workflow_stage,
        )
        return result

    deterministic_glossary = _apply_exact_glossary_replacements(
        original_translation, issues
    )
    allowed_attempts = min(
        config.workflow.max_retries + 1, config.audit.repair_max_attempts
    )
    last_message = ""
    failed_outputs: set[str] = set()
    attempts_used = 0
    for offset in range(1, allowed_attempts + 1):
        attempts_used = offset
        generation = None
        corrected_text = ""
        validation = None
        if offset == 1 and deterministic_glossary != original_translation:
            corrected_text, validation = validate_repair_output(
                f"<{segment_id}>{deterministic_glossary}</{segment_id}>",
                source_text,
                segment_id,
                original_translation,
                config,
                source.relevant_glossary,
                trigger_issues=issues,
            )
        dedup_diagnosis = " ".join([feedback, *(item.message for item in issues)])
        if (
            (validation is None or not validation.passed)
            and offset == 1
            and _feedback_requests_deduplication(dedup_diagnosis)
        ):
            deterministic = _deduplicate_adjacent_marker_text(original_translation)
            if deterministic != original_translation:
                corrected_text, validation = validate_repair_output(
                    f"<{segment_id}>{deterministic}</{segment_id}>",
                    source_text,
                    segment_id,
                    original_translation,
                    config,
                    source.relevant_glossary,
                    trigger_issues=issues,
                )
        if validation is None or not validation.passed:
            request_prompt = prompt
            if last_message:
                request_prompt += (
                    "\n\nThe previous feedback repair failed the structural contract. "
                    f"Return a different complete correction. Validation errors: {last_message}"
                )
            generation = client.generate_text(
                request_prompt,
                stream=True,
                think=(config.translation.thinking if thinking is None else thinking),
                model=model,
                progress_label=(
                    f"feedback-repair={feedback_index}/{total_feedback} mode={mode} "
                    f"id={segment_id} document={document_index}/{total_documents} "
                    f"attempt={offset}/{allowed_attempts}"
                ),
                **llm_role_kwargs(client, usage_role),
                context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
                context_maximum=(context_bucket or config.audit.max_num_ctx),
            )
            corrected_text, validation = validate_repair_output(
                generation.content,
                source_text,
                segment_id,
                original_translation,
                config,
                source.relevant_glossary,
                trigger_issues=issues,
            )
        if (
            not validation.passed
            and corrected_text
            and callable(getattr(client, "generate_structured", None))
            and all(
                item.code == "protected_marker_mismatch"
                for item in validation.issues
            )
        ):
            try:
                corrected_text, validation = _repair_feedback_markers(
                    client,
                    source_text,
                    segment_id,
                    original_translation,
                    corrected_text,
                    config,
                    model=model,
                    thinking=(config.translation.thinking if thinking is None else thinking),
                    progress_label=(
                        f"feedback-repair={feedback_index}/{total_feedback} mode={mode} "
                        f"id={segment_id} marker-placement=1/1"
                    ),
                    context_bucket=context_bucket,
                    relevant_glossary=source.relevant_glossary,
                    trigger_issues=issues,
                    usage_role=f"{usage_role}.marker_placement",
                )
            except Exception:
                pass
        record_validation(
            connection,
            unit_id,
            "feedback_repair_contract",
            validation.passed,
            details=validation.model_dump(mode="json"),
        )
        if validation.passed:
            result = SegmentRepair(
                segment_id=segment_id,
                disposition=RepairDisposition.REPAIRED,
                original_translation=original_translation,
                repaired_translation=corrected_text,
                issues=issues,
                attempts=offset,
            )
            atomic_write_text(path, result.model_dump_json(indent=2))
            set_work_unit_status(
                connection,
                unit_id,
                workflow_stage.value,
                "feedback_repair",
                StageStatus.COMPLETED,
                parent_id=source.manifest_id,
                attempts=offset,
                input_hash=input_hash,
                output_hash=sha256_file(path),
                validation={"passed": True, "disposition": result.disposition.value},
            )
            record_attempt(
                connection,
                unit_id,
                workflow_stage.value,
                offset,
                StageStatus.COMPLETED,
                metrics=(
                    asdict(generation.metrics)
                    if generation is not None
                    else {"deterministic_repair": True}
                ),
            )
            _record_file(
                connection, workspace, path, "repair_feedback_result",
                workflow_stage=workflow_stage,
            )
            return result
        last_message = "; ".join(item.message for item in validation.issues)
        record_attempt(
            connection,
            unit_id,
            workflow_stage.value,
            offset,
            StageStatus.FAILED,
            message=last_message,
            metrics=(
                asdict(generation.metrics)
                if generation is not None
                else {"deterministic_repair": True}
            ),
        )
        normalized_failed = corrected_text.strip()
        if normalized_failed:
            if normalized_failed in failed_outputs:
                last_message += "; repeated identical invalid output; switching strategy"
                break
            failed_outputs.add(normalized_failed)

    result = SegmentRepair(
        segment_id=segment_id,
        disposition=RepairDisposition.REVIEW,
        original_translation=original_translation,
        repaired_translation="",
        issues=issues,
        attempts=attempts_used,
        message=last_message,
    )
    atomic_write_text(path, result.model_dump_json(indent=2))
    set_work_unit_status(
        connection,
        unit_id,
        workflow_stage.value,
        "feedback_repair",
        StageStatus.COMPLETED,
        parent_id=source.manifest_id,
        attempts=attempts_used,
        input_hash=input_hash,
        output_hash=sha256_file(path),
        validation={"passed": False, "disposition": result.disposition.value},
        message=last_message,
    )
    _record_file(
        connection, workspace, path, "repair_feedback_result",
        workflow_stage=workflow_stage,
    )
    return result


def _feedback_repair_examples(direction: str) -> str:
    """Return compact examples for repair after an independent verifier rejects a draft."""
    if direction == "zh-en":
        return (
            "\n\nFeedback-repair examples (copy the repair pattern, never the wording):\n"
            "1. Duplicate marker word: SOURCE `当<I000>我</I000>发现`, broken "
            "`when I <I000>I</I000> discover`, corrected `when <I000>I</I000> discover`.\n"
            "2. Missing predicate: SOURCE `它长着<I000>很</I000>长的爪子`, broken "
            "`it <I000>very</I000> long claws`, corrected "
            "`it has <I000>very</I000> long claws`.\n"
            "3. Unnatural literal wording: if the verifier identifies an idiom, wordplay, "
            "or elliptical question, rewrite the complete clause as natural English while "
            "preserving its intent; do not patch only the rejected word."
        )
    return (
        "\n\nFeedback-repair examples (copy the repair pattern, never the wording):\n"
        "1. Duplicate marker word: SOURCE `when <I000>I</I000> discover`, broken "
        "`当我<I000>我</I000>发现`, corrected `当<I000>我</I000>发现`.\n"
        "2. Missing predicate: SOURCE `it has <I000>very</I000> long claws`, broken "
        "`它<I000>非常</I000>长的爪子`, corrected "
        "`它长着<I000>非常</I000>长的爪子`.\n"
        "3. Unnatural literal wording: SOURCE `the sound of the <I000>what?</I000>`, "
        "broken `什么的声音？`, corrected `你说的是<I000>什么</I000>的声音？`. "
        "Rewrite the complete clause when a word-for-word patch cannot sound natural.\n"
        "4. Possessive inside an idiom: SOURCE `do not lose <I000>your</I000> temper`, "
        "broken `不要发<I000>你</I000>的脾气`, corrected "
        "`别把<I000>你的</I000>脾气发出来`. Restructure the idiom around the marker."
    )


def _is_nonblocking_preference(issues: list[AuditIssue]) -> bool:
    """Recognize findings that explicitly concede correctness and state a preference."""
    return bool(issues) and all(is_preference_only_audit_issue(item) for item in issues)


def _load_current_feedback(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return SegmentRepair.model_validate_json(path.read_text(encoding="utf-8"))


def _repair_feedback_markers(
    client,
    source_text,
    segment_id,
    original_translation,
    marker_free_translation,
    config,
    *,
    model,
    thinking,
    progress_label,
    context_bucket=None,
    relevant_glossary=None,
    trigger_issues=None,
    usage_role="validate_repaired.feedback.marker_placement",
):
    marker_free_translation = re.sub(r"</?I\d+>", "", marker_free_translation)
    chunk = TranslationChunk(
        chunk_id=f"feedback-marker-{segment_id}",
        document_id="feedback",
        document_order=0,
        pieces=[
            TranslationChunkPiece(
                reference_id=segment_id,
                segment_id=segment_id,
                part_number=1,
                source_text=source_text,
            )
        ],
        estimated_source_tokens=0,
    )
    translations = {segment_id: marker_free_translation}
    single = supports_single_inline_marker_placement(chunk)
    schema = (
        SingleInlineMarkerPlacementResult if single else InlineMarkerPlacementResult
    )
    prompt = (
        build_single_inline_marker_placement_prompt(chunk, translations)
        if single
        else build_inline_marker_placement_prompt(chunk, translations)
    )
    placement = client.generate_structured(
        prompt,
        schema,
        think=thinking,
        model=model,
        progress_label=progress_label,
        **llm_role_kwargs(client, usage_role),
        context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
        context_maximum=(context_bucket or config.audit.max_num_ctx),
        max_attempts=1,
    )
    repaired = (
        apply_single_inline_marker_placements(chunk, translations, placement.value)
        if single
        else apply_inline_marker_placements(chunk, translations, placement.value)
    )[segment_id]
    return validate_repair_output(
        f"<{segment_id}>{repaired}</{segment_id}>",
        source_text,
        segment_id,
        original_translation,
        config,
        relevant_glossary,
        trigger_issues=trigger_issues,
    )


def _run_verification(
    connection,
    unit_id,
    document_id,
    input_hash,
    path,
    prompt,
    expected_ids,
    config,
    client,
    *,
    existing_attempts,
    verification_index,
    total_verifications,
    context_bucket=None,
    candidate_first=False,
):
    allowed_attempts = min(
        config.workflow.max_retries + 1, config.audit.repair_max_attempts
    )
    last_error = ""
    for offset in range(1, allowed_attempts + 1):
        attempt = existing_attempts + offset
        generated = None
        request_prompt = prompt
        if last_error:
            request_prompt += (
                "\n\nThe previous verification was rejected as invalid. Return every required "
                "decision using the exact Allowed IDs. Keep every message to one "
                "sentence of at most 40 words and close all JSON arrays and objects."
            )
        try:
            generated = client.generate_structured(
                request_prompt,
                PairwiseRepairVerificationResult,
                model=config.audit.verifier_model or config.audit.model,
                think=config.audit.thinking,
                progress_label=(
                    f"verification={verification_index}/{total_verifications} "
                    f"id={unit_id} attempt={offset}/{allowed_attempts}"
                ),
                **llm_role_kwargs(client, "validate_repaired.final_verification"),
                context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
                context_maximum=(context_bucket or config.audit.max_num_ctx),
                max_output_tokens=_verification_output_token_limit(len(expected_ids)),
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
        except (StructuredOutputError, ValueError) as error:
            last_error = str(error)
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.VALIDATE_REPAIRED.value,
                attempt,
                StageStatus.FAILED,
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
            connection,
            unit_id,
            WorkflowStage.VALIDATE_REPAIRED.value,
            "repair_verification",
            StageStatus.COMPLETED,
            parent_id=document_id,
            attempts=attempt,
            input_hash=input_hash,
            output_hash=sha256_file(path),
            validation={"scope_valid": True, "all_passed": all(item.passed for item in result.verifications)},
        )
        record_attempt(
            connection,
            unit_id,
            WorkflowStage.VALIDATE_REPAIRED.value,
            attempt,
            StageStatus.COMPLETED,
            metrics=asdict(generated.generation.metrics),
        )
        record_validation(
            connection,
            unit_id,
            "repair_verification_scope",
            True,
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
        connection, unit_id, WorkflowStage.VALIDATE_REPAIRED.value,
        "repair_verification", StageStatus.COMPLETED,
        parent_id=document_id, attempts=existing_attempts + allowed_attempts,
        input_hash=input_hash, output_hash=sha256_file(path),
        message="structured verification exhausted; escalated to human review",
        validation={
            "scope_valid": True,
            "all_passed": False,
            "fallback_escalation": True,
        },
    )
    record_validation(
        connection, unit_id, "repair_verification_scope", True,
        details={
            "expected_ids": expected_ids,
            "fallback_escalation": True,
            "error": last_error,
        },
    )
    return result


def _verification_output_token_limit(expected_count: int) -> int:
    """Bound judge output while leaving room for one concise decision per segment."""
    return repair_verification_output_token_limit(expected_count)


def _load_current_verification(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return RepairVerificationResult.model_validate_json(path.read_text(encoding="utf-8"))


def _record_file(
    connection, workspace, path, kind,
    *, workflow_stage=WorkflowStage.VALIDATE_REPAIRED,
) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        workflow_stage.value,
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
    previous = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.VALIDATE_REPAIRED.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
