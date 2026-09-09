"""Canonical glossary schemas and legacy-format rendering."""

import re
from collections import defaultdict
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator, model_validator


class GlossaryCategory(str, Enum):
    PERSON = "人名"
    PLACE = "地名"
    ORGANIZATION = "组织"
    ITEM = "物品"
    TECHNOLOGY = "技术"
    CONCEPT = "概念"
    TERM = "术语"
    OTHER = "其他"


CATEGORY_ORDER = tuple(GlossaryCategory)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_PRESERVED_CODE_RE = re.compile(
    r"^(?=.{1,48}$)(?:"
    r"(?=.*(?:\d|[._+/#-]))[A-Za-z0-9][A-Za-z0-9._+/#-]*"
    r"|[A-Z]{2,12}"
    r"|(?=(?:.*[A-Z]){2,})[A-Za-z]{2,12}"
    r")$"
)


def is_preservable_technical_identifier(value: str) -> bool:
    """Return whether an exact Latin identifier may remain unchanged in Chinese prose."""
    return bool(_PRESERVED_CODE_RE.fullmatch(value.strip()))


class GlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    english: str = Field(min_length=1)
    chinese: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("english")
    @classmethod
    def validate_english(cls, value: str) -> str:
        if not _LATIN_RE.search(value):
            raise ValueError("english term must contain a Latin letter")
        return _validate_legacy_term(value)

    @field_validator("chinese")
    @classmethod
    def validate_chinese(cls, value: str) -> str:
        return _validate_legacy_term(value)

    @model_validator(mode="after")
    def validate_chinese_or_preserved_code(self) -> "GlossaryEntry":
        if _CJK_RE.search(self.chinese):
            return self
        if self.chinese == self.english and is_preservable_technical_identifier(
            self.chinese
        ):
            return self
        raise ValueError(
            "chinese term must contain a CJK character unless it is the exact same "
            "technical code, model designation, or uppercase acronym as the English term"
        )

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("note cannot contain line breaks")
        return value


class GlossaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[GlossaryEntry] = Field(default_factory=list)


class GlossaryResolutionDecision(BaseModel):
    """One resolver decision keyed by a pipeline-owned source-term ID."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    term_id: str = Field(pattern=r"^T\d{5}$")
    chinese: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("chinese")
    @classmethod
    def validate_chinese(cls, value: str) -> str:
        # The exact English source term is restored after generation, so the
        # final GlossaryEntry performs the CJK/preserved-code validation.
        return _validate_legacy_term(value)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("note cannot contain line breaks")
        return value


class GlossaryResolutionResult(BaseModel):
    """Bounded resolver output that cannot rewrite an English source term."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[GlossaryResolutionDecision] = Field(default_factory=list)


def build_glossary_resolution_schema(
    allowed_term_ids: list[str],
    max_decisions: int,
) -> type[BaseModel]:
    """Build a structured-output schema restricted to pipeline-owned term IDs."""
    if not allowed_term_ids:
        raise ValueError("allowed_term_ids cannot be empty")
    if max_decisions <= 0:
        raise ValueError("max_decisions must be positive")
    term_id_value = Literal.__getitem__(tuple(dict.fromkeys(allowed_term_ids)))
    decision_schema = create_model(
        f"GlossaryResolutionDecisionN{len(allowed_term_ids)}",
        __base__=GlossaryResolutionDecision,
        term_id=(term_id_value, ...),
    )
    return create_model(
        f"GlossaryResolutionResultN{max_decisions}",
        __config__=ConfigDict(extra="forbid"),
        decisions=(
            list[decision_schema],
            Field(default_factory=list, max_length=max_decisions),
        ),
    )


class GlossaryApprovalAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"
    PENDING = "pending"


class GlossaryApprovalDecision(BaseModel):
    """One approval delta keyed by a pipeline-owned glossary-entry ID."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    term_id: str = Field(pattern=r"^A\d{5}$")
    action: GlossaryApprovalAction
    chinese: str | None = None
    note: str | None = None
    category: GlossaryCategory | None = None
    aliases: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("chinese")
    @classmethod
    def validate_optional_chinese(cls, value: str | None) -> str | None:
        return _validate_legacy_term(value) if value is not None else None

    @field_validator("note", "reason")
    @classmethod
    def validate_single_line(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("approval text cannot contain line breaks")
        return value

    @model_validator(mode="after")
    def validate_revision(self) -> "GlossaryApprovalDecision":
        if self.action == GlossaryApprovalAction.REVISE and self.chinese is None:
            raise ValueError("revise decisions must provide chinese")
        return self


class GlossaryApprovalResult(BaseModel):
    """Complete decision set for only the entries selected for LLM review."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[GlossaryApprovalDecision] = Field(default_factory=list)


def build_glossary_approval_schema(
    allowed_term_ids: list[str],
) -> type[BaseModel]:
    """Build an exact-size approval schema restricted to selected entry IDs."""
    if not allowed_term_ids:
        raise ValueError("allowed_term_ids cannot be empty")
    unique_ids = list(dict.fromkeys(allowed_term_ids))
    if len(unique_ids) != len(allowed_term_ids):
        raise ValueError("allowed_term_ids must be unique")
    term_id_value = Literal.__getitem__(tuple(unique_ids))
    decision_schema = create_model(
        f"GlossaryApprovalDecisionN{len(unique_ids)}",
        __base__=GlossaryApprovalDecision,
        term_id=(term_id_value, ...),
    )
    return create_model(
        f"GlossaryApprovalResultN{len(unique_ids)}",
        __config__=ConfigDict(extra="forbid"),
        decisions=(
            list[decision_schema],
            Field(min_length=len(unique_ids), max_length=len(unique_ids)),
        ),
    )


class GlossaryApprovalRecord(BaseModel):
    """Per-entry outcome emitted by automatic glossary approval."""

    model_config = ConfigDict(extra="forbid")
    term_id: str = Field(pattern=r"^A\d{5}$")
    english: str
    result: Literal["approved", "rejected", "revised", "pending", "failed"]
    mode: Literal["deterministic", "llm"]
    reasons: list[str] = Field(default_factory=list)
    chinese: str | None = None


class GlossaryApprovalReport(BaseModel):
    """Machine-readable accounting for every automatic approval entry."""

    model_config = ConfigDict(extra="forbid")
    result: Literal["approved", "pending", "failed"]
    input_count: int = Field(ge=0)
    approved_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    revised_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    deterministic_count: int = Field(ge=0)
    llm_count: int = Field(ge=0)
    records: list[GlossaryApprovalRecord] = Field(default_factory=list)


class GlossaryExtractionCandidate(BaseModel):
    """A provisional candidate whose translation is not yet publication-ready."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    english: str = Field(min_length=1)
    chinese: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("english")
    @classmethod
    def validate_english(cls, value: str) -> str:
        if not _LATIN_RE.search(value):
            raise ValueError("english term must contain a Latin letter")
        return _validate_legacy_term(value)

    @field_validator("chinese")
    @classmethod
    def validate_chinese(cls, value: str) -> str:
        # Extraction is recall-oriented. Final Chinese/code policy is applied to
        # each candidate independently after the structured response is parsed.
        return _validate_legacy_term(value)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("note cannot contain line breaks")
        return value


def build_glossary_extraction_schema(
    max_entries: int,
    max_evidence_per_entry: int,
    allowed_evidence_ids: list[str] | None = None,
) -> type[BaseModel]:
    """Build a structured-output schema using the configured extraction bounds."""
    evidence_value = (
        str
        if allowed_evidence_ids is None
        else Literal.__getitem__(tuple(dict.fromkeys(allowed_evidence_ids)))
    )
    entry_schema = create_model(
        f"GlossaryExtractionEntryE{max_evidence_per_entry}",
        __base__=GlossaryExtractionCandidate,
        evidence=(
            list[evidence_value],
            Field(min_length=1, max_length=max_evidence_per_entry),
        ),
    )
    return create_model(
        f"GlossaryExtractionResultN{max_entries}E{max_evidence_per_entry}",
        __config__=ConfigDict(extra="forbid"),
        entries=(
            list[entry_schema],
            Field(default_factory=list, max_length=max_entries),
        ),
    )


def _validate_legacy_term(value: str) -> str:
    if any(character in value for character in "():：\n\r"):
        raise ValueError("glossary terms cannot contain parentheses, colons, or line breaks")
    return value


def normalize_term(value: str) -> str:
    """Normalize a term for case-insensitive duplicate comparison."""
    return " ".join(value.casefold().split())


def deduplicate_entries(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Remove exact term/translation duplicates while retaining alternatives."""
    result: list[GlossaryEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (normalize_term(entry.english), normalize_term(entry.chinese))
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def render_legacy_glossary(entries: list[GlossaryEntry]) -> str:
    """Render validated entries in categorized colon-separated legacy format."""
    grouped: dict[GlossaryCategory, list[GlossaryEntry]] = defaultdict(list)
    for entry in deduplicate_entries(entries):
        grouped[entry.category].append(entry)

    sections: list[str] = []
    for category in CATEGORY_ORDER:
        category_entries = grouped.get(category, [])
        if not category_entries:
            continue
        category_entries.sort(
            key=lambda entry: (normalize_term(entry.english), normalize_term(entry.chinese))
        )
        lines = [f"--{category.value}--"]
        lines.extend(
            f"{entry.english}:{entry.chinese}:{entry.note}" for entry in category_entries
        )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")
