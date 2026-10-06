"""The model-free checks on languages that have no profile (the generic tier).

Each language below is paired with English, both ways, and with Russian: the
translation contract, the audit's language rules, glossary matching on either
side, and numbers. None of this needs a model. The sentences are one English
sentence and its translation.
"""

from __future__ import annotations

import pytest

from book_agent.audit import _audit_language
from book_agent.config import AuditConfig
from book_agent.content_policy import glossary_target_matches, glossary_term_count, numeric_content_matches
from book_agent.languages import (
    SCRIPTS,
    LanguagePair,
    build_direction_instruction,
    compares_source_words,
    language_support,
    profile,
    source_worded_passage,
)
from book_agent.preprocessing import ReplacementRule, _rule_pattern, select_relevant_glossary_entries
from book_agent.schemas import GlossaryEntry
from book_agent.translation import TranslationChunk, TranslationChunkPiece, validate_translation_output

EN = "Alice opened the small door at 3 o'clock and saw 12 gardeners in the garden of the Queen."
# language: (translation of EN, name "Alice", a longer word containing the name's letters, word for "garden", local digits 3 and 12)
SAMPLES = {
    "ru": ("Алиса открыла маленькую дверь в 3 часа и увидела 12 садовников в саду Королевы.", "Алиса", "Алисания", "сад", None),
    "ar": ("فتحت أليس الباب الصغير في الساعة 3 ورأت 12 بستانيًا في حديقة الملكة.", "أليس", "أليسون", "حديقة", ("٣", "١٢")),
    "fa": ("آلیس در ساعت 3 در کوچک را باز کرد و 12 باغبان را در باغ ملکه دید.", "آلیس", "آلیسون", "باغ", ("۳", "۱۲")),
    "he": ("אליס פתחה את הדלת הקטנה בשעה 3 וראתה 12 גננים בגן של המלכה.", "אליס", "אליסון", "גן", None),
    "hi": ("ऐलिस ने 3 बजे छोटा दरवाज़ा खोला और रानी के बगीचे में 12 माली देखे।", "ऐलिस", "ऐलिसन", "बगीचे", ("३", "१२")),
    "bn": ("অ্যালিস 3 টায় ছোট দরজাটি খুলল এবং রানির বাগানে 12 জন মালী দেখল।", "অ্যালিস", "অ্যালিসন", "বাগানে", ("৩", "১২")),
    "th": ("อลิซเปิดประตูบานเล็กตอน 3 นาฬิกา และเห็นคนสวน 12 คนในสวนของราชินี", "อลิซ", "อลิซาเบธ", "สวน", ("๓", "๑๒")),
    "el": ("Η Αλίκη άνοιξε τη μικρή πόρτα στις 3 η ώρα και είδε 12 κηπουρούς στον κήπο της Βασίλισσας.", "Αλίκη", "Αλίκηδες", "κήπο", None),
    "ka": ("ალისამ 3 საათზე პატარა კარი გააღო და დედოფლის ბაღში 12 მებაღე დაინახა.", "ალისამ", "ალისამაც", "ბაღში", None),
    "hy": ("Ալիսը ժամը 3-ին բացեց փոքր դուռը և թագուհու այգում տեսավ 12 այգեպան։", "Ալիսը", "Ալիսըներ", "այգում", None),
    "am": ("አሊስ በ3 ሰዓት ትንሹን በር ከፈተች እና በንግሥቲቱ የአትክልት ስፍራ 12 አትክልተኞችን አየች።", "አሊስ", "አሊስያ", "ስፍራ", None),
    "vi": ("Alice mở cánh cửa nhỏ lúc 3 giờ và thấy 12 người làm vườn trong khu vườn của Nữ hoàng.", "Alice", "Aliceson", "vườn", None),
    "tr": ("Alice saat 3'te küçük kapıyı açtı ve Kraliçe'nin bahçesinde 12 bahçıvan gördü.", "Alice", "Aliceson", "bahçesinde", None),
    "pl": ("Alicja otworzyła małe drzwi o 3 i zobaczyła 12 ogrodników w ogrodzie Królowej.", "Alicja", "Alicjanka", "ogrodzie", None),
    "sr-Cyrl": ("Алиса је отворила мала врата у 3 сата и видела 12 баштована у врту Краљице.", "Алиса", "Алисанија", "врту", None),
    "km": ("អាលីសបានបើកទ្វារតូចនៅម៉ោង 3 ហើយបានឃើញអ្នកថែសួន 12 នាក់នៅក្នុងសួនរបស់ម្ចាស់ក្សត្រី។", "អាលីស", "អាលីសា", "សួន", ("៣", "១២")),
}

RESPELLED = EN.replace("small", "little")
LEFT = "And saw twelve gardeners in the garden of the Queen."


def _codes(output, source, pair, glossary=None):
    chunk = TranslationChunk(
        chunk_id="c", document_id="d", document_order=0, estimated_source_tokens=0,
        pieces=[TranslationChunkPiece(reference_id="S1", segment_id="S1", part_number=1, source_text=source)],
    )
    return [issue.code for issue in validate_translation_output(f"<S1>{output}</S1>", chunk, pair, glossary)[1].issues]


def _audit(source, target, pair, glossary=()):
    issues = []
    _audit_language("S1", source, target, pair, AuditConfig(), issues, glossary)
    return [issue.message for issue in issues]


@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_the_translation_contract_and_the_audit_tell_a_translation_from_its_source(code):
    text = SAMPLES[code][0]
    into, out = LanguagePair(f"en>{code}"), LanguagePair(f"{code}>en")
    assert _codes(text, EN, into) == [] and _codes(EN, text, out) == []
    assert _audit(EN, text, into) == [] and _audit(text, EN, out) == []
    # The source handed back, as it was or with a word changed.
    assert _codes(EN, EN, into) and _codes(text, text, out)
    assert _codes(RESPELLED, EN, into)
    assert _audit(EN, EN, into) and _audit(text, text, out) and _audit(EN, RESPELLED, into)
    # A translation that goes back to the source language part of the way through.
    half = text[: len(text) // 2]
    assert _audit(EN.replace("12", "twelve"), f"{half} {LEFT}", into)
    assert _audit(EN.replace("12", "twelve"), f"{half} {LEFT[0].lower()}{LEFT[1:]}", into)
    assert isinstance(build_direction_instruction(into), str) and language_support(out)["source"]["tier"] == "generic"


@pytest.mark.parametrize("code", sorted(set(SAMPLES) - {"ru"}))
def test_two_languages_without_a_profile(code):
    text, russian = SAMPLES[code][0], SAMPLES["ru"][0]
    pair = LanguagePair(f"ru>{code}")
    assert _codes(text, russian, pair) == [] and _audit(russian, text, pair) == []
    assert _codes(russian, russian, pair) and _audit(russian, russian, pair)


@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_a_glossary_term_is_matched_as_a_word(code):
    text, name, longer, _, _ = SAMPLES[code]
    rules = profile(code)
    out = LanguagePair(f"{code}>en")
    entry = GlossaryEntry.for_pair({"source": name, "target": "Ellis", "category": "人名"}, out)
    assert select_relevant_glossary_entries(text, [entry], out) == [entry]
    pattern = _rule_pattern(ReplacementRule(name, "Ellis", "Ellis", rules.code))
    assert name not in pattern.sub("Ellis", text) and "Ellis" in pattern.sub("Ellis", text)
    if rules.spaced_words:  # where words are not set apart, a term is found wherever it stands
        other = text.replace(name, longer)
        assert select_relevant_glossary_entries(other, [entry], out) == []
        assert pattern.sub("Ellis", other) == other
    # The term on the target side: kept, counted, and missed when it is gone.
    assert glossary_target_matches(text, name, rules.code) and glossary_term_count(text, name, rules.code) == 1
    assert not glossary_target_matches(text.replace(name, "X"), name, rules.code)


@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_numbers_are_compared_in_digits_of_either_kind(code):
    text, digits = SAMPLES[code][0], SAMPLES[code][4]
    into, out = LanguagePair(f"en>{code}"), LanguagePair(f"{code}>en")
    assert numeric_content_matches(EN, text, into) and numeric_content_matches(text, EN, out)
    assert not numeric_content_matches(EN, text.replace("12", "15"), into)
    if digits:  # the language's own digits
        local = text.replace("12", digits[1]).replace("3", digits[0])
        assert numeric_content_matches(EN, local, into) and _codes(local, EN, into) == []
        assert not numeric_content_matches(EN, local.replace(digits[1], digits[0]), into)


def test_every_listed_language_has_its_script_and_an_unlisted_one_still_works():
    for code in SAMPLES:
        rules = profile(code)
        assert rules.scripts and all(name in SCRIPTS for name in rules.scripts), code
        assert rules.script_pattern.search(SAMPLES[code][0]), code
    # Tigre is written in the Ethiopic script and is not in the table: nothing is
    # known of it, and English left in a translation is still found by its words.
    tigre = LanguagePair("en>tig")
    assert profile("tig").scripts == () and compares_source_words(tigre)
    amharic = SAMPLES["am"][0]
    assert _codes(amharic, EN, tigre) == [] and _audit(EN, amharic, tigre) == []
    assert _codes(RESPELLED, EN, tigre) == ["untranslated_source_language"]
    assert _audit(EN, RESPELLED, tigre)


def test_source_words_are_compared_only_where_nothing_else_tells():
    assert compares_source_words(LanguagePair("en>pl")) and compares_source_words(LanguagePair("ru>uk"))
    for pair in ("en>de", "en>zh", "en>ru", "zh>ja", "en>th"):  # function words, the script, or no spaces
        assert not compares_source_words(LanguagePair(pair)), pair
    en_pl = LanguagePair("en>pl")
    source = "Mr. Sherlock Holmes, Dr. John Watson, Mrs. Hudson and Miss Mary Morstan were in the room at the time."
    # Names stand in a translation as the source has them, and cognates do not fill a sentence.
    assert source_worded_passage(source, "Mr. Sherlock Holmes, Dr. John Watson, Mrs. Hudson i Miss Mary Morstan byli w pokoju.", en_pl) == ""
    assert source_worded_passage("The man was in the water.", "De man was in het water.", LanguagePair("en>nl")) == ""
    assert source_worded_passage(source, "Mr. Sherlock Holmes and Miss Mary Morstan were in the room at the time.", en_pl)
    # A term the glossary keeps as written is not left-over text.
    kept = "the room were in and at"
    assert source_worded_passage(source, f"Był tam, {kept} i tyle.", en_pl) and not source_worded_passage(source, f"Był tam, {kept} i tyle.", en_pl, [kept])


def test_a_short_sentence_two_related_languages_share_is_not_rejected():
    # English and Dutch write this sentence almost alike, and the Dutch is right.
    en_nl = LanguagePair("en>nl")
    source, dutch = "Water is water and gas is gas.", "Water is water en gas is gas."
    assert _codes(dutch, source, en_nl) == []
    assert source_worded_passage(source, dutch, en_nl, whole=True) == ""
    # A whole passage handed back with a word changed is still refused.
    long_source = "She was beginning to get very tired of sitting by her sister on the bank and of having nothing to do."
    assert _codes(long_source.replace("very", "quite"), long_source, en_nl) == ["untranslated_source_language"]
    # The audit may still point a sentence like the first one out; it does not reject.
    assert all("possible untranslated" in message for message in _audit(source, dutch, en_nl))
