"""Command-line operations for the resumable document translation workflow."""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .atomic_io import atomic_write_text
from .config import AppConfig, load_config
from .glossary import (
    GlossarySource,
    GlossarySourceKind,
    analyze_glossary_quality,
    screen_glossary_candidates,
)
from .manual_review import (
    load_manual_review_resolution_file,
    resolve_manual_review,
)
from .pipeline_state import WorkflowStage
from .ollama_client import GenerationProgressEvent, OllamaClient
from .styles import TranslationStyle
from .series_glossary import (
    build_series_glossary,
    resolve_series_glossary_conflicts,
    synchronize_book_glossaries,
    write_series_book_overlays,
    write_series_glossary,
)
from .stages.glossary import load_approved_glossary, load_glossary_draft
from .stages.preprocess import load_preprocessing_report
from .workflow import (
    ExitCode,
    ProgressEvent,
    approve_final_draft,
    approve_glossary,
    format_json,
    format_status_plain,
    load_workspace_config,
    retry_failed_from_stage,
    retry_from_stage,
    run_workflow,
    workflow_report,
    workflow_status,
)
from .workspace import create_job_workspace, open_job_workspace


@dataclass
class StageProcessStats:
    """Per-stage wall-clock and LLM usage collected for one file-processing run."""

    status: str = "pending"
    started_at: float | None = None
    elapsed_seconds: float = 0.0
    llm_calls: int = 0
    completed_llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    prompt_token_estimates: int = 0
    llm_seconds: float = 0.0
    load_seconds: float = 0.0
    prompt_eval_seconds: float = 0.0
    decode_seconds: float = 0.0
    planned_prescreened: int = 0
    planned_llm_tasks: int = 0
    planned_skipped: int = 0
    segment_results: Counter[str] = field(default_factory=Counter)


@dataclass
class LlmRoleStats:
    """LLM consumption attributed to one explicit pipeline role."""

    llm_calls: int = 0
    completed_llm_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    prompt_token_estimates: int = 0
    llm_seconds: float = 0.0
    load_seconds: float = 0.0
    prompt_eval_seconds: float = 0.0
    decode_seconds: float = 0.0
    model_context_calls: Counter[str] = field(default_factory=Counter)


@dataclass
class CliProgressContext:
    """Shared identity and active stage for one CLI invocation."""

    session_hash: str = field(default_factory=lambda: secrets.token_hex(4))
    stage: str = "startup"
    log_file: Path | None = None
    stage_stats: dict[str, StageProcessStats] = field(default_factory=dict)
    role_stats: dict[str, LlmRoleStats] = field(default_factory=dict)
    source_segment_count: int = 0


_STAGE_ROLE_SUBSECTIONS: dict[str, tuple[str, ...]] = {
    WorkflowStage.AUDIT_TRANSLATION.value: (
        "audit.semantic",
        "audit.quantity.base",
        "audit.quantity.escalation",
    ),
}


def _stage_role_subsections(
    context: CliProgressContext, stage: str
) -> list[tuple[str, LlmRoleStats]]:
    """Return configured LLM-role rows nested beneath a workflow stage."""
    return [
        (role, context.role_stats[role])
        for role in _STAGE_ROLE_SUBSECTIONS.get(stage, ())
        if role in context.role_stats
    ]


def _llm_role_stats_payload(stats: LlmRoleStats) -> dict[str, object]:
    """Serialize one role consistently for global and stage-nested summaries."""
    return {
        "llm_calls": stats.llm_calls,
        "completed_llm_calls": stats.completed_llm_calls,
        "prompt_tokens": stats.prompt_tokens,
        "output_tokens": stats.output_tokens,
        "total_tokens": stats.prompt_tokens + stats.output_tokens,
        "llm_seconds": round(stats.llm_seconds, 3),
        "load_seconds": round(stats.load_seconds, 3),
        "prompt_eval_seconds": round(stats.prompt_eval_seconds, 3),
        "decode_seconds": round(stats.decode_seconds, 3),
        "model_context_calls": dict(sorted(stats.model_context_calls.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without performing side effects."""
    parser = argparse.ArgumentParser(
        prog="book-agent",
        description="Resumable local-Ollama EPUB/RTF translation workflow",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser(
        "config", help="validate and print resolved configuration"
    )
    config_parser.add_argument("--file", help="YAML configuration path")

    config_diff_parser = subparsers.add_parser(
        "config-diff", help="compare a workspace snapshot with a proposed configuration"
    )
    config_diff_parser.add_argument("workspace", help="job workspace path")
    config_diff_parser.add_argument("--config", required=True, help="proposed YAML configuration")
    config_diff_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    subparsers.add_parser("styles", help="list available prose-style names")

    run_parser = subparsers.add_parser("run", help="create and run a translation job")
    run_parser.add_argument("source", help="source EPUB or RTF path")
    run_parser.add_argument("--config", dest="config_file", help="YAML configuration path")
    run_parser.add_argument("--runs", help="override the run-workspace directory")
    run_parser.add_argument("--job-id", help="explicit safe workspace name")
    run_parser.add_argument("--dry-run", action="store_true", help="inspect without writing or calling Ollama")
    run_parser.add_argument("--plain", action="store_true", help="disable styled progress output")

    resume_parser = subparsers.add_parser("resume", help="continue an existing job")
    resume_parser.add_argument("workspace", help="job workspace path")
    resume_parser.add_argument("--plain", action="store_true", help="disable styled progress output")

    status_parser = subparsers.add_parser("status", help="show current job state")
    status_parser.add_argument("workspace", help="job workspace path")
    status_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    approve_parser = subparsers.add_parser("approve", help="approve a paused review gate")
    approve_parser.add_argument("workspace", help="job workspace path")
    approve_parser.add_argument(
        "--glossary",
        help=(
            "glossary JSON or legacy file; treated as human-reviewed unless "
            "combined with --llm-glossary"
        ),
    )
    approve_parser.add_argument(
        "--llm-glossary",
        action="store_true",
        help=(
            "have the configured Qwen model review and approve the resolved glossary "
            "or the candidate supplied by --glossary"
        ),
    )
    approve_parser.add_argument(
        "--final", action="store_true", help="approve the current repaired draft"
    )
    approve_parser.add_argument("--resume", action="store_true", help="continue immediately after approval")
    approve_parser.add_argument("--plain", action="store_true", help="disable styled progress output")

    retry_parser = subparsers.add_parser("retry", help="reset a stage and its dependents")
    retry_parser.add_argument("workspace", help="job workspace path")
    retry_parser.add_argument("--stage", required=True, choices=[stage.value for stage in WorkflowStage])
    retry_parser.add_argument("--resume", action="store_true", help="continue immediately after reset")
    retry_parser.add_argument(
        "--failed-only",
        action="store_true",
        help="retain completed checkpoints and reset only failed work units",
    )
    retry_parser.add_argument("--plain", action="store_true", help="disable styled progress output")

    report_parser = subparsers.add_parser("report", help="show job metadata and artifact reports")
    report_parser.add_argument("workspace", help="job workspace path")
    report_parser.add_argument("--json", action="store_true", help="emit complete machine-readable JSON")

    resolve_parser = subparsers.add_parser(
        "resolve-review",
        help="apply explicit human resolutions or bounded corrections to the reviewed draft",
    )
    resolve_parser.add_argument("workspace", help="job workspace path")
    resolve_parser.add_argument(
        "resolutions",
        help="completed final-review worksheet or legacy resolution JSON file",
    )
    resolve_parser.add_argument(
        "--approve-final",
        action="store_true",
        help="approve the draft when this worksheet clears the review queue",
    )
    resolve_parser.add_argument(
        "--resume",
        action="store_true",
        help="resume compilation after a successful --approve-final",
    )
    resolve_parser.add_argument("--plain", action="store_true", help="disable styled progress output")

    series_parser = subparsers.add_parser(
        "build-series-glossary",
        help="promote agreed terms from approved book glossaries",
    )
    series_source = series_parser.add_mutually_exclusive_group(required=True)
    series_source.add_argument(
        "--runs",
        help="directory containing completed book workspaces",
    )
    series_source.add_argument(
        "--workspace",
        dest="workspaces",
        action="append",
        help="approved book workspace; repeat for each series book",
    )
    series_parser.add_argument(
        "--job-pattern",
        default="*",
        help="workspace glob used with --runs (default: *)",
    )
    series_parser.add_argument("--output", required=True, help="series glossary JSON path")
    series_parser.add_argument("--report", help="conflict report JSON path")
    series_parser.add_argument("--legacy", help="legacy glossary text path")
    series_parser.add_argument(
        "--source-stage",
        choices=("approved", "resolved"),
        default="approved",
        help=(
            "book glossary boundary to aggregate (default: approved); use resolved "
            "to prepare a review candidate before per-book approval"
        ),
    )
    series_parser.add_argument(
        "--book-output-dir",
        help=(
            "write per-book review overlays synchronized to the generated series canon"
        ),
    )
    series_parser.add_argument(
        "--minimum-books",
        type=int,
        default=2,
        help="minimum approved books containing a term (default: 2)",
    )
    series_parser.add_argument(
        "--consensus-ratio",
        type=float,
        default=1.0,
        help="required translation agreement above 0.5 (default: 1.0)",
    )
    series_parser.add_argument(
        "--llm-conflicts",
        action="store_true",
        help="ask the configured Qwen model to choose only among reported conflict variants",
    )
    series_parser.add_argument(
        "--config",
        dest="config_file",
        help="YAML configuration for --llm-conflicts model and context settings",
    )
    series_parser.add_argument(
        "--plain",
        action="store_true",
        help="disable styled LLM progress output",
    )
    return parser


def format_config(config: AppConfig) -> str:
    """Serialize resolved configuration as stable, readable JSON."""
    return config.model_dump_json(indent=2)


def build_config_diff(
    workspace,
    proposed_config_path: str | Path,
) -> dict[str, object]:
    """Return leaf-level effective configuration changes without mutating a job."""
    path = Path(proposed_config_path).resolve()
    proposed = resolve_config_paths(load_config(path), path.parent).model_dump(mode="json")
    captured = load_workspace_config(workspace).model_dump(mode="json")
    changes: list[dict[str, object]] = []

    def walk(prefix: str, before, after) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                child = f"{prefix}.{key}" if prefix else key
                walk(child, before.get(key), after.get(key))
            return
        if before != after:
            changes.append({"path": prefix, "captured": before, "proposed": after})

    walk("", captured, proposed)
    return {
        "workspace": str(workspace.root),
        "proposed_config": str(path),
        "changed": bool(changes),
        "change_count": len(changes),
        "changes": changes,
        "note": "Read-only diff; retry affected stages explicitly before resuming.",
    }


def format_config_diff_plain(diff: dict[str, object]) -> str:
    """Render a concise read-only configuration diff."""
    lines = [
        f"Workspace: {diff['workspace']}",
        f"Proposed config: {diff['proposed_config']}",
        f"Changed: {'yes' if diff['changed'] else 'no'}",
    ]
    for item in diff["changes"]:
        lines.append(
            f"- {item['path']}: {item['captured']!r} -> {item['proposed']!r}"
        )
    lines.append(str(diff["note"]))
    return "\n".join(lines)


def list_style_names() -> list[str]:
    """Return supported style values in declaration order."""
    return [style.value for style in TranslationStyle]


def build_series_glossary_from_workspaces(
    workspace_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    report_path: str | Path | None = None,
    legacy_path: str | Path | None = None,
    minimum_books: int = 2,
    consensus_ratio: float = 1.0,
    source_stage: str = "approved",
    book_output_dir: str | Path | None = None,
    llm_conflicts: bool = False,
    config: AppConfig | None = None,
    client: OllamaClient | None = None,
    generation_progress: Callable[[GenerationProgressEvent], None] | None = None,
) -> dict[str, object]:
    """Build, quality-screen, and optionally synchronize book glossary overlays."""
    resolved_paths = [Path(path).resolve() for path in workspace_paths]
    if not resolved_paths:
        raise ValueError("at least one book workspace is required")
    if len(resolved_paths) != len(set(resolved_paths)):
        raise ValueError("book workspaces must be unique")
    if source_stage not in {"approved", "resolved"}:
        raise ValueError("source_stage must be 'approved' or 'resolved'")
    sources: list[GlossarySource] = []
    screening_reports = {}
    source_quality_reports = {}
    input_source_entry_count = 0
    for path in sorted(resolved_paths, key=lambda item: str(item).casefold()):
        workspace = open_job_workspace(path)
        loaded = (
            load_approved_glossary(workspace)
            if source_stage == "approved"
            else load_glossary_draft(workspace)
        )
        input_source_entry_count += len(loaded.entries)
        screened, screening = screen_glossary_candidates(
            loaded,
            remove_ordinary_terms=source_stage == "resolved",
        )
        screening_reports[workspace.root.name] = screening
        source_quality_reports[workspace.root.name] = analyze_glossary_quality(
            screened
        )
        sources.append(
            GlossarySource(
                name=workspace.root.name,
                kind=GlossarySourceKind.SERIES,
                entries=tuple(screened.entries),
            )
        )
    build = build_series_glossary(
        sources,
        minimum_sources=minimum_books,
        consensus_ratio=consensus_ratio,
        defer_generic_terms=source_stage == "resolved",
    )
    build = build.model_copy(
        update={
            "report": build.report.model_copy(
                update={
                    "input_source_entry_count": input_source_entry_count,
                    "deterministically_rejected_count": sum(
                        report.rejected_count for report in screening_reports.values()
                    ),
                    "screening_reports": screening_reports,
                    "source_quality_reports": source_quality_reports,
                }
            )
        }
    )
    if llm_conflicts and build.report.conflicts:
        resolved_config = config or AppConfig()
        active_client = client or OllamaClient(
            resolved_config.ollama,
            progress=generation_progress,
        )
        active_client.validate_model_context(
            resolved_config.glossary.resolution_max_num_ctx,
            model=resolved_config.ollama.model,
        )
        resolved_output = Path(output_path).resolve()
        checkpoint_directory = resolved_output.with_name(
            f"{resolved_output.stem}.conflict-checkpoints"
        )
        build = resolve_series_glossary_conflicts(
            build,
            sources,
            active_client,
            model=resolved_config.ollama.model,
            chunk_tokens=resolved_config.glossary.resolution_chunk_tokens,
            min_num_ctx=resolved_config.glossary.resolution_min_num_ctx,
            max_num_ctx=resolved_config.glossary.resolution_max_num_ctx,
            context_multiplier=(
                resolved_config.glossary.resolution_context_multiplier
            ),
            max_attempts=resolved_config.workflow.max_retries + 1,
            checkpoint_directory=checkpoint_directory,
        )
    output, report, legacy = write_series_glossary(
        build,
        output_path,
        report_path=report_path,
        legacy_path=legacy_path,
    )
    overlay_summary: dict[str, object] = {}
    if book_output_dir is not None:
        overlays, overlay_reports = synchronize_book_glossaries(
            sources, build.glossary
        )
        written, overlay_report_path = write_series_book_overlays(
            overlays, book_output_dir, overlay_reports
        )
        overlay_summary = {
            "book_output_dir": str(Path(book_output_dir).resolve()),
            "book_overlay_count": len(written),
            "book_overlay_report": str(overlay_report_path),
            "canonicalized_book_term_count": sum(
                item.canonicalized_term_count for item in overlay_reports
            ),
        }
    return {
        "result": "completed",
        "output": str(output),
        "report": str(report),
        "legacy": str(legacy),
        "source_count": build.report.source_count,
        "source_entry_count": build.report.source_entry_count,
        "recurring_term_count": build.report.recurring_term_count,
        "included_term_count": build.report.included_term_count,
        "conflict_count": build.report.conflict_count,
        "category_conflict_count": build.report.category_conflict_count,
        "llm_attempted_conflict_count": (
            build.report.llm_attempted_conflict_count
        ),
        "llm_resolved_conflict_count": build.report.llm_resolved_conflict_count,
        "source_stage": source_stage,
        "deterministically_rejected_count": (
            build.report.deterministically_rejected_count
        ),
        "source_quality_warning_count": sum(
            report.result == "warning"
            for report in build.report.source_quality_reports.values()
        ),
        **overlay_summary,
    }


def resolve_config_paths(config: AppConfig, base_directory: str | Path) -> AppConfig:
    """Make persisted configuration paths independent of the launch directory."""
    base = Path(base_directory).resolve()

    def resolved(path: Path | None) -> Path | None:
        if path is None:
            return None
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    return config.model_copy(
        update={
            "translation": config.translation.model_copy(
                update={"custom_style_file": resolved(config.translation.custom_style_file)}
            ),
            "glossary": config.glossary.model_copy(
                update={
                    "seed_glossaries": [resolved(path) for path in config.glossary.seed_glossaries],
                    "series_glossaries": [resolved(path) for path in config.glossary.series_glossaries],
                    "book_glossaries": [resolved(path) for path in config.glossary.book_glossaries],
                }
            ),
            "paths": config.paths.model_copy(
                update={
                    "runs": resolved(config.paths.runs),
                    "prompts": resolved(config.paths.prompts),
                }
            ),
        }
    )


def build_dry_run_summary(source: str | Path, config: AppConfig, runs: str | Path) -> dict[str, object]:
    """Validate and describe a prospective job without creating files or contacting Ollama."""
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source_format = source_path.suffix.casefold().lstrip(".")
    if source_format not in {"epub", "rtf"}:
        raise ValueError("source must be an EPUB or RTF file")
    return {
        "dry_run": True,
        "source": str(source_path),
        "source_format": source_format,
        "runs": str(Path(runs).resolve()),
        "direction": config.translation.direction.value,
        "translation_model": config.ollama.model,
        "glossary_extraction_enabled": config.glossary.extraction_enabled,
        "glossary_extraction_model": (
            config.glossary.extraction_model
            if config.glossary.extraction_enabled
            else None
        ),
        "audit_model": config.audit.model,
        "reprose_enabled": config.reprose.enabled,
        "reprose_model": config.reprose.model if config.reprose.enabled else None,
        "reprose_verifier_model": (
            config.reprose.verifier_model if config.reprose.enabled else None
        ),
        "working_context": config.budget.working_limit,
        "stages": [stage.value for stage in WorkflowStage],
    }


def make_progress_printer(
    *, plain: bool = False, context: CliProgressContext | None = None
):
    """Create a Rich progress callback when available, otherwise plain text."""
    active = context or CliProgressContext()
    if not plain:
        try:
            from rich.console import Console
        except ImportError:
            pass
        else:
            console = Console(stderr=True)

            def rich_progress(event: ProgressEvent) -> None:
                active.stage = event.stage
                _record_stage_progress(active, event)
                suffix = f" — {event.message}" if event.message else ""
                _write_session_log(active, _format_plain_progress(event, active))
                console.print(
                    f"{_progress_prefix(active)} {event.status:9}{suffix}",
                    markup=False,
                )

            return rich_progress

    def plain_progress(event: ProgressEvent) -> None:
        active.stage = event.stage
        _record_stage_progress(active, event)
        rendered = _format_plain_progress(event, active)
        _write_session_log(active, rendered)
        print(rendered, file=sys.stderr, flush=True)

    return plain_progress


def make_generation_progress_printer(
    *, plain: bool = False, context: CliProgressContext | None = None
):
    """Render safe 2,000-character LLM heartbeats and exact final token metrics."""
    active = context or CliProgressContext()
    if not plain:
        try:
            from rich.console import Console
        except ImportError:
            pass
        else:
            console = Console(stderr=True)

            def rich_generation_progress(event: GenerationProgressEvent) -> None:
                _record_generation_progress(active, event)
                rendered = _format_generation_progress(event, active)
                _write_session_log(active, rendered)
                console.print(
                    rendered,
                    style="magenta",
                    markup=False,
                )

            return rich_generation_progress

    def plain_generation_progress(event: GenerationProgressEvent) -> None:
        _record_generation_progress(active, event)
        rendered = _format_generation_progress(event, active)
        _write_session_log(active, rendered)
        print(rendered, file=sys.stderr, flush=True)

    return plain_generation_progress


def _format_generation_progress(
    event: GenerationProgressEvent, context: CliProgressContext
) -> str:
    prefix = f"{_progress_prefix(context)} "
    if event.role:
        prefix += f"[role={event.role}] "
    if event.label:
        prefix += f"[{event.label}] "
    if event.kind == "stage_plan":
        return f"{prefix}[schedule] {event.message}"
    if event.kind == "segment_result":
        return f"{prefix}[segment] {event.message}"
    if event.kind == "started":
        prompt = (
            f", prompt ~{event.prompt_tokens_estimate:,} tokens"
            if event.prompt_tokens_estimate
            else ""
        )
        context_size = (
            f", context {event.context_size:,}" if event.context_size else ""
        )
        return f"{prefix}[llm] {event.model}: generation started{prompt}{context_size}"
    if event.kind == "heartbeat":
        observed = []
        if event.generated_tokens_estimate:
            observed.append(
                f"~{event.generated_tokens_estimate:,} output tokens observed"
            )
        if event.thinking_chars:
            observed.append(f"thinking {event.thinking_chars:,} chars")
        if event.generated_chars:
            observed.append(f"visible output {event.generated_chars:,} chars")
        detail = ", ".join(observed) or "response stream active"
        return f"{prefix}[llm] {event.model}: streaming — {detail}"
    if event.kind == "validation_failed":
        return f"{prefix}[llm] {event.model}: validation failed — {event.message}"
    metrics = event.metrics
    if metrics is None:
        return f"{prefix}[llm] {event.model}: generation completed"
    elapsed = metrics.total_duration_ns / 1_000_000_000
    load_seconds = metrics.load_duration_ns / 1_000_000_000
    prompt_seconds = metrics.prompt_eval_duration_ns / 1_000_000_000
    decode_seconds = metrics.eval_duration_ns / 1_000_000_000
    tokens_per_second = metrics.eval_count / decode_seconds if decode_seconds > 0 else 0.0
    speed = f", {tokens_per_second:.1f} tok/s" if tokens_per_second > 0 else ""
    timing = (
        f"load {_format_elapsed(load_seconds)}, "
        f"prompt {_format_elapsed(prompt_seconds)}, "
        f"decode {_format_elapsed(decode_seconds)}"
    )
    if metrics.thinking_chars:
        timing += (
            f", observed thinking {metrics.thinking_chars:,} chars/"
            f"{_format_elapsed(metrics.thinking_duration_ns / 1_000_000_000)}, "
            f"visible output {_format_elapsed(metrics.content_duration_ns / 1_000_000_000)}"
        )
    else:
        timing += ", thinking not observed"
    return (
        f"{prefix}[llm] {event.model}: completed, {event.generated_chars:,} characters, "
        f"prompt {metrics.prompt_eval_count:,} tokens, output {metrics.eval_count:,} tokens"
        f"{speed}, elapsed {_format_elapsed(elapsed)} ({timing})"
    )


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3_600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3_600:.1f}h"


def _record_stage_progress(
    context: CliProgressContext,
    event: ProgressEvent,
) -> None:
    stats = context.stage_stats.setdefault(event.stage, StageProcessStats())
    stats.status = event.status
    if event.status == "running":
        if stats.started_at is None:
            stats.started_at = time.monotonic()
        return
    if stats.started_at is not None:
        stats.elapsed_seconds += max(0.0, time.monotonic() - stats.started_at)
        stats.started_at = None


def _record_generation_progress(
    context: CliProgressContext,
    event: GenerationProgressEvent,
) -> None:
    stats = context.stage_stats.setdefault(context.stage, StageProcessStats())
    if event.kind == "stage_plan":
        stats.planned_prescreened = max(
            stats.planned_prescreened, event.prescreened
        )
        stats.planned_llm_tasks = max(stats.planned_llm_tasks, event.llm_tasks)
        stats.planned_skipped = max(stats.planned_skipped, event.skipped)
        return
    if event.kind == "segment_result":
        if event.result:
            stats.segment_results[event.result] += 1
        context.source_segment_count = max(
            context.source_segment_count, event.result_total
        )
        return
    if event.kind == "started":
        role = event.role or context.stage
        role_stats = context.role_stats.setdefault(role, LlmRoleStats())
        stats.llm_calls += 1
        stats.prompt_token_estimates += event.prompt_tokens_estimate
        role_stats.llm_calls += 1
        role_stats.prompt_token_estimates += event.prompt_tokens_estimate
        model_context = event.model
        if event.context_size:
            model_context += f"@{event.context_size}"
        role_stats.model_context_calls[model_context] += 1
        return
    if event.kind != "completed" or event.metrics is None:
        return
    role = event.role or context.stage
    role_stats = context.role_stats.setdefault(role, LlmRoleStats())
    stats.completed_llm_calls += 1
    stats.prompt_tokens += event.metrics.prompt_eval_count
    stats.output_tokens += event.metrics.eval_count
    stats.llm_seconds += event.metrics.total_duration_ns / 1_000_000_000
    stats.load_seconds += event.metrics.load_duration_ns / 1_000_000_000
    stats.prompt_eval_seconds += (
        event.metrics.prompt_eval_duration_ns / 1_000_000_000
    )
    stats.decode_seconds += event.metrics.eval_duration_ns / 1_000_000_000
    role_stats.completed_llm_calls += 1
    role_stats.prompt_tokens += event.metrics.prompt_eval_count
    role_stats.output_tokens += event.metrics.eval_count
    role_stats.llm_seconds += event.metrics.total_duration_ns / 1_000_000_000
    role_stats.load_seconds += event.metrics.load_duration_ns / 1_000_000_000
    role_stats.prompt_eval_seconds += (
        event.metrics.prompt_eval_duration_ns / 1_000_000_000
    )
    role_stats.decode_seconds += event.metrics.eval_duration_ns / 1_000_000_000


def _format_processing_summary(context: CliProgressContext) -> list[str]:
    """Render one end-of-file table for stage timing and LLM consumption."""
    if not context.stage_stats:
        return []
    header = (
        "stage / subsection                status       time    calls  done  "
        "prompt tok  output tok  total tok  avg prompt  LLM time  "
        "load time  prompt time  decode time"
    )
    separator = "-" * len(header)
    lines = [
        f"{_progress_prefix(context)} [file-summary] processing summary",
        header,
        separator,
    ]
    total_elapsed = 0.0
    total_calls = 0
    total_completed = 0
    total_prompt = 0
    total_output = 0
    total_llm_seconds = 0.0
    total_load_seconds = 0.0
    total_prompt_eval_seconds = 0.0
    total_decode_seconds = 0.0
    for stage, stats in context.stage_stats.items():
        elapsed = stats.elapsed_seconds
        if stats.started_at is not None:
            elapsed += max(0.0, time.monotonic() - stats.started_at)
        average_prompt = (
            stats.prompt_tokens / stats.completed_llm_calls
            if stats.completed_llm_calls
            else (
                stats.prompt_token_estimates / stats.llm_calls
                if stats.llm_calls
                else 0.0
            )
        )
        total_elapsed += elapsed
        total_calls += stats.llm_calls
        total_completed += stats.completed_llm_calls
        total_prompt += stats.prompt_tokens
        total_output += stats.output_tokens
        total_llm_seconds += stats.llm_seconds
        total_load_seconds += stats.load_seconds
        total_prompt_eval_seconds += stats.prompt_eval_seconds
        total_decode_seconds += stats.decode_seconds
        lines.append(
            f"{stage:<35} {stats.status:<10} {_format_elapsed(elapsed):>8} "
            f"{stats.llm_calls:>7,} {stats.completed_llm_calls:>5,} "
            f"{stats.prompt_tokens:>11,} {stats.output_tokens:>11,} "
            f"{stats.prompt_tokens + stats.output_tokens:>10,} "
            f"{average_prompt:>11,.0f} {_format_elapsed(stats.llm_seconds):>9} "
            f"{_format_elapsed(stats.load_seconds):>10} "
            f"{_format_elapsed(stats.prompt_eval_seconds):>12} "
            f"{_format_elapsed(stats.decode_seconds):>12}"
        )
        for role, role_stats in _stage_role_subsections(context, stage):
            role_average_prompt = (
                role_stats.prompt_tokens / role_stats.completed_llm_calls
                if role_stats.completed_llm_calls
                else (
                    role_stats.prompt_token_estimates / role_stats.llm_calls
                    if role_stats.llm_calls
                    else 0.0
                )
            )
            lines.append(
                f"  {role:<33} {'subsection':<10} {'-':>8} "
                f"{role_stats.llm_calls:>7,} {role_stats.completed_llm_calls:>5,} "
                f"{role_stats.prompt_tokens:>11,} {role_stats.output_tokens:>11,} "
                f"{role_stats.prompt_tokens + role_stats.output_tokens:>10,} "
                f"{role_average_prompt:>11,.0f} "
                f"{_format_elapsed(role_stats.llm_seconds):>9} "
                f"{_format_elapsed(role_stats.load_seconds):>10} "
                f"{_format_elapsed(role_stats.prompt_eval_seconds):>12} "
                f"{_format_elapsed(role_stats.decode_seconds):>12}"
            )
    overall_average = total_prompt / total_completed if total_completed else 0.0
    lines.extend(
        [
            separator,
            f"{'TOTAL':<35} {'':<10} {_format_elapsed(total_elapsed):>8} "
            f"{total_calls:>7,} {total_completed:>5,} "
            f"{total_prompt:>11,} {total_output:>11,} "
            f"{total_prompt + total_output:>10,} "
            f"{overall_average:>11,.0f} {_format_elapsed(total_llm_seconds):>9} "
            f"{_format_elapsed(total_load_seconds):>10} "
            f"{_format_elapsed(total_prompt_eval_seconds):>12} "
            f"{_format_elapsed(total_decode_seconds):>12}",
        ]
    )
    if context.source_segment_count:
        denominator = context.source_segment_count / 1_000
        lines.extend(
            [
                "",
                (
                    "normalized workload per 1,000 source segments "
                    f"(book denominator: {context.source_segment_count:,})"
                ),
                "stage                             calls/1k  tokens/1k  time/1k  plan llm/skip  outcomes",
                "--------------------------------------------------------------------------------",
            ]
        )
        for stage, stats in context.stage_stats.items():
            elapsed = stats.elapsed_seconds
            if stats.started_at is not None:
                elapsed += max(0.0, time.monotonic() - stats.started_at)
            outcomes = ",".join(
                f"{name}={count}"
                for name, count in sorted(stats.segment_results.items())
            ) or "none"
            plan = (
                f"{stats.planned_llm_tasks}/{stats.planned_skipped}"
                if stats.planned_llm_tasks or stats.planned_skipped
                else "-"
            )
            lines.append(
                f"{stage:<35} {stats.llm_calls / denominator:>8.1f} "
                f"{(stats.prompt_tokens + stats.output_tokens) / denominator:>10,.0f} "
                f"{_format_elapsed(elapsed / denominator):>9} "
                f"{plan:>13}  {outcomes}"
            )
    if context.role_stats:
        lines.extend(
            [
                "",
                "LLM usage by pipeline role (model@context: calls)",
                "role                              calls  done  prompt tok  output tok  LLM time  load time  model/context calls",
                "---------------------------------------------------------------------------------------------------------------",
            ]
        )
        for role, stats in context.role_stats.items():
            model_contexts = ",".join(
                f"{name}:{count}"
                for name, count in sorted(stats.model_context_calls.items())
            ) or "none"
            lines.append(
                f"{role:<33} {stats.llm_calls:>5,} {stats.completed_llm_calls:>5,} "
                f"{stats.prompt_tokens:>11,} {stats.output_tokens:>11,} "
                f"{_format_elapsed(stats.llm_seconds):>9} "
                f"{_format_elapsed(stats.load_seconds):>10}  {model_contexts}"
            )
    return lines


def _processing_summary_payload(context: CliProgressContext) -> dict[str, object]:
    """Build a persisted, book-normalized performance snapshot for this session."""
    stage_rows: dict[str, dict[str, object]] = {}
    totals = {
        "elapsed_seconds": 0.0,
        "llm_calls": 0,
        "completed_llm_calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "llm_seconds": 0.0,
    }
    denominator = context.source_segment_count / 1_000
    for stage, stats in context.stage_stats.items():
        elapsed = stats.elapsed_seconds
        if stats.started_at is not None:
            elapsed += max(0.0, time.monotonic() - stats.started_at)
        total_tokens = stats.prompt_tokens + stats.output_tokens
        row: dict[str, object] = {
            "status": stats.status,
            "elapsed_seconds": round(elapsed, 3),
            "llm_calls": stats.llm_calls,
            "completed_llm_calls": stats.completed_llm_calls,
            "llm_completion_rate": round(
                stats.completed_llm_calls / stats.llm_calls, 4
            ) if stats.llm_calls else None,
            "prompt_tokens": stats.prompt_tokens,
            "output_tokens": stats.output_tokens,
            "total_tokens": total_tokens,
            "llm_seconds": round(stats.llm_seconds, 3),
            "load_seconds": round(stats.load_seconds, 3),
            "prompt_eval_seconds": round(stats.prompt_eval_seconds, 3),
            "decode_seconds": round(stats.decode_seconds, 3),
            "plan": {
                "prescreened": stats.planned_prescreened,
                "llm_tasks": stats.planned_llm_tasks,
                "skipped": stats.planned_skipped,
            },
            "outcomes": dict(sorted(stats.segment_results.items())),
        }
        if denominator:
            row["per_1000_source_segments"] = {
                "elapsed_seconds": round(elapsed / denominator, 3),
                "llm_calls": round(stats.llm_calls / denominator, 3),
                "total_tokens": round(total_tokens / denominator, 3),
            }
        subsections = {
            role: _llm_role_stats_payload(role_stats)
            for role, role_stats in _stage_role_subsections(context, stage)
        }
        if subsections:
            row["subsections"] = subsections
        stage_rows[stage] = row
        totals["elapsed_seconds"] += elapsed
        totals["llm_calls"] += stats.llm_calls
        totals["completed_llm_calls"] += stats.completed_llm_calls
        totals["prompt_tokens"] += stats.prompt_tokens
        totals["output_tokens"] += stats.output_tokens
        totals["llm_seconds"] += stats.llm_seconds
    totals = {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in totals.items()
    }
    if denominator:
        totals["per_1000_source_segments"] = {
            "elapsed_seconds": round(
                float(totals["elapsed_seconds"]) / denominator, 3
            ),
            "llm_calls": round(int(totals["llm_calls"]) / denominator, 3),
            "total_tokens": round(
                (int(totals["prompt_tokens"]) + int(totals["output_tokens"]))
                / denominator,
                3,
            ),
        }
    role_rows = {
        role: _llm_role_stats_payload(stats)
        for role, stats in context.role_stats.items()
    }
    return {
        "schema_version": 3,
        "session": context.session_hash,
        "source_segment_count": context.source_segment_count,
        "stages": stage_rows,
        "llm_roles": role_rows,
        "totals": totals,
    }


def _print_processing_summary(context: CliProgressContext, workspace=None) -> None:
    for line in _format_processing_summary(context):
        print(line, file=sys.stderr, flush=True)
        _write_session_log(context, line)
    if workspace is not None and context.stage_stats:
        report_path = workspace.directory("reports") / (
            f"session-performance-{context.session_hash}.json"
        )
        atomic_write_text(
            report_path,
            format_json(_processing_summary_payload(context)) + "\n",
        )
        _log_cli_message(
            context,
            f"performance report={report_path.relative_to(workspace.root).as_posix()}",
        )


def _timestamp() -> str:
    """Return an unambiguous local timestamp for CLI progress lines."""
    return f"[{datetime.now().astimezone().isoformat(timespec='seconds')}]"


def _progress_prefix(context: CliProgressContext) -> str:
    return (
        f"{_timestamp()} [stage={context.stage}] "
        f"[session={context.session_hash}]"
    )


def _format_plain_progress(event: ProgressEvent, context: CliProgressContext) -> str:
    suffix = f": {event.message}" if event.message else ""
    return f"{_progress_prefix(context)} [{event.status}]{suffix}"


def _start_session_log(
    context: CliProgressContext,
    workspace,
    command: str,
) -> Path | None:
    """Create one append-only plain-text diagnostic log per CLI invocation."""
    directory = getattr(workspace, "directory", None)
    if not callable(directory):
        return None
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    log_root = directory("logs")
    log_root.mkdir(parents=True, exist_ok=True)
    log_file = log_root / (
        f"{timestamp}-{context.session_hash}-{command}.log"
    )
    context.log_file = log_file
    try:
        context.source_segment_count = load_preprocessing_report(
            workspace
        ).segment_count
    except (FileNotFoundError, OSError, ValueError):
        # A new run has no preprocessing report yet. Segment progress will fill
        # the denominator later without making logging a workflow dependency.
        pass
    _log_cli_message(context, f"{command} started; workspace={workspace.root}")
    return log_file


def _write_session_log(context: CliProgressContext, line: str) -> None:
    """Append a rendered CLI diagnostic line when a workspace log is active."""
    if context.log_file is None:
        return
    with context.log_file.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line.rstrip("\r\n") + "\n")


def _log_cli_message(context: CliProgressContext, message: str) -> None:
    _write_session_log(context, f"{_progress_prefix(context)} [cli] {message}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run a CLI command and return its stable process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "approve":
        glossary_approval = bool(args.glossary or args.llm_glossary)
        if glossary_approval == bool(args.final):
            parser.error(
                "approve requires either --final or glossary approval via "
                "--glossary/--llm-glossary; --glossary may be combined with "
                "--llm-glossary"
            )
    if args.command == "resolve-review" and args.resume and not args.approve_final:
        parser.error("resolve-review --resume requires --approve-final")
    progress_context = CliProgressContext()
    try:
        if args.command == "config":
            config = load_config(args.file) if args.file else AppConfig()
            print(format_config(config))
            return ExitCode.COMPLETE
        if args.command == "config-diff":
            workspace = open_job_workspace(args.workspace)
            diff = build_config_diff(workspace, args.config)
            print(format_json(diff) if args.json else format_config_diff_plain(diff))
            return ExitCode.COMPLETE
        if args.command == "styles":
            print("\n".join(list_style_names()))
            return ExitCode.COMPLETE
        if args.command == "run":
            config_path = Path(args.config_file).resolve() if args.config_file else None
            config = load_config(config_path) if config_path else AppConfig()
            config = resolve_config_paths(config, config_path.parent if config_path else Path.cwd())
            runs = Path(args.runs).resolve() if args.runs else config.paths.runs
            if args.dry_run:
                print(format_json(build_dry_run_summary(args.source, config, runs)))
                return ExitCode.COMPLETE
            workspace = create_job_workspace(
                args.source,
                runs,
                config,
                job_id=args.job_id,
                config_source=config_path,
            )
            _start_session_log(progress_context, workspace, "run")
            print(f"Workspace: {workspace.root}")
            result = run_workflow(
                workspace,
                config,
                progress=make_progress_printer(
                    plain=args.plain, context=progress_context
                ),
                generation_progress=make_generation_progress_printer(
                    plain=args.plain, context=progress_context
                ),
            )
            _print_processing_summary(progress_context, workspace)
            if result.message:
                print(result.message, file=sys.stderr)
                _log_cli_message(progress_context, result.message)
            _log_cli_message(progress_context, f"run finished; exit_code={result.exit_code}")
            return result.exit_code
        if args.command == "resume":
            workspace = open_job_workspace(args.workspace)
            _start_session_log(progress_context, workspace, "resume")
            result = run_workflow(
                workspace,
                progress=make_progress_printer(
                    plain=args.plain, context=progress_context
                ),
                generation_progress=make_generation_progress_printer(
                    plain=args.plain, context=progress_context
                ),
            )
            _print_processing_summary(progress_context, workspace)
            if result.message:
                print(result.message, file=sys.stderr)
                _log_cli_message(progress_context, result.message)
            _log_cli_message(progress_context, f"resume finished; exit_code={result.exit_code}")
            return result.exit_code
        if args.command == "status":
            snapshot = workflow_status(open_job_workspace(args.workspace))
            print(format_json(snapshot) if args.json else format_status_plain(snapshot))
            return _status_exit_code(str(snapshot["overall"]))
        if args.command == "approve":
            workspace = open_job_workspace(args.workspace)
            _start_session_log(progress_context, workspace, "approve")
            if args.glossary or args.llm_glossary:
                progress_context.stage = WorkflowStage.APPROVE_GLOSSARY.value
                approve_glossary(
                    workspace,
                    args.glossary,
                    llm_review=args.llm_glossary,
                    generation_progress=make_generation_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                )
                method = "LLM-reviewed" if args.llm_glossary else "human-reviewed"
                print(f"Glossary approved ({method}).")
                _log_cli_message(progress_context, f"Glossary approved ({method}).")
            else:
                approve_final_draft(workspace)
                print("Final repaired draft approved.")
                _log_cli_message(progress_context, "Final repaired draft approved.")
            if args.resume:
                result = run_workflow(
                    workspace,
                    progress=make_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                    generation_progress=make_generation_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                )
                _print_processing_summary(progress_context, workspace)
                if result.message:
                    _log_cli_message(progress_context, result.message)
                _log_cli_message(
                    progress_context,
                    f"approve/resume finished; exit_code={result.exit_code}",
                )
                return result.exit_code
            _log_cli_message(progress_context, "approve finished; exit_code=0")
            return ExitCode.COMPLETE
        if args.command == "retry":
            workspace = open_job_workspace(args.workspace)
            _start_session_log(progress_context, workspace, "retry")
            affected = (
                retry_failed_from_stage(workspace, WorkflowStage(args.stage))
                if args.failed_only
                else retry_from_stage(workspace, WorkflowStage(args.stage))
            )
            if args.failed_only and not affected:
                warning = (
                    f"warning: stage has no failed work units: {args.stage}; "
                    "continuing without reset"
                )
                print(warning, file=sys.stderr)
                _log_cli_message(progress_context, warning)
            else:
                reset = "Reset: " + ", ".join(stage.value for stage in affected)
                print(reset)
                _log_cli_message(progress_context, reset)
            if args.resume:
                result = run_workflow(
                    workspace,
                    progress=make_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                    generation_progress=make_generation_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                )
                _print_processing_summary(progress_context, workspace)
                if result.message:
                    _log_cli_message(progress_context, result.message)
                _log_cli_message(
                    progress_context,
                    f"retry/resume finished; exit_code={result.exit_code}",
                )
                return result.exit_code
            _log_cli_message(progress_context, "retry finished; exit_code=0")
            return ExitCode.COMPLETE
        if args.command == "report":
            report = workflow_report(open_job_workspace(args.workspace))
            if args.json:
                print(format_json(report))
            else:
                print(format_status_plain(report))
                print(f"\nArtifacts: {len(report['artifacts'])}")
                for artifact in report["reports"]:
                    print(f"- {artifact['path']}")
            return _status_exit_code(str(report["overall"]))
        if args.command == "build-series-glossary":
            if args.config_file and not args.llm_conflicts:
                raise ValueError("--config requires --llm-conflicts")
            if args.runs:
                runs = Path(args.runs).resolve()
                if not runs.is_dir():
                    raise FileNotFoundError(runs)
                workspace_paths = sorted(
                    (path for path in runs.glob(args.job_pattern) if path.is_dir()),
                    key=lambda path: path.name.casefold(),
                )
                if not workspace_paths:
                    raise ValueError(
                        f"no book workspaces matched {args.job_pattern!r} in {runs}"
                    )
            else:
                if args.job_pattern != "*":
                    raise ValueError("--job-pattern can only be used with --runs")
                workspace_paths = args.workspaces
            series_config_path = (
                Path(args.config_file).resolve() if args.config_file else None
            )
            series_config = (
                resolve_config_paths(
                    load_config(series_config_path), series_config_path.parent
                )
                if series_config_path is not None
                else AppConfig()
            )
            if args.llm_conflicts:
                progress_context.stage = "series_glossary"
            summary = build_series_glossary_from_workspaces(
                workspace_paths,
                args.output,
                report_path=args.report,
                legacy_path=args.legacy,
                minimum_books=args.minimum_books,
                consensus_ratio=args.consensus_ratio,
                source_stage=args.source_stage,
                book_output_dir=args.book_output_dir,
                llm_conflicts=args.llm_conflicts,
                config=series_config,
                generation_progress=(
                    make_generation_progress_printer(
                        plain=args.plain, context=progress_context
                    )
                    if args.llm_conflicts
                    else None
                ),
            )
            print(format_json(summary))
            return ExitCode.COMPLETE
        if args.command == "resolve-review":
            workspace = open_job_workspace(args.workspace)
            _start_session_log(progress_context, workspace, "resolve-review")
            payload = load_manual_review_resolution_file(
                workspace,
                args.resolutions,
            )
            report = resolve_manual_review(workspace, payload)
            print(report.model_dump_json(indent=2))
            if args.approve_final:
                if not report.passed:
                    raise ValueError(
                        "manual review still has unresolved segments; final approval refused"
                    )
                approve_final_draft(workspace)
                print("Final repaired draft approved.")
                _log_cli_message(progress_context, "Final repaired draft approved.")
            if args.resume:
                result = run_workflow(
                    workspace,
                    progress=make_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                    generation_progress=make_generation_progress_printer(
                        plain=args.plain, context=progress_context
                    ),
                )
                _print_processing_summary(progress_context, workspace)
                if result.message:
                    _log_cli_message(progress_context, result.message)
                _log_cli_message(
                    progress_context,
                    f"resolve-review/resume finished; exit_code={result.exit_code}",
                )
                return result.exit_code
            return ExitCode.COMPLETE if report.passed else ExitCode.PAUSED
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        _log_cli_message(progress_context, "cancelled")
        return ExitCode.CANCELLED
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        _log_cli_message(progress_context, f"error: {error}")
        return ExitCode.FAILED

    parser.print_help()
    return ExitCode.COMPLETE


def _status_exit_code(overall: str) -> ExitCode:
    if overall == "complete":
        return ExitCode.COMPLETE
    if overall == "paused":
        return ExitCode.PAUSED
    if overall == "failed":
        return ExitCode.FAILED
    return ExitCode.COMPLETE


def entrypoint() -> None:
    """Installed console-script entry point."""
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
