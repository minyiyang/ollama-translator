
from book_agent.languages import (
    Language,
    TranslationDirection,
    build_direction_instruction,
)


class LanguageTests:
    def test_language_display_names(self) -> None:
        assert Language.ENGLISH.display_name == "English"
        assert Language.CHINESE.display_name == "Simplified Chinese"

    def test_english_to_chinese_language_pair(self) -> None:
        direction = TranslationDirection.EN_TO_ZH
        assert direction.source_language == Language.ENGLISH
        assert direction.target_language == Language.CHINESE

    def test_chinese_to_english_language_pair(self) -> None:
        direction = TranslationDirection.ZH_TO_EN
        assert direction.source_language == Language.CHINESE
        assert direction.target_language == Language.ENGLISH

    def test_direction_instruction_names_both_languages_and_output_constraint(self) -> None:
        instruction = build_direction_instruction(TranslationDirection.ZH_TO_EN)
        assert "Simplified Chinese" in instruction
        assert "English" in instruction
        assert "Output translated English only" in instruction


