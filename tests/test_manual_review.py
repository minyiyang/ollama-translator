import json
import tempfile
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.manual_review import (
    ManualReviewResolution,
    ManualReviewResolutionSet,
    ManualTextReplacement,
    load_manual_review_resolution_file,
    resolve_manual_review,
)
from book_agent.pipeline_state import build_stage_input_hash
from book_agent.stages.compile import run_epub_compile_stage
from book_agent.stages.validate_repaired import load_validated_repaired_documents
from book_agent.state import connect_state, get_stage_status


class ManualReviewResolutionTests:
    def test_completed_generated_worksheet_applies_and_writes_handoff_verdict(self):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )
            with pytest.raises(RuntimeError):
                run_epub_compile_stage(workspace, config)
            worksheet_path = workspace.directory(
                "reports/final-human-review.decisions.json"
            )
            worksheet = json.loads(
                worksheet_path.read_text(encoding="utf-8")
            )
            assert worksheet["resolutions"][0]["decision"] == "pending"
            worksheet["resolutions"][0]["decision"] = "accept"
            worksheet["resolutions"][0]["reason"] = (
                "Human reviewer accepted the source-grounded current wording."
            )
            worksheet_path.write_text(
                json.dumps(worksheet, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            payload = load_manual_review_resolution_file(workspace, worksheet_path)
            assert payload.resolutions[0].override_deterministic_findings
            report = resolve_manual_review(workspace, payload)

            assert report.passed
            verdict = json.loads(
                workspace.directory("reports/final-human-review-verdict.json").read_text(
                    encoding="utf-8"
                )
            )
            assert verdict["ready_for_final_approval"]
            assert verdict["verdicts"][0]["decision"] == "accept"
            markdown = workspace.directory(
                "reports/final-human-review-verdict.md"
            ).read_text(encoding="utf-8")
            assert "# Final human-review verdict" in markdown
            assert "## Approval command" in markdown

    def test_generated_worksheet_refuses_pending_or_stale_decisions(self):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )
            with pytest.raises(RuntimeError):
                run_epub_compile_stage(workspace, config)
            path = workspace.directory("reports/final-human-review.decisions.json")
            with pytest.raises(ValueError, match="pending decisions"):
                load_manual_review_resolution_file(workspace, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["draft_output_hash"] = "stale-draft"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with pytest.raises(ValueError, match="stale"):
                load_manual_review_resolution_file(workspace, path)

    def test_obfuscated_review_acceptance_is_checked_and_unblocks_compile(self):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )
            from book_agent.stages.validate_repaired import (
                load_repaired_validation_report,
            )

            before = load_repaired_validation_report(workspace)
            assert len(before.review_segment_ids) == 1
            result = resolve_manual_review(
                workspace,
                ManualReviewResolutionSet(
                    resolutions=[
                        ManualReviewResolution(
                            segment_id=before.review_segment_ids[0],
                            reason="Obfuscated human review accepted the current wording.",
                        )
                    ]
                ),
            )
            assert result.passed
            assert result.review_segment_ids == []
            connection = connect_state(workspace.state_file)
            try:
                validate_stage = get_stage_status(connection, "validate_repaired")
                repair_review_stage = get_stage_status(connection, "repair_review")
                assert validate_stage["message"] == "manual review resolved"
                from book_agent.stages.validate_repaired import (
                    VALIDATE_REPAIRED_STAGE_VERSION,
                )

                assert validate_stage["input_hash"] == (build_stage_input_hash(
                        {
                            "repair_review": str(repair_review_stage["output_hash"]),
                            "audit": config.audit.model_dump_json(),
                            "model": config.audit.verifier_model or config.audit.model,
                            "stage_version": VALIDATE_REPAIRED_STAGE_VERSION,
                        }
                    ))
                assert get_stage_status(connection, "compile")["status"] == "pending"
            finally:
                connection.close()
            assert run_epub_compile_stage(workspace, config).output_size > 0

    def test_duplicate_manual_resolution_ids_are_rejected(self):
        item = ManualReviewResolution(
            segment_id="DX001-SX000001",
            reason="Obfuscated reviewed decision.",
        )
        with pytest.raises(ValueError, match="unique"):
            ManualReviewResolutionSet(resolutions=[item, item])

    def test_out_of_queue_manual_acceptance_requires_an_explicit_edit(self):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )
            current = load_validated_repaired_documents(workspace)[0].document
            from book_agent.stages.validate_repaired import (
                load_repaired_validation_report,
            )

            unresolved = set(load_repaired_validation_report(workspace).review_segment_ids)
            segment_id = next(
                item.segment_id
                for item in current.segments
                if item.segment_id not in unresolved
            )
            with pytest.raises(ValueError, match="must contain an explicit edit"):
                resolve_manual_review(
                    workspace,
                    ManualReviewResolutionSet(
                        resolutions=[
                            ManualReviewResolution(
                                segment_id=segment_id,
                                reason="Obfuscated acceptance outside the queue.",
                            )
                        ]
                    ),
                )

    def test_out_of_queue_bounded_correction_is_supported(self):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(
                Path(directory), config, unresolved=True
            )
            from book_agent.stages.validate_repaired import (
                load_repaired_validation_report,
            )

            unresolved = set(load_repaired_validation_report(workspace).review_segment_ids)
            current = load_validated_repaired_documents(workspace)[0].document
            segment = next(
                item
                for item in current.segments
                if item.segment_id not in unresolved
                and item.translated_text.count("译文") == 1
            )
            report = resolve_manual_review(
                workspace,
                ManualReviewResolutionSet(
                    resolutions=[
                        ManualReviewResolution(
                            segment_id=segment.segment_id,
                            replacements=[
                                ManualTextReplacement(
                                    old_span="译文",
                                    new_span="修订译文",
                                )
                            ],
                            reason="Obfuscated bounded correction outside the queue.",
                        )
                    ]
                ),
            )
            assert report.review_segment_ids == sorted(unresolved)
            updated = load_validated_repaired_documents(workspace)[0].document
            updated_segment = next(
                item for item in updated.segments if item.segment_id == segment.segment_id
            )
            assert "修订译文" in updated_segment.translated_text

    def test_obfuscated_manual_resolution_supports_one_exact_replacement(self):
        resolution = ManualReviewResolution(
            segment_id="DX001-SX000001",
            replacements=[
                ManualTextReplacement(old_span="QX-71", new_span="QX-72")
            ],
            reason="Obfuscated bounded correction.",
        )
        assert resolution.translated_text is None
        assert resolution.replacements[0].old_span == "QX-71"

    def test_obfuscated_manual_resolution_rejects_mixed_edit_modes(self):
        with pytest.raises(ValueError, match="not both"):
            ManualReviewResolution(
                segment_id="DX001-SX000001",
                translated_text="Obfuscated replacement.",
                replacements=[
                    ManualTextReplacement(old_span="QX-71", new_span="QX-72")
                ],
                reason="Obfuscated invalid mixed request.",
            )

    def test_deterministic_override_can_accompany_reviewed_replacement(self):
        resolution = ManualReviewResolution(
            segment_id="DX001-SX000001",
            translated_text="Obfuscated replacement.",
            reason="Human-reviewed contextual exception.",
            override_deterministic_findings=True,
        )
        assert resolution.override_deterministic_findings

