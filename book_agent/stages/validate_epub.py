"""Resumable final compiled-document validation stage."""

from __future__ import annotations

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..epub_compile import (
    EpubCompilationError,
    EpubValidationReport,
    validate_compiled_epub,
)
from ..hashing import sha256_file
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..rtf import validate_compiled_rtf
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
from .compile import load_compiled_epub_path
from .decompile import load_decompile_manifest
from .validate_repaired import load_validated_repaired_documents


VALIDATE_EPUB_STAGE_VERSION = "4"


def run_epub_validation_stage(
    workspace: JobWorkspace,
    config: AppConfig | None = None,
) -> EpubValidationReport:
    """Validate the compiled EPUB or RTF and fail on any structural defect."""
    connection = connect_state(workspace.state_file)
    try:
        resolved_config = config or AppConfig.model_validate_json(
            workspace.config_file.read_text(encoding="utf-8")
        )
        initialize_state(connection)
        compiled = _require_completed(connection, WorkflowStage.COMPILE)
        input_hash = build_stage_input_hash(
            {
                "compile": str(compiled["output_hash"]),
                "stage_version": VALIDATE_EPUB_STAGE_VERSION,
            }
        )
        if stage_is_current(
            connection,
            WorkflowStage.VALIDATE_EPUB,
            input_hash,
            artifact_root=workspace.root,
        ):
            return load_epub_validation_report(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.VALIDATE_EPUB.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.VALIDATE_EPUB)
            previous = get_stage_status(connection, WorkflowStage.VALIDATE_EPUB.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection,
            WorkflowStage.VALIDATE_EPUB.value,
            StageStatus.RUNNING,
            attempts=attempts,
            input_hash=input_hash,
        )
        output_path = load_compiled_epub_path(workspace)
        manifest = load_decompile_manifest(workspace)
        repaired = load_validated_repaired_documents(workspace)
        manifest_relative = get_job_metadata(connection, "decompile_manifest")
        if not manifest_relative:
            raise FileNotFoundError("decompile manifest is not recorded")
        package_root = workspace.directory(manifest_relative).parent / "package"
        report = (
            validate_compiled_rtf(output_path, manifest, repaired)
            if manifest.source_format == "rtf"
            else validate_compiled_epub(
                output_path,
                manifest,
                repaired,
                strip_print_page_markers=(
                    resolved_config.epub.strip_print_page_markers
                ),
                source_package_root=package_root,
            )
        )
        report_path = workspace.directory("reports") / f"validate-document-{input_hash[:16]}.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "epub_validation_report")
        set_job_metadata(
            connection,
            "document_validation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        set_job_metadata(
            connection,
            "epub_validation_report",
            report_path.relative_to(workspace.root).as_posix(),
        )
        if not report.passed:
            raise EpubCompilationError(
                "compiled document validation failed: " + "; ".join(report.errors)
            )
        output_hash = build_stage_output_hash(connection, WorkflowStage.VALIDATE_EPUB)
        set_stage_status(
            connection,
            WorkflowStage.VALIDATE_EPUB.value,
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


def load_epub_validation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> EpubValidationReport:
    """Load the final document validation report (legacy-compatible name)."""
    owns_connection = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "document_validation_report") or get_job_metadata(
            active, "epub_validation_report"
        )
        if not relative:
            raise FileNotFoundError("document validation report is not recorded")
        path = workspace.directory(relative)
        return EpubValidationReport.model_validate_json(path.read_text(encoding="utf-8"))
    finally:
        if owns_connection:
            active.close()


def run_document_validation_stage(
    workspace: JobWorkspace,
    config: AppConfig | None = None,
) -> EpubValidationReport:
    """Format-neutral alias for the historical EPUB-named stage API."""
    return run_epub_validation_stage(workspace, config)


def load_document_validation_report(
    workspace: JobWorkspace,
    *,
    connection=None,
) -> EpubValidationReport:
    """Load final EPUB validation for either supported source format."""
    return load_epub_validation_report(workspace, connection=connection)


def _record_file(connection, workspace, path, kind) -> None:
    record_artifact(
        connection,
        path.relative_to(workspace.root).as_posix(),
        WorkflowStage.VALIDATE_EPUB.value,
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
    previous = get_stage_status(connection, WorkflowStage.VALIDATE_EPUB.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection,
        WorkflowStage.VALIDATE_EPUB.value,
        StageStatus.FAILED,
        attempts=int(previous["attempts"]),
        message=str(error),
        input_hash=str(previous["input_hash"]),
    )
