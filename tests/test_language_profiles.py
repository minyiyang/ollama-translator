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


def test_the_profiled_languages_and_the_unchanged_tuned_set():
    assert PROFILED == ["de", "es", "fr", "ja", "ko"]
    assert list(TUNED_PROFILES) == ["en", "zh"]
    assert [pair.value for pair in TranslationDirection] == ["en-zh", "zh-en"]


@pytest.mark.parametrize("code", PROFILED)
def test_a_profile_is_complete(code):
    rules = profile(code)
    assert len(rules.marker_examples) == 2 and all("<I000>" in example for example in rules.marker_examples)
    assert rules.quotes and rules.nested_quotes and rules.ellipsis and rules.dash
    assert rules.conventions and rules.stock_phrases and rules.sentence_end
    assert rules.pronouns and rules.number_words
    # Korean marks address by speech level, not by a pronoun pair (docs/GENERIC_LANGUAGES.md, 2.4).
    assert rules.address_forms or code == "ko"
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
    assert {issue.message for issue in issues} == {
        "translation is identical to source",
        f"possible untranslated English passage: {sentence}",
    }
    issues = []
    _audit_language("S1", sentence, "Les soldats attendaient l'ordre de marcher.", LanguagePair("en>fr"), AuditConfig(), issues)
    assert issues == []


# -- found by the benchmarks (runs/bench/language-results.md) --------------------------------------


def test_french_numbers_found_on_la_chasse_au_meteore():
    # Italic English coins, not 600; "un bon mille" is a mile; a time of day is left to the model.
    assert number_tokens("cinq ou six <I000>cents</I000>", "fr")["600"] == 0
    assert number_tokens("six cents soldats", "fr")["600"] == 1
    assert number_tokens("un bon mille aux environs", "fr")["1000"] == 0
    assert number_tokens("deux mille soldats", "fr")["2000"] == 1
    fr_en = LanguagePair("fr>en")
    assert numeric_content_matches("Et voilà que quatre heures et demie vont sonner!", "And four thirty is about to strike!", fr_en)
    assert numeric_content_matches("Onze heures quarante-six, répondit-il.", "Eleven forty-six, he replied.", fr_en)


def test_a_glossary_abbreviation_does_not_match_a_longer_word():
    fr_en = LanguagePair("fr>en")
    mr = GlossaryEntry.for_pair({"source": "Mr", "target": "Mr.", "category": "术语"}, fr_en)
    assert select_relevant_glossary_entries("Mrs Hudelson arriva.", [mr], fr_en) == []
    assert select_relevant_glossary_entries("Mr Dean Forsyth arriva.", [mr], fr_en) == [mr]


def test_a_short_name_may_stay_unchanged_between_languages_sharing_a_script():
    from book_agent.languages import copy_is_untranslated

    assert not copy_is_untranslated("DEAN FORSYTH.", LanguagePair("fr>en"))
    assert copy_is_untranslated("The soldiers waited for the order to march.", LanguagePair("fr>en"))
    assert copy_is_untranslated("Paris.", TranslationDirection.EN_TO_ZH)  # different scripts: always


def test_english_dialogue_left_in_a_french_translation_is_found():
    """Pat's dialect lines in Alice (en>fr benchmark) were kept in English inside « »."""
    en_fr = LanguagePair("en>fr")
    source = "“Sure, it’s an arm, yer honour!” (He pronounced it “arrum.”)"
    issues = []
    _audit_language("S1", source, "« Sure, it’s an arm, yer honour! » (Il prononça « arrum. »)", en_fr, AuditConfig(), issues)
    assert [issue.message for issue in issues] == ["possible untranslated English passage: Sure, it’s an arm, yer honour!"]
    issues = []
    _audit_language("S1", source, "« Bien sûr, c’est un bras, votre honneur ! » (Il prononça « arrum. »)", en_fr, AuditConfig(), issues)
    assert issues == []
    # The other way round: French left in an English translation.
    issues = []
    _audit_language("S1", "« Je ne sais pas », dit-il.", "“Je ne sais pas,” he said.", LanguagePair("fr>en"), AuditConfig(), issues)
    assert any("untranslated French passage" in issue.message for issue in issues)


def test_french_nested_quotes_are_not_a_departure():
    book = _segments("« Viens ! »", "« Va ! »", "« Il a dit : “Mlle Alice ! Viens ici !” »", "“Non”, répondit-il.")
    flagged = {issue.segment_id for issue in convention_issues(book, ConsistencySettings(target_language="fr"))}
    assert flagged == {"S3"}


# -- phase 6: Spanish, German, Korean -------------------------------------------------------------

from book_agent.content_policy import glossary_term_count  # noqa: E402
from book_agent.number_words import (  # noqa: E402
    korean_word_values,
    parse_german_number,
    parse_korean_native,
    parse_korean_sino,
    parse_spanish_number,
)


@pytest.mark.parametrize(
    ("words", "value"),
    [
        ("dos", 2), ("quince", 15), ("veintiuno", 21), ("veintitrés", 23), ("treinta y dos", 32),
        ("cien", 100), ("ciento veinte", 120), ("trescientos cuarenta y dos", 342), ("quinientas", 500),
        ("mil", 1000), ("dos mil quinientos", 2500), ("mil novecientos ochenta y cuatro", 1984),
        ("un millón", 1_000_000), ("tres millones", 3_000_000),
    ],
)
def test_spanish_number_words_round_trip(words, value):
    assert parse_spanish_number(words) == value


@pytest.mark.parametrize(
    ("word", "value"),
    [
        ("zwei", 2), ("zwölf", 12), ("siebzehn", 17), ("einundzwanzig", 21), ("dreißig", 30),
        ("hundert", 100), ("hundertzwei", 102), ("zweihundertdreiundvierzig", 243),
        ("Dreihundertfünfundzwanzig", 325), ("tausend", 1000), ("dreitausendfünfhundert", 3500),
        ("neunzehnhundertvierundachtzig", 1984), ("zwölftausend", 12000),
    ],
)
def test_german_number_words_round_trip(word, value):
    assert parse_german_number(word) == value


def test_korean_numbers_are_read_before_a_counter_only():
    assert parse_korean_sino("삼백오십") == 350 and parse_korean_sino("이천오백") == 2500 and parse_korean_sino("삼만") == 30000
    assert parse_korean_native("스물 두") == 22
    # 일 "work" and 열 "will open" are not numbers; 두 사람, 삼십 전, 일 원 오십 전 are.
    assert list(korean_word_values("두 사람이 삼십 전을 받았다. 일을 했다. 일 원 오십 전. 문을 열 사람.")) == [2, 30, 1, 50]


def test_number_words_are_compared_across_languages():
    assert numeric_content_matches("Ganó dos mil quinientos pesos.", "He earned two thousand five hundred pesos.", LanguagePair("es>en"))
    assert not numeric_content_matches("Ganó dos mil quinientos pesos.", "He earned two thousand pesos.", LanguagePair("es>en"))
    assert numeric_content_matches("Er war dreihundertfünfundzwanzig Tage fort.", "He was gone for three hundred and twenty-five days.", LanguagePair("de>en"))
    assert not numeric_content_matches("Er war dreihundertfünfundzwanzig Tage fort.", "He was gone for three hundred days.", LanguagePair("de>en"))
    # A time of day and an article are not counts.
    assert number_tokens("Llegó a las cinco y media con un perro.", "es") == number_tokens("Llegó a las cinco y media con un perro.")


def test_spanish_questions_and_exclamations_open_with_their_marks():
    book = _segments("¿Vienes?", "¡Qué frío!", "¿Cuándo? —preguntó—. ¡Ahora!", "Vienes?", "Qué frío!")
    found = {issue.segment_id: issue.message for issue in convention_issues(book, ConsistencySettings(target_language="es"))}
    assert found == {
        "S3": "A question here has no opening ¿, but the book writes them (2 segments).",
        "S4": "An exclamation here has no opening ¡, but the book writes them (2 segments).",
    }


def test_german_quotes_follow_the_books_style():
    book = _segments("„Komm!“", "„Geh!“", "„Nein“, sagte er.", "“Ja”, sagte sie.")
    assert [issue.segment_id for issue in convention_issues(book, ConsistencySettings(target_language="de"))] == ["S3"]
    # A book in » « is fine with » «, and only straight quotes depart from it.
    book = _segments("»Komm!«", "»Geh!«", '"Nein", sagte er.')
    assert [issue.segment_id for issue in convention_issues(book, ConsistencySettings(target_language="de"))] == ["S2"]


def test_an_approved_target_term_may_carry_the_languages_endings():
    assert glossary_term_count("Die Beine des Käfers zappelten.", "Käfer", "de") == 1
    assert glossary_term_count("Die Beine des Käfers zappelten.", "Käfer") == 0  # English rules: exact
    assert glossary_term_count("Los caballeros llegaron.", "caballero", "es") == 1
    assert glossary_term_count("Der Käferbein-Abschnitt.", "Käfer", "de") == 0  # inside a longer word


def test_a_korean_term_matches_before_its_particle():
    ko_zh = LanguagePair("ko>zh")
    entry = GlossaryEntry.for_pair({"source": "김첨지", "target": "金添知", "category": "人名"}, ko_zh)
    assert select_relevant_glossary_entries("김첨지는 인력거를 끌었다.", [entry], ko_zh) == [entry]
    assert select_relevant_glossary_entries("어린김첨지", [entry], ko_zh) == []  # not inside a word


def test_english_left_in_german_or_spanish_is_found():
    issues = []
    _audit_language("S1", "“I don’t know what you mean,” he said.", "„I don’t know what you mean“, sagte er.", LanguagePair("en>de"), AuditConfig(), issues)
    assert any("untranslated English passage" in issue.message for issue in issues)
    issues = []
    _audit_language("S1", "“Was it you?”", "„Warst du es?“", LanguagePair("en>de"), AuditConfig(), issues)
    assert issues == []  # "was" is German too, so it tells nothing
    issues = []
    _audit_language("S1", "“I have seen it,” she said.", "—Lo he visto —dijo ella.", LanguagePair("en>es"), AuditConfig(), issues)
    assert issues == []  # Spanish "he" (I have) is not English "he"


# -- phase 6 benchmarks: what Die Verwandlung, Fortunata y Jacinta, 운수 좋은 날 and Alice showed ------

from book_agent.glossary import is_suspicious_generic_candidate  # noqa: E402
from book_agent.glossary_prompts import build_extraction_prompt  # noqa: E402
from book_agent.repair import apply_convention_replacements, apply_deterministic_repairs  # noqa: E402
from book_agent.style_sheet import StyleExpression, StyleSheet, drop_glossary_conflicts  # noqa: E402


def test_a_one_syllable_korean_term_is_not_found_inside_another_word():
    ko_zh = LanguagePair("ko>zh")
    porridge = GlossaryEntry.for_pair({"source": "죽", "target": "粥", "category": "物品"}, ko_zh)
    liver = GlossaryEntry.for_pair({"source": "간", "target": "肝", "category": "其他"}, ko_zh)
    rickshaw = GlossaryEntry.for_pair({"source": "인력거", "target": "人力车", "category": "物品"}, ko_zh)
    entries = [porridge, liver, rickshaw]
    assert select_relevant_glossary_entries("“우리 마누라가 죽었다네.”", entries, ko_zh) == []  # died
    assert select_relevant_glossary_entries("간신히 그의 귀에 들어왔다.", entries, ko_zh) == []  # barely
    assert {e.source for e in select_relevant_glossary_entries("죽을 먹고 간이며 콩팥이며", entries, ko_zh)} == {"죽", "간"}
    assert select_relevant_glossary_entries("인력거에서는 내렸다.", entries, ko_zh) == [rickshaw]  # two particles
    assert select_relevant_glossary_entries("인력거꾼 노릇", entries, ko_zh) == []  # the rickshaw man: another word


def test_every_word_of_a_german_or_spanish_term_inflects():
    assert glossary_term_count("Es war der Weiße Hase.", "Weißer Hase", "de") == 1
    assert glossary_term_count("die Uhr des Weißen Hasen", "Weißer Hase", "de") == 1
    assert glossary_term_count("ein weißer Hund", "Weißer Hase", "de") == 0
    assert glossary_term_count("Ja, Euer Ehren!", "Euer Ehren", "de") == 1
    assert glossary_term_count("los Conejos Blancos", "Conejo Blanco", "es") == 1
    assert glossary_term_count("Mrs Smith", "Mr", "de") == 0
    de_en = LanguagePair("de>en")
    rabbit = GlossaryEntry.for_pair({"source": "Weißer Hase", "target": "White Rabbit", "category": "人名"}, de_en)
    assert select_relevant_glossary_entries("die Uhr des Weißen Hasen", [rabbit], de_en) == [rabbit]


def test_a_spanish_sentence_is_opened_by_either_mark_and_through_a_dash_or_a_tag():
    book = _segments(
        "¿Vienes?",
        "¡Qué frío!",
        "¿Por qué, apenas hay espacio para ti!",
        "«¿Te gustan —te gustan —los perros?»",
        "«¿Sería útil», pensó Alicia, «hablar con este ratón?»",
        "¿Y si… no viene?",
        "«Claro, es un brazo, su honor!»",
    )
    found = convention_issues(book, ConsistencySettings(target_language="es"))
    assert [issue.segment_id for issue in found] == ["S6"]


def test_a_line_quoted_from_the_source_keeps_its_own_punctuation():
    book = [
        BookSegment("d", 0, "S0", "“Where?”", "«¿Dónde?»"),
        BookSegment("d", 1, "S1", "So she began again: “Où est ma chatte?”", "Así que comenzó de nuevo: «Où est ma chatte?»"),
        BookSegment("d", 2, "S2", "“Who?”", "«Quién?»"),
    ]
    found = convention_issues(book, ConsistencySettings(target_language="es"))
    assert [issue.segment_id for issue in found] == ["S2"]


def test_a_german_closing_quote_is_corrected_without_a_model():
    book = _segments("„Komm!“", "„Geh!“", "„Was gefunden?” fragte die Ente.")
    issues = convention_issues(book, ConsistencySettings(target_language="de"))
    assert [issue.segment_id for issue in issues] == ["S2"]
    assert apply_deterministic_repairs("„Was gefunden?” fragte die Ente.", issues, "de") == (
        "„Was gefunden?“ fragte die Ente.",
        ["convention_replacement"],
    )
    # With another finding the model repairs the segment; its answer is corrected too.
    other = issues[0].model_copy(update={"source": "semantic", "suggested_fix": "Use „fragte“."})
    assert apply_deterministic_repairs("„Was?” sagte sie.", [issues[0], other], "de") == ("„Was?” sagte sie.", [])
    assert apply_convention_replacements("„Was?” fragte sie.", [issues[0], other], "de") == "„Was?“ fragte sie."
    # A straight quote could open or close: that stays with the model.
    straight = convention_issues(_segments("„Komm!“", "„Geh!“", '"Nein", sagte er.'), ConsistencySettings(target_language="de"))
    assert apply_deterministic_repairs('"Nein", sagte er.', straight, "de") == ('"Nein", sagte er.', [])


def test_words_in_a_third_script_are_found():
    en_ko = LanguagePair("en>ko")
    issues = []
    _audit_language("S1", "how to set about it", "그녀가 어떻게着手해야 할지 몰랐다.", en_ko, AuditConfig(), issues)
    assert [issue.message for issue in issues] == ["text in a script neither language uses: 着手"]
    issues = []
    _audit_language("S1", "his creed (信條)", "그의 신조(信條)에", en_ko, AuditConfig(), issues)
    assert issues == []  # the source wrote it
    issues = []
    _audit_language("S1", "He said so.", "彼はそう言った。", LanguagePair("en>ja"), AuditConfig(), issues)
    assert issues == []  # Japanese is written in Han too


def test_a_lowercase_word_is_no_name_and_a_korean_syllable_is_an_everyday_word():
    en_es = LanguagePair("en>es")
    well = GlossaryEntry.for_pair({"source": "well", "target": "pozo", "category": "地名"}, en_es)
    alice = GlossaryEntry.for_pair({"source": "Alice", "target": "Alicia", "category": "人名"}, en_es)
    assert is_suspicious_generic_candidate(well, "en") and not is_suspicious_generic_candidate(alice, "en")
    assert not is_suspicious_generic_candidate(well)  # the en/zh screen keeps its own rule
    es_en = LanguagePair("es>en")
    title = GlossaryEntry.for_pair({"source": "señorito", "target": "young master", "category": "人名"}, es_en)
    cloth = GlossaryEntry.for_pair({"source": "crespón", "target": "crêpe", "category": "物品"}, es_en)
    assert not is_suspicious_generic_candidate(title, "es") and is_suspicious_generic_candidate(cloth, "es")
    ko_zh = LanguagePair("ko>zh")
    porridge = GlossaryEntry.for_pair({"source": "죽", "target": "粥", "category": "物品"}, ko_zh)
    soup = GlossaryEntry.for_pair({"source": "설렁탕", "target": "牛骨汤", "category": "物品"}, ko_zh)
    assert is_suspicious_generic_candidate(porridge, "ko") and not is_suspicious_generic_candidate(soup, "ko")


def test_a_style_expression_does_not_contradict_the_glossary():
    sheet = StyleSheet(expressions=[
        StyleExpression(source="Herr Prokurist", rendering="the Prokurist"),
        StyleExpression(source="Herr Samsa", rendering="Mr. Samsa"),
        StyleExpression(source="Himmlischer Vater!", rendering="Heavenly Father!"),
    ])
    terms = [("Prokurist", "chief clerk"), ("Vater", "father"), ("Vater", "Father")]
    kept = drop_glossary_conflicts(sheet, terms, "en")
    assert [item.source for item in kept.expressions] == ["Herr Samsa", "Himmlischer Vater!"]


def test_the_extraction_prompt_says_german_nouns_are_capitalized():
    from book_agent.glossary import GlossaryChunk

    chunk = GlossaryChunk(chunk_id="c", pieces=[], estimated_tokens=0)
    assert "German capitalizes every noun" in build_extraction_prompt(chunk, LanguagePair("de>en"))
    assert "capitalizes every noun" not in build_extraction_prompt(chunk, LanguagePair("fr>en"))


def test_a_translation_model_takes_the_translate_stage_only():
    from book_agent.config import AppConfig, translation_model
    from book_agent.web.setup import model_roles

    plain = AppConfig()
    assert translation_model(plain) == plain.ollama.model
    assert "model" not in plain.model_dump(mode="json")["translation"]  # stored configs and hashes unchanged
    config = AppConfig.model_validate({"translation": {"model": "translategemma:27b"}})
    assert translation_model(config) == "translategemma:27b"
    roles = {role["role"]: role["model"] for role in model_roles(config)}
    assert roles["translation"] == "translategemma:27b"
    assert roles["glossary resolution/approval"] == config.ollama.model
    with pytest.raises(ValueError):
        AppConfig.model_validate({"translation": {"model": " "}})


def test_a_passage_repeated_under_a_later_marker_fails_the_translation_contract():
    from book_agent.translation import TranslationChunk, TranslationChunkPiece, validate_translation_output

    sources = {
        "S1": "Er glitt wieder in seine frühere Lage zurück und dachte lange über das frühe Aufstehen nach.",
        "S2": "Und er sah zur Weckuhr hinüber, die auf dem Kasten tickte, und erschrak sehr.",
        "S3": "Schon war er so weit, daß er bei stärkerem Schaukeln kaum das Gleichgewicht noch erhielt.",
        "S4": "»Ja,« sagte er.",
        "S5": "»Ja,« sagte sie.",
    }
    chunk = TranslationChunk(
        chunk_id="c", document_id="d", document_order=0, estimated_source_tokens=0,
        pieces=[TranslationChunkPiece(reference_id=k, segment_id=k, part_number=1, source_text=v) for k, v in sources.items()],
    )
    first = "He slid back into his former position and thought for a long time about getting up early."
    second = "And he looked over at the alarm clock, which was ticking on the chest, and was very startled."
    looped = f"<S1>{first}</S1><S2>{second}</S2><S3>{first}</S3><S4>“Yes,” he said.</S4><S5>“Yes,” he said.</S5>"
    _, validation = validate_translation_output(looped, chunk, LanguagePair("de>en"))
    assert [(issue.code, issue.reference_id) for issue in validation.issues] == [("repeated_passage", "S3")]
    third = "He had already got so far that, rocking harder, he could hardly keep his balance."
    good = looped.replace(f"<S3>{first}", f"<S3>{third}")
    assert validate_translation_output(good, chunk, LanguagePair("de>en"))[1].passed  # a short line may repeat


def test_a_passage_translated_under_its_neighbours_marker_is_found():
    from book_agent.translation import misplaced_passages

    next_source = "“I wish I hadn’t cried so much!” said Alice, as she swam about, trying to find her way out."
    passages = [
        ("S1", "As she said these words her foot slipped, and in another moment, splash! she was up to her chin in salt water.",
         "“나는 너무 많이 울지 않았으면 좋았을 텐데!” 앨리스는 헤엄쳐서 길을 찾으려고 하면서 말했다."),
        ("S2", next_source, "“그렇게 많이 울지 말았어야 했는데!” 앨리스는 길을 찾으려고 헤엄치며 말했다."),
        ("S3", "Just then she heard something splashing about in the pool a little way off.",
         "바로 그때 그녀는 조금 떨어진 곳에서 무언가 첨벙거리는 소리를 들었다."),
    ]
    assert misplaced_passages(passages) == [("S1", "S2"), ("S2", "S1")]  # which of the two is wrong, the text cannot say
    passages[0] = ("S1", passages[0][1], "그녀가 이 말을 하는 순간 발이 미끄러졌고, 다음 순간 첨벙! 턱까지 소금물에 잠겼다.")
    assert misplaced_passages(passages) == []
    # The same sentence twice in the source may be translated twice alike.
    assert misplaced_passages([("S1", next_source, passages[1][2]), ("S2", next_source, passages[1][2])]) == []


def test_a_translation_left_in_the_source_language_fails_the_translation_contract():
    from book_agent.languages import reads_as_source
    from book_agent.translation import TranslationChunk, TranslationChunkPiece, validate_translation_output

    source = "Gregor sah ein, daß er den Prokuristen in dieser Stimmung auf keinen Fall weggehen lassen dürfe, wenn seine Stellung nicht gefährdet werden sollte."
    copied = source.replace("daß", "dass").replace("dürfe", "durfte")  # respelled: not identical to the source
    de_en = LanguagePair("de>en")
    assert reads_as_source(copied, de_en)
    assert not reads_as_source("Gregor realized that he must not let the chief clerk go in this mood, if his position was not to be endangered.", de_en)
    assert not reads_as_source("“Was it you?” he said, and was gone.", de_en)  # too little to tell
    assert not reads_as_source(copied, LanguagePair("de>zh"))  # the script tells these apart; the audit's rule applies
    chunk = TranslationChunk(
        chunk_id="c", document_id="d", document_order=0, estimated_source_tokens=0,
        pieces=[TranslationChunkPiece(reference_id="S1", segment_id="S1", part_number=1, source_text=source)],
    )
    _, validation = validate_translation_output(f"<S1>{copied}</S1>", chunk, de_en)
    assert [issue.code for issue in validation.issues] == ["untranslated_source_language"]


def test_a_run_of_translations_one_marker_late_is_found():
    from book_agent.translation import shifted_passages

    sources = [
        "阿Q没有家，住在未庄的土谷祠里；也没有固定的职业，只给人家做短工，割麦便割麦，舂米便舂米，撑船便撑船。",
        "『阿Q真能做！』",
        "这时阿Q赤着膊，懒洋洋的瘦伶仃的正在他面前，别人也摸不着这话是真心还是讥笑，然而阿Q很喜欢。",
        "『我们先前——比你阔的多啦！你算是什么东西！』",
        "阿Q又很自尊，所有未庄的居民，全不在他眼睛里，甚而至于对于两位『文童』也有以为不值一笑的神情。",
        "『哙，亮起来了。』",
        "阿Q照例的发了怒，他怒目而视了。",
        "『原来有保险灯在这里！』他们并不怕。",
        "阿Q没有法，只得另外想出报复的话来：",
        "『你还不配……』",
    ]
    right = [
        "Ah Q had no home and lived in the Tutelary God's Temple at Weizhuang; he had no regular work either, doing odd jobs for others: cutting wheat, hulling rice, punting a boat.",
        "“Ah Q is a real worker!”",
        "Ah Q, bare to the waist, listless and lean, was standing right in front of him; nobody could tell whether the remark was meant or mocking, but Ah Q was pleased.",
        "“We used to be much better off than you! Who do you think you are?”",
        "Ah Q was proud, too: he looked down on all the people of Weizhuang, and even thought the two young scholars not worth a smile.",
        "“Hey, it's lighting up.”",
        "Ah Q flew into a rage as usual and glared at them.",
        "“So there is a paraffin lamp here!” They were not afraid.",
        "Ah Q could do nothing but think of another retort:",
        "“You don't even deserve…”",
    ]
    ids = [f"S{index}" for index in range(len(sources))]
    assert shifted_passages(list(zip(ids, sources, right))) == []
    # The model skipped the first passage: every translation stands one marker early.
    # After ten passages in place, the model skips one: each later translation
    # stands one marker early. (The usual length ratio comes from the chunk.)
    late = right[1:] + ["Ah Q thought these were scars of honour."]
    more = [f"T{index}" for index in range(len(sources))]
    found = shifted_passages(list(zip(ids + more, sources + sources, right + late)))
    assert {offset for _, offset in found} == {1} and len(found) >= 5
    assert all(key.startswith("T") for key, _ in found)
    # Too few passages to tell a usual length from an unusual one.
    assert shifted_passages(list(zip(ids[:3], sources[:3], late[:3]))) == []


def test_chinese_left_inside_a_japanese_translation_is_found():
    from book_agent.languages import copied_source_run

    zh_ja = LanguagePair("zh>ja")
    source = "阿Q以为他要逃了，抢进去就是一拳。这拳头还未达到身上，已经被他抓住了。"
    left = "阿Qは彼が逃げると思った、抢进去就是一拳。この拳はまだ身に達していないのに、すでに彼に掴まれた。"
    done = "阿Qは彼が逃げようとしていると思い、飛びかかって一発殴りつけた。しかしその拳は届く前に捕らえられた。"
    assert copied_source_run(source, left, zh_ja) == "抢进去就是一拳"
    assert copied_source_run(source, done, zh_ja) == ""
    assert copied_source_run("土谷祠", "阿Qは土谷祠に住んでいる。", zh_ja) == ""  # a name both languages write alike
    assert copied_source_run(source, left, LanguagePair("zh>fr")) == ""  # the script tells these apart already
    issues = []
    _audit_language("S1", source, left, zh_ja, AuditConfig(), issues)
    assert [issue.message for issue in issues] == ["possible untranslated Simplified Chinese text: 抢进去就是一拳"] or \
        [issue.message for issue in issues] == ["possible untranslated Chinese text: 抢进去就是一拳"]
    issues = []
    _audit_language("S1", source, done, zh_ja, AuditConfig(), issues)
    assert issues == []


def test_a_japanese_translation_with_no_kana_is_untranslated():
    from book_agent.languages import lacks_target_script

    zh_ja = LanguagePair("zh>ja")
    assert lacks_target_script("「和尚动得，我动不得？」他扭住她的面颊。", zh_ja)
    assert not lacks_target_script("「和尚は触れてよくて、私は駄目なのか？」彼は彼女の頬をつねった。", zh_ja)
    assert not lacks_target_script("第一章 序", zh_ja)  # a heading both languages write alike
    assert not lacks_target_script("Chapter one, in which nothing is translated", LanguagePair("en>ja"))  # the script check's case
    assert not lacks_target_script("他扭住她的面颊，她大叫着往外跑。", LanguagePair("ja>zh"))  # Chinese has no script of its own
    issues = []
    _audit_language("S1", "『和尚动得，我动不得？』他扭住伊的面颊。", "「和尚动得，我动不得？」他扭住她的面颊。", zh_ja, AuditConfig(), issues)
    assert any(issue.severity.value == "high" and "contains no Japanese text" in issue.message for issue in issues)


def test_the_audit_model_reads_every_segment_of_a_pair_other_than_english_and_chinese():
    from book_agent.audit import select_semantic_audit_candidates
    from book_agent.preprocessing import PreprocessedDocument, PreprocessedSegment

    document = PreprocessedDocument(
        order=0, manifest_id="m", archive_path="a", source_sha256="s",
        segments=[
            PreprocessedSegment(segment_id=f"S{index}", original_text="『阿Q真能做！』", processed_text="『阿Q真能做！』")
            for index in range(60)
        ],
    )
    config = AuditConfig()
    assert select_semantic_audit_candidates(document, [], config) == []  # short lines, no finding: the en/zh rule
    every = select_semantic_audit_candidates(document, [], config, every_segment=True)
    assert len(every) == 60  # and not cut at max_semantic_candidates_per_document (50)


def test_a_short_line_of_dialogue_is_prose_not_a_heading():
    from book_agent.content_policy import SegmentKind, classify_segment

    zh_ja, en_de = LanguagePair("zh>ja"), LanguagePair("en>de")
    prose = [
        ("『我们的少奶奶是八月里要生孩子了……』", "「うちの若奥様は八月にお子さんが生まれるのよ……」", zh_ja),
        ("『你出去！』", "「出て行け！」", zh_ja),
        ("他迎上去，大声的吐一口唾沫：", "彼は歩み寄り、大きく唾を吐いた。", zh_ja),
        ("“Not I!”", "„Ich nicht!“", en_de),
    ]
    for source, target, pair in prose:
        assert classify_segment(source, target, pair, []) is SegmentKind.PROSE, source
    headings = [("第一章 序", "第一章 序", zh_ja), ("Down the Rabbit-Hole", "Hinab in den Kaninchenbau", en_de)]
    for source, target, pair in headings:
        assert classify_segment(source, target, pair, []) is SegmentKind.STRUCTURAL, source


def test_a_translation_with_almost_none_of_its_passages_terms_is_another_passages():
    from book_agent.audit import WHOLE_TRANSLATION_WRONG, _audit_glossary
    from book_agent.preprocessing import PreprocessedDocument, PreprocessedSegment
    from book_agent.repair import requires_full_segment_translation

    zh_ja = LanguagePair("zh>ja")
    terms = [("阿Q", "阿Q"), ("断子绝孙", "断子絶孫"), ("圣经贤传", "聖経賢伝"), ("若敖", "若敖"), ("不孝有三", "不孝に三あり")]
    glossary = [GlossaryEntry.for_pair({"source": s, "target": t, "category": "人名" if s == "阿Q" else "术语"}, zh_ja) for s, t in terms]
    source = "阿Q想：断子绝孙便没有人供一碗饭。夫不孝有三无后为大，而若敖之鬼馁而，所以他那思想是合于圣经贤传的。"
    document = PreprocessedDocument(
        order=0, manifest_id="m", archive_path="a", source_sha256="s", glossary_pair=zh_ja, relevant_glossary=glossary,
        segments=[PreprocessedSegment(segment_id="S1", original_text=source, processed_text=source)],
    )
    wrong = "その日、阿Qは、趙太爺の家で、一日中、米を舂いていた。夕食後、彼は台所に座ってタバコを吸っていた。"
    issues = []
    _audit_glossary("S1", source, wrong, document, zh_ja, issues)
    found = [issue for issue in issues if issue.severity.value == "high"]
    assert [issue.message for issue in found] == [
        "translation may be another passage's, or leaves most of this one out: it has 1 of the 5 approved terms in this passage"
    ]
    assert requires_full_segment_translation(found)  # translated again, not edited
    right = "阿Qは思った。断子絶孫となれば飯を供える者もない。不孝に三あり、若敖の鬼も飢える。彼の考えは聖経賢伝に適っていた。"
    issues = []
    _audit_glossary("S1", source, right, document, zh_ja, issues)
    assert not [issue for issue in issues if issue.severity.value == "high"]
    # Every finding that condemns the whole translation, in any target language.
    for message in ("translation contains no Japanese text", "translation is identical to source",
                    "translation fits the source segment 1 later, not this one: the translations here appear shifted"):
        assert WHOLE_TRANSLATION_WRONG.match(message), message
    assert not WHOLE_TRANSLATION_WRONG.match("approved glossary term was not preserved: 阿Q")


def test_a_retranslation_is_not_held_to_the_wrong_passages_terms():
    from book_agent.audit import AuditIssue, AuditSeverity
    from book_agent.config import AppConfig
    from book_agent.repair import validate_repair_output

    config = AppConfig.model_validate({"translation": {"direction": "zh>ja"}})
    zh_ja = config.translation.direction
    glossary = [GlossaryEntry.for_pair({"source": "阿Q", "target": "阿Q", "category": "人名"}, zh_ja)]
    source = "阿Q的耳朵里又听到这句话。他想：不错，应该有一个女人。"
    wrong = "その日、阿Qは一日中、米を舂いていた。夕食後、阿Qは台所に座り、阿Qはタバコを吸っていた。"
    right = "阿Qの耳にまたこの言葉が聞こえた。彼は思った。そうだ、女が一人いるべきだ。"
    finding = AuditIssue(
        segment_id="S1", category=AuditCategory.MISTRANSLATION, severity=AuditSeverity.HIGH,
        message="translation may be another passage's, or leaves most of this one out: it has 1 of the 6 approved terms in this passage",
    )
    # 阿Q stands three times in the wrong text and once in the right one: no fault of the repair.
    _, validation = validate_repair_output(f"<S1>{right}</S1>", source, "S1", wrong, config, glossary, trigger_issues=[finding])
    assert validation.passed, validation.issues
    # An ordinary repair is still held to the terms of the text it edits: it may not lose the name.
    other = finding.model_copy(update={"message": "The meaning differs.", "source": "semantic"})
    nameless = "彼の耳にまたこの言葉が聞こえた。彼は思った。そうだ、女が一人いるべきだ。"
    _, validation = validate_repair_output(f"<S1>{nameless}</S1>", source, "S1", wrong, config, glossary, trigger_issues=[other])
    assert [issue.code for issue in validation.issues] == ["glossary_integrity"]


def test_a_repair_may_bring_a_terms_count_to_the_sources_outside_en_zh():
    from book_agent.content_policy import repair_preserves_glossary

    en_de = LanguagePair("en>de")
    rabbit = GlossaryEntry.for_pair({"source": "White Rabbit", "target": "Weißes Kaninchen", "category": "人名"}, en_de)
    other = GlossaryEntry.for_pair({"source": "White Rabbit", "target": "Weißer Hase", "category": "人名"}, en_de)
    source = "It was the White Rabbit, trotting slowly back again."
    accepted = "Es war das Weiße Kaninchen; das Weiße Kaninchen trottete langsam zurück."  # the name twice for the source's once
    once = "Es war das Weiße Kaninchen, das langsam zurücktrottete."
    assert repair_preserves_glossary(source, accepted, once, en_de, [rabbit])
    assert repair_preserves_glossary(source, accepted, "Es war der Weiße Hase, der langsam zurücktrottete.", en_de, [rabbit, other])  # another approved rendering
    assert not repair_preserves_glossary(source, accepted, "Es war es, das langsam zurücktrottete.", en_de, [rabbit])  # dropped altogether
    assert not repair_preserves_glossary(source, once, accepted + " Das Weiße Kaninchen!", en_de, [rabbit])  # more than either
    # en/zh keeps its own rule: never fewer than the accepted text.
    en_zh = TranslationDirection.EN_TO_ZH
    entry = GlossaryEntry(english="White Rabbit", chinese="白兔", category="人名")
    assert not repair_preserves_glossary(source, "那是白兔；白兔慢慢地小跑回来。", "那是白兔，慢慢地小跑回来。", en_zh, [entry])


def test_feedback_repair_uses_the_repair_model_and_the_audit_schema_has_no_library_pattern():
    import json

    from book_agent.audit import SemanticAuditResult
    from book_agent.config import AppConfig
    from book_agent.stages.repair_review import _feedback_repair_model

    assert _feedback_repair_model(AppConfig()) == AppConfig().ollama.model
    config = AppConfig.model_validate({"audit": {"repair_model": "gemma4:31b"}})
    assert _feedback_repair_model(config) == "gemma4:31b"
    assert "(?!" not in json.dumps(SemanticAuditResult.model_json_schema())


def test_a_translation_far_from_the_usual_length_is_found():
    from book_agent.audit import WHOLE_TRANSLATION_WRONG
    from book_agent.translation import (
        TranslationChunk, TranslationChunkPiece, unusual_length_passages, validate_translation_output,
    )

    sentence = "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do. "
    korean = "앨리스는 강둑에서 언니 곁에 앉아 아무 할 일도 없는 것이 몹시 지루해지기 시작했다. "
    passages = [(f"S{index}", sentence * (1 + index % 3), korean * (1 + index % 3)) for index in range(6)]
    assert unusual_length_passages(passages) == []
    # A paragraph of three sentences answered with part of one.
    passages[5] = ("S5", sentence * 3, "그녀가 이 말을 하자 발이 미끄러졌다.")
    found = unusual_length_passages(passages)
    assert [key for key, _ in found] == ["S5"] and found[0][1] < 0.4
    assert unusual_length_passages(passages[:3]) == []  # too few to know what is usual
    en_ko = LanguagePair("en>ko")
    chunk = TranslationChunk(
        chunk_id="c", document_id="d", document_order=0, estimated_source_tokens=0,
        pieces=[TranslationChunkPiece(reference_id=k, segment_id=k, part_number=1, source_text=s) for k, s, _ in passages],
    )
    output = "".join(f"<{k}>{t}</{k}>" for k, _, t in passages)
    codes = [(i.code, i.reference_id) for i in validate_translation_output(output, chunk, en_ko)[1].issues]
    assert ("unusual_length", "S5") in codes
    # en/zh keeps its fixed token ratio in the audit and has no such contract failure.
    assert not [i for i in validate_translation_output(output, chunk, TranslationDirection.EN_TO_ZH)[1].issues if i.code == "unusual_length"]
    assert WHOLE_TRANSLATION_WRONG.match("translation length is far from usual here: 0.17 of the usual length for its source")
