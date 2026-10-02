"""The book style sheet (docs/BOOK_CONSISTENCY.md, phase 2).

What the glossary does not hold: how each character is referred to and
addressed, recurring expressions with one fixed rendering, and punctuation
conventions. Candidates come from the glossary extraction call (one more
section in its structured output), are merged deterministically, reviewed at
the glossary gate, and then used three ways: in translation and repair prompts
(only the entries relevant to a chunk), as reprose-protected wording, and as
checks in ``audit_consistency``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, create_model

from .languages import TranslationDirection

_INLINE_MARKER = re.compile(r"</?I\d{3}>")

Pronoun = Literal["他", "她", "它", "he", "she", "it", ""]
Address = Literal["你", "您", ""]


class StyleCharacter(BaseModel):
    """How one character is referred to and addressed throughout the book."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80, description="The character's name as written in the source.")
    pronoun: Pronoun = Field(default="", description="Third-person pronoun in the translation.")
    addressed_as: Address = Field(default="", description="How other characters address them in the translation.")
    voice: str = Field(default="", max_length=160, description="How they speak, in a few words.")
    evidence: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(
        default_factory=list, description="Other pronoun/address values seen in candidates, for review."
    )


class StyleExpression(BaseModel):
    """A recurring line or phrase that must be rendered the same way every time."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: str = Field(min_length=4, max_length=200)
    rendering: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=160)
    evidence: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class StyleConventions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotation_marks: str = "“ ”"
    nested_quotation_marks: str = "‘ ’"
    ellipsis: str = "……"
    dash: str = "——"
    numerals: Literal["chinese", "arabic", ""] = ""


class StyleSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    characters: list[StyleCharacter] = Field(default_factory=list)
    expressions: list[StyleExpression] = Field(default_factory=list)
    conventions: StyleConventions = Field(default_factory=StyleConventions)

    def is_empty(self) -> bool:
        return not self.characters and not self.expressions


def default_conventions(direction: TranslationDirection) -> StyleConventions:
    if direction.target_language.value == "zh":
        return StyleConventions()
    return StyleConventions(
        quotation_marks="“ ”", nested_quotation_marks="‘ ’", ellipsis="…", dash="—", numerals=""
    )


# -- extraction ------------------------------------------------------------------------


def extraction_instructions(direction: TranslationDirection, *, max_characters: int, max_expressions: int) -> str:
    """The paragraph appended to the glossary extraction prompt when the style sheet is on."""
    zh = direction.target_language.value == "zh"
    pronouns = "他, 她, or 它" if zh else "he, she, or it"
    address = (
        " and, when the passages show it, whether other characters address them as 你 or the "
        "polite 您"
        if zh
        else ""
    )
    return (
        "\n\nAlso fill the separate `style` object, a book style sheet for consistent "
        f"translation into {direction.target_language.display_name}. In `characters`, list "
        f"at most {max_characters} characters who speak or act in these passages, including "
        "animals and personified creatures, with the name exactly as written in the passage, "
        f"the pronoun the translation should use for them ({pronouns}; use it for an animal "
        f"only when the story treats it as an object rather than a person){address}, and "
        "a few words on how they speak. In `expressions`, list at most "
        f"{max_expressions} lines or phrases the passages repeat, such as a catchphrase, "
        "refrain, or recurring formula of address, each copied verbatim from the passage "
        f"with one fixed {direction.target_language.display_name} rendering. Cite evidence "
        "IDs exactly as for glossary entries. Leave `style` lists empty rather than guess."
    )


def build_style_candidate_schema(
    allowed_evidence_ids: Sequence[str], *, max_characters: int, max_expressions: int
) -> type[BaseModel]:
    evidence = list[Literal.__getitem__(tuple(dict.fromkeys(allowed_evidence_ids)))]  # type: ignore[misc]
    character = create_model(
        "StyleCharacterCandidate",
        __base__=StyleCharacter,
        evidence=(evidence, Field(min_length=1, max_length=3)),
    )
    expression = create_model(
        "StyleExpressionCandidate",
        __base__=StyleExpression,
        evidence=(evidence, Field(min_length=1, max_length=3)),
    )
    return create_model(
        "StyleSheetCandidate",
        __config__=ConfigDict(extra="forbid"),
        characters=(list[character], Field(default_factory=list, max_length=max_characters)),
        expressions=(list[expression], Field(default_factory=list, max_length=max_expressions)),
    )


def candidate_from_model(value: BaseModel | None) -> StyleSheet:
    """A validated per-chunk candidate as a plain StyleSheet (alternatives dropped)."""
    if value is None:
        return StyleSheet()
    data = value.model_dump()
    return StyleSheet(
        characters=[StyleCharacter.model_validate({**item, "alternatives": []}) for item in data["characters"]],
        expressions=[StyleExpression.model_validate({**item, "alternatives": []}) for item in data["expressions"]],
    )


# -- resolution ------------------------------------------------------------------------


def _key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _majority(values: Iterable[str]) -> tuple[str, list[str]]:
    counts = Counter(value for value in values if value)
    if not counts:
        return "", []
    ordered = [value for value, _ in counts.most_common()]
    return ordered[0], ordered[1:]


def merge_style_candidates(
    candidates: Sequence[StyleSheet], direction: TranslationDirection
) -> StyleSheet:
    """One draft style sheet from every chunk's candidates.

    Characters merge by name and expressions by source text; the most common
    value wins and the others are kept as ``alternatives`` for the reviewer.
    """
    characters: dict[str, list[StyleCharacter]] = {}
    for sheet in candidates:
        for item in sheet.characters:
            characters.setdefault(_key(item.name), []).append(item)
    expressions: dict[str, list[StyleExpression]] = {}
    for sheet in candidates:
        for item in sheet.expressions:
            expressions.setdefault(_key(item.source), []).append(item)

    merged_characters = []
    for group in characters.values():
        pronoun, other_pronouns = _majority(item.pronoun for item in group)
        address, other_addresses = _majority(item.addressed_as for item in group)
        voice = max((item.voice for item in group), key=len)
        merged_characters.append(StyleCharacter(
            name=group[0].name,
            pronoun=pronoun,  # type: ignore[arg-type]
            addressed_as=address,  # type: ignore[arg-type]
            voice=voice,
            evidence=list(dict.fromkeys(e for item in group for e in item.evidence))[:3],
            alternatives=[*other_pronouns, *other_addresses],
        ))
    merged_expressions = []
    for group in expressions.values():
        rendering, others = _majority(item.rendering for item in group)
        merged_expressions.append(StyleExpression(
            source=group[0].source,
            rendering=rendering,
            note=max((item.note for item in group), key=len),
            evidence=list(dict.fromkeys(e for item in group for e in item.evidence))[:3],
            alternatives=others,
        ))
    return StyleSheet(
        characters=sorted(merged_characters, key=lambda item: _key(item.name)),
        expressions=sorted(merged_expressions, key=lambda item: _key(item.source)),
        conventions=default_conventions(direction),
    )


def _is_spelled_word_character(character: str) -> bool:
    """A letter or digit of a script that separates words with spaces (not CJK)."""
    return character.isalnum() and ord(character) < 0x2E80


def expression_pattern(source: str) -> re.Pattern[str]:
    """Matches an expression in NFKC-casefolded text, as whole words.

    An end that is a letter or digit must not continue into another word, so
    "the rat" does not match "the rattle".
    """
    key = _key(source)
    before = r"(?<![^\W_])" if _is_spelled_word_character(key[:1]) else ""
    after = r"(?![^\W_])" if _is_spelled_word_character(key[-1:]) else ""
    return re.compile(before + re.escape(key) + after)


MIN_EXPRESSION_CHARACTERS = 6


def keep_recurring_expressions(sheet: StyleSheet, texts: Iterable[str]) -> StyleSheet:
    """Drop expressions that do not recur verbatim in the book's source.

    Extraction proposes many "recurring" lines that occur once or not at all
    (on *The Wind in the Willows*, 84 of 101): a style-sheet expression must
    occur at least twice. Very short ones ("O my!", a nickname) are dropped
    too; names belong in the glossary.
    """
    # Segments are joined with a separator no expression contains, so a match cannot span two.
    book = " | ".join(unicodedata.normalize("NFKC", _visible(text)).casefold() for text in texts)
    kept = [
        item
        for item in sheet.expressions
        if len(item.source) >= MIN_EXPRESSION_CHARACTERS
        and len(expression_pattern(item.source).findall(book)) >= 2
    ]
    return sheet.model_copy(update={"expressions": kept})


# -- LLM review ------------------------------------------------------------------------


class StyleCharacterDecision(BaseModel):
    """A reviewer decision on one character, always with the final values.

    The values are required even for approve, so a "revise" can never arrive
    without the correction (a reviewer once explained 它 -> 他 only in its reason).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    entry_id: str
    action: Literal["approve", "revise", "reject"]
    pronoun: Pronoun
    addressed_as: Address
    reason: str = Field(min_length=3, max_length=200)


class StyleExpressionDecision(BaseModel):
    """A reviewer decision on one expression, always with the final rendering."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    entry_id: str
    action: Literal["approve", "revise", "reject"]
    rendering: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=200)


StyleReviewDecision = StyleCharacterDecision | StyleExpressionDecision


def style_entry_ids(sheet: StyleSheet) -> list[str]:
    return [f"C{index:04d}" for index in range(1, len(sheet.characters) + 1)] + [
        f"X{index:04d}" for index in range(1, len(sheet.expressions) + 1)
    ]


def _decisions_field(base: type[BaseModel], ids: Sequence[str], name: str):
    entry_id = Literal.__getitem__(tuple(ids)) if ids else str  # type: ignore[misc]
    decision = create_model(name, __base__=base, entry_id=(entry_id, ...))
    return (list[decision], Field(min_length=len(ids), max_length=len(ids)))


def build_style_review_schema(sheet: StyleSheet) -> type[BaseModel]:
    """Exactly one decision per entry, characters and expressions in separate lists."""
    ids = style_entry_ids(sheet)
    return create_model(
        "StyleReviewResult",
        __config__=ConfigDict(extra="forbid"),
        characters=_decisions_field(
            StyleCharacterDecision, [i for i in ids if i.startswith("C")], "StyleCharacterDecisionFor"
        ),
        expressions=_decisions_field(
            StyleExpressionDecision, [i for i in ids if i.startswith("X")], "StyleExpressionDecisionFor"
        ),
    )


def review_decisions(result: BaseModel) -> list[StyleReviewDecision]:
    """The structured review result as plain decisions."""
    return [
        *(StyleCharacterDecision.model_validate(item.model_dump()) for item in result.characters),
        *(StyleExpressionDecision.model_validate(item.model_dump()) for item in result.expressions),
    ]


def style_review_line(
    entry_id: str, item: StyleCharacter | StyleExpression, evidence: dict[str, str]
) -> str:
    """One entry as the review prompt shows it."""
    quotes = " | ".join(evidence.get(ref, "")[:160] for ref in item.evidence if ref in evidence)
    if isinstance(item, StyleCharacter):
        return (
            f"{entry_id} character {item.name!r}: pronoun={item.pronoun or '-'} "
            f"addressed_as={item.addressed_as or '-'} voice={item.voice!r} "
            f"alternatives={item.alternatives} evidence: {quotes}"
        )
    return (
        f"{entry_id} expression {item.source!r} => {item.rendering!r} "
        f"alternatives={item.alternatives} evidence: {quotes}"
    )


def build_style_review_prompt(
    sheet: StyleSheet, direction: TranslationDirection, evidence: dict[str, str]
) -> str:
    target = direction.target_language.display_name
    lines = [
        style_review_line(entry_id, item, evidence)
        for entry_id, item in zip(style_entry_ids(sheet), [*sheet.characters, *sheet.expressions])
    ]
    return (
        f"Review this draft book style sheet for a translation into {target}. For every "
        "entry return exactly one decision, always with the entry's final values: pronoun and "
        "addressed_as for a character (in `characters`), rendering for an expression (in "
        "`expressions`). approve: the draft values are correct; repeat them. revise: give the "
        "corrected values. reject: not a real character or not a genuinely recurring "
        "expression, or the evidence does not support it. A character's pronoun must fit how "
        "the story treats them (a talking animal that acts as a person is not 它). Give a "
        "short reason for each decision.\n\n" + "\n".join(lines)
    )


def apply_style_review(sheet: StyleSheet, decisions: Sequence[StyleReviewDecision]) -> StyleSheet:
    by_id = {decision.entry_id: decision for decision in decisions}
    ids = style_entry_ids(sheet)
    characters, expressions = [], []
    for entry_id, item in zip(ids, [*sheet.characters, *sheet.expressions]):
        decision = by_id.get(entry_id)
        if decision is None or decision.action == "reject":
            continue
        if isinstance(item, StyleCharacter) and isinstance(decision, StyleCharacterDecision):
            if decision.action == "revise":
                item = item.model_copy(
                    update={"pronoun": decision.pronoun, "addressed_as": decision.addressed_as}
                )
            characters.append(item.model_copy(update={"alternatives": []}))
        elif isinstance(item, StyleExpression) and isinstance(decision, StyleExpressionDecision):
            if decision.action == "revise":
                item = item.model_copy(update={"rendering": decision.rendering})
            expressions.append(item.model_copy(update={"alternatives": []}))
    return StyleSheet(characters=characters, expressions=expressions, conventions=sheet.conventions)


def load_style_sheet(workspace, key: str = "style_approved") -> StyleSheet | None:
    """The approved (or, with ``key="style_draft"``, draft) style sheet, if one was published."""
    from .state import connect_state, get_job_metadata

    connection = connect_state(workspace.state_file)
    try:
        path = get_job_metadata(connection, key)
    finally:
        connection.close()
    if not path:
        return None
    file = workspace.directory(path)
    if not file.is_file():
        return None
    return StyleSheet.model_validate_json(file.read_text(encoding="utf-8"))


def enabled_style_sheet(workspace, config) -> StyleSheet | None:
    """The approved style sheet when ``consistency.style_sheet`` is on, else None."""
    if not config.consistency.style_sheet.enabled:
        return None
    return load_style_sheet(workspace)


# -- use --------------------------------------------------------------------------------


def _visible(text: str) -> str:
    return " ".join(_INLINE_MARKER.sub("", text).split())


def _name_pattern(name: str) -> re.Pattern[str]:
    if re.search(r"[A-Za-z]", name):
        return re.compile(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])")
    return re.compile(re.escape(name))


def select_relevant_style(sheet: StyleSheet, source_text: str, *, max_entries: int) -> StyleSheet:
    """The characters and expressions that occur in ``source_text``, conventions always."""
    text = _visible(source_text)
    folded = unicodedata.normalize("NFKC", text).casefold()
    characters = [item for item in sheet.characters if _name_pattern(item.name).search(text)]
    expressions = [
        item for item in sheet.expressions
        if expression_pattern(item.source).search(folded)
    ]
    budget = max(0, max_entries)
    expressions = expressions[:budget]
    characters = characters[: max(0, budget - len(expressions))]
    return StyleSheet(characters=characters, expressions=expressions, conventions=sheet.conventions)


def format_relevant_style(sheet: StyleSheet) -> str:
    """The prompt block, or "" when nothing applies (so prompts without a style sheet are unchanged)."""
    if sheet.is_empty():
        return ""
    lines = ["Book style sheet (approved with the glossary):"]
    # The source wording and the scene come first: a pronoun or a form of address
    # changes with context, so character lines are notes, never rules.
    notes = []
    for item in sheet.characters:
        parts = []
        if item.pronoun:
            parts.append(f"usually {item.pronoun}")
        if item.addressed_as:
            parts.append(f"often addressed as {item.addressed_as}")
        if item.voice:
            parts.append(f"voice: {item.voice}")
        if parts:
            notes.append(f"  - {item.name}: " + "; ".join(parts))
    if notes:
        lines.append(
            "- Character notes, context only. The source wording and the scene decide "
            "pronouns and forms of address; use a note only where the source leaves the "
            "choice open:"
        )
        lines += notes
    if sheet.expressions:
        lines.append(
            "- Recurring expressions: where the same line recurs with the same meaning, "
            "render it as shown, so it reads the same throughout the book:"
        )
        lines += [f"  - {item.source!r} => {item.rendering!r}" for item in sheet.expressions]
    conventions = sheet.conventions
    lines.append(
        f"- conventions: quotation marks {conventions.quotation_marks}, nested "
        f"{conventions.nested_quotation_marks}, ellipsis {conventions.ellipsis}, dash {conventions.dash}"
    )
    return "\n".join(lines)
