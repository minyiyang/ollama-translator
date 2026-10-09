"""What each command does with its arguments: the work itself is tested with the stage it belongs to."""

import contextlib
import io
import json
import runpy
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from book_agent import cli
from book_agent.cli import (
    _run_series_command,
    _series_config,
    _status_exit_code,
    build_parser,
    build_series_glossary_from_workspaces,
    main,
)
from book_agent.config import AppConfig
from book_agent.ollama_client import PauseRequested
from book_agent.pipeline_state import WorkflowStage
from book_agent.schemas import GlossaryCategory, GlossaryResult
from book_agent.stages.glossary import run_glossary_extraction_stage, run_glossary_resolution_stage
from book_agent.workflow import ExitCode, load_workspace_config, workflow_status
from tests.test_glossary_stages import FakeGlossaryClient, entry, make_workspace, resolution_result
from tests.test_web_ui import paused_glossary_workspace


def run(argv: list[str]) -> tuple[int, str, str]:
    output, error = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        code = main(argv)
    return code, output.getvalue(), error.getvalue()


def finished(exit_code: ExitCode = ExitCode.COMPLETE, message: str = ""):
    return SimpleNamespace(exit_code=exit_code, message=message)


def session_log(workspace) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((workspace.root / "logs").glob("*.log")))


@pytest.fixture
def job():
    with tempfile.TemporaryDirectory() as directory:
        workspace, _ = paused_glossary_workspace(Path(directory))
        yield workspace


class RunAndResumeTests:
    def test_run_creates_the_workspace_and_reports_how_the_workflow_ended(self, job):
        base = job.root.parent.parent
        with patch.object(cli, "run_workflow", return_value=finished(ExitCode.PAUSED, "paused at the glossary gate")) as workflow:
            code, output, error = run(
                ["run", str(base / "fixture.epub"), "--runs", str(base / "more"), "--job-id", "second", "--plain"]
            )
        created = workflow.call_args.args[0]
        assert code == ExitCode.PAUSED and created.root == (base / "more" / "second").resolve()
        assert f"Workspace: {created.root}" in output and "paused at the glossary gate" in error
        log = session_log(created)
        assert "run started" in log and "paused at the glossary gate" in log and "run finished; exit_code=2" in log

    def test_run_says_what_a_subtitle_job_changes_in_the_config(self, job):
        base = job.root.parent.parent
        with patch.object(cli, "run_workflow", return_value=finished()), \
             patch.object(cli, "subtitle_config", side_effect=lambda source, config: (config, "subtitle job: prose rewrite is off")):
            code, _, error = run(["run", str(base / "fixture.epub"), "--runs", str(base / "more"), "--job-id", "third"])
        assert code == ExitCode.COMPLETE and "subtitle job: prose rewrite is off" in error

    def test_run_refuses_an_output_format_the_job_cannot_be_given_before_any_work(self, job):
        base = job.root.parent.parent
        argv = ["run", str(base / "fixture.epub"), "--runs", str(base / "more"), "--job-id", "refused"]
        with patch.object(cli, "run_workflow") as workflow, \
             patch.object(cli, "check_output", side_effect=ValueError("no font of this system has the letters")):
            code, _, error = run(argv)
        assert code == ExitCode.FAILED and "error: no font of this system has the letters" in error
        workflow.assert_not_called()
        assert not (base / "more" / "refused").exists()
        # A format that was quietly replaced is said, and the job runs.
        with patch.object(cli, "run_workflow", return_value=finished()), \
             patch.object(cli, "check_output", return_value="the book is written as an EPUB, not a PDF: no font"):
            code, _, error = run(argv)
        assert code == ExitCode.COMPLETE and "written as an EPUB, not a PDF" in error

    def test_a_dry_run_names_the_output_format_and_resolves_the_font_beside_the_config(self, job):
        base = job.root.parent.parent
        config = base / "job.yaml"
        config.write_text("output:\n  format: docx\n  pdf_font: fonts/serif.ttf\n", encoding="utf-8")
        code, output, _ = run(["run", str(base / "fixture.epub"), "--config", str(config), "--dry-run"])
        assert code == ExitCode.COMPLETE and json.loads(output)["output_format"] == "docx"
        resolved = cli.resolve_config_paths(cli.load_config(config), base)
        assert resolved.output.pdf_font == (base / "fonts" / "serif.ttf").resolve()
        assert cli.resolve_config_paths(AppConfig(), base).output.pdf_font is None

    def test_resume_continues_the_job(self, job):
        with patch.object(cli, "run_workflow", return_value=finished(ExitCode.FAILED, "translate failed")) as workflow:
            code, _, error = run(["resume", str(job.root), "--plain"])
        assert code == ExitCode.FAILED and workflow.call_args.args[0].root == job.root
        assert "translate failed" in error
        assert "resume finished; exit_code=1" in session_log(job)

    def test_pause_leaves_a_request_for_the_running_job(self, job):
        with patch.object(cli, "request_pause") as request:
            code, output, _ = run(["pause", str(job.root)])
        assert code == ExitCode.COMPLETE and "Pause requested" in output
        assert request.call_args.args[0].root == job.root


class StatusAndReportTests:
    def test_status_exit_code_follows_the_job(self, job):
        overall = workflow_status(job)["overall"]
        code, output, _ = run(["status", str(job.root)])
        assert code == _status_exit_code(overall) and overall in output
        code, output, _ = run(["status", str(job.root), "--json"])
        assert json.loads(output)["overall"] == overall

    def test_exit_codes_for_each_overall_state(self):
        assert _status_exit_code("complete") == ExitCode.COMPLETE
        assert _status_exit_code("paused") == ExitCode.PAUSED
        assert _status_exit_code("failed") == ExitCode.FAILED
        assert _status_exit_code("running") == ExitCode.COMPLETE

    def test_report_lists_the_reports_or_prints_everything_as_json(self, job):
        code, output, _ = run(["report", str(job.root)])
        assert code == _status_exit_code(workflow_status(job)["overall"])
        assert "Artifacts: " in output
        code, output, _ = run(["report", str(job.root), "--json"])
        report = json.loads(output)
        assert "artifacts" in report and "reports" in report

        fake = {"overall": "failed", "artifacts": [1, 2], "reports": [{"path": "reports/audit.json"}]}
        with patch.object(cli, "workflow_report", return_value=fake), patch.object(cli, "format_status_plain", return_value="status"):
            code, output, _ = run(["report", str(job.root)])
        assert code == ExitCode.FAILED and output == "status\n\nArtifacts: 2\n- reports/audit.json\n"

    def test_config_diff_shows_what_a_new_config_would_change(self, job):
        captured = load_workspace_config(job)
        proposed = job.root.parent / "next.yaml"
        proposed.write_text(f"ollama:\n  model: other-model:1b\n", encoding="utf-8")
        code, output, _ = run(["config-diff", str(job.root), "--config", str(proposed)])
        assert code == ExitCode.COMPLETE and "Changed: yes" in output
        assert f"- ollama.model: {captured.ollama.model!r} -> 'other-model:1b'" in output
        assert output.rstrip().endswith("retry affected stages explicitly before resuming.")
        code, output, _ = run(["config-diff", str(job.root), "--config", str(proposed), "--json"])
        assert {"path": "ollama.model", "captured": captured.ollama.model, "proposed": "other-model:1b"} in json.loads(output)["changes"]


class ExportTests:
    def test_export_writes_the_compiled_book_in_the_asked_format(self, job):
        compiled = job.root / "output" / "book.epub"
        with patch.object(cli, "load_compiled_epub_path", return_value=str(compiled)), \
             patch.object(cli, "export_book", side_effect=lambda source, target, kind, pdf_font=None: target) as export:
            code, output, _ = run(["export", str(job.root), "--format", "txt"])
            assert code == ExitCode.COMPLETE and output.strip() == str(compiled.with_suffix(".txt"))
            code, output, _ = run(["export", str(job.root), "--format", "md", "--out", "elsewhere.md"])
            assert output.strip() == "elsewhere.md" and export.call_args.args == (compiled, Path("elsewhere.md"), "md")
            assert export.call_args.kwargs == {"pdf_font": None}
            run(["export", str(job.root), "--format", "pdf", "--pdf-font", "serif.ttf"])
            assert export.call_args.args[2] == "pdf" and export.call_args.kwargs == {"pdf_font": "serif.ttf"}

    def test_a_subtitle_job_has_no_book_to_export(self, job):
        with patch.object(cli, "load_compiled_epub_path", return_value=str(job.root / "output" / "film.srt")):
            code, _, error = run(["export", str(job.root), "--format", "txt"])
        assert code == ExitCode.FAILED and "there is no book to export" in error


class ApproveTests:
    def test_approve_needs_one_gate_to_approve(self, job):
        for argv in (["approve", str(job.root)], ["approve", str(job.root), "--final", "--llm-glossary"]):
            with pytest.raises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()) as error:
                main(argv)
            assert stopped.value.code == 2 and "approve requires either --final or glossary approval" in error.getvalue()

    def test_glossary_approval_names_who_reviewed_it(self, job):
        with patch.object(cli, "approve_glossary") as approve:
            code, output, _ = run(["approve", str(job.root), "--glossary", "reviewed.json", "--style", "style.json"])
            assert code == ExitCode.COMPLETE and "Glossary approved (human-reviewed)." in output
            assert approve.call_args.args[1] == "reviewed.json"
            assert approve.call_args.kwargs["llm_review"] is False
            assert approve.call_args.kwargs["reviewed_style_file"] == "style.json"
            code, output, _ = run(["approve", str(job.root), "--llm-glossary", "--plain"])
            assert "Glossary approved (LLM-reviewed)." in output and approve.call_args.kwargs["llm_review"] is True
        log = session_log(job)
        assert "Glossary approved (LLM-reviewed)." in log and "approve finished; exit_code=0" in log

    def test_final_approval_can_continue_the_job_at_once(self, job):
        with patch.object(cli, "approve_final_draft") as approve, \
             patch.object(cli, "run_workflow", return_value=finished(ExitCode.COMPLETE, "book compiled")) as workflow:
            code, output, _ = run(["approve", str(job.root), "--final", "--resume", "--plain"])
        assert code == ExitCode.COMPLETE and "Final repaired draft approved." in output
        approve.assert_called_once()
        workflow.assert_called_once()
        log = session_log(job)
        assert "book compiled" in log and "approve/resume finished; exit_code=0" in log

    def test_a_pause_or_an_interrupt_during_approval_is_not_a_failure(self, job):
        with patch.object(cli, "approve_glossary", side_effect=PauseRequested("pause requested from the dashboard")):
            code, _, error = run(["approve", str(job.root), "--llm-glossary"])
        assert code == ExitCode.PAUSED and "paused: pause requested from the dashboard" in error
        with patch.object(cli, "approve_glossary", side_effect=KeyboardInterrupt):
            code, _, error = run(["approve", str(job.root), "--llm-glossary"])
        assert code == ExitCode.CANCELLED and error.strip() == "cancelled"
        log = session_log(job)
        assert "paused: pause requested from the dashboard" in log and "[cli] cancelled" in log


class RetryTests:
    def test_retry_names_the_stages_it_reset(self, job):
        reset = [WorkflowStage.TRANSLATE, WorkflowStage.AUDIT_TRANSLATION]
        with patch.object(cli, "retry_from_stage", return_value=reset) as retry:
            code, output, _ = run(["retry", str(job.root), "--stage", "translate"])
        assert code == ExitCode.COMPLETE and output.strip() == "Reset: translate, audit_translation"
        assert retry.call_args.args[1] is WorkflowStage.TRANSLATE
        assert "retry finished; exit_code=0" in session_log(job)

    def test_retry_can_continue_the_job_at_once(self, job):
        with patch.object(cli, "retry_failed_from_stage", return_value=[WorkflowStage.TRANSLATE]), \
             patch.object(cli, "run_workflow", return_value=finished(ExitCode.PAUSED, "paused for review")):
            code, output, _ = run(["retry", str(job.root), "--stage", "translate", "--failed-only", "--resume", "--plain"])
        assert code == ExitCode.PAUSED and "Reset: translate" in output
        log = session_log(job)
        assert "paused for review" in log and "retry/resume finished; exit_code=2" in log


class ResolveReviewTests:
    def report(self, passed: bool):
        return SimpleNamespace(passed=passed, model_dump_json=lambda indent: json.dumps({"passed": passed}))

    def test_resume_needs_the_final_approval(self, job):
        with pytest.raises(SystemExit) as stopped, contextlib.redirect_stderr(io.StringIO()) as error:
            main(["resolve-review", str(job.root), "sheet.json", "--resume"])
        assert stopped.value.code == 2 and "--resume requires --approve-final" in error.getvalue()

    def test_unresolved_segments_leave_the_job_paused_and_unapproved(self, job):
        with patch.object(cli, "load_manual_review_resolution_file", return_value={"sheet": 1}) as load, \
             patch.object(cli, "resolve_manual_review", return_value=self.report(False)) as resolve, \
             patch.object(cli, "approve_final_draft") as approve:
            code, output, _ = run(["resolve-review", str(job.root), "sheet.json"])
            assert code == ExitCode.PAUSED and json.loads(output) == {"passed": False}
            assert load.call_args.args[1] == "sheet.json" and resolve.call_args.args[1] == {"sheet": 1}
            code, _, error = run(["resolve-review", str(job.root), "sheet.json", "--approve-final"])
            assert code == ExitCode.FAILED and "final approval refused" in error
        approve.assert_not_called()

    def test_a_cleared_queue_can_be_approved_and_the_job_continued(self, job):
        with patch.object(cli, "load_manual_review_resolution_file", return_value={}), \
             patch.object(cli, "resolve_manual_review", return_value=self.report(True)), \
             patch.object(cli, "approve_final_draft") as approve, \
             patch.object(cli, "run_workflow", return_value=finished(ExitCode.COMPLETE, "book compiled")) as workflow:
            code, _, _ = run(["resolve-review", str(job.root), "sheet.json"])
            assert code == ExitCode.COMPLETE
            approve.assert_not_called()
            code, output, _ = run(["resolve-review", str(job.root), "sheet.json", "--approve-final", "--resume", "--plain"])
        assert code == ExitCode.COMPLETE and "Final repaired draft approved." in output
        approve.assert_called_once()
        workflow.assert_called_once()
        log = session_log(job)
        assert "book compiled" in log and "resolve-review/resume finished; exit_code=0" in log


class DashboardTests:
    def test_ui_serves_the_runs_folder(self):
        with patch("book_agent.web.serve_ui") as serve:
            code, _, _ = run(["ui", "--runs", "my-runs", "--configs", "my-configs", "--port", "0", "--no-browser"])
        assert code == ExitCode.COMPLETE
        assert serve.call_args.args == (Path("my-runs"), Path("my-configs"))
        assert serve.call_args.kwargs["port"] == 0 and serve.call_args.kwargs["open_browser"] is False
        assert serve.call_args.kwargs["start_path"] == "/"

    def test_review_ui_opens_on_the_final_review_of_one_job(self, job):
        with patch("book_agent.web.serve_ui") as serve:
            code, _, _ = run(["review-ui", str(job.root)])
        assert code == ExitCode.COMPLETE
        assert serve.call_args.args == (job.root.parent, Path("configs"))
        assert serve.call_args.kwargs["start_path"] == "/jobs/fixture/review"
        assert serve.call_args.kwargs["open_browser"] is True


class EditsTests:
    def test_xliff_goes_to_the_terminal_when_no_file_is_named(self, job):
        with patch.object(cli, "export_xliff", return_value="<xliff/>"):
            code, output, _ = run(["edits", str(job.root), "--export", "xliff"])
        assert code == ExitCode.COMPLETE and output == "<xliff/>\n"


class SeriesGlossaryArgumentTests:
    def refused(self, argv: list[str]) -> str:
        code, _, error = run(["build-series-glossary", "--output", "series.json", *argv])
        assert code == ExitCode.FAILED
        return error

    def test_arguments_that_do_not_go_together_are_refused(self, job):
        runs = job.root.parent
        assert "--config requires --llm-conflicts" in self.refused(["--runs", str(runs), "--config", "c.yaml"])
        assert "--job-pattern can only be used with --runs" in self.refused(
            ["--workspace", str(job.root), "--job-pattern", "qel-*"]
        )
        assert "no book workspaces matched 'qel-*'" in self.refused(["--runs", str(runs), "--job-pattern", "qel-*"])
        assert str(runs / "missing") in self.refused(["--runs", str(runs / "missing")])


def series_book(base: Path, name: str, qelwright: str) -> Path:
    """A job with a resolved glossary of two names, in a folder of its own name."""
    (base / name).mkdir()
    workspace = make_workspace(base / name)
    config = AppConfig.model_validate({"glossary": {"extraction_chunk_tokens": 100}})
    candidates = GlossaryResult(
        entries=[entry("Qelwright", qelwright, ["D0000-S000001"]), entry("Vraxwright", "弗拉克斯赖特", ["D0000-S000001"])]
    )
    run_glossary_extraction_stage(workspace, config, FakeGlossaryClient([candidates]))
    run_glossary_resolution_stage(
        workspace,
        config,
        FakeGlossaryClient(
            [resolution_result(("T00001", qelwright, GlossaryCategory.PERSON), ("T00002", "弗拉克斯赖特", GlossaryCategory.PERSON))]
        ),
    )
    return Path(shutil.move(workspace.root, workspace.root.with_name(name)))


class SeriesGlossaryBuildTests:
    def test_terms_the_books_agree_on_are_written_with_an_overlay_per_book(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            books = [series_book(base, "one", "奎尔赖特"), series_book(base, "two", "奎尔赖特")]
            summary = build_series_glossary_from_workspaces(
                books, base / "series.json", source_stage="resolved", book_output_dir=base / "overlays"
            )
            assert summary["result"] == "completed" and summary["source_stage"] == "resolved"
            assert (summary["source_count"], summary["source_entry_count"]) == (2, 4)
            assert (summary["included_term_count"], summary["conflict_count"]) == (2, 0)
            for key in ("output", "report", "legacy", "book_overlay_report"):
                assert Path(summary[key]).is_file(), key
            assert summary["book_output_dir"] == str(base / "overlays") and summary["book_overlay_count"] == 2
            assert (base / "overlays" / "one.glossary.review.json").is_file()
            assert "奎尔赖特" in Path(summary["output"]).read_text(encoding="utf-8")

            # Without a folder for them, no overlays are written or reported.
            plain = build_series_glossary_from_workspaces(books, base / "plain.json", source_stage="resolved")
            assert "book_overlay_count" not in plain and plain["included_term_count"] == 2

    def test_a_disputed_term_is_left_out_unless_the_model_is_asked(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            books = [
                series_book(base, "one", "奎尔赖特"),
                series_book(base, "two", "奎尔赖特"),
                series_book(base, "three", "凯尔莱特"),
            ]
            summary = build_series_glossary_from_workspaces(books, base / "series.json", source_stage="resolved")
            assert (summary["included_term_count"], summary["conflict_count"]) == (1, 1)

            config = AppConfig()
            client = Mock()
            with patch.object(cli, "resolve_series_glossary_conflicts", side_effect=lambda build, *rest, **options: build) as resolve:
                build_series_glossary_from_workspaces(
                    books, base / "series.json", source_stage="resolved", llm_conflicts=True, config=config, client=client
                )
            client.validate_model_context.assert_called_once_with(
                config.glossary.resolution_max_num_ctx, model=config.ollama.model
            )
            assert resolve.call_args.args[2] is client
            assert resolve.call_args.kwargs["checkpoint_directory"] == base / "series.conflict-checkpoints"
            assert resolve.call_args.kwargs["max_attempts"] == config.workflow.max_retries + 1

            # No client given: one is made from the config, with the progress printer.
            with patch.object(cli, "resolve_series_glossary_conflicts", side_effect=lambda build, *rest, **options: build), \
                 patch.object(cli, "OllamaClient") as made:
                build_series_glossary_from_workspaces(
                    books, base / "series.json", source_stage="resolved", llm_conflicts=True, generation_progress="printer"
                )
            assert made.call_args.kwargs == {"progress": "printer"}

    def test_the_books_must_be_given_once_each_from_a_known_boundary(self, job):
        with pytest.raises(ValueError, match="at least one book workspace"):
            build_series_glossary_from_workspaces([], "series.json")
        with pytest.raises(ValueError, match="must be unique"):
            build_series_glossary_from_workspaces([job.root, job.root], "series.json")
        with pytest.raises(ValueError, match="'approved' or 'resolved'"):
            build_series_glossary_from_workspaces([job.root], "series.json", source_stage="draft")

    def test_books_in_different_language_pairs_do_not_mix(self, job):
        loaded = cli.load_glossary_draft(job)
        other = loaded.model_copy(update={"pair": SimpleNamespace(value="en>ja")})
        second = job.root.with_name("second")
        shutil.copytree(job.root, second)
        with patch.object(cli, "load_glossary_draft", side_effect=[loaded, other]), \
             patch.object(cli, "screen_glossary_candidates", side_effect=lambda glossary, **options: (loaded, Mock())), \
             patch.object(cli, "analyze_glossary_quality"), \
             pytest.raises(ValueError, match="different language pairs"):
            build_series_glossary_from_workspaces([job.root, second], "series.json", source_stage="resolved")


class SeriesCommandTests:
    def args(self, *argv: str):
        return build_parser().parse_args(["series", *argv])

    def test_each_action_passes_its_arguments_on(self):
        dumped = SimpleNamespace(model_dump=lambda mode: {"dumped": mode})
        with patch.object(cli, "create_series", return_value=dumped) as create:
            assert _run_series_command(self.args("create", "qel", "--name", "Qel", "--direction", "en-zh")) == {"dumped": "json"}
            assert create.call_args.args[:3] == (Path("runs"), "qel", "Qel")
        with patch.object(cli, "add_books", return_value=dumped) as add:
            _run_series_command(self.args("add", "--runs", "r", "qel", "one", "two"))
            assert add.call_args.args == (Path("r"), "qel", ["one", "two"])
        with patch.object(cli, "publish_workbench", return_value=dumped) as publish:
            assert _run_series_command(self.args("publish", "qel")) == {"dumped": "json"}
            publish.assert_called_once_with(Path("runs"), "qel")
        with patch.object(cli, "import_version", return_value=dumped) as imported:
            _run_series_command(self.args("import", "qel", "old.json"))
            assert imported.call_args.args == (Path("runs"), "qel", Path("old.json"))
        with patch.object(cli, "bind_book", return_value={"bound": "v002"}) as bind:
            assert _run_series_command(self.args("bind", "qel", "one", "--version", "v002", "--upgrade")) == {"bound": "v002"}
            assert bind.call_args.args == (Path("runs"), "qel", "one", "v002") and bind.call_args.kwargs == {"upgrade": True}
        with patch.object(cli, "series_status", return_value={"books": []}) as status:
            assert _run_series_command(self.args("status", "qel")) == {"books": []}
            status.assert_called_once_with(Path("runs"), "qel")

    def test_build_counts_the_terms_by_origin_and_decision(self):
        term = lambda origin, decision: SimpleNamespace(origin=origin, decision=decision)  # noqa: E731
        workbench = SimpleNamespace(
            series_id="qel", based_on="v001", source_jobs=["one", "two"], not_ready=["three"],
            terms=[term("new", "pending"), term("new", "pending"), term("carried", "keep")],
        )
        with patch.object(cli, "build_workbench", return_value=workbench) as build:
            result = _run_series_command(self.args("build", "qel", "--minimum-books", "3", "--consensus-ratio", "0.8"))
        assert build.call_args.kwargs == {"minimum_books": 3, "consensus_ratio": 0.8}
        assert result == {
            "series_id": "qel", "based_on": "v001", "source_jobs": ["one", "two"], "not_ready": ["three"],
            "terms": 3, "by_origin_and_decision": {"carried/keep": 1, "new/pending": 2},
        }

    def test_decide_and_accept_report_the_terms_they_touched(self):
        with patch.object(cli, "decide_terms", return_value=SimpleNamespace(series_id="qel")) as decide:
            result = _run_series_command(
                self.args("decide", "qel", "T00001", "T00002", "--decision", "drop", "--reason", "generic", "--unlock")
            )
        assert result == {"series_id": "qel", "decided": ["T00001", "T00002"], "decision": "drop"}
        assert decide.call_args.kwargs == {"reason": "generic", "target": None, "category": None, "unlock": True}
        with patch("book_agent.series_llm.accept_suggestions") as accept:
            assert _run_series_command(self.args("accept", "qel", "T00001", "--reject")) == {
                "series_id": "qel", "terms": ["T00001"], "accepted": False,
            }
        accept.assert_called_once_with(Path("runs"), "qel", ["T00001"], False)

    def test_suggest_checks_the_model_before_asking_it(self, job):
        config = load_workspace_config(job)
        with patch.object(cli, "_series_config", return_value=config), \
             patch.object(cli, "OllamaClient") as client, \
             patch("book_agent.series_llm.suggest", return_value={"suggested": 4}) as suggest:
            result = _run_series_command(self.args("suggest", "qel", "--task", "conflicts"), generation_progress="printer")
        assert result == {"series_id": "qel", "task": "conflicts", "suggested": 4}
        assert client.call_args.kwargs == {"progress": "printer"}
        client.return_value.validate_model_context.assert_called_once_with(
            config.glossary.resolution_max_num_ctx, model=config.ollama.model
        )
        assert suggest.call_args.args == (Path("runs"), "qel", "conflicts", client.return_value, config)

    def test_main_prints_the_result_and_shows_model_progress_for_suggest(self):
        with patch.object(cli, "_run_series_command", return_value={"suggested": 4}) as command:
            code, output, _ = run(["series", "suggest", "qel", "--task", "generic", "--plain"])
            assert code == ExitCode.COMPLETE and json.loads(output) == {"suggested": 4}
            assert callable(command.call_args.args[1])
            run(["series", "status", "qel"])
            assert command.call_args.args[1] is None


class SeriesConfigTests:
    def test_a_named_config_file_wins(self, job):
        path = job.root.parent / "series.yaml"
        path.write_text("ollama:\n  model: other-model:1b\n", encoding="utf-8")
        assert _series_config(job.root.parent, "qel", str(path)).ollama.model == "other-model:1b"

    def test_otherwise_the_first_readable_member_book_gives_it(self, job):
        runs = job.root.parent
        books = SimpleNamespace(books=[SimpleNamespace(job_id="gone"), SimpleNamespace(job_id="fixture")])
        with patch("book_agent.series.load_manifest", return_value=books):
            assert _series_config(runs, "qel", None) == load_workspace_config(job)
        with patch("book_agent.series.load_manifest", return_value=SimpleNamespace(books=books.books[:1])), \
             pytest.raises(ValueError, match="pass --config"):
            _series_config(runs, "qel", None)


def test_the_package_runs_as_a_module():
    with patch.object(cli, "main", return_value=0) as entry, pytest.raises(SystemExit) as stopped:
        runpy.run_module("book_agent", run_name="__main__")
    assert stopped.value.code == 0
    entry.assert_called_once_with()
