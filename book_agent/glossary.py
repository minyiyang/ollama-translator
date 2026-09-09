"""Deterministic glossary chunking, parsing, merging, and precedence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .epub import ChapterDocument
from .schemas import (
    CATEGORY_ORDER,
    GlossaryCategory,
    GlossaryEntry,
    GlossaryResult,
    deduplicate_entries,
    normalize_term,
)


class GlossaryFormatError(ValueError):
    """A glossary file does not follow the canonical or legacy contract."""


class GlossaryChunkPiece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: str
    document_path: str
    text: str


class GlossaryDocumentDecision(BaseModel):
    """One deterministic decision about glossary-extraction eligibility."""

    model_config = ConfigDict(extra="forbid")
    order: int = Field(ge=0)
    document_path: str
    title: str = ""
    role: str
    reasons: list[str] = Field(default_factory=list)


class GlossaryDocumentScreeningReport(BaseModel):
    """Accounting for EPUB documents removed before any glossary LLM call."""

    model_config = ConfigDict(extra="forbid")
    input_document_count: int = Field(ge=0)
    included_document_count: int = Field(ge=0)
    excluded_document_count: int = Field(ge=0)
    decisions: list[GlossaryDocumentDecision] = Field(default_factory=list)


class GlossaryChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    pieces: list[GlossaryChunkPiece]
    estimated_tokens: int = Field(ge=0)

    def render(self) -> str:
        """Render all evidence pieces with stable reference markers."""
        return "\n\n".join(
            f"<{piece.reference_id}>{piece.text}</{piece.reference_id}>"
            for piece in self.pieces
        )


class GlossaryRejectedCandidate(BaseModel):
    """One candidate removed by a high-confidence deterministic rule."""

    model_config = ConfigDict(extra="forbid")
    english: str
    reason: str
    evidence: list[str] = Field(default_factory=list)


class GlossaryScreeningReport(BaseModel):
    """Machine-readable accounting for deterministic candidate screening."""

    model_config = ConfigDict(extra="forbid")
    input_count: int = Field(ge=0)
    retained_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rejected_reason_counts: dict[str, int] = Field(default_factory=dict)
    suspicious_retained_count: int = Field(default=0, ge=0)
    suspicious_retained_terms: list[str] = Field(default_factory=list)
    rejected: list[GlossaryRejectedCandidate] = Field(default_factory=list)


class GlossaryQualityReport(BaseModel):
    """Cheap quality signals suitable for every glossary review boundary."""

    model_config = ConfigDict(extra="forbid")
    result: str
    scope: str = "volume"
    entry_count: int = Field(ge=0)
    evidence_backed_count: int = Field(ge=0)
    suspicious_generic_count: int = Field(ge=0)
    suspicious_generic_terms: list[str] = Field(default_factory=list)
    category_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class GlossarySourceKind(str, Enum):
    EXTRACTED = "extracted"
    SEED = "seed"
    SERIES = "series"
    BOOK = "book"

    @property
    def priority(self) -> int:
        """Return explicit precedence; larger values override smaller ones."""
        return {
            GlossarySourceKind.EXTRACTED: 0,
            GlossarySourceKind.SEED: 100,
            GlossarySourceKind.SERIES: 200,
            GlossarySourceKind.BOOK: 300,
        }[self]


@dataclass(frozen=True)
class GlossarySource:
    name: str
    kind: GlossarySourceKind
    entries: tuple[GlossaryEntry, ...]


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# These rules intentionally cover only unmistakable ebook boilerplate and a
# small set of ordinary lowercase words. Borderline fictional terms remain in
# the candidate set and are reported for review instead of being deleted.
_PUBLICATION_DOCUMENT_RE = re.compile(
    r"(?:^|[/_.-])(?:"
    r"cover|title(?:page)?|copyright|colophon|toc|contents|navigation|nav|"
    r"acknowledg(?:e)?ments?|dedication|about(?:the)?author|also(?:by)?|"
    r"bibliography|credits?|imprint|newsletter|advertisement|"
    r"discover(?:page)?|personblurb"
    r")(?:$|[/_.-])",
    re.IGNORECASE,
)
_PUBLICATION_TITLES = frozenset(
    {
        "about the author",
        "acknowledgements",
        "acknowledgments",
        "also by",
        "bibliography",
        "colophon",
        "contents",
        "copyright",
        "cover",
        "credits",
        "dedication",
        "discover more",
        "discovery page",
        "extras",
        "imprint",
        "meet the author",
        "newsletter",
        "praise",
        "title page",
    }
)
_PUBLICATION_TITLE_RULES = (
    ("book_preview", re.compile(r"^(?:a\s+)?preview\s+of\b", re.IGNORECASE)),
    ("book_sample", re.compile(r"^(?:a\s+)?sample\s+of\b", re.IGNORECASE)),
)
_PUBLICATION_TEXT_RULES = (
    ("praise", re.compile(r"^praise\s+for\b", re.IGNORECASE)),
    ("also_by", re.compile(r"^also\s+by\b", re.IGNORECASE)),
    ("author_catalog", re.compile(r"^BY\s+[A-Z][A-Z .'-]{2,80}\b")),
    (
        "copyright",
        re.compile(
            r"\b(?:all rights reserved|first published\s+\d{4}|"
            r"isbn(?:-1[03])?\b|cataloguing[- ]in[- ]publication)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "author_biography",
        re.compile(
            r"\bwas born\b.{0,500}\b(?:is the author of|has (?:since )?worked as|works as)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "book_preview",
        re.compile(
            r"^if you enjoyed\b.{0,500}\blook out for\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)
_UNMISTAKABLE_ORDINARY_TERMS = frozenset(
    {
        "air",
        "arm",
        "back",
        "day",
        "door",
        "earth",
        "eye",
        "face",
        "fear",
        "fire",
        "foot",
        "ground",
        "hair",
        "hand",
        "head",
        "heart",
        "home",
        "light",
        "mind",
        "moment",
        "night",
        "room",
        "sky",
        "sound",
        "time",
        "voice",
        "water",
        "way",
        "wind",
        "world",
    }
)
_GENERIC_CATEGORIES = {
    GlossaryCategory.ITEM,
    GlossaryCategory.CONCEPT,
    GlossaryCategory.TERM,
    GlossaryCategory.OTHER,
}


def screen_glossary_documents(
    documents: list[ChapterDocument],
) -> tuple[list[ChapterDocument], GlossaryDocumentScreeningReport]:
    """Exclude high-confidence publication matter before glossary extraction.

    The classifier deliberately keeps summaries, in-book glossaries, appendices,
    and any ambiguous document. It removes only navigation and publisher matter
    that cannot improve prose terminology.
    """
    included: list[ChapterDocument] = []
    decisions: list[GlossaryDocumentDecision] = []
    for document in sorted(documents, key=lambda item: item.order):
        reasons = _publication_document_reasons(document)
        role = "publication" if reasons else "content"
        decisions.append(
            GlossaryDocumentDecision(
                order=document.order,
                document_path=document.archive_path,
                title=document.title,
                role=role,
                reasons=reasons,
            )
        )
        if role == "content":
            included.append(document)
    return included, GlossaryDocumentScreeningReport(
        input_document_count=len(documents),
        included_document_count=len(included),
        excluded_document_count=len(documents) - len(included),
        decisions=decisions,
    )


def _publication_document_reasons(document: ChapterDocument) -> list[str]:
    reasons: list[str] = []
    path = document.archive_path.replace("\\", "/")
    if _PUBLICATION_DOCUMENT_RE.search(path):
        reasons.append("publication_path")
    title = " ".join(document.title.casefold().split())
    if title in _PUBLICATION_TITLES:
        reasons.append(f"publication_title:{title.replace(' ', '_')}")
    for name, pattern in _PUBLICATION_TITLE_RULES:
        if pattern.search(title):
            reasons.append(f"publication_title:{name}")
    text = " ".join(segment.text.strip() for segment in document.segments).strip()
    sample = text[:8_000]
    for name, pattern in _PUBLICATION_TEXT_RULES:
        if pattern.search(sample):
            reasons.append(f"publication_text:{name}")
    # Short early dedications are common, while an epigraph is harmless and is
    # therefore intentionally left in content unless it has this clear form.
    if (
        document.order <= 3
        and 0 < len(text.split()) <= 24
        and re.match(r"^(?:for|to)\s+[A-Z]", text, re.IGNORECASE)
    ):
        reasons.append("publication_text:dedication")
    return sorted(set(reasons))


def screen_glossary_candidates(
    result: GlossaryResult,
    pieces: list[GlossaryChunkPiece] | tuple[GlossaryChunkPiece, ...] = (),
    *,
    remove_ordinary_terms: bool = True,
) -> tuple[GlossaryResult, GlossaryScreeningReport]:
    """Remove only high-confidence noise and report ambiguous generic terms."""
    path_by_evidence = {piece.reference_id: piece.document_path for piece in pieces}
    retained: list[GlossaryEntry] = []
    rejected: list[GlossaryRejectedCandidate] = []
    suspicious: list[str] = []
    for entry in result.entries:
        evidence_paths = [
            path_by_evidence[reference]
            for reference in entry.evidence
            if reference in path_by_evidence
        ]
        reason = ""
        if evidence_paths and all(
            _PUBLICATION_DOCUMENT_RE.search(path.replace("\\", "/"))
            for path in evidence_paths
        ):
            reason = "publication_document_only"
        elif remove_ordinary_terms and _is_unmistakable_ordinary_candidate(entry):
            reason = "ordinary_lowercase_term"
        if reason:
            rejected.append(
                GlossaryRejectedCandidate(
                    english=entry.english,
                    reason=reason,
                    evidence=list(entry.evidence),
                )
            )
            continue
        if is_suspicious_generic_candidate(entry):
            suspicious.append(entry.english)
        retained.append(entry)

    reason_counts = Counter(item.reason for item in rejected)
    screened = GlossaryResult(entries=sort_glossary_entries(retained))
    report = GlossaryScreeningReport(
        input_count=len(result.entries),
        retained_count=len(screened.entries),
        rejected_count=len(rejected),
        rejected_reason_counts=dict(sorted(reason_counts.items())),
        suspicious_retained_count=len(suspicious),
        suspicious_retained_terms=sorted(set(suspicious), key=normalize_term),
        rejected=rejected,
    )
    return screened, report


def analyze_glossary_quality(
    result: GlossaryResult, *, scope: str = "volume"
) -> GlossaryQualityReport:
    """Return deterministic warnings without mutating the supplied glossary."""
    if scope not in {"volume", "series"}:
        raise ValueError("glossary quality scope must be 'volume' or 'series'")
    suspicious = sorted(
        {
            entry.english
            for entry in result.entries
            if is_suspicious_generic_candidate(entry)
        },
        key=normalize_term,
    )
    warnings: list[str] = []
    if suspicious:
        warnings.append(
            f"{len(suspicious)} lowercase single-word generic candidate(s) require review"
        )
    if scope == "volume" and len(result.entries) > 400:
        warnings.append(
            f"oversized glossary draft ({len(result.entries)} entries) requires pruning"
        )
    evidence_backed = sum(bool(entry.evidence) for entry in result.entries)
    if result.entries and evidence_backed < len(result.entries):
        warnings.append(
            f"{len(result.entries) - evidence_backed} entry/entries have no source evidence"
        )
    category_counts = Counter(entry.category.value for entry in result.entries)
    return GlossaryQualityReport(
        result="warning" if warnings else "passed",
        scope=scope,
        entry_count=len(result.entries),
        evidence_backed_count=evidence_backed,
        suspicious_generic_count=len(suspicious),
        suspicious_generic_terms=suspicious,
        category_counts=dict(sorted(category_counts.items())),
        warnings=warnings,
    )


def restore_glossary_evidence(
    result: GlossaryResult,
    candidates: list[GlossaryEntry],
    *,
    max_evidence_per_entry: int = 3,
) -> GlossaryResult:
    """Restore bounded source evidence after a resolver or reviewer response.

    Evidence is provenance, not model-authored prose. The model may select or
    correct a translation, but it must not be able to erase or invent the links
    back to the extraction passages.
    """
    if max_evidence_per_entry <= 0:
        raise ValueError("max_evidence_per_entry must be positive")
    evidence_by_term: dict[str, list[str]] = {}
    for candidate in candidates:
        bucket = evidence_by_term.setdefault(normalize_term(candidate.english), [])
        for reference in candidate.evidence:
            if reference not in bucket:
                bucket.append(reference)
    restored = [
        entry.model_copy(
            update={
                "evidence": evidence_by_term.get(
                    normalize_term(entry.english), list(entry.evidence)
                )[:max_evidence_per_entry]
            }
        )
        for entry in result.entries
    ]
    return GlossaryResult(entries=sort_glossary_entries(restored))


def _is_unmistakable_ordinary_candidate(entry: GlossaryEntry) -> bool:
    english = entry.english.strip()
    return (
        entry.category in _GENERIC_CATEGORIES
        and english == english.lower()
        and normalize_term(english) in _UNMISTAKABLE_ORDINARY_TERMS
    )


def is_suspicious_generic_candidate(entry: GlossaryEntry) -> bool:
    """Return whether a term needs relevance review before series promotion."""
    english = entry.english.strip()
    return (
        entry.category in _GENERIC_CATEGORIES
        and english == english.lower()
        and bool(re.fullmatch(r"[a-z][a-z'-]{1,23}", english))
    )


def estimate_tokens(text: str) -> int:
    """Conservatively estimate mixed English/Chinese model tokens."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(_CJK_RE.sub("", text).encode("utf-8"))
    return cjk + math.ceil(non_cjk / 4)


def split_text_to_budget(text: str, max_tokens: int) -> list[str]:
    """Split oversized text deterministically, preferring whitespace boundaries."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    remaining = text.strip()
    if not remaining:
        return []
    parts: list[str] = []
    while estimate_tokens(remaining) > max_tokens:
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(remaining[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        split_at = low
        whitespace = max(remaining.rfind(" ", 0, split_at), remaining.rfind("\n", 0, split_at))
        if whitespace > max(1, split_at // 2):
            split_at = whitespace
        part = remaining[:split_at].strip()
        if not part:
            part = remaining[:low]
            split_at = low
        parts.append(part)
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def build_glossary_chunks(
    documents: list[ChapterDocument],
    max_tokens: int,
    *,
    max_documents: int = 5,
) -> list[GlossaryChunk]:
    """Batch complete documents up to count/token caps, splitting only oversized text."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if max_documents <= 0:
        raise ValueError("max_documents must be positive")
    chunks: list[GlossaryChunk] = []
    current: list[GlossaryChunkPiece] = []
    current_tokens = 0
    current_documents = 0

    def flush() -> None:
        nonlocal current, current_tokens, current_documents
        if not current:
            return
        chunks.append(
            GlossaryChunk(
                chunk_id=f"glossary-{len(chunks) + 1:05d}",
                pieces=current,
                estimated_tokens=current_tokens,
            )
        )
        current = []
        current_tokens = 0
        current_documents = 0

    for document in sorted(documents, key=lambda item: item.order):
        document_pieces: list[GlossaryChunkPiece] = []
        for segment in document.segments:
            parts = split_text_to_budget(segment.text, max_tokens)
            for part_number, part in enumerate(parts, start=1):
                suffix = f"-P{part_number:03d}" if len(parts) > 1 else ""
                document_pieces.append(
                    GlossaryChunkPiece(
                        reference_id=f"{segment.segment_id}{suffix}",
                        document_path=document.archive_path,
                        text=part,
                    )
                )
        if not document_pieces:
            continue
        document_tokens = sum(estimate_tokens(piece.text) for piece in document_pieces)
        if current and (
            current_documents >= max_documents
            or current_tokens + document_tokens > max_tokens
        ):
            flush()
        if document_tokens <= max_tokens:
            current.extend(document_pieces)
            current_tokens += document_tokens
            current_documents += 1
            if current_documents >= max_documents:
                flush()
            continue
        for piece in document_pieces:
            piece_tokens = estimate_tokens(piece.text)
            if current and current_tokens + piece_tokens > max_tokens:
                flush()
            current.append(piece)
            current_tokens += piece_tokens
            current_documents = 1
    flush()
    return chunks


def validate_candidate_evidence(
    result: GlossaryResult,
    chunk: GlossaryChunk,
) -> None:
    """Require candidate evidence IDs to belong to the supplied chunk."""
    allowed = {piece.reference_id for piece in chunk.pieces}
    for entry in result.entries:
        if not entry.evidence:
            raise GlossaryFormatError(f"candidate has no evidence: {entry.english}")
        unknown = sorted(set(entry.evidence) - allowed)
        if unknown:
            raise GlossaryFormatError(
                f"candidate {entry.english} has unknown evidence: {', '.join(unknown)}"
            )


_EVIDENCE_ID_RE = re.compile(
    r"^D(?P<document>\d+)-S(?P<segment>\d+)(?:-P(?P<part>\d+))?$"
)


def canonicalize_candidate_evidence(
    result: GlossaryResult,
    chunk: GlossaryChunk,
) -> GlossaryResult:
    """Repair zero-padding differences only when they identify a supplied passage."""
    allowed = {piece.reference_id for piece in chunk.pieces}
    canonical_by_number: dict[tuple[int, int, int | None], str] = {}
    for reference_id in allowed:
        match = _EVIDENCE_ID_RE.fullmatch(reference_id)
        if match is None:
            continue
        canonical_by_number[
            (
                int(match.group("document")),
                int(match.group("segment")),
                int(match.group("part")) if match.group("part") is not None else None,
            )
        ] = reference_id

    normalized_entries: list[GlossaryEntry] = []
    for entry in result.entries:
        evidence: list[str] = []
        for reference_id in entry.evidence:
            canonical = reference_id
            if reference_id not in allowed:
                match = _EVIDENCE_ID_RE.fullmatch(reference_id)
                if match is not None:
                    key = (
                        int(match.group("document")),
                        int(match.group("segment")),
                        int(match.group("part")) if match.group("part") is not None else None,
                    )
                    canonical = canonical_by_number.get(key, reference_id)
            if canonical not in evidence:
                evidence.append(canonical)
        normalized_entries.append(entry.model_copy(update={"evidence": evidence}))
    return GlossaryResult(entries=normalized_entries)


def validate_resolution_scope(
    result: GlossaryResult,
    candidates: list[GlossaryEntry],
) -> None:
    """Reject resolver entries whose English term was not present in candidates."""
    allowed = {normalize_term(entry.english) for entry in candidates}
    invented = sorted(
        entry.english
        for entry in result.entries
        if normalize_term(entry.english) not in allowed
    )
    if invented:
        raise GlossaryFormatError(
            f"resolver invented terms outside candidate scope: {', '.join(invented)}"
        )


def build_glossary_resolution_cases(
    entries: list[GlossaryEntry],
) -> list[dict[str, object]]:
    """Group candidates under stable IDs while retaining exact source spellings."""
    groups: dict[str, list[GlossaryEntry]] = {}
    for entry in sort_glossary_entries(entries):
        groups.setdefault(normalize_term(entry.english), []).append(entry)
    cases: list[dict[str, object]] = []
    for index, term in enumerate(sorted(groups), start=1):
        group = groups[term]
        variants: list[dict[str, object]] = []
        for entry in group:
            payload = entry.model_dump(mode="json")
            payload.pop("english")
            variants.append(payload)
        cases.append(
            {
                "term_id": f"T{index:05d}",
                "english": group[0].english,
                "candidates": variants,
            }
        )
    return cases


def build_glossary_approval_cases(
    entries: list[GlossaryEntry],
) -> list[dict[str, object]]:
    """Assign stable IDs to exact resolved entries for delta-only approval."""
    return [
        {
            "term_id": f"A{index:05d}",
            "entry": entry.model_dump(mode="json"),
        }
        for index, entry in enumerate(sort_glossary_entries(entries), start=1)
    ]


def screen_glossary_approval_cases(
    cases: list[dict[str, object]],
    *,
    min_confidence: float = 0.9,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, list[str]]]:
    """Route only uncertain or structurally ambiguous entries to LLM review."""
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between zero and one")
    entries = [GlossaryEntry.model_validate(case["entry"]) for case in cases]
    targets_by_source: dict[str, set[str]] = {}
    sources_by_target: dict[str, set[str]] = {}
    for entry in entries:
        source = normalize_term(entry.english)
        target = normalize_term(entry.chinese)
        targets_by_source.setdefault(source, set()).add(target)
        sources_by_target.setdefault(target, set()).add(source)

    deterministic: list[dict[str, object]] = []
    review: list[dict[str, object]] = []
    reasons_by_id: dict[str, list[str]] = {}
    for case, entry in zip(cases, entries, strict=True):
        reasons: list[str] = []
        source = normalize_term(entry.english)
        target = normalize_term(entry.chinese)
        if not entry.evidence:
            reasons.append("missing_evidence")
        if entry.confidence < min_confidence:
            reasons.append("low_confidence")
        if is_suspicious_generic_candidate(entry):
            reasons.append("suspicious_generic")
        if entry.category == GlossaryCategory.OTHER:
            reasons.append("other_category")
        if len(targets_by_source[source]) > 1:
            reasons.append("source_translation_conflict")
        if len(sources_by_target[target]) > 1:
            reasons.append("target_collision")
        term_id = str(case["term_id"])
        reasons_by_id[term_id] = reasons
        (review if reasons else deterministic).append(case)
    return deterministic, review, reasons_by_id


def build_glossary_approval_batches(
    cases: list[dict[str, object]],
    max_tokens: int,
) -> list[list[dict[str, object]]]:
    """Pack approval cases while keeping source-term alternatives together."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    groups: dict[str, list[dict[str, object]]] = {}
    for case in cases:
        entry = GlossaryEntry.model_validate(case["entry"])
        groups.setdefault(normalize_term(entry.english), []).append(case)
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_tokens = 0
    for term in sorted(groups):
        group = groups[term]
        group_tokens = sum(
            estimate_tokens(json.dumps(case, ensure_ascii=False, separators=(",", ":")))
            for case in group
        )
        if current and current_tokens + group_tokens > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.extend(group)
        current_tokens += group_tokens
    if current:
        batches.append(current)
    return batches


def build_glossary_resolution_batches(
    entries: list[GlossaryEntry],
    max_tokens: int,
) -> list[list[GlossaryEntry]]:
    """Pack entries while keeping every source-term alternative in one batch."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    groups: dict[str, list[GlossaryEntry]] = {}
    for entry in sort_glossary_entries(entries):
        groups.setdefault(normalize_term(entry.english), []).append(entry)
    batches: list[list[GlossaryEntry]] = []
    current: list[GlossaryEntry] = []
    current_tokens = 0
    for term in sorted(groups):
        group = groups[term]
        group_tokens = sum(estimate_tokens(item.model_dump_json()) for item in group)
        if current and current_tokens + group_tokens > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.extend(group)
        current_tokens += group_tokens
    if current:
        batches.append(current)
    return batches


def merge_candidate_entries(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Merge exact bilingual candidates while preserving real translation alternatives."""
    merged: dict[tuple[str, str], GlossaryEntry] = {}
    for entry in entries:
        key = (normalize_term(entry.english), normalize_term(entry.chinese))
        existing = merged.get(key)
        if existing is None:
            merged[key] = entry.model_copy(deep=True)
            continue
        aliases = sorted(set(existing.aliases) | set(entry.aliases), key=normalize_term)
        evidence = sorted(set(existing.evidence) | set(entry.evidence))
        preferred = entry if entry.confidence > existing.confidence else existing
        note = max((existing.note, entry.note), key=lambda value: (len(value), value))
        merged[key] = preferred.model_copy(
            update={"aliases": aliases, "evidence": evidence, "note": note}
        )
    return sort_glossary_entries(list(merged.values()))


def sort_glossary_entries(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """Sort entries by fixed category order, English term, and Chinese alternative."""
    category_rank = {category: index for index, category in enumerate(CATEGORY_ORDER)}
    return sorted(
        deduplicate_entries(entries),
        key=lambda entry: (
            category_rank[entry.category],
            normalize_term(entry.english),
            normalize_term(entry.chinese),
        ),
    )


def parse_legacy_glossary(text: str) -> GlossaryResult:
    """Strictly parse categorized `English:Chinese:note` glossary text."""
    current_category: GlossaryCategory | None = None
    entries: list[GlossaryEntry] = []
    categories = {category.value: category for category in GlossaryCategory}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("--") and line.endswith("--") and len(line) > 4:
            name = line[2:-2].strip()
            if name not in categories:
                raise GlossaryFormatError(f"unknown category on line {line_number}: {name}")
            current_category = categories[name]
            continue
        if current_category is None:
            raise GlossaryFormatError(f"entry appears before category on line {line_number}")
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise GlossaryFormatError(f"invalid glossary entry on line {line_number}")
        try:
            entries.append(
                GlossaryEntry(
                    english=parts[0].strip(),
                    chinese=parts[1].strip(),
                    note=parts[2].strip(),
                    category=current_category,
                )
            )
        except ValueError as error:
            raise GlossaryFormatError(f"invalid glossary entry on line {line_number}: {error}") from error
    return GlossaryResult(entries=sort_glossary_entries(entries))


def load_glossary_file(path: str | Path) -> GlossaryResult:
    """Load canonical JSON or strict legacy glossary text by file suffix."""
    glossary_path = Path(path)
    text = glossary_path.read_text(encoding="utf-8")
    if glossary_path.suffix.casefold() == ".json":
        try:
            result = GlossaryResult.model_validate_json(text)
        except ValueError as error:
            raise GlossaryFormatError(f"invalid glossary JSON: {glossary_path}") from error
        return GlossaryResult(entries=sort_glossary_entries(result.entries))
    return parse_legacy_glossary(text)


def merge_prioritized_sources(sources: list[GlossarySource]) -> list[GlossaryEntry]:
    """Merge sources so book > series > seed > extracted for each English term."""
    selected_priority: dict[str, int] = {}
    selected: dict[str, list[GlossaryEntry]] = {}
    for source in sorted(sources, key=lambda item: (item.kind.priority, item.name)):
        by_term: dict[str, list[GlossaryEntry]] = {}
        for entry in source.entries:
            by_term.setdefault(normalize_term(entry.english), []).append(entry)
        for term, entries in by_term.items():
            priority = source.kind.priority
            if priority > selected_priority.get(term, -1):
                selected_priority[term] = priority
                selected[term] = list(entries)
            elif priority == selected_priority.get(term):
                selected[term].extend(entries)
    return sort_glossary_entries(
        [entry for term_entries in selected.values() for entry in term_entries]
    )
