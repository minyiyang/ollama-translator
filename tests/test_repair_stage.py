import re
import tempfile
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredOutputError,
)
from book_agent.repair import RepairDisposition
from book_agent.stages.audit import run_translation_audit_stage
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.repair import (
    load_repaired_documents,
    load_translation_repair_report,
    run_translation_repair_stage,
)
from book_agent.stages.translate import run_translation_stage
from book_agent.state import connect_state, get_stage_status, get_work_unit, list_attempts
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_audit_stage import FakeAuditClient
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_translate_stage import FakeTranslationClient


class FakeRepairClient:
    def __init__(self, *, invalid_calls=0):
        self.invalid_calls = invalid_calls
        self.prompts = []
        self.thinking = []
        self.progress_labels = []
        self.context_minimums = []
        self.context_maximums = []
        self.context_multipliers = []
        self.models = []

    def generate_text(
        self, prompt, *, stream=True, think=None, progress_label="",
        context_minimum=None, context_maximum=None, context_multiplier=2.0,
        model=None,
    ):
        self.prompts.append(prompt)
        self.thinking.append(think)
        self.progress_labels.append(progress_label)
        self.context_minimums.append(context_minimum)
        self.context_maximums.append(context_maximum)
        self.context_multipliers.append(context_multiplier)
        self.models.append(model)
        if len(self.prompts) <= self.invalid_calls:
            content = "invalid repair"
        else:
            segment_id = re.search(r"Output exactly <([^>]+)>", prompt).group(1)
            source_block = re.search(
                rf"\[{re.escape(segment_id)}\] REPAIR\nSOURCE: (.*?)\nCURRENT:",
                prompt,
                flags=re.DOTALL,
            )
            markers = re.findall(
                r"</?I\d{3}>", source_block.group(1) if source_block else ""
            )
            translated = "修订后的正确译文。"
            if markers:
                midpoint = next(
                    (
                        index
                        for index, marker in enumerate(markers)
                        if marker.startswith("</")
                    ),
                    len(markers),
                )
                translated = (
                    "".join(markers[:midpoint])
                    + translated
                    + "".join(markers[midpoint:])
                )
            content = f"<{segment_id}>{translated}</{segment_id}>"
        return GenerationResult(
            content=content,
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=30, eval_count=8),
        )


class StructuredFailureRepairClient(FakeRepairClient):
    def __init__(self):
        super().__init__()
        self.structured_output_limits = []
        self.structured_attempt_limits = []

    def generate_structured(self, prompt, schema, **kwargs):
        self.structured_output_limits.append(kwargs.get("max_output_tokens"))
        self.structured_attempt_limits.append(kwargs.get("max_attempts"))
        raise StructuredOutputError("obfuscated runaway JSON was rejected")


class FullRetranslationRepairClient(FakeRepairClient):
    def __init__(self):
        super().__init__()
        self.structured_calls = 0

    def generate_structured(self, prompt, schema, **kwargs):
        self.structured_calls += 1
        raise StructuredOutputError("obfuscated ordinary located edit rejected")


class RepairStageTests:
    def prepare_workspace(self, base, config, *, semantic_issue=True):
        epub = make_epub(base / "fixture.epub")
        workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, FakeTranslationClient())
        audit_client = FakeAuditClient(return_issue=semantic_issue)
        run_translation_audit_stage(
            workspace, config, audit_client if config.audit.semantic_enabled else None
        )
        return workspace

    def test_targeted_repair_changes_only_flagged_segment_and_resumes(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeRepairClient()
            first = run_translation_repair_stage(workspace, config, client)
            second = run_translation_repair_stage(workspace, config, client)
            assert first == second
            assert first.targeted_segment_count == 1
            assert first.repaired_segment_count == 1
            assert first.review_segment_ids == []
            assert len(client.prompts) == 1
            assert client.thinking == [False]
            assert "repair=1/1" in client.progress_labels[0]
            assert "attempt=1/2" in client.progress_labels[0]
            assert client.context_minimums == [16_384]
            assert client.models == ["qwen3.8:latest"]
            assert client.context_multipliers == [1.0]
            repaired = load_repaired_documents(workspace)[0]
            assert len(repaired.repairs) == 1
            assert repaired.repairs[0].disposition == RepairDisposition.REPAIRED
            changed = [
                item for item in repaired.document.segments
                if "修订后的正确译文。" in item.translated_text
            ]
            assert len(changed) == 1
            assert load_translation_repair_report(workspace) == first
            connection = connect_state(workspace.state_file)
            try:
                unit_id = f"repair-{changed[0].segment_id}"
                unit = get_work_unit(connection, unit_id, "repair_translation")
                assert unit["validation"]["passed"]
                assert len(list_attempts(connection, unit_id)) == 1
            finally:
                connection.close()

    def test_explicit_repair_model_and_context_ceiling_are_used(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "repair_model": "repair-model:test",
                    "repair_max_num_ctx": 16_384,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeRepairClient()

            run_translation_repair_stage(workspace, config, client)

            assert client.models == ["repair-model:test"]
            assert client.context_maximums == [16_384]

    def test_invalid_repair_retries_with_validator_diagnostics(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeRepairClient(invalid_calls=1)
            report = run_translation_repair_stage(workspace, config, client)
            assert report.repaired_segment_count == 1
            assert len(client.prompts) == 2
            assert "previous repair was rejected" in client.prompts[1]

    def test_malformed_located_edit_falls_back_with_bounded_output_obfuscated(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = StructuredFailureRepairClient()
            report = run_translation_repair_stage(workspace, config, client)

            assert report.repaired_segment_count == 1
            assert client.structured_output_limits == [1_024]
            assert client.structured_attempt_limits == [1]
            assert len(client.prompts) == 1
            assert "attempt=1/2" in client.progress_labels[0]

    def test_obfuscated_deferred_segments_use_full_retranslation_directly(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_enabled": False},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(
                epub, base / "runs", config, job_id="fixture"
            )
            run_decompile_stage(workspace)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)
            translation = run_translation_stage(
                workspace,
                config,
                FakeTranslationClient(invalid_calls=1),
            )
            run_translation_audit_stage(workspace, config)
            client = FullRetranslationRepairClient()

            report = run_translation_repair_stage(workspace, config, client)

            assert report.targeted_segment_count == len(client.prompts)
            deferred_repairs = [
                repair
                for document in load_repaired_documents(workspace)
                for repair in document.repairs
                if any(
                    issue.source == "translation-deferred"
                    for issue in repair.issues
                )
            ]
            assert len(deferred_repairs) == translation.deferred_segment_count
            assert all(item.attempts >= 1 for item in deferred_repairs)
            full_labels = [
                item
                for item in client.progress_labels
                if "mode=full-retranslation" in item
            ]
            assert len(full_labels) == translation.deferred_segment_count
            assert client.structured_calls == report.targeted_segment_count - translation.deferred_segment_count
            assert client.prompts
            assert sum("Translate the complete SOURCE" in prompt for prompt in client.prompts) == translation.deferred_segment_count

    def test_repeated_repair_failure_enters_review_queue(self):
        config = AppConfig.model_validate(
            {"audit": {"semantic_sample_every": 2}, "workflow": {"max_retries": 1}}
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            report = run_translation_repair_stage(
                workspace, config, FakeRepairClient(invalid_calls=10)
            )
            assert report.repaired_segment_count == 0
            assert len(report.review_segment_ids) == 1
            repaired = load_repaired_documents(workspace)[0]
            assert repaired.repairs[0].disposition == RepairDisposition.REVIEW
            assert repaired.repairs[0].attempts == 2
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "repair_translation")
                assert stage["status"] == "completed"
                assert "human review" in stage["message"]
            finally:
                connection.close()

    def test_no_targets_copies_draft_without_client(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            report = run_translation_repair_stage(workspace, config)
            assert report.targeted_segment_count == 0
            repaired = load_repaired_documents(workspace)[0]
            assert repaired.repairs == []
            assert len(repaired.document.segments) == 4

    def test_stage_requires_completed_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = AppConfig()
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
            with pytest.raises(RuntimeError, match="audit_translation"):
                run_translation_repair_stage(workspace, config, FakeRepairClient())

