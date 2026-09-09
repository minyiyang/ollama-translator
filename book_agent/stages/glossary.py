"""Resumable glossary extraction, resolution, and approval stages."""

from __future__ import annotations

from pathlib import Path

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..glossary import (
    GlossaryChunk,
    GlossarySource,
    GlossarySourceKind,
    analyze_glossary_quality,
    build_glossary_approval_batches,
    build_glossary_approval_cases,
    build_glossary_chunks,
    build_glossary_resolution_cases,
    build_glossary_resolution_batches,
    canonicalize_candidate_evidence,
    load_glossary_file,
    merge_candidate_entries,
    merge_prioritized_sources,
    restore_glossary_evidence,
    screen_glossary_approval_cases,
    screen_glossary_candidates,
    screen_glossary_documents,
    sort_glossary_entries,
    validate_candidate_evidence,
    validate_resolution_scope,
)
from ..glossary_prompts import (
    build_conflict_resolution_prompt,
    build_approval_review_prompt,
    build_extraction_prompt,
    build_resolution_prompt,
)
from ..hashing import hash_named_values, sha256_file, sha256_text
from ..ollama_client import OllamaClient, StructuredGenerationResult
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..schemas import (
    GlossaryApprovalAction,
    GlossaryApprovalRecord,
    GlossaryApprovalReport,
    GlossaryApprovalResult,
    GlossaryEntry,
    GlossaryResolutionResult,
    GlossaryResult,
    build_glossary_approval_schema,
    build_glossary_extraction_schema,
    build_glossary_resolution_schema,
    normalize_term,
    render_legacy_glossary,
)
from ..stage_progress import (
    group_by_context_bucket,
    llm_role_kwargs,
    report_stage_plan,
    report_segment_result,
    request_context_bucket,
)
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    get_work_unit,
    initialize_state,
    record_artifact,
    record_attempt,
    set_job_metadata,
    set_stage_status,
    set_work_unit_status,
)
from ..workspace import JobWorkspace
from .decompile import load_decompile_manifest


EXTRACTION_STAGE_VERSION = "9"
RESOLUTION_STAGE_VERSION = "7"
APPROVAL_STAGE_VERSION = "10"


class GlossaryApprovalRequired(RuntimeError):
    """Human review is required before the approved glossary can be published."""


def load_approved_glossary(workspace: JobWorkspace) -> GlossaryResult:
    """Load the canonical approved glossary for downstream stages."""
    connection = connect_state(workspace.state_file)
    try:
        return _load_recorded_result(workspace, connection, "glossary_approved")
    finally:
        connection.close()


def load_glossary_draft(workspace: JobWorkspace) -> GlossaryResult:
    """Load the resolved, not-yet-approved glossary for series preparation."""
    connection = connect_state(workspace.state_file)
    try:
        return _load_recorded_result(workspace, connection, "glossary_draft")
    finally:
        connection.close()


def run_glossary_extraction_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None,
) -> GlossaryResult:
    """Extract structured candidates from every decompiled text segment."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        decompile = _require_completed(connection, WorkflowStage.DECOMPILE)
        if not config.glossary.extraction_enabled:
            input_hash = build_stage_input_hash(
                {
                    "decompile": str(decompile["output_hash"]),
                    "extraction_enabled": "false",
                    "stage_version": EXTRACTION_STAGE_VERSION,
                }
            )
            if stage_is_current(
                connection,
                WorkflowStage.EXTRACT_GLOSSARY,
                input_hash,
                artifact_root=workspace.root,
            ):
                return _load_recorded_result(
                    workspace, connection, "glossary_candidates"
                )
            previous = get_stage_status(
                connection, WorkflowStage.EXTRACT_GLOSSARY.value
            )
            if previous and previous["status"] == StageStatus.COMPLETED.value:
                invalidate_stage_and_dependents(
                    connection, WorkflowStage.EXTRACT_GLOSSARY
                )
                previous = get_stage_status(
                    connection, WorkflowStage.EXTRACT_GLOSSARY.value
                )
            stage_attempt = int(previous["attempts"]) + 1 if previous else 1
            set_stage_status(
                connection,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                StageStatus.RUNNING,
                attempts=stage_attempt,
                input_hash=input_hash,
                message="configured glossary reuse; extraction disabled",
            )
            connection.execute(
                "DELETE FROM work_units WHERE stage = ?",
                (WorkflowStage.EXTRACT_GLOSSARY.value,),
            )
            connection.commit()
            stage_root = workspace.directory(
                f"glossary/extraction-{input_hash[:16]}"
            )
            stage_root.mkdir(parents=True, exist_ok=True)
            merged = GlossaryResult(entries=[])
            merged_path = stage_root / "candidates.merged.json"
            atomic_write_text(merged_path, merged.model_dump_json(indent=2))
            _record_file(
                connection,
                workspace,
                merged_path,
                WorkflowStage.EXTRACT_GLOSSARY,
                "merged_candidates",
            )
            output_hash = build_stage_output_hash(
                connection, WorkflowStage.EXTRACT_GLOSSARY
            )
            set_job_metadata(
                connection,
                "glossary_candidates",
                merged_path.relative_to(workspace.root).as_posix(),
            )
            set_job_metadata(connection, "glossary_extraction_mode", "disabled")
            set_stage_status(
                connection,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                StageStatus.COMPLETED,
                attempts=stage_attempt,
                input_hash=input_hash,
                output_hash=output_hash,
                message="configured glossary reuse; no LLM extraction",
            )
            return merged
        if client is None:
            raise ValueError("an Ollama client is required for glossary extraction")
        manifest = load_decompile_manifest(workspace, connection=connection)
        eligible_documents, document_screening = screen_glossary_documents(
            manifest.documents
        )
        chunks = build_glossary_chunks(
            eligible_documents,
            config.glossary.extraction_chunk_tokens,
            max_documents=config.glossary.extraction_chapters_per_chunk,
        )
        input_hash = build_stage_input_hash(
            {
                "decompile": str(decompile["output_hash"]),
                "direction": config.translation.direction.value,
                "model": config.glossary.extraction_model,
                "thinking": str(config.glossary.extraction_thinking),
                "min_num_ctx": str(config.glossary.extraction_min_num_ctx),
                "max_num_ctx": str(config.glossary.extraction_max_num_ctx),
                "context_multiplier": str(
                    config.glossary.extraction_context_multiplier
                ),
                "max_entries": str(config.glossary.extraction_max_entries),
                "max_evidence_per_entry": str(
                    config.glossary.extraction_max_evidence_per_entry
                ),
                "chapters_per_chunk": str(
                    config.glossary.extraction_chapters_per_chunk
                ),
                "chunk_tokens": str(config.glossary.extraction_chunk_tokens),
                "stage_version": EXTRACTION_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.EXTRACT_GLOSSARY,
            input_hash,
            artifact_root=workspace.root,
        ):
            return _load_recorded_result(workspace, connection, "glossary_candidates")

        previous = get_stage_status(connection, WorkflowStage.EXTRACT_GLOSSARY.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.EXTRACT_GLOSSARY)
            previous = get_stage_status(connection, WorkflowStage.EXTRACT_GLOSSARY.value)
        stage_attempt = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.EXTRACT_GLOSSARY.value,
            StageStatus.RUNNING,
            attempts=stage_attempt,
            input_hash=input_hash,
        )
        stage_root = workspace.directory(f"glossary/extraction-{input_hash[:16]}")
        chunks_root = stage_root / "chunks"
        candidates_root = stage_root / "candidates"
        chunks_root.mkdir(parents=True, exist_ok=True)
        candidates_root.mkdir(parents=True, exist_ok=True)
        document_screening_path = stage_root / "documents.screening.report.json"
        atomic_write_text(
            document_screening_path,
            document_screening.model_dump_json(indent=2),
        )
        _record_file(
            connection,
            workspace,
            document_screening_path,
            WorkflowStage.EXTRACT_GLOSSARY,
            "glossary_document_screening_report",
        )
        results_by_chunk: dict[str, GlossaryResult] = {}
        extraction_tasks = []
        total_chunks = len(chunks)
        for chunk in chunks:
            chunk_path = chunks_root / f"{chunk.chunk_id}.json"
            atomic_write_text(chunk_path, chunk.model_dump_json(indent=2))
            _record_file(connection, workspace, chunk_path, WorkflowStage.EXTRACT_GLOSSARY, "glossary_chunk")
            prompt = build_extraction_prompt(
                chunk,
                config.translation.direction,
                max_entries=config.glossary.extraction_max_entries,
                max_evidence_per_entry=(
                    config.glossary.extraction_max_evidence_per_entry
                ),
            )
            unit_input_hash = hash_named_values(
                {
                    "chunk": sha256_text(chunk.model_dump_json()),
                    "prompt": sha256_text(prompt),
                    "model": config.glossary.extraction_model,
                    "thinking": str(config.glossary.extraction_thinking),
                    "min_num_ctx": str(config.glossary.extraction_min_num_ctx),
                    "max_num_ctx": str(config.glossary.extraction_max_num_ctx),
                    "context_multiplier": str(
                        config.glossary.extraction_context_multiplier
                    ),
                }
            )
            candidate_path = candidates_root / f"{chunk.chunk_id}.json"
            existing = get_work_unit(
                connection, chunk.chunk_id, WorkflowStage.EXTRACT_GLOSSARY.value
            )
            if (
                existing
                and existing["status"] == StageStatus.COMPLETED.value
                and existing["input_hash"] == unit_input_hash
                and candidate_path.is_file()
                and sha256_file(candidate_path) == existing["output_hash"]
            ):
                result = GlossaryResult.model_validate_json(
                    candidate_path.read_text(encoding="utf-8")
                )
                validate_candidate_evidence(result, chunk)
                results_by_chunk[chunk.chunk_id] = result
                _record_file(
                    connection,
                    workspace,
                    candidate_path,
                    WorkflowStage.EXTRACT_GLOSSARY,
                    "glossary_candidates",
                )
                continue
            extraction_schema = build_glossary_extraction_schema(
                config.glossary.extraction_max_entries,
                config.glossary.extraction_max_evidence_per_entry,
                [piece.reference_id for piece in chunk.pieces],
            )
            bucket = (
                config.glossary.extraction_max_num_ctx
                if not config.ollama.adaptive_num_ctx
                else request_context_bucket(
                    prompt,
                    minimum=config.glossary.extraction_min_num_ctx,
                    maximum=config.glossary.extraction_max_num_ctx,
                    schema=extraction_schema,
                    multiplier=config.glossary.extraction_context_multiplier,
                )
            )
            extraction_tasks.append(
                {
                    "chunk": chunk,
                    "prompt": prompt,
                    "candidate_path": candidate_path,
                    "unit_input_hash": unit_input_hash,
                    "bucket": bucket,
                }
            )

        report_stage_plan(
            client,
            model=config.glossary.extraction_model,
            stage=WorkflowStage.EXTRACT_GLOSSARY.value,
            prescreened=total_chunks,
            llm_tasks=len(extraction_tasks),
            skipped=total_chunks - len(extraction_tasks),
            context_buckets=(task["bucket"] for task in extraction_tasks),
            role="glossary.extract",
        )
        ordered_tasks = group_by_context_bucket(
            extraction_tasks, lambda task: int(task["bucket"])
        )
        scheduled_total = len(ordered_tasks)
        for scheduled_index, task in enumerate(ordered_tasks, start=1):
            result = _extract_one_chunk(
                connection,
                task["chunk"],
                task["prompt"],
                task["candidate_path"],
                task["unit_input_hash"],
                config,
                client,
                chunk_index=scheduled_index,
                total_chunks=scheduled_total,
                context_bucket=int(task["bucket"]),
            )
            _record_file(
                connection,
                workspace,
                task["candidate_path"],
                WorkflowStage.EXTRACT_GLOSSARY,
                "glossary_candidates",
            )
            results_by_chunk[task["chunk"].chunk_id] = result

        collected = [
            entry
            for chunk in chunks
            for entry in results_by_chunk[chunk.chunk_id].entries
        ]

        raw_merged = GlossaryResult(entries=merge_candidate_entries(collected))
        raw_merged_path = stage_root / "candidates.raw.merged.json"
        atomic_write_text(raw_merged_path, raw_merged.model_dump_json(indent=2))
        _record_file(
            connection,
            workspace,
            raw_merged_path,
            WorkflowStage.EXTRACT_GLOSSARY,
            "raw_merged_candidates",
        )
        merged, screening = screen_glossary_candidates(
            raw_merged,
            [piece for chunk in chunks for piece in chunk.pieces],
        )
        merged_path = stage_root / "candidates.merged.json"
        screening_path = stage_root / "candidates.screening.report.json"
        atomic_write_text(merged_path, merged.model_dump_json(indent=2))
        atomic_write_text(screening_path, screening.model_dump_json(indent=2))
        _record_file(
            connection, workspace, merged_path, WorkflowStage.EXTRACT_GLOSSARY, "merged_candidates"
        )
        _record_file(
            connection,
            workspace,
            screening_path,
            WorkflowStage.EXTRACT_GLOSSARY,
            "glossary_screening_report",
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.EXTRACT_GLOSSARY)
        set_job_metadata(
            connection,
            "glossary_candidates",
            merged_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "glossary_candidate_screening",
            screening_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "glossary_document_screening",
            document_screening_path.relative_to(workspace.root).as_posix(),
        )
        set_stage_status(
            connection,
            WorkflowStage.EXTRACT_GLOSSARY.value,
            StageStatus.COMPLETED,
            attempts=stage_attempt,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        return merged
    except Exception as error:
        _mark_stage_failed(connection, WorkflowStage.EXTRACT_GLOSSARY, error)
        raise
    finally:
        connection.close()


def run_glossary_resolution_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None,
) -> GlossaryResult:
    """Resolve candidate conflicts, apply configured precedence, and publish a draft."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        extraction = _require_completed(connection, WorkflowStage.EXTRACT_GLOSSARY)
        candidates = _load_recorded_result(workspace, connection, "glossary_candidates")
        configured_sources, source_hashes = load_configured_glossary_sources(config)
        locked_entries = merge_prioritized_sources(configured_sources)
        locked_terms = {normalize_term(entry.english) for entry in locked_entries}
        unresolved = [
            entry for entry in candidates.entries if normalize_term(entry.english) not in locked_terms
        ]
        input_values = {
            "extraction": str(extraction["output_hash"]),
            "direction": config.translation.direction.value,
            "model": config.ollama.model,
            "chunk_tokens": str(config.glossary.resolution_chunk_tokens),
            "min_num_ctx": str(config.glossary.resolution_min_num_ctx),
            "max_num_ctx": str(config.glossary.resolution_max_num_ctx),
            "context_multiplier": str(config.glossary.resolution_context_multiplier),
            "candidates": sha256_text(
                GlossaryResult(entries=unresolved).model_dump_json()
            ),
            "stage_version": RESOLUTION_STAGE_VERSION,
            **source_hashes,
        }
        input_hash = build_stage_input_hash(input_values)
        if stage_is_current(
            connection,
            WorkflowStage.RESOLVE_GLOSSARY,
            input_hash,
            artifact_root=workspace.root,
        ):
            return _load_recorded_result(workspace, connection, "glossary_draft")
        previous = get_stage_status(connection, WorkflowStage.RESOLVE_GLOSSARY.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.RESOLVE_GLOSSARY)
            previous = get_stage_status(connection, WorkflowStage.RESOLVE_GLOSSARY.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.RESOLVE_GLOSSARY.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )
        stage_root = workspace.directory(f"glossary/resolution-{input_hash[:16]}")
        stage_root.mkdir(parents=True, exist_ok=True)
        if unresolved:
            if client is None:
                raise ValueError("an Ollama client is required for glossary resolution")
            resolved_entries = _resolve_candidates(
                connection,
                unresolved,
                config,
                client,
                workspace,
                stage_root,
                input_hash,
            )
        else:
            resolved_entries = []
        all_sources = configured_sources + [
            GlossarySource(
                "resolved-candidates",
                GlossarySourceKind.EXTRACTED,
                tuple(sort_glossary_entries(resolved_entries)),
            )
        ]
        draft = GlossaryResult(entries=merge_prioritized_sources(all_sources))
        quality = analyze_glossary_quality(
            draft,
            scope="series" if config.glossary.series_glossaries else "volume",
        )
        json_path = stage_root / "glossary.draft.json"
        text_path = stage_root / "glossary.draft.txt"
        quality_path = stage_root / "glossary.draft.quality.report.json"
        atomic_write_text(json_path, draft.model_dump_json(indent=2))
        atomic_write_text(text_path, render_legacy_glossary(draft.entries))
        atomic_write_text(quality_path, quality.model_dump_json(indent=2))
        _record_file(connection, workspace, json_path, WorkflowStage.RESOLVE_GLOSSARY, "glossary_draft_json")
        _record_file(connection, workspace, text_path, WorkflowStage.RESOLVE_GLOSSARY, "glossary_draft_legacy")
        _record_file(connection, workspace, quality_path, WorkflowStage.RESOLVE_GLOSSARY, "glossary_draft_quality_report")
        output_hash = build_stage_output_hash(connection, WorkflowStage.RESOLVE_GLOSSARY)
        set_job_metadata(connection, "glossary_draft", json_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "glossary_draft_legacy", text_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "glossary_draft_quality_report", quality_path.relative_to(workspace.root).as_posix())
        set_stage_status(
            connection,
            WorkflowStage.RESOLVE_GLOSSARY.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        if config.workflow.require_glossary_review:
            set_stage_status(
                connection,
                WorkflowStage.APPROVE_GLOSSARY.value,
                StageStatus.PAUSED,
                message="human glossary review required",
            )
        return draft
    except Exception as error:
        _mark_stage_failed(connection, WorkflowStage.RESOLVE_GLOSSARY, error)
        raise
    finally:
        connection.close()


def run_glossary_approval_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    *,
    reviewed_file: str | Path | None = None,
    llm_review: bool = False,
    client: OllamaClient | None = None,
) -> GlossaryResult:
    """Validate human/LLM review or auto-approve and publish final glossary files."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        resolution = _require_completed(connection, WorkflowStage.RESOLVE_GLOSSARY)
        use_llm_review = llm_review or config.workflow.llm_glossary_review
        if (
            config.workflow.require_glossary_review
            and reviewed_file is None
            and not use_llm_review
        ):
            set_stage_status(
                connection,
                WorkflowStage.APPROVE_GLOSSARY.value,
                StageStatus.PAUSED,
                message="reviewed_file is required",
            )
            raise GlossaryApprovalRequired("a reviewed glossary file is required")
        review_model = ""
        approval_report: GlossaryApprovalReport | None = None
        draft = _load_recorded_result(workspace, connection, "glossary_draft")
        candidate = draft
        review_source = "resolved"
        if reviewed_file is not None:
            candidate = load_glossary_file(reviewed_file)
            reviewed_hash = sha256_file(reviewed_file)
            review_source = "external"
        else:
            reviewed_hash = sha256_text(draft.model_dump_json())
        if use_llm_review:
            if client is None:
                raise ValueError("an Ollama client is required for LLM glossary review")
            review_model = config.ollama.model
            review_mode = "llm"
        elif reviewed_file is not None:
            approved = candidate
            review_mode = "human"
        else:
            approved = draft
            reviewed_hash = str(resolution["output_hash"])
            review_mode = "none"
        input_hash = build_stage_input_hash(
            {
                "resolution": str(resolution["output_hash"]),
                "reviewed": reviewed_hash,
                "review_mode": review_mode,
                "review_source": review_source,
                "review_model": review_model,
                "chunk_tokens": (
                    str(config.glossary.approval_chunk_tokens)
                    if use_llm_review
                    else ""
                ),
                "min_num_ctx": (
                    str(config.glossary.approval_min_num_ctx)
                    if use_llm_review
                    else ""
                ),
                "max_num_ctx": (
                    str(config.glossary.approval_max_num_ctx)
                    if use_llm_review
                    else ""
                ),
                "context_multiplier": (
                    str(config.glossary.approval_context_multiplier)
                    if use_llm_review
                    else ""
                ),
                "auto_approve_min_confidence": (
                    str(config.glossary.approval_auto_approve_min_confidence)
                    if use_llm_review
                    else ""
                ),
                "stage_version": APPROVAL_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.APPROVE_GLOSSARY,
            input_hash,
            artifact_root=workspace.root,
        ):
            return _load_recorded_result(workspace, connection, "glossary_approved")
        previous = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.APPROVE_GLOSSARY)
            previous = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.APPROVE_GLOSSARY.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )
        stage_root = workspace.directory(f"glossary/approved-{input_hash[:16]}")
        stage_root.mkdir(parents=True, exist_ok=True)
        if use_llm_review:
            approved, approval_report = _review_glossary(
                connection,
                candidate.entries,
                config,
                client,
                workspace,
                stage_root,
                input_hash,
            )
        approved = restore_glossary_evidence(
            approved,
            candidate.entries + draft.entries,
            max_evidence_per_entry=config.glossary.extraction_max_evidence_per_entry,
        )
        approved = GlossaryResult(entries=sort_glossary_entries(approved.entries))
        quality = analyze_glossary_quality(
            approved,
            scope="series" if config.glossary.series_glossaries else "volume",
        )
        json_path = stage_root / "glossary.approved.json"
        text_path = stage_root / "glossary.approved.txt"
        quality_path = stage_root / "glossary.quality.report.json"
        atomic_write_text(json_path, approved.model_dump_json(indent=2))
        atomic_write_text(text_path, render_legacy_glossary(approved.entries))
        atomic_write_text(quality_path, quality.model_dump_json(indent=2))
        _record_file(connection, workspace, json_path, WorkflowStage.APPROVE_GLOSSARY, "approved_glossary_json")
        _record_file(connection, workspace, text_path, WorkflowStage.APPROVE_GLOSSARY, "approved_glossary_legacy")
        _record_file(connection, workspace, quality_path, WorkflowStage.APPROVE_GLOSSARY, "glossary_quality_report")
        if approval_report is not None:
            approval_report_path = stage_root / "glossary.approval.report.json"
            atomic_write_text(
                approval_report_path, approval_report.model_dump_json(indent=2)
            )
            _record_file(
                connection,
                workspace,
                approval_report_path,
                WorkflowStage.APPROVE_GLOSSARY,
                "glossary_approval_report",
            )
            set_job_metadata(
                connection,
                "glossary_approval_report",
                approval_report_path.relative_to(workspace.root).as_posix(),
            )
        output_hash = build_stage_output_hash(connection, WorkflowStage.APPROVE_GLOSSARY)
        set_job_metadata(connection, "glossary_approved", json_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "glossary_approved_legacy", text_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "glossary_review_mode", review_mode)
        set_job_metadata(connection, "glossary_review_source", review_source)
        set_job_metadata(
            connection,
            "glossary_quality_report",
            quality_path.relative_to(workspace.root).as_posix(),
        )
        set_stage_status(
            connection,
            WorkflowStage.APPROVE_GLOSSARY.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        return approved
    except GlossaryApprovalRequired:
        raise
    except Exception as error:
        _mark_stage_failed(connection, WorkflowStage.APPROVE_GLOSSARY, error)
        raise
    finally:
        connection.close()


def load_configured_glossary_sources(
    config: AppConfig,
) -> tuple[list[GlossarySource], dict[str, str]]:
    """Load configured glossary files and return sources plus named content hashes."""
    sources: list[GlossarySource] = []
    hashes: dict[str, str] = {}
    groups = (
        (GlossarySourceKind.SEED, config.glossary.seed_glossaries),
        (GlossarySourceKind.SERIES, config.glossary.series_glossaries),
        (GlossarySourceKind.BOOK, config.glossary.book_glossaries),
    )
    for kind, paths in groups:
        for index, path in enumerate(paths):
            resolved = Path(path).resolve()
            result = load_glossary_file(resolved)
            name = f"{kind.value}-{index:03d}-{resolved.name}"
            sources.append(GlossarySource(name, kind, tuple(result.entries)))
            hashes[f"glossary:{name}"] = sha256_file(resolved)
    return sources, hashes


def _extract_one_chunk(
    connection,
    chunk: GlossaryChunk,
    prompt: str,
    candidate_path: Path,
    input_hash: str,
    config: AppConfig,
    client: OllamaClient,
    *,
    chunk_index: int,
    total_chunks: int,
    context_bucket: int,
) -> GlossaryResult:
    extraction_schema = build_glossary_extraction_schema(
        config.glossary.extraction_max_entries,
        config.glossary.extraction_max_evidence_per_entry,
        [piece.reference_id for piece in chunk.pieces],
    )
    previous = get_work_unit(
        connection, chunk.chunk_id, WorkflowStage.EXTRACT_GLOSSARY.value
    )
    starting_attempt = int(previous["attempts"]) if previous else 0
    last_error: Exception | None = None
    for offset in range(1, config.workflow.max_retries + 2):
        attempt = starting_attempt + offset
        set_work_unit_status(
            connection,
            chunk.chunk_id,
            WorkflowStage.EXTRACT_GLOSSARY.value,
            "glossary_chunk",
            StageStatus.RUNNING,
            attempts=attempt,
            input_hash=input_hash,
        )
        try:
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt += (
                    "\n\nThe previous response failed validation: "
                    f"{last_error}. Use only evidence IDs visible in the supplied passage."
                )
            generated = client.generate_structured(
                attempt_prompt,
                extraction_schema,
                model=config.glossary.extraction_model,
                think=config.glossary.extraction_thinking,
                context_minimum=context_bucket,
                context_maximum=context_bucket,
                progress_label=(
                    f"chunk={chunk_index}/{total_chunks} id={chunk.chunk_id} "
                    f"attempt={offset}/{config.workflow.max_retries + 1}"
                ),
                **llm_role_kwargs(client, "glossary.extract"),
                context_multiplier=config.glossary.extraction_context_multiplier,
                max_attempts=1,
            )
            accepted: list[GlossaryEntry] = []
            discarded: list[str] = []
            for candidate in generated.value.entries:
                try:
                    accepted.append(GlossaryEntry.model_validate(candidate.model_dump()))
                except ValueError:
                    # Candidate extraction is intentionally recall-oriented. A model
                    # copying an ordinary English name into the Chinese field must not
                    # discard every other valid entry or trigger an identical retry.
                    discarded.append(candidate.english)
            extracted = GlossaryResult(entries=accepted)
            normalized = canonicalize_candidate_evidence(extracted, chunk)
            validate_candidate_evidence(normalized, chunk)
            result = GlossaryResult(entries=sort_glossary_entries(normalized.entries))
            atomic_write_text(candidate_path, result.model_dump_json(indent=2))
            output_hash = sha256_file(candidate_path)
            set_work_unit_status(
                connection,
                chunk.chunk_id,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                "glossary_chunk",
                StageStatus.COMPLETED,
                attempts=attempt,
                input_hash=input_hash,
                output_hash=output_hash,
                validation={
                    "evidence": "passed",
                    "entry_count": len(result.entries),
                    "discarded_invalid_entry_count": len(discarded),
                    "discarded_invalid_terms": discarded,
                },
            )
            record_attempt(
                connection,
                chunk.chunk_id,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                attempt,
                StageStatus.COMPLETED,
                metrics=_generation_metrics(generated),
            )
            return result
        except Exception as error:
            last_error = error
            record_attempt(
                connection,
                chunk.chunk_id,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                attempt,
                StageStatus.FAILED,
                message=str(error),
            )
            set_work_unit_status(
                connection,
                chunk.chunk_id,
                WorkflowStage.EXTRACT_GLOSSARY.value,
                "glossary_chunk",
                StageStatus.FAILED,
                attempts=attempt,
                input_hash=input_hash,
                message=str(error),
            )
    assert last_error is not None
    raise last_error


def _resolve_candidates(
    connection,
    candidates: list[GlossaryEntry],
    config: AppConfig,
    client: OllamaClient,
    workspace: JobWorkspace,
    stage_root: Path,
    stage_input_hash: str,
) -> list[GlossaryEntry]:
    local = _run_resolution_batches(
        connection,
        candidates,
        config,
        client,
        workspace,
        stage_root,
        stage_input_hash,
        pass_name="local",
        prompt_builder=build_resolution_prompt,
    )
    merged = merge_candidate_entries(local)
    conflicting_terms = _conflicting_source_terms(merged)
    if not conflicting_terms:
        return sort_glossary_entries(merged)
    conflicts = [
        entry
        for entry in merged
        if normalize_term(entry.english) in conflicting_terms
    ]
    consolidated = _run_resolution_batches(
        connection,
        conflicts,
        config,
        client,
        workspace,
        stage_root,
        stage_input_hash,
        pass_name="conflict",
        prompt_builder=build_conflict_resolution_prompt,
    )
    retained = [
        entry
        for entry in merged
        if normalize_term(entry.english) not in conflicting_terms
    ]
    return sort_glossary_entries(merge_candidate_entries(retained + consolidated))


def _run_resolution_batches(
    connection,
    candidates: list[GlossaryEntry],
    config: AppConfig,
    client: OllamaClient,
    workspace: JobWorkspace,
    stage_root: Path,
    stage_input_hash: str,
    *,
    pass_name: str,
    prompt_builder,
) -> list[GlossaryEntry]:
    batches = build_glossary_resolution_batches(
        candidates, config.glossary.resolution_chunk_tokens
    )
    results: dict[str, GlossaryResult] = {}
    tasks = []
    for batch_number, batch in enumerate(batches, start=1):
        unit_id = f"glossary-resolution-{pass_name}-{batch_number:05d}"
        prompt = prompt_builder(batch, config.translation.direction)
        cases = build_glossary_resolution_cases(batch)
        resolution_schema = build_glossary_resolution_schema(
            [str(case["term_id"]) for case in cases],
            max_decisions=len(batch),
        )
        unit_input_hash = hash_named_values(
            {
                "stage": stage_input_hash,
                "pass": pass_name,
                "prompt": sha256_text(prompt),
                "model": config.ollama.model,
                "min_num_ctx": str(config.glossary.resolution_min_num_ctx),
                "max_num_ctx": str(config.glossary.resolution_max_num_ctx),
                "context_multiplier": str(
                    config.glossary.resolution_context_multiplier
                ),
            }
        )
        path = stage_root / f"{unit_id}.json"
        existing = get_work_unit(
            connection, unit_id, WorkflowStage.RESOLVE_GLOSSARY.value
        )
        current = _load_current_glossary_batch(
            existing, path, unit_input_hash, batch
        )
        if current is not None:
            results[unit_id] = current
            _record_file(
                connection,
                workspace,
                path,
                WorkflowStage.RESOLVE_GLOSSARY,
                "glossary_resolution_batch",
            )
            continue
        bucket = (
            config.glossary.resolution_max_num_ctx
            if not config.ollama.adaptive_num_ctx
            else request_context_bucket(
                prompt,
                minimum=config.glossary.resolution_min_num_ctx,
                maximum=config.glossary.resolution_max_num_ctx,
                schema=resolution_schema,
                multiplier=config.glossary.resolution_context_multiplier,
            )
        )
        tasks.append(
            {
                "unit_id": unit_id,
                "batch": batch,
                "prompt": prompt,
                "unit_input_hash": unit_input_hash,
                "path": path,
                "existing": existing,
                "bucket": bucket,
                "schema": resolution_schema,
            }
        )

    report_stage_plan(
        client,
        model=config.ollama.model,
        stage=f"{WorkflowStage.RESOLVE_GLOSSARY.value}-{pass_name}",
        prescreened=len(candidates),
        llm_tasks=len(tasks),
        skipped=len(candidates) - sum(len(task["batch"]) for task in tasks),
        context_buckets=(task["bucket"] for task in tasks),
        role=f"glossary.resolve.{pass_name}",
    )
    ordered = group_by_context_bucket(tasks, lambda task: int(task["bucket"]))
    for batch_index, task in enumerate(ordered, start=1):
        result = _resolve_candidate_batch(
            connection,
            task["unit_id"],
            task["batch"],
            task["prompt"],
            task["path"],
            task["unit_input_hash"],
            config,
            client,
            task["schema"],
            pass_name=pass_name,
            batch_index=batch_index,
            total_batches=len(ordered),
            context_bucket=int(task["bucket"]),
            existing_attempts=(
                int(task["existing"]["attempts"]) if task["existing"] else 0
            ),
        )
        results[task["unit_id"]] = result
        _record_file(
            connection,
            workspace,
            task["path"],
            WorkflowStage.RESOLVE_GLOSSARY,
            "glossary_resolution_batch",
        )
    return [
        entry
        for batch_number in range(1, len(batches) + 1)
        for entry in results[
            f"glossary-resolution-{pass_name}-{batch_number:05d}"
        ].entries
    ]


def _resolve_candidate_batch(
    connection,
    unit_id: str,
    candidates: list[GlossaryEntry],
    prompt: str,
    path: Path,
    input_hash: str,
    config: AppConfig,
    client: OllamaClient,
    resolution_schema: type,
    *,
    pass_name: str,
    batch_index: int,
    total_batches: int,
    context_bucket: int,
    existing_attempts: int,
) -> GlossaryResult:
    last_error: Exception | None = None
    invalid_response_hashes: set[str] = set()
    for offset in range(1, config.workflow.max_retries + 2):
        generated = None
        attempt = existing_attempts + offset
        set_work_unit_status(
            connection,
            unit_id,
            WorkflowStage.RESOLVE_GLOSSARY.value,
            "glossary_resolution_batch",
            StageStatus.RUNNING,
            attempts=attempt,
            input_hash=input_hash,
        )
        try:
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt += (
                    "\n\nThe previous response failed validation: "
                    f"{last_error}. Use only the supplied term_id values; never return "
                    "or rewrite an English term."
                )
            generated = client.generate_structured(
                attempt_prompt,
                resolution_schema,
                model=config.ollama.model,
                think=False,
                progress_label=(
                    f"batch={batch_index}/{total_batches} id={unit_id} "
                    f"mode={pass_name} "
                    f"attempt={offset}/{config.workflow.max_retries + 1}"
                ),
                **llm_role_kwargs(client, f"glossary.resolve.{pass_name}"),
                context_minimum=context_bucket,
                context_maximum=context_bucket,
                context_multiplier=config.glossary.resolution_context_multiplier,
                max_attempts=1,
            )
            result = _materialize_resolution_decisions(
                generated.value,
                candidates,
                max_evidence_per_entry=config.glossary.extraction_max_evidence_per_entry,
            )
            atomic_write_text(path, result.model_dump_json(indent=2))
            output_hash = sha256_file(path)
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.RESOLVE_GLOSSARY.value,
                "glossary_resolution_batch",
                StageStatus.COMPLETED,
                attempts=attempt,
                input_hash=input_hash,
                output_hash=output_hash,
                validation={"scope": "passed", "entry_count": len(result.entries)},
            )
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.RESOLVE_GLOSSARY.value,
                attempt,
                StageStatus.COMPLETED,
                metrics=_generation_metrics(generated),
            )
            return result
        except Exception as error:
            last_error = error
            response_hash = sha256_text(
                generated.value.model_dump_json()
                if generated is not None
                else f"{type(error).__name__}:{error}"
            )
            repeated_response = response_hash in invalid_response_hashes
            invalid_response_hashes.add(response_hash)
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.RESOLVE_GLOSSARY.value,
                attempt,
                StageStatus.FAILED,
                message=str(error),
            )
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.RESOLVE_GLOSSARY.value,
                "glossary_resolution_batch",
                StageStatus.FAILED,
                attempts=attempt,
                input_hash=input_hash,
                message=str(error),
            )
            if repeated_response:
                raise ValueError(
                    "resolver repeated an identical invalid structured response; "
                    f"stopping redundant retries: {error}"
                ) from error
    assert last_error is not None
    raise last_error


def _materialize_resolution_decisions(
    generated: GlossaryResolutionResult,
    candidates: list[GlossaryEntry],
    *,
    max_evidence_per_entry: int,
) -> GlossaryResult:
    """Restore exact English terms after validating pipeline-owned decision IDs."""
    cases = build_glossary_resolution_cases(candidates)
    english_by_id = {
        str(case["term_id"]): str(case["english"])
        for case in cases
    }
    if len(generated.decisions) > len(candidates):
        raise ValueError(
            "resolver returned more decisions than supplied candidate alternatives"
        )
    entries: list[GlossaryEntry] = []
    for decision in generated.decisions:
        english = english_by_id.get(decision.term_id)
        if english is None:
            raise ValueError(
                f"resolver returned out-of-scope term_id: {decision.term_id}"
            )
        entries.append(
            GlossaryEntry(
                english=english,
                chinese=decision.chinese,
                note=decision.note,
                category=decision.category,
                aliases=decision.aliases,
                confidence=decision.confidence,
            )
        )
    materialized = GlossaryResult(entries=entries)
    validate_resolution_scope(materialized, candidates)
    return restore_glossary_evidence(
        materialized,
        candidates,
        max_evidence_per_entry=max_evidence_per_entry,
    )


def _load_current_glossary_batch(existing, path, input_hash, candidates):
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    result = GlossaryResult.model_validate_json(path.read_text(encoding="utf-8"))
    validate_resolution_scope(result, candidates)
    return restore_glossary_evidence(result, candidates)


def _conflicting_source_terms(entries: list[GlossaryEntry]) -> set[str]:
    targets: dict[str, set[str]] = {}
    for entry in entries:
        targets.setdefault(normalize_term(entry.english), set()).add(
            normalize_term(entry.chinese)
        )
    return {term for term, values in targets.items() if len(values) > 1}


def _review_glossary(
    connection,
    draft_entries: list[GlossaryEntry],
    config: AppConfig,
    client: OllamaClient,
    workspace: JobWorkspace,
    stage_root: Path,
    stage_input_hash: str,
) -> tuple[GlossaryResult, GlossaryApprovalReport]:
    """Auto-approve credible entries and review only questionable entry deltas."""
    cases = build_glossary_approval_cases(draft_entries)
    deterministic_cases, review_cases, reasons_by_id = (
        screen_glossary_approval_cases(
            cases,
            min_confidence=config.glossary.approval_auto_approve_min_confidence,
        )
    )
    batches = build_glossary_approval_batches(
        review_cases, config.glossary.approval_chunk_tokens
    )
    results: dict[str, GlossaryResult] = {}
    records: dict[str, list[GlossaryApprovalRecord]] = {}
    tasks = []
    for batch_number, batch in enumerate(batches, start=1):
        unit_id = f"glossary-llm-review-{batch_number:05d}"
        prompt = build_approval_review_prompt(batch, config.translation.direction)
        approval_schema = build_glossary_approval_schema(
            [str(case["term_id"]) for case in batch]
        )
        unit_input_hash = hash_named_values(
            {
                "stage": stage_input_hash,
                "prompt": sha256_text(prompt),
                "model": config.ollama.model,
                "min_num_ctx": str(config.glossary.approval_min_num_ctx),
                "max_num_ctx": str(config.glossary.approval_max_num_ctx),
                "context_multiplier": str(
                    config.glossary.approval_context_multiplier
                ),
            }
        )
        path = stage_root / f"{unit_id}.json"
        existing = get_work_unit(
            connection, unit_id, WorkflowStage.APPROVE_GLOSSARY.value
        )
        current = _load_current_approval_batch(
            existing,
            path,
            unit_input_hash,
            batch,
            reasons_by_id,
        )
        if current is not None:
            results[unit_id], records[unit_id] = current
            _record_file(
                connection,
                workspace,
                path,
                WorkflowStage.APPROVE_GLOSSARY,
                "glossary_review_batch",
            )
            continue
        bucket = (
            config.glossary.approval_max_num_ctx
            if not config.ollama.adaptive_num_ctx
            else request_context_bucket(
                prompt,
                minimum=config.glossary.approval_min_num_ctx,
                maximum=config.glossary.approval_max_num_ctx,
                schema=approval_schema,
                multiplier=config.glossary.approval_context_multiplier,
            )
        )
        tasks.append(
            {
                "unit_id": unit_id,
                "batch": batch,
                "prompt": prompt,
                "unit_input_hash": unit_input_hash,
                "path": path,
                "existing": existing,
                "bucket": bucket,
                "schema": approval_schema,
            }
        )

    report_stage_plan(
        client,
        model=config.ollama.model,
        stage=f"{WorkflowStage.APPROVE_GLOSSARY.value}-llm-review",
        prescreened=len(cases),
        llm_tasks=len(tasks),
        skipped=len(deterministic_cases),
        context_buckets=(task["bucket"] for task in tasks),
        role="glossary.approve",
    )
    ordered = group_by_context_bucket(tasks, lambda task: int(task["bucket"]))
    for batch_index, task in enumerate(ordered, start=1):
        result, batch_records = _review_glossary_batch(
            connection,
            task["unit_id"],
            task["batch"],
            task["prompt"],
            task["path"],
            task["unit_input_hash"],
            config,
            client,
            task["schema"],
            reasons_by_id,
            batch_index=batch_index,
            total_batches=len(ordered),
            context_bucket=int(task["bucket"]),
            existing_attempts=(
                int(task["existing"]["attempts"]) if task["existing"] else 0
            ),
        )
        results[task["unit_id"]] = result
        records[task["unit_id"]] = batch_records
        _record_file(
            connection,
            workspace,
            task["path"],
            WorkflowStage.APPROVE_GLOSSARY,
            "glossary_review_batch",
        )
    reviewed = [
        GlossaryEntry.model_validate(case["entry"])
        for case in deterministic_cases
    ] + [
        entry
        for batch_number in range(1, len(batches) + 1)
        for entry in results[f"glossary-llm-review-{batch_number:05d}"].entries
    ]
    all_records = [
        GlossaryApprovalRecord(
            term_id=str(case["term_id"]),
            english=GlossaryEntry.model_validate(case["entry"]).english,
            result="approved",
            mode="deterministic",
            reasons=["evidence_backed_high_confidence"],
            chinese=GlossaryEntry.model_validate(case["entry"]).chinese,
        )
        for case in deterministic_cases
    ] + [
        record
        for batch_number in range(1, len(batches) + 1)
        for record in records[f"glossary-llm-review-{batch_number:05d}"]
    ]
    all_records.sort(key=lambda record: record.term_id)
    report = _build_glossary_approval_report(all_records)
    for index, record in enumerate(all_records, start=1):
        report_segment_result(
            client,
            model=config.ollama.model,
            stage=WorkflowStage.APPROVE_GLOSSARY.value,
            segment_id=record.term_id,
            result=record.result,
            mode=record.mode,
            issue_count=0 if record.result == "approved" else 1,
            message=(
                ",".join(reasons_by_id.get(record.term_id, []))
                or "precheck_passed"
            ),
            result_index=index,
            result_total=len(all_records),
        )
    return GlossaryResult(
        entries=sort_glossary_entries(merge_candidate_entries(reviewed))
    ), report


def _review_glossary_batch(
    connection,
    unit_id: str,
    approval_cases: list[dict[str, object]],
    prompt: str,
    path: Path,
    input_hash: str,
    config: AppConfig,
    client: OllamaClient,
    approval_schema: type,
    reasons_by_id: dict[str, list[str]],
    *,
    batch_index: int,
    total_batches: int,
    context_bucket: int,
    existing_attempts: int,
) -> tuple[GlossaryResult, list[GlossaryApprovalRecord]]:
    """Run one checkpointed, ID-safe Qwen approval-delta batch."""
    last_error: Exception | None = None
    invalid_response_hashes: set[str] = set()
    for offset in range(1, config.workflow.max_retries + 2):
        generated = None
        attempt = existing_attempts + offset
        set_work_unit_status(
            connection,
            unit_id,
            WorkflowStage.APPROVE_GLOSSARY.value,
            "glossary_review_batch",
            StageStatus.RUNNING,
            attempts=attempt,
            input_hash=input_hash,
        )
        try:
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt += (
                    "\n\nThe previous review failed validation: "
                    f"{last_error}. Return every supplied term_id exactly once and never "
                    "return an English field."
                )
            generated = client.generate_structured(
                attempt_prompt,
                approval_schema,
                model=config.ollama.model,
                think=False,
                progress_label=(
                    f"batch={batch_index}/{total_batches} id={unit_id} "
                    f"mode=review "
                    f"attempt={offset}/{config.workflow.max_retries + 1}"
                ),
                **llm_role_kwargs(client, "glossary.approve"),
                context_minimum=context_bucket,
                context_maximum=context_bucket,
                context_multiplier=config.glossary.approval_context_multiplier,
                max_attempts=1,
            )
            reviewed, approval_records = _materialize_approval_decisions(
                generated.value,
                approval_cases,
                reasons_by_id,
            )
            atomic_write_text(path, generated.value.model_dump_json(indent=2))
            output_hash = sha256_file(path)
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.APPROVE_GLOSSARY.value,
                "glossary_review_batch",
                StageStatus.COMPLETED,
                attempts=attempt,
                input_hash=input_hash,
                output_hash=output_hash,
                validation={
                    "scope": "passed",
                    "decision_count": len(generated.value.decisions),
                    "entry_count": len(reviewed.entries),
                },
            )
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.APPROVE_GLOSSARY.value,
                attempt,
                StageStatus.COMPLETED,
                metrics=_generation_metrics(generated),
            )
            return reviewed, approval_records
        except Exception as error:
            last_error = error
            response_hash = sha256_text(
                generated.value.model_dump_json()
                if generated is not None
                else f"{type(error).__name__}:{error}"
            )
            repeated_response = response_hash in invalid_response_hashes
            invalid_response_hashes.add(response_hash)
            record_attempt(
                connection,
                unit_id,
                WorkflowStage.APPROVE_GLOSSARY.value,
                attempt,
                StageStatus.FAILED,
                message=str(error),
            )
            set_work_unit_status(
                connection,
                unit_id,
                WorkflowStage.APPROVE_GLOSSARY.value,
                "glossary_review_batch",
                StageStatus.FAILED,
                attempts=attempt,
                input_hash=input_hash,
                message=str(error),
            )
            if repeated_response:
                raise ValueError(
                    "glossary approver repeated an identical invalid structured response; "
                    f"stopping redundant retries: {error}"
                ) from error
    assert last_error is not None
    raise last_error


def _load_current_approval_batch(
    existing,
    path: Path,
    input_hash: str,
    approval_cases: list[dict[str, object]],
    reasons_by_id: dict[str, list[str]],
) -> tuple[GlossaryResult, list[GlossaryApprovalRecord]] | None:
    if not (
        existing
        and existing["status"] == StageStatus.COMPLETED.value
        and existing["input_hash"] == input_hash
        and path.is_file()
        and sha256_file(path) == existing["output_hash"]
    ):
        return None
    decisions = GlossaryApprovalResult.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    return _materialize_approval_decisions(
        decisions, approval_cases, reasons_by_id
    )


def _materialize_approval_decisions(
    generated: GlossaryApprovalResult,
    approval_cases: list[dict[str, object]],
    reasons_by_id: dict[str, list[str]],
) -> tuple[GlossaryResult, list[GlossaryApprovalRecord]]:
    """Apply approval deltas while retaining exact English and evidence fields."""
    case_by_id = {str(case["term_id"]): case for case in approval_cases}
    returned_ids = [decision.term_id for decision in generated.decisions]
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("glossary approver returned a duplicate term_id")
    missing = sorted(set(case_by_id) - set(returned_ids))
    unexpected = sorted(set(returned_ids) - set(case_by_id))
    if missing or unexpected:
        raise ValueError(
            "glossary approval decision scope mismatch: "
            f"missing={missing or 'none'}; unexpected={unexpected or 'none'}"
        )
    pending = sorted(
        decision.term_id
        for decision in generated.decisions
        if decision.action == GlossaryApprovalAction.PENDING
    )
    if pending:
        raise ValueError(
            "glossary approver left entries pending manual review: " + ", ".join(pending)
        )

    approved: list[GlossaryEntry] = []
    records: list[GlossaryApprovalRecord] = []
    for decision in generated.decisions:
        original = GlossaryEntry.model_validate(case_by_id[decision.term_id]["entry"])
        reasons = list(reasons_by_id.get(decision.term_id, []))
        if decision.reason:
            reasons.append(decision.reason)
        if decision.action == GlossaryApprovalAction.REJECT:
            records.append(
                GlossaryApprovalRecord(
                    term_id=decision.term_id,
                    english=original.english,
                    result="rejected",
                    mode="llm",
                    reasons=reasons,
                    chinese=original.chinese,
                )
            )
            continue
        if decision.action == GlossaryApprovalAction.REVISE:
            payload = original.model_dump(mode="python")
            for field in ("chinese", "note", "category", "aliases", "confidence"):
                value = getattr(decision, field)
                if value is not None:
                    payload[field] = value
            revised = GlossaryEntry.model_validate(payload)
            approved.append(revised)
            records.append(
                GlossaryApprovalRecord(
                    term_id=decision.term_id,
                    english=original.english,
                    result="revised",
                    mode="llm",
                    reasons=reasons,
                    chinese=revised.chinese,
                )
            )
            continue
        approved.append(original)
        records.append(
            GlossaryApprovalRecord(
                term_id=decision.term_id,
                english=original.english,
                result="approved",
                mode="llm",
                reasons=reasons,
                chinese=original.chinese,
            )
        )
    return GlossaryResult(entries=sort_glossary_entries(approved)), records


def _build_glossary_approval_report(
    records: list[GlossaryApprovalRecord],
) -> GlossaryApprovalReport:
    counts = {
        result: sum(record.result == result for record in records)
        for result in ("approved", "rejected", "revised", "pending", "failed")
    }
    return GlossaryApprovalReport(
        result=(
            "failed"
            if counts["failed"]
            else "pending"
            if counts["pending"]
            else "approved"
        ),
        input_count=len(records),
        approved_count=counts["approved"],
        rejected_count=counts["rejected"],
        revised_count=counts["revised"],
        pending_count=counts["pending"],
        failed_count=counts["failed"],
        deterministic_count=sum(record.mode == "deterministic" for record in records),
        llm_count=sum(record.mode == "llm" for record in records),
        records=records,
    )


def _record_file(connection, workspace, path, stage, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        stage.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
    )


def _load_recorded_result(workspace, connection, metadata_key) -> GlossaryResult:
    relative = get_job_metadata(connection, metadata_key)
    if not relative:
        raise FileNotFoundError(f"job metadata is missing: {metadata_key}")
    path = workspace.directory(relative)
    return GlossaryResult.model_validate_json(path.read_text(encoding="utf-8"))


def _require_completed(connection, stage: WorkflowStage) -> dict[str, object]:
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_stage_failed(connection, stage: WorkflowStage, error: Exception) -> None:
    previous = get_stage_status(connection, stage.value)
    if previous is None or previous["status"] in {
        StageStatus.COMPLETED.value,
        StageStatus.PAUSED.value,
    }:
        return
    set_stage_status(
        connection,
        stage.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )


def _generation_metrics(generated: StructuredGenerationResult) -> dict[str, object]:
    metrics = generated.generation.metrics
    return {
        "done_reason": metrics.done_reason,
        "total_duration_ns": metrics.total_duration_ns,
        "load_duration_ns": metrics.load_duration_ns,
        "prompt_eval_count": metrics.prompt_eval_count,
        "prompt_eval_duration_ns": metrics.prompt_eval_duration_ns,
        "eval_count": metrics.eval_count,
        "eval_duration_ns": metrics.eval_duration_ns,
        "time_to_first_output_ns": metrics.time_to_first_output_ns,
        "thinking_duration_ns": metrics.thinking_duration_ns,
        "content_duration_ns": metrics.content_duration_ns,
        "thinking_chars": metrics.thinking_chars,
    }
