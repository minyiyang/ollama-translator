"""Book-level consistency measurement (docs/BOOK_CONSISTENCY.md, phase 0).

A read-only report over a job's current translation: repeated source lines
rendered differently, book-wide glossary misses, recurring names without a
glossary entry, and, for English to Chinese, pronouns and forms of address per
character and punctuation conventions. It makes no model calls and writes
nothing into the job folder; its job is to show which consistency gaps matter
on a real book before any of the later phases are built.

The heuristics are deliberately simple and are signals, not verdicts; the
limits of each section are listed in the proposal (section 6.1).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from .config import AppConfig
from .glossary import find_unglossed_proper_nouns
from .languages import (
    ANY_SCRIPT,
    SENTENCE_END,
    UNSPACED_SCRIPT,
    LanguagePair,
    glossary_pair,
    glossary_sides,
    profile,
)
from .pipeline_state import WorkflowStage
from .schemas import GlossaryCategory, GlossaryEntry
from .state import StageStatus, connect_state, get_stage_status
from .text_edits import active_edit_texts, overlay_active_edits
from .workspace import JobWorkspace

_INLINE_MARKER = re.compile(r"</?I\d{3}>")
# Han characters, for the Chinese-target character and convention reports below.
_CJK = profile("zh").script_pattern
_LATIN = re.compile(r"[A-Za-z]")
_QUOTED = re.compile(r"“([^”]*)”|「([^」]*)」")

# Singular third-person pronouns; plurals (他们) and 其他 ("other") are not about one character.
_TARGET_PRONOUN = re.compile(r"(?<!其)([他她它])(?!们)")
_SOURCE_PRONOUNS = {
    "he": re.compile(r"\b(?:he|him|his|himself)\b", re.IGNORECASE),
    "she": re.compile(r"\b(?:she|her|hers|herself)\b", re.IGNORECASE),
    "it": re.compile(r"\b(?:it|its|itself)\b", re.IGNORECASE),
}
_TARGET_PRONOUN_LABEL = {"他": "he", "她": "she", "它": "it"}
_INFORMAL_YOU = re.compile(r"你(?!们)")
_FORMAL_YOU = re.compile(r"您(?!们)")

# Convention styles counted per segment, grouped by the convention they belong to.
_CONVENTIONS: dict[str, dict[str, re.Pattern[str]]] = {
    "quotation marks": {
        "“ ” curly double": re.compile(r"[“”]"),
        "「 」 corner brackets": re.compile(r"[「」]"),
        '" straight ASCII': re.compile(r'"'),
    },
    # Counted for information only: ‘ ’ is the normal nested quote inside “ ”.
    "nested quotation marks": {
        "‘ ’ curly single": re.compile(r"[‘’]"),
        "『 』 double corner": re.compile(r"[『』]"),
    },
    "ellipsis": {
        "…… paired": re.compile(r"……"),
        "… single": re.compile(r"(?<!…)…(?!…)"),
        "... ASCII dots": re.compile(r"\.\.\.|。。。"),
    },
    "dash": {
        "—— paired": re.compile(r"——"),
        "— single": re.compile(r"(?<!—)—(?!—)"),
        "-- ASCII": re.compile(r"--"),
    },
    "numerals": {
        "Arabic digits": re.compile(r"[0-9０-９]"),
    },
}

# A minority usage at or above this share, with enough occurrences, is reported as mixed.
_MIXED_SHARE = 0.2
_MIXED_MIN_TOTAL = 5


@dataclass(frozen=True)
class ReportSegment:
    document_id: str
    order: int
    segment_id: str
    source: str
    target: str
    edited: bool = False


def visible_text(text: str) -> str:
    """Text without inline markers, whitespace collapsed."""
    return re.sub(r"\s+", " ", _INLINE_MARKER.sub("", text)).strip()


def _source_key(text: str) -> str:
    return unicodedata.normalize("NFKC", visible_text(text))


def _target_key(text: str) -> str:
    # Scripts without spaces between words lose them; spaced targets keep single spaces.
    visible = visible_text(text)
    return visible.replace(" ", "") if UNSPACED_SCRIPT.search(visible) else visible


def _is_mixed(counts: Counter[str] | dict[str, int]) -> bool:
    """Whether usages other than the most common one make up a real share."""
    total = sum(counts.values())
    if total < _MIXED_MIN_TOTAL:
        return False
    return (total - max(counts.values())) / total >= _MIXED_SHARE


# -- loading ------------------------------------------------------------------------


def load_report_segments(workspace: JobWorkspace) -> tuple[list[ReportSegment], str]:
    """The current translation in reading order, and a description of where it came from."""
    from .stages.repair import load_repaired_documents
    from .stages.reprose import load_reprosed_documents
    from .stages.translate import load_translated_documents
    from .stages.validate_repaired import load_validated_repaired_documents

    connection = connect_state(workspace.state_file)
    try:
        validate = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
    finally:
        connection.close()
    if validate and validate["status"] == StageStatus.COMPLETED.value:
        active = active_edit_texts(workspace)
        repaired = overlay_active_edits(load_validated_repaired_documents(workspace), active)
        documents = [item.document for item in repaired]
        basis = f"validated draft with {len(active)} active manual edit(s) applied"
    else:
        active = {}
        documents = []
        basis = ""
        for loader, label in (
            (load_reprosed_documents, "prose-rewrite output"),
            (load_repaired_documents, "repair output"),
        ):
            try:
                documents = [item.document for item in loader(workspace)]
            except (FileNotFoundError, RuntimeError, ValueError):
                documents = []
            if documents:
                basis = f"{label} (validate_repaired has not completed)"
                break
        if not documents:
            documents = load_translated_documents(workspace)
            basis = "first-draft translation (validate_repaired has not completed)"
    segments = [
        ReportSegment(
            document_id=document.manifest_id,
            order=document.order,
            segment_id=segment.segment_id,
            source=segment.source_text,
            target=segment.translated_text,
            edited=segment.segment_id in active,
        )
        for document in sorted(documents, key=lambda item: item.order)
        for segment in document.segments
    ]
    return segments, basis


# -- analyses ---------------------------------------------------------------------


def repeated_lines(
    segments: Sequence[ReportSegment], *, min_chars: int = 12
) -> dict[str, Any]:
    """Source segments that occur more than once, and how their renderings differ."""
    groups: dict[str, list[ReportSegment]] = defaultdict(list)
    for segment in segments:
        key = _source_key(segment.source)
        if len(key) >= min_chars:
            groups[key].append(segment)
    repeated = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        variants = Counter(_target_key(member.target) for member in members)
        repeated.append({
            "source": key,
            "occurrences": len(members),
            "variant_count": len(variants),
            "renderings": [
                {
                    "segment_id": member.segment_id,
                    "document_id": member.document_id,
                    "text": visible_text(member.target),
                    "edited": member.edited,
                }
                for member in members
            ],
        })
    repeated.sort(key=lambda item: (item["variant_count"] == 1, -item["occurrences"], item["source"]))
    inconsistent = [item for item in repeated if item["variant_count"] > 1]
    return {
        "min_chars": min_chars,
        "repeated_count": len(repeated),
        "inconsistent_count": len(inconsistent),
        "segments_in_inconsistent": sum(item["occurrences"] for item in inconsistent),
        "groups": repeated,
    }


_SENTENCE_SPLIT = re.compile(rf"(?<=[{SENTENCE_END}])[”’\"」]?\s*|[“”「」]")
_SPEECH_TAG = re.compile(
    r"\b(?:said|says|thought|asked|cried|replied|added|went on|continued|remarked|"
    r"exclaimed|whispered|shouted|repeated|muttered|to (?:her|him)self)\b",
    re.IGNORECASE,
)


def repeated_sentences(
    segments: Sequence[ReportSegment], *, min_chars: int = 12, examples: int = 5
) -> dict[str, Any]:
    """Source sentences repeated across different segments, speech tags left out.

    Source side only: without sentence alignment the renderings cannot be
    compared yet, so this measures how much a sentence-level translation
    memory or a style sheet's recurring expressions would have to cover.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for segment in segments:
        seen: set[str] = set()
        for sentence in _SENTENCE_SPLIT.split(_source_key(segment.source)):
            sentence = sentence.strip(" \"'‘’—-,;:()")
            if (
                len(sentence) < min_chars
                or sentence in seen
                or _SPEECH_TAG.search(sentence)
                or not ANY_SCRIPT.search(sentence)  # "* * * *"
            ):
                continue
            seen.add(sentence)
            groups[sentence].append(segment.segment_id)
    repeated = sorted(
        (
            {"sentence": sentence, "segments": len(ids), "segment_ids": ids[:examples]}
            for sentence, ids in groups.items()
            if len(ids) > 1
        ),
        key=lambda item: (-item["segments"], item["sentence"]),
    )
    return {
        "min_chars": min_chars,
        "repeated_count": len(repeated),
        "segments_involved": len({i for item in repeated for i in groups[item["sentence"]]}),
        "sentences": repeated,
    }


def _term_pattern(term: str) -> re.Pattern[str]:
    flags = 0 if any(character.isupper() for character in term) else re.IGNORECASE
    if _LATIN.search(term):
        return re.compile(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", flags)
    return re.compile(re.escape(term))


@dataclass(frozen=True)
class _Term:
    index: int
    source_terms: tuple[str, ...]
    target_terms: tuple[str, ...]


def _terms(entries: Sequence[GlossaryEntry], direction: LanguagePair) -> list[_Term]:
    """The job's source and target names of each entry. Aliases are variants of
    the glossary's source term, which is the job's target in a swapped glossary
    (the English side of an en-zh glossary in a zh-en job)."""
    swapped = glossary_pair(direction) != direction
    aliases_side = profile(direction.target_language if swapped else direction.source_language)
    terms = []
    for index, entry in enumerate(entries):
        source, target = glossary_sides(entry, direction)
        aliases = tuple(alias for alias in entry.aliases if aliases_side.script_pattern.search(alias))
        if swapped:
            terms.append(_Term(index, (source,), (target, *aliases)))
        else:
            terms.append(_Term(index, (source, *aliases), (target,)))
    return terms


_SENTENCE_START = re.compile(r"(?:^|[.!?;:]\s+|[“\"‘'(—]\s*)$")


def _ambiguous_capitals(segments: Sequence[ReportSegment], terms: Sequence[_Term]) -> set[str]:
    """Single-word capitalized terms that the book also uses as ordinary lowercase words ("Two")."""
    words = {
        word
        for segment in segments
        for word in re.findall(r"\b[a-z]+\b", visible_text(segment.source))
    }
    return {
        source
        for term in terms
        for source in term.source_terms
        if re.fullmatch(r"[A-Z][a-z]+", source) and source.lower() in words
    }


def _source_matches(
    text: str,
    terms: Sequence[_Term],
    patterns: dict[str, re.Pattern[str]],
    ambiguous: set[str] | frozenset[str] = frozenset(),
) -> set[int]:
    """Entries whose source term occurs, ignoring matches inside a longer matched term.

    An ambiguous capitalized word at the start of a sentence is skipped: "Two
    days wrong!" is not the playing card called Two.
    """
    spans: list[tuple[int, int, int]] = []
    for term in terms:
        for source in term.source_terms:
            for match in patterns[source].finditer(text):
                if source in ambiguous and _SENTENCE_START.search(text[: match.start()]):
                    continue
                spans.append((match.start(), match.end(), term.index))
    kept: set[int] = set()
    for start, end, index in spans:
        inside_longer = any(
            other_start <= start and end <= other_end and (other_end - other_start) > (end - start)
            for other_start, other_end, _ in spans
        )
        if not inside_longer:
            kept.add(index)
    return kept


def glossary_compliance(
    segments: Sequence[ReportSegment],
    entries: Sequence[GlossaryEntry],
    *,
    source_language: str = "en",
    target_language: str = "zh",
    examples: int = 5,
) -> dict[str, Any]:
    """Per glossary entry, source occurrences whose translation lacks the approved rendering."""
    direction = LanguagePair.of(source_language, target_language)
    terms = _terms(entries, direction)
    patterns = {source: _term_pattern(source) for term in terms for source in term.source_terms}
    ambiguous = _ambiguous_capitals(segments, terms) if profile(source_language).cased else set()
    occurrences: Counter[int] = Counter()
    misses: dict[int, list[dict[str, str]]] = defaultdict(list)
    for segment in segments:
        source = visible_text(segment.source)
        target = visible_text(segment.target)
        for index in _source_matches(source, terms, patterns, ambiguous):
            occurrences[index] += 1
            term = terms[index]
            if profile(target_language).cased:
                present = any(t.casefold() in target.casefold() for t in term.target_terms)
            else:
                present = any(t in target for t in term.target_terms)
            if not present:
                misses[index].append({
                    "segment_id": segment.segment_id,
                    "source": source,
                    "text": target,
                })
    rows = []
    for index, missed in misses.items():
        source_term, target_term = glossary_sides(entries[index], direction)
        rows.append({
            "source": source_term,
            "target": target_term,
            "category": entries[index].category.name.lower(),
            "occurrences": occurrences[index],
            "misses": len(missed),
            "examples": missed[:examples],
        })
    rows.sort(key=lambda row: (-row["misses"], row["source"]))
    return {
        "entries_checked": len(entries),
        "entries_seen": len(occurrences),
        "entries_with_misses": len(rows),
        "miss_count": sum(row["misses"] for row in rows),
        "entries": rows,
    }


@dataclass(frozen=True)
class _Character:
    name: str
    source_names: tuple[str, ...]
    target_names: tuple[str, ...]


def _characters(entries: Sequence[GlossaryEntry]) -> list[_Character]:
    """Person entries, merged when they share a Chinese rendering (Queen / The Queen).

    Runs for Chinese targets, whose glossary always runs into Chinese (en-zh, or
    another source into zh), so an entry's target is the Chinese name.
    """
    by_target: dict[str, list[GlossaryEntry]] = defaultdict(list)
    for entry in entries:
        if entry.category is GlossaryCategory.PERSON:
            by_target[entry.target].append(entry)
    characters = []
    for target, group in by_target.items():
        names = sorted({entry.source for entry in group}, key=len)
        aliases = {alias for entry in group for alias in entry.aliases}
        characters.append(_Character(
            name=" / ".join(names),
            source_names=tuple(names) + tuple(a for a in aliases if not _CJK.search(a)),
            target_names=(target, *sorted(a for a in aliases if _CJK.search(a))),
        ))
    return characters


def _named_characters(target: str, characters: Sequence[_Character]) -> set[str]:
    """Characters named in a Chinese text, ignoring a name inside a longer matched name."""
    spans = []
    for character in characters:
        for name in character.target_names:
            for match in re.finditer(re.escape(name), target):
                spans.append((match.start(), match.end(), character.name))
    return {
        name
        for start, end, name in spans
        if not any(
            s <= start and end <= e and (e - s) > (end - start) for s, e, _ in spans
        )
    }


def character_usage(
    segments: Sequence[ReportSegment],
    entries: Sequence[GlossaryEntry],
    *,
    examples: int = 3,
) -> dict[str, Any]:
    """Pronouns (in segments naming only one character) and 你/您 in quoted speech, per character.

    Only 他 vs 她 counts as mixed: 它 mostly refers to objects ("it"), so it is
    shown but not judged. Formal 您 is rare, so every segment using it is
    listed for a reader to judge who is being addressed.
    """
    characters = _characters(entries)
    formal_segments = [
        {"segment_id": segment.segment_id, "text": visible_text(segment.target)}
        for segment in segments
        if _FORMAL_YOU.search("".join(a or b for a, b in _QUOTED.findall(visible_text(segment.target))))
    ]
    informal_segment_count = sum(
        1
        for segment in segments
        if _INFORMAL_YOU.search("".join(a or b for a, b in _QUOTED.findall(visible_text(segment.target))))
    )
    pronouns: dict[str, Counter[str]] = defaultdict(Counter)
    source_pronouns: dict[str, Counter[str]] = defaultdict(Counter)
    address: dict[str, Counter[str]] = defaultdict(Counter)
    pronoun_examples: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    address_examples: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    segments_named: Counter[str] = Counter()
    for segment in segments:
        target = visible_text(segment.target)
        named = _named_characters(target, characters)
        if not named:
            continue
        for name in named:
            segments_named[name] += 1
        quoted = "".join(a or b for a, b in _QUOTED.findall(target))
        informal = len(_INFORMAL_YOU.findall(quoted))
        formal = len(_FORMAL_YOU.findall(quoted))
        for name in named:
            if informal:
                address[name]["你"] += informal
                address_examples[name]["你"].append(segment.segment_id)
            if formal:
                address[name]["您"] += formal
                address_examples[name]["您"].append(segment.segment_id)
        if len(named) != 1:
            continue
        (name,) = named
        for pronoun in _TARGET_PRONOUN.findall(target):
            label = _TARGET_PRONOUN_LABEL[pronoun]
            pronouns[name][label] += 1
            if segment.segment_id not in pronoun_examples[name][label]:
                pronoun_examples[name][label].append(segment.segment_id)
        source = visible_text(segment.source)
        for label, pattern in _SOURCE_PRONOUNS.items():
            source_pronouns[name][label] += len(pattern.findall(source))

    rows = []
    for character in characters:
        name = character.name
        if not segments_named[name]:
            continue
        rows.append({
            "character": name,
            "target_names": list(character.target_names),
            "segments": segments_named[name],
            "pronouns": dict(pronouns[name]),
            "source_pronouns": {k: v for k, v in source_pronouns[name].items() if v},
            "pronouns_mixed": _is_mixed({k: pronouns[name][k] for k in ("he", "she")}),
            "pronoun_examples": {k: v[:examples] for k, v in pronoun_examples[name].items()},
            "address": dict(address[name]),
            "address_mixed": bool(address[name]["你"] and address[name]["您"]),
            "address_examples": {k: v[:examples] for k, v in address_examples[name].items()},
        })
    rows.sort(key=lambda row: (-(row["pronouns_mixed"] or row["address_mixed"]), -row["segments"]))
    return {
        "quoted_informal_segments": informal_segment_count,
        "quoted_formal_segments": formal_segments,
        "characters_checked": len(characters),
        "characters_seen": len(rows),
        "pronouns_mixed": [row["character"] for row in rows if row["pronouns_mixed"]],
        "address_mixed": [row["character"] for row in rows if row["address_mixed"]],
        "characters": rows,
    }


def conventions(segments: Sequence[ReportSegment], *, examples: int = 5) -> dict[str, Any]:
    """How often each punctuation and numeral style appears in the Chinese translation."""
    counts: dict[str, Counter[str]] = {group: Counter() for group in _CONVENTIONS}
    found: dict[str, dict[str, list[str]]] = {group: defaultdict(list) for group in _CONVENTIONS}
    for segment in segments:
        target = visible_text(segment.target)
        if not _CJK.search(target):
            continue
        for group, styles in _CONVENTIONS.items():
            for style, pattern in styles.items():
                if pattern.search(target):
                    counts[group][style] += 1
                    found[group][style].append(segment.segment_id)
    return {
        group: {
            "segments_by_style": dict(counts[group]),
            # Nested quotes accompany “ ” by design, so they are never "mixed".
            "mixed": len(counts[group]) > 1 and group != "nested quotation marks",
            "examples": {style: ids[:examples] for style, ids in found[group].items()},
        }
        for group in _CONVENTIONS
    }


def build_report(
    segments: Sequence[ReportSegment],
    entries: Sequence[GlossaryEntry],
    *,
    source_language: str,
    target_language: str,
    unglossed: dict[str, int] | None = None,
    min_chars: int = 12,
    examples: int = 5,
) -> dict[str, Any]:
    rules = profile(target_language)
    return {
        "direction": LanguagePair.of(source_language, target_language).value,
        "segment_count": len(segments),
        "document_count": len({segment.document_id for segment in segments}),
        "edited_segment_count": sum(1 for segment in segments if segment.edited),
        "repeated_lines": repeated_lines(segments, min_chars=min_chars),
        "repeated_sentences": repeated_sentences(segments, min_chars=min_chars, examples=examples),
        "glossary": glossary_compliance(
            segments,
            entries,
            source_language=source_language,
            target_language=target_language,
            examples=examples,
        ),
        "unglossed_names": unglossed or {},
        "characters": (
            character_usage(segments, entries, examples=examples) if rules.convention_checks else None
        ),
        "conventions": conventions(segments, examples=examples) if rules.convention_checks else None,
    }


def consistency_report(
    workspace: JobWorkspace, *, min_chars: int = 12, examples: int = 5
) -> dict[str, Any]:
    """Measure a job's current translation. Read-only."""
    from .stages.decompile import load_decompile_manifest
    from .stages.glossary import load_approved_glossary

    config = AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
    direction = config.translation.direction
    source_language = direction.source_language.value
    target_language = direction.target_language.value
    segments, basis = load_report_segments(workspace)
    try:
        entries = load_approved_glossary(workspace).entries
        glossary_note = ""
    except (FileNotFoundError, RuntimeError, ValueError):
        entries = []
        glossary_note = "no approved glossary; glossary and character sections are empty"
    unglossed: dict[str, int] = {}
    if profile(source_language).cased:
        manifest = load_decompile_manifest(workspace, connection=None)
        unglossed = find_unglossed_proper_nouns(list(manifest.documents), list(entries))
    report = build_report(
        segments,
        entries,
        source_language=source_language,
        target_language=target_language,
        unglossed=unglossed,
        min_chars=min_chars,
        examples=examples,
    )
    report["job_id"] = workspace.root.name
    report["text_basis"] = basis
    report["notes"] = [note for note in (glossary_note,) if note]
    return report


# -- rendering --------------------------------------------------------------------


def _clip(text: str, limit: int = 70) -> str:
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key} {value}" for key, value in counts.items()) or "—"


def format_report(report: dict[str, Any], *, limit: int = 20) -> str:
    """The report as Markdown, showing at most ``limit`` rows per table."""
    lines: list[str] = []
    add = lines.append
    add(f"# Consistency report: {report['job_id']}")
    add("")
    add(
        f"{report['segment_count']} segments in {report['document_count']} documents, "
        f"{report['direction']}. Text: {report['text_basis']}."
    )
    for note in report.get("notes", []):
        add(f"Note: {note}.")
    add("")

    repeated = report["repeated_lines"]
    add("## Repeated source lines")
    add("")
    add(
        f"{repeated['repeated_count']} source lines of at least {repeated['min_chars']} characters "
        f"occur more than once; **{repeated['inconsistent_count']}** are rendered in more than one way "
        f"({repeated['segments_in_inconsistent']} segments)."
    )
    inconsistent = [group for group in repeated["groups"] if group["variant_count"] > 1]
    for group in inconsistent[:limit]:
        add("")
        add(f"- **{_clip(group['source'], 90)}** ({group['occurrences']} times, {group['variant_count']} renderings)")
        for rendering in group["renderings"]:
            edited = " (edited)" if rendering["edited"] else ""
            add(f"  - `{rendering['segment_id']}`{edited}: {_clip(rendering['text'], 90)}")
    if len(inconsistent) > limit:
        add(f"\n…and {len(inconsistent) - limit} more (use --json for all).")
    add("")

    sentences = report["repeated_sentences"]
    add("## Repeated sentences inside segments (source only)")
    add("")
    add(
        f"{sentences['repeated_count']} source sentences of at least {sentences['min_chars']} characters "
        f"recur across {sentences['segments_involved']} segments, speech tags (*said the King*) left out. "
        "Their renderings are not compared yet: that needs sentence alignment, or a recurring-expression "
        "entry in the style sheet (phase 2)."
    )
    if sentences["sentences"]:
        add("")
        add("| Sentence | Segments | Where |")
        add("|---|---:|---|")
        for item in sentences["sentences"][:limit]:
            add(f"| {_clip(item['sentence'], 60)} | {item['segments']} | {', '.join(item['segment_ids'][:3])} |")
        if len(sentences["sentences"]) > limit:
            add(f"\n…and {len(sentences['sentences']) - limit} more.")
    add("")

    glossary = report["glossary"]
    add("## Glossary compliance, book-wide")
    add("")
    add(
        f"{glossary['entries_seen']} of {glossary['entries_checked']} entries occur in the source; "
        f"**{glossary['entries_with_misses']}** have occurrences whose translation lacks the approved "
        f"rendering ({glossary['miss_count']} segments)."
    )
    if glossary["entries"]:
        add("")
        add("| Term | Rendering | Misses / occurrences | Example |")
        add("|---|---|---:|---|")
        for row in glossary["entries"][:limit]:
            example = row["examples"][0] if row["examples"] else None
            shown = f"`{example['segment_id']}` {_clip(example['text'], 50)}" if example else ""
            add(f"| {row['source']} | {row['target']} | {row['misses']} / {row['occurrences']} | {shown} |")
        if len(glossary["entries"]) > limit:
            add(f"\n…and {len(glossary['entries']) - limit} more entries.")
    add("")

    unglossed = report["unglossed_names"]
    add("## Recurring names without a glossary entry")
    add("")
    if unglossed:
        add(", ".join(f"{name} ({count})" for name, count in unglossed.items()))
        add("")
        add("Their renderings, pronouns and forms of address are not checked below.")
    else:
        measured = profile(LanguagePair(report["direction"]).source_language).cased
        add("None found." if measured else "Not measured for this direction.")
    add("")

    characters = report["characters"]
    if characters is not None:
        add("## Pronouns and forms of address per character")
        add("")
        formal = characters["quoted_formal_segments"]
        add(
            f"Quoted speech uses 你 in {characters['quoted_informal_segments']} segments and "
            f"**您 in {len(formal)}**. Every 您 segment, to check who is being addressed:"
        )
        add("")
        for item in formal[:limit]:
            add(f"- `{item['segment_id']}` {_clip(item['text'], 90)}")
        if len(formal) > limit:
            add(f"- …and {len(formal) - limit} more (use --json for all).")
        add("")
        add(
            f"{characters['characters_seen']} of {characters['characters_checked']} person entries "
            "are named in the translation. Pronouns are counted only in segments that name one "
            "character, and only 他 vs 她 counts as mixed (它 is mostly *it*, an object). "
            "你/您 is counted inside quoted speech in segments naming the character, as speaker "
            "or addressee. These are signals, not verdicts."
        )
        add("")
        add(f"Mixed 他/她: {', '.join(characters['pronouns_mixed']) or 'none'}. "
            f"Both 你 and 您: {', '.join(characters['address_mixed']) or 'none'}.")
        add("")
        add("| Character | Segments | Pronouns (translation) | Pronouns (source) | 你 / 您 in quotes |")
        add("|---|---:|---|---|---|")
        for row in characters["characters"][:limit]:
            flag_p = " ⚠" if row["pronouns_mixed"] else ""
            flag_a = " ⚠" if row["address_mixed"] else ""
            add(
                f"| {row['character']} ({'/'.join(row['target_names'])}) | {row['segments']} | "
                f"{_counts(row['pronouns'])}{flag_p} | {_counts(row['source_pronouns'])} | "
                f"{_counts(row['address'])}{flag_a} |"
            )
        flagged = [row for row in characters["characters"] if row["pronouns_mixed"] or row["address_mixed"]]
        if flagged:
            add("")
            add("Examples for the flagged characters:")
            add("")
        labels = {"he": "他", "she": "她", "it": "它"}
        for row in flagged[:limit]:
            parts = []
            if row["pronouns_mixed"]:
                parts += [f"{labels[k]}: {', '.join(v)}" for k, v in row["pronoun_examples"].items() if k != "it"]
            if row["address_mixed"]:
                parts += [f"{k}: {', '.join(v)}" for k, v in row["address_examples"].items()]
            add(f"- {row['character']}: " + "; ".join(parts))
        add("")

    convention = report["conventions"]
    if convention is not None:
        add("## Punctuation and numeral conventions")
        add("")
        add("| Convention | Segments by style | Mixed | Example segments |")
        add("|---|---|---|---|")
        for group, data in convention.items():
            examples = "; ".join(f"{style}: {', '.join(ids[:3])}" for style, ids in data["examples"].items())
            add(f"| {group} | {_counts(data['segments_by_style'])} | {'yes' if data['mixed'] else 'no'} | {_clip(examples, 120)} |")
        add("")
    return "\n".join(lines)
