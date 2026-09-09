import json
import tempfile
from pathlib import Path

from book_agent.config import AppConfig
from book_agent.ollama_client import (
    GenerationMetrics,
    GenerationResult,
    StructuredGenerationResult,
)
from book_agent.prose_rewrite import (
    ProseRewriteBatch,
    ProseRewriteDecision,
    prose_rewrite_candidate_reasons,
    protected_rewrite_changes,
)
from book_agent.stages.audit import run_translation_audit_stage
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.repair import (
    load_repaired_documents,
    run_translation_repair_stage,
)
from book_agent.stages.repair_review import (
    load_review_repaired_documents,
    run_review_repair_stage,
)
from book_agent.stages.reprose import (
    load_reprosed_documents,
    run_prose_rewrite_stage,
)
from book_agent.stages.review_repaired import (
    load_repaired_review_results,
    run_repaired_review_stage,
)
from book_agent.stages.translate import run_translation_stage
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient
from tests.test_validate_repaired_stage import FakeVerificationClient


class FakeProseRewriteClient:
    def __init__(self):
        self.calls = 0
        self.models = []
        self.progress_events = []

    def report_progress(self, event):
        self.progress_events.append(event)

    def generate_structured(self, prompt, schema, **kwargs):
        self.calls += 1
        self.models.append(kwargs.get("model"))
        items = json.loads(prompt.split("ITEMS:\n", 1)[1])
        decisions = []
        for index, item in enumerate(items):
            current = item["current_translation"]
            decisions.append(
                ProseRewriteDecision(
                    segment_id=item["segment_id"],
                    action="rewrite" if index == 0 else "keep",
                    rewritten_text=(current + "确实") if index == 0 else "",
                    reason=(
                        "Remove a concrete piece of translationese."
                        if index == 0
                        else "The current literary Chinese is already natural."
                    ),
                )
            )
        value = ProseRewriteBatch(decisions=decisions)
        generation = GenerationResult(
            content=value.model_dump_json(),
            thinking="",
            metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20),
        )
        return StructuredGenerationResult(value=value, generation=generation)


class ProseRewriteStageTests:
    def test_risk_filter_selects_concrete_translationese_or_complex_prose(self):
        assert "translationese" in (prose_rewrite_candidate_reasons(
                "He inspected it.",
                "他在这种情况下对它进行了检查。",
                target_characters=90,
            ))
        assert (prose_rewrite_candidate_reasons(
                "He left.", "他走了。", target_characters=90
            )) == []
        assert "translationese" in (prose_rewrite_candidate_reasons(
                "He is all about the present.",
                "他全关于现在。",
                target_characters=90,
            ))
        assert "translationese" in (prose_rewrite_candidate_reasons(
                "The tool was not designed to be used as a weapon.",
                "工具并非计划被重新用作武器。",
                target_characters=90,
            ))

    def test_protected_signature_gate_detects_semantic_rewrite_drift(self):
        changes = protected_rewrite_changes(
            "The rush-boats sped past.",
            "快艇疾驰而过。",
            "快艇在芦苇丛中疾驰而过。",
        )
        assert "spatial relation" in changes
        assert "negation/polarity" in (protected_rewrite_changes(
                "He did not resist.", "他没有反抗。", "他反抗了。"
            ))

    def prepare_workspace(self, base: Path, config: AppConfig):
        epub = make_epub(base / "fixture.epub")
        workspace = create_job_workspace(
            epub, base / "runs", config, job_id="fixture"
        )
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(
            workspace,
            config,
            FakeTranslationClient(translated_text="这是一段自然且完整的中文译文。"),
        )
        run_translation_audit_stage(workspace, config, None)
        run_translation_repair_stage(workspace, config, FakeRepairClient())
        return workspace

    def test_enabled_stage_rewrites_safely_and_resumes(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_enabled": False},
                "reprose": {
                    "enabled": True,
                    "model": "gemma4:31b",
                    "min_target_characters": 1,
                    "batch_size": 2,
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            client = FakeProseRewriteClient()
            first = run_prose_rewrite_stage(workspace, config, client)
            call_count = client.calls
            second = run_prose_rewrite_stage(workspace, config, client)

            assert first == second
            assert first.candidate_segment_count > 0
            assert first.applied_rewrite_count > 0
            assert client.calls == call_count
            assert all(model == "gemma4:31b" for model in client.models)
            documents = load_reprosed_documents(workspace)
            prose_repairs = [
                repair
                for document in documents
                for repair in document.repairs
                if any(issue.source == "reprose" for issue in repair.issues)
            ]
            assert len(prose_repairs) == first.applied_rewrite_count

    def test_disabled_stage_publishes_passthrough_without_client(self):
        config = AppConfig.model_validate(
            {"audit": {"semantic_enabled": False}}
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            report = run_prose_rewrite_stage(workspace, config, None)

            assert report.candidate_segment_count == 0
            assert report.applied_rewrite_count == 0
            assert load_reprosed_documents(workspace)

    def test_every_applied_rewrite_enters_independent_review(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_enabled": False},
                "reprose": {
                    "enabled": True,
                    "min_target_characters": 1,
                    "batch_size": 2,
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            rewrite = run_prose_rewrite_stage(
                workspace, config, FakeProseRewriteClient()
            )
            reviewer = FakeVerificationClient(passed=True)
            review = run_repaired_review_stage(workspace, config, reviewer)

            reviewed_ids = {
                item.segment_id
                for result in load_repaired_review_results(workspace).values()
                for item in result.verifications
            }
            assert review.passed
            assert reviewed_ids == set(rewrite.changed_segment_ids)
            assert reviewer.prompts
            assert all(model == "gemma4:31b" for model in reviewer.models)
            assert not any(reviewer.thinking)

    def test_tied_rewrite_restores_last_known_good_translation(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_enabled": False},
                "reprose": {
                    "enabled": True,
                    "min_target_characters": 1,
                    "batch_size": 2,
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            originals = {
                segment.segment_id: segment.translated_text
                for document in load_repaired_documents(workspace)
                for segment in document.document.segments
            }
            rewrite = run_prose_rewrite_stage(
                workspace, config, FakeProseRewriteClient()
            )
            reviewer = FakeVerificationClient(
                passed=True, current_acceptable=True
            )
            review = run_repaired_review_stage(workspace, config, reviewer)
            assert not review.passed

            repaired = run_review_repair_stage(
                workspace, config, reviewer
            )
            assert repaired.review_segment_ids == []
            final_targets = {
                segment.segment_id: segment.translated_text
                for document in load_review_repaired_documents(workspace)
                for segment in document.document.segments
            }
            for segment_id in rewrite.changed_segment_ids:
                assert final_targets[segment_id] == originals[segment_id]

