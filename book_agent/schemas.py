"""Canonical glossary schemas and legacy-format rendering."""

import re
from collections import defaultdict
from enum import Enum
from typing import Literal

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
)

from .languages import LanguagePair, glossary_names, profile


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
# The scripts of the model-facing `english` and `chinese` fields of an en-zh glossary.
_CJK_RE = profile("zh").script_pattern
_LATIN_RE = profile("en").script_pattern
# A glossary written before phase 3 has no pair: it is an en-zh glossary.
DEFAULT_GLOSSARY_PAIR = LanguagePair("en-zh")
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


# The glossary pair of the entries being validated, set by a container that
# knows it (a GlossaryResult, a preprocessed document) around its nested entries.
_ENTRY_PAIR: ContextVar[LanguagePair | None] = ContextVar("glossary_entry_pair", default=None)


@contextmanager
def glossary_pair_scope(pair: LanguagePair) -> Iterator[None]:
    """Validate the entries inside this block as entries of a `pair` glossary."""
    token = _ENTRY_PAIR.set(LanguagePair(pair))
    try:
        yield
    finally:
        _ENTRY_PAIR.reset(token)


def _context_pair(info: ValidationInfo | None) -> LanguagePair:
    context = info.context if info is not None else None
    pair = context.get("pair") if isinstance(context, dict) else None
    if pair is not None:
        return LanguagePair(pair)
    return _ENTRY_PAIR.get() or DEFAULT_GLOSSARY_PAIR


def _from_pair_names(data: Any, pair: LanguagePair) -> Any:
    """Read an entry written with the pair's own side names (`english`/`chinese`)."""
    if not isinstance(data, dict):
        return data
    names = glossary_names(pair)
    if names == ("source", "target") or not any(name in data for name in names):
        if "english" in data or "chinese" in data:
            raise ValueError(
                f"english/chinese entries belong to an en-zh glossary; this glossary is {pair.value}"
            )
        return data
    renamed = {}
    for key, value in data.items():
        renamed[{names[0]: "source", names[1]: "target"}.get(key, key)] = value
    return renamed


def validate_entry_terms(source: str, target: str, pair: LanguagePair) -> None:
    """A term must be written in its language's script; the target may instead
    repeat a preserved code exactly (a model designation, an acronym)."""
    source_name, target_name = glossary_names(pair)
    source_rules = profile(pair.source_language)
    target_rules = profile(pair.target_language)
    if not source_rules.script_pattern.search(source):
        script = source_rules.script or "letter"
        raise ValueError(f"{source_name} term must contain a {script} letter")
    if target_rules.script_pattern.search(target):
        return
    if target == source and is_preservable_technical_identifier(target):
        return
    if target_rules.scripts == ("Han",):
        script = "CJK"
    elif len(target_rules.scripts) == 1:
        script = target_rules.script
    else:
        script = target_rules.short_name if target_rules.scripts else "letter"
    raise ValueError(
        f"{target_name} term must contain a {script} character unless it is the exact same "
        f"technical code, model designation, or uppercase acronym as the "
        f"{source_rules.short_name} term"
    )


class GlossaryEntry(BaseModel):
    """A source term and its fixed target rendering.

    The sides belong to the glossary's pair (`GlossaryResult.pair`; an en/zh
    glossary is en-zh, keyed by English). Validation reads that pair from the
    validation context, defaulting to en-zh; entries written before phase 3
    name the sides `english`/`chinese` and are read as en-zh.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    # Variants of the source term.
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def read_pair_names(cls, data: Any, info: ValidationInfo) -> Any:
        return _from_pair_names(data, _context_pair(info))

    @field_validator("source", "target")
    @classmethod
    def validate_term(cls, value: str) -> str:
        return _validate_legacy_term(value)

    @model_validator(mode="after")
    def validate_scripts(self, info: ValidationInfo) -> "GlossaryEntry":
        validate_entry_terms(self.source, self.target, _context_pair(info))
        return self

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("note cannot contain line breaks")
        return value

    @classmethod
    def for_pair(cls, data: Any, pair: LanguagePair) -> "GlossaryEntry":
        """Validate an entry of a `pair` glossary (written with either side names)."""
        return cls.model_validate(data, context={"pair": pair})


def entry_for_model(entry: GlossaryEntry, pair: LanguagePair) -> dict[str, Any]:
    """An entry as the model sees it: `english`/`chinese` in an en-zh glossary."""
    source_name, target_name = glossary_names(pair)
    data = entry.model_dump(mode="json")
    return {
        {"source": source_name, "target": target_name}.get(key, key): value
        for key, value in data.items()
    }


class GlossaryResult(BaseModel):
    """A glossary: its pair and its entries."""

    model_config = ConfigDict(extra="forbid")
    pair: LanguagePair = DEFAULT_GLOSSARY_PAIR
    entries: list[GlossaryEntry] = Field(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def validate_entries_for_pair(cls, data: Any, handler: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return handler(data)
        pair = LanguagePair(data["pair"]) if data.get("pair") is not None else _context_pair(info)
        with glossary_pair_scope(pair):
            return handler({**data, "pair": pair})


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


    @property
    def target(self) -> str:
        """The resolved target term (named `chinese` for the model in an en-zh glossary)."""
        return self.chinese


class GlossaryTargetResolutionDecision(BaseModel):
    """A resolver decision in a glossary other than en-zh, whose sides are source/target."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    term_id: str = Field(pattern=r"^T\d{5}$")
    target: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
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
    pair: LanguagePair = DEFAULT_GLOSSARY_PAIR,
) -> type[BaseModel]:
    """Build a structured-output schema restricted to pipeline-owned term IDs."""
    if not allowed_term_ids:
        raise ValueError("allowed_term_ids cannot be empty")
    if max_decisions <= 0:
        raise ValueError("max_decisions must be positive")
    term_id_value = Literal.__getitem__(tuple(dict.fromkeys(allowed_term_ids)))
    decision_schema = create_model(
        f"GlossaryResolutionDecisionN{len(allowed_term_ids)}",
        __base__=GlossaryResolutionDecision if pair.legacy else GlossaryTargetResolutionDecision,
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

    @property
    def target(self) -> str | None:
        """The revised target term (named `chinese` for the model in an en-zh glossary)."""
        return self.chinese


class GlossaryTargetApprovalDecision(BaseModel):
    """An approval delta in a glossary other than en-zh, whose sides are source/target."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    term_id: str = Field(pattern=r"^A\d{5}$")
    action: GlossaryApprovalAction
    target: str | None = None
    note: str | None = None
    category: GlossaryCategory | None = None
    aliases: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("target")
    @classmethod
    def validate_optional_target(cls, value: str | None) -> str | None:
        return _validate_legacy_term(value) if value is not None else None

    @field_validator("note", "reason")
    @classmethod
    def validate_single_line(cls, value: str | None) -> str | None:
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError("approval text cannot contain line breaks")
        return value

    @model_validator(mode="after")
    def validate_revision(self) -> "GlossaryTargetApprovalDecision":
        if self.action == GlossaryApprovalAction.REVISE and self.target is None:
            raise ValueError("revise decisions must provide target")
        return self


class GlossaryApprovalResult(BaseModel):
    """Complete decision set for only the entries selected for LLM review."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[GlossaryApprovalDecision] = Field(default_factory=list)


class GlossaryTargetApprovalResult(BaseModel):
    """Approval decisions in a glossary other than en-zh."""

    model_config = ConfigDict(extra="forbid")
    decisions: list[GlossaryTargetApprovalDecision] = Field(default_factory=list)


def approval_result_type(pair: LanguagePair) -> type[BaseModel]:
    """How stored approval decisions of a `pair` glossary are read."""
    return GlossaryApprovalResult if pair.legacy else GlossaryTargetApprovalResult


def build_glossary_approval_schema(
    allowed_term_ids: list[str],
    pair: LanguagePair = DEFAULT_GLOSSARY_PAIR,
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
        __base__=GlossaryApprovalDecision if pair.legacy else GlossaryTargetApprovalDecision,
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
    source: str
    result: Literal["approved", "rejected", "revised", "pending", "failed"]
    mode: Literal["deterministic", "llm"]
    reasons: list[str] = Field(default_factory=list)
    target: str | None = None

    @model_validator(mode="before")
    @classmethod
    def read_pre_phase3_names(cls, data: Any) -> Any:
        if isinstance(data, dict) and "english" in data:
            data = dict(data)
            data["source"] = data.pop("english")
            if "chinese" in data:
                data["target"] = data.pop("chinese")
        return data


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


class GlossaryExtractionTerm(BaseModel):
    """An extraction candidate in a glossary other than en-zh, whose sides are source/target.

    Script rules are checked afterwards, per candidate, by GlossaryEntry.for_pair.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    note: str = ""
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("source", "target")
    @classmethod
    def validate_term(cls, value: str) -> str:
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
    pair: LanguagePair = DEFAULT_GLOSSARY_PAIR,
) -> type[BaseModel]:
    """Build a structured-output schema using the configured extraction bounds."""
    evidence_value = (
        str
        if allowed_evidence_ids is None
        else Literal.__getitem__(tuple(dict.fromkeys(allowed_evidence_ids)))
    )
    entry_schema = create_model(
        f"GlossaryExtractionEntryE{max_evidence_per_entry}",
        __base__=GlossaryExtractionCandidate if pair.legacy else GlossaryExtractionTerm,
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


# Categories whose terms are ordinary words rather than names, so a lowercase
# spelling is the useful one to keep when case variants collapse.
_COMMON_NOUN_CATEGORIES = frozenset(
    {
        GlossaryCategory.ITEM,
        GlossaryCategory.TECHNOLOGY,
        GlossaryCategory.CONCEPT,
        GlossaryCategory.TERM,
        GlossaryCategory.OTHER,
    }
)


def deduplicate_entries(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Remove exact term/translation duplicates while retaining alternatives.

    Case variants of one term collapse to a single entry.  For a common noun keep
    the all-lowercase spelling when the glossary carries one: annotation matches a
    lowercase term case-insensitively, so it covers the capitalised occurrences as
    well, whereas a capitalised entry never matches the lowercase prose form.
    Names keep their capitalisation, which is what stops ``Hood`` from annotating
    the hood of a car.
    """
    result: list[GlossaryEntry] = []
    index: dict[tuple[str, str], int] = {}
    for entry in entries:
        key = (normalize_term(entry.source), normalize_term(entry.target))
        position = index.get(key)
        if position is None:
            index[key] = len(result)
            result.append(entry)
        elif (
            entry.source.islower()
            and not result[position].source.islower()
            and entry.category in _COMMON_NOUN_CATEGORIES
        ):
            result[position] = entry
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
            key=lambda entry: (normalize_term(entry.source), normalize_term(entry.target))
        )
        lines = [f"--{category.value}--"]
        lines.extend(
            f"{entry.source}:{entry.target}:{entry.note}" for entry in category_entries
        )
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")
