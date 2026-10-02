"""The profiled languages: French and Japanese (docs/GENERIC_LANGUAGES.md, phase 5).

A profile is complete when its number words round-trip and its conventions,
examples, and style-sheet lists are present (section 3). Each check below runs
its rules only for text in the profile's language, so en-zh and zh-en are
unchanged (tests/test_language_golden.py).
"""

from __future__ import annotations

import pytest

from book_agent.audit import AuditCategory, _audit_language
from book_agent.config import AuditConfig
from book_agent.consistency import BookSegment, ConsistencySettings, convention_issues
from book_agent.content_policy import numeric_content_matches, number_tokens
from book_agent.languages import PROFILES, TUNED_PROFILES, LanguagePair, TranslationDirection, profile
from book_agent.number_words import parse_french_number
from book_agent.preprocessing import select_relevant_glossary_entries
from book_agent.schemas import GlossaryEntry
from book_agent.style_sheet import extraction_instructions

PROFILED = sorted(code for code, item in PROFILES.items() if item.tier == "profiled")


def test_french_and_japanese_are_profiled_and_the_tuned_set_is_unchanged():
    assert PROFILED == ["fr", "ja"]
    assert list(TUNED_PROFILES) == ["en", "zh"]
    assert [pair.value for pair in TranslationDirection] == ["en-zh", "zh-en"]


@pytest.mark.parametrize("code", PROFILED)
def test_a_profile_is_complete(code):
    rules = profile(code)
    assert len(rules.marker_examples) == 2 and all("<I000>" in example for example in rules.marker_examples)
    assert rules.quotes and rules.nested_quotes and rules.ellipsis and rules.dash
    assert rules.conventions and rules.stock_phrases and rules.sentence_end
    assert rules.pronouns and rules.address_forms and rules.number_words
    # The examples are written in the language itself.
    assert all(rules.script_pattern.search(example) for example in rules.marker_examples)


# -- number words ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("words", "value"),
    [
        ("trois", 3), ("seize", 16), ("vingt et un", 21), ("trente-deux", 32), ("soixante-dix", 70),
        ("soixante et onze", 71), ("quatre-vingts", 80), ("quatre-vingt-dix-neuf", 99), ("cent", 100),
        ("deux cent cinquante", 250), ("cent quatre-vingts", 180), ("mille deux cents", 1200),
        ("mille neuf cent quatre-vingt-quatre", 1984), ("trois cent mille", 300_000),
        ("deux millions", 2_000_000), ("un milliard", 1_000_000_000), ("septante", 70),
    ],
)
def test_french_number_words_round_trip(words, value):
    assert parse_french_number(words) == value


def test_french_numbers_are_read_only_in_french_text():
    en_fr = LanguagePair("en>fr")
    assert numeric_content_matches("Three hundred soldiers waited.", "Trois cents soldats attendaient.", en_fr)
    assert not numeric_content_matches("Three hundred soldiers waited.", "Quatre cents soldats attendaient.", en_fr)
    assert number_tokens("Cinquante pour cent des soldats.", "fr")["0.5"] == 1
    # "cent" is not a hundred in English, and an article is not a count.
    assert number_tokens("He paid five cent.", "en")["100"] == 0
    assert number_tokens("He paid five cent.")["100"] == 0
    assert number_tokens("Un livre neuf.", "fr") == number_tokens("Un livre neuf.")


def test_japanese_writes_hundred_millions_with_its_own_character():
    en_ja = LanguagePair("en>ja")
    assert number_tokens("三億人", "ja")["300000000"] == 1
    assert numeric_content_matches("Three hundred million people.", "三億人の人々。", en_ja)


# -- conventions ---------------------------------------------------------------------------------


def _segments(*targets):
    return [
        BookSegment(document_id="d", order=index, segment_id=f"S{index}", source="x", target=target)
        for index, target in enumerate(targets)
    ]


def test_french_quotes_and_spacing_follow_the_books_majority():
    nbsp = " "
    book = _segments(
        f"«{nbsp}Bonjour{nbsp}!{nbsp}» dit-elle.",
        f"«{nbsp}Où vas-tu{nbsp}?{nbsp}»",
        f"«{nbsp}Viens{nbsp}!{nbsp}»",
        '"Non !" répondit-il.',
    )
    messages = [issue.message for issue in convention_issues(book, ConsistencySettings(target_language="fr"))]
    assert any("straight quotation mark" in message and "« »" in message for message in messages)
    assert any("no-break space" in message for message in messages)
    assert all(issue.segment_id == "S3" for issue in convention_issues(book, ConsistencySettings(target_language="fr")))


def test_japanese_quotes_and_ellipsis_follow_the_books_majority():
    book = _segments("「そうか……」", "「行こう」", "「待って……」", "“行くぞ…”")
    messages = [issue.message for issue in convention_issues(book, ConsistencySettings(target_language="ja"))]
    assert any("「 」" in message for message in messages) and any("……" in message for message in messages)


def test_a_language_without_conventions_is_not_checked():
    assert convention_issues(_segments('"Hi."', "“Hi.”", "“Yes.”"), ConsistencySettings(target_language="en")) == []


# -- style sheet, matching, untranslated text -----------------------------------------------------


def test_style_sheet_extraction_names_the_targets_pronouns_and_forms_of_address():
    french = extraction_instructions(LanguagePair("en>fr"), max_characters=5, max_expressions=5)
    assert "(il, or elle;" in french and "address them as tu or the polite vous" in french
    japanese = extraction_instructions(LanguagePair("en>ja"), max_characters=5, max_expressions=5)
    assert "(彼, or 彼女;" in japanese and "(さん, 様, 君, or ちゃん)" in japanese


def test_a_french_term_matches_after_an_elided_article():
    fr_en = LanguagePair("fr>en")
    entry = GlossaryEntry.for_pair({"source": "Aster", "target": "Aster", "category": "人名"}, fr_en)
    assert select_relevant_glossary_entries("Le chapeau d'Aster.", [entry], fr_en) == [entry]


def test_french_and_english_share_a_script_so_only_an_identical_long_line_is_untranslated():
    issues = []
    sentence = "The soldiers waited for the order to march."
    _audit_language("S1", sentence, sentence, LanguagePair("en>fr"), AuditConfig(), issues)
    assert [issue.category for issue in issues] == [AuditCategory.UNTRANSLATED]
    issues = []
    _audit_language("S1", sentence, "Les soldats attendaient l'ordre de marcher.", LanguagePair("en>fr"), AuditConfig(), issues)
    assert issues == []
