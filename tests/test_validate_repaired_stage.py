import re
import tempfile
from pathlib import Path

import pytest

from book_agent.audit import AuditCategory, AuditIssue, AuditSeverity
from book_agent.audit import DocumentAudit
from book_agent.config import AppConfig
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredGenerationResult,
)
from book_agent.repair import (
    PairwiseRepairVerification,
    PairwiseRepairVerificationResult,
    RepairDisposition,
    RepairedDocument,
    RepairVerification,
    RepairVerificationResult,
    SegmentRepair,
    repair_candidate_first,
)
from book_agent.stages.audit import run_translation_audit_stage
from book_agent.stages.compile import run_epub_compile_stage
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.repair import run_translation_repair_stage
from book_agent.stages.repair_review import (
    _original_is_safe_to_reinstate,
    run_review_repair_stage,
)
from book_agent.stages.review_repaired import run_repaired_review_stage
from book_agent.stages.translate import run_translation_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.stages.validate_repaired import (
    _apply_exact_glossary_replacements,
    _accept_unreproduced_deterministic_reviews,
    _deduplicate_adjacent_marker_text,
    _feedback_repair_examples,
    _is_nonblocking_preference,
    _repair_feedback_markers,
    _requires_human_high_risk_change,
    load_repaired_document_validations,
    load_repaired_validation_report,
    load_validated_repaired_documents,
    run_repaired_validation_stage,
)
from book_agent.state import connect_state, get_stage_status, get_work_unit, list_attempts
from book_agent.translation import (
    TranslatedDocument,
    TranslatedSegment,
    SingleInlineMarkerPlacement,
    SingleInlineMarkerPlacementResult,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_audit_stage import FakeAuditClient
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient


class FakeVerificationClient:
    def __init__(
        self, *, invalid_calls=0, passed=True, pass_after_feedback=False,
        pass_after_feedback_count=None, current_acceptable=False
    ):
        self.invalid_calls = invalid_calls
        self.passed = passed
        self.prompts = []
        self.models = []
        self.thinking = []
        self.progress_labels = []
        self.context_minimums = []
        self.output_token_limits = []
        self.structured_attempt_limits = []
        self.feedback_prompts = []
        self.feedback_models = []
        self.events = []
        self.progress_events = []
        self.current_acceptable = current_acceptable
        self.pass_after_feedback_count = (
            pass_after_feedback_count
            if pass_after_feedback_count is not None
            else (1 if pass_after_feedback else None)
        )

    def report_progress(self, event):
        self.progress_events.append(event)

    def generate_structured(
        self, prompt, schema, *, model=None, think=None, progress_label="",
        context_minimum=None, context_maximum=None, max_output_tokens=None,
        max_attempts=None,
    ):
        self.events.append(("structured", model))
        self.prompts.append(prompt)
        self.models.append(model)
        self.thinking.append(think)
        self.progress_labels.append(progress_label)
        self.context_minimums.append(context_minimum)
        self.output_token_limits.append(max_output_tokens)
        self.structured_attempt_limits.append(max_attempts)
        expected = re.search(r"Allowed IDs: ([^\n]+)", prompt).group(1).split(", ")
        ids = ["wrong-id"] if len(self.prompts) <= self.invalid_calls else expected
        passed = self.passed or (
            self.pass_after_feedback_count is not None
            and len(self.feedback_prompts) >= self.pass_after_feedback_count
        )
        if schema is PairwiseRepairVerificationResult:
            candidate_first = repair_candidate_first(expected)
            candidate_acceptable = passed
            current_acceptable = self.current_acceptable
            a_acceptable = (
                candidate_acceptable if candidate_first else current_acceptable
            )
            b_acceptable = (
                current_acceptable if candidate_first else candidate_acceptable
            )
            value = PairwiseRepairVerificationResult(
                verifications=[
                    PairwiseRepairVerification(
                        segment_id=item,
                        a_acceptable=a_acceptable,
                        b_acceptable=b_acceptable,
                        winner=(
                            "tie"
                            if a_acceptable and b_acceptable
                            else "a"
                            if a_acceptable
                            else "b"
                            if b_acceptable
                            else "neither"
                        ),
                        message=(
                            "The repair resolves the listed semantic defect."
                            if passed
                            else "The original semantic defect remains unresolved."
                        ),
                    )
                    for item in ids
                ]
            )
        else:
            value = RepairVerificationResult(
                verifications=[
                    RepairVerification(
                        segment_id=item,
                        passed=passed,
                        current_acceptable=self.current_acceptable,
                        message=(
                            "The repair resolves the listed semantic defect."
                            if passed
                            else "The original semantic defect remains unresolved."
                        ),
                    )
                    for item in ids
                ]
            )
        generation = GenerationResult(
            content=value.model_dump_json(),
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=40, eval_count=8),
        )
        return StructuredGenerationResult(value=value, generation=generation)

    def generate_text(
        self, prompt, *, stream=True, think=None, progress_label="",
        context_minimum=None, context_maximum=None, model=None
    ):
        self.events.append(("text", model))
        self.feedback_prompts.append(prompt)
        self.feedback_models.append(model)
        segment_id = re.search(r"Output exactly <([^>]+)>", prompt).group(1)
        current = re.search(
            rf"\[{re.escape(segment_id)}\] REPAIR\nSOURCE:.*?\nCURRENT: (.*?)(?:\n\n|\Z)",
            prompt,
            flags=re.DOTALL,
        ).group(1)
        replacement = "甲" if len(self.feedback_prompts) == 1 else "乙"
        trailing_markers = re.search(r"((?:</I\d{3}>)+)$", current)
        suffix = trailing_markers.group(1) if trailing_markers else ""
        base = current[: -len(suffix)] if suffix else current
        corrected = (base[:-1] + replacement + suffix) if base else replacement
        return GenerationResult(
            content=f"<{segment_id}>{corrected}</{segment_id}>",
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=60, eval_count=12),
        )


class FakeMarkerPlacementClient:
    def generate_structured(self, prompt, schema, **kwargs):
        value = SingleInlineMarkerPlacementResult(
            placements=[
                SingleInlineMarkerPlacement(
                    reference_id="D0001-S000001",
                    before="当",
                    emphasized="我",
                    after="发现一样东西时。",
                )
            ]
        )
        generation = GenerationResult(
            content=value.model_dump_json(),
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=20, eval_count=5),
        )
        return StructuredGenerationResult(value=value, generation=generation)


class FakeUnchangedFeedbackClient(FakeVerificationClient):
    def generate_text(self, prompt, **kwargs):
        self.events.append(("text", kwargs.get("model")))
        self.feedback_prompts.append(prompt)
        self.feedback_models.append(kwargs.get("model"))
        segment_id = re.search(r"Output exactly <([^>]+)>", prompt).group(1)
        current = re.search(
            rf"\[{re.escape(segment_id)}\] REPAIR\nSOURCE:.*?\nCURRENT: (.*?)(?:\n\n|\Z)",
            prompt,
            flags=re.DOTALL,
        ).group(1)
        return GenerationResult(
            content=f"<{segment_id}>{current}</{segment_id}>",
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=20, eval_count=5),
        )


class ValidateRepairedStageTests:
    def test_unreproduced_deterministic_wrapper_is_not_sticky_obfuscated(self):
        segment_id = "D0001-S000001"
        deterministic = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.GLOSSARY,
            severity=AuditSeverity.MEDIUM,
            message="approved glossary term was not preserved: VRX",
        )
        wrapper = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message=(
                "Repair verification failed: Deterministic validation failed before "
                "semantic review: approved glossary term was not preserved: VRX"
            ),
            source="semantic",
        )
        document = TranslatedDocument(
            order=1,
            manifest_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            style="literary",
            segments=[
                TranslatedSegment(
                    segment_id=segment_id,
                    source_text="His vrx arm moved.",
                    translated_text="\u4ed6\u7684\u624b\u81c2\u52a8\u4e86\u3002",
                )
            ],
        )
        repaired = RepairedDocument(
            document=document,
            repairs=[
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=RepairDisposition.REVIEW,
                    original_translation="\u4ed6\u7684\u624b\u81c2\u52a8\u4e86\u3002",
                    repaired_translation="",
                    issues=[deterministic, wrapper],
                    attempts=2,
                )
            ],
        )
        clean = DocumentAudit(
            document_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            segment_count=1,
            passed=True,
        )
        result = _accept_unreproduced_deterministic_reviews(repaired, clean)
        assert result.repairs[0].disposition == RepairDisposition.ACCEPTED

    def test_unreproduced_quantity_deterministic_review_is_not_sticky(self):
        segment_id = "D0001-S000001"
        quantity = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message="typed quantity facts differ from source",
            source="quantity-deterministic",
        )
        wrapper = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message=(
                "Repair verification failed: Deterministic validation failed before "
                "semantic review: typed quantity facts differ from source"
            ),
            source="semantic",
        )
        document = TranslatedDocument(
            order=1,
            manifest_id="quantity-obfuscated",
            archive_path="OEBPS/quantity.xhtml",
            direction="en-zh",
            style="literary",
            segments=[
                TranslatedSegment(
                    segment_id=segment_id,
                    source_text="A dozen qel units.",
                    translated_text="十二个单位。",
                )
            ],
        )
        repaired = RepairedDocument(
            document=document,
            repairs=[
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=RepairDisposition.REVIEW,
                    original_translation=document.segments[0].translated_text,
                    repaired_translation="",
                    issues=[quantity, wrapper],
                    attempts=2,
                )
            ],
        )
        clean = DocumentAudit(
            document_id=document.manifest_id,
            archive_path=document.archive_path,
            direction="en-zh",
            segment_count=1,
            passed=True,
        )

        result = _accept_unreproduced_deterministic_reviews(repaired, clean)

        assert result.repairs[0].disposition == RepairDisposition.ACCEPTED

    def test_reproduced_or_independent_issue_remains_review_obfuscated(self):
        segment_id = "D0001-S000001"
        independent = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message="The qel quantity is reversed.",
            source="semantic",
        )
        document = TranslatedDocument(
            order=1,
            manifest_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            style="literary",
            segments=[
                TranslatedSegment(
                    segment_id=segment_id,
                    source_text="Five qel units.",
                    translated_text="\u4e94\u4e2a\u5355\u4f4d\u3002",
                )
            ],
        )
        repaired = RepairedDocument(
            document=document,
            repairs=[
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=RepairDisposition.REVIEW,
                    original_translation="\u4e94\u4e2a\u5355\u4f4d\u3002",
                    repaired_translation="",
                    issues=[independent],
                    attempts=1,
                )
            ],
        )
        clean = DocumentAudit(
            document_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            segment_count=1,
            passed=True,
        )
        result = _accept_unreproduced_deterministic_reviews(repaired, clean)
        assert result.repairs[0].disposition == RepairDisposition.REVIEW

    def test_clean_deterministic_root_retires_lower_severity_semantic_noise(self):
        segment_id = "D0001-S000001"
        deterministic = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message="numeric content differs from source",
        )
        lower_semantic = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.MEDIUM,
            message="The phrasing may be less literal than the source.",
            source="semantic",
        )
        wrapper = AuditIssue(
            segment_id=segment_id,
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.HIGH,
            message=(
                "Repair verification failed: Deterministic validation failed before "
                "semantic review: numeric content differs from source"
            ),
            source="semantic",
        )
        document = TranslatedDocument(
            order=1,
            manifest_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            style="literary",
            segments=[
                TranslatedSegment(
                    segment_id=segment_id,
                    source_text="Six and a half hundred qel units.",
                    translated_text="\u516d\u767e\u4e94\u5341\u4e2a\u5355\u4f4d\u3002",
                )
            ],
        )
        repaired = RepairedDocument(
            document=document,
            repairs=[
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=RepairDisposition.REVIEW,
                    original_translation=document.segments[0].translated_text,
                    repaired_translation="",
                    issues=[deterministic, lower_semantic, wrapper],
                    attempts=2,
                )
            ],
        )
        clean = DocumentAudit(
            document_id="obfuscated",
            archive_path="OEBPS/obfuscated.xhtml",
            direction="en-zh",
            segment_count=1,
            passed=True,
        )

        result = _accept_unreproduced_deterministic_reviews(repaired, clean)

        assert result.repairs[0].disposition == RepairDisposition.ACCEPTED

    def test_high_risk_semantic_change_remains_human_advisory(self):
        repair = SegmentRepair(
            segment_id="D0001-S000001",
            disposition=RepairDisposition.REPAIRED,
            original_translation="\u539f\u8bd1\u3002",
            repaired_translation="\u4fee\u8ba2\u8bd1\u6587\u3002",
            issues=[
                AuditIssue(
                    segment_id="D0001-S000001",
                    category=AuditCategory.MISTRANSLATION,
                    severity=AuditSeverity.HIGH,
                    message="The spatial direction is reversed.",
                    source="semantic",
                )
            ],
            attempts=1,
        )
        decision = RepairVerification(
            segment_id=repair.segment_id,
            passed=True,
            current_acceptable=False,
            message="The candidate fixes the direction.",
        )
        advisory = AppConfig.model_validate(
            {"audit": {"semantic_verification_policy": "human-high-risk"}}
        )
        autonomous = AppConfig()

        assert _requires_human_high_risk_change(repair, decision, advisory)
        assert not _requires_human_high_risk_change(repair, decision, autonomous)
        assert not (_requires_human_high_risk_change(
                repair,
                decision.model_copy(update={"current_acceptable": True}),
                advisory,
            ))

        generic_high = repair.model_copy(
            update={
                "issues": [
                    repair.issues[0].model_copy(
                        update={"message": "The idiomatic phrasing is inaccurate."}
                    )
                ]
            }
        )
        medium_spatial = repair.model_copy(
            update={
                "issues": [
                    repair.issues[0].model_copy(
                        update={"severity": AuditSeverity.MEDIUM}
                    )
                ]
            }
        )
        high_omission = repair.model_copy(
            update={
                "issues": [
                    repair.issues[0].model_copy(
                        update={
                            "category": AuditCategory.OMISSION,
                            "message": "A complete source clause is omitted.",
                        }
                    )
                ]
            }
        )
        assert not _requires_human_high_risk_change(generic_high, decision, advisory)
        assert not _requires_human_high_risk_change(medium_spatial, decision, advisory)
        assert _requires_human_high_risk_change(high_omission, decision, advisory)

    def prepare_workspace(
        self, base, config, *, repair_fails=False, audit_client=None
    ):
        epub = make_epub(base / "fixture.epub")
        workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, FakeTranslationClient())
        run_translation_audit_stage(
            workspace, config, audit_client or FakeAuditClient()
        )
        run_translation_repair_stage(
            workspace,
            config,
            FakeRepairClient(invalid_calls=10 if repair_fails else 0),
        )
        return workspace

    def run_validation_pipeline(self, workspace, config, client):
        run_repaired_review_stage(workspace, config, client)
        run_review_repair_stage(workspace, config, client)
        return run_repaired_validation_stage(workspace, config, client)

    def test_validation_passes_semantic_repair_loads_and_resumes(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "verifier_model": "gemma4:26b",
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeVerificationClient()
            first = self.run_validation_pipeline(workspace, config, client)
            call_count = len(client.events)
            run_repaired_review_stage(workspace, config, client)
            run_review_repair_stage(workspace, config, client)
            second = run_repaired_validation_stage(workspace, config, client)
            assert first == second
            assert len(client.events) == call_count
            assert first.passed
            assert first.review_segment_ids == []
            assert len(client.prompts) == 1
            assert client.models == ["gemma4:26b"]
            assert client.thinking == [False]
            assert "review=1/1" in client.progress_labels[0]
            assert "attempt=1/2" in client.progress_labels[0]
            assert client.context_minimums == [16_384]
            assert client.output_token_limits == [1_024]
            assert client.structured_attempt_limits == [1]
            assert load_repaired_validation_report(workspace) == first
            validations = load_repaired_document_validations(workspace)
            assert validations[0].passed
            assert validations[0].semantic_verifications == []
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(connection, "review-0000-chapter", "review_repaired")
                assert unit["validation"]["scope_valid"]
                assert len(list_attempts(connection, "verify-final-0000-chapter")) == 0
            finally:
                connection.close()

    def test_failed_semantic_verification_enters_review_queue(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            report = self.run_validation_pipeline(
                workspace, config, FakeVerificationClient(passed=False)
            )
            assert not report.passed
            assert len(report.review_segment_ids) == 1
            assert report.remaining_issue_count == 1
            assert report.remaining_defect_count == 1
            assert report.approval_required_count == 0
            final = load_validated_repaired_documents(workspace)[0]
            failed_id = report.review_segment_ids[0]
            repair = next(item for item in final.repairs if item.segment_id == failed_id)
            segment = next(
                item for item in final.document.segments if item.segment_id == failed_id
            )
            assert segment.translated_text == repair.original_translation
            assert repair.disposition == RepairDisposition.REVIEW

    def test_high_risk_verified_repair_stays_in_human_review_queue(self):
        config = AppConfig.model_validate(
            {
                "audit": {
                    "semantic_sample_every": 2,
                    "semantic_verification_policy": "human-high-risk",
                }
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(
                Path(directory),
                config,
                audit_client=FakeAuditClient(
                    issue_severity=AuditSeverity.HIGH,
                    issue_message="The spatial direction is reversed.",
                ),
            )
            report = self.run_validation_pipeline(
                workspace, config, FakeVerificationClient(passed=True)
            )

            assert not report.passed
            assert report.remaining_issue_count == 1
            assert report.remaining_defect_count == 0
            assert report.approval_required_count == 1
            assert report.approval_segment_ids == report.review_segment_ids
            validations = load_repaired_document_validations(workspace)
            assert validations[0].review_segment_ids == report.review_segment_ids
            assert validations[0].approval_segment_ids == report.review_segment_ids
            advisory = next(
                item
                for item in validations[0].semantic_verifications
                if item.segment_id == report.review_segment_ids[0]
            )
            assert not advisory.passed
            assert "explicit human review" in advisory.message

    def test_exhausted_review_verification_escalates_without_aborting_stage(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            report = run_repaired_review_stage(
                workspace, config, FakeVerificationClient(invalid_calls=10)
            )

            assert not report.passed
            assert len(report.review_segment_ids) == 1
            connection = connect_state(workspace.state_file)
            try:
                unit = get_work_unit(
                    connection, "review-0000-chapter", "review_repaired"
                )
                assert unit["status"] == "completed"
                assert unit["validation"]["fallback_escalation"]
            finally:
                connection.close()

    def test_verifier_feedback_repairs_revalidates_and_publishes_corrected_draft(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeVerificationClient(
                passed=False, pass_after_feedback=True
            )
            report = self.run_validation_pipeline(workspace, config, client)
            assert report.passed
            assert client.events == ([
                    ("structured", "gemma4:31b"),
                    ("text", None),
                    ("structured", "gemma4:31b"),
                ])
            assert len(client.feedback_prompts) == 1
            assert client.output_token_limits == [1_024, 1_024]
            assert client.structured_attempt_limits == [1, 1]
            assert "Independent validation rejected" in client.feedback_prompts[0]
            assert "Retranslate the entire target segment" in client.feedback_prompts[0]
            assert "semantic emphasis" in client.feedback_prompts[0]
            assert "restructure the complete clause" in client.feedback_prompts[0]
            assert "Feedback-repair examples" in client.feedback_prompts[0]
            assert "它长着<I000>非常</I000>长的爪子" in client.feedback_prompts[0]
            assert "copy the repair pattern, never the wording" in client.feedback_prompts[0]
            assert len(client.prompts) == 2
            segment_labels = [
                event.label
                for event in client.progress_events
                if event.kind == "segment_result"
            ]
            for stage in ("review_repaired", "repair_review", "validate_repaired"):
                assert (any(
                        re.search(rf"segment=\d+/\d+ id=.* stage={stage}$", label)
                        for label in segment_labels
                    )), f"missing counted segment result for {stage}: {segment_labels}"
            final = load_validated_repaired_documents(workspace)[0]
            assert (any(
                    re.search(r"甲(?:</I\d{3}>)*$", item.translated_text)
                    for item in final.document.segments
                ))
            compiled = run_epub_compile_stage(workspace, config)
            assert compiled.output_size > 0
            epub_validation = run_epub_validation_stage(workspace)
            assert epub_validation.passed

    def test_rejected_final_candidate_stops_without_another_repair_loop(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeVerificationClient(
                passed=False, pass_after_feedback_count=2
            )
            report = self.run_validation_pipeline(workspace, config, client)
            assert not report.passed
            assert client.feedback_models == [None]
            assert len(client.prompts) == 2
            assert client.models == ["gemma4:31b", "gemma4:31b"]

    def test_feedback_marker_placement_recovers_marker_free_correction(self):
        repaired, validation = _repair_feedback_markers(
            FakeMarkerPlacementClient(),
            "when <I000>I</I000> find a thing.",
            "D0001-S000001",
            "当我<I000>我</I000>发现一样东西时。",
            "当我发现一样东西时。",
            AppConfig(),
            model=None,
            thinking=False,
            progress_label="feedback marker test",
        )
        assert validation.passed
        assert repaired == "当<I000>我</I000>发现一样东西时。"

    def test_feedback_examples_cover_chinese_to_english_repairs(self):
        examples = _feedback_repair_examples("zh-en")
        assert "when <I000>I</I000> discover" in examples
        assert "it has <I000>very</I000> long claws" in examples
        assert "rewrite the complete clause as natural English" in examples

    def test_marker_adjacent_duplicates_are_removed_without_moving_marker(self):
        assert _deduplicate_adjacent_marker_text("当我<I000>我</I000>发现") == "当<I000>我</I000>发现"
        assert _deduplicate_adjacent_marker_text("when <I000>I</I000> I discover") == "when <I000>I</I000> discover"
        assert _deduplicate_adjacent_marker_text("very <I000>important</I000> work") == "very <I000>important</I000> work"

    def test_preference_only_correct_translation_does_not_block_compilation(self):
        issue = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.MISTRANSLATION,
            severity=AuditSeverity.MEDIUM,
            message=(
                "The translation is grammatically acceptable but slightly formulaic; "
                "another wording may sound more natural."
            ),
            suggested_fix="Use another word order.",
            source="semantic",
        )
        assert _is_nonblocking_preference([issue])
        assert not (_is_nonblocking_preference(
                [issue.model_copy(update={"severity": AuditSeverity.HIGH})]
            ))

    def test_exact_glossary_replace_all_instruction_is_deterministic(self):
        issue = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.GLOSSARY,
            severity=AuditSeverity.MEDIUM,
            message="A legal entity is translated inconsistently.",
            suggested_fix=(
                "Change all instances of '古腾堡项目文学档案基金会' to "
                "'古腾堡计划文学档案基金会'."
            ),
            source="semantic",
        )
        original = "款项付给古腾堡项目文学档案基金会，再寄往古腾堡项目文学档案基金会。"
        assert _apply_exact_glossary_replacements(original, [issue]) == "款项付给古腾堡计划文学档案基金会，再寄往古腾堡计划文学档案基金会。"

    def test_verification_scope_retries_with_diagnostics(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeVerificationClient(invalid_calls=1)
            report = self.run_validation_pipeline(workspace, config, client)
            assert report.passed
            assert len(client.prompts) == 2
            assert "previous verification was rejected" in client.prompts[1]

    def test_obfuscated_unreproduced_diagnosis_accepts_original(self):
        config = AppConfig.model_validate({"audit": {"semantic_sample_every": 2}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeVerificationClient(
                passed=False, current_acceptable=True
            )
            report = self.run_validation_pipeline(workspace, config, client)
            assert report.passed
            final = load_validated_repaired_documents(workspace)[0]
            accepted = next(
                item
                for item in final.repairs
                if item.disposition is RepairDisposition.ACCEPTED
            )
            segment = next(
                item
                for item in final.document.segments
                if item.segment_id == accepted.segment_id
            )
            assert segment.translated_text == accepted.original_translation
            assert "diagnosis not reproduced" in accepted.message

    def test_high_severity_audited_original_cannot_be_reinstated(self):
        repair = SegmentRepair(
            segment_id="D0001-S000001",
            disposition=RepairDisposition.REPAIRED,
            original_translation="old",
            repaired_translation="candidate",
            issues=[
                AuditIssue(
                    segment_id="D0001-S000001",
                    category=AuditCategory.MISTRANSLATION,
                    severity=AuditSeverity.HIGH,
                    message="The original invents an action.",
                    source="semantic",
                )
            ],
            attempts=1,
        )

        assert not _original_is_safe_to_reinstate(repair)
        assert (_original_is_safe_to_reinstate(
                repair.model_copy(
                    update={
                        "issues": [
                            repair.issues[0].model_copy(
                                update={"severity": AuditSeverity.MEDIUM}
                            )
                        ]
                    }
                )
            ))

    def test_unrepaired_failure_remains_in_review_without_verifier(self):
        config = AppConfig.model_validate(
            {"audit": {"semantic_sample_every": 2}, "workflow": {"max_retries": 0}}
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config, repair_fails=True)
            client = FakeUnchangedFeedbackClient(passed=True)
            report = self.run_validation_pipeline(workspace, config, client)
            assert not report.passed
            assert len(report.review_segment_ids) == 1
            assert client.feedback_models == [None]
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "validate_repaired")
                assert stage["status"] == "completed"
                assert "human review" in stage["message"]
            finally:
                connection.close()

    def test_stage_requires_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = AppConfig()
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
            with pytest.raises(RuntimeError, match="repair_translation"):
                run_repaired_review_stage(workspace, config, FakeVerificationClient())

