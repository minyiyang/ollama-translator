import contextlib
import io
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent.cli import (
    CliProgressContext,
    _format_processing_summary,
    _processing_summary_payload,
    build_dry_run_summary,
    build_config_diff,
    build_parser,
    entrypoint,
    format_config,
    list_style_names,
    main,
    make_generation_progress_printer,
    make_progress_printer,
    resolve_config_paths,
)
from book_agent.config import AppConfig
from book_agent.ollama_client import GenerationMetrics, GenerationProgressEvent
from book_agent.styles import TranslationStyle
from book_agent.workflow import ProgressEvent


class CliTests:
    def test_build_parser_parses_config_command(self) -> None:
        args = build_parser().parse_args(["config", "--file", "config.yaml"])
        assert args.command == "config"
        assert args.file == "config.yaml"

    def test_build_parser_parses_all_operational_commands(self) -> None:
        assert build_parser().parse_args(["run", "book.epub"]).command == "run"
        assert build_parser().parse_args(["resume", "job"]).command == "resume"
        assert build_parser().parse_args(["status", "job", "--json"]).json
        config_diff = build_parser().parse_args(
            ["config-diff", "job", "--config", "next.yaml", "--json"]
        )
        assert config_diff.command == "config-diff"
        assert config_diff.json
        assert build_parser().parse_args(["approve", "job", "--final"]).final
        assert (build_parser().parse_args(
                ["approve", "job", "--llm-glossary"]
            ).llm_glossary)
        overlay_approval = build_parser().parse_args(
            [
                "approve",
                "job",
                "--glossary",
                "qel-overlay.json",
                "--llm-glossary",
            ]
        )
        assert overlay_approval.glossary == "qel-overlay.json"
        assert overlay_approval.llm_glossary
        retry = build_parser().parse_args(["retry", "job", "--stage", "translate"])
        assert retry.stage == "translate"
        failed_only = build_parser().parse_args(
            ["retry", "job", "--stage", "translate", "--failed-only"]
        )
        assert failed_only.failed_only
        assert build_parser().parse_args(["report", "job"]).command == "report"
        resolved = build_parser().parse_args(
            [
                "resolve-review",
                "job",
                "resolutions.json",
                "--approve-final",
                "--resume",
            ]
        )
        assert resolved.resolutions == "resolutions.json"
        assert resolved.approve_final
        assert resolved.resume
        series = build_parser().parse_args(
            [
                "build-series-glossary",
                "--runs",
                "qel-runs",
                "--job-pattern",
                "qel-volume-*",
                "--output",
                "qel-series.json",
                "--source-stage",
                "resolved",
                "--book-output-dir",
                "qel-overlays",
            ]
        )
        assert series.runs == "qel-runs"
        assert series.job_pattern == "qel-volume-*"
        assert series.consensus_ratio == 1.0
        assert series.source_stage == "resolved"
        assert series.book_output_dir == "qel-overlays"
        assert not series.llm_conflicts
        llm_series = build_parser().parse_args(
            [
                "build-series-glossary",
                "--workspace",
                "qel-volume-01",
                "--workspace",
                "qel-volume-02",
                "--output",
                "qel-series.json",
                "--llm-conflicts",
                "--config",
                "config.yaml",
            ]
        )
        assert llm_series.llm_conflicts
        assert llm_series.config_file == "config.yaml"

    def test_format_config_returns_valid_resolved_json(self) -> None:
        rendered = format_config(AppConfig())
        data = json.loads(rendered)
        assert data["ollama"]["model"] == "qwen3.8:latest"
        assert data["translation"]["direction"] == "en-zh"

    def test_build_config_diff_reports_effective_leaf_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "next.yaml")
            path.write_text(
                "workflow:\n  require_final_review: true\n",
                encoding="utf-8",
            )

            class Workspace:
                root = Path(directory, "job")

            with patch("book_agent.cli.load_workspace_config", return_value=AppConfig()):
                diff = build_config_diff(Workspace(), path)
            assert diff["changed"]
            assert "workflow.require_final_review" in [item["path"] for item in diff["changes"]]

    def test_list_style_names_includes_every_enum_value(self) -> None:
        assert list_style_names() == [style.value for style in TranslationStyle]

    def test_main_without_command_prints_help(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main([])
        assert result == 0
        assert "usage:" in output.getvalue()

    def test_main_styles_prints_available_styles(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["styles"])
        assert result == 0
        assert output.getvalue().splitlines() == list_style_names()

    def test_main_build_series_glossary_selects_matching_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # The CLI resolves workspace paths, and a Windows temporary directory
            # can be an 8.3 short path, so expectations must be resolved too.
            root = Path(directory).resolve()
            runs = root / "qel-runs"
            first = runs / "qel-volume-01"
            second = runs / "qel-volume-02"
            ignored = runs / "nul-volume-01"
            for path in (first, second, ignored):
                path.mkdir(parents=True)
            output = io.StringIO()
            summary = {
                "result": "completed",
                "included_term_count": 3,
            }
            with (
                patch(
                    "book_agent.cli.build_series_glossary_from_workspaces",
                    return_value=summary,
                ) as builder,
                contextlib.redirect_stdout(output),
            ):
                result = main(
                    [
                        "build-series-glossary",
                        "--runs",
                        str(runs),
                        "--job-pattern",
                        "qel-volume-*",
                        "--output",
                        str(root / "qel-series.json"),
                    ]
                )

            assert result == 0
            selected = builder.call_args.args[0]
            assert selected == [first, second]
            assert json.loads(output.getvalue()) == summary

    def test_main_build_series_glossary_passes_llm_conflict_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory, "qel-volume-01")
            second = Path(directory, "qel-volume-02")
            first.mkdir()
            second.mkdir()
            config = Path(directory, "config.yaml")
            config.write_text("glossary:\n  resolution_chunk_tokens: 4000\n", encoding="utf-8")
            output = io.StringIO()
            with (
                patch(
                    "book_agent.cli.build_series_glossary_from_workspaces",
                    return_value={"result": "completed"},
                ) as builder,
                contextlib.redirect_stdout(output),
            ):
                result = main(
                    [
                        "build-series-glossary",
                        "--workspace",
                        str(first),
                        "--workspace",
                        str(second),
                        "--output",
                        str(Path(directory, "qel-series.json")),
                        "--llm-conflicts",
                        "--config",
                        str(config),
                        "--plain",
                    ]
                )

            assert result == 0
            assert builder.call_args.kwargs["llm_conflicts"]
            assert builder.call_args.kwargs["config"].glossary.resolution_chunk_tokens == 4_000
            assert builder.call_args.kwargs["generation_progress"] is not None

    def test_main_config_prints_defaults(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["config"])
        assert result == 0
        assert json.loads(output.getvalue())["budget"]["working_limit"] == 65_536

    def test_main_config_loads_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.yaml")
            path.write_text("translation:\n  direction: zh-en\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["config", "--file", str(path)])
            assert result == 0
            assert json.loads(output.getvalue())["translation"]["direction"] == "zh-en"

    def test_entrypoint_exits_with_main_result(self) -> None:
        with patch("book_agent.cli.main", return_value=7):
            with pytest.raises(SystemExit) as raised:
                entrypoint()
        assert raised.value.code == 7

    def test_resolve_config_paths_uses_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = AppConfig.model_validate(
                {
                    "glossary": {"seed_glossaries": ["seed.txt"]},
                    "paths": {"runs": "jobs", "prompts": "prompts"},
                }
            )
            resolved = resolve_config_paths(config, base)
            assert resolved.glossary.seed_glossaries == [(base / "seed.txt").resolve()]
            assert resolved.paths.runs == (base / "jobs").resolve()

    def test_build_dry_run_summary_validates_source_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.epub"
            source.write_bytes(b"epub")
            summary = build_dry_run_summary(source, AppConfig(), root / "runs")
            assert summary["dry_run"]
            assert summary["stages"][0] == "decompile"
            assert summary["glossary_extraction_model"] == "qwen3.8:27b"
            assert summary["glossary_extraction_enabled"]
            assert summary["source_format"] == "epub"
            assert not (root / "runs").exists()
            rtf = root / "qelm.rtf"
            rtf.write_bytes(rb"{\rtf1 Zorvak}")
            rtf_summary = build_dry_run_summary(rtf, AppConfig(), root / "runs")
            assert rtf_summary["source_format"] == "rtf"
            bad = root / "sample.txt"
            bad.write_text("text", encoding="utf-8")
            with pytest.raises(ValueError, match="EPUB"):
                build_dry_run_summary(bad, AppConfig(), root / "runs")
            with pytest.raises(FileNotFoundError):
                build_dry_run_summary(root / "missing.epub", AppConfig(), root / "runs")

    def test_plain_progress_printer_writes_event(self) -> None:
        error = io.StringIO()
        context = CliProgressContext(session_hash="abc12345")
        with contextlib.redirect_stderr(error):
            make_progress_printer(plain=True, context=context)(
                ProgressEvent("translate", "completed")
            )
        assert "[stage=translate] [session=abc12345] [completed]" in error.getvalue()

    def test_progress_printers_also_write_plain_session_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory, "session.log")
            context = CliProgressContext(
                session_hash="abc12345",
                stage="translate",
                log_file=log_file,
            )
            with contextlib.redirect_stderr(io.StringIO()):
                make_progress_printer(plain=True, context=context)(
                    ProgressEvent("translate", "running")
                )
                make_generation_progress_printer(plain=True, context=context)(
                    GenerationProgressEvent(
                        "started",
                        "qwen",
                        context_size=16_384,
                        prompt_tokens_estimate=2_000,
                    )
                )

            rendered = log_file.read_text(encoding="utf-8")
            assert "[session=abc12345] [running]" in rendered
            assert "generation started, prompt ~2,000 tokens, context 16,384" in rendered

    def test_file_processing_summary_aggregates_stage_llm_metrics(self) -> None:
        context = CliProgressContext(session_hash="abc12345")
        progress = make_progress_printer(plain=True, context=context)
        generation = make_generation_progress_printer(plain=True, context=context)
        with (
            patch("book_agent.cli.time.monotonic", side_effect=[100.0, 102.5]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            progress(ProgressEvent("translate", "running"))
            generation(
                GenerationProgressEvent(
                    "started",
                    "qwen",
                    context_size=16_384,
                    prompt_tokens_estimate=120,
                    role="translate.draft.primary",
                )
            )
            generation(
                GenerationProgressEvent(
                    "completed",
                    "qwen",
                    200,
                    GenerationMetrics(
                        total_duration_ns=1_500_000_000,
                        load_duration_ns=200_000_000,
                        prompt_eval_duration_ns=300_000_000,
                        eval_duration_ns=1_000_000_000,
                        prompt_eval_count=100,
                        eval_count=40,
                    ),
                    context_size=16_384,
                    role="translate.draft.primary",
                )
            )
            progress(ProgressEvent("translate", "completed"))

        rendered = "\n".join(_format_processing_summary(context))
        assert "[file-summary] processing summary" in rendered
        assert re.search(r"translate\s+completed\s+2\.5s", rendered)
        assert "load time" in rendered
        assert "prompt time" in rendered
        assert "decode time" in rendered
        assert re.search(r"1\s+1\s+100\s+40\s+140\s+100\s+1\.5s\s+0\.2s\s+0\.3s\s+1\.0s", rendered)
        assert "TOTAL" in rendered
        assert "LLM usage by pipeline role" in rendered
        assert "translate.draft.primary" in rendered
        assert "qwen@16384:1" in rendered

    def test_file_processing_summary_nests_audit_subsections(self) -> None:
        context = CliProgressContext(
            session_hash="abc12345", stage="audit_translation"
        )
        progress = make_progress_printer(plain=True, context=context)
        generation = make_generation_progress_printer(plain=True, context=context)
        with (
            patch("book_agent.cli.time.monotonic", side_effect=[100.0, 103.0]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            progress(ProgressEvent("audit_translation", "running"))
            for role, model in (
                ("audit.semantic", "gemma4:31b"),
                ("audit.quantity.base", "gemma4:26b"),
                ("audit.quantity.escalation", "gemma4:31b"),
            ):
                generation(
                    GenerationProgressEvent(
                        "started",
                        model,
                        context_size=16_384,
                        prompt_tokens_estimate=100,
                        role=role,
                    )
                )
                generation(
                    GenerationProgressEvent(
                        "completed",
                        model,
                        metrics=GenerationMetrics(
                            total_duration_ns=1_000_000_000,
                            prompt_eval_count=80,
                            eval_count=20,
                        ),
                        context_size=16_384,
                        role=role,
                    )
                )
            progress(ProgressEvent("audit_translation", "completed"))

        rendered = "\n".join(_format_processing_summary(context))
        assert re.search(r"audit_translation\s+completed\s+3\.0s", rendered)
        assert re.search(r"audit\.semantic\s+subsection", rendered)
        assert re.search(r"audit\.quantity\.base\s+subsection", rendered)
        assert re.search(r"audit\.quantity\.escalation\s+subsection", rendered)

        payload = _processing_summary_payload(context)
        assert payload["schema_version"] == 3
        subsections = payload["stages"]["audit_translation"]["subsections"]
        assert list(subsections) == ([
                "audit.semantic",
                "audit.quantity.base",
                "audit.quantity.escalation",
            ])
        assert subsections["audit.semantic"]["llm_calls"] == 1

    def test_file_processing_summary_normalizes_work_by_source_segments(self) -> None:
        context = CliProgressContext(session_hash="abc12345", stage="reprose")
        generation = make_generation_progress_printer(plain=True, context=context)
        with contextlib.redirect_stderr(io.StringIO()):
            generation(
                GenerationProgressEvent(
                    "stage_plan",
                    "qwen",
                    prescreened=5_000,
                    llm_tasks=1_250,
                    skipped=3_750,
                )
            )
            generation(
                GenerationProgressEvent(
                    "started", "qwen", prompt_tokens_estimate=100
                )
            )
            generation(
                GenerationProgressEvent(
                    "completed",
                    "qwen",
                    metrics=GenerationMetrics(
                        total_duration_ns=2_000_000_000,
                        prompt_eval_count=120,
                        eval_count=30,
                    ),
                )
            )
            generation(
                GenerationProgressEvent(
                    "segment_result",
                    "qwen",
                    result="repaired",
                    result_index=1,
                    result_total=5_000,
                )
            )

        rendered = "\n".join(_format_processing_summary(context))
        assert "per 1,000 source segments" in rendered
        assert "book denominator: 5,000" in rendered
        assert re.search(r"reprose\s+0\.2\s+30\s+", rendered)
        assert "1250/3750" in rendered
        assert "repaired=1" in rendered

    def test_plain_generation_progress_prints_streaming_and_final_token_metrics(self) -> None:
        context = CliProgressContext(session_hash="abc12345", stage="translate")
        printer = make_generation_progress_printer(plain=True, context=context)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            printer(
                GenerationProgressEvent(
                    "started",
                    "qwen",
                    context_size=32_768,
                    prompt_tokens_estimate=12_345,
                    label="chunk=translate-0002-00001 attempt=1/4 mode=full",
                )
            )
            printer(
                GenerationProgressEvent(
                    "heartbeat",
                    "qwen",
                    2_015,
                    thinking_chars=900,
                    label="chunk=translate-0002-00001 attempt=2/4 mode=focused passages=1",
                )
            )
            printer(
                GenerationProgressEvent(
                    "validation_failed",
                    "qwen",
                    label="chunk=translate-0002-00001 attempt=1/4 mode=full",
                    message="protected_marker_mismatch; retrying",
                )
            )
            printer(
                GenerationProgressEvent(
                    "completed",
                    "qwen",
                    3_000,
                    GenerationMetrics(
                        total_duration_ns=10_000_000_000,
                        load_duration_ns=500_000_000,
                        prompt_eval_count=100,
                        prompt_eval_duration_ns=1_500_000_000,
                        eval_count=40,
                        eval_duration_ns=8_000_000_000,
                    ),
                )
            )
        rendered = error.getvalue()
        assert "streaming — thinking 900 chars, visible output 2,015 chars" in rendered
        assert "generation started, prompt ~12,345 tokens, context 32,768" in rendered
        assert "prompt 100 tokens, output 40 tokens" in rendered
        assert "5.0 tok/s" in rendered
        assert "load 0.5s, prompt 1.5s, decode 8.0s" in rendered
        assert "thinking not observed" in rendered
        assert "[stage=translate] [session=abc12345]" in rendered
        assert "attempt=2/4 mode=focused passages=1" in rendered
        assert "validation failed" in rendered
        assert "protected_marker_mismatch; retrying" in rendered
        assert re.search(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}\]", rendered)

    def test_obfuscated_stage_plan_and_segment_result_are_printed(self) -> None:
        context = CliProgressContext(session_hash="zxc12345", stage="audit_z")
        printer = make_generation_progress_printer(plain=True, context=context)
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            printer(
                GenerationProgressEvent(
                    "stage_plan",
                    "mdl_z",
                    label="stage-plan=audit_z",
                    message="result=pending; prescreened=3; llm_tasks=1; skipped=2",
                )
            )
            printer(
                GenerationProgressEvent(
                    "segment_result",
                    "mdl_z",
                    label="segment=D_z-S_z stage=audit_z",
                    message="result=passed; mode=deterministic-prescreen; issues=0",
                )
            )

        rendered = error.getvalue()
        assert "[schedule] result=pending" in rendered
        assert "[segment] result=passed" in rendered
        assert "segment=D_z-S_z" in rendered

    def test_main_run_dry_run_does_not_create_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.epub"
            source.write_bytes(b"epub")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["run", str(source), "--runs", str(root / "runs"), "--dry-run"])
            assert result == 0
            assert json.loads(output.getvalue())["dry_run"]
            assert not (root / "runs").exists()

    def test_main_reports_clean_error_for_missing_workspace(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            result = main(["status", "does-not-exist"])
        assert result == 1
        assert "error:" in error.getvalue()

    def test_retry_failed_only_warns_without_blocking_when_none_failed(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        with (
            patch("book_agent.cli.open_job_workspace", return_value=object()),
            patch("book_agent.cli.retry_failed_from_stage", return_value=[]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            result = main(
                [
                    "retry",
                    "job",
                    "--stage",
                    "translate",
                    "--failed-only",
                ]
            )
        assert result == 0
        assert "warning: stage has no failed work units" in error.getvalue()
        assert "continuing without reset" in error.getvalue()

