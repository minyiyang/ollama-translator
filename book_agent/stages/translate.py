"""Resumable first-draft translation stage backed by the local Ollama client."""

from __future__ import annotations

from dataclasses import asdict

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..hashing import hash_named_values, sha256_file
from ..ollama_client import (
    GenerationMetrics,
    GenerationProgressEvent,
    GenerationResult,
    OllamaClient,
    StructuredOutputError,
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
from ..stage_artifacts import (
    list_active_stage_artifacts,
    require_document_totals,
    require_unique_items,
)
from ..styles import build_style_prompt, load_style_instruction
from ..translation import (
    InlineMarkerPlacementResult,
    InlineMarkerSelectionResult,
    SingleInlineMarkerSelectionResult,
    TranslatedChunk,
    TranslatedDocument,
    TranslationIssue,
    TranslationOutputError,
    TranslationReport,
    TranslationValidation,
    apply_inline_marker_placements,
    apply_inline_marker_selections,
    apply_single_inline_marker_selections,
    assemble_translated_segments,
    build_inline_marker_placement_prompt,
    build_inline_marker_selection_prompt,
    build_single_inline_marker_selection_prompt,
    build_translation_chunks,
    build_translation_prompt,
    calculate_translation_source_budget,
    constrain_translation_chunks_by_prompt,
    format_relevant_glossary,
    render_translated_document,
    restore_deterministic_inline_markers,
    supports_inline_marker_selection,
    supports_single_inline_marker_selection,
    validate_translation_output,
)
from ..preprocessing import PreprocessedDocument, select_relevant_glossary_entries
from ..schemas import GlossaryEntry
from ..stage_progress import (
    group_by_context_bucket,
    llm_role_kwargs,
    report_stage_plan,
    request_context_bucket,
)
from ..workspace import JobWorkspace
from .preprocess import load_preprocessed_documents


TRANSLATE_STAGE_VERSION = "5"


def run_translation_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient,
) -> TranslationReport:
    """Translate all preprocessed documents and checkpoint every validated chunk."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        preprocessing = _require_completed(connection, WorkflowStage.PREPROCESS)
        style_instruction = load_style_instruction(
            config.translation.style,
            config.translation.custom_style_file,
        )
        style_prompt = build_style_prompt(
            style_instruction,
            config.translation.direction,
            de_ai_enabled=config.translation.de_ai_enabled,
            de_ai_strength=config.translation.de_ai_strength,
        )
        input_hash = build_stage_input_hash(
            {
                "preprocessing": str(preprocessing["output_hash"]),
                "direction": config.translation.direction.value,
                "style": config.translation.style.value,
                "style_instruction": style_instruction,
                "de_ai_enabled": str(config.translation.de_ai_enabled),
                "de_ai_strength": config.translation.de_ai_strength,
                "model": config.ollama.model,
                "num_ctx": str(config.ollama.num_ctx),
                "temperature": str(config.ollama.temperature),
                "translation_config": config.translation.model_dump_json(),
                "budget": config.budget.model_dump_json(),
                "stage_version": TRANSLATE_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.TRANSLATE,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_translation_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.TRANSLATE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.TRANSLATE)
            previous = get_stage_status(connection, WorkflowStage.TRANSLATE.value)
        retire_stage_artifacts(connection, WorkflowStage.TRANSLATE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.TRANSLATE.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        stage_root = workspace.directory(f"translated/{input_hash[:16]}")
        chunk_root = stage_root / "chunks"
        attempt_root = stage_root / "attempts"
        chunk_root.mkdir(parents=True, exist_ok=True)
        attempt_root.mkdir(parents=True, exist_ok=True)
        set_job_metadata(
            connection,
            "translation_active_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "translation_attempts_root",
            attempt_root.relative_to(workspace.root).as_posix(),
        )
        documents = load_preprocessed_documents(workspace)
        translated_documents: list[TranslatedDocument] = []
        generation_attempts = 0
        document_plans = []
        for document in documents:
            source_budget = calculate_translation_source_budget(
                config, style_prompt, ""
            )
            chunks = build_translation_chunks(
                document,
                min(source_budget, max(1, config.translation.max_prompt_tokens // 2)),
            )
            chunks = constrain_translation_chunks_by_prompt(
                chunks,
                config.translation.max_prompt_tokens,
                lambda chunk: _build_chunk_prompt(
                    document,
                    chunk,
                    _chunk_relevant_glossary(document, chunk, config),
                    config,
                ),
            )
            document_plans.append((document, chunks))
        chunk_count = sum(len(chunks) for _, chunks in document_plans)
        translated_chunks: dict[str, TranslatedChunk] = {}
        translation_tasks = []
        for document, chunks in document_plans:
            for chunk in chunks:
                chunk_glossary = _chunk_relevant_glossary(document, chunk, config)
                glossary_text = format_relevant_glossary(
                    chunk_glossary, config.translation.direction
                )
                chunk_input_hash = hash_named_values(
                    {
                        "stage": input_hash,
                        "chunk": chunk.model_dump_json(),
                        "glossary": glossary_text,
                    }
                )
                chunk_path = chunk_root / f"{chunk.chunk_id}.json"
                existing = get_work_unit(
                    connection, chunk.chunk_id, WorkflowStage.TRANSLATE.value
                )
                translated_chunk = _load_current_chunk(
                    existing, chunk_path, chunk_input_hash
                )
                if translated_chunk is not None:
                    translated_chunks[chunk.chunk_id] = translated_chunk
                    _record_file(
                        connection, workspace, chunk_path, "translated_chunk_json"
                    )
                    continue
                prompt = _build_chunk_prompt(document, chunk, chunk_glossary, config)
                bucket = (
                    config.translation.max_num_ctx
                    if not config.ollama.adaptive_num_ctx
                    else request_context_bucket(
                        prompt,
                        minimum=config.ollama.min_num_ctx,
                        maximum=config.translation.max_num_ctx,
                        multiplier=config.translation.context_multiplier,
                    )
                )
                translation_tasks.append(
                    {
                        "chunk": chunk,
                        "document": document,
                        "chunk_input_hash": chunk_input_hash,
                        "chunk_path": chunk_path,
                        "existing": existing,
                        "relevant_glossary": chunk_glossary,
                        "bucket": bucket,
                    }
                )

        report_stage_plan(
            client,
            model=config.ollama.model,
            stage=WorkflowStage.TRANSLATE.value,
            prescreened=chunk_count,
            llm_tasks=len(translation_tasks),
            skipped=chunk_count - len(translation_tasks),
            context_buckets=(task["bucket"] for task in translation_tasks),
            role="translate.draft",
        )
        ordered_tasks = group_by_context_bucket(
            translation_tasks, lambda task: int(task["bucket"])
        )
        scheduled_total = len(ordered_tasks)
        for scheduled_index, task in enumerate(ordered_tasks, start=1):
            existing = task["existing"]
            translated_chunk, used_attempts = _translate_chunk(
                connection,
                task["document"],
                task["chunk"],
                task["chunk_input_hash"],
                task["chunk_path"],
                attempt_root,
                config,
                client,
                existing_attempts=(
                    int(existing["attempts"])
                    if existing
                    and existing["input_hash"] == task["chunk_input_hash"]
                    else 0
                ),
                relevant_glossary=task["relevant_glossary"],
                chunk_index=scheduled_index,
                total_chunks=scheduled_total,
                context_bucket=int(task["bucket"]),
            )
            generation_attempts += used_attempts
            translated_chunks[task["chunk"].chunk_id] = translated_chunk
            _record_file(
                connection,
                workspace,
                task["chunk_path"],
                "translated_chunk_json",
            )

        for document, chunks in document_plans:
            translated_parts: dict[str, str] = {}
            for chunk in chunks:
                translated_parts.update(translated_chunks[chunk.chunk_id].translations)

            translated_document = TranslatedDocument(
                order=document.order,
                manifest_id=document.manifest_id,
                archive_path=document.archive_path,
                direction=config.translation.direction,
                style=config.translation.style.value,
                segments=assemble_translated_segments(
                    document, translated_parts, config.translation.direction
                ),
            )
            stem = f"{document.order:04d}-{document.manifest_id}"
            json_path = stage_root / f"{stem}.json"
            text_path = stage_root / f"{stem}.txt"
            atomic_write_text(json_path, translated_document.model_dump_json(indent=2))
            atomic_write_text(text_path, render_translated_document(translated_document))
            _record_file(connection, workspace, json_path, "translated_document_json")
            _record_file(connection, workspace, text_path, "translated_document_text")
            translated_documents.append(translated_document)

        deferred_chunks = [
            item
            for item in translated_chunks.values()
            if any(
                issue.code == "deferred_translation"
                for issue in item.validation.issues
            )
        ]
        deferred_reference_ids = {
            issue.reference_id
            for item in deferred_chunks
            for issue in item.validation.issues
            if issue.code == "deferred_translation" and issue.reference_id
        }
        deferred_segment_ids = sorted(
            {
                piece.segment_id
                for _document, chunks in document_plans
                for chunk in chunks
                for piece in chunk.pieces
                if piece.reference_id in deferred_reference_ids
            }
        )
        report = TranslationReport(
            direction=config.translation.direction,
            style=config.translation.style.value,
            document_count=len(translated_documents),
            segment_count=sum(len(item.segments) for item in translated_documents),
            chunk_count=chunk_count,
            generation_attempts=generation_attempts,
            deferred_chunk_count=len(deferred_chunks),
            deferred_segment_count=len(deferred_segment_ids),
            deferred_segment_ids=deferred_segment_ids,
        )
        report_path = stage_root / "translation.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "translation_report")
        output_hash = build_stage_output_hash(connection, WorkflowStage.TRANSLATE)
        set_job_metadata(
            connection,
            "translation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "translated_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        set_stage_status(
            connection,
            WorkflowStage.TRANSLATE.value,
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


def _chunk_relevant_glossary(
    document: PreprocessedDocument,
    chunk: TranslationChunk,
    config: AppConfig,
) -> list[GlossaryEntry]:
    """Select only unambiguous terms present in one translation chunk."""
    return select_relevant_glossary_entries(
        "\n".join(piece.source_text for piece in chunk.pieces),
        document.relevant_glossary,
        config.translation.direction,
    )


def _build_chunk_prompt(
    document: PreprocessedDocument,
    chunk: TranslationChunk,
    relevant_glossary: list[GlossaryEntry],
    config: AppConfig,
) -> str:
    """Build a target-only prompt with optional immutable boundary context."""
    prompt = build_translation_prompt(chunk, relevant_glossary, config)
    if config.translation.boundary_context != "adjacent-read-only":
        return prompt
    index_by_id = {
        segment.segment_id: index for index, segment in enumerate(document.segments)
    }
    first = index_by_id[chunk.pieces[0].segment_id]
    last = index_by_id[chunk.pieces[-1].segment_id]
    sections = [
        "Read-only boundary context follows. Use it only to disambiguate meaning, "
        "continuity, referents, and narrative voice. Do not translate, quote, "
        "summarize, or emit it. It is immutable context, not an output target. "
        f"Your output must contain exactly {len(chunk.pieces)} target markers from "
        "the target passage above and no boundary-context marker."
    ]
    if first > 0:
        sections.append(
            "Previous source segment (read-only):\n"
            f"{document.segments[first - 1].processed_text}"
        )
    if last + 1 < len(document.segments):
        sections.append(
            "Next source segment (read-only):\n"
            f"{document.segments[last + 1].processed_text}"
        )
    return prompt + "\n\n" + "\n\n".join(sections)


def load_translated_documents(workspace: JobWorkspace) -> list[TranslatedDocument]:
    """Load published translated document manifests in spine order."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.TRANSLATE.value,
            root_metadata_key="translated_root",
            report_metadata_key="translation_report",
        )
        documents = []
        for artifact in artifacts:
            if artifact["kind"] != "translated_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            documents.append(
                TranslatedDocument.model_validate_json(path.read_text(encoding="utf-8"))
            )
        documents = require_unique_items(
            documents,
            lambda document: document.manifest_id,
            label="translated document",
        )
        report = load_translation_report(workspace, connection=connection)
        documents = require_document_totals(
            documents,
            expected_documents=report.document_count,
            expected_segments=report.segment_count,
            segment_count=lambda document: len(document.segments),
            label="translated document",
        )
        return sorted(documents, key=lambda document: document.order)
    finally:
        connection.close()


def load_translation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> TranslationReport:
    """Load the published first-draft translation report."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "translation_report")
        if not relative:
            raise FileNotFoundError("translation report is not recorded")
        path = workspace.directory(relative)
        return TranslationReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def _translate_chunk(
    connection,
    document,
    chunk,
    chunk_input_hash,
    chunk_path,
    attempt_root,
    config,
    client,
    *,
    existing_attempts,
    relevant_glossary,
    chunk_index,
    total_chunks,
    context_bucket,
):
    last_validation = None
    accepted_translations: dict[str, str] = {}
    pending_chunk = chunk
    saved_repairable_content = _load_repairable_saved_attempt(
        attempt_root,
        chunk,
        config.translation.direction,
        relevant_glossary,
    )
    allowed_attempts = config.workflow.max_retries + 1
    generation_calls = 0
    for offset in range(1, allowed_attempts + 1):
        attempt = existing_attempts + offset
        models = [config.ollama.model, *config.translation.fallback_models]
        model_index = min(
            (attempt - 1) // config.translation.attempts_per_model,
            len(models) - 1,
        )
        active_model = models[model_index]
        retry_mode = (
            "full"
            if offset == 1 or len(pending_chunk.pieces) == len(chunk.pieces)
            else f"focused passages={len(pending_chunk.pieces)}"
        )
        previous_codes = sorted(
            {issue.code for issue in last_validation.issues}
        ) if last_validation is not None else []
        progress_label = (
            f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
            f"attempt={offset}/{allowed_attempts} "
            f"mode={retry_mode}"
        )
        if previous_codes:
            progress_label += f" after={','.join(previous_codes)}"
        prompt = _build_chunk_prompt(
            document, pending_chunk, relevant_glossary, config
        )
        if last_validation is not None:
            diagnostics = "; ".join(issue.message for issue in last_validation.issues)
            prompt += (
                "\n\nYour previous response was rejected. Return corrected output only for "
                "the marked passages in the Source section of this request. "
                f"Validation errors: {diagnostics}"
            )
        if offset == 1 and saved_repairable_content is not None:
            result = GenerationResult(
                content=saved_repairable_content,
                thinking="",
                metrics=GenerationMetrics(),
            )
        else:
            result = client.generate_text(
                prompt,
                stream=True,
                think=config.translation.thinking,
                model=active_model,
                progress_label=progress_label,
                **llm_role_kwargs(
                    client,
                    "translate.draft.primary"
                    if active_model == config.ollama.model
                    else "translate.draft.fallback",
                ),
                context_minimum=context_bucket,
                context_maximum=context_bucket,
                context_multiplier=config.translation.context_multiplier,
            )
            generation_calls += 1
        attempt_path = attempt_root / (
            f"{chunk.chunk_id}.attempt-{attempt:03d}-{_safe_model_name(active_model)}.txt"
        )
        atomic_write_text(attempt_path, result.content)
        translations, validation = validate_translation_output(
            result.content,
            pending_chunk,
            config.translation.direction,
            relevant_glossary,
        )
        metrics = {**asdict(result.metrics), "model": active_model}
        record_validation(
            connection,
            chunk.chunk_id,
            "first_draft_contract",
            validation.passed,
            details=validation.model_dump(mode="json"),
        )
        if (
            not validation.passed
            and translations
            and callable(getattr(client, "generate_structured", None))
            and all(
                issue.code == "protected_marker_mismatch"
                for issue in validation.issues
            )
        ):
            marker_attempt_name = "marker-placement"
            marker_purpose = "structured_marker_placement"
            try:
                _retain_valid_translations(
                    accepted_translations,
                    translations,
                    validation,
                )
                repair_chunk = _build_retry_chunk(
                    pending_chunk,
                    accepted_translations,
                    validation,
                )
                repair_translations = {
                    piece.reference_id: translations[piece.reference_id]
                    for piece in repair_chunk.pieces
                }
                repaired_translations = restore_deterministic_inline_markers(
                    repair_chunk,
                    repair_translations,
                )
                marker_metrics: dict[str, object]
                marker_validation_name: str
                if repaired_translations is not None:
                    marker_attempt_name = "marker-restoration"
                    marker_validation_name = "deterministic_marker_restoration"
                    marker_purpose = marker_validation_name
                    marker_metrics = {
                        "model": "deterministic",
                        "purpose": marker_validation_name,
                    }
                    _report_deterministic_marker_restoration(
                        client,
                        (
                            f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
                            "marker-restoration=deterministic"
                        ),
                        len(repair_chunk.pieces),
                    )
                else:
                    single_marker_selection = (
                        supports_single_inline_marker_selection(repair_chunk)
                    )
                    flat_marker_selection = (
                        not single_marker_selection
                        and supports_inline_marker_selection(repair_chunk)
                    )
                    placement_schema = (
                        SingleInlineMarkerSelectionResult
                        if single_marker_selection
                        else (
                            InlineMarkerSelectionResult
                            if flat_marker_selection
                            else InlineMarkerPlacementResult
                        )
                    )
                    cached_placement = _load_saved_marker_placement(
                        attempt_root,
                        chunk.chunk_id,
                        placement_schema,
                    )
                    if cached_placement is not None:
                        try:
                            repaired_translations = (
                                apply_single_inline_marker_selections(
                                    repair_chunk,
                                    repair_translations,
                                    cached_placement,
                                )
                                if single_marker_selection
                                else (
                                    apply_inline_marker_selections(
                                        repair_chunk,
                                        repair_translations,
                                        cached_placement,
                                    )
                                    if flat_marker_selection
                                    else apply_inline_marker_placements(
                                        repair_chunk,
                                        repair_translations,
                                        cached_placement,
                                    )
                                )
                            )
                        except TranslationOutputError:
                            cached_placement = None
                    if cached_placement is not None:
                        marker_validation_name = "cached_structured_marker_placement"
                        marker_purpose = marker_validation_name
                        marker_metrics = {
                            "model": "cached",
                            "purpose": marker_validation_name,
                        }
                        _report_cached_marker_placement(
                            client,
                            f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
                            "marker-restoration=cached-structured"
                        )
                    else:
                        generation_calls += 1
                        placement_prompt = (
                            build_single_inline_marker_selection_prompt(
                                repair_chunk, repair_translations
                            )
                            if single_marker_selection
                            else (
                                build_inline_marker_selection_prompt(
                                    repair_chunk, repair_translations
                                )
                                if flat_marker_selection
                                else build_inline_marker_placement_prompt(
                                    repair_chunk, repair_translations
                                )
                            )
                        )
                        marker_repair_mode = (
                            "exact-span"
                            if single_marker_selection or flat_marker_selection
                            else "structured-placement"
                        )
                        try:
                            placement = client.generate_structured(
                                placement_prompt,
                                placement_schema,
                                think=config.translation.thinking,
                                model=active_model,
                                progress_label=(
                                    f"chunk={chunk_index}/{total_chunks} "
                                    f"id={chunk.chunk_id} marker-repair=1/1 "
                                    f"mode={marker_repair_mode}"
                                ),
                                **llm_role_kwargs(client, "translate.marker_repair"),
                                context_minimum=context_bucket,
                                context_maximum=context_bucket,
                                context_multiplier=config.translation.context_multiplier,
                                max_attempts=1,
                            )
                        except StructuredOutputError:
                            if not flat_marker_selection:
                                raise
                            generation_calls += 1
                            placement_schema = InlineMarkerPlacementResult
                            flat_marker_selection = False
                            placement = client.generate_structured(
                                build_inline_marker_placement_prompt(
                                    repair_chunk, repair_translations
                                ),
                                placement_schema,
                                think=config.translation.thinking,
                                model=active_model,
                                progress_label=(
                                    f"chunk={chunk_index}/{total_chunks} "
                                    f"id={chunk.chunk_id} marker-repair=1/1 "
                                    "mode=structured-placement-fallback"
                                ),
                                **llm_role_kwargs(client, "translate.marker_repair"),
                                context_minimum=context_bucket,
                                context_maximum=context_bucket,
                                context_multiplier=config.translation.context_multiplier,
                                max_attempts=1,
                            )
                        placement_path = attempt_root / (
                            f"{chunk.chunk_id}.marker-repair-{attempt:03d}-"
                            f"{_safe_model_name(active_model)}.json"
                        )
                        atomic_write_text(placement_path, placement.generation.content)
                        repaired_translations = (
                            apply_single_inline_marker_selections(
                                repair_chunk,
                                repair_translations,
                                placement.value,
                            )
                            if single_marker_selection
                            else (
                                apply_inline_marker_selections(
                                    repair_chunk,
                                    repair_translations,
                                    placement.value,
                                )
                                if flat_marker_selection
                                else apply_inline_marker_placements(
                                    repair_chunk,
                                    repair_translations,
                                    placement.value,
                                )
                            )
                        )
                        marker_validation_name = "structured_marker_placement"
                        marker_metrics = {
                            **asdict(placement.generation.metrics),
                            "model": active_model,
                            "purpose": marker_validation_name,
                        }
                repaired_output = _render_chunk_translations(
                    repair_chunk, repaired_translations
                )
                translations, validation = validate_translation_output(
                    repaired_output,
                    repair_chunk,
                    config.translation.direction,
                    relevant_glossary,
                )
                record_validation(
                    connection,
                    chunk.chunk_id,
                    marker_validation_name,
                    validation.passed,
                    details=validation.model_dump(mode="json"),
                )
                record_attempt(
                    connection,
                    f"{chunk.chunk_id}:{marker_attempt_name}",
                    WorkflowStage.TRANSLATE.value,
                    1,
                    StageStatus.COMPLETED if validation.passed else StageStatus.FAILED,
                    message=(
                        ""
                        if validation.passed
                        else "; ".join(issue.message for issue in validation.issues)
                    ),
                    metrics=marker_metrics,
                )
            except Exception as marker_error:
                record_attempt(
                    connection,
                    f"{chunk.chunk_id}:{marker_attempt_name}",
                    WorkflowStage.TRANSLATE.value,
                    1,
                    StageStatus.FAILED,
                    message=str(marker_error),
                    metrics={
                        "model": active_model,
                        "purpose": marker_purpose,
                    },
                )
        if validation.passed:
            accepted_translations.update(translations)
            complete_output = _render_chunk_translations(chunk, accepted_translations)
            translations, validation = validate_translation_output(
                complete_output,
                chunk,
                config.translation.direction,
                relevant_glossary,
            )
            if not validation.passed:
                raise TranslationOutputError(
                    f"internal merged translation failed validation: {chunk.chunk_id}"
                )
            record_attempt(
                connection,
                chunk.chunk_id,
                WorkflowStage.TRANSLATE.value,
                attempt,
                StageStatus.COMPLETED,
                metrics=metrics,
            )
            if (
                active_model != config.ollama.model
                and config.translation.harmonize_fallback_with_primary
            ):
                harmonized = client.generate_text(
                    _build_harmonization_prompt(
                        _build_chunk_prompt(
                            document, chunk, relevant_glossary, config
                        ),
                        complete_output,
                    ),
                    stream=True,
                    think=config.translation.thinking,
                    model=config.ollama.model,
                    progress_label=(
                        f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
                        "harmonize=1/1 mode=fallback-style"
                    ),
                    **llm_role_kwargs(client, "translate.fallback_harmonization"),
                    context_maximum=config.translation.max_num_ctx,
                    context_multiplier=config.translation.context_multiplier,
                )
                generation_calls += 1
                harmonized_path = attempt_root / (
                    f"{chunk.chunk_id}.attempt-{attempt:03d}-harmonized-"
                    f"{_safe_model_name(config.ollama.model)}.txt"
                )
                atomic_write_text(harmonized_path, harmonized.content)
                harmonized_translations, harmonized_validation = (
                    validate_translation_output(
                        harmonized.content,
                        chunk,
                        config.translation.direction,
                        relevant_glossary,
                    )
                )
                record_validation(
                    connection,
                    chunk.chunk_id,
                    "fallback_style_harmonization",
                    harmonized_validation.passed,
                    details=harmonized_validation.model_dump(mode="json"),
                )
                record_attempt(
                    connection,
                    f"{chunk.chunk_id}:harmonize",
                    WorkflowStage.TRANSLATE.value,
                    1,
                    (
                        StageStatus.COMPLETED
                        if harmonized_validation.passed
                        else StageStatus.FAILED
                    ),
                    message=(
                        ""
                        if harmonized_validation.passed
                        else "; ".join(
                            issue.message
                            for issue in harmonized_validation.issues
                        )
                    ),
                    metrics={
                        **asdict(harmonized.metrics),
                        "model": config.ollama.model,
                        "purpose": "fallback_style_harmonization",
                    },
                )
                if harmonized_validation.passed:
                    translations = harmonized_translations
                    validation = harmonized_validation
            translated = TranslatedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                translations=translations,
                validation=validation,
            )
            atomic_write_text(chunk_path, translated.model_dump_json(indent=2))
            output_hash = sha256_file(chunk_path)
            set_work_unit_status(
                connection,
                chunk.chunk_id,
                WorkflowStage.TRANSLATE.value,
                "chunk",
                StageStatus.COMPLETED,
                parent_id=chunk.document_id,
                attempts=attempt,
                input_hash=chunk_input_hash,
                output_hash=output_hash,
                validation=validation.model_dump(mode="json"),
            )
            return translated, generation_calls
        _retain_valid_translations(
            accepted_translations,
            translations,
            validation,
        )
        pending_chunk = _build_retry_chunk(chunk, accepted_translations, validation)
        _report_validation_failure(
            client,
            active_model,
            progress_label,
            validation,
            retrying=offset < allowed_attempts,
        )
        last_validation = validation
        message = "; ".join(issue.message for issue in validation.issues)
        set_work_unit_status(
            connection,
            chunk.chunk_id,
            WorkflowStage.TRANSLATE.value,
            "chunk",
            StageStatus.FAILED,
            parent_id=chunk.document_id,
            attempts=attempt,
            input_hash=chunk_input_hash,
            validation=validation.model_dump(mode="json"),
            message=message,
        )
        record_attempt(
            connection,
            chunk.chunk_id,
            WorkflowStage.TRANSLATE.value,
            attempt,
            StageStatus.FAILED,
            message=message,
            metrics=metrics,
        )
    if config.workflow.defer_failed_translation_segments:
        return _defer_failed_chunk(
            connection,
            chunk,
            chunk_input_hash,
            chunk_path,
            accepted_translations,
            last_validation,
            existing_attempts + allowed_attempts,
            generation_calls,
            client,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
    raise TranslationOutputError(
        f"chunk {chunk.chunk_id} failed validation after {allowed_attempts} attempts"
    )


def _defer_failed_chunk(
    connection,
    chunk,
    chunk_input_hash,
    chunk_path,
    accepted_translations: dict[str, str],
    last_validation,
    attempts: int,
    generation_calls: int,
    client,
    *,
    chunk_index: int,
    total_chunks: int,
):
    """Checkpoint exhausted passages for downstream audit instead of aborting the book."""
    deferred_pieces = [
        piece
        for piece in chunk.pieces
        if piece.reference_id not in accepted_translations
    ]
    if not deferred_pieces:
        raise TranslationOutputError(
            f"chunk {chunk.chunk_id} exhausted retries without a deferrable passage"
        )
    translations = dict(accepted_translations)
    for piece in deferred_pieces:
        # Preserve the exact source and marker contract. The audit stage treats this
        # as an explicit high-severity repair target before compilation can proceed.
        translations[piece.reference_id] = piece.source_text
    original_issues = list(last_validation.issues) if last_validation else []
    deferred_issues = [
        TranslationIssue(
            code="deferred_translation",
            reference_id=piece.reference_id,
            message="translation retries exhausted; source retained for downstream repair",
        )
        for piece in deferred_pieces
    ]
    if last_validation is None:
        raise TranslationOutputError(
            f"chunk {chunk.chunk_id} exhausted retries without validation details"
        )
    validation = TranslationValidation(
        passed=False,
        issues=[*original_issues, *deferred_issues],
    )
    translated = TranslatedChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        translations=translations,
        validation=validation,
    )
    atomic_write_text(chunk_path, translated.model_dump_json(indent=2))
    output_hash = sha256_file(chunk_path)
    message = (
        f"deferred {len(deferred_pieces)} passage(s) for audit/repair after "
        f"{attempts} attempt(s)"
    )
    set_work_unit_status(
        connection,
        chunk.chunk_id,
        WorkflowStage.TRANSLATE.value,
        "chunk",
        StageStatus.COMPLETED,
        parent_id=chunk.document_id,
        attempts=attempts,
        input_hash=chunk_input_hash,
        output_hash=output_hash,
        validation=validation.model_dump(mode="json"),
        message=message,
    )
    _report_deferred_translation(
        client,
        chunk,
        deferred_pieces,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )
    return translated, generation_calls


def _report_deferred_translation(
    client,
    chunk,
    deferred_pieces,
    *,
    chunk_index: int,
    total_chunks: int,
) -> None:
    report = getattr(client, "report_progress", None)
    if not callable(report):
        return
    for piece in deferred_pieces:
        report(
            GenerationProgressEvent(
                "segment_result",
                "deterministic",
                label=(
                    f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
                    f"segment={piece.segment_id} stage=translate"
                ),
                message=(
                    "result=pending-repair; reason=translation validation retries "
                    "exhausted; source preserved"
                ),
            )
        )


def _retain_valid_translations(
    accepted: dict[str, str],
    translations: dict[str, str],
    validation,
) -> None:
    """Checkpoint valid passages from a structurally parseable partial failure."""
    invalid_ids = {
        issue.reference_id for issue in validation.issues if issue.reference_id
    }
    if not invalid_ids:
        return
    for reference_id, translated in translations.items():
        if reference_id not in invalid_ids:
            accepted[reference_id] = translated


def _build_retry_chunk(chunk, accepted: dict[str, str], validation):
    """Limit the next expensive model call to passages that still need correction."""
    invalid_ids = {
        issue.reference_id for issue in validation.issues if issue.reference_id
    }
    if not invalid_ids:
        return chunk
    pending = [
        piece
        for piece in chunk.pieces
        if piece.reference_id not in accepted
    ]
    return chunk.model_copy(update={"pieces": pending}) if pending else chunk


def _render_chunk_translations(chunk, translations: dict[str, str]) -> str:
    """Render translations in the chunk's canonical outer-marker order."""
    missing = [
        piece.reference_id
        for piece in chunk.pieces
        if piece.reference_id not in translations
    ]
    if missing:
        raise TranslationOutputError(
            f"merged translation is missing passages: {', '.join(missing)}"
        )
    return "\n".join(
        f"<{piece.reference_id}>{translations[piece.reference_id]}</{piece.reference_id}>"
        for piece in chunk.pieces
    )


def _report_validation_failure(
    client,
    model: str,
    progress_label: str,
    validation,
    *,
    retrying: bool,
) -> None:
    """Expose contract retry status without coupling stages to the CLI."""
    report = getattr(client, "report_progress", None)
    if not callable(report):
        return
    codes = sorted({issue.code for issue in validation.issues})
    disposition = "retrying" if retrying else "no attempts remaining"
    report(
        GenerationProgressEvent(
            "validation_failed",
            model,
            label=progress_label,
            message=f"{','.join(codes) or 'unknown validation error'}; {disposition}",
        )
    )


def _report_deterministic_marker_restoration(
    client,
    progress_label: str,
    passage_count: int,
) -> None:
    """Expose successful zero-cost marker recovery in the per-segment log."""
    report = getattr(client, "report_progress", None)
    if not callable(report):
        return
    report(
        GenerationProgressEvent(
            "segment_result",
            "deterministic",
            label=progress_label,
            message=(
                f"result=succeeded; passages={passage_count}; "
                "protected markers restored"
            ),
        )
    )


def _report_cached_marker_placement(client, progress_label: str) -> None:
    """Expose successful reuse of a previously generated placement."""
    report = getattr(client, "report_progress", None)
    if not callable(report):
        return
    report(
        GenerationProgressEvent(
            "segment_result",
            "cached",
            label=progress_label,
            message="result=succeeded; saved marker placement reused",
        )
    )


def _safe_model_name(model: str) -> str:
    """Create a readable portable model component for diagnostic filenames."""
    safe = "".join(character if character.isalnum() else "-" for character in model)
    return safe.strip("-") or "model"


def _load_saved_marker_placement(attempt_root, chunk_id: str, schema):
    """Load the newest schema-compatible marker response for zero-cost replay."""
    candidates = sorted(
        attempt_root.glob(f"{chunk_id}.marker-repair-*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in candidates:
        try:
            return schema.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _load_repairable_saved_attempt(
    attempt_root, chunk, direction, relevant_glossary
) -> str | None:
    """Reuse the saved draft with the largest safe repairable passage set."""
    candidates = sorted(
        attempt_root.glob(f"{chunk.chunk_id}.attempt-*.txt"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    best_content: str | None = None
    best_translation_count = 0
    for path in candidates:
        content = path.read_text(encoding="utf-8")
        translations, validation = validate_translation_output(
            content,
            chunk,
            direction,
            relevant_glossary,
        )
        if validation.passed:
            return content
        if (
            translations
            and not validation.passed
            and validation.issues
            and all(
                issue.code in {"protected_marker_mismatch", "marker_contract"}
                for issue in validation.issues
            )
            and len(translations) > best_translation_count
        ):
            best_content = content
            best_translation_count = len(translations)
    return best_content


def _build_harmonization_prompt(base_prompt: str, fallback_draft: str) -> str:
    """Ask the primary translator to align only a validated fallback draft's prose."""
    return (
        f"{base_prompt}\n\n"
        "Style-harmonization task:\n"
        "A fallback translator produced the complete structurally valid draft below. "
        "Revise its target-language prose to match the style, glossary, naturalness, and "
        "de-AI instructions above. Preserve its meaning and completeness. Copy every outer "
        "and inline marker exactly; do not add or remove markers. Output only the complete "
        "harmonized marked translation.\n\n"
        f"Fallback draft:\n{fallback_draft}"
    )


def _load_current_chunk(existing, path, input_hash):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    return TranslatedChunk.model_validate_json(path.read_text(encoding="utf-8"))


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.TRANSLATE.value,
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
    previous = get_stage_status(connection, WorkflowStage.TRANSLATE.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.TRANSLATE.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
    apply_inline_marker_placements,
    build_inline_marker_placement_prompt,
