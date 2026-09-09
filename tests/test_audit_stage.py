import re
import tempfile
from pathlib import Path

import pytest

from book_agent.audit import (
    AuditCategory,
    AuditIssue,
    AuditSeverity,
    SemanticAuditResult,
    audit_translated_document,
)
from book_agent.config import AppConfig
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredOutputError,
    StructuredGenerationResult,
)
from book_agent.pipeline_state import WorkflowStage
from book_agent.quantities import (
    QuantityAuditDecision,
    QuantityAuditResult,
)
from book_agent.stages.audit import (
    _include_deferred_translation_findings,
    load_document_audits,
    load_translation_audit_report,
    run_translation_audit_stage,
)
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.translate import run_translation_stage
from book_agent.state import connect_state, get_stage_status, get_work_unit, list_attempts
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_audit import source_document, translated_document
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_translate_stage import FakeTranslationClient


class FakeAuditClient:
    def __init__(
        self,
        *,
        invalid_calls=0,
        structured_failures=0,
        unpadded_calls=0,
        return_issue=True,
        issue_severity=AuditSeverity.MEDIUM,
        issue_message="The translated meaning differs from the source.",
    ):
        self.invalid_calls = invalid_calls
        self.structured_failures = structured_failures
        self.unpadded_calls = unpadded_calls
        self.return_issue = return_issue
        self.issue_severity = issue_severity
        self.issue_message = issue_message
        self.prompts = []
        self.models = []
        self.thinking = []
        self.progress_labels = []
        self.output_token_limits = []
        self.max_attempts = []

    def generate_structured(
        self, prompt, schema, *, model=None, think=None, progress_label="",
        context_maximum=None, max_output_tokens=None, max_attempts=None
    ):
        self.prompts.append(prompt)
        self.models.append(model)
        self.thinking.append(think)
        self.progress_labels.append(progress_label)
        self.output_token_limits.append(max_output_tokens)
        self.max_attempts.append(max_attempts)
        if len(self.prompts) <= self.structured_failures:
            raise StructuredOutputError("truncated semantic audit JSON")
        allowed = re.search(r"Allowed IDs: ([^\n]+)", prompt).group(1).split(", ")
        if len(self.prompts) <= self.invalid_calls:
            segment_id = "outside-scope"
        else:
            high_risk = re.search(r"\[([^\]]+)\] AUDIT HIGH RISK", prompt)
            segment_id = high_risk.group(1) if high_risk else allowed[0]
        block = re.search(
            rf"\[{re.escape(segment_id)}\].*?\nSOURCE: (?P<source>.*?)\n"
            rf"TRANSLATION: (?P<translation>.*?)(?=\n\n\[|\Z)",
            prompt,
            flags=re.DOTALL,
        )
        source_quote = block.group("source").strip()[:12] if block else "evidence"
        translation_quote = (
            block.group("translation").strip()[:12] if block else "evidence"
        )
        if len(self.prompts) <= self.unpadded_calls:
            segment_id = re.sub(r"-S0(\d+)$", r"-S\1", segment_id)
        issues = []
        if self.return_issue:
            issues.append(
                AuditIssue(
                    segment_id=segment_id,
                    category=AuditCategory.MISTRANSLATION,
                    severity=self.issue_severity,
                    message=self.issue_message,
                    suggested_fix="Translate the source meaning accurately.",
                    source="semantic",
                    source_quote=source_quote,
                    translation_quote=translation_quote,
                )
            )
        value = SemanticAuditResult(issues=issues)
        generation = GenerationResult(
            content=value.model_dump_json(),
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=50, eval_count=10),
        )
        return StructuredGenerationResult(value=value, generation=generation)


class FakeQuantityAuditClient:
    def __init__(self, *, status="match", confidence=0.96):
        self.status = status
        self.confidence = confidence
        self.prompts = []
        self.models = []
        self.progress_events = []

    def report_progress(self, event):
        self.progress_events.append(event)

    def generate_structured(
        self, prompt, schema, *, model=None, think=None, progress_label="",
        context_maximum=None, max_output_tokens=None, max_attempts=None
    ):
        self.prompts.append(prompt)
        self.models.append(model)
        segment_id = re.search(r"Allowed ID: ([^\n]+)", prompt).group(1)
        source = re.search(r"\nSOURCE: (.*?)\nFOLLOWING SOURCE:", prompt).group(1)
        target = re.search(r"\nTRANSLATION: (.*?)\nSOURCE FACT", prompt).group(1)
        value = QuantityAuditResult(
            decisions=[
                QuantityAuditDecision(
                    segment_id=segment_id,
                    status=self.status,
                    mismatch_types=(
                        ["relation_change"] if self.status == "mismatch" else []
                    ),
                    message="The relative quantity relation is equivalent.",
                    source_quote="half again" if "half again" in source else "",
                    translation_quote="大出一半" if "大出一半" in target else "",
                    confidence=self.confidence,
                )
            ]
        )
        generation = GenerationResult(
            content=value.model_dump_json(),
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=30, eval_count=8),
        )
        return StructuredGenerationResult(value=value, generation=generation)


class AuditStageTests:
    def test_intentionally_preserved_identifier_is_not_forced_back_to_repair(self):
        text = "ISBN 978-0-123-12345-9 PDF ISBN 978-0-123-12345-4 EPUB"
        source = source_document([text])
        target = translated_document([text])
        deterministic = audit_translated_document(
            source, target, AppConfig().audit
        )

        result = _include_deferred_translation_findings(
            source,
            target,
            deterministic,
            {"D0001-S000001"},
        )

        assert result.passed
        assert not any(item.source == "translation-deferred" for item in result.issues)

    def prepare_workspace(self, base, config):
        epub = make_epub(base / "fixture.epub")
        workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, FakeTranslationClient())
        return workspace

    def test_deterministic_only_stage_passes_loads_and_resumes(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            first = run_translation_audit_stage(workspace, config)
            second = run_translation_audit_stage(workspace, config)
            assert first == second
            assert first.passed
            assert first.document_count == 1
            assert first.segment_count == 4
            assert load_translation_audit_report(workspace) == first
            audits = load_document_audits(workspace)
            assert len(audits) == 1
            assert audits[0].passed
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "audit_translation")
                assert stage["status"] == "completed"
                unit = get_work_unit(connection, "document:chapter", "audit_translation")
                assert unit["validation"]["passed"]
            finally:
                connection.close()

    def test_selective_semantic_stage_records_issue_and_resumes_batch(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_max_candidates_per_batch": 50,
                    "semantic_max_output_tokens": 1_536,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient()
            first = run_translation_audit_stage(workspace, config, client)
            second = run_translation_audit_stage(workspace, config, client)
            assert first == second
            assert not first.passed
            assert first.issue_count == 1
            assert len(client.prompts) == 1
            assert client.models == ["gemma4:31b"]
            assert client.thinking == [False]
            assert client.max_attempts == [1]
            assert client.output_token_limits == [1_536]
            assert "batch=1/1 id=audit-0000-00001 document=1/1 attempt=1/4" in client.progress_labels[0]
            assert "CONTEXT ONLY" in client.prompts[0]
            audit = load_document_audits(workspace)[0]
            assert audit.issues[0].source == "semantic"
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(connection, "audit-0000-00001", "audit_translation")
                assert unit["validation"]["scope_valid"]
                assert len(list_attempts(connection, "audit-0000-00001")) == 1
            finally:
                connection.close()

    def test_quantity_stage_adjudicates_uncertain_relation_and_resumes(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_enabled": False,
                    "quantity": {
                        "enabled": True,
                        "mode": "enforce",
                        "model": "gemma4:26b",
                        "escalation_model": "gemma4:31b",
                    },
                }
            }
        )
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><p>It was half again as large.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub", chapter=chapter)
            workspace = create_job_workspace(
                epub, base / "runs", config, job_id="fixture"
            )
            run_decompile_stage(workspace)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)
            run_translation_stage(
                workspace,
                config,
                FakeTranslationClient(translated_text="它大出一半。"),
            )
            client = FakeQuantityAuditClient()

            first = run_translation_audit_stage(workspace, config, client)
            second = run_translation_audit_stage(workspace, config, client)

            assert first == second
            assert first.passed
            assert len(client.prompts) == 1
            assert client.models == ["gemma4:26b"]
            audit = load_document_audits(workspace)[0]
            assert audit.quantity_comparisons[0].adjudicated
            assert audit.quantity_comparisons[0].status == "match"
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(
                    connection, "quantity-0000-00001", "audit_translation"
                )
                assert unit["validation"]["status"] == "match"
            finally:
                connection.close()

    def test_quantity_stage_adjudicates_raw_deterministic_mismatch(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_enabled": False,
                    "quantity": {
                        "enabled": True,
                        "mode": "enforce",
                        "model": "gemma4:26b",
                        "escalation_model": "gemma4:31b",
                    },
                }
            }
        )
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><p>They walked five miles.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub", chapter=chapter)
            workspace = create_job_workspace(
                epub, base / "runs", config, job_id="fixture"
            )
            run_decompile_stage(workspace)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)
            run_translation_stage(
                workspace,
                config,
                FakeTranslationClient(translated_text="他们走了五公里。"),
            )

            client = FakeQuantityAuditClient(status="match")
            report = run_translation_audit_stage(workspace, config, client)

            assert report.passed
            audit = load_document_audits(workspace)[0]
            assert audit.quantity_comparisons[0].status == "match"
            assert audit.quantity_comparisons[0].adjudicated
            assert client.models == ["gemma4:26b", "gemma4:31b"]
            assert not any(item.source == "quantity-deterministic" for item in audit.issues)

    def test_quantity_escalations_run_in_model_major_phases(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_enabled": False,
                    "quantity": {
                        "enabled": True,
                        "mode": "shadow",
                        "model": "gemma4:26b",
                        "escalation_model": "gemma4:31b",
                    },
                }
            }
        )
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><p>It was half again as large.</p><p>It was half again as large.</p></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub", chapter=chapter)
            workspace = create_job_workspace(
                epub, base / "runs", config, job_id="fixture"
            )
            run_decompile_stage(workspace)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)
            run_translation_stage(
                workspace,
                config,
                FakeTranslationClient(translated_text="å®ƒå¤§å‡ºä¸€åŠã€‚"),
            )
            client = FakeQuantityAuditClient(status="mismatch", confidence=0.96)

            run_translation_audit_stage(workspace, config, client)

            assert client.models == ["gemma4:26b", "gemma4:26b", "gemma4:31b", "gemma4:31b"]

    def test_semantic_stage_isolates_high_risk_candidates(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_min_source_tokens": 1,
                    "max_semantic_candidates_per_document": 2,
                    "semantic_max_candidates_per_batch": 1,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(return_issue=False)

            report = run_translation_audit_stage(workspace, config, client)

            assert report.passed
            assert len(client.prompts) == 2
            for prompt in client.prompts:
                assert prompt.count("AUDIT HIGH RISK") == 1

    def test_semantic_scope_failure_retries_with_diagnostics(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_max_candidates_per_batch": 50,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(invalid_calls=1, return_issue=True)
            report = run_translation_audit_stage(workspace, config, client)
            assert not report.passed
            assert len(client.prompts) == 2
            assert "previous structured result was rejected" in client.prompts[1]
            connection = connect_state(workspace.state_file)
            try:
                attempts = list_attempts(connection, "audit-0000-00001")
                assert [item["status"] for item in attempts] == ["failed", "completed"]
            finally:
                connection.close()

    def test_truncated_structured_output_retries_with_diagnostics(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_max_candidates_per_batch": 50,
                    "semantic_max_output_tokens": 1_024,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(structured_failures=1)

            report = run_translation_audit_stage(workspace, config, client)

            assert not report.passed
            assert len(client.prompts) == 2
            assert client.output_token_limits == [1_024, 2_048]
            assert "previous structured result was rejected" in client.prompts[1]
            connection = connect_state(workspace.state_file)
            try:
                attempts = list_attempts(connection, "audit-0000-00001")
                assert [item["status"] for item in attempts] == ["failed", "completed"]
            finally:
                connection.close()

    def test_exhausted_multi_segment_structured_output_escalates_entire_batch(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 1,
                    "semantic_max_candidates_per_batch": 2,
                    "max_semantic_candidates_per_document": 2,
                    "semantic_max_output_tokens": 1_024,
                },
                "workflow": {"max_retries": 2},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(structured_failures=10)

            report = run_translation_audit_stage(workspace, config, client)

            assert not report.passed
            assert client.output_token_limits == [1_024, 2_048, 4_096]
            audits = load_document_audits(workspace)
            escalated = [
                issue
                for audit in audits
                for issue in audit.issues
                if "could not produce a complete structured decision" in issue.message
            ]
            assert len(escalated) == 2
            assert {issue.segment_id for issue in escalated} == set(report.review_segment_ids)

    def test_exhausted_truncated_output_escalates_instead_of_aborting_book(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_max_candidates_per_batch": 1,
                    "max_semantic_candidates_per_document": 1,
                },
                "workflow": {"max_retries": 1},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(structured_failures=10)

            report = run_translation_audit_stage(workspace, config, client)

            assert not report.passed
            assert len(client.prompts) == 2
            audits = load_document_audits(workspace)
            escalated = [
                issue
                for audit in audits
                for issue in audit.issues
                if "could not produce a complete structured decision" in issue.message
            ]
            assert len(escalated) == 1
            assert escalated[0].severity == AuditSeverity.HIGH
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(
                    connection, "audit-0000-00001", "audit_translation"
                )
                assert unit["status"] == "completed"
                assert unit["validation"]["fallback_escalation"]
            finally:
                connection.close()

    def test_zero_padding_scope_variant_is_normalized_without_retry(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_max_candidates_per_batch": 50,
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(unpadded_calls=1)

            report = run_translation_audit_stage(workspace, config, client)

            assert not report.passed
            assert len(client.prompts) == 1
            assert re.search(r"^D\d{4}-S\d{6}$", report.review_segment_ids[0])

    def test_stage_fails_after_bounded_invalid_semantic_results(self):
        config = AppConfig.model_validate(
            {"audit": {"semantic_sample_every": 2}, "workflow": {"max_retries": 1}}
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeAuditClient(invalid_calls=10)
            with pytest.raises(ValueError, match="failed validation"):
                run_translation_audit_stage(workspace, config, client)
            assert len(client.prompts) == 2
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "audit_translation")["status"] == "failed"
            finally:
                connection.close()

    def test_stage_requires_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
            with pytest.raises(RuntimeError, match="preprocess|translate"):
                run_translation_audit_stage(workspace, config)

