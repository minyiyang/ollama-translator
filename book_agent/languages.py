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
from typing import Any, ClassVar, Iterator, Sequence


# -- language profiles ---------------------------------------------------------------
#
# Everything a check or prompt needs to know about one language. Values are the
# rules the code used before profiles existed, copied exactly, so today's pairs
# behave as before.

@dataclass(frozen=True)
class ConventionRule:
    """A house convention the book-level check enforces by majority: segments
    using `slip` are flagged when at least as many segments use `house`."""

    house: str  # regex of the convention the book follows
    slip: str  # regex of the departure
    message: str  # with {count}: the segments that follow the convention
    fix: str
    # The slip is also the nested form (French “ ” inside « »): a segment that
    # uses the house form as well is not a departure.
    nested_ok: bool = False
    # When the slip is one character that is always wrong under the house
    # convention, (slip, correction): repair replaces it without a model call.
    replace: tuple[str, str] | None = None


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    display_name: str
    # The plain name used in audit findings ("possible untranslated Chinese text").
    short_name: str
    # Unicode scripts the language is written in (keys of SCRIPTS); empty when unknown.
    scripts: tuple[str, ...]
    # Character-class bodies for the language's script: the full range, and the
    # narrower range some checks historically used. Empty when the script is unknown.
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
    # Endings an approved target term may take in a translation into this
    # language (des Käfers, los caballeros); empty: the term must appear as is.
    inflection_suffix: str = ""
    # Particles that attach to the end of a word (홍길동은, 홍길동에게는): a term
    # matches at the start of a word, followed by nothing or by particles only,
    # so 죽 (porridge) is not found in 죽었다 (died).
    attached_particles: tuple[str, ...] = field(default_factory=tuple)
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
    # House conventions the book-level punctuation check enforces (consistency.py).
    conventions: tuple[ConventionRule, ...] = field(default_factory=tuple)
    # Whether consistency_report's character and convention sections are written for it.
    consistency_report: bool = False
    # Third-person singular pronouns (he, she, it) and forms of address
    # (informal, polite) a translation chooses between for a character.
    pronouns: tuple[str, ...] = field(default_factory=tuple)
    address_forms: tuple[str, ...] = field(default_factory=tuple)
    # Frequent function words that say nothing about a passage's content.
    stop_words: frozenset[str] = field(default_factory=frozenset)
    # Short words found in almost every sentence of the language and rarely in
    # another's: in a pair sharing a script, a passage full of the source's and
    # none of the target's is left untranslated (audit, "left-over source words").
    function_words: frozenset[str] = field(default_factory=frozenset)
    # What separates the parts of a transliterated name ("·" in Chinese), so a
    # full name can be checked against the rendering of each part.
    name_separator: str = ""
    # A sentence for the glossary extraction prompt about terms in this language.
    extraction_note: str = ""
    # The number-word parser that reads this language: "english" and "chinese"
    # run on every text; "french" and "japanese" on text known to be in them
    # (number_words.py). Empty when no parser reads it.
    number_words: str = ""
    # "tuned", "profiled", or "generic" (docs/GENERIC_LANGUAGES.md, 2.4).
    tier: str = "generic"

    @property
    def script(self) -> str:
        return self.scripts[0] if self.scripts else ""

    @cached_property
    def script_pattern(self) -> re.Pattern[str]:
        """A letter of this language's script; any letter when the script is unknown."""
        return re.compile(f"[{self.script_chars}]" if self.script_chars else r"[^\W\d_]")

    def normalize_term(self, term: str) -> str:
        """A glossary term in the form used for matching and de-duplication."""
        return " ".join(term.casefold().split()) if self.cased else term

    @cached_property
    def term_end(self) -> str:
        """Regex for what may follow a term in running text: the end of the word,
        after any attached particles."""
        boundary = f"(?![{self.word_chars}])"
        if not self.attached_particles:
            return boundary
        particles = "|".join(sorted(map(re.escape, self.attached_particles), key=len, reverse=True))
        return f"(?:{particles}){{0,3}}{boundary}"

    def inflected(self, words: Sequence[str], suffix: str) -> str:
        """Regex for a term (its words already escaped) whose words may each take
        `suffix`. A word before the last may also change the ending it has: the
        approved "Weißer Hase" is written "der Weiße Hase", "des Weißen Hasen".
        Not on a word under three letters ("Mr" + "s" is "Mrs").
        """
        letters = "[^\\W\\d_]"
        parts = []
        for index, word in enumerate(words):
            if not re.search(letters + "{3}$", word):
                parts.append(word)
                continue
            if index < len(words) - 1:
                stem = re.fullmatch(f"(.*?{letters}{{3}}?)(?:{suffix})", word)
                word = stem.group(1) if stem else word
            parts.append(word + suffix)
        return "\\s+".join(parts)


_HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_BASIC = "\u3400-\u9fff"

# Keyed by the code jobs store; `zh` is Simplified Chinese (profile `zh-Hans`).
PROFILES: dict[str, LanguageProfile] = {
    "en": LanguageProfile(
        code="en",
        display_name="English",
        short_name="English",
        scripts=("Latin",),
        script_chars="A-Za-z",
        script_basic_chars="A-Za-z",
        spaced_words=True,
        cased=True,
        word_chars="A-Za-z0-9_",
        plural_suffix="(?:e?s)?",
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
        function_words=frozenset({
            "the", "and", "of", "is", "was", "are", "were", "it's", "i'm", "don't", "can't",
            "won't", "isn't", "you", "your", "he", "she", "they", "we", "them", "this", "that",
            "what", "with", "have", "has", "had", "not", "but", "an", "at", "be", "would",
            "will", "there", "here", "my", "me", "him", "her", "his", "it", "if", "do", "did",
        }),
        number_words="english",
        tier="tuned",
    ),
    "zh": LanguageProfile(
        code="zh-Hans",
        display_name="Simplified Chinese",
        short_name="Chinese",
        scripts=("Han",),
        script_chars=_HAN,
        script_basic_chars=_HAN_BASIC,
        spaced_words=False,
        cased=False,
        word_chars="",
        plural_suffix="",
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
        conventions=(
            ConventionRule(
                house="——",
                slip="(?<!—)—(?!—)",
                message="A single dash — is used here, but the book uses the paired Chinese dash —— ({count} segments).",
                fix="Replace the single dash — with ——.",
            ),
            ConventionRule(
                house="[“”]",
                slip='"',
                message='A straight quotation mark " is used here, but the book uses “ ” ({count} segments).',
                fix='Replace the straight quotation marks " with “ and ”.',
            ),
        ),
        consistency_report=True,
        pronouns=("他", "她", "它"),
        address_forms=("你", "您"),
        name_separator="·",
        number_words="chinese",
        tier="tuned",
    ),
}

# Other spellings of a stored code.
_CODE_ALIASES = {"zh-Hans": "zh", "zh-CN": "zh", "zh-SG": "zh"}


# -- generic profiles ------------------------------------------------------------------
#
# A language without a profile gets one built from its code: a display name,
# its script, and whether it spaces its words. Every other field is empty, so
# the checks that need them are skipped (see language_support) rather than run
# with another language's rules.

# Character-class bodies of the scripts a generic profile can name.
SCRIPTS: dict[str, str] = {
    "Latin": "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff",
    "Greek": "\u0370-\u03ff\u1f00-\u1fff",
    "Cyrillic": "\u0400-\u052f",
    "Armenian": "\u0531-\u058f",
    "Hebrew": "\u05d0-\u05ff",
    "Arabic": "\u0620-\u064a\u0660-\u06ff\u0750-\u077f",
    "Devanagari": "\u0900-\u097f",
    "Bengali": "\u0980-\u09ff",
    "Thai": "\u0e00-\u0e7f",
    "Georgian": "\u10a0-\u10ff",
    "Hangul": "\u1100-\u11ff\u3130-\u318f\uac00-\ud7af",
    "Hiragana": "\u3040-\u309f",
    "Katakana": "\u30a0-\u30ff",
    "Han": _HAN,
}
# Scripts with upper and lower case.
_CASED_SCRIPTS = {"Latin", "Greek", "Cyrillic", "Armenian"}
# ISO 15924 script subtags this table understands.
_SCRIPT_SUBTAGS = {
    "Latn": ("Latin",), "Grek": ("Greek",), "Cyrl": ("Cyrillic",), "Armn": ("Armenian",),
    "Hebr": ("Hebrew",), "Arab": ("Arabic",), "Deva": ("Devanagari",), "Beng": ("Bengali",),
    "Thai": ("Thai",), "Geor": ("Georgian",), "Kore": ("Hangul", "Han"), "Hang": ("Hangul",),
    "Jpan": ("Han", "Hiragana", "Katakana"), "Hans": ("Han",), "Hant": ("Han",), "Hani": ("Han",),
}
# Languages written without spaces between words.
_UNSPACED = {"ja", "zh", "th", "lo", "km", "my"}

# English name and default script of common languages.
_LANGUAGES: dict[str, tuple[str, tuple[str, ...]]] = {
    "af": ("Afrikaans", ("Latin",)), "ar": ("Arabic", ("Arabic",)), "be": ("Belarusian", ("Cyrillic",)),
    "bg": ("Bulgarian", ("Cyrillic",)), "bn": ("Bengali", ("Bengali",)), "ca": ("Catalan", ("Latin",)),
    "cs": ("Czech", ("Latin",)), "cy": ("Welsh", ("Latin",)), "da": ("Danish", ("Latin",)),
    "de": ("German", ("Latin",)), "el": ("Greek", ("Greek",)), "en": ("English", ("Latin",)),
    "eo": ("Esperanto", ("Latin",)), "es": ("Spanish", ("Latin",)), "et": ("Estonian", ("Latin",)),
    "eu": ("Basque", ("Latin",)), "fa": ("Persian", ("Arabic",)), "fi": ("Finnish", ("Latin",)),
    "fr": ("French", ("Latin",)), "ga": ("Irish", ("Latin",)), "gl": ("Galician", ("Latin",)),
    "he": ("Hebrew", ("Hebrew",)), "hi": ("Hindi", ("Devanagari",)), "hr": ("Croatian", ("Latin",)),
    "hu": ("Hungarian", ("Latin",)), "hy": ("Armenian", ("Armenian",)), "id": ("Indonesian", ("Latin",)),
    "is": ("Icelandic", ("Latin",)), "it": ("Italian", ("Latin",)),
    "ja": ("Japanese", ("Han", "Hiragana", "Katakana")), "ka": ("Georgian", ("Georgian",)),
    "kk": ("Kazakh", ("Cyrillic",)), "ko": ("Korean", ("Hangul",)), "la": ("Latin", ("Latin",)),
    "lt": ("Lithuanian", ("Latin",)), "lv": ("Latvian", ("Latin",)), "mk": ("Macedonian", ("Cyrillic",)),
    "mn": ("Mongolian", ("Cyrillic",)), "mr": ("Marathi", ("Devanagari",)), "ms": ("Malay", ("Latin",)),
    "nb": ("Norwegian Bokmål", ("Latin",)), "ne": ("Nepali", ("Devanagari",)), "nl": ("Dutch", ("Latin",)),
    "nn": ("Norwegian Nynorsk", ("Latin",)), "no": ("Norwegian", ("Latin",)), "pl": ("Polish", ("Latin",)),
    "pt": ("Portuguese", ("Latin",)), "ro": ("Romanian", ("Latin",)), "ru": ("Russian", ("Cyrillic",)),
    "sk": ("Slovak", ("Latin",)), "sl": ("Slovenian", ("Latin",)), "sq": ("Albanian", ("Latin",)),
    "sr": ("Serbian", ("Cyrillic",)), "sv": ("Swedish", ("Latin",)), "sw": ("Swahili", ("Latin",)),
    "th": ("Thai", ("Thai",)), "tl": ("Tagalog", ("Latin",)), "tr": ("Turkish", ("Latin",)),
    "uk": ("Ukrainian", ("Cyrillic",)), "ur": ("Urdu", ("Arabic",)), "vi": ("Vietnamese", ("Latin",)),
    "zh": ("Chinese", ("Han",)),
}
_SCRIPT_NAMES = {"Hans": "Simplified", "Hant": "Traditional", "Latn": "Latin", "Cyrl": "Cyrillic"}
# Regions whose Chinese is Traditional, so `zh-TW` is not the Simplified profile.
_TRADITIONAL_REGIONS = {"TW", "HK", "MO"}

_CODE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def _canonical_code(code: str) -> str:
    """BCP 47 casing: `zh-hant-tw` -> `zh-Hant-TW`."""
    parts = code.replace("_", "-").split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            out.append(part.title())
        elif len(part) == 2 and part.isalpha():
            out.append(part.upper())
        else:
            out.append(part.lower())
    return "-".join(out)


def _subtags(code: str) -> tuple[str, str, str]:
    """(language, script subtag, region) of a canonical code."""
    parts = code.split("-")
    script = next((part for part in parts[1:] if len(part) == 4 and part.isalpha()), "")
    region = next((part for part in parts[1:] if len(part) == 2 or (len(part) == 3 and part.isdigit())), "")
    return parts[0], script, region


def _generic_profile(code: str) -> LanguageProfile:
    language, script_tag, region = _subtags(code)
    name, scripts = _LANGUAGES.get(language, (code, ()))
    if language == "zh" and region in _TRADITIONAL_REGIONS and not script_tag:
        script_tag = "Hant"
    if script_tag in _SCRIPT_SUBTAGS:
        scripts = _SCRIPT_SUBTAGS[script_tag]
    qualifiers = [item for item in (_SCRIPT_NAMES.get(script_tag, script_tag), region) if item]
    display = f"{name} ({', '.join(qualifiers)})" if qualifiers and name != code else name
    chars = "".join(SCRIPTS[item] for item in scripts)
    spaced = language not in _UNSPACED
    return LanguageProfile(
        code=code,
        display_name=display,
        short_name=display,
        scripts=scripts,
        script_chars=chars,
        script_basic_chars=chars,
        spaced_words=spaced,
        cased=bool(set(scripts) & _CASED_SCRIPTS),
        word_chars=f"{chars}0-9_" if spaced and chars else "",
        plural_suffix="",
    )


# -- profiled languages (docs/GENERIC_LANGUAGES.md, phase 5) ----------------------------------

# The languages the code knew before profiles existed. Patterns built over "every
# language" (any script, word runs, lexical tokens, the pair list) keep using
# only these, so a new profile never changes what en-zh and zh-en do.
TUNED_PROFILES: dict[str, LanguageProfile] = dict(PROFILES)

_NBSP = "\u00a0\u202f"
_KANA_HAN = SCRIPTS["Han"] + SCRIPTS["Hiragana"] + SCRIPTS["Katakana"]

PROFILES["fr"] = LanguageProfile(
    code="fr",
    display_name="French",
    short_name="French",
    scripts=("Latin",),
    script_chars=SCRIPTS["Latin"],
    script_basic_chars=SCRIPTS["Latin"],
    spaced_words=True,
    cased=True,
    # Elided articles (l', d') end a word, so "d'Aster" still matches "Aster".
    word_chars=SCRIPTS["Latin"] + "0-9_",
    plural_suffix="(?:s|x)?",
    inflection_suffix="(?:s|x)?",
    sentence_end=".!?",
    stock_phrases=(
        "il est important de noter que",
        "il convient de noter que",
        "en conclusion",
        "force est de constater",
        "le temps semblait suspendu",
        "comme si le temps s'était arrêté",
    ),
    marker_examples=(
        "Elle était <I000>très</I000> fatiguée.",
        "<I000></I000>Chapitre <I001></I001> un",
    ),
    quotes="« »",
    nested_quotes="“ ”",
    ellipsis="…",
    dash="—",
    conventions=(
        ConventionRule(
            house="[«»]",
            slip='"',
            message='A straight quotation mark " is used here, but the book uses « » ({count} segments).',
            fix='Replace the straight quotation marks " with « and », a no-break space inside each.',
        ),
        ConventionRule(
            house="[«»]",
            slip="[“”]",
            message="English quotation marks “ ” are used here, but the book uses « » ({count} segments).",
            fix="Replace “ and ” with « and ».",
            nested_ok=True,  # “ ” is the French nested quote, inside « »
        ),
        ConventionRule(
            house=f"[{_NBSP}][;:!?]",
            slip=rf"(?<![{_NBSP}\d;:!?])[;:!?](?![\d/])",
            message="No no-break space before ; : ! or ? here, but the book uses one ({count} segments).",
            fix="Put a no-break space (U+00A0 or U+202F) before ; : ! and ?.",
        ),
    ),
    pronouns=("il", "elle"),
    address_forms=("tu", "vous"),
    stop_words=frozenset({"les", "des", "une", "que", "qui", "dans", "pour", "avec", "est", "sont", "était", "elle", "mais"}),
    function_words=frozenset({
        "le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "sont", "était", "être",
        "que", "qui", "dans", "pour", "avec", "pas", "ne", "je", "tu", "il", "elle", "nous",
        "vous", "ils", "elles", "mais", "ou", "sur", "au", "aux", "ce", "cette", "son", "sa",
        "ses", "mon", "ma", "mes", "c'est", "n'est", "j'ai", "plus", "très", "oui", "non",
    }),
    number_words="french",
    tier="profiled",
)

PROFILES["ja"] = LanguageProfile(
    code="ja",
    display_name="Japanese",
    short_name="Japanese",
    scripts=("Han", "Hiragana", "Katakana"),
    script_chars=_KANA_HAN,
    script_basic_chars=_KANA_HAN,
    spaced_words=False,
    cased=False,
    word_chars="",
    plural_suffix="",
    sentence_end="。！？",
    stock_phrases=(
        "言うまでもなく",
        "重要なのは",
        "結論として",
        "まるで時間が止まったかのように",
        "と言えるでしょう",
    ),
    marker_examples=(
        "彼女は<I000>とても</I000>疲れていた。",
        "<I000></I000>第一章<I001></I001>",
    ),
    quotes="「 」",
    nested_quotes="『 』",
    ellipsis="……",
    dash="――",
    conventions=(
        ConventionRule(
            house="[「」]",
            slip='["“”]',
            message='Quotation marks " or “ ” are used here, but the book uses 「 」 ({count} segments).',
            fix="Replace them with 「 and 」.",
        ),
        ConventionRule(
            house="……",
            slip=r"(?<!…)…(?!…)|\.\.\.",
            message="A single ellipsis … is used here, but the book uses …… ({count} segments).",
            fix="Replace … or ... with …….",
        ),
    ),
    pronouns=("彼", "彼女"),
    # Honorific suffixes rather than one polite pronoun.
    address_forms=("さん", "様", "君", "ちゃん"),
    # Katakana names separate their parts with a middle dot (ジョン・スミス).
    name_separator="・",
    number_words="japanese",
    tier="profiled",
)

PROFILES["es"] = LanguageProfile(
    code="es",
    display_name="Spanish",
    short_name="Spanish",
    scripts=("Latin",),
    script_chars=SCRIPTS["Latin"],
    script_basic_chars=SCRIPTS["Latin"],
    spaced_words=True,
    cased=True,
    word_chars=SCRIPTS["Latin"] + "0-9_",
    plural_suffix="(?:s|es)?",
    inflection_suffix="(?:s|es)?",
    sentence_end=".!?",
    stock_phrases=(
        "cabe destacar que",
        "es importante señalar que",
        "en conclusión",
        "un testimonio de",
        "como si el tiempo se hubiera detenido",
    ),
    marker_examples=(
        "Ella estaba <I000>muy</I000> cansada.",
        "<I000></I000>Capítulo <I001></I001> uno",
    ),
    # Publishers use « » or “ ”; the book's majority decides. A sentence opened
    # by either mark is opened: the Academy accepts ¡...? and ¿...!. A dash or
    # an ellipsis does not end one (¿Te gustan —te gustan— los perros?), nor
    # does the quotation mark before a dialogue tag («¿Sería útil», pensó, «hablarle?»).
    quotes="« »",
    nested_quotes="“ ”",
    ellipsis="…",
    dash="—",
    conventions=(
        ConventionRule(
            house="[«»“”]",
            slip='"',
            message='A straight quotation mark " is used here, but the book uses typographic quotes ({count} segments).',
            fix='Replace the straight quotation marks " with the book\'s « » or “ ”.',
        ),
        ConventionRule(
            house="¿",
            slip=r"(?:^|(?<=[.!?:;]))[^¿¡.!?:;]*[^\W\d_][^¿¡.!?:;]*\?",
            message="A question here has no opening ¿, but the book writes them ({count} segments).",
            fix="Open the question with ¿.",
        ),
        ConventionRule(
            house="¡",
            slip=r"(?:^|(?<=[.!?:;]))[^¿¡.!?:;]*[^\W\d_][^¿¡.!?:;]*!",
            message="An exclamation here has no opening ¡, but the book writes them ({count} segments).",
            fix="Open the exclamation with ¡.",
        ),
    ),
    pronouns=("él", "ella"),
    address_forms=("tú", "usted"),
    stop_words=frozenset({"los", "las", "del", "una", "que", "para", "con", "por", "pero", "como", "más", "era"}),
    function_words=frozenset({
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "y", "que", "en",
        "es", "era", "son", "no", "se", "por", "con", "para", "su", "sus", "lo", "le", "les",
        "pero", "como", "más", "muy", "este", "esta", "ese", "esa", "yo", "tú", "usted", "mi",
        "me", "te", "he", "ha", "había", "sí", "también", "ya",
    }),
    number_words="spanish",
    tier="profiled",
)

PROFILES["de"] = LanguageProfile(
    code="de",
    display_name="German",
    short_name="German",
    scripts=("Latin",),
    script_chars=SCRIPTS["Latin"],
    script_basic_chars=SCRIPTS["Latin"],
    spaced_words=True,
    cased=True,
    word_chars=SCRIPTS["Latin"] + "0-9_",
    # Case endings and plurals: des Käfers, den Männern, die Schwestern.
    plural_suffix="(?:e|en|em|n|er|ern|es|s)?",
    inflection_suffix="(?:e|en|em|n|er|ern|es|s)?",
    sentence_end=".!?",
    stock_phrases=(
        "es ist wichtig zu beachten",
        "zusammenfassend lässt sich sagen",
        "ein Zeugnis für",
        "als ob die Zeit stillstünde",
        "es sei darauf hingewiesen",
    ),
    marker_examples=(
        "Sie war <I000>sehr</I000> müde.",
        "<I000></I000>Kapitel <I001></I001> eins",
    ),
    # Publishers use „ “ or » «; the book's majority decides.
    quotes="„ “",
    nested_quotes="‚ ‘",
    ellipsis="…",
    dash="–",
    conventions=(
        ConventionRule(
            house="[„“»«]",
            slip='"',
            message='A straight quotation mark " is used here, but the book uses typographic quotes ({count} segments).',
            fix='Replace the straight quotation marks " with the book\'s „ “ or » «.',
        ),
        ConventionRule(
            house="„",
            slip="”",
            message="An English closing quotation mark ” is used here, but the book uses „ “ ({count} segments).",
            fix="Close the quotation with “.",
            replace=("”", "“"),
        ),
    ),
    pronouns=("er", "sie", "es"),
    address_forms=("du", "Sie"),
    stop_words=frozenset({"der", "die", "das", "und", "nicht", "eine", "einen", "dem", "den", "mit", "sich", "auch"}),
    function_words=frozenset({
        "der", "die", "das", "den", "dem", "des", "und", "ist", "war", "nicht", "ein", "eine",
        "einen", "einem", "einer", "zu", "mit", "sich", "auf", "auch", "es", "er", "sie", "ich",
        "du", "wir", "ihr", "aber", "wie", "noch", "nur", "schon", "im", "vom", "zum", "zur",
        "wenn", "dass", "doch", "hatte", "wurde", "sein", "seine", "ihre", "nach", "bei",
    }),
    extraction_note=(
        "German capitalizes every noun, so a capital letter does not mark a name: leave out "
        "ordinary nouns, family relations (Mutter, Vater) included."
    ),
    number_words="german",
    tier="profiled",
)

_HANGUL = SCRIPTS["Hangul"]

PROFILES["ko"] = LanguageProfile(
    code="ko",
    display_name="Korean",
    short_name="Korean",
    scripts=("Hangul",),
    script_chars=_HANGUL,
    script_basic_chars=_HANGUL,
    spaced_words=True,
    cased=False,
    word_chars=_HANGUL + "0-9_",
    plural_suffix="",
    # Case and topic particles, the copula, and the plural and honorific
    # suffixes a noun takes before them (학생들에게는, 선생님이었다).
    attached_particles=(
        "이", "가", "은", "는", "을", "를", "의", "에", "에게", "에게서", "에서", "께", "께서",
        "한테", "더러", "로", "으로", "로서", "으로서", "로써", "으로써", "와", "과", "랑", "이랑",
        "하고", "도", "만", "까지", "부터", "조차", "마저", "밖에", "뿐", "마다", "처럼", "같이",
        "만큼", "보다", "나", "이나", "든", "이든", "며", "이며", "고", "이고", "야", "이야", "아",
        "여", "이여", "요", "이요", "라", "이라", "라고", "이라고", "라는", "이라는", "란", "이란",
        "다", "이다", "였다", "이었다", "입니다", "인", "들", "님", "씨", "네",
    ),
    sentence_end=".!?",
    stock_phrases=(
        "주목할 만한 점은",
        "결론적으로",
        "마치 시간이 멈춘 듯",
        "라고 할 수 있습니다",
        "의심할 여지 없이",
    ),
    marker_examples=(
        "그녀는 <I000>매우</I000> 피곤했다.",
        "<I000></I000>제1장<I001></I001>",
    ),
    quotes="“ ”",
    nested_quotes="‘ ’",
    ellipsis="……",
    dash="—",
    conventions=(
        ConventionRule(
            house="[“”]",
            slip='"',
            message='A straight quotation mark " is used here, but the book uses “ ” ({count} segments).',
            fix='Replace the straight quotation marks " with “ and ”.',
        ),
    ),
    # Korean marks address by speech level, not a pronoun; the style sheet keeps
    # the third-person pronoun and the character's voice.
    pronouns=("그", "그녀"),
    number_words="korean",
    tier="profiled",
)

_GENERIC: dict[str, LanguageProfile] = {}


def _profile_for_code(code: str) -> LanguageProfile:
    """The tuned profile for a stored code, the base language's for a regional
    variant of it (`en-GB`), or a generic one."""
    if code in PROFILES:
        return PROFILES[code]
    language, script_tag, region = _subtags(code)
    if (
        not script_tag
        and language in PROFILES
        and not (language == "zh" and region in _TRADITIONAL_REGIONS)
    ):
        return PROFILES[language]
    if code not in _GENERIC:
        _GENERIC[code] = _generic_profile(code)
    return _GENERIC[code]


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
    """A BCP 47 language code ("en", "zh", "ja", "pt-BR").

    Compares, hashes, and serializes as the code. `zh` is Simplified Chinese,
    and its other spellings (`zh-Hans`, `zh-CN`) become `zh`.
    """

    ENGLISH: ClassVar[Language]
    CHINESE: ClassVar[Language]
    _interned: ClassVar[dict[str, Language]] = {}

    def __new__(cls, code: str) -> Language:
        if isinstance(code, Language):
            return code
        if not isinstance(code, str) or not _CODE.fullmatch(code.strip()):
            raise ValueError(f"{code!r} is not a language code such as en, fr, ja, or pt-BR")
        code = _canonical_code(code.strip())
        code = _CODE_ALIASES.get(code, code)
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
        if str(self) in PROFILES or profile(self).tier == "generic":
            return profile(self).display_name
        return _generic_profile(str(self)).display_name  # a regional variant: "English (GB)"

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return _pydantic_string_schema(cls)


# The two pairs that predate profiles keep their spelling in every stored file.
_LEGACY_PAIRS = {"en-zh": ("en", "zh"), "zh-en": ("zh", "en")}


class _PairType(type):
    def __iter__(cls) -> Iterator[LanguagePair]:
        """Every pair of profiled languages, in profile order."""
        return iter(
            cls(f"{source}>{target}") for source in TUNED_PROFILES for target in TUNED_PROFILES if source != target
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
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not a language pair; write it as source>target, e.g. en>ja")
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

    @classmethod
    def of(cls, source: Language | str, target: Language | str) -> LanguagePair:
        return cls(f"{Language(source)}>{Language(target)}")

    def __getnewargs__(self) -> tuple[str]:
        return (str(self),)

    def __repr__(self) -> str:
        return f"LanguagePair({str(self)!r})"

    @property
    def value(self) -> str:
        return str(self)

    @property
    def legacy(self) -> bool:
        """One of the two pairs that predate profiles (`en-zh`, `zh-en`)."""
        return str(self) in _LEGACY_PAIRS

    @property
    def slug(self) -> str:
        """The pair in a file or job name, where `>` is not allowed: `en-zh`, `en-ja`."""
        return str(self).replace(">", "-")

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


# Letters of any profiled script.
ANY_SCRIPT = re.compile("[" + "".join(item.script_chars for item in TUNED_PROFILES.values()) + "]")
# Letters of scripts written without spaces: about one model token each.
UNSPACED_SCRIPT = re.compile(
    "[" + "".join(item.script_chars for item in TUNED_PROFILES.values() if not item.spaced_words) + "]"
)
# Characters that end a sentence in any profiled language.
SENTENCE_END = "".join(item.sentence_end for item in TUNED_PROFILES.values())


def profile(language: Language | str) -> LanguageProfile:
    """The profile for a language or language code (a generic one when none is written)."""
    return _profile_for_code(Language(language))


def scripts_disjoint(direction: LanguagePair) -> bool:
    """Whether the script alone tells source text from target text (en/zh: yes; en/de, zh/ja: no)."""
    source = profile(direction.source_language).scripts
    target = profile(direction.target_language).scripts
    return bool(source) and bool(target) and not set(source) & set(target)


def leftover_scripts(direction: LanguagePair) -> tuple[str, ...]:
    """Source scripts a target never uses: their letters in a translation are left-over source text."""
    target = profile(direction.target_language).scripts
    if not target:
        return ()
    return tuple(item for item in profile(direction.source_language).scripts if item not in target)


# In a pair sharing a script, a short identical line is usually a name or a
# title kept on purpose ("Paris.", "DEAN FORSYTH."); only a longer one counts.
SHARED_SCRIPT_IDENTICAL_MIN = 20


def copy_is_untranslated(text: str, direction: LanguagePair) -> bool:
    """Whether a translation identical to `text` counts as left untranslated."""
    return scripts_disjoint(direction) or sum(ch.isalnum() for ch in text) >= SHARED_SCRIPT_IDENTICAL_MIN


_FUNCTION_WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?")


def reads_as_source(text: str, direction: LanguagePair) -> bool:
    """Whether a translation is, as a whole, still written in the source
    language, for a pair sharing a script: at least four of the source's own
    function words and more than three for each of the target's. (A copy with
    its spelling modernized, "daß" to "dass", is not identical to the source.)
    The audit looks for single left-over passages; this is the whole text."""
    if leftover_scripts(direction):
        return False
    source = profile(direction.source_language).function_words
    target = profile(direction.target_language).function_words
    if not source or not target:
        return False
    words = [word.casefold().replace("’", "'") for word in _FUNCTION_WORD.findall(text)]
    source_hits = sum(word in source and word not in target for word in words)
    target_hits = sum(word in target and word not in source for word in words)
    return source_hits >= 4 and source_hits > 3 * target_hits


# A sentence of Japanese has kana in it. A translation this long written only
# in the script the two languages share is the source left as it was; a
# heading or a name ("第一章 序") is shorter.
SHARED_SCRIPT_ONLY_MIN_CHARACTERS = 10


def lacks_target_script(text: str, direction: LanguagePair) -> bool:
    """Whether a translation has none of the scripts only the target is written
    in (kana, for Chinese into Japanese) while long enough to need them."""
    source_scripts = profile(direction.source_language).scripts
    target_scripts = profile(direction.target_language).scripts
    own = [name for name in target_scripts if name not in source_scripts]
    shared = [name for name in target_scripts if name in source_scripts]
    if not own or not shared:
        return False
    if re.search("[" + "".join(SCRIPTS[name] for name in own) + "]", text):
        return False
    letters = re.findall("[" + "".join(SCRIPTS[name] for name in shared) + "]", text)
    return len(letters) >= SHARED_SCRIPT_ONLY_MIN_CHARACTERS


# Two languages written without spaces in one script (Chinese and Japanese)
# share words, names, and four-character phrases; a run this long copied
# character for character from the source is a clause left untranslated.
COPIED_RUN_MIN_CHARACTERS = 6


def copied_source_run(source: str, text: str, direction: LanguagePair) -> str:
    """A run of the shared script in a translation that stands in the source
    as written, for a pair of unspaced languages sharing a script; else ""."""
    source_rules, target_rules = profile(direction.source_language), profile(direction.target_language)
    shared = [name for name in source_rules.scripts if name in target_rules.scripts]
    if not shared or source_rules.spaced_words or target_rules.spaced_words:
        return ""
    chars = "".join(SCRIPTS[name] for name in shared)
    length = COPIED_RUN_MIN_CHARACTERS
    for run in re.findall(f"[{chars}]{{{length},}}", text):
        for start in range(len(run) - length + 1):
            if run[start : start + length] in source:
                return run
    return ""


def glossary_pair(direction: LanguagePair) -> LanguagePair:
    """The pair a job's glossary is written in: its source and target sides.

    The en/zh glossary pipeline keys every entry by its English term and
    resolves a Chinese rendering, whichever way the book is translated, so a
    zh-en job uses an en-zh glossary. Any other job's glossary has its own pair.
    """
    return LanguagePair.EN_TO_ZH if direction.legacy else direction


def glossary_language_names(pair: LanguagePair) -> dict[str, str]:
    """A glossary pair for an interface: its code and each side's language name."""
    return {
        "pair": pair.value,
        "source": pair.source_language.display_name,
        "target": pair.target_language.display_name,
    }


def glossary_names(pair: LanguagePair) -> tuple[str, str]:
    """What the model and pre-phase-3 files call an entry's two sides."""
    return ("english", "chinese") if pair.legacy else ("source", "target")


def language_support(direction: LanguagePair) -> dict[str, Any]:
    """The pair's tiers and the checks its profiles cannot run (docs/GENERIC_LANGUAGES.md, 2.3)."""
    source = profile(direction.source_language)
    target = profile(direction.target_language)
    skipped: list[dict[str, str]] = []

    def skip(check: str, reason: str) -> None:
        skipped.append({"check": check, "reason": reason})

    if not scripts_disjoint(direction):
        skip(
            "untranslated text",
            "source and target share a script: only a translation identical to a long source is "
            "flagged by script; left-over source words and the semantic audit cover the rest",
        )
    unspaced_shared = (
        not source.spaced_words and not target.spaced_words and bool(set(source.scripts) & set(target.scripts))
    )
    if (
        not leftover_scripts(direction)
        and not (source.function_words and target.function_words)
        # Chinese and Japanese: a run copied from the source, or no kana, shows it.
        and not unspaced_shared
    ):
        skip("left-over source words", "no script or function words tell the source from the target")
    if not (source.number_words and target.number_words):
        skip("number words", "numbers written as words go to the semantic audit's numeric ruling")
    if not target.conventions:
        skip("punctuation conventions", f"no house conventions for {target.display_name}")
    if not target.consistency_report:
        skip("character report", f"the consistency report's character section is written for Chinese")
    if not target.pronouns:
        skip("style-sheet pronouns", "characters in the style sheet keep their voice only")
    if not target.stock_phrases:
        skip("formulaic phrases", f"no phrase list for {target.display_name}")
    if not (source.marker_examples and target.marker_examples):
        skip("marker examples", "the translation prompt states the marker rules in words only")
    if target.tier != "tuned":
        skip("prose rewrite", "its prompt and rules are written for Chinese; reprose stays off")
    notice = ""
    if source.tier != "tuned" or target.tier != "tuned":
        # docs/GENERIC_LANGUAGES.md, 2.8: no pivot through English; say the risk instead.
        notice = (
            "Quality depends on the local model: many are much weaker outside English and "
            "Chinese, and weaker still between two languages other than English. Expect more "
            "passages in human review, and try the model on a chapter first."
        )
    return {
        "pair": direction.value,
        "source": {"code": direction.source_language.value, "name": source.display_name, "tier": source.tier},
        "target": {"code": direction.target_language.value, "name": target.display_name, "tier": target.tier},
        "skipped": skipped,
        "notice": notice,
    }


def language_catalog() -> list[dict[str, str]]:
    """Languages to offer in a picker: the profiled ones first, then common
    languages by name. Any other BCP 47 code is accepted too."""
    profiled = [
        {"code": code, "name": item.display_name, "tier": item.tier} for code, item in PROFILES.items()
    ]
    others = [
        {"code": code, "name": _generic_profile(code).display_name, "tier": "generic"}
        for code in [*_LANGUAGES, "zh-Hant"]
        if code not in PROFILES
    ]
    return profiled + sorted(others, key=lambda item: item["name"])


def build_direction_instruction(direction: LanguagePair) -> str:
    """Build the non-optional language constraint for a translation prompt."""
    source = direction.source_language.display_name
    target = direction.target_language.display_name
    return (
        f"Translate every source passage from {source} into {target}. "
        f"Output translated {target} only, apart from protected structural markers."
    )


def glossary_sides(entry, direction: LanguagePair) -> tuple[str, str]:
    """(source term, target term) of a job's glossary entry: swapped when the
    glossary runs the other way (an en-zh glossary in a zh-en job)."""
    if glossary_pair(direction) == direction:
        return entry.source, entry.target
    return entry.target, entry.source


def source_aliases(entry, direction: LanguagePair) -> list[str]:
    """An entry's aliases when they are variants of the job's source term, else none.

    Aliases belong to the glossary's source side, so a swapped glossary has none.
    """
    return list(entry.aliases) if glossary_pair(direction) == direction else []
