"""Validated project configuration."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .languages import TranslationDirection
from .styles import TranslationStyle
from .token_budget import TokenBudget, validate_budget


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OllamaConfig(StrictModel):
    host: str = "http://localhost:11434"
    model: str = "qwen3.8:latest"
    num_ctx: int = Field(default=131_072, gt=0)
    adaptive_num_ctx: bool = True
    min_num_ctx: int = Field(default=16_384, gt=0)
    model_num_ctx_caps: dict[str, int] = Field(default_factory=dict)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=1800, gt=0)
    keep_alive: str = "30m"
    max_retries: int = Field(default=3, ge=0)
    retry_initial_seconds: float = Field(default=0.5, ge=0.0)
    retry_max_seconds: float = Field(default=8.0, ge=0.0)
    progress_interval_seconds: float = Field(default=30.0, ge=0.0)
    progress_interval_tokens: int = Field(default=2_000, ge=0)
    # Legacy fallback. Token-based throttling takes precedence when enabled.
    progress_interval_chars: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_progress_defaults(cls, value):
        """Upgrade resolved workspaces that captured the former 2s/2K-char defaults."""
        if not isinstance(value, dict) or "progress_interval_tokens" in value:
            return value
        if (
            value.get("progress_interval_seconds") == 2.0
            and value.get("progress_interval_chars") == 2_000
        ):
            upgraded = dict(value)
            upgraded["progress_interval_seconds"] = 30.0
            upgraded["progress_interval_tokens"] = 2_000
            upgraded["progress_interval_chars"] = 0
            return upgraded
        return value

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "OllamaConfig":
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds cannot be less than retry_initial_seconds")
        if self.min_num_ctx > self.num_ctx:
            raise ValueError("min_num_ctx cannot exceed num_ctx")
        for model_name, context_cap in self.model_num_ctx_caps.items():
            if not model_name.strip():
                raise ValueError("model_num_ctx_caps keys cannot be empty")
            if context_cap <= 0:
                raise ValueError("model_num_ctx_caps values must be positive")
            if context_cap > self.num_ctx:
                raise ValueError("model_num_ctx_caps values cannot exceed num_ctx")
        return self


class BudgetConfig(StrictModel):
    working_limit: int = 65_536
    prompt_tokens: int = 10_000
    glossary_tokens: int = 5_000
    source_tokens: int = 20_000
    expected_output_tokens: int = 24_000
    reserve_tokens: int = 5_000

    def as_token_budget(self) -> TokenBudget:
        """Convert configuration to the domain token-budget value object."""
        return TokenBudget(**self.model_dump())


class TranslationConfig(StrictModel):
    direction: TranslationDirection = TranslationDirection.EN_TO_ZH
    style: TranslationStyle = TranslationStyle.LITERARY
    custom_style_file: Path | None = None
    preserve_paragraphs: bool = True
    preserve_punctuation: bool = True
    use_glossary_strictly: bool = True
    de_ai_enabled: bool = True
    de_ai_strength: Literal["conservative", "moderate"] = "conservative"
    thinking: bool = False
    marker_examples: Literal["none", "one-shot", "few-shot"] = "few-shot"
    fallback_models: list[str] = Field(default_factory=list)
    attempts_per_model: int = Field(default=2, gt=0)
    harmonize_fallback_with_primary: bool = True
    max_num_ctx: int = Field(default=65_536, gt=0)
    max_prompt_tokens: int = Field(default=20_000, gt=0)
    context_multiplier: float = Field(default=3.0, ge=1.0, le=8.0)
    boundary_context: Literal["none", "adjacent-read-only"] = "none"

    @model_validator(mode="after")
    def validate_custom_style(self) -> "TranslationConfig":
        if self.style is TranslationStyle.CUSTOM and self.custom_style_file is None:
            raise ValueError("custom_style_file is required for the custom style")
        if self.style is not TranslationStyle.CUSTOM and self.custom_style_file is not None:
            raise ValueError("custom_style_file can only be used with the custom style")
        if any(not model.strip() for model in self.fallback_models):
            raise ValueError("fallback_models cannot contain an empty model name")
        return self


class GlossaryConfig(StrictModel):
    extraction_enabled: bool = True
    extraction_model: str = Field(default="qwen3.8:27b", min_length=1)
    extraction_thinking: bool = False
    extraction_min_num_ctx: int = Field(default=16_384, gt=0)
    extraction_max_num_ctx: int = Field(default=32_768, gt=0)
    extraction_max_entries: int = Field(default=120, ge=1, le=1_000)
    extraction_max_evidence_per_entry: int = Field(default=3, ge=1, le=20)
    extraction_chapters_per_chunk: int = Field(default=8, ge=1, le=10)
    extraction_chunk_tokens: int = Field(default=9_000, gt=0, le=100_000)
    extraction_context_multiplier: float = Field(default=2.0, ge=1.0, le=8.0)
    resolution_chunk_tokens: int = Field(default=8_000, gt=0, le=50_000)
    resolution_min_num_ctx: int = Field(default=16_384, gt=0)
    resolution_max_num_ctx: int = Field(default=32_768, gt=0)
    resolution_context_multiplier: float = Field(default=2.0, ge=1.0, le=8.0)
    approval_chunk_tokens: int = Field(default=8_000, gt=0, le=50_000)
    approval_min_num_ctx: int = Field(default=16_384, gt=0)
    approval_max_num_ctx: int = Field(default=32_768, gt=0)
    approval_context_multiplier: float = Field(default=2.0, ge=1.0, le=8.0)
    approval_auto_approve_min_confidence: float = Field(
        default=0.9, ge=0.0, le=1.0
    )
    seed_glossaries: list[Path] = Field(default_factory=list)
    series_glossaries: list[Path] = Field(default_factory=list)
    book_glossaries: list[Path] = Field(default_factory=list)


class PreprocessingConfig(StrictModel):
    mode: Literal["annotate", "replace"] = "annotate"
    conflict_policy: Literal["skip", "error"] = "skip"


class EpubConfig(StrictModel):
    strip_print_page_markers: bool = False
    insert_missing_chapter_headings: bool = False
    chapter_heading_labels: list[str] = Field(default_factory=list)


class QuantityAuditConfig(StrictModel):
    """Confidence-aware quantity checking within the translation audit stage."""

    enabled: bool = False
    mode: Literal["shadow", "advisory", "enforce"] = "shadow"
    model: str = Field(default="gemma4:26b", min_length=1)
    escalation_model: str | None = None
    thinking: bool = False
    max_num_ctx: int = Field(default=16_384, gt=0)
    min_decision_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    batch_size: Literal[1] = 1
    verify_repairs: bool = True

    @model_validator(mode="after")
    def validate_models(self) -> "QuantityAuditConfig":
        if self.escalation_model is not None and not self.escalation_model.strip():
            raise ValueError("quantity escalation_model cannot be empty")
        return self


class AuditConfig(StrictModel):
    model: str = Field(default="gemma4:31b", min_length=1)
    verifier_model: str | None = None
    verifier_max_num_ctx: int | None = Field(default=None, gt=0)
    repair_model: str | None = None
    semantic_verification_policy: Literal[
        "autonomous", "human-high-risk"
    ] = "autonomous"
    thinking: bool = False
    semantic_enabled: bool = True
    semantic_min_source_tokens: int = Field(default=180, gt=0)
    semantic_batch_source_tokens: int = Field(default=8_000, gt=0)
    semantic_max_candidates_per_batch: int = Field(default=50, gt=0)
    semantic_max_output_tokens: int = Field(default=3_072, ge=512, le=8_192)
    semantic_max_num_ctx: int | None = Field(default=None, gt=0)
    semantic_sample_every: int = Field(default=0, ge=0)
    max_semantic_candidates_per_document: int = Field(default=50, gt=0)
    min_length_ratio: float = Field(default=0.20, gt=0.0)
    max_length_ratio: float = Field(default=4.0, gt=0.0)
    untranslated_min_words: int = Field(default=3, gt=0)
    repair_min_severity: Literal["low", "medium", "high"] = "medium"
    repair_min_num_ctx: int = Field(default=16_384, gt=0)
    repair_max_num_ctx: int | None = Field(default=None, gt=0)
    repair_context_multiplier: float = Field(default=1.0, ge=1.0, le=8.0)
    repair_max_attempts: int = Field(default=2, ge=1, le=2)
    max_num_ctx: int = Field(default=65_536, gt=0)
    quantity: QuantityAuditConfig = Field(default_factory=QuantityAuditConfig)

    @model_validator(mode="after")
    def validate_length_ratios(self) -> "AuditConfig":
        if self.max_length_ratio <= self.min_length_ratio:
            raise ValueError("max_length_ratio must exceed min_length_ratio")
        if self.verifier_model is not None and not self.verifier_model.strip():
            raise ValueError("verifier_model cannot be empty")
        if self.repair_model is not None and not self.repair_model.strip():
            raise ValueError("repair_model cannot be empty")
        if (
            self.semantic_max_num_ctx is not None
            and self.semantic_max_num_ctx > self.max_num_ctx
        ):
            raise ValueError("semantic_max_num_ctx cannot exceed max_num_ctx")
        if (
            self.verifier_max_num_ctx is not None
            and self.verifier_max_num_ctx > self.max_num_ctx
        ):
            raise ValueError("verifier_max_num_ctx cannot exceed max_num_ctx")
        if (
            self.repair_max_num_ctx is not None
            and self.repair_max_num_ctx > self.max_num_ctx
        ):
            raise ValueError("repair_max_num_ctx cannot exceed max_num_ctx")
        if (
            self.repair_max_num_ctx is not None
            and self.repair_min_num_ctx > self.repair_max_num_ctx
        ):
            raise ValueError(
                "repair_min_num_ctx cannot exceed repair_max_num_ctx"
            )
        if self.quantity.max_num_ctx > self.max_num_ctx:
            raise ValueError("quantity max_num_ctx cannot exceed audit max_num_ctx")
        return self


class ProseRewriteConfig(StrictModel):
    """Conservative source-grounded literary rewrite after semantic repair."""

    enabled: bool = False
    model: str = Field(default="gemma4:26b", min_length=1)
    thinking: bool = False
    verifier_model: str = Field(default="gemma4:31b", min_length=1)
    verifier_thinking: bool = False
    verifier_max_num_ctx: int = Field(default=16_384, gt=0)
    min_num_ctx: int = Field(default=16_384, gt=0)
    max_num_ctx: int = Field(default=16_384, gt=0)
    batch_size: int = Field(default=4, ge=1, le=12)
    min_target_characters: int = Field(default=40, ge=1)
    max_candidates_per_document: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=3)
    candidate_mode: Literal["all-prose", "risk-filtered"] = "all-prose"
    long_target_characters: int = Field(default=100, ge=40)
    candidate_sample_every: int = Field(default=0, ge=0)
    preserve_semantic_signatures: bool = True

    @model_validator(mode="after")
    def validate_context_range(self) -> "ProseRewriteConfig":
        if self.min_num_ctx > self.max_num_ctx:
            raise ValueError("reprose min_num_ctx cannot exceed max_num_ctx")
        if self.verifier_max_num_ctx < self.min_num_ctx:
            raise ValueError(
                "reprose verifier_max_num_ctx cannot be below min_num_ctx"
            )
        return self


class WorkflowConfig(StrictModel):
    production_profile: str = ""
    production_profile_version: int = Field(default=0, ge=0)
    enforce_human_high_risk: bool = False
    max_retries: int = Field(default=3, ge=0)
    defer_failed_translation_segments: bool = True
    require_glossary_review: bool = True
    llm_glossary_review: bool = False
    require_final_review: bool = False
    compile_max_unresolved_review_segments: int = Field(default=0, ge=0)
    overwrite: bool = False


class PathsConfig(StrictModel):
    runs: Path = Path("runs")
    prompts: Path = Path("prompts")


class AppConfig(StrictModel):
    ollama: OllamaConfig = OllamaConfig()
    budget: BudgetConfig = BudgetConfig()
    translation: TranslationConfig = TranslationConfig()
    glossary: GlossaryConfig = GlossaryConfig()
    preprocessing: PreprocessingConfig = PreprocessingConfig()
    epub: EpubConfig = EpubConfig()
    audit: AuditConfig = AuditConfig()
    reprose: ProseRewriteConfig = ProseRewriteConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    paths: PathsConfig = PathsConfig()

    @model_validator(mode="after")
    def validate_context_budget(self) -> "AppConfig":
        validate_budget(self.budget.as_token_budget(), self.ollama.num_ctx)
        if (
            self.workflow.enforce_human_high_risk
            and self.audit.semantic_verification_policy != "human-high-risk"
        ):
            raise ValueError(
                "workflow.enforce_human_high_risk requires "
                "audit.semantic_verification_policy=human-high-risk"
            )
        if self.workflow.production_profile and not self.workflow.production_profile_version:
            raise ValueError(
                "workflow.production_profile_version must be positive when a "
                "production_profile is named"
            )
        if not self.glossary.extraction_enabled and not (
            self.glossary.seed_glossaries
            or self.glossary.series_glossaries
            or self.glossary.book_glossaries
        ):
            raise ValueError(
                "glossary extraction can only be disabled when at least one "
                "configured glossary is supplied"
            )
        if self.glossary.extraction_min_num_ctx > self.ollama.num_ctx:
            raise ValueError("glossary.extraction_min_num_ctx cannot exceed ollama.num_ctx")
        if self.glossary.extraction_max_num_ctx > self.ollama.num_ctx:
            raise ValueError("glossary.extraction_max_num_ctx cannot exceed ollama.num_ctx")
        if (
            self.glossary.extraction_min_num_ctx
            > self.glossary.extraction_max_num_ctx
        ):
            raise ValueError(
                "glossary.extraction_min_num_ctx cannot exceed "
                "glossary.extraction_max_num_ctx"
            )
        if self.glossary.resolution_max_num_ctx > self.ollama.num_ctx:
            raise ValueError("glossary.resolution_max_num_ctx cannot exceed ollama.num_ctx")
        if (
            self.glossary.resolution_min_num_ctx
            > self.glossary.resolution_max_num_ctx
        ):
            raise ValueError(
                "glossary.resolution_min_num_ctx cannot exceed "
                "glossary.resolution_max_num_ctx"
            )
        if self.reprose.max_num_ctx > self.ollama.num_ctx:
            raise ValueError("reprose max_num_ctx cannot exceed ollama.num_ctx")
        if self.reprose.verifier_max_num_ctx > self.ollama.num_ctx:
            raise ValueError(
                "reprose verifier_max_num_ctx cannot exceed ollama.num_ctx"
            )
        if self.glossary.approval_max_num_ctx > self.ollama.num_ctx:
            raise ValueError("glossary.approval_max_num_ctx cannot exceed ollama.num_ctx")
        if (
            self.glossary.approval_min_num_ctx
            > self.glossary.approval_max_num_ctx
        ):
            raise ValueError(
                "glossary.approval_min_num_ctx cannot exceed "
                "glossary.approval_max_num_ctx"
            )
        if self.audit.repair_min_num_ctx > self.ollama.num_ctx:
            raise ValueError("audit.repair_min_num_ctx cannot exceed ollama.num_ctx")
        if self.audit.max_num_ctx > self.ollama.num_ctx:
            raise ValueError("audit.max_num_ctx cannot exceed ollama.num_ctx")
        if self.audit.repair_min_num_ctx > self.audit.max_num_ctx:
            raise ValueError("audit.repair_min_num_ctx cannot exceed audit.max_num_ctx")
        if self.translation.max_num_ctx > self.ollama.num_ctx:
            raise ValueError("translation.max_num_ctx cannot exceed ollama.num_ctx")
        if self.translation.max_num_ctx < self.ollama.min_num_ctx:
            raise ValueError("translation.max_num_ctx cannot be below ollama.min_num_ctx")
        if self.translation.max_prompt_tokens >= self.translation.max_num_ctx:
            raise ValueError(
                "translation.max_prompt_tokens must be below translation.max_num_ctx"
            )
        translation_fixed_budget = (
            self.budget.prompt_tokens
            + self.budget.glossary_tokens
            + self.budget.expected_output_tokens
            + self.budget.reserve_tokens
        )
        if translation_fixed_budget >= self.translation.max_num_ctx:
            raise ValueError(
                "translation.max_num_ctx must leave room beyond the configured "
                "prompt, glossary, output, and reserve budgets"
            )
        return self


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a YAML configuration file."""
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return AppConfig.model_validate(raw)
