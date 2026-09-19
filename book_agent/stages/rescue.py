"""Resumable rescue stage for chunks the primary translator could not draft.

The translate stage drafts every chunk with the primary model and checkpoints the
passages it exhausts its retries on.  This stage picks those up, redrafts them
with the configured fallback model, and then has the primary model harmonize the
rescued prose so the book keeps one voice.  Keeping it separate from translate
means each model stays loaded for one contiguous phase, the work resumes on its
own stage row, and its cost shows up under its own telemetry roles.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..hashing import sha256_file
from ..ollama_client import OllamaClient
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..stage_progress import group_by_context_bucket, report_stage_plan
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
from ..translation import (
    TranslatedChunk,
    TranslatedDocument,
    assemble_translated_segments,
    render_translated_document,
)
from ..workspace import JobWorkspace
from .translate import (
    _harmonize_chunk,
    _translate_chunk,
    load_translation_report,
    plan_chunk_tasks,
)


RESCUE_STAGE_VERSION = "1"


class TranslationRescueReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    candidate_chunk_count: int = Field(default=0, ge=0)
    rescued_chunk_count: int = Field(default=0, ge=0)
    harmonized_chunk_count: int = Field(default=0, ge=0)
    deferred_chunk_count: int = Field(default=0, ge=0)
    deferred_segment_count: int = Field(default=0, ge=0)
    deferred_segment_ids: list[str] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)


def run_translation_rescue_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> TranslationRescueReport:
    """Redraft deferred chunks with the fallback model and restyle the result."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        translate_stage = _require_completed(connection, WorkflowStage.TRANSLATE)
        fallback_models = list(config.translation.fallback_models)
        input_hash = build_stage_input_hash(
            {
                "translate": str(translate_stage["output_hash"]),
                "fallback_models": ",".join(fallback_models),
                "harmonize": str(config.translation.harmonize_fallback_with_primary),
                "attempts_per_model": str(config.translation.attempts_per_model),
                "max_retries": str(config.workflow.max_retries),
                "stage_version": RESCUE_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.RESCUE_TRANSLATION,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_rescue_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.RESCUE_TRANSLATION.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(
                connection, WorkflowStage.RESCUE_TRANSLATION
            )
            previous = get_stage_status(
                connection, WorkflowStage.RESCUE_TRANSLATION.value
            )
        retire_stage_artifacts(connection, WorkflowStage.RESCUE_TRANSLATION.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.RESCUE_TRANSLATION.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        translation = load_translation_report(workspace, connection=connection)
        stage_root = workspace.directory(f"rescued/{input_hash[:16]}")
        chunk_root = stage_root / "chunks"
        attempt_root = stage_root / "attempts"
        chunk_root.mkdir(parents=True, exist_ok=True)
        attempt_root.mkdir(parents=True, exist_ok=True)

        # The rescue belongs between the first draft and the audit.  A job whose
        # audit already consumed the draft (an older run, or one resumed after the
        # stage was introduced) must not have its documents swapped underneath the
        # repairs that were built from them.
        audit = get_stage_status(connection, WorkflowStage.AUDIT_TRANSLATION.value)
        draft_already_audited = bool(
            audit and audit["status"] == StageStatus.COMPLETED.value
        )
        if (
            draft_already_audited
            or not fallback_models
            or not translation.deferred_chunk_count
        ):
            # Nothing to rescue: publish an empty generation so downstream stages
            # keep reading the first-draft documents.
            report = TranslationRescueReport(
                document_count=translation.document_count,
                segment_count=translation.segment_count,
                deferred_chunk_count=translation.deferred_chunk_count,
                deferred_segment_count=translation.deferred_segment_count,
                deferred_segment_ids=list(translation.deferred_segment_ids),
                fallback_models=fallback_models,
            )
            _publish(connection, workspace, stage_root, report, attempts, rooted=False)
            return report

        if client is None:
            raise ValueError("an Ollama client is required to rescue deferred chunks")

        translated_root = workspace.directory(
            str(get_job_metadata(connection, "translation_active_root") or "")
        )
        document_plans, tasks = plan_chunk_tasks(
            workspace,
            config,
            connection,
            input_hash=input_hash,
            chunk_root=chunk_root,
            stage=WorkflowStage.RESCUE_TRANSLATION,
        )
        drafts: dict[str, TranslatedChunk] = {}
        candidates = []
        for task in tasks:
            chunk_id = task["chunk"].chunk_id
            cached = task.pop("cached")
            if cached is not None:
                drafts[chunk_id] = cached
                _record(connection, workspace, task["chunk_path"])
                continue
            first_draft = _load_first_draft(translated_root, chunk_id)
            if first_draft is None:
                continue
            if not _has_deferred_passages(first_draft):
                drafts[chunk_id] = first_draft
                continue
            candidates.append(task)

        report_stage_plan(
            client,
            model=fallback_models[0],
            stage=WorkflowStage.RESCUE_TRANSLATION.value,
            prescreened=len(tasks),
            llm_tasks=len(candidates),
            skipped=len(tasks) - len(candidates),
            context_buckets=(task["bucket"] for task in candidates),
            role="rescue.draft",
        )

        harmonize_queue: list[tuple[dict, TranslatedChunk]] = []
        ordered = group_by_context_bucket(candidates, lambda task: int(task["bucket"]))
        for index, task in enumerate(ordered, start=1):
            rescued, _calls, used_fallback = _translate_chunk(
                connection,
                task["document"],
                task["chunk"],
                task["chunk_input_hash"],
                task["chunk_path"],
                attempt_root,
                config,
                client,
                existing_attempts=0,
                relevant_glossary=task["relevant_glossary"],
                chunk_index=index,
                total_chunks=len(ordered),
                context_bucket=int(task["bucket"]),
                models=fallback_models,
                defer_on_failure=True,
                harmonize=False,
                reuse_saved_attempt=False,
                stage=WorkflowStage.RESCUE_TRANSLATION,
                role_prefix="rescue",
            )
            drafts[task["chunk"].chunk_id] = rescued
            _record(connection, workspace, task["chunk_path"])
            if used_fallback and config.translation.harmonize_fallback_with_primary:
                harmonize_queue.append((task, rescued))

        harmonized = 0
        for task, draft in harmonize_queue:
            result, _calls = _harmonize_chunk(
                connection,
                task["document"],
                task["chunk"],
                task["chunk_input_hash"],
                task["chunk_path"],
                attempt_root,
                config,
                client,
                translated=draft,
                relevant_glossary=task["relevant_glossary"],
                attempts=1,
                stage=WorkflowStage.RESCUE_TRANSLATION,
                role_prefix="rescue",
            )
            if result is not draft:
                harmonized += 1
            drafts[task["chunk"].chunk_id] = result
            _record(connection, workspace, task["chunk_path"])

        documents: list[TranslatedDocument] = []
        for document, chunks in document_plans:
            parts: dict[str, str] = {}
            for chunk in chunks:
                parts.update(drafts[chunk.chunk_id].translations)
            translated_document = TranslatedDocument(
                order=document.order,
                manifest_id=document.manifest_id,
                archive_path=document.archive_path,
                direction=config.translation.direction,
                style=config.translation.style.value,
                segments=assemble_translated_segments(
                    document, parts, config.translation.direction
                ),
            )
            stem = f"{document.order:04d}-{document.manifest_id}"
            json_path = stage_root / f"{stem}.json"
            text_path = stage_root / f"{stem}.txt"
            atomic_write_text(json_path, translated_document.model_dump_json(indent=2))
            atomic_write_text(text_path, render_translated_document(translated_document))
            _record(connection, workspace, json_path, "translated_document_json")
            _record(connection, workspace, text_path, "translated_document_text")
            documents.append(translated_document)

        still_deferred = [
            item for item in drafts.values() if _has_deferred_passages(item)
        ]
        deferred_ids = sorted(
            {
                piece.segment_id
                for _document, chunks in document_plans
                for chunk in chunks
                for piece in chunk.pieces
                if piece.reference_id
                in {
                    issue.reference_id
                    for item in still_deferred
                    for issue in item.validation.issues
                    if issue.code == "deferred_translation" and issue.reference_id
                }
            }
        )
        report = TranslationRescueReport(
            document_count=len(documents),
            segment_count=sum(len(item.segments) for item in documents),
            candidate_chunk_count=len(candidates),
            rescued_chunk_count=len(candidates) - len(still_deferred),
            harmonized_chunk_count=harmonized,
            deferred_chunk_count=len(still_deferred),
            deferred_segment_count=len(deferred_ids),
            deferred_segment_ids=deferred_ids,
            fallback_models=fallback_models,
        )
        _publish(connection, workspace, stage_root, report, attempts, rooted=True)
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def load_rescue_report(
    workspace: JobWorkspace, *, connection=None
) -> TranslationRescueReport:
    """Load the published rescue report for the active generation."""
    owned = connection is None
    connection = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(connection, "rescue_report")
        if not relative:
            raise FileNotFoundError("rescue report is not recorded")
        path = workspace.directory(str(relative))
        return TranslationRescueReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    finally:
        if owned:
            connection.close()


def _publish(connection, workspace, stage_root, report, attempts, *, rooted: bool):
    report_path = stage_root / "rescue.report.json"
    atomic_write_text(report_path, report.model_dump_json(indent=2))
    _record(connection, workspace, report_path, "rescue_report")
    set_job_metadata(
        connection,
        "rescue_report",
        report_path.relative_to(workspace.root).as_posix(),
    )
    # A no-op rescue must also clear an older rescued generation, or readers
    # would keep loading documents this rescue did not publish.
    set_job_metadata(
        connection,
        "rescued_root",
        stage_root.relative_to(workspace.root).as_posix() if rooted else "",
    )
    output_hash = build_stage_output_hash(connection, WorkflowStage.RESCUE_TRANSLATION)
    set_stage_status(
        connection,
        WorkflowStage.RESCUE_TRANSLATION.value,
        StageStatus.COMPLETED,
        attempts=attempts,
        input_hash=get_stage_status(
            connection, WorkflowStage.RESCUE_TRANSLATION.value
        )["input_hash"],
        output_hash=output_hash,
    )


def _record(connection, workspace, path, kind: str = "rescued_chunk_json") -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.RESCUE_TRANSLATION.value,
        kind,
        sha256_file(path),
        path.stat().st_size,
    )


def _load_first_draft(translated_root, chunk_id: str) -> TranslatedChunk | None:
    path = translated_root / "chunks" / f"{chunk_id}.json"
    if not path.is_file():
        return None
    return TranslatedChunk.model_validate_json(path.read_text(encoding="utf-8"))


def _has_deferred_passages(chunk: TranslatedChunk) -> bool:
    return any(
        issue.code == "deferred_translation" for issue in chunk.validation.issues
    )


def _require_completed(connection, stage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, error) -> None:
    previous = get_stage_status(connection, WorkflowStage.RESCUE_TRANSLATION.value)
    set_stage_status(
        connection,
        WorkflowStage.RESCUE_TRANSLATION.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]) if previous else 1,
        message=str(error),
    )
