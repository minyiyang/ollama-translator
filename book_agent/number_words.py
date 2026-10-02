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


def parse_french_number(text: str) -> int | None:
    """The value of French number words ("quatre-vingt-dix-neuf" -> 99), or None."""
    words = [word for word in re.split(r"[\s\u00a0-]+", text.casefold()) if word and word != "et"]
    if not words or any(word not in _FR_WORDS for word in words):
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
        return parse_french_number(match.group(0)) if words & {*_FR_HUNDRED, *_FR_SCALES} else None

    return [(_FR_PERCENT, percent), (_FR_NUMBER, magnitude)]


def french_word_values(text: str) -> Iterator[int]:
    """Number words as lower-confidence counts, leaving out the ambiguous single words."""
    for match in _FR_NUMBER.finditer(text):
        if match.group(0).casefold() in _FR_AMBIGUOUS:
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
