"""Languages, language pairs, and the profiles that hold every language rule.

Code asks a language's profile ("is this script Han?", "which dash?") instead
of testing for English or Chinese (docs/GENERIC_LANGUAGES.md, 2.2). A
language is a code ("en", "zh"); a pair is written "en-zh" or "zh-en" as it
always has been, and "<source>><target>" ("en>ja") for any other pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, ClassVar, Iterator


# -- language profiles ---------------------------------------------------------------
#
# Everything a check or prompt needs to know about one language. Values are the
# rules the code used before profiles existed, copied exactly, so today's pairs
# behave as before.

@dataclass(frozen=True)
class LanguageProfile:
    code: str
    display_name: str
    # The plain name used in audit findings ("possible untranslated Chinese text").
    short_name: str
    script: str
    # Character-class bodies for the language's script: the full range, and the
    # narrower range some checks historically used.
    script_chars: str
    script_basic_chars: str
    spaced_words: bool
    cased: bool
    # Characters that continue a word: a glossary term only matches between
    # them in languages that space their words.
    word_chars: str
    # Regex for the inflection a term may carry in running prose (English
    # plurals), appended when the term ends in a letter.
    plural_suffix: str
    # The stored glossary field that holds terms in this language.
    glossary_field: str
    # Characters that end a sentence.
    sentence_end: str = ""
    # Phrases that read as machine-written prose (audit "ai_style").
    stock_phrases: tuple[str, ...] = field(default_factory=tuple)
    # The same two marked passages written in this language, for the
    # translation prompt's marker examples: a sentence and a chapter heading.
    marker_examples: tuple[str, ...] = field(default_factory=tuple)
    # House punctuation for a translation into this language (style sheet defaults).
    quotes: str = ""
    nested_quotes: str = ""
    ellipsis: str = ""
    dash: str = ""
    # Whether the book-level punctuation and character reports are tuned for it.
    convention_checks: bool = False
    # Third-person singular pronouns (he, she, it) and forms of address
    # (informal, polite) a translation chooses between for a character.
    pronouns: tuple[str, ...] = field(default_factory=tuple)
    address_forms: tuple[str, ...] = field(default_factory=tuple)
    # Frequent function words that say nothing about a passage's content.
    stop_words: frozenset[str] = field(default_factory=frozenset)

    @cached_property
    def script_pattern(self) -> re.Pattern[str]:
        return re.compile(f"[{self.script_chars}]")

    def normalize_term(self, term: str) -> str:
        """A glossary term in the form used for matching and de-duplication."""
        return " ".join(term.casefold().split()) if self.cased else term


_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_BASIC = "\u3400-\u9fff"

# Keyed by the code jobs store; `zh` is Simplified Chinese (profile `zh-Hans`).
PROFILES: dict[str, LanguageProfile] = {
    "en": LanguageProfile(
        code="en",
        display_name="English",
        short_name="English",
        script="Latin",
        script_chars="A-Za-z",
        script_basic_chars="A-Za-z",
        spaced_words=True,
        cased=True,
        word_chars="A-Za-z0-9_",
        plural_suffix="(?:e?s)?",
        glossary_field="english",
        sentence_end=".!?",
        stock_phrases=(
            "it is worth noting that",
            "in conclusion",
            "a testament to",
            "time seemed to stand still",
            "as if whispering a story",
        ),
        marker_examples=(
            "She was <I000>very</I000> tired.",
            "<I000></I000>Chapter <I001></I001> One",
        ),
        quotes="“ ”",
        nested_quotes="‘ ’",
        ellipsis="…",
        dash="—",
        pronouns=("he", "she", "it"),
        stop_words=frozenset({"the", "and", "that", "with", "this", "from", "was", "were"}),
    ),
    "zh": LanguageProfile(
        code="zh-Hans",
        display_name="Simplified Chinese",
        short_name="Chinese",
        script="Han",
        script_chars=_HAN,
        script_basic_chars=_HAN_BASIC,
        spaced_words=False,
        cased=False,
        word_chars="",
        plural_suffix="",
        glossary_field="chinese",
        sentence_end="。！？",
        stock_phrases=(
            "值得注意的是",
            "总而言之",
            "不禁让人",
            "仿佛在诉说着",
            "这一刻，时间仿佛静止",
        ),
        marker_examples=(
            "她<I000>非常</I000>疲倦。",
            "<I000></I000>第一章<I001></I001>",
        ),
        quotes="“ ”",
        nested_quotes="‘ ’",
        ellipsis="……",
        dash="——",
        convention_checks=True,
        pronouns=("他", "她", "它"),
        address_forms=("你", "您"),
    ),
}

# Other spellings of a stored code.
_CODE_ALIASES = {"zh-Hans": "zh"}


# -- languages and pairs ---------------------------------------------------------------


def _pydantic_string_schema(cls: type) -> Any:
    """Validate from a string, serialize to that string in JSON (like a str enum)."""
    from pydantic_core import core_schema

    return core_schema.no_info_plain_validator_function(
        cls,
        json_schema_input_schema=core_schema.str_schema(),
        serialization=core_schema.plain_serializer_function_ser_schema(str, when_used="json"),
    )


class Language(str):
    """A language code ("en", "zh"). Compares, hashes, and serializes as the code."""

    ENGLISH: ClassVar[Language]
    CHINESE: ClassVar[Language]
    _interned: ClassVar[dict[str, Language]] = {}

    def __new__(cls, code: str) -> Language:
        if isinstance(code, Language):
            return code
        code = _CODE_ALIASES.get(code, code)
        if code not in PROFILES:
            raise ValueError(f"{code!r} is not a supported language")
        if code not in cls._interned:
            cls._interned[code] = super().__new__(cls, code)
        return cls._interned[code]

    def __getnewargs__(self) -> tuple[str]:
        return (str(self),)

    def __repr__(self) -> str:
        return f"Language({str(self)!r})"

    @property
    def value(self) -> str:
        return str(self)

    @property
    def display_name(self) -> str:
        """Return a stable English display name for prompts and reports."""
        return profile(self).display_name

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return _pydantic_string_schema(cls)


# The two pairs that predate profiles keep their spelling in every stored file.
_LEGACY_PAIRS = {"en-zh": ("en", "zh"), "zh-en": ("zh", "en")}


class _PairType(type):
    def __iter__(cls) -> Iterator[LanguagePair]:
        """Every pair of profiled languages, in profile order."""
        return iter(
            cls(f"{source}>{target}") for source in PROFILES for target in PROFILES if source != target
        )


class LanguagePair(str, metaclass=_PairType):
    """A translation direction. Written "en-zh"/"zh-en" for today's pairs, "en>ja" otherwise."""

    EN_TO_ZH: ClassVar[LanguagePair]
    ZH_TO_EN: ClassVar[LanguagePair]
    _interned: ClassVar[dict[str, LanguagePair]] = {}
    source_language: Language
    target_language: Language

    def __new__(cls, value: str) -> LanguagePair:
        if isinstance(value, LanguagePair):
            return value
        if value in cls._interned:
            return cls._interned[value]
        if value in _LEGACY_PAIRS:
            source_code, target_code = _LEGACY_PAIRS[value]
        elif isinstance(value, str) and value.count(">") == 1:
            source_code, target_code = value.split(">")
        else:
            raise ValueError(f"{value!r} is not a language pair; write it as source>target, e.g. en>ja")
        source, target = Language(source_code.strip()), Language(target_code.strip())
        if source == target:
            raise ValueError(f"{value!r} translates a language into itself")
        text = next(
            (legacy for legacy, codes in _LEGACY_PAIRS.items() if codes == (source, target)),
            f"{source}>{target}",
        )
        if text not in cls._interned:
            pair = super().__new__(cls, text)
            pair.source_language = source
            pair.target_language = target
            cls._interned[text] = pair
        cls._interned[value] = cls._interned[text]
        return cls._interned[text]

    def __getnewargs__(self) -> tuple[str]:
        return (str(self),)

    def __repr__(self) -> str:
        return f"LanguagePair({str(self)!r})"

    @property
    def value(self) -> str:
        return str(self)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return _pydantic_string_schema(cls)


# The name the code has always used; existing imports keep working.
TranslationDirection = LanguagePair

Language.ENGLISH = Language("en")
Language.CHINESE = Language("zh")
LanguagePair.EN_TO_ZH = LanguagePair("en-zh")
LanguagePair.ZH_TO_EN = LanguagePair("zh-en")

# The pair a new job translates unless configured otherwise.
DEFAULT_DIRECTION = LanguagePair("en-zh")

# The languages a stored glossary entry holds, one field each (until phase 3).
GLOSSARY_LANGUAGES = ("en", "zh")

# Letters of any profiled script.
ANY_SCRIPT = re.compile("[" + "".join(item.script_chars for item in PROFILES.values()) + "]")
# Letters of scripts written without spaces: about one model token each.
UNSPACED_SCRIPT = re.compile(
    "[" + "".join(item.script_chars for item in PROFILES.values() if not item.spaced_words) + "]"
)
# Characters that end a sentence in any profiled language.
SENTENCE_END = "".join(item.sentence_end for item in PROFILES.values())


def profile(language: Language | str) -> LanguageProfile:
    """The profile for a language or language code."""
    return PROFILES[Language(language)]


def build_direction_instruction(direction: LanguagePair) -> str:
    """Build the non-optional language constraint for a translation prompt."""
    source = direction.source_language.display_name
    target = direction.target_language.display_name
    return (
        f"Translate every source passage from {source} into {target}. "
        f"Output translated {target} only, apart from protected structural markers."
    )


def glossary_sides(entry, direction: LanguagePair) -> tuple[str, str]:
    """(source term, target term) of a glossary entry for this direction."""
    return (
        getattr(entry, profile(direction.source_language).glossary_field),
        getattr(entry, profile(direction.target_language).glossary_field),
    )


# Stored glossary aliases are variants of this field's term (today, English).
ALIAS_FIELD = "english"


def source_aliases(entry, direction: LanguagePair) -> list[str]:
    """An entry's aliases when they are variants of the source term, else none."""
    if profile(direction.source_language).glossary_field != ALIAS_FIELD:
        return []
    return list(entry.aliases)
