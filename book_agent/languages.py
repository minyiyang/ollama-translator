"""Supported translation languages and directions."""

from enum import Enum


class Language(str, Enum):
    ENGLISH = "en"
    CHINESE = "zh"

    @property
    def display_name(self) -> str:
        """Return a stable English display name for prompts and reports."""
        return {
            Language.ENGLISH: "English",
            Language.CHINESE: "Simplified Chinese",
        }[self]


class TranslationDirection(str, Enum):
    EN_TO_ZH = "en-zh"
    ZH_TO_EN = "zh-en"

    @property
    def source_language(self) -> Language:
        """Return the configured source language."""
        return {
            TranslationDirection.EN_TO_ZH: Language.ENGLISH,
            TranslationDirection.ZH_TO_EN: Language.CHINESE,
        }[self]

    @property
    def target_language(self) -> Language:
        """Return the configured target language."""
        return {
            TranslationDirection.EN_TO_ZH: Language.CHINESE,
            TranslationDirection.ZH_TO_EN: Language.ENGLISH,
        }[self]


def build_direction_instruction(direction: TranslationDirection) -> str:
    """Build the non-optional language constraint for a translation prompt."""
    source = direction.source_language.display_name
    target = direction.target_language.display_name
    return (
        f"Translate every source passage from {source} into {target}. "
        f"Output translated {target} only, apart from protected structural markers."
    )

