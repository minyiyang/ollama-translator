"""Book-level consistency detectors (docs/BOOK_CONSISTENCY.md, section 8.2).

Deterministic checks across the whole book, where every other check sees one
document or one chunk at a time:

- **Repeated lines.** A source segment that occurs more than once, and a
  quoted line that recurs inside different segments, should be rendered the
  same way everywhere. Quoted speech is paired with the translation's quotes
  in order when both sides have the same number of them.
- **Conventions.** A single ``—`` where the book uses ``——``, and a straight
  ``"`` in Chinese text.

Each inconsistent occurrence becomes an ``AuditIssue`` (category
``consistency``, source ``consistency``) naming the reference rendering to
follow. The reference is chosen by decision 7.4: a human edit, then the
majority, then on a tie a verified repair, then the first in reading order.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Collection, Iterable, Mapping, Sequence

from .audit import AuditCategory, AuditIssue, AuditSeverity
from .languages import DEFAULT_DIRECTION, UNSPACED_SCRIPT, profile
from .style_sheet import StyleSheet, expression_pattern
from .translation import TranslatedDocument

CONSISTENCY_SOURCE = "consistency"

_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_QUOTE = re.compile(r"“([^”]*)”|「([^」]*)」")
# Punctuation and spacing ignored when comparing two renderings of one line.
_RENDERING_NOISE = re.compile(r"[\s，。！？、；：,.!?;:…—\-“”‘’「」『』\"'（）()]")
_QUOTE_EDGES = " \t,.!?;:—-…"


@dataclass(frozen=True)
class BookSegment:
    """One segment of the current translation, in reading order."""

    document_id: str
    order: int
    segment_id: str
    source: str
    target: str
    edited: bool = False  # an active human edit (Text tab, Final review, XLIFF import)
    repaired: bool = False  # a verified LLM repair


@dataclass(frozen=True)
class ConsistencySettings:
    min_repeat_characters: int = 12
    quoted_speech: bool = True
    conventions: bool = True
    close_variant_similarity: float = 0.6
    target_language: str = DEFAULT_DIRECTION.target_language.value


def book_segments(
    documents: Iterable[TranslatedDocument],
    *,
    human_texts: Mapping[str, str] | None = None,
    repaired_ids: Collection[str] = (),
) -> list[BookSegment]:
    """The book in reading order, with human edits in place of pipeline text."""
    human_texts = human_texts or {}
    return [
        BookSegment(
            document_id=document.manifest_id,
            order=document.order,
            segment_id=segment.segment_id,
            source=segment.source_text,
            target=human_texts.get(segment.segment_id, segment.translated_text),
            edited=segment.segment_id in human_texts,
            repaired=segment.segment_id in repaired_ids,
        )
        for document in sorted(documents, key=lambda item: item.order)
        for segment in document.segments
    ]


def visible_text(text: str) -> str:
    """Text without inline markers, whitespace collapsed."""
    return re.sub(r"\s+", " ", _INLINE_MARKER.sub("", text)).strip()


def source_key(text: str) -> str:
    """A source line normalized for finding repeats."""
    return unicodedata.normalize("NFKC", visible_text(text))


def rendering_key(text: str) -> str:
    """A rendering with punctuation and spacing removed, for comparing two renderings.

    你 and 您 compare equal: English "you" does not decide the register, the
    scene does (decision 7.6), so a 你/您 difference is not drift.
    """
    return _RENDERING_NOISE.sub("", visible_text(text)).casefold().replace("您", "你")


# Lines this short are replies and interjections ("Certainly not.", "Come on,
# then") whose rendering depends on the question or the scene: listed, never
# queued. Four-word refrains ("Off with his head!") still count.
_SHORT_LINE_WORDS = 3
_SHORT_LINE_CHARACTERS = 6  # in a script without spaces


def is_short_line(source: str) -> bool:
    words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", source)
    if words:
        return len(words) <= _SHORT_LINE_WORDS
    return len(UNSPACED_SCRIPT.findall(source)) < _SHORT_LINE_CHARACTERS


def _quotes(text: str) -> list[str]:
    return [a or b for a, b in _QUOTE.findall(visible_text(text))]


def _quote_key(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip(_QUOTE_EDGES).strip()


@dataclass(frozen=True)
class _Occurrence:
    segment: BookSegment
    position: int  # reading order across the book
    kind: str  # "segment" or "quote"
    source: str
    rendering: str

    @property
    def key(self) -> str:
        return rendering_key(self.rendering)


def _occurrences(
    segments: Sequence[BookSegment], settings: ConsistencySettings
) -> dict[tuple[str, str], list[_Occurrence]]:
    """Repeatable units grouped by (kind, normalized source)."""
    groups: dict[tuple[str, str], list[_Occurrence]] = defaultdict(list)
    for position, segment in enumerate(segments):
        key = source_key(segment.source)
        if len(key) >= settings.min_repeat_characters:
            groups[("segment", key)].append(
                _Occurrence(segment, position, "segment", key, visible_text(segment.target))
            )
    repeated_segments = {
        occurrence.segment.segment_id
        for (kind, _), members in groups.items()
        if kind == "segment" and len({m.segment.segment_id for m in members}) > 1
        for occurrence in members
    }
    if settings.quoted_speech:
        for position, segment in enumerate(segments):
            if segment.segment_id in repeated_segments:
                continue  # already compared as a whole segment
            sources, targets = _quotes(segment.source), _quotes(segment.target)
            if not sources or len(sources) != len(targets):
                continue  # cannot pair the quotes reliably
            seen: set[str] = set()
            for source, target in zip(sources, targets):
                key = _quote_key(source)
                if len(key) < settings.min_repeat_characters or key in seen:
                    continue
                seen.add(key)
                groups[("quote", key)].append(
                    _Occurrence(segment, position, "quote", key, target.strip())
                )
    return {
        key: members
        for key, members in groups.items()
        if len({m.segment.segment_id for m in members}) > 1
    }


def _reference(members: list[_Occurrence]) -> tuple[str | None, list[_Occurrence]]:
    """The reference rendering key (decision 7.4), or None and the disagreeing human edits."""
    edited = [m for m in members if m.segment.edited]
    if edited:
        keys = {m.key for m in edited}
        if len(keys) == 1:
            return next(iter(keys)), []
        return None, edited
    counts = Counter(m.key for m in members)
    best = max(counts.values())
    leading = [m for m in members if counts[m.key] == best]
    # A repair breaks a tie only: one repaired occurrence must not turn the
    # occurrences that agree with each other into findings nothing will fix.
    for member in leading:
        if member.segment.repaired:
            return member.key, []
    if leading:  # members are in reading order: first wins a tie
        return leading[0].key, []
    return None, []  # unreachable: members is never empty


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def repeated_line_issues(
    segments: Sequence[BookSegment], settings: ConsistencySettings
) -> list[AuditIssue]:
    """One issue per occurrence of a repeated line whose rendering differs from the reference."""
    issues: list[AuditIssue] = []
    for (kind, source), members in _occurrences(segments, settings).items():
        if len({m.key for m in members}) < 2:
            continue
        label = "Repeated line" if kind == "segment" else "Repeated quoted line"
        reference_key, disagreeing = _reference(members)
        if reference_key is None:
            ids = ", ".join(m.segment.segment_id for m in disagreeing)
            for member in disagreeing:
                issues.append(AuditIssue(
                    segment_id=member.segment.segment_id,
                    category=AuditCategory.CONSISTENCY,
                    severity=AuditSeverity.MEDIUM,
                    message=(
                        f'{label} "{_clip(source)}" has manual edits that disagree '
                        f"({ids}); choose one rendering for all of them."
                    ),
                    source=CONSISTENCY_SOURCE,
                    source_quote=_clip(source, 120),
                    translation_quote=_clip(member.rendering, 120),
                ))
            continue
        reference = [m for m in members if m.key == reference_key]
        first = reference[0]
        reference_ids = ", ".join(m.segment.segment_id for m in reference[:3])
        if len(reference) > 3:
            reference_ids += f" and {len(reference) - 3} more"
        for member in members:
            if member.key == reference_key or member.segment.edited:
                continue
            similarity = SequenceMatcher(None, member.key, reference_key).ratio()
            severity = (
                AuditSeverity.MEDIUM
                if similarity >= settings.close_variant_similarity and not is_short_line(source)
                else AuditSeverity.LOW
            )
            issues.append(AuditIssue(
                segment_id=member.segment.segment_id,
                category=AuditCategory.CONSISTENCY,
                severity=severity,
                message=(
                    f'{label} "{_clip(source)}" is rendered "{_clip(member.rendering)}" here '
                    f'but "{_clip(first.rendering)}" in {reference_ids}.'
                ),
                suggested_fix=(
                    f"Render it as in {first.segment.segment_id}: {first.rendering}"
                    if kind == "quote"
                    else f"Use the rendering from {first.segment.segment_id}: {first.rendering}"
                ),
                source=CONSISTENCY_SOURCE,
                source_quote=_clip(source, 120),
                translation_quote=_clip(member.rendering, 120),
            ))
    return issues


def convention_issues(
    segments: Sequence[BookSegment], settings: ConsistencySettings
) -> list[AuditIssue]:
    """Punctuation that departs from the book's own majority convention (the target profile's rules)."""
    rules = profile(settings.target_language)
    if not rules.conventions:
        return []
    # Spacing is part of a convention (a no-break space before French ; : ! ?),
    # so only ordinary whitespace is collapsed here.
    written = [
        (segment, re.sub(r"[ \t\r\n]+", " ", _INLINE_MARKER.sub("", segment.target)).strip())
        for segment in segments
        if rules.script_pattern.search(segment.target)
    ]
    issues: list[AuditIssue] = []
    for rule in rules.conventions:
        house, slip = re.compile(rule.house), re.compile(rule.slip)
        following = sum(1 for _, text in written if house.search(text))
        departures = [(segment, text) for segment, text in written if slip.search(text)]
        if not departures or following < len(departures):
            continue
        for segment, text in departures:
            issues.append(AuditIssue(
                segment_id=segment.segment_id,
                category=AuditCategory.CONSISTENCY,
                severity=AuditSeverity.MEDIUM,
                message=rule.message.format(count=following),
                suggested_fix=rule.fix,
                source=CONSISTENCY_SOURCE,
                translation_quote=_clip(text, 120),
            ))
    return issues


def book_consistency_issues(
    segments: Sequence[BookSegment],
    settings: ConsistencySettings,
    style: StyleSheet | None = None,
) -> list[AuditIssue]:
    """Every consistency issue in the book, in reading order.

    Conventions and, with an approved style sheet, its expressions are checked
    on unedited segments only; a human edit is checked when it is proposed
    instead, since repair works on the pipeline text, not the edit.
    """
    issues = repeated_line_issues(segments, settings)
    if settings.conventions:
        edited = {segment.segment_id for segment in segments if segment.edited}
        issues += [
            issue for issue in convention_issues(segments, settings)
            if issue.segment_id not in edited
        ]
    if style is not None:
        issues += expression_issues(
            ((s.segment_id, s.source, s.target) for s in segments if not s.edited), style
        )
    position = {segment.segment_id: index for index, segment in enumerate(segments)}
    return sorted(issues, key=lambda issue: (position.get(issue.segment_id, 0), issue.message))


def issues_for(
    issues: Sequence[AuditIssue], segment_ids: set[str]
) -> list[AuditIssue]:
    return [issue for issue in issues if issue.segment_id in segment_ids]


def expression_issues(
    segments: Iterable[tuple[str, str, str]], sheet: StyleSheet
) -> list[AuditIssue]:
    """(segment id, source, translation) triples whose source has an approved expression
    but whose translation lacks its rendering."""
    if not sheet.expressions:
        return []
    patterns = [(expression_pattern(item.source), item) for item in sheet.expressions]
    issues = []
    for segment_id, source, target in segments:
        folded = unicodedata.normalize("NFKC", visible_text(source)).casefold()
        visible_target = visible_text(target)
        for pattern, item in patterns:
            if pattern.search(folded) and item.rendering not in visible_target:
                issues.append(AuditIssue(
                    segment_id=segment_id,
                    category=AuditCategory.CONSISTENCY,
                    # Listed, not queued: the style sheet prevents drift in the prompt,
                    # and repeated_line_issues enforces agreement between the actual
                    # renderings. Exact wording varies legitimately with the scene
                    # (decision 7.6); on The Wind in the Willows a medium severity
                    # queued 24 such segments for review, none of them real drift.
                    severity=AuditSeverity.LOW,
                    message=(
                        f'The style sheet renders "{item.source}" as "{item.rendering}", '
                        "but this translation does not use it."
                    ),
                    suggested_fix=f'Render "{item.source}" as "{item.rendering}".',
                    source=CONSISTENCY_SOURCE,
                    source_quote=item.source[:120],
                ))
    return issues
