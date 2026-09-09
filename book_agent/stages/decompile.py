"""Resumable source-document decompilation stage."""

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from ..atomic_io import atomic_write_text
from ..epub import ChapterDocument, EpubPackageManifest, inspect_epub_package, safe_extract_epub
from ..hashing import sha256_file
from ..rtf import inspect_rtf_document
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
    initialize_state,
    record_artifact,
    set_job_metadata,
    set_stage_status,
)
from ..workspace import JobWorkspace


DECOMPILE_STAGE_VERSION = "5"


def render_document_segments(document: ChapterDocument) -> str:
    """Render normalized chapter text with stable protected segment markers."""
    return "\n\n".join(
        f"<{segment.segment_id}>{segment.protected_text or segment.text}</{segment.segment_id}>"
        for segment in document.segments
    ) + ("\n" if document.segments else "")


def run_decompile_stage(workspace: JobWorkspace) -> EpubPackageManifest:
    """Inspect and atomically publish an EPUB or RTF document inventory."""
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        source_hash = sha256_file(workspace.source_file)
        input_hash = build_stage_input_hash(
            {"source": source_hash, "stage_version": DECOMPILE_STAGE_VERSION}
        )
        if stage_is_current(
            connection,
            WorkflowStage.DECOMPILE,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_decompile_manifest(workspace, connection=connection)

        previous = get_stage_status(connection, WorkflowStage.DECOMPILE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.DECOMPILE)
            previous = get_stage_status(connection, WorkflowStage.DECOMPILE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.DECOMPILE.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )

        staging = workspace.directory(f"tmp/decompile-{uuid.uuid4().hex}")
        final_root = workspace.directory(f"decompiled/{input_hash[:16]}")
        if final_root.exists():
            shutil.rmtree(final_root)
        staging.mkdir(parents=True)
        try:
            source_format = workspace.source_file.suffix.casefold()
            if source_format == ".epub":
                package_root = staging / "package"
                safe_extract_epub(workspace.source_file, package_root)
                manifest = inspect_epub_package(package_root, source_hash)
            elif source_format == ".rtf":
                manifest = inspect_rtf_document(workspace.source_file, source_hash)
            else:
                raise ValueError("source must be an EPUB or RTF file")
            chapters_root = staging / "chapters"
            chapters_root.mkdir()
            for document in manifest.documents:
                stem = f"{document.order:04d}-{document.manifest_id}"
                atomic_write_text(
                    chapters_root / f"{stem}.json",
                    document.model_dump_json(indent=2),
                )
                atomic_write_text(
                    chapters_root / f"{stem}.txt",
                    render_document_segments(document),
                )
            atomic_write_text(staging / "manifest.json", manifest.model_dump_json(indent=2))
            os.replace(staging, final_root)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        connection.execute(
            "DELETE FROM artifacts WHERE stage = ?", (WorkflowStage.DECOMPILE.value,)
        )
        connection.commit()
        for artifact_path in sorted(path for path in final_root.rglob("*") if path.is_file()):
            relative = artifact_path.relative_to(workspace.root).as_posix()
            kind = _artifact_kind(artifact_path, final_root)
            record_artifact(
                connection,
                relative,
                WorkflowStage.DECOMPILE.value,
                kind,
                sha256_file(artifact_path),
                artifact_path.stat().st_size,
            )
        output_hash = build_stage_output_hash(connection, WorkflowStage.DECOMPILE)
        manifest_relative = (final_root / "manifest.json").relative_to(workspace.root).as_posix()
        set_job_metadata(connection, "decompile_manifest", manifest_relative)
        set_job_metadata(connection, "source_format", manifest.source_format)
        set_stage_status(
            connection,
            WorkflowStage.DECOMPILE.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=output_hash,
        )
        return manifest
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def load_decompile_manifest(
    workspace: JobWorkspace,
    *,
    connection: sqlite3.Connection | None = None,
) -> EpubPackageManifest:
    """Load the published decompile manifest recorded in job metadata."""
    owns_connection = connection is None
    active_connection = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active_connection, "decompile_manifest")
        if not relative:
            raise FileNotFoundError("decompile manifest is not recorded")
        path = workspace.directory(relative)
        if not path.is_file():
            raise FileNotFoundError(path)
        return EpubPackageManifest.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active_connection.close()


def _artifact_kind(path: Path, stage_root: Path) -> str:
    relative = path.relative_to(stage_root)
    if relative.as_posix() == "manifest.json":
        return "manifest"
    if relative.parts[0] == "chapters":
        return "chapter_manifest" if path.suffix == ".json" else "normalized_chapter"
    return "package_resource"


def _mark_failed(connection: sqlite3.Connection, error: Exception) -> None:
    previous = get_stage_status(connection, WorkflowStage.DECOMPILE.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.DECOMPILE.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
