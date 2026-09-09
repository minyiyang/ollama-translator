"""Resumable deterministic glossary preprocessing stage."""

from __future__ import annotations

from pathlib import Path

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..glossary import (
    GlossarySource,
    GlossarySourceKind,
    load_glossary_file,
    merge_prioritized_sources,
)
from ..hashing import hash_named_values, sha256_file
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..preprocessing import (
    PreprocessedDocument,
    PreprocessingReport,
    build_replacement_index,
    annotate_segment,
    preprocess_segment,
    render_preprocessed_document,
    select_relevant_glossary_entries,
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
from ..schemas import GlossaryResult, normalize_term
from .decompile import load_decompile_manifest
from .glossary import load_approved_glossary


PREPROCESS_STAGE_VERSION = "6"


def run_preprocessing_stage(
    workspace: JobWorkspace,
    config: AppConfig,
) -> PreprocessingReport:
    """Apply the approved glossary to every document and publish relevant subsets."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        decompile = _require_completed(connection, WorkflowStage.DECOMPILE)
        approval = _require_completed(connection, WorkflowStage.APPROVE_GLOSSARY)
        manifest = load_decompile_manifest(workspace, connection=connection)
        glossary, series_hashes = _load_effective_glossary(workspace, config)
        effective_glossary_hash = hash_named_values(
            {
                "approved": str(approval["output_hash"]),
                **series_hashes,
            }
        )
        input_hash = build_stage_input_hash(
            {
                "decompile": str(decompile["output_hash"]),
                "approved_glossary": str(approval["output_hash"]),
                "direction": config.translation.direction.value,
                "mode": config.preprocessing.mode,
                "conflict_policy": config.preprocessing.conflict_policy,
                "stage_version": PREPROCESS_STAGE_VERSION,
                **series_hashes,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.PREPROCESS,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_preprocessing_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.PREPROCESS.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.PREPROCESS)
            previous = get_stage_status(connection, WorkflowStage.PREPROCESS.value)
        retire_stage_artifacts(connection, WorkflowStage.PREPROCESS.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.PREPROCESS.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )
        index = build_replacement_index(
            glossary.entries,
            config.translation.direction,
            conflict_policy=config.preprocessing.conflict_policy,
        )
        stage_root = workspace.directory(f"preprocessed/{input_hash[:16]}")
        stage_root.mkdir(parents=True, exist_ok=True)
        replacement_count = 0
        segment_count = 0
        volume_active_terms: set[tuple[str, str]] = set()
        document_active_counts: dict[str, int] = {}
        for source_document in manifest.documents:
            unit_id = f"document:{source_document.manifest_id}"
            document_input_hash = hash_named_values(
                {
                    "document": source_document.source_sha256,
                    "glossary": effective_glossary_hash,
                    "direction": config.translation.direction.value,
                    "mode": config.preprocessing.mode,
                    "conflicts": config.preprocessing.conflict_policy,
                }
            )
            stem = f"{source_document.order:04d}-{source_document.manifest_id}"
            json_path = stage_root / f"{stem}.json"
            text_path = stage_root / f"{stem}.txt"
            existing = get_work_unit(connection, unit_id, WorkflowStage.PREPROCESS.value)
            if (
                existing
                and existing["status"] == StageStatus.COMPLETED.value
                and existing["input_hash"] == document_input_hash
                and json_path.is_file()
                and text_path.is_file()
                and hash_named_values(
                    {"json": sha256_file(json_path), "text": sha256_file(text_path)}
                ) == existing["output_hash"]
            ):
                document = PreprocessedDocument.model_validate_json(
                    json_path.read_text(encoding="utf-8")
                )
            else:
                original_text = "\n".join(segment.text for segment in source_document.segments)
                relevant = select_relevant_glossary_entries(
                    original_text, glossary.entries, config.translation.direction
                )
                if config.preprocessing.mode == "replace":
                    processed_segments = [
                        preprocess_segment(
                            segment.segment_id,
                            segment.protected_text or segment.text,
                            index,
                        )
                        for segment in source_document.segments
                    ]
                else:
                    processed_segments = [
                        annotate_segment(
                            segment.segment_id,
                            segment.protected_text or segment.text,
                        )
                        for segment in source_document.segments
                    ]
                document = PreprocessedDocument(
                    order=source_document.order,
                    manifest_id=source_document.manifest_id,
                    archive_path=source_document.archive_path,
                    source_sha256=source_document.source_sha256,
                    segments=processed_segments,
                    relevant_glossary=relevant,
                )
                atomic_write_text(json_path, document.model_dump_json(indent=2))
                atomic_write_text(text_path, render_preprocessed_document(document))
                output_hash = hash_named_values(
                    {"json": sha256_file(json_path), "text": sha256_file(text_path)}
                )
                set_work_unit_status(
                    connection,
                    unit_id,
                    WorkflowStage.PREPROCESS.value,
                    "document",
                    StageStatus.COMPLETED,
                    attempts=(int(existing["attempts"]) + 1 if existing else 1),
                    input_hash=document_input_hash,
                    output_hash=output_hash,
                    validation={
                        "segment_count": len(document.segments),
                        "markers_preserved": True,
                    },
                )
            _record_file(connection, workspace, json_path, "preprocessed_document_json")
            _record_file(connection, workspace, text_path, "preprocessed_document_text")
            segment_count += len(document.segments)
            document_active_counts[document.manifest_id] = len(
                document.relevant_glossary
            )
            volume_active_terms.update(
                (
                    normalize_term(entry.english),
                    normalize_term(entry.chinese),
                )
                for entry in document.relevant_glossary
            )
            replacement_count += sum(
                occurrence.count
                for segment in document.segments
                for occurrence in segment.occurrences
            )
        report = PreprocessingReport(
            direction=config.translation.direction,
            mode=config.preprocessing.mode,
            document_count=len(manifest.documents),
            segment_count=segment_count,
            replacement_count=replacement_count,
            ambiguous_terms={key: list(values) for key, values in index.conflicts.items()},
            effective_glossary_entry_count=len(glossary.entries),
            volume_active_glossary_entry_count=len(volume_active_terms),
            document_active_glossary_entry_counts=dict(
                sorted(document_active_counts.items())
            ),
        )
        report_path = stage_root / "preprocessing.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "preprocessing_report")
        output_hash = build_stage_output_hash(connection, WorkflowStage.PREPROCESS)
        set_job_metadata(
            connection,
            "preprocessing_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "preprocessed_root",
            stage_root.relative_to(workspace.root).as_posix(),
        )
        set_stage_status(
            connection,
            WorkflowStage.PREPROCESS.value,
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


def _load_effective_glossary(
    workspace: JobWorkspace,
    config: AppConfig,
) -> tuple[GlossaryResult, dict[str, str]]:
    """Late-bind shared series terms while preserving approved book overrides."""
    approved = load_approved_glossary(workspace)
    sources: list[GlossarySource] = []
    hashes: dict[str, str] = {}
    for index, path in enumerate(config.glossary.series_glossaries):
        resolved = Path(path).resolve()
        series = load_glossary_file(resolved)
        sources.append(
            GlossarySource(
                name=f"series-{index:03d}-{resolved.name}",
                kind=GlossarySourceKind.SERIES,
                entries=tuple(series.entries),
            )
        )
        hashes[f"series_glossary:{index:03d}:{resolved.name}"] = sha256_file(
            resolved
        )
    sources.append(
        GlossarySource(
            name="approved-book",
            kind=GlossarySourceKind.BOOK,
            entries=tuple(approved.entries),
        )
    )
    return GlossaryResult(entries=merge_prioritized_sources(sources)), hashes


def load_preprocessed_documents(workspace: JobWorkspace) -> list[PreprocessedDocument]:
    """Load all published preprocessed document manifests in spine order."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.PREPROCESS.value,
            root_metadata_key="preprocessed_root",
            report_metadata_key="preprocessing_report",
        )
        documents = []
        for artifact in artifacts:
            if artifact["kind"] != "preprocessed_document_json":
                continue
            path = workspace.directory(str(artifact["path"]))
            documents.append(
                PreprocessedDocument.model_validate_json(path.read_text(encoding="utf-8"))
            )
        documents = require_unique_items(
            documents,
            lambda document: document.manifest_id,
            label="preprocessed document",
        )
        report = load_preprocessing_report(workspace, connection=connection)
        documents = require_document_totals(
            documents,
            expected_documents=report.document_count,
            expected_segments=report.segment_count,
            segment_count=lambda document: len(document.segments),
            label="preprocessed document",
        )
        return sorted(documents, key=lambda document: document.order)
    finally:
        connection.close()


def load_preprocessing_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> PreprocessingReport:
    """Load the published preprocessing report."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "preprocessing_report")
        if not relative:
            raise FileNotFoundError("preprocessing report is not recorded")
        path = workspace.directory(relative)
        return PreprocessingReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.PREPROCESS.value,
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
    previous = get_stage_status(connection, WorkflowStage.PREPROCESS.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.PREPROCESS.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
