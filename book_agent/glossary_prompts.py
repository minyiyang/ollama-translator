"""Direction-aware prompts for structured glossary extraction and resolution."""

import json

from .glossary import (
    GlossaryChunk,
    build_glossary_approval_cases,
    build_glossary_resolution_cases,
)
from .languages import TranslationDirection
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
    return (
        f"Analyze the following {source} book passages and extract proper names, places, "
        f"organizations, objects, technologies, concepts, and recurring terms useful for "
        f"consistent translation into {target}. Return canonical English and Simplified "
        "Chinese fields regardless of source direction. Include a term only when it is a "
        "named entity, an invented or meaningfully coined expression, a recurring "
        "institutional label, or specialist terminology whose Chinese rendering genuinely "
        "needs to remain stable. Do not include ordinary nouns, body parts, furniture, "
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
    serialized = "\n".join(
        json.dumps(case, ensure_ascii=False, separators=(",", ":"))
        for case in build_glossary_resolution_cases(candidates)
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
    serialized = "\n".join(
        json.dumps(case, ensure_ascii=False, separators=(",", ":"))
        for case in build_glossary_resolution_cases(candidates)
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
        build_glossary_approval_cases(entries), direction
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
