import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from book_agent.config import AppConfig
from book_agent.ollama_client import GenerationCancelled
from book_agent.pipeline_state import WorkflowStage
from book_agent.stages.glossary import GlossaryApprovalRequired
from book_agent.state import (
    StageStatus,
    connect_state,
    get_stage_status,
    get_work_unit,
    record_artifact,
    set_stage_status,
    set_work_unit_status,
)
from book_agent.workflow import (
    ExitCode,
    ProgressEvent,
    approve_final_draft,
    approve_glossary,
    default_stage_runners,
    format_json,
    format_status_plain,
    load_workspace_config,
    retry_failed_from_stage,
    retry_from_stage,
    run_workflow,
    workflow_report,
    workflow_status,
    _stage_result_message,
)
from book_agent.workspace import create_job_workspace


class WorkflowTests:
    def setup_method(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        source = root / "book.epub"
        source.write_bytes(b"epub")
        self.config = AppConfig.model_validate(
            {"workflow": {"require_glossary_review": False}}
        )
        self.workspace = create_job_workspace(
            source, root / "runs", self.config, job_id="job"
        )

    def teardown_method(self) -> None:
        self.temporary.cleanup()

    def _runners(self, *, failing=None, cancelling=None, pausing=None):
        def make(stage):
            def runner(workspace, config, client):
                if stage is failing:
                    raise RuntimeError("stage broke")
                if stage is cancelling:
                    raise GenerationCancelled("stop now")
                if stage is pausing:
                    connection = connect_state(workspace.state_file)
                    try:
                        set_stage_status(
                            connection, stage.value, StageStatus.PAUSED, message="review"
                        )
                    finally:
                        connection.close()
                    raise GlossaryApprovalRequired("review")
                connection = connect_state(workspace.state_file)
                try:
                    set_stage_status(
                        connection,
                        stage.value,
                        StageStatus.COMPLETED,
                        attempts=1,
                        input_hash="i" * 64,
                        output_hash=(str(list(WorkflowStage).index(stage)) * 64)[:64],
                    )
                finally:
                    connection.close()
                return stage.value

            return runner

        return {stage: make(stage) for stage in WorkflowStage}

    def test_load_workspace_config_round_trips_captured_config(self) -> None:
        loaded = load_workspace_config(self.workspace)
        assert loaded.translation.direction == self.config.translation.direction

    def test_default_stage_runners_cover_every_stage(self) -> None:
        assert set(default_stage_runners()) == set(WorkflowStage)

    def test_run_workflow_completes_and_then_skips_all_stages(self) -> None:
        events = []
        first = run_workflow(
            self.workspace,
            self.config,
            client=object(),
            progress=events.append,
            stage_runners=self._runners(),
        )
        assert first.exit_code == ExitCode.COMPLETE
        assert len([event for event in events if event.status == "completed"]) == len(WorkflowStage)
        events.clear()
        second = run_workflow(
            self.workspace,
            self.config,
            client=object(),
            progress=events.append,
            stage_runners=self._runners(failing=WorkflowStage.DECOMPILE),
        )
        assert second.exit_code == ExitCode.COMPLETE
        assert all(event.status == "skipped" for event in events)
        assert all(event.message.startswith("result=skipped") for event in events)

    def test_obfuscated_quality_stage_results_are_explicit(self) -> None:
        translation = SimpleNamespace(
            deferred_chunk_count=2,
            deferred_segment_count=3,
        )
        audit = SimpleNamespace(
            passed=False,
            issue_count=3,
            review_segment_ids=["DX001-SX000001", "DX001-SX000002"],
        )
        repair = SimpleNamespace(
            targeted_segment_count=2,
            repaired_segment_count=1,
            review_segment_ids=["DX001-SX000002"],
        )
        reprose = SimpleNamespace(
            candidate_segment_count=12,
            proposed_rewrite_count=4,
            applied_rewrite_count=3,
            rejected_rewrite_count=1,
            decision_failure_segment_ids=[],
        )
        review = SimpleNamespace(
            passed=False,
            remaining_issue_count=1,
            review_segment_ids=["DX001-SX000002"],
        )
        passed = review.__class__(
            passed=True,
            remaining_issue_count=0,
            review_segment_ids=[],
        )

        assert _stage_result_message(WorkflowStage.TRANSLATE, translation) == "result=pending-repair; deferred_chunks=2; deferred_segments=3"
        assert _stage_result_message(WorkflowStage.AUDIT_TRANSLATION, audit) == "result=issues-found; issues=3; review_segments=2"
        assert _stage_result_message(WorkflowStage.REPAIR_TRANSLATION, repair) == "result=pending-review; targeted=2; repaired=1; review_segments=1"
        assert _stage_result_message(WorkflowStage.REPROSE_TRANSLATION, reprose) == ("result=passed; candidates=12; proposed=4; applied=3; rejected=1; "
            "decision_failures=0")
        assert _stage_result_message(WorkflowStage.REVIEW_REPAIRED, review) == "result=repair-required; remaining=1; review_segments=1"
        assert _stage_result_message(WorkflowStage.VALIDATE_REPAIRED, passed) == "result=passed; remaining=0; review_segments=0"

    def test_run_workflow_returns_failed_cancelled_and_paused_outcomes(self) -> None:
        failed = run_workflow(
            self.workspace,
            self.config,
            client=object(),
            stage_runners=self._runners(failing=WorkflowStage.DECOMPILE),
        )
        assert (failed.exit_code, failed.stage) == (ExitCode.FAILED, "decompile")
        assert workflow_status(self.workspace)["overall"] == "failed"

        cancelled_workspace = self._new_workspace("cancelled")
        cancelled = run_workflow(
            cancelled_workspace,
            self.config,
            client=object(),
            stage_runners=self._runners(cancelling=WorkflowStage.DECOMPILE),
        )
        assert cancelled.exit_code == ExitCode.CANCELLED
        assert workflow_status(cancelled_workspace)["overall"] == "paused"

        paused_workspace = self._new_workspace("paused")
        paused = run_workflow(
            paused_workspace,
            self.config,
            client=object(),
            stage_runners=self._runners(pausing=WorkflowStage.APPROVE_GLOSSARY),
        )
        assert (paused.exit_code, paused.stage) == (ExitCode.PAUSED, "approve_glossary")

    def test_created_client_preflights_translation_and_audit_models(self) -> None:
        class FakeClient:
            def __init__(self):
                self.checked = []

            def validate_model_context(self, context, *, model=None):
                self.checked.append((context, model))

        fake = FakeClient()
        generation_events = []
        generation_callback = generation_events.append
        with patch("book_agent.workflow.OllamaClient", return_value=fake) as client_factory:
            result = run_workflow(
                self.workspace,
                self.config,
                generation_progress=generation_callback,
                stage_runners=self._runners(),
            )
        assert result.exit_code == ExitCode.COMPLETE
        assert client_factory.call_args.kwargs["progress"] is generation_callback
        assert fake.checked == ([
                (self.config.ollama.num_ctx, self.config.ollama.model),
                (
                    self.config.glossary.extraction_max_num_ctx,
                    self.config.glossary.extraction_model,
                ),
                (self.config.audit.max_num_ctx, self.config.audit.model),
            ])

    def test_run_workflow_rejects_incomplete_runner_map(self) -> None:
        with pytest.raises(ValueError, match="missing stage runners"):
            run_workflow(self.workspace, self.config, stage_runners={})

    def test_final_review_gate_pauses_then_approval_allows_completion(self) -> None:
        config = self.config.model_copy(
            update={
                "workflow": self.config.workflow.model_copy(
                    update={"require_final_review": True}
                )
            }
        )
        result = run_workflow(
            self.workspace,
            config,
            client=object(),
            stage_runners=self._runners(),
        )
        assert (result.exit_code, result.stage) == (ExitCode.PAUSED, "compile")
        approved_hash = approve_final_draft(self.workspace)
        assert approved_hash
        resumed = run_workflow(
            self.workspace,
            config,
            client=object(),
            stage_runners=self._runners(),
        )
        assert resumed.exit_code == ExitCode.COMPLETE

    def test_unresolved_queue_blocks_final_approval_before_approval_prompt(self) -> None:
        config = self.config.model_copy(
            update={
                "workflow": self.config.workflow.model_copy(
                    update={"require_final_review": True}
                )
            }
        )
        blocker = (
            "2 unresolved review segment(s) exceed the compile limit of 0; "
            "resolve them before final approval. IDs: D0001-S000001, D0001-S000002"
        )
        with (
            patch(
                "book_agent.workflow._unresolved_compile_review_message",
                return_value=blocker,
            ),
            patch(
                "book_agent.workflow.write_unresolved_review_report",
                return_value="reports/unresolved-review-segments.md",
            ),
        ):
            result = run_workflow(
                self.workspace,
                config,
                client=object(),
                stage_runners=self._runners(),
            )
        assert (result.exit_code, result.stage) == (ExitCode.PAUSED, "compile")
        assert result.message == blocker + " Review worksheet: reports/unresolved-review-segments.md"
        assert "approval required" not in result.message

        with patch(
            "book_agent.workflow._unresolved_compile_review_message",
            return_value=blocker,
        ):
            with pytest.raises(ValueError, match="2 unresolved review segment"):
                approve_final_draft(self.workspace)

    def test_approve_final_requires_completed_validation(self) -> None:
        with pytest.raises(ValueError, match="has not completed"):
            approve_final_draft(self.workspace)

    def test_approve_glossary_delegates_to_typed_stage(self) -> None:
        reviewed = Path(self.temporary.name) / "reviewed.txt"
        reviewed.write_text("", encoding="utf-8")
        with patch("book_agent.workflow.run_glossary_approval_stage") as called:
            approve_glossary(self.workspace, reviewed, self.config)
        assert called.call_args.kwargs["reviewed_file"] == reviewed

    def test_approve_glossary_can_create_llm_reviewer(self) -> None:
        with (
            patch("book_agent.workflow.OllamaClient") as client_type,
            patch("book_agent.workflow.run_glossary_approval_stage") as called,
        ):
            approve_glossary(
                self.workspace,
                config=self.config,
                llm_review=True,
            )
        client_type.return_value.validate_model_context.assert_called_once_with(
            self.config.ollama.num_ctx,
            model=self.config.ollama.model,
        )
        assert called.call_args.kwargs["llm_review"]
        assert called.call_args.kwargs["client"] is client_type.return_value

    def test_retry_resets_stage_dependents_and_artifacts(self) -> None:
        connection = connect_state(self.workspace.state_file)
        try:
            set_stage_status(
                connection,
                WorkflowStage.TRANSLATE.value,
                StageStatus.COMPLETED,
                input_hash="a",
                output_hash="b",
            )
            artifact = self.workspace.root / "translated" / "one.json"
            artifact.write_text("{}", encoding="utf-8")
            record_artifact(
                connection,
                "translated/one.json",
                WorkflowStage.TRANSLATE.value,
                "translation",
                "a" * 64,
                2,
            )
        finally:
            connection.close()
        affected = retry_from_stage(self.workspace, WorkflowStage.TRANSLATE)
        assert affected[0] == WorkflowStage.TRANSLATE
        connection = connect_state(self.workspace.state_file)
        try:
            assert get_stage_status(connection, "translate")["status"] == StageStatus.PENDING.value
        finally:
            connection.close()

    def test_retry_failed_only_preserves_completed_checkpoint(self) -> None:
        connection = connect_state(self.workspace.state_file)
        try:
            set_stage_status(connection, "translate", StageStatus.FAILED)
            set_work_unit_status(
                connection,
                "complete",
                "translate",
                "chunk",
                StageStatus.COMPLETED,
                output_hash="saved",
            )
            set_work_unit_status(
                connection,
                "failed",
                "translate",
                "chunk",
                StageStatus.FAILED,
                attempts=4,
            )
        finally:
            connection.close()
        retry_failed_from_stage(self.workspace, WorkflowStage.TRANSLATE)
        connection = connect_state(self.workspace.state_file)
        try:
            assert get_work_unit(connection, "complete", "translate")["status"] == "completed"
            assert get_work_unit(connection, "failed", "translate")["status"] == "pending"
        finally:
            connection.close()

    def test_retry_failed_only_is_noop_when_nothing_failed(self) -> None:
        assert retry_failed_from_stage(self.workspace, WorkflowStage.TRANSLATE) == []

    def test_status_report_and_formatters_are_stable(self) -> None:
        status = workflow_status(self.workspace)
        assert status["overall"] == "pending"
        assert [item["name"] for item in status["stages"]] == [s.value for s in WorkflowStage]
        plain = format_status_plain(status)
        assert "Overall: pending" in plain
        assert json.loads(format_json(status))["job_id"] == "job"
        report = workflow_report(self.workspace)
        assert report["artifacts"] == []
        assert "metadata" in report

    def test_status_reports_source_configuration_drift(self) -> None:
        root = Path(self.temporary.name)
        source = root / "drift.epub"
        source.write_bytes(b"epub")
        config_source = root / "production.yaml"
        config_source.write_text("workflow:\n  require_final_review: true\n", encoding="utf-8")
        workspace = create_job_workspace(
            source,
            root / "drift-runs",
            self.config,
            job_id="drift",
            config_source=config_source,
        )
        current = workflow_status(workspace)
        assert not current["configuration"]["drifted"]
        config_source.write_text("workflow:\n  require_final_review: false\n", encoding="utf-8")
        drifted = workflow_status(workspace)
        assert drifted["configuration"]["drifted"]
        assert "Configuration drift: yes" in format_status_plain(drifted)

    def test_existing_workspace_adds_new_split_stage_rows(self) -> None:
        connection = connect_state(self.workspace.state_file)
        try:
            connection.execute(
                "DELETE FROM stages WHERE name IN (?, ?)",
                (WorkflowStage.REVIEW_REPAIRED.value, WorkflowStage.REPAIR_REVIEW.value),
            )
            connection.commit()
        finally:
            connection.close()
        status = workflow_status(self.workspace)
        names = [item["name"] for item in status["stages"]]
        assert names == [stage.value for stage in WorkflowStage]
        assert next(item for item in status["stages"] if item["name"] == "review_repaired")["status"] == "pending"

    def _new_workspace(self, job_id):
        root = Path(self.temporary.name)
        source = root / f"{job_id}.epub"
        source.write_bytes(b"epub")
        return create_job_workspace(source, root / "runs", self.config, job_id=job_id)

