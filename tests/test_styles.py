import tempfile
from pathlib import Path

import pytest

from book_agent.languages import TranslationDirection
from book_agent.styles import (
    DE_AI_INSTRUCTIONS,
    STYLE_INSTRUCTIONS,
    TranslationStyle,
    build_de_ai_instruction,
    build_style_prompt,
    load_style_instruction,
)


class StyleTests:
    def test_every_builtin_style_has_a_nonempty_instruction(self) -> None:
        builtins = set(TranslationStyle) - {TranslationStyle.CUSTOM}
        assert builtins == set(STYLE_INSTRUCTIONS)
        for style in builtins:
            instruction = load_style_instruction(style)
            assert instruction
            assert "Chinese" not in instruction
            assert "English" not in instruction

    def test_custom_style_loads_utf8_and_strips_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "style.txt")
            path.write_text("  使用冷峻克制的中文。 \n", encoding="utf-8")
            assert load_style_instruction(TranslationStyle.CUSTOM, path) == "使用冷峻克制的中文。"

    def test_custom_style_requires_a_file(self) -> None:
        with pytest.raises(ValueError, match="required"):
            load_style_instruction(TranslationStyle.CUSTOM)

    def test_custom_style_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "style.txt")
            path.write_text(" \n", encoding="utf-8")
            with pytest.raises(ValueError, match="empty"):
                load_style_instruction(TranslationStyle.CUSTOM, path)

    def test_builtin_style_rejects_custom_file(self) -> None:
        with pytest.raises(ValueError, match="only"):
            load_style_instruction(TranslationStyle.LITERARY, "unused.txt")

    def test_build_style_prompt_includes_instruction_and_hard_constraints(self) -> None:
        prompt = build_style_prompt("使用自然中文。", TranslationDirection.EN_TO_ZH)
        assert "使用自然中文。" in prompt
        assert "English" in prompt
        assert "Simplified Chinese" in prompt
        assert "do not omit" in prompt
        assert "glossary" in prompt
        assert "Naturalness overlay" in prompt

    def test_build_style_prompt_supports_chinese_to_english(self) -> None:
        prompt = build_style_prompt("Use restrained literary English.", TranslationDirection.ZH_TO_EN)
        assert "from Simplified Chinese into English" in prompt
        assert "Use restrained literary English." in prompt

    def test_build_style_prompt_rejects_empty_instruction(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            build_style_prompt("  ", TranslationDirection.EN_TO_ZH)

    def test_style_prompt_can_disable_de_ai_overlay(self) -> None:
        prompt = build_style_prompt(
            "Natural prose.",
            TranslationDirection.ZH_TO_EN,
            de_ai_enabled=False,
        )
        assert "Naturalness overlay" not in prompt

    def test_de_ai_overlay_supports_bounded_strengths(self) -> None:
        assert build_de_ai_instruction("conservative") == DE_AI_INSTRUCTIONS["conservative"]
        assert "rhetorical triplets" in build_de_ai_instruction("moderate")
        with pytest.raises(ValueError, match="de_ai_strength"):
            build_de_ai_instruction("aggressive")

