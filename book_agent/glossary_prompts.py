"""Direction-aware prompts for structured glossary extraction and resolution.

An en/zh book's glossary is en-zh whichever way the book is translated, and its
prompts are worded for English and Chinese (byte-for-byte as before phase 3 of
docs/GENERIC_LANGUAGES.md). Any other pair's glossary is keyed by the source
term, and its prompts name the two languages and the `source`/`target` fields.
"""

import json

from .glossary import (
    GlossaryChunk,
    build_glossary_approval_cases,
    build_glossary_resolution_cases,
)
from .languages import TranslationDirection, glossary_pair, profile
from .schemas import GlossaryCategory, GlossaryEntry


_CATEGORIES = "人名, 地名, 组织, 物品, 技术, 概念, 术语, 其他"


def _resolution_format_example() -> str:
    """Return one obfuscated shape example without real-book terminology."""
    example_input = {
        "term_id": "T90001",
        "english": "Qelm Spindle",
        "candidates": [
            {
                "chinese": "奇尔姆纺锤",
                "note": "",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "evidence": ["D0000-S000001"],
                "confidence": 1.0,
            }
        ],
    }
    example_output = {
        "decisions": [
            {
                "term_id": "T90001",
                "chinese": "奇尔姆纺锤",
                "note": "架空设备",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "confidence": 1.0,
            }
        ]
    }
    return (
        "Format-only example; T90001 is not a real term_id and must never be "
        "returned for the actual cases. Example input: "
        + json.dumps(example_input, ensure_ascii=False, separators=(",", ":"))
        + " Example output: "
        + json.dumps(example_output, ensure_ascii=False, separators=(",", ":"))
        + " Notice that the output has no English field."
    )


def _approval_format_example() -> str:
    """Return one obfuscated example covering the approval delta actions."""
    example_input = [
        {
            "term_id": "A90001",
            "entry": {
                "english": "Qelm",
                "chinese": "奇尔姆",
                "note": "人物",
                "category": GlossaryCategory.PERSON.value,
                "aliases": [],
                "evidence": ["D0000-S000001"],
                "confidence": 1.0,
            },
        },
        {
            "term_id": "A90002",
            "entry": {
                "english": "Vrax gauge",
                "chinese": "弗拉克斯",
                "note": "装置",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "evidence": ["D0000-S000002"],
                "confidence": 0.7,
            },
        },
        {
            "term_id": "A90003",
            "entry": {
                "english": "zorb",
                "chinese": "佐布",
                "note": "普通词",
                "category": GlossaryCategory.OTHER.value,
                "aliases": [],
                "evidence": ["D0000-S000003"],
                "confidence": 0.6,
            },
        },
    ]
    example_output = {
        "decisions": [
            {"term_id": "A90001", "action": "approve"},
            {
                "term_id": "A90002",
                "action": "revise",
                "chinese": "弗拉克斯量规",
                "note": "架空测量装置",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "confidence": 0.95,
                "reason": "原译遗漏类别词",
            },
            {
                "term_id": "A90003",
                "action": "reject",
                "reason": "缺乏专名或术语意义",
            },
        ]
    }
    return (
        "Format-only example; A90001-A90003 are not real IDs and must never be "
        "returned for actual cases. Example input: "
        + json.dumps(example_input, ensure_ascii=False, separators=(",", ":"))
        + " Example output: "
        + json.dumps(example_output, ensure_ascii=False, separators=(",", ":"))
        + " The output contains deltas only and has no English field."
    )


def build_extraction_prompt(
    chunk: GlossaryChunk,
    direction: TranslationDirection,
    *,
    max_entries: int = 80,
    max_evidence_per_entry: int = 3,
) -> str:
    """Build a structured candidate-extraction prompt for one complete chunk."""
    source = direction.source_language.display_name
    target = direction.target_language.display_name
    if not glossary_pair(direction).legacy:
        return _generic_extraction_prompt(
            chunk,
            source,
            target,
            max_entries=max_entries,
            max_evidence_per_entry=max_evidence_per_entry,
            note=profile(direction.source_language).extraction_note,
        )
    return (
        f"Analyze the following {source} book passages and extract proper names, places, "
        f"organizations, objects, technologies, concepts, and recurring terms useful for "
        f"consistent translation into {target}. Return canonical English and Simplified "
        "Chinese fields regardless of source direction. Include a term only when it is a "
        "named entity, an invented or meaningfully coined expression, a recurring "
        "institutional label, or specialist terminology whose Chinese rendering genuinely "
        "needs to remain stable. Include recurring titles and forms of address used for a "
        "particular person, with one fixed rendering: an honorific used in place of a name "
        "(His Grace, Your Majesty) and a title that goes with a name (Lady Hilda, Dr. "
        "Armstrong, Inspector Lestrade), in the category for people. Do not include "
        "ordinary nouns, body parts, furniture, "
        "materials, tools, weapons, standard academic disciplines, common biological "
        "vocabulary, adjectives, verbs, or transparent inflectional variants. Prefer one "
        "canonical entry over separate singular/plural or capitalization variants. "
        "For a technical code, model designation, or uppercase acronym that Chinese prose "
        "normally preserves unchanged, copy the exact same identifier into both fields "
        "instead of inventing Chinese characters. "
        f"Use only these category values: {_CATEGORIES}. Every entry must cite one or more "
        f"one to {max_evidence_per_entry} exact, representative reference IDs from the "
        "passage in its evidence array; never list every occurrence of a term. Return at "
        f"most {max_entries} entries for this "
        "chunk. Copy every evidence ID "
        "character-for-character, including its full prefix and all leading zeroes. Never "
        "shorten, renumber, infer, continue, or invent an ID, and never cite an ID range. "
        "Each cited evidence passage must contain the English term or one of its aliases "
        "verbatim, allowing only harmless typographic apostrophe or hyphen variation; do "
        "not attach a plausible label to a passage where that label does not occur. "
        "Keep aliases separate "
        "from the canonical English term.\n\n"
        f"{chunk.render()}"
    )


def build_resolution_prompt(
    candidates: list[GlossaryEntry],
    direction: TranslationDirection,
) -> str:
    """Build a conflict-resolution prompt over deterministically merged candidates."""
    target = direction.target_language.display_name
    pair = glossary_pair(direction)
    serialized = "\n".join(
        json.dumps(case, ensure_ascii=False, separators=(",", ":"))
        for case in build_glossary_resolution_cases(candidates, pair)
    )
    if not pair.legacy:
        return _generic_resolution_prompt(
            direction.source_language.display_name, target, serialized, conflicts=False
        )
    return (
        f"Resolve these candidate glossary entries for consistent translation into {target}. "
        "Remove false positives and exact duplicates. Preserve genuinely valid alternative "
        "translations as separate entries. Choose conventional translations when known, but "
        "do not claim web verification. Return decisions keyed only by the supplied term_id; "
        "never return, copy, correct, or rewrite the English spelling. The pipeline restores "
        "that spelling deterministically. Omit a term_id only when removing that source term "
        "as a false positive. Use multiple decisions with the same term_id only for genuinely "
        "different senses or useful translations. Preserve technical codes, model designations, "
        "and uppercase acronyms unchanged in Chinese when that is conventional. "
        f"Use only these category values: {_CATEGORIES}. Notes must be concise Chinese.\n\n"
        f"{_resolution_format_example()}\n\n"
        f"Candidate cases:\n{serialized}"
    )


def build_conflict_resolution_prompt(
    candidates: list[GlossaryEntry],
    direction: TranslationDirection,
) -> str:
    """Build a strict second-pass prompt only for unresolved term conflicts."""
    target = direction.target_language.display_name
    pair = glossary_pair(direction)
    serialized = "\n".join(
        json.dumps(case, ensure_ascii=False, separators=(",", ":"))
        for case in build_glossary_resolution_cases(candidates, pair)
    )
    if not pair.legacy:
        return _generic_resolution_prompt(
            direction.source_language.display_name, target, serialized, conflicts=True
        )
    return (
        f"Resolve only the remaining conflicting glossary entries for translation into "
        f"{target}. Candidate variants under one term_id are presumed to describe the "
        "same entity or concept. Select one canonical Chinese translation and category "
        "unless the supplied notes and evidence identifiers clearly establish genuinely "
        "different senses. Remove speculative role, organization, place, or technology "
        "interpretations. Return decisions keyed only by the supplied term_id. Never return, "
        "copy, correct, or rewrite an English term; the pipeline restores it exactly. Return "
        "the complete resolved decision set for these conflicts only.\n\n"
        f"{_resolution_format_example()}\n\n"
        f"Conflict cases:\n{serialized}"
    )


def build_review_prompt(
    entries: list[GlossaryEntry],
    direction: TranslationDirection,
) -> str:
    """Build an ID-safe approval prompt for a standalone entry list."""
    return build_approval_review_prompt(
        build_glossary_approval_cases(entries, glossary_pair(direction)), direction
    )


def build_approval_review_prompt(
    cases: list[dict[str, object]],
    direction: TranslationDirection,
) -> str:
    """Build an independent delta-only review prompt for selected entries."""
    target = direction.target_language.display_name
    serialized = "\n".join(
        json.dumps(case, ensure_ascii=False, separators=(",", ":"))
        for case in cases
    )
    if not glossary_pair(direction).legacy:
        return _generic_approval_prompt(direction.source_language.display_name, target, serialized)
    return (
        f"Independently review only these selected English-Chinese glossary entries for "
        f"translation into {target}. Return exactly one decision for every supplied term_id. "
        "Use action=approve when the entry is already correct, action=reject for a false "
        "positive or ordinary word, action=revise only when fields must change, and "
        "action=pending only when the evidence is genuinely insufficient for a safe decision. "
        "For revise, provide the corrected Chinese field and any corrected category, aliases, "
        "note, or confidence. Prefer established conventional translations when known, but "
        "do not claim internet verification. Never return, copy, correct, or rewrite English; "
        "the pipeline owns exact source spellings. Preserve technical identifiers unchanged "
        f"when conventional. Use only these category values: {_CATEGORIES}.\n\n"
        f"{_approval_format_example()}\n\n"
        f"Selected approval cases:\n{serialized}"
    )


# -- any other pair: fields `source` and `target` ----------------------------------------


def _generic_resolution_example() -> str:
    example_input = {
        "term_id": "T90001",
        "source": "Qelm Spindle",
        "candidates": [
            {
                "target": "QELM-SPINDLE-RENDERING",
                "note": "",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "evidence": ["D0000-S000001"],
                "confidence": 1.0,
            }
        ],
    }
    example_output = {
        "decisions": [
            {
                "term_id": "T90001",
                "target": "QELM-SPINDLE-RENDERING",
                "note": "invented device",
                "category": GlossaryCategory.TECHNOLOGY.value,
                "aliases": [],
                "confidence": 1.0,
            }
        ]
    }
    return (
        "Format-only example; T90001 is not a real term_id and must never be returned for "
        "the actual cases, and QELM-SPINDLE-RENDERING stands for a real rendering. Example "
        "input: "
        + json.dumps(example_input, ensure_ascii=False, separators=(",", ":"))
        + " Example output: "
        + json.dumps(example_output, ensure_ascii=False, separators=(",", ":"))
        + " Notice that the output has no source field."
    )


def _generic_approval_example() -> str:
    example_input = [
        {
            "term_id": "A90001",
            "entry": {
                "source": "Qelm",
                "target": "QELM-RENDERING",
                "note": "person",
                "category": GlossaryCategory.PERSON.value,
                "aliases": [],
                "evidence": ["D0000-S000001"],
                "confidence": 1.0,
            },
        },
        {
            "term_id": "A90002",
            "entry": {
                "source": "zorb",
                "target": "ZORB-RENDERING",
                "note": "ordinary word",
                "category": GlossaryCategory.OTHER.value,
                "aliases": [],
                "evidence": ["D0000-S000002"],
                "confidence": 0.6,
            },
        },
    ]
    example_output = {
        "decisions": [
            {"term_id": "A90001", "action": "approve"},
            {"term_id": "A90002", "action": "reject", "reason": "ordinary word, not a term"},
        ]
    }
    return (
        "Format-only example; A90001-A90002 are not real IDs and must never be returned for "
        "actual cases, and the uppercase renderings stand for real ones. Example input: "
        + json.dumps(example_input, ensure_ascii=False, separators=(",", ":"))
        + " Example output: "
        + json.dumps(example_output, ensure_ascii=False, separators=(",", ":"))
        + " The output contains deltas only and has no source field."
    )


def _generic_extraction_prompt(
    chunk: GlossaryChunk,
    source: str,
    target: str,
    *,
    max_entries: int,
    max_evidence_per_entry: int,
    note: str = "",
) -> str:
    return (
        f"Analyze the following {source} book passages and extract proper names, places, "
        f"organizations, objects, technologies, concepts, and recurring terms useful for "
        f"consistent translation into {target}. Put each {source} term, exactly as written in "
        f"the passage, in the `source` field and its {target} rendering in the `target` field. "
        "Include a term only when it is a named entity, an invented or meaningfully coined "
        "expression, a recurring institutional label, or specialist terminology whose "
        f"{target} rendering genuinely needs to remain stable. Include recurring titles and "
        "forms of address used for a particular person, with one fixed rendering, in the "
        "category for people. Do not include ordinary nouns, body parts, furniture, materials, "
        "tools, weapons, standard academic disciplines, common biological vocabulary, "
        "adjectives, verbs, or transparent inflectional variants. "
        + (note + " " if note else "")
        + "Prefer one canonical entry "
        "over separate singular/plural or capitalization variants. For a technical code, model "
        f"designation, or uppercase acronym that {target} prose normally preserves unchanged, "
        "copy the exact same identifier into both fields. "
        f"Use only these category values: {_CATEGORIES}. Every entry must cite one to "
        f"{max_evidence_per_entry} exact, representative reference IDs from the passage in its "
        "evidence array; never list every occurrence of a term. Return at most "
        f"{max_entries} entries for this chunk. Copy every evidence ID character-for-character, "
        "including its full prefix and all leading zeroes. Never shorten, renumber, infer, "
        "continue, or invent an ID, and never cite an ID range. Each cited evidence passage "
        "must contain the source term or one of its aliases verbatim; do not attach a "
        "plausible label to a passage where that label does not occur. Keep aliases (other "
        f"{source} spellings of the term) separate from the source term.\n\n"
        f"{chunk.render()}"
    )


def _generic_resolution_prompt(source: str, target: str, serialized: str, *, conflicts: bool) -> str:
    if conflicts:
        task = (
            f"Resolve only the remaining conflicting glossary entries for translation from "
            f"{source} into {target}. Candidate variants under one term_id are presumed to "
            f"describe the same entity or concept. Select one canonical {target} rendering and "
            "category unless the supplied notes and evidence identifiers clearly establish "
            "genuinely different senses. Remove speculative role, organization, place, or "
            "technology interpretations. Return the complete resolved decision set for these "
            "conflicts only."
        )
        heading = "Conflict cases"
    else:
        task = (
            f"Resolve these candidate glossary entries for consistent translation from {source} "
            f"into {target}. Remove false positives and exact duplicates. Preserve genuinely valid "
            "alternative translations as separate entries. Choose conventional translations when "
            "known, but do not claim web verification. Omit a term_id only when removing that "
            "source term as a false positive. Use multiple decisions with the same term_id only "
            "for genuinely different senses or useful translations. Preserve technical codes, "
            f"model designations, and uppercase acronyms unchanged in {target} when that is "
            "conventional."
        )
        heading = "Candidate cases"
    return (
        f"{task} Return decisions keyed only by the supplied term_id, with the rendering in "
        f"`target`; never return, copy, correct, or rewrite the {source} source term. The "
        f"pipeline restores it exactly. Use only these category values: {_CATEGORIES}. Notes "
        "must be concise.\n\n"
        f"{_generic_resolution_example()}\n\n"
        f"{heading}:\n{serialized}"
    )


def _generic_approval_prompt(source: str, target: str, serialized: str) -> str:
    return (
        f"Independently review only these selected {source}-{target} glossary entries for "
        f"translation into {target}. Return exactly one decision for every supplied term_id. "
        "Use action=approve when the entry is already correct, action=reject for a false "
        "positive or ordinary word, action=revise only when fields must change, and "
        "action=pending only when the evidence is genuinely insufficient for a safe decision. "
        f"For revise, provide the corrected `target` and any corrected category, aliases, note, "
        "or confidence. Prefer established conventional translations when known, but do not "
        f"claim internet verification. Never return, copy, correct, or rewrite the {source} "
        "source term; the pipeline owns exact source spellings. Preserve technical identifiers "
        f"unchanged when conventional. Use only these category values: {_CATEGORIES}.\n\n"
        f"{_generic_approval_example()}\n\n"
        f"Selected approval cases:\n{serialized}"
    )
