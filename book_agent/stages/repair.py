"""Resumable targeted translation-repair stage."""

from __future__ import annotations

from dataclasses import asdict

from ..atomic_io import atomic_write_text
from ..audit import is_decorative_separator, reconcile_document_audit
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
    LocatedRepairResult,
    RepairDisposition,
    RepairedDocument,
    SegmentRepair,
    TranslationRepairReport,
    apply_deterministic_repairs,
    apply_located_edits,
    apply_segment_repairs,
    build_repair_prompt,
    build_located_repair_prompt,
    requires_full_segment_translation,
    select_repair_targets,
    validate_repair_output,
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
from ..translation import render_translated_document
from ..workspace import JobWorkspace
from .audit import load_document_audits
from .preprocess import load_preprocessed_documents
from .translate import load_translated_documents


REPAIR_STAGE_VERSION = "18"


def _repair_model(config: AppConfig) -> str:
    return config.audit.repair_model or config.ollama.model


def _repair_max_num_ctx(config: AppConfig) -> int:
    return config.audit.repair_max_num_ctx or config.audit.max_num_ctx


def run_translation_repair_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> TranslationRepairReport:
    """Repair only audit-flagged segments and queue repeated failures for review."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        audit_stage = _require_completed(connection, WorkflowStage.AUDIT_TRANSLATION)
        translation_stage = _require_completed(connection, WorkflowStage.TRANSLATE)
        input_hash = build_stage_input_hash(
            {
                "audit": str(audit_stage["output_hash"]),
                "translation": str(translation_stage["output_hash"]),
                "minimum_severity": config.audit.repair_min_severity,
                "repair_min_num_ctx": str(config.audit.repair_min_num_ctx),
                "repair_context_multiplier": str(
                    config.audit.repair_context_multiplier
                ),
                "repair_max_attempts": str(config.audit.repair_max_attempts),
                "repair_max_num_ctx": str(_repair_max_num_ctx(config)),
                "style": config.translation.model_dump_json(),
                "model": _repair_model(config),
                "stage_version": REPAIR_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.REPAIR_TRANSLATION,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_translation_repair_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.REPAIR_TRANSLATION.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.REPAIR_TRANSLATION)
            previous = get_stage_status(connection, WorkflowStage.REPAIR_TRANSLATION.value)
        retire_stage_artifacts(connection, WorkflowStage.REPAIR_TRANSLATION.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.REPAIR_TRANSLATION.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
        translated = {item.manifest_id: item for item in load_translated_documents(workspace)}
        historical_audits = load_document_audits(workspace)
        audits = [
            reconcile_document_audit(
                sources[item.document_id],
                translated[item.document_id],
                item,
                config.audit,
            )
            for item in historical_audits
        ]
        targets = select_repair_targets(audits, config.audit.repair_min_severity)
        stage_root = workspace.directory(f"repaired/{input_hash[:16]}")
        segment_root = stage_root / "segments"
        segment_root.mkdir(parents=True, exist_ok=True)
        repaired_documents: list[RepairedDocument] = []
        all_repairs: list[SegmentRepair] = []
        source_texts = {
            document_id: {
                segment.segment_id: segment.processed_text
                for segment in document.segments
            }
            for document_id, document in sources.items()
        }
        total_repairs = sum(
            not is_decorative_separator(source_texts[document_id][segment_id])
            for document_id, items in targets.items()
            for segment_id in items
        )
        repair_tasks = []
        repairs_by_document: dict[str, dict[str, SegmentRepair]] = {}
        for audit in audits:
            source = sources[audit.document_id]
            draft = translated[audit.document_id]
            issues_by_segment = targets.get(audit.document_id, {})
            source_by_id = {item.segment_id: item.processed_text for item in source.segments}
            target_by_id = {item.segment_id: item.translated_text for item in draft.segments}
            for segment_id, issues in issues_by_segment.items():
                if is_decorative_separator(source_by_id[segment_id]):
                    report_segment_result(
                        client,
                        model=_repair_model(config),
                        stage=WorkflowStage.REPAIR_TRANSLATION.value,
                        segment_id=segment_id,
                        result="skipped",
                        mode="decorative-prescreen",
                        role="repair.initial",
                    )
                    continue
                unit_id = f"repair-{segment_id}"
                full_retranslation = requires_full_segment_translation(issues)
                prompt = build_repair_prompt(source, draft, segment_id, issues, config)
                located_prompt = (
                    ""
                    if full_retranslation
                    else build_located_repair_prompt(
                        source, draft, segment_id, issues, config
                    )
                )
                unit_input_hash = hash_named_values(
                    {
                        "stage": input_hash,
                        "prompt": prompt,
                        "located_prompt": located_prompt,
                        "repair_mode": (
                            "full-retranslation"
                            if full_retranslation
                            else "bounded-edit"
                        ),
                        "segment": segment_id,
                    }
                )
                path = segment_root / f"{unit_id}.json"
                existing = get_work_unit(
                    connection, unit_id, WorkflowStage.REPAIR_TRANSLATION.value
                )
                repair = _load_current_repair(existing, path, unit_input_hash)
                text_bucket = (
                    _repair_max_num_ctx(config)
                    if not config.ollama.adaptive_num_ctx
                    else request_context_bucket(
                        prompt,
                        minimum=config.audit.repair_min_num_ctx,
                        maximum=_repair_max_num_ctx(config),
                        multiplier=config.audit.repair_context_multiplier,
                    )
                )
                structured_bucket = (
                    text_bucket
                    if full_retranslation
                    else (
                        _repair_max_num_ctx(config)
                        if not config.ollama.adaptive_num_ctx
                        else request_context_bucket(
                            located_prompt,
                            minimum=config.audit.repair_min_num_ctx,
                            maximum=_repair_max_num_ctx(config),
                            schema=LocatedRepairResult,
                        )
                    )
                )
                task = {
                    "audit": audit,
                    "source": source,
                    "unit_id": unit_id,
                    "unit_input_hash": unit_input_hash,
                    "path": path,
                    "prompt": prompt,
                    "located_prompt": located_prompt,
                    "source_text": source_by_id[segment_id],
                    "target_text": target_by_id[segment_id],
                    "segment_id": segment_id,
                    "issues": issues,
                    "full_retranslation": full_retranslation,
                    "existing": existing,
                    "bucket": max(structured_bucket, text_bucket),
                }
                if repair is None:
                    try:
                        repair = _run_repair(
                            connection,
                            unit_id,
                            audit.document_id,
                            unit_input_hash,
                            path,
                            prompt,
                            located_prompt,
                            source_by_id[segment_id],
                            target_by_id[segment_id],
                            segment_id,
                            issues,
                            config,
                            None,
                            full_retranslation=full_retranslation,
                            relevant_glossary=source.relevant_glossary,
                            existing_attempts=(
                                int(existing["attempts"]) if existing else 0
                            ),
                            repair_index=0,
                            total_repairs=total_repairs,
                        )
                    except ValueError as error:
                        if "required for non-mechanical repair" not in str(error):
                            raise
                        repair_tasks.append(task)
                    else:
                        repairs_by_document.setdefault(audit.document_id, {})[
                            segment_id
                        ] = repair
                        _record_file(
                            connection, workspace, path, "segment_repair"
                        )
                else:
                    repairs_by_document.setdefault(audit.document_id, {})[
                        segment_id
                    ] = repair
                    _record_file(connection, workspace, path, "segment_repair")

        report_stage_plan(
            client,
            model=_repair_model(config),
            stage=WorkflowStage.REPAIR_TRANSLATION.value,
            prescreened=sum(len(items) for items in targets.values()),
            llm_tasks=len(repair_tasks),
            skipped=total_repairs - len(repair_tasks),
            context_buckets=(task["bucket"] for task in repair_tasks),
            role="repair.initial",
        )
        ordered_tasks = group_by_context_bucket(
            repair_tasks, lambda task: int(task["bucket"])
        )
        for repair_index, task in enumerate(ordered_tasks, start=1):
            source = task["source"]
            repair = _run_repair(
                connection,
                task["unit_id"],
                task["audit"].document_id,
                task["unit_input_hash"],
                task["path"],
                task["prompt"],
                task["located_prompt"],
                task["source_text"],
                task["target_text"],
                task["segment_id"],
                task["issues"],
                config,
                client,
                full_retranslation=bool(task["full_retranslation"]),
                relevant_glossary=source.relevant_glossary,
                existing_attempts=(
                    int(task["existing"]["attempts"]) if task["existing"] else 0
                ),
                repair_index=repair_index,
                total_repairs=len(ordered_tasks),
                context_bucket=int(task["bucket"]),
            )
            repairs_by_document.setdefault(task["audit"].document_id, {})[
                task["segment_id"]
            ] = repair
            _record_file(connection, workspace, task["path"], "segment_repair")

        for audit in audits:
            source = sources[audit.document_id]
            draft = translated[audit.document_id]
            repairs = list(repairs_by_document.get(audit.document_id, {}).values())
            order = {
                segment_id: index
                for index, segment_id in enumerate(
                    targets.get(audit.document_id, {}).keys()
                )
            }
            repairs.sort(key=lambda item: order[item.segment_id])
            repaired_draft = apply_segment_repairs(draft, repairs)
            result = RepairedDocument(document=repaired_draft, repairs=repairs)
            json_path = stage_root / f"{source.order:04d}-{source.manifest_id}.repaired.json"
            text_path = stage_root / f"{source.order:04d}-{source.manifest_id}.repaired.txt"
            atomic_write_text(json_path, result.model_dump_json(indent=2))
            atomic_write_text(text_path, render_translated_document(repaired_draft))
            _record_file(connection, workspace, json_path, "repaired_document_json")
            _record_file(connection, workspace, text_path, "repaired_document_text")
            repaired_documents.append(result)
            all_repairs.extend(repairs)
            for repair in repairs:
                report_segment_result(
                    client,
                    model=_repair_model(config),
                    stage=WorkflowStage.REPAIR_TRANSLATION.value,
                    segment_id=repair.segment_id,
                    result=(
                        "repaired"
                        if repair.disposition is RepairDisposition.REPAIRED
                        else "pending"
                    ),
                    mode=(
                        "deterministic-repair"
                        if repair.attempts == 0
                        else (
                            "full-retranslation"
                            if requires_full_segment_translation(repair.issues)
                            else "semantic-repair"
                        )
                    ),
                    issue_count=(
                        0 if repair.disposition is RepairDisposition.REPAIRED
                        else len(repair.issues)
                    ),
                    context_bucket=next(
                        (
                            int(task["bucket"])
                            for task in repair_tasks
                            if task["segment_id"] == repair.segment_id
                        ),
                        0,
                    ),
                    message=(
                        "repair-contract-unresolved"
                        if repair.disposition is RepairDisposition.REVIEW
                        else ""
                    ),
                    role="repair.initial",
                )

        review_ids = sorted(
            item.segment_id
            for item in all_repairs
            if item.disposition is RepairDisposition.REVIEW
        )
        report = TranslationRepairReport(
            document_count=len(repaired_documents),
            targeted_segment_count=len(all_repairs),
            repaired_segment_count=sum(
                item.disposition is RepairDisposition.REPAIRED for item in all_repairs
            ),
            review_segment_ids=review_ids,
        )
        report_path = stage_root / "translation-repair.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "translation_repair_report")
        set_job_metadata(
            connection,
            "translation_repair_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "repaired_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.REPAIR_TRANSLATION)
        set_stage_status(
            connection,
            WorkflowStage.REPAIR_TRANSLATION.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
            message=(f"{len(review_ids)} segment(s) require human review" if review_ids else ""),
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def load_repaired_documents(workspace: JobWorkspace) -> list[RepairedDocument]:
    """Load repaired document records in spine order."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.REPAIR_TRANSLATION.value,
            root_metadata_key="repaired_root",
            report_metadata_key="translation_repair_report",
        )
        documents = []
        for artifact in artifacts:
            if artifact["kind"] != "repaired_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            documents.append(RepairedDocument.model_validate_json(path.read_text(encoding="utf-8")))
        documents = require_unique_items(
            documents,
            lambda item: item.document.manifest_id,
            label="repaired document",
        )
        return sorted(documents, key=lambda item: item.document.order)
    finally:
        connection.close()


def load_translation_repair_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> TranslationRepairReport:
    """Load the published targeted-repair report."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "translation_repair_report")
        if not relative:
            raise FileNotFoundError("translation repair report is not recorded")
        path = workspace.directory(relative)
        return TranslationRepairReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def _run_repair(
    connection,
    unit_id,
    document_id,
    input_hash,
    path,
    prompt,
    located_prompt,
    source_text,
    original_translation,
    segment_id,
    issues,
    config,
    client,
    *,
    full_retranslation=False,
    relevant_glossary,
    existing_attempts,
    repair_index,
    total_repairs,
    context_bucket=None,
):
    deterministic, applied_rules = apply_deterministic_repairs(
        original_translation, issues
    )
    if deterministic != original_translation:
        repaired, validation = validate_repair_output(
            f"<{segment_id}>{deterministic}</{segment_id}>",
            source_text,
            segment_id,
            original_translation,
            config,
            relevant_glossary,
            trigger_issues=issues,
        )
        record_validation(
            connection,
            unit_id,
            "deterministic_repair_contract",
            validation.passed,
            details={
                **validation.model_dump(mode="json"),
                "rules": applied_rules,
            },
        )
        if validation.passed:
            result = SegmentRepair(
                segment_id=segment_id,
                disposition=RepairDisposition.REPAIRED,
                original_translation=original_translation,
                repaired_translation=repaired,
                issues=issues,
                attempts=0,
                message="deterministic repair: " + ", ".join(applied_rules),
            )
            atomic_write_text(path, result.model_dump_json(indent=2))
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.REPAIR_TRANSLATION.value,
                "segment_repair",
                StageStatus.COMPLETED,
                parent_id=document_id,
                attempts=0,
                input_hash=input_hash,
                output_hash=sha256_file(path),
                validation={
                    "passed": True,
                    "disposition": result.disposition.value,
                    "deterministic": True,
                    "rules": applied_rules,
                },
            )
            return result

    if client is None:
        raise ValueError(
            f"an Ollama client is required for non-mechanical repair: {segment_id}"
        )
    allowed_attempts = min(
        config.workflow.max_retries + 1, config.audit.repair_max_attempts
    )
    last_message = ""
    seen_candidates: set[str] = set()
    seen_validations: set[tuple[str, ...]] = set()
    last_attempt = existing_attempts
    for offset in range(1, allowed_attempts + 1):
        attempt = existing_attempts + offset
        last_attempt = attempt
        request_prompt = prompt
        if last_message:
            request_prompt += (
                "\n\nThe previous repair was rejected. Return the complete corrected marker. "
                f"Validation errors: {last_message}"
            )
        generation = None
        repaired = ""
        validation = None
        if (
            not full_retranslation
            and offset == 1
            and callable(getattr(client, "generate_structured", None))
        ):
            try:
                generated = client.generate_structured(
                    located_prompt,
                    LocatedRepairResult,
                    model=_repair_model(config),
                    think=config.translation.thinking,
                    progress_label=(
                        f"repair={repair_index}/{total_repairs} id={unit_id} "
                        "located-edit=1/1"
                    ),
                    **llm_role_kwargs(client, "repair.initial.located_edit"),
                    context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
                    context_maximum=(
                        context_bucket or _repair_max_num_ctx(config)
                    ),
                    max_output_tokens=1_024,
                    max_attempts=1,
                )
                located_text = apply_located_edits(
                    original_translation, generated.value.edits
                )
                repaired, validation = validate_repair_output(
                    f"<{segment_id}>{located_text}</{segment_id}>",
                    source_text,
                    segment_id,
                    original_translation,
                    config,
                    relevant_glossary,
                    trigger_issues=issues,
                )
                generation = generated.generation
            except (StructuredOutputError, TypeError, ValueError):
                validation = None
        if validation is None or not validation.passed:
            generation = client.generate_text(
                request_prompt,
                stream=True,
                think=config.translation.thinking,
                model=_repair_model(config),
                progress_label=(
                    f"repair={repair_index}/{total_repairs} id={unit_id} "
                    f"attempt={offset}/{allowed_attempts} "
                    f"mode={'full-retranslation' if full_retranslation else 'semantic'}"
                ),
                **llm_role_kwargs(client, "repair.initial"),
                context_minimum=(context_bucket or config.audit.repair_min_num_ctx),
                context_maximum=(context_bucket or _repair_max_num_ctx(config)),
                context_multiplier=config.audit.repair_context_multiplier,
            )
            repaired, validation = validate_repair_output(
                generation.content,
                source_text,
                segment_id,
                original_translation,
                config,
                relevant_glossary,
                trigger_issues=issues,
            )
        record_validation(
            connection,
            unit_id,
            "targeted_repair_contract",
            validation.passed,
            details=validation.model_dump(mode="json"),
        )
        if validation.passed:
            result = SegmentRepair(
                segment_id=segment_id,
                disposition=RepairDisposition.REPAIRED,
                original_translation=original_translation,
                repaired_translation=repaired,
                issues=issues,
                attempts=attempt,
            )
            atomic_write_text(path, result.model_dump_json(indent=2))
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.REPAIR_TRANSLATION.value,
                "segment_repair",
                StageStatus.COMPLETED,
                parent_id=document_id,
                attempts=attempt,
                input_hash=input_hash,
                output_hash=sha256_file(path),
                validation={"passed": True, "disposition": result.disposition.value},
            )
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.REPAIR_TRANSLATION.value,
                attempt,
                StageStatus.COMPLETED,
                metrics=asdict(generation.metrics),
            )
            return result
        normalized_candidate = " ".join(repaired.casefold().split())
        validation_signature = tuple(
            sorted(f"{item.code}:{' '.join(item.message.casefold().split())}" for item in validation.issues)
        )
        convergence_reason = ""
        if normalized_candidate in seen_candidates:
            convergence_reason = "repeated_candidate"
        elif validation_signature in seen_validations:
            convergence_reason = "repeated_validation"
        seen_candidates.add(normalized_candidate)
        seen_validations.add(validation_signature)
        last_message = convergence_reason or "; ".join(
            item.message for item in validation.issues
        )
        record_attempt(
            connection,
            unit_id,
            WorkflowStage.REPAIR_TRANSLATION.value,
            attempt,
            StageStatus.FAILED,
            message=last_message,
            metrics=asdict(generation.metrics),
        )
        if convergence_reason:
            break
    result = SegmentRepair(
        segment_id=segment_id,
        disposition=RepairDisposition.REVIEW,
        original_translation=original_translation,
        repaired_translation="",
        issues=issues,
        attempts=last_attempt,
        message=last_message,
    )
    atomic_write_text(path, result.model_dump_json(indent=2))
    set_work_unit_status(
        connection,
        unit_id,
        WorkflowStage.REPAIR_TRANSLATION.value,
        "segment_repair",
        StageStatus.COMPLETED,
        parent_id=document_id,
        attempts=result.attempts,
        input_hash=input_hash,
        output_hash=sha256_file(path),
        validation={"passed": False, "disposition": result.disposition.value},
        message=last_message,
    )
    return result


def _load_current_repair(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return SegmentRepair.model_validate_json(path.read_text(encoding="utf-8"))


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.REPAIR_TRANSLATION.value,
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
    previous = get_stage_status(connection, WorkflowStage.REPAIR_TRANSLATION.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.REPAIR_TRANSLATION.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
