"""build_story_context: one source-side summary per chapter (docs/BOOK_CONSISTENCY.md, phase 3).

Opt-in (``consistency.story_context.enabled``). Each chapter is a work unit, so
an interrupted run resumes where it stopped. Preprocessing turns the summaries
into each document's "story so far", which translation adds to its prompts.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..hashing import hash_named_values, sha256_file, sha256_text
from ..ollama_client import OllamaClient
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..stage_artifacts import list_active_stage_artifacts
from ..stage_progress import llm_role_kwargs, report_stage_plan, request_context_bucket
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    get_work_unit,
    initialize_state,
    record_artifact,
    retire_stage_artifacts,
    set_job_metadata,
    set_stage_status,
    set_work_unit_status,
)
from ..story_context import (
    STORY_PROMPT_VERSION,
    ChapterSummary,
    DocumentSummary,
    build_summary_prompt,
)
from ..workspace import JobWorkspace
from .decompile import load_decompile_manifest

STORY_STAGE_VERSION = "1"
_STAGE = WorkflowStage.BUILD_STORY_CONTEXT


class StoryContextReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    document_count: int = Field(ge=0)
    summarized_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)
    model: str = ""


def _model(config: AppConfig) -> str:
    return config.consistency.story_context.model or config.ollama.model


def run_story_context_stage(
    workspace: JobWorkspace,
    config: AppConfig,
    client: OllamaClient | None = None,
) -> StoryContextReport:
    """Summarize every chapter with text, or publish an empty report when switched off."""
    story = config.consistency.story_context
    connection = connect_state(workspace.state_file)
    try:
        initialize_state(connection)
        decompile = _require_completed(connection, WorkflowStage.DECOMPILE)
        hash_fields = {
            "decompile": str(decompile["output_hash"]),
            "enabled": str(story.enabled),
            "stage_version": STORY_STAGE_VERSION,
        }
        if story.enabled:
            # Only what changes a summary; chapters_before is applied by preprocessing.
            hash_fields.update({
                "max_summary_words": str(story.max_summary_words),
                "max_source_characters": str(story.max_source_characters),
                "model": _model(config),
                "prompt_version": STORY_PROMPT_VERSION,
            })
        input_hash = build_stage_input_hash(hash_fields)
        if stage_is_current(connection, _STAGE, input_hash, artifact_root=workspace.root):
            return load_story_report(workspace)
        previous = get_stage_status(connection, _STAGE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, _STAGE)
            previous = get_stage_status(connection, _STAGE.value)
        retire_stage_artifacts(connection, _STAGE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(connection, _STAGE.value, StageStatus.RUNNING, attempts=attempts, input_hash=input_hash)

        stage_root = workspace.directory(f"story/{input_hash[:16]}")
        documents = [
            (document, "\n".join(s.text for s in document.segments if s.text.strip()))
            for document in load_decompile_manifest(workspace, connection=connection).documents
        ]
        documents = [(document, text) for document, text in documents if text]
        summarized = reused = 0
        if story.enabled:
            if client is None:
                raise ValueError("an Ollama client is required to build story context")
            tasks = []
            for document, text in documents:
                truncated = len(text) > story.max_source_characters
                prompt = build_summary_prompt(
                    text[: story.max_source_characters],
                    max_words=story.max_summary_words,
                    truncated=truncated,
                )
                path = stage_root / f"{document.order:04d}-{document.manifest_id}.summary.json"
                unit_id = f"story:{document.manifest_id}"
                unit_hash = hash_named_values(
                    {"prompt": sha256_text(prompt), "model": _model(config), "title": document.title}
                )
                existing = get_work_unit(connection, unit_id, _STAGE.value)
                if (
                    existing
                    and existing["status"] == StageStatus.COMPLETED.value
                    and existing["input_hash"] == unit_hash
                    and path.is_file()
                    and sha256_file(path) == existing["output_hash"]
                ):
                    reused += 1
                else:
                    tasks.append((document, prompt, path, unit_id, unit_hash))
            report_stage_plan(
                client,
                model=_model(config),
                stage=_STAGE.value,
                prescreened=len(documents),
                llm_tasks=len(tasks),
                skipped=len(documents) - len(tasks),
                role="story.summarize",
            )
            for index, (document, prompt, path, unit_id, unit_hash) in enumerate(tasks, start=1):
                _summarize(connection, client, config, document, prompt, path, unit_id, unit_hash, index, len(tasks))
                summarized += 1
            for document, _ in documents:
                path = stage_root / f"{document.order:04d}-{document.manifest_id}.summary.json"
                _record_file(connection, workspace, path, "chapter_summary")

        report = StoryContextReport(
            enabled=story.enabled,
            document_count=len(documents),
            summarized_count=summarized,
            reused_count=reused,
            model=_model(config) if story.enabled else "",
        )
        report_path = stage_root / "story.report.json"
        atomic_write_text(report_path, report.model_dump_json(indent=2))
        _record_file(connection, workspace, report_path, "story_report")
        set_job_metadata(connection, "story_report", report_path.relative_to(workspace.root).as_posix())
        set_job_metadata(connection, "story_root", stage_root.relative_to(workspace.root).as_posix())
        set_stage_status(
            connection,
            _STAGE.value,
            StageStatus.COMPLETED,
            attempts=attempts,
            input_hash=input_hash,
            output_hash=build_stage_output_hash(connection, _STAGE),
            message=(
                f"{len(documents)} chapter summaries ({reused} reused)"
                if story.enabled
                else "story context disabled"
            ),
        )
        return report
    except Exception as error:
        _mark_failed(connection, error)
        raise
    finally:
        connection.close()


def _summarize(connection, client, config, document, prompt, path, unit_id, unit_hash, index, total) -> None:
    story = config.consistency.story_context
    previous = get_work_unit(connection, unit_id, _STAGE.value)
    attempt = int(previous["attempts"]) + 1 if previous else 1
    set_work_unit_status(
        connection, unit_id, _STAGE.value, "chapter_summary", StageStatus.RUNNING,
        attempts=attempt, input_hash=unit_hash,
    )
    bucket = request_context_bucket(
        prompt,
        minimum=config.ollama.min_num_ctx,
        maximum=config.ollama.num_ctx,
        schema=ChapterSummary,
    )
    generated = client.generate_structured(
        prompt,
        ChapterSummary,
        model=_model(config),
        think=False,
        progress_label=f"chapter={index}/{total} id={document.manifest_id}",
        **llm_role_kwargs(client, "story.summarize"),
        context_minimum=bucket,
        context_maximum=bucket,
        max_attempts=config.workflow.max_retries + 1,
    )
    item = DocumentSummary(
        document_id=document.manifest_id,
        order=document.order,
        title=document.title,
        summary=generated.value,
    )
    atomic_write_text(path, item.model_dump_json(indent=2))
    set_work_unit_status(
        connection, unit_id, _STAGE.value, "chapter_summary", StageStatus.COMPLETED,
        attempts=attempt, input_hash=unit_hash, output_hash=sha256_file(path),
        validation={"characters": len(item.summary.characters), "max_words": story.max_summary_words},
    )


def load_story_summaries(workspace: JobWorkspace) -> list[DocumentSummary]:
    """Published chapter summaries in reading order; empty when the stage is off or has not run."""
    connection = connect_state(workspace.state_file)
    try:
        record = get_stage_status(connection, _STAGE.value)
        if not (record and record["status"] == StageStatus.COMPLETED.value and get_job_metadata(connection, "story_root")):
            return []
        artifacts = list_active_stage_artifacts(
            connection, _STAGE.value, root_metadata_key="story_root", report_metadata_key="story_report"
        )
        items = [
            DocumentSummary.model_validate_json(workspace.directory(str(a["path"])).read_text(encoding="utf-8"))
            for a in artifacts
            if a["kind"] == "chapter_summary"
        ]
        return sorted(items, key=lambda item: item.order)
    finally:
        connection.close()


def load_story_report(workspace: JobWorkspace) -> StoryContextReport:
    connection = connect_state(workspace.state_file)
    try:
        path = get_job_metadata(connection, "story_report")
    finally:
        connection.close()
    if not path:
        raise RuntimeError("the story context report has not been published")
    return StoryContextReport.model_validate_json(workspace.directory(path).read_text(encoding="utf-8"))


def _record_file(connection, workspace: JobWorkspace, path, kind: str) -> None:
    record_artifact(
        connection, path.relative_to(workspace.root).as_posix(), _STAGE.value, kind,
        sha256_file(path), path.stat().st_size,
    )


def _require_completed(connection, stage: WorkflowStage):
    record = get_stage_status(connection, stage.value)
    if record is None or record["status"] != StageStatus.COMPLETED.value:
        raise RuntimeError(f"required stage is not complete: {stage.value}")
    return record


def _mark_failed(connection, error: Exception) -> None:
    previous = get_stage_status(connection, _STAGE.value)
    if previous is None or previous["status"] == StageStatus.COMPLETED.value:
        return
    set_stage_status(
        connection, _STAGE.value, StageStatus.FAILED,
        attempts=int(previous["attempts"]), message=str(error), input_hash=str(previous["input_hash"]),
    )
