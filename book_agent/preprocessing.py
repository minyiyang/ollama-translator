"""Direction-aware deterministic glossary preprocessing and selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .languages import (
    Language,
    LanguagePair,
    TranslationDirection,
    glossary_sides,
    profile,
    source_aliases,
)
from .schemas import DEFAULT_GLOSSARY_PAIR, GlossaryEntry, glossary_pair_scope, normalize_term
from .style_sheet import StyleSheet


class GlossaryReplacementConflict(ValueError):
    """One source term maps to multiple target translations."""


@dataclass(frozen=True)
class ReplacementRule:
    source: str
    target: str
    canonical_term: str
    source_language: Language

    @property
    def normalized_source(self) -> str:
        """Return the comparison key appropriate for the source language."""
        return profile(self.source_language).normalize_term(self.source)


@dataclass(frozen=True)
class ReplacementIndex:
    direction: TranslationDirection
    rules: tuple[ReplacementRule, ...]
    conflicts: dict[str, tuple[str, ...]]


class ReplacementOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    # The glossary entry's source term (the English term in an en-zh glossary).
    canonical_term: str
    count: int = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def read_pre_phase3_name(cls, data: Any) -> Any:
        if isinstance(data, dict) and "canonical_english" in data:
            data = {("canonical_term" if key == "canonical_english" else key): value for key, value in data.items()}
        return data


class PreprocessedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    original_text: str
    processed_text: str
    occurrences: list[ReplacementOccurrence] = Field(default_factory=list)


class PreprocessedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    manifest_id: str
    archive_path: str
    source_sha256: str
    segments: list[PreprocessedSegment]
    # The pair of the glossary the relevant entries come from (an en-zh glossary
    # in an en-zh or zh-en job); documents written before phase 3 lack it.
    glossary_pair: LanguagePair = DEFAULT_GLOSSARY_PAIR
    relevant_glossary: list[GlossaryEntry] = Field(default_factory=list)
    # Style-sheet entries relevant to this document (docs/BOOK_CONSISTENCY.md, phase 2).
    relevant_style: StyleSheet | None = None
    # "Story so far" for this document (docs/BOOK_CONSISTENCY.md, phase 3).
    story_context: str | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _validate_entries_for_pair(cls, data: Any, handler: Any) -> Any:
        if not isinstance(data, dict):
            return handler(data)
        with glossary_pair_scope(LanguagePair(data.get("glossary_pair") or DEFAULT_GLOSSARY_PAIR)):
            return handler(data)

    @model_serializer(mode="wrap")
    def _omit_absent_style(self, handler):
        # Without a style sheet the file stays byte-identical to before the
        # field existed, so preprocessing reruns do not retranslate the book.
        data = handler(self)
        if isinstance(data, dict):
            for key in ("relevant_style", "story_context"):
                if data.get(key) is None:
                    data.pop(key, None)
        return data


class PreprocessingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: TranslationDirection
    mode: str
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    replacement_count: int = Field(ge=0)
    ambiguous_terms: dict[str, list[str]] = Field(default_factory=dict)
    effective_glossary_entry_count: int = Field(default=0, ge=0)
    volume_active_glossary_entry_count: int = Field(default=0, ge=0)
    document_active_glossary_entry_counts: dict[str, int] = Field(
        default_factory=dict
    )


_PROTECTED_TAG = re.compile(r"(<[^>]+>)")


def build_replacement_index(
    entries: list[GlossaryEntry],
    direction: TranslationDirection,
    *,
    conflict_policy: str = "skip",
) -> ReplacementIndex:
    """Build longest-first unambiguous replacement rules for one direction."""
    if conflict_policy not in {"skip", "error"}:
        raise ValueError("conflict_policy must be 'skip' or 'error'")
    candidates: dict[str, list[ReplacementRule]] = {}
    display_source: dict[str, str] = {}
    language = direction.source_language
    for entry in entries:
        source_term, target = glossary_sides(entry, direction)
        source_terms = [source_term, *source_aliases(entry, direction)]
        for source in source_terms:
            source = source.strip()
            if not source:
                continue
            key = profile(language).normalize_term(source)
            display_source.setdefault(key, source)
            candidates.setdefault(key, []).append(
                ReplacementRule(source, target, entry.source, language)
            )

    conflicts: dict[str, tuple[str, ...]] = {}
    rules: list[ReplacementRule] = []
    for key, possible in candidates.items():
        targets = tuple(sorted({rule.target for rule in possible}, key=normalize_term))
        if len(targets) > 1:
            conflicts[display_source[key]] = targets
            continue
        preferred = sorted(
            possible,
            key=lambda rule: (
                -len(rule.source),
                normalize_term(rule.canonical_term),
                rule.source,
            ),
        )[0]
        rules.append(preferred)
    if conflicts and conflict_policy == "error":
        details = "; ".join(
            f"{source} => {', '.join(targets)}" for source, targets in sorted(conflicts.items())
        )
        raise GlossaryReplacementConflict(f"ambiguous glossary replacements: {details}")
    rules.sort(key=lambda rule: (-len(rule.source), rule.normalized_source, rule.target))
    return ReplacementIndex(direction, tuple(rules), dict(sorted(conflicts.items())))


def apply_replacement_index(
    text: str,
    index: ReplacementIndex,
) -> tuple[str, list[ReplacementOccurrence]]:
    """Apply rules outside protected markup and return deterministic occurrence counts."""
    pieces = _PROTECTED_TAG.split(text)
    counts: dict[tuple[str, str, str], int] = {}
    for position in range(0, len(pieces), 2):
        content = pieces[position]
        for rule in index.rules:
            pattern = _rule_pattern(rule)

            def replace(match: re.Match[str]) -> str:
                key = (rule.source, rule.target, rule.canonical_term)
                counts[key] = counts.get(key, 0) + 1
                return rule.target

            content = pattern.sub(replace, content)
        pieces[position] = content
    occurrences = [
        ReplacementOccurrence(
            source=source,
            target=target,
            canonical_term=canonical,
            count=count,
        )
        for (source, target, canonical), count in sorted(
            counts.items(), key=lambda item: (-len(item[0][0]), normalize_term(item[0][0]))
        )
    ]
    return "".join(pieces), occurrences


def select_relevant_glossary_entries(
    text: str,
    entries: list[GlossaryEntry],
    direction: TranslationDirection,
) -> list[GlossaryEntry]:
    """Select unambiguous longest source-term matches from the supplied text.

    Annotation is conservative: case-bearing English glossary terms must match
    source case. The longest approved source term owns an overlapping occurrence,
    so a generic suffix such as ``clade`` cannot compete with ``Spider-clade``.
    An independent occurrence of the shorter term remains eligible.
    """
    visible = _PROTECTED_TAG.sub("", text)
    matches: dict[tuple[int, int, str], set[int]] = {}
    language = direction.source_language
    for entry_index, entry in enumerate(entries):
        terms = [glossary_sides(entry, direction)[0], *source_aliases(entry, direction)]
        for term in terms:
            if not term:
                continue
            pattern = _selection_pattern(term, language)
            for match in pattern.finditer(visible):
                key = (
                    match.start(),
                    match.end(),
                    profile(language).normalize_term(match.group()),
                )
                matches.setdefault(key, set()).add(entry_index)

    selected_indices: set[int] = set()
    occupied: list[tuple[int, int]] = []
    ordered_matches = sorted(
        matches,
        key=lambda item: (-(item[1] - item[0]), item[0], item[2]),
    )
    for match in ordered_matches:
        start, end, normalized_source = match
        if any(
            start < occupied_end and occupied_start < end
            for occupied_start, occupied_end in occupied
        ):
            continue
        selected_indices.update(matches[match])
        occupied.append((start, end))

    selected = [entries[index] for index in selected_indices]
    return sorted(
        selected,
        key=lambda entry: (normalize_term(entry.source), normalize_term(entry.target)),
    )


def preprocess_segment(
    segment_id: str,
    text: str,
    index: ReplacementIndex,
) -> PreprocessedSegment:
    """Preprocess one stable source segment."""
    processed, occurrences = apply_replacement_index(text, index)
    return PreprocessedSegment(
        segment_id=segment_id,
        original_text=text,
        processed_text=processed,
        occurrences=occurrences,
    )


def annotate_segment(segment_id: str, text: str) -> PreprocessedSegment:
    """Preserve source text exactly while preparing it for glossary-aware translation."""
    return PreprocessedSegment(
        segment_id=segment_id,
        original_text=text,
        processed_text=text,
        occurrences=[],
    )


def render_preprocessed_document(document: PreprocessedDocument) -> str:
    """Render preprocessed text with unchanged protected segment IDs."""
    return "\n\n".join(
        f"<{segment.segment_id}>{segment.processed_text}</{segment.segment_id}>"
        for segment in document.segments
    ) + ("\n" if document.segments else "")


def _rule_pattern(rule: ReplacementRule) -> re.Pattern[str]:
    escaped = re.escape(rule.source)
    language = profile(rule.source_language)
    if language.spaced_words:
        return re.compile(
            rf"(?<![{language.word_chars}]){escaped}{language.term_end}",
            flags=re.IGNORECASE if language.cased else 0,
        )
    return re.compile(escaped)


def _selection_pattern(source: str, language: Language) -> re.Pattern[str]:
    """Match glossary annotations conservatively without changing replace mode."""
    def escape(text: str) -> str:
        return "".join(
            "['\u2018\u2019]"
            if character in "'\u2018\u2019"
            else "[-\u2010\u2011]"
            if character in "-\u2010\u2011"
            else re.escape(character)
            for character in text
        )

    escaped = escape(source)
    rules = profile(language)
    if rules.spaced_words:
        case_sensitive = not rules.cased or any(character.isupper() for character in source)
        # A term is written in prose as often in the plural as the singular
        # (``binders`` for ``binder``).  Without this the entry annotates the
        # singular only, and every plural occurrence silently escapes both the
        # translation prompt and the glossary audit.
        # Not on an abbreviation: "Mr" + "s" is "Mrs", another word.
        if rules.inflection_suffix:
            # Every word of the term inflects (des Weißen Hasen, los Conejos Blancos).
            term = rules.inflected([escape(word) for word in source.split()], rules.plural_suffix)
        else:
            term = escaped + (rules.plural_suffix if re.search(r"[^\W\d_]{3}$", source) else "")
        return re.compile(
            rf"(?<![{rules.word_chars}]){term}{rules.term_end}",
            flags=0 if case_sensitive else re.IGNORECASE,
        )
    return re.compile(escaped)


def _targets_are_incompatible(
    longer: list[GlossaryEntry],
    shorter: list[GlossaryEntry],
    direction: TranslationDirection,
) -> bool:
    """Return whether nested source mappings cannot both hold in one target phrase."""
    longer_targets = {glossary_sides(entry, direction)[1] for entry in longer}
    shorter_targets = {glossary_sides(entry, direction)[1] for entry in shorter}
    return not any(
        normalize_term(short_target) in normalize_term(long_target)
        for long_target in longer_targets
        for short_target in shorter_targets
    )
