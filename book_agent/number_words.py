"""Number words of the profiled languages beyond English and Chinese.

English and Chinese number words are read from every text by content_policy,
as they always have been. The parsers here run only on text known to be in
their language (a profile's `number_words`), so a French "cent" never turns an
English "five-cent coin" into 100 (docs/GENERIC_LANGUAGES.md, phase 5).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

# -- French ---------------------------------------------------------------------------------

_FR_UNITS = {
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
    "quatorze": 14, "quinze": 15, "seize": 16,
}
_FR_TENS = {"vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "septante": 70, "huitante": 80, "octante": 80, "nonante": 90}
_FR_HUNDRED = {"cent": 100, "cents": 100}
_FR_SCALES = {"mille": 1_000, "million": 1_000_000, "millions": 1_000_000, "milliard": 1_000_000_000, "milliards": 1_000_000_000}
_FR_WORDS = {**_FR_UNITS, **_FR_TENS, **_FR_HUNDRED, **_FR_SCALES}
_FR_WORD = "|".join(sorted(map(re.escape, _FR_WORDS), key=len, reverse=True))
# A run of number words joined by hyphens or spaces; "et" joins only a tens word
# to un/une/onze (vingt et un, soixante et onze), so "neuf et trois" stays apart.
_FR_JOIN = r"(?:[\s\u00a0-]+et[\s\u00a0-]+(?=(?:un|une|onze)(?![\w-]))|[\s\u00a0-]+)"
_FR_RUN = rf"(?:{_FR_WORD})(?:{_FR_JOIN}(?:{_FR_WORD}))*"
_FR_NUMBER = re.compile(rf"(?<![\w-]){_FR_RUN}(?![\w-])", flags=re.IGNORECASE)
_FR_PERCENT = re.compile(
    rf"(?P<number>\d+(?:[.,]\d+)?|{_FR_RUN})[\s\u00a0]*(?:pour[\s\u00a0-]*cent|%)",
    flags=re.IGNORECASE,
)
# Words that are also ordinary words: an article ("un livre") and "new" (neuf).
_FR_AMBIGUOUS = {"un", "une", "neuf"}
# "Mille" after one of these is the old unit, a mile ("un bon mille"): French
# writes a thousand as plain "mille", never "un mille".
_FR_MILE_BEFORE = re.compile(
    r"(?:\b(?:un|le|du|au|ce|chaque|quel|bon|demi|premier|dernier)[\s\u00a0-]+)$",
    flags=re.IGNORECASE,
)
# A time of day ("quatre heures et demie") is left to the semantic audit: the
# other language writes it too many ways ("four thirty", "half past four").
_FR_HOUR_AFTER = re.compile(r"^[\s\u00a0]*heures?\b", flags=re.IGNORECASE)


def _is_mile(match: re.Match[str]) -> bool:
    return match.group(0).casefold() == "mille" and bool(_FR_MILE_BEFORE.search(match.string[: match.start()]))


def _is_hour(match: re.Match[str]) -> bool:
    return bool(_FR_HOUR_AFTER.match(match.string[match.end() :]))


def parse_french_number(text: str) -> int | None:
    """The value of French number words ("quatre-vingt-dix-neuf" -> 99), or None."""
    words = [word for word in re.split(r"[\s\u00a0-]+", text.casefold()) if word and word != "et"]
    if not words or any(word not in _FR_WORDS for word in words):
        return None
    # A plural follows a multiplier (deux cents, trois millions): alone it is a
    # noun ("des millions") or another word (the coin "cents").
    if words[0] in {"cents", "vingts", "millions", "milliards"}:
        return None
    total = 0
    current = 0
    for word in words:
        if word in _FR_UNITS:
            current += _FR_UNITS[word]
        elif word in ("vingt", "vingts") and current % 100 == 4:
            current += 76  # quatre-vingt(s) is 4 x 20, not 4 + 20
        elif word in _FR_TENS:
            current += _FR_TENS[word]
        elif word in _FR_HUNDRED:
            current = (current or 1) * 100
        else:
            total += (current or 1) * _FR_SCALES[word]
            current = 0
    return total + current


def _french_value(match: re.Match[str]) -> int | None:
    return parse_french_number(match.group(0))


def french_objective_patterns() -> list[tuple[re.Pattern[str], Callable[[re.Match[str]], float | int | None]]]:
    """Patterns whose matches are firm number facts: percentages, and written
    numbers that carry a magnitude (cent, mille, million, milliard)."""

    def percent(match: re.Match[str]) -> float | None:
        number = match.group("number")
        value = float(number.replace(",", ".")) if number[0].isdigit() else parse_french_number(number)
        return None if value is None else value / 100

    def magnitude(match: re.Match[str]) -> int | None:
        words = set(re.split(r"[\s\u00a0-]+", match.group(0).casefold()))
        if not words & {*_FR_HUNDRED, *_FR_SCALES} or _is_mile(match) or _is_hour(match):
            return None
        return parse_french_number(match.group(0))

    return [(_FR_PERCENT, percent), (_FR_NUMBER, magnitude)]


def french_word_values(text: str) -> Iterator[int]:
    """Number words as lower-confidence counts, leaving out the ambiguous single words."""
    for match in _FR_NUMBER.finditer(text):
        if match.group(0).casefold() in _FR_AMBIGUOUS or _is_mile(match) or _is_hour(match):
            continue
        value = parse_french_number(match.group(0))
        if value is not None:
            yield value


# -- Japanese -------------------------------------------------------------------------------

# Japanese writes its numerals with the Han characters the Chinese parser reads,
# except 億 (10^8), where Simplified Chinese writes 亿. Same length, same positions.
_JA_TO_CHINESE_NUMERALS = str.maketrans({"億": "亿"})


def japanese_as_chinese_numerals(text: str) -> str:
    return text.translate(_JA_TO_CHINESE_NUMERALS)


# -- Spanish --------------------------------------------------------------------------------

_ES_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciséis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiún": 21, "veintiuno": 21,
    "veintiuna": 21, "veintidós": 22, "veintitrés": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiséis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_ES_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_ES_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300,
    "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500,
    "quinientas": 500, "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800, "novecientos": 900, "novecientas": 900,
}
_ES_SCALES = {"mil": 1_000, "millón": 1_000_000, "millones": 1_000_000}
_ES_WORDS = {**_ES_UNITS, **_ES_TENS, **_ES_HUNDREDS, **_ES_SCALES}
_ES_WORD = "|".join(sorted(map(re.escape, _ES_WORDS), key=len, reverse=True))
# "y" joins only a tens word to its unit (treinta y dos), so "dos y tres" stays apart.
_ES_JOIN = rf"(?:(?<=a)\s+y\s+(?=(?:{'|'.join(_ES_UNITS)})(?![\w-]))|\s+)"
_ES_RUN = rf"(?:{_ES_WORD})(?:{_ES_JOIN}(?:{_ES_WORD}))*"
_ES_NUMBER = re.compile(rf"(?<![\w-]){_ES_RUN}(?![\w-])", flags=re.IGNORECASE)
_ES_PERCENT = re.compile(rf"(?P<number>\d+(?:[.,]\d+)?|{_ES_RUN})\s*(?:por\s+ciento|%)", flags=re.IGNORECASE)
# An article ("un hombre", "una casa") is not a count.
_ES_AMBIGUOUS = {"un", "uno", "una"}
# A time of day ("a las cinco y media", "la una") is left to the semantic audit.
_ES_HOUR_BEFORE = re.compile(r"\b(?:las?)\s+$", flags=re.IGNORECASE)


def parse_spanish_number(text: str) -> int | None:
    """The value of Spanish number words ("trescientos cuarenta y dos" -> 342), or None."""
    words = [word for word in re.split(r"\s+", text.casefold()) if word and word != "y"]
    if not words or any(word not in _ES_WORDS for word in words):
        return None
    if words[0] == "millones":
        return None  # "millones de personas": a noun, not a number
    total = 0
    current = 0
    for word in words:
        if word in _ES_UNITS or word in _ES_TENS or word in _ES_HUNDREDS:
            current += _ES_WORDS[word]
        elif word == "mil":
            total += (current or 1) * 1_000
            current = 0
        else:
            total = (total + current or 1) * 1_000_000
            current = 0
    return total + current


def _es_is_hour(match: re.Match[str]) -> bool:
    return bool(_ES_HOUR_BEFORE.search(match.string[: match.start()]))


def spanish_objective_patterns() -> list[tuple[re.Pattern[str], Callable[[re.Match[str]], float | int | None]]]:
    """Percentages and written numbers that carry a magnitude (cien, mil, millón)."""

    def percent(match: re.Match[str]) -> float | None:
        number = match.group("number")
        value = float(number.replace(",", ".")) if number[0].isdigit() else parse_spanish_number(number)
        return None if value is None else value / 100

    def magnitude(match: re.Match[str]) -> int | None:
        words = set(re.split(r"\s+", match.group(0).casefold()))
        if not words & {*_ES_HUNDREDS, *_ES_SCALES} or _es_is_hour(match):
            return None
        return parse_spanish_number(match.group(0))

    return [(_ES_PERCENT, percent), (_ES_NUMBER, magnitude)]


def spanish_word_values(text: str) -> Iterator[int]:
    for match in _ES_NUMBER.finditer(text):
        if match.group(0).casefold() in _ES_AMBIGUOUS or _es_is_hour(match):
            continue
        value = parse_spanish_number(match.group(0))
        if value is not None:
            yield value


# -- German ---------------------------------------------------------------------------------

# German writes a number below a million as one word (dreihundertfünfundzwanzig).
_DE_UNITS = {
    "null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
    "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17,
    "achtzehn": 18, "neunzehn": 19,
}
_DE_TENS = {"zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90}
_DE_PARTS = {**_DE_UNITS, **_DE_TENS, "hundert": 100, "tausend": 1_000, "und": 0}
_DE_PART = "|".join(sorted(map(re.escape, _DE_PARTS), key=len, reverse=True))
_DE_WORD = re.compile(rf"(?<![\w-])(?:{_DE_PART})+(?![\w-])", flags=re.IGNORECASE)
_DE_SCALE = re.compile(r"(?<![\w-])(?P<count>\w+)\s+(?P<scale>Millionen|Million|Milliarden|Milliarde)(?![\w-])", flags=re.IGNORECASE)
_DE_PERCENT = re.compile(r"(?P<number>\d+(?:[.,]\d+)?|\w+)\s*(?:Prozent|%)", flags=re.IGNORECASE)
# Articles (ein Mann, eine Frau) are not counts; "acht geben" is attention.
_DE_AMBIGUOUS = {"ein", "eine", "eins", "und", "acht"}
# A time of day ("um fünf Uhr") is left to the semantic audit.
_DE_HOUR_AFTER = re.compile(r"^\s*Uhr\b")


def parse_german_number(word: str) -> int | None:
    """The value of a German number word ("dreihundertfünfundzwanzig" -> 325), or None."""
    word = word.lower()  # not casefold(): that turns ß into ss (dreißig)
    parts = re.findall(_DE_PART, word)
    if not parts or "".join(parts) != word or parts == ["und"]:
        return None
    total = 0
    current = 0
    pending = 0  # a unit waiting for "und" + tens (fünfundzwanzig)
    for part in parts:
        if part == "und":
            continue
        if part in _DE_UNITS:
            current += pending
            pending = _DE_UNITS[part]
        elif part in _DE_TENS:
            current += _DE_TENS[part] + pending
            pending = 0
        elif part == "hundert":
            current = (current + pending or 1) * 100
            pending = 0
        else:  # tausend
            total += (current + pending or 1) * 1_000
            current = pending = 0
    return total + current + pending


def german_objective_patterns() -> list[tuple[re.Pattern[str], Callable[[re.Match[str]], float | int | None]]]:
    """Percentages, Millionen/Milliarden, and one-word numbers with hundert or tausend."""

    def number(text: str) -> float | None:
        if text[0].isdigit():
            return float(text.replace(".", "").replace(",", "."))
        return parse_german_number(text)

    def percent(match: re.Match[str]) -> float | None:
        value = number(match.group("number"))
        return None if value is None else value / 100

    def scale(match: re.Match[str]) -> float | None:
        count = number(match.group("count"))
        if count is None:
            return None
        return count * (1_000_000 if match.group("scale").casefold().startswith("million") else 1_000_000_000)

    def magnitude(match: re.Match[str]) -> int | None:
        word = match.group(0).lower()
        if ("hundert" not in word and "tausend" not in word) or _DE_HOUR_AFTER.match(match.string[match.end():]):
            return None
        return parse_german_number(word)

    return [(_DE_PERCENT, percent), (_DE_SCALE, scale), (_DE_WORD, magnitude)]


def german_word_values(text: str) -> Iterator[int]:
    for match in _DE_WORD.finditer(text):
        if match.group(0).lower() in _DE_AMBIGUOUS or _DE_HOUR_AFTER.match(text[match.end():]):
            continue
        value = parse_german_number(match.group(0))
        if value is not None:
            yield value


# -- Korean ---------------------------------------------------------------------------------

# Korean numbers are only read before a counter: on their own the syllables are
# ordinary words (일 "work", 이 "this", 사 "person", 만 "only"). Native numbers
# count things and people; Sino-Korean numbers count money, years, and floors.
_KO_NATIVE = {"두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스무": 20, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50}
_KO_NATIVE_COUNTERS = "명|사람|개|마리|번|살|시간|권|잔|채|대|가지|달|해|그릇|켤레|벌"
_KO_SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_KO_SINO_POWERS = {"십": 10, "백": 100, "천": 1_000}
_KO_SINO_COUNTERS = "원|전|년|층|리|퍼센트|프로|세|분|초|미터|킬로"
# What may follow a counter: the end of the word, or a particle (사람이, 전을).
_KO_AFTER_COUNTER = r"(?=[\s.,!?”’)]|$|[이을를은는의에도만과와가])"
_KO_NATIVE_NUMBER = re.compile(rf"(?<![가-힣])(?P<number>(?:{'|'.join(sorted(_KO_NATIVE, key=len, reverse=True))})(?:\s?(?:{'|'.join(sorted(_KO_NATIVE, key=len, reverse=True))}))?)\s?(?:{_KO_NATIVE_COUNTERS}){_KO_AFTER_COUNTER}")
_KO_SINO_NUMBER = re.compile(rf"(?<![가-힣])(?P<number>[일이삼사오육칠팔구십백천만]+)\s?(?:{_KO_SINO_COUNTERS}){_KO_AFTER_COUNTER}")


def parse_korean_sino(text: str) -> int | None:
    """Sino-Korean numerals ("삼백오십" -> 350), or None."""
    total = 0
    section = 0
    digit = 0
    for character in text:
        if character in _KO_SINO_DIGITS:
            if digit:
                return None  # two digits in a row is not a number
            digit = _KO_SINO_DIGITS[character]
        elif character in _KO_SINO_POWERS:
            section += (digit or 1) * _KO_SINO_POWERS[character]
            digit = 0
        elif character == "만":
            total += (section + digit or 1) * 10_000
            section = digit = 0
        else:
            return None
    return total + section + digit


def parse_korean_native(text: str) -> int | None:
    """Native Korean numerals before a counter ("스물 두" -> 22), or None."""
    parts = re.split(r"\s+", text.strip())
    values = [_KO_NATIVE.get(part) for part in parts]
    if None in values:
        return None
    return sum(values)


def korean_word_values(text: str) -> Iterator[int]:
    """Counted numbers, as lower-confidence facts."""
    for match in _KO_NATIVE_NUMBER.finditer(text):
        if match.group("number") == "열" and match.group(0).rstrip().endswith("사람"):
            continue  # "문을 열 사람": the person who will open the door
        value = parse_korean_native(match.group("number"))
        if value is not None:
            yield value
    for match in _KO_SINO_NUMBER.finditer(text):
        value = parse_korean_sino(match.group("number"))
        if value is not None:
            yield value


# -- registry -------------------------------------------------------------------------------

# Parsers by a profile's `number_words`; English and Chinese are read in every text.
OBJECTIVE_PATTERNS: dict[str, Callable[[], list]] = {
    "french": french_objective_patterns,
    "spanish": spanish_objective_patterns,
    "german": german_objective_patterns,
}
WORD_VALUES: dict[str, Callable[[str], Iterator[int]]] = {
    "french": french_word_values,
    "spanish": spanish_word_values,
    "german": german_word_values,
    "korean": korean_word_values,
}
