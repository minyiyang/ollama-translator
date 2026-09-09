import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from book_agent.config import AppConfig, BudgetConfig, load_config
from book_agent.languages import TranslationDirection
from book_agent.styles import TranslationStyle


PRODUCTION_PROFILE_YAML = """ollama:
  model: qwen3.8:latest
  num_ctx: 131072
budget:
  working_limit: 32768
  prompt_tokens: 3000
  glossary_tokens: 1500
  source_tokens: 3500
  expected_output_tokens: 6000
  reserve_tokens: 1500
translation:
  thinking: false
  max_prompt_tokens: 8000
  max_num_ctx: 16384
  boundary_context: adjacent-read-only
audit:
  model: gemma4:31b
  verifier_model: gemma4:31b
  repair_model: qwen3.8:latest
  semantic_max_candidates_per_batch: 1
  repair_max_num_ctx: 16384
  max_num_ctx: 16384
workflow:
  production_profile: example-production
  production_profile_version: 3
  require_final_review: true
  compile_max_unresolved_review_segments: 0
"""


class ConfigTests:
    def test_defaults_target_local_qwen_and_100k_working_limit(self) -> None:
        config = AppConfig()
        assert config.ollama.model == "qwen3.8:latest"
        assert config.ollama.num_ctx == 131_072
        assert config.ollama.adaptive_num_ctx
        assert config.ollama.min_num_ctx == 16_384
        assert config.ollama.progress_interval_seconds == 30.0
        assert config.ollama.progress_interval_tokens == 2_000
        assert config.ollama.progress_interval_chars == 0
        assert config.budget.working_limit == 65_536
        assert config.translation.direction == TranslationDirection.EN_TO_ZH
        assert config.translation.style == TranslationStyle.LITERARY
        assert config.translation.de_ai_enabled
        assert config.translation.de_ai_strength == "conservative"
        assert not config.translation.thinking
        assert config.translation.marker_examples == "few-shot"
        assert config.translation.fallback_models == []
        assert config.translation.attempts_per_model == 2
        assert config.translation.harmonize_fallback_with_primary
        assert config.translation.max_num_ctx == 65_536
        assert config.translation.max_prompt_tokens == 20_000
        assert config.translation.context_multiplier == 3.0
        assert config.translation.boundary_context == "none"
        assert config.workflow.defer_failed_translation_segments
        assert not config.reprose.enabled
        assert config.reprose.model == "gemma4:26b"
        assert config.reprose.verifier_model == "gemma4:31b"
        assert not config.reprose.verifier_thinking
        assert config.audit.semantic_max_output_tokens == 3_072

    def test_legacy_progress_defaults_migrate_to_token_throttling(self) -> None:
        config = AppConfig.model_validate(
            {
                "ollama": {
                    "progress_interval_seconds": 2.0,
                    "progress_interval_chars": 2_000,
                }
            }
        )
        assert config.ollama.progress_interval_seconds == 30.0
        assert config.ollama.progress_interval_tokens == 2_000
        assert config.ollama.progress_interval_chars == 0

    def test_reprose_context_ranges_are_bounded(self) -> None:
        with pytest.raises(ValidationError, match="reprose min_num_ctx"):
            AppConfig.model_validate(
                {
                    "reprose": {
                        "min_num_ctx": 32_768,
                        "max_num_ctx": 16_384,
                    }
                }
            )
        with pytest.raises(ValidationError, match="verifier_max_num_ctx"):
            AppConfig.model_validate(
                {"reprose": {"verifier_max_num_ctx": 200_000}}
            )

    def test_budget_config_converts_to_domain_object(self) -> None:
        budget = BudgetConfig().as_token_budget()
        assert budget.source_tokens == 20_000
        assert budget.allocated_tokens == 64_000

    def test_glossary_defaults_use_bounded_nonthinking_extraction(self) -> None:
        glossary = AppConfig().glossary
        assert glossary.extraction_enabled
        assert glossary.extraction_model == "qwen3.8:27b"
        assert not glossary.extraction_thinking
        assert glossary.extraction_min_num_ctx == 16_384
        assert glossary.extraction_max_num_ctx == 32_768
        assert glossary.extraction_max_entries == 120
        assert glossary.extraction_max_evidence_per_entry == 3
        assert glossary.extraction_chapters_per_chunk == 8
        assert glossary.extraction_chunk_tokens == 9_000
        assert glossary.extraction_context_multiplier == 2.0
        assert glossary.resolution_chunk_tokens == 8_000
        assert glossary.resolution_min_num_ctx == 16_384
        assert glossary.resolution_max_num_ctx == 32_768
        assert glossary.resolution_context_multiplier == 2.0
        assert glossary.approval_chunk_tokens == 8_000
        assert glossary.approval_min_num_ctx == 16_384
        assert glossary.approval_max_num_ctx == 32_768
        assert glossary.approval_context_multiplier == 2.0
        assert glossary.approval_auto_approve_min_confidence == 0.9
        assert not AppConfig().workflow.llm_glossary_review
        assert AppConfig().workflow.compile_max_unresolved_review_segments == 0
        assert AppConfig().preprocessing.mode == "annotate"

    def test_glossary_extraction_can_be_disabled_with_configured_sources(self) -> None:
        config = AppConfig.model_validate(
            {
                "glossary": {
                    "extraction_enabled": False,
                    "book_glossaries": ["reviewed.json"],
                }
            }
        )
        assert not config.glossary.extraction_enabled

    def test_disabled_glossary_extraction_requires_a_configured_source(self) -> None:
        with pytest.raises(ValidationError, match="at least one configured glossary"):
            AppConfig.model_validate(
                {"glossary": {"extraction_enabled": False}}
            )

    def test_glossary_extraction_model_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"glossary": {"extraction_model": ""}})

    def test_glossary_extraction_context_range_is_bounded(self) -> None:
        with pytest.raises(ValidationError, match="extraction_max_num_ctx"):
            AppConfig.model_validate(
                {"glossary": {"extraction_max_num_ctx": 200_000}}
            )
        with pytest.raises(ValidationError, match="extraction_min_num_ctx"):
            AppConfig.model_validate(
                {
                    "glossary": {
                        "extraction_min_num_ctx": 65_536,
                        "extraction_max_num_ctx": 32_768,
                    }
                }
            )

    def test_glossary_resolution_context_range_is_bounded(self) -> None:
        with pytest.raises(ValidationError, match="resolution_max_num_ctx"):
            AppConfig.model_validate(
                {"glossary": {"resolution_max_num_ctx": 200_000}}
            )
        with pytest.raises(ValidationError, match="resolution_min_num_ctx"):
            AppConfig.model_validate(
                {
                    "glossary": {
                        "resolution_min_num_ctx": 65_536,
                        "resolution_max_num_ctx": 32_768,
                    }
                }
            )

    def test_glossary_approval_context_range_is_bounded(self) -> None:
        with pytest.raises(ValidationError, match="approval_max_num_ctx"):
            AppConfig.model_validate(
                {"glossary": {"approval_max_num_ctx": 200_000}}
            )
        with pytest.raises(ValidationError, match="approval_min_num_ctx"):
            AppConfig.model_validate(
                {
                    "glossary": {
                        "approval_min_num_ctx": 65_536,
                        "approval_max_num_ctx": 32_768,
                    }
                }
            )

    def test_preprocessing_mode_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"preprocessing": {"mode": "guess"}})

    def test_translation_fallback_rejects_empty_model(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"translation": {"fallback_models": [""]}})

    def test_translation_marker_examples_reject_unknown_mode(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"translation": {"marker_examples": "many"}})

    def test_translation_boundary_context_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate(
                {"translation": {"boundary_context": "translate-neighbors"}}
            )

    def test_translation_context_ceiling_must_fit_global_context(self) -> None:
        with pytest.raises(ValidationError, match="translation.max_num_ctx"):
            AppConfig.model_validate({"translation": {"max_num_ctx": 200_000}})

    def test_translation_prompt_ceiling_must_fit_translation_context(self) -> None:
        with pytest.raises(ValidationError, match="max_prompt_tokens"):
            AppConfig.model_validate(
                {"translation": {"max_num_ctx": 16_384, "max_prompt_tokens": 16_384}}
            )

    def test_audit_defaults_are_selective_and_target_medium_issues(self) -> None:
        audit = AppConfig().audit
        assert audit.model == "gemma4:31b"
        assert audit.verifier_model is None
        assert audit.repair_model is None
        assert not audit.thinking
        assert audit.semantic_enabled
        assert audit.semantic_min_source_tokens == 180
        assert audit.semantic_max_candidates_per_batch == 50
        assert audit.repair_min_severity == "medium"
        assert audit.repair_min_num_ctx == 16_384
        assert audit.repair_max_num_ctx is None
        assert audit.repair_context_multiplier == 1.0
        assert audit.repair_max_attempts == 2
        assert audit.max_num_ctx == 65_536
        assert not audit.quantity.enabled
        assert audit.quantity.mode == "shadow"
        assert audit.quantity.model == "gemma4:26b"

        quantity = AppConfig.model_validate(
            {
                "audit": {
                    "quantity": {
                        "enabled": True,
                        "mode": "enforce",
                        "escalation_model": "gemma4:31b",
                    }
                }
            }
        ).audit.quantity
        assert quantity.enabled
        assert quantity.mode == "enforce"
        assert quantity.escalation_model == "gemma4:31b"

    def test_audit_length_ratio_range_must_be_ordered(self) -> None:
        with pytest.raises(ValidationError, match="max_length_ratio"):
            AppConfig.model_validate(
                {"audit": {"min_length_ratio": 2.0, "max_length_ratio": 1.0}}
            )
        with pytest.raises(ValidationError, match="verifier_model"):
            AppConfig.model_validate({"audit": {"verifier_model": " "}})
        with pytest.raises(ValidationError, match="quantity escalation_model"):
            AppConfig.model_validate(
                {"audit": {"quantity": {"escalation_model": " "}}}
            )

    def test_glossary_chunk_limit_cannot_exceed_working_limit(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"glossary": {"extraction_chunk_tokens": 100_001}})

    def test_context_smaller_than_working_limit_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="model context"):
            AppConfig.model_validate({"ollama": {"num_ctx": 65_535}})

    def test_retry_max_cannot_be_less_than_initial_delay(self) -> None:
        with pytest.raises(ValidationError, match="retry_max_seconds"):
            AppConfig.model_validate(
                {
                    "ollama": {
                        "retry_initial_seconds": 2,
                        "retry_max_seconds": 1,
                    }
                }
            )

    def test_minimum_adaptive_context_cannot_exceed_maximum(self) -> None:
        with pytest.raises(ValidationError, match="min_num_ctx"):
            AppConfig.model_validate(
                {"ollama": {"num_ctx": 100_000, "min_num_ctx": 100_001}}
            )

    def test_repair_context_minimum_cannot_exceed_maximum(self) -> None:
        with pytest.raises(ValidationError, match="repair_min_num_ctx"):
            AppConfig.model_validate(
                {"audit": {"repair_min_num_ctx": 200_000}}
            )

    def test_glossary_context_minimum_cannot_exceed_maximum(self) -> None:
        with pytest.raises(ValidationError, match="extraction_min_num_ctx"):
            AppConfig.model_validate(
                {"glossary": {"extraction_min_num_ctx": 200_000}}
            )

    def test_unknown_configuration_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"unknown": True})

    def test_custom_style_requires_file(self) -> None:
        with pytest.raises(ValidationError, match="custom_style_file"):
            AppConfig.model_validate({"translation": {"style": "custom"}})

    def test_chinese_to_english_direction_is_supported(self) -> None:
        config = AppConfig.model_validate({"translation": {"direction": "zh-en"}})
        assert config.translation.direction == TranslationDirection.ZH_TO_EN

    def test_unsupported_direction_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"translation": {"direction": "en-fr"}})

    def test_unsupported_de_ai_strength_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"translation": {"de_ai_strength": "aggressive"}})

    def test_noncustom_style_rejects_custom_file(self) -> None:
        with pytest.raises(ValidationError, match="only"):
            AppConfig.model_validate(
                {"translation": {"style": "natural", "custom_style_file": "style.txt"}}
            )

    def test_load_config_parses_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.yaml")
            path.write_text(
                "ollama:\n  model: qwen3.8:latest\ntranslation:\n  direction: zh-en\n  style: fantasy\n",
                encoding="utf-8",
            )
            config = load_config(path)
            assert config.ollama.model == "qwen3.8:latest"
            assert config.translation.style == TranslationStyle.FANTASY
            assert config.translation.direction == TranslationDirection.ZH_TO_EN

    def test_load_empty_config_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.yaml")
            path.write_text("", encoding="utf-8")
            assert load_config(path) == AppConfig()

    def test_load_config_requires_mapping_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.yaml")
            path.write_text("- invalid\n- root\n", encoding="utf-8")
            with pytest.raises(ValueError, match="mapping"):
                load_config(path)


    def test_load_config_reads_full_production_profile(self) -> None:
        """A complete profile overrides every stage-level control it names."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "production.yaml")
            path.write_text(
                PRODUCTION_PROFILE_YAML,
                encoding="utf-8",
            )
            config = load_config(path)

        assert config.ollama.model == "qwen3.8:latest"
        assert config.budget.source_tokens == 3_500
        assert config.translation.max_prompt_tokens == 8_000
        assert config.translation.max_num_ctx == 16_384
        assert config.translation.boundary_context == "adjacent-read-only"
        assert not config.translation.thinking
        assert config.audit.model == "gemma4:31b"
        assert config.audit.verifier_model == "gemma4:31b"
        assert config.audit.semantic_max_candidates_per_batch == 1
        assert config.audit.repair_model == "qwen3.8:latest"
        assert config.audit.repair_max_num_ctx == 16_384
        assert config.workflow.production_profile_version == 3
        assert config.workflow.require_final_review
        assert config.workflow.compile_max_unresolved_review_segments == 0

    def test_volume_profiles_reuse_reviewed_series_glossaries(self) -> None:
        """Per-volume profiles disable extraction and pin reviewed glossaries."""
        with tempfile.TemporaryDirectory() as directory:
            for volume in range(1, 4):
                code = f"{volume:02d}"
                path = Path(directory, f"series-{code}.yaml")
                path.write_text(
                    "glossary:\n"
                    "  extraction_enabled: false\n"
                    "  series_glossaries:\n"
                    "    - glossaries/example-series.series.json\n"
                    "  book_glossaries:\n"
                    f"    - glossaries/example-series-{code}.book.reviewed.json\n"
                    "workflow:\n"
                    "  require_glossary_review: false\n"
                    "  require_final_review: true\n",
                    encoding="utf-8",
                )
                config = load_config(path)

                assert not config.glossary.extraction_enabled
                assert [item.name for item in config.glossary.series_glossaries] == ["example-series.series.json"]
                assert [item.name for item in config.glossary.book_glossaries] == [f"example-series-{code}.book.reviewed.json"]
                assert not config.workflow.require_glossary_review
                assert config.workflow.require_final_review

    def test_profile_assigns_custom_style_and_per_role_models(self) -> None:
        """Each stage may name its own model and escalation target."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "roles.yaml")
            path.write_text(
                "translation:\n"
                "  style: custom\n"
                "  custom_style_file: styles/example-ereader-tts.txt\n"
                "audit:\n"
                "  model: qwen3.8:latest\n"
                "  semantic_max_candidates_per_batch: 2\n"
                "  semantic_max_output_tokens: 4096\n"
                "  repair_model: gemma4:31b\n"
                "  quantity:\n"
                "    enabled: true\n"
                "    model: gemma4:26b\n"
                "    escalation_model: qwen3.8:latest\n"
                "reprose:\n"
                "  enabled: true\n"
                "  model: qwen3.8:latest\n"
                "  long_target_characters: 260\n"
                "workflow:\n"
                "  production_profile: example-production\n"
                "  production_profile_version: 4\n",
                encoding="utf-8",
            )
            config = load_config(path)

        assert config.translation.style == TranslationStyle.CUSTOM
        assert config.translation.custom_style_file.name == "example-ereader-tts.txt"
        assert config.audit.model == "qwen3.8:latest"
        assert config.audit.semantic_max_candidates_per_batch == 2
        assert config.audit.semantic_max_output_tokens == 4_096
        assert config.audit.repair_model == "gemma4:31b"
        assert config.audit.quantity.model == "gemma4:26b"
        assert config.audit.quantity.escalation_model == "qwen3.8:latest"
        assert config.reprose.model == "qwen3.8:latest"
        assert config.reprose.long_target_characters == 260
        assert config.workflow.production_profile_version == 4

    def test_profile_restores_semantic_gate_and_narrows_reprose(self) -> None:
        """Reviewed glossaries let a profile narrow reprose to the riskiest text."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "narrowed.yaml")
            path.write_text(
                "glossary:\n"
                "  extraction_enabled: false\n"
                "  seed_glossaries:\n"
                "    - glossary.approved.json\n"
                "  book_glossaries:\n"
                "    - glossaries/example-book.reviewed.v4.json\n"
                "audit:\n"
                "  model: gemma4:31b\n"
                "  verifier_model: gemma4:31b\n"
                "  repair_model: qwen3.8:latest\n"
                "  semantic_max_candidates_per_batch: 1\n"
                "  quantity:\n"
                "    model: gemma4:26b\n"
                "    escalation_model: gemma4:31b\n"
                "reprose:\n"
                "  enabled: true\n"
                "  model: qwen3.8:latest\n"
                "  verifier_model: gemma4:26b\n"
                "  min_target_characters: 100\n"
                "  long_target_characters: 420\n"
                "  max_candidates_per_document: 3\n"
                "workflow:\n"
                "  production_profile: example-production\n"
                "  production_profile_version: 5\n",
                encoding="utf-8",
            )
            config = load_config(path)

        assert not config.glossary.extraction_enabled
        assert [item.name for item in config.glossary.seed_glossaries] == ["glossary.approved.json"]
        assert [item.name for item in config.glossary.book_glossaries] == ["example-book.reviewed.v4.json"]
        assert config.audit.model == "gemma4:31b"
        assert config.audit.verifier_model == "gemma4:31b"
        assert config.audit.repair_model == "qwen3.8:latest"
        assert config.audit.semantic_max_candidates_per_batch == 1
        assert config.audit.quantity.model == "gemma4:26b"
        assert config.audit.quantity.escalation_model == "gemma4:31b"
        assert config.reprose.model == "qwen3.8:latest"
        assert config.reprose.verifier_model == "gemma4:26b"
        assert config.reprose.min_target_characters == 100
        assert config.reprose.long_target_characters == 420
        assert config.reprose.max_candidates_per_document == 3
        assert config.workflow.production_profile_version == 5
