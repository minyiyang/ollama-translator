"""Translation prose-style profiles."""

from enum import Enum
from pathlib import Path

from .languages import TranslationDirection, build_direction_instruction


class TranslationStyle(str, Enum):
    FAITHFUL = "faithful"
    NATURAL = "natural"
    LITERARY = "literary"
    CONCISE = "concise"
    CLASSIC = "classic"
    YOUNG_ADULT = "young_adult"
    FANTASY = "fantasy"
    SCIENCE_FICTION = "science_fiction"
    CUSTOM = "custom"


STYLE_INSTRUCTIONS: dict[TranslationStyle, str] = {
    TranslationStyle.FAITHFUL: (
        "Stay maximally faithful to source meaning, structure, tone, and emphasis while "
        "using natural target-language syntax."
    ),
    TranslationStyle.NATURAL: (
        "Use fluent contemporary target-language prose and avoid stiff calques without "
        "adding, deleting, or simplifying source meaning."
    ),
    TranslationStyle.LITERARY: (
        "Use polished literary prose with deliberate rhythm, imagery, and distinct "
        "character voices while strictly preserving the source meaning."
    ),
    TranslationStyle.CONCISE: (
        "Use concise, direct target-language expression and reduce verbal padding without "
        "removing any source information."
    ),
    TranslationStyle.CLASSIC: (
        "Use restrained, moderately formal diction with a classic register while remaining "
        "clear and readable rather than archaic."
    ),
    TranslationStyle.YOUNG_ADULT: (
        "Use clear, accessible contemporary prose, preserve character individuality, and "
        "avoid making the voice childish."
    ),
    TranslationStyle.FANTASY: (
        "Use a literary fantasy register and maintain consistent world-building terms, "
        "titles, forms of address, and proper names."
    ),
    TranslationStyle.SCIENCE_FICTION: (
        "Use a precise, controlled science-fiction register and prioritize consistency in "
        "technical terminology and scientific concepts."
    ),
}

DE_AI_INSTRUCTIONS = {
    "conservative": (
        "Preserve the author's idiolect and natural sentence variation. Avoid adding formulaic "
        "transitions, repetitive parallel structures, generic lyrical embellishment, inflated "
        "abstractions, explanatory conclusions, or emphasis absent from the source."
    ),
    "moderate": (
        "Actively avoid model-like prose: formulaic transitions, repeated rhetorical triplets, "
        "uniform sentence rhythm, excessive em dashes or semicolons, generic poetic padding, "
        "over-explanation, inflated abstractions, and repeated restatement. Preserve all source "
        "meaning and the author's intentional repetitions."
    ),
}


def load_style_instruction(
    style: TranslationStyle,
    custom_style_file: str | Path | None = None,
) -> str:
    """Return the instruction for a built-in style or load a custom UTF-8 file."""
    if style is TranslationStyle.CUSTOM:
        if custom_style_file is None:
            raise ValueError("custom_style_file is required for the custom style")
        content = Path(custom_style_file).read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError("custom style prompt cannot be empty")
        return content

    if custom_style_file is not None:
        raise ValueError("custom_style_file can only be used with the custom style")
    return STYLE_INSTRUCTIONS[style]


def build_style_prompt(
    instruction: str,
    direction: TranslationDirection,
    *,
    de_ai_enabled: bool = True,
    de_ai_strength: str = "conservative",
) -> str:
    """Wrap a style instruction with direction and hard translation constraints."""
    cleaned = instruction.strip()
    if not cleaned:
        raise ValueError("style instruction cannot be empty")
    naturalness = ""
    if de_ai_enabled:
        naturalness = f"\n\nNaturalness overlay:\n{build_de_ai_instruction(de_ai_strength)}"
    return (
        f"{build_direction_instruction(direction)}\n\n"
        "Translation prose style:\n"
        f"{cleaned}{naturalness}\n\n"
        "Hard constraints: do not omit, add, or change source information; preserve "
        "structural markers; follow the approved glossary; prose style never overrides "
        "these constraints."
    )


def build_de_ai_instruction(strength: str) -> str:
    """Return a bounded naturalness overlay without requesting broad rewriting."""
    if strength not in DE_AI_INSTRUCTIONS:
        raise ValueError("de_ai_strength must be 'conservative' or 'moderate'")
    return DE_AI_INSTRUCTIONS[strength]
