"""Shared, deterministic policy for deciding which segments are translatable prose."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from enum import Enum
from typing import Iterable

from .languages import TranslationDirection
from .schemas import GlossaryEntry, is_preservable_technical_identifier


class SegmentKind(str, Enum):
    PROSE = "prose"
    LANGUAGE_NEUTRAL = "language_neutral"
    PROTECTED_IDENTIFIER = "protected_identifier"
    STRUCTURAL = "structural"


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[A-Za-z]")
_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_SEPARATOR = re.compile(r"^[\W_]+$", flags=re.UNICODE)
_URI = re.compile(r"(?:https?|ftp)://\S+|mailto:\S+", flags=re.IGNORECASE)
_ABBREVIATED_METADATA_FIELD = re.compile(
    r"^[A-Za-z]{1,3}\.\s+[A-Za-z]{1,3}\.$"
)
_TECHNICAL_PUNCTUATION = re.compile(r"[._+/#-]")
_ISBN_IDENTIFIER = re.compile(
    r"^ISBN(?:-1[03])?\s*:?\s*(?=[0-9Xx -]{10,}$)[0-9Xx]+(?:[ -][0-9Xx]+)+$",
    flags=re.IGNORECASE,
)
_PUBLICATION_REVISION_IDENTIFIER = re.compile(
    r"^[A-Za-z]{1,8}\s+r\d+(?:\.\d+)+$",
    flags=re.IGNORECASE,
)
_PUBLICATION_METADATA = re.compile(
    r"^(?:"
    r"copyright\b|all rights reserved\b|first published\b|"
    r"originally published\b|printed (?:in|by)\b|published by\b|"
    r"a catalogue record\b|library of congress\b|cover (?:art|design)\b|"
    r"typeset by\b"
    r")",
    flags=re.IGNORECASE,
)
_LIBRARY_CALL_IDENTIFIER = re.compile(
    r"^(?:"
    r"[A-Z]{1,3}\d{3,}(?:\.[A-Z0-9]+)+(?:\s+\d{4})?"
    r"|\d{3}(?:['\u2019]?\.\d+)?\s*[-\u2010-\u2015]\s*[A-Za-z]{1,4}\d{1,4}"
    r")$"
)
_IDENTIFIER_WITH_DIGIT = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)*(?![A-Za-z0-9])"
)
_SPACED_NUMBERED_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2,}\s*#\s*\d+(?![A-Za-z0-9])"
)
_SPACED_WORD_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>[A-Z]{2,4})[ -]+(?P<number>(?i:zero|one|two|"
    r"three|four|five|six|seven|eight|nine|ten))(?![A-Za-z])",
)
_NAMED_WORD_IDENTIFIER = re.compile(
    r"\b(?:ship|vessel|station|sector|gate|unit|chapter|volume|book)[ -]+"
    r"(?P<number>zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)\b",
    flags=re.IGNORECASE,
)
_ARABIC_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])[+-]?(?:\d+(?:[.,]\d+)*|\.\d+)(?:万|亿)?(?!\d)"
)
_ARABIC_ENGLISH_MAGNITUDE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>[+-]?\d+(?:[.,]\d+)*)\s+"
    r"(?P<scale>thousand|million|billion|trillion)\b",
    flags=re.IGNORECASE,
)
_ARABIC_PERCENT = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>[+-]?(?:\d+(?:[.,]\d+)*|\.\d+))[\s-]*"
    r"(?:percent\b|per[ -]+cent\b|%)",
    flags=re.IGNORECASE,
)
_ENGLISH_PERCENT_RANGE = re.compile(
    r"\b(?P<start>zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[ -]+(?:to|through)[ -]+"
    r"(?P<end>zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[ -]+(?:percent|per[ -]+cent)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_SIMPLE_PERCENT = re.compile(
    r"\b(?P<number>zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"twenty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"thirty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"forty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"fifty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"sixty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"seventy[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"eighty[- ](?:one|two|three|four|five|six|seven|eight|nine)|"
    r"ninety[- ](?:one|two|three|four|five|six|seven|eight|nine))"
    r"\s+(?:percent|per[ -]+cent)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_HALF_PERCENT = re.compile(
    r"\bhalf[ -]+(?:a[ -]+)?(?:percent|per[ -]+cent)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_WRITTEN_PERCENT = re.compile(
    r"\b(?P<number>"
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
    r"(?:(?:[ -]+and)?[ -]+(?:zero|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    r"ninety|hundred))*"
    r")[ -]+(?:percent|per[ -]+cent)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_HALF_MAGNITUDE = re.compile(
    r"\bhalf[ -]+(?:a[ -]+)?(?P<scale>thousand|million|billion|trillion)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_DURATION_HALF_MAGNITUDE = re.compile(
    r"\bhalf[ -]+(?:a[ -]+)?(?P<scale>decade|century|millennium)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_BY_HALF_AGAIN = re.compile(r"\bby[ -]+half[ -]+again\b", flags=re.IGNORECASE)
_ENGLISH_HALF_AGAIN = re.compile(r"\bhalf[ -]+again\b", flags=re.IGNORECASE)
_ENGLISH_HALF_AS_MUCH = re.compile(
    r"\bhalf[ -]+as[ -]+much\b",
    flags=re.IGNORECASE,
)
_ENGLISH_HALF_COMPOUND_TENTH = re.compile(
    r"\bhalf[ -]+(?:a[ -]+)?[A-Za-z]+[ -]+tenth\b",
    flags=re.IGNORECASE,
)
_ENGLISH_MIXED_HALF_MAGNITUDE = re.compile(
    r"\b(?P<whole>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[ -]+and[ -]+a[ -]+half[ -]+"
    r"(?P<scale>hundred|thousand|million|billion|trillion)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_PARTIAL_STATE_HALF = re.compile(
    r"\bhalf[ -]+(?:open|closed|loaded|empty|full|finished|complete|done)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_BISECTION = re.compile(
    r"\b(?:crack(?:ed|ing)?|split(?:ting)?|cut(?:ting)?|shear(?:ed|ing)?|"
    r"scissor(?:ed|ing|s)?|"
    r"break(?:ing)?|broke(?:n)?|divid(?:e|ed|ing))"
    r"(?:[ -]+[A-Za-z]+){0,6}[ -]+(?:in|into)[ -]+(?:half|halves|two)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_APPROXIMATE_MAGNITUDE = re.compile(
    r"\b(?P<count>tens|hundreds|thousands)[ -]+of[ -]+"
    r"(?P<scale>hundreds|thousands|millions|billions|trillions)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_SIMPLE_APPROXIMATE_MAGNITUDE = re.compile(
    r"\b(?:hundreds|(?:a[ -]+)?couple(?:[ -]+of)?[ -]+hundred)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_BARE_APPROXIMATE_MAGNITUDE = re.compile(
    r"\b(?P<scale>hundreds|thousands|millions|billions|trillions)[ -]+of\b",
    flags=re.IGNORECASE,
)
_ENGLISH_STANDALONE_APPROXIMATE_MAGNITUDE = re.compile(
    r"\b(?P<scale>hundreds|thousands|millions|billions|trillions|centuries)\b"
    r"(?![ -]+(?:of|upon)\b)",
    flags=re.IGNORECASE,
)
_ENGLISH_REPEATED_APPROXIMATE_MAGNITUDE = re.compile(
    r"\b(?P<scale>tens|hundreds|thousands)[ -]+upon[ -]+(?P=scale)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_SCORE_COUNT = re.compile(
    r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty)[ -]+score\b",
    flags=re.IGNORECASE,
)
_ENGLISH_QUANTIFIED_DURATION = re.compile(
    r"\b(?P<count>\d+(?:,\d{3})*|one|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty)[ -]+"
    r"(?P<scale>decades?|centur(?:y|ies)|millenn(?:ium|ia))\b",
    flags=re.IGNORECASE,
)
_CHINESE_COUNTDOWN_NUMBER = re.compile(r"[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e]+")
_ENGLISH_FRACTION_OF_MAGNITUDE = re.compile(
    r"\b(?P<numerator>a|one|two|three|four|five|six|seven|eight|nine)[ -]+"
    r"(?P<denominator>half|halves|third|thirds|quarter|quarters|fifth|fifths|"
    r"sixth|sixths|seventh|sevenths|eighth|eighths|ninth|ninths|tenth|tenths)"
    r"[ -]+of[ -]+(?:a[ -]+)?"
    r"(?P<scale>thousand|million|billion|trillion)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_WRITTEN_MAGNITUDE = re.compile(
    r"\b(?P<number>"
    r"(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|million|billion|trillion)(?:[ -]+and)?[ -]+)*"
    r"(?:hundred|thousand|million|billion|trillion)"
    r"(?:[ -]+(?:and[ -]+)?(?:one|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|first|"
    r"second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
    r"twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
    r"nineteenth|twentieth|thirtieth|fortieth|fiftieth|sixtieth|seventieth|"
    r"eightieth|ninetieth))*)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_COMPOUND_MAGNITUDE = re.compile(
    r"\b(?:(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[ -]+)?"
    r"(?P<left>thousand|million|billion|trillion)[ -]+"
    r"(?P<right>thousand|million|billion|trillion)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_CLOCK_TIME = re.compile(
    r"\b(?P<hour>zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty(?:[- ](?:one|two|three))?)[ -]+"
    r"(?P<minute>zero|oh[ -]+(?:one|two|three|four|five|six|seven|eight|nine)|"
    r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"thirty(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"forty(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|"
    r"fifty(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)"
    r"(?=\s+(?:this\s+)?(?:morning|afternoon|evening|tonight)\b|"
    r"\s*(?:a\.?m\.?|p\.?m\.?)\b)",
    flags=re.IGNORECASE,
)
_ENGLISH_WORD_DECIMAL = re.compile(
    r"\b(?:zero|oh)[ -]+point[ -]+(?P<digits>(?:zero|oh|one|two|three|four|"
    r"five|six|seven|eight|nine)(?:[ -]+(?:zero|oh|one|two|three|four|five|"
    r"six|seven|eight|nine))*)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_FRACTION = re.compile(
    r"\b(?:(?P<numerator>a|one|two|three|four|five|six|seven|eight|nine)[ -]+"
    r"(?P<denominator>half|halves|third|thirds|quarter|quarters|fifth|fifths|"
    r"sixth|sixths|seventh|sevenths|eighth|eighths|ninth|ninths|tenth|tenths)|"
    r"(?P<standalone_half>half))\b",
    flags=re.IGNORECASE,
)
_ENGLISH_ARTICLE_ORDINAL_NOUN = re.compile(
    r"\b(?:a|an)[ -]+(?:first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth)[ -]+(?!of\b)(?=[A-Za-z])",
    flags=re.IGNORECASE,
)
_CHINESE_FRACTION = re.compile(
    r"(?P<denominator>[零〇一二两三四五六七八九十百千万亿]+)分之"
    r"(?P<numerator>[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?)"
)
_CHINESE_PERCENT_RANGE = re.compile(
    r"百分之(?P<start>[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?)"
    r"(?:到|至)(?P<end>[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?)"
)
_CHINESE_FRACTION_OF_MAGNITUDE_PREFIX = re.compile(
    r"(?P<denominator>[零〇一二两三四五六七八九十百千万亿]+)分之"
    r"(?P<numerator>[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?)"
    r"(?P<magnitude>[零〇一二两三四五六七八九十百千万亿]*[万亿])"
)
_CHINESE_FRACTION_OF_MAGNITUDE_SUFFIX = re.compile(
    r"(?P<magnitude>[零〇一二两三四五六七八九十百千万亿]*[万亿])"
    r"(?:[\u3400-\u9fff]{0,8})的"
    r"(?P<denominator>[零〇一二两三四五六七八九十百千万亿]+)分之"
    r"(?P<numerator>[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:点[零〇一二两三四五六七八九]+)?)"
)
_CHINESE_MAGNITUDE = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿]*[百千万亿]"
    r"[零〇一二两三四五六七八九十百千万亿]*"
)
_CHINESE_APPROXIMATE_MAGNITUDE = re.compile(
    r"数以(?P<number>[万亿])计"
)
_CHINESE_SIMPLE_APPROXIMATE_MAGNITUDE = re.compile(
    r"(?:几|数|上)(?P<scale>[百千万亿])(?![百千万亿])"
)
_CHINESE_RELATIVE_HALF_INCREASE = re.compile(
    r"(?:增加|提高|提升|增长|延长|扩大|增大|多出|大出|大上|超出)"
    r"[^。！？；]{0,24}(?:一半|百分之五十|50\s*[%％])"
)
_ENGLISH_ZERO_POSITIVE_PERCENT = re.compile(
    r"\b(?:zero|0)\s*(?:percent|per[ -]+cent|%)"
    r"[^.!?;]{0,48}\b(?:useful|effective|productive|successful)\b",
    flags=re.IGNORECASE,
)
_ENGLISH_FULL_NEGATIVE_PERCENT = re.compile(
    r"\b(?:one[ -]+hundred|100)\s*(?:percent|per[ -]+cent|%)"
    r"[^.!?;]{0,48}\b(?:useless|ineffective|unproductive|unsuccessful)\b",
    flags=re.IGNORECASE,
)
_CHINESE_FULL_NEGATIVE_PERCENT = re.compile(
    r"(?:\u767e\u5206\u4e4b\u767e|100\s*[%\uff05]|\u5168\u90e8|\u5168\u7136)"
    r"[^\u3002\uff01\uff1f\uff1b]{0,32}"
    r"(?:\u65e0\u7528|\u6ca1\u7528|\u65e0\u6548|\u4e0d\u8d77\u4f5c\u7528|\u6beb\u65e0\u7528\u5904)",
)
_CHINESE_ZERO_POSITIVE_PERCENT = re.compile(
    r"(?:\u767e\u5206\u4e4b\u96f6|0\s*[%\uff05]|\u6ca1\u6709\u4efb\u4f55)"
    r"[^\u3002\uff01\uff1f\uff1b]{0,32}"
    r"(?:\u6709\u7528|\u6709\u6548|\u8d77\u4f5c\u7528|\u6709\u6210\u6548)",
)
_CHINESE_DECIMAL = re.compile(
    r"(?P<whole>[零〇一二两三四五六七八九十百千万亿]+)点"
    r"(?P<digits>[零〇一二两三四五六七八九]+)"
)
_CHINESE_CONTEXTUAL_ZERO = re.compile(
    r"(?:恰好|正好|等于|降至|为)\s*(?P<number>零)(?![点十百千万亿])"
)
_CHINESE_NONNUMERIC_WANYI = re.compile(
    r"(?<![零〇一二两三四五六七八九十百千万亿])万一"
    r"(?![零〇一二两三四五六七八九十百千万亿])"
)
_CHINESE_HALF = re.compile(
    r"半(?=(?:个|米|公里|英里|年|月|日|天|小时|分钟|钟|秒|船身|世界|圈|倍|成))"
)
_CHINESE_SUFFIX_HALF = re.compile(
    r"(?:(?<=\u4e2a)|(?<=\u7c73)|(?<=\u516c\u91cc)|(?<=\u82f1\u91cc)|"
    r"(?<=\u82f1\u5c3a)|(?<=\u5e74)|(?<=\u6708)|(?<=\u65e5)|(?<=\u5929)|(?<=\u5206)|"
    r"(?<=\u5c81)|(?<=\u5c0f\u65f6)|(?<=\u5206\u949f)|(?<=\u79d2)|"
    r"(?<=\u500d))\u534a"
)
_CHINESE_STANDALONE_HALF = re.compile(r"(?:\u4e00\u534a|\u534a\u6570)")
_CHINESE_TWO_HALVES = re.compile(r"\u4e24\u534a")
_CHINESE_BISECTION = re.compile(
    r"(?:\u88c2|\u5288|\u5207|\u526a|\u5206|\u65a9|\u65ad)"
    r"[^\u3002\uff01\uff1f\uff1b]{0,8}(?:\u6210|\u4e3a)?\u4e86?"
    r"\u4e24(?:\u534a|\u622a|\u6bb5)"
)
_CHINESE_EXTRA_PREFIX_HALF = re.compile(r"\u534a(?=\u622a)")
_CHINESE_CLOCK_TIME = re.compile(
    r"(?P<period>\u4e0a\u5348|\u4e0b\u5348|\u665a\u4e0a|\u4e2d\u5348|\u51cc\u6668)?\s*"
    r"(?P<hour>[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)"
    r"[\u70b9\u6642\u65f6]"
    r"(?P<minute>[\u96f6\u3007\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\u5206?"
)
_CHINESE_MAGNITUDE_UNIT = re.compile(
    r"^(?:\u4e2a|\u9897|\u53ea|\u672c|\u5f20|\u5e45|\u6761|\u9762|\u6b21|\u53f7|"
    r"\u5e74|\u8f7d|\u6708|\u65e5|\u5929|\u5c81|\u4eba|\u4f4d|\u540d|\u5c42|\u7ae0|\u8282|"
    r"\u5377|\u9875|\u884c|\u5217|\u500d|\u6210|\u5206|\u7c73|\u516c\u91cc|"
    r"\u82f1\u91cc|\u82f1\u5c3a|\u78c5|\u5ea6|\u5468|\u79cd|\u7aef|\u7248|"
    r"\u5927\u9053|\u8857|\u91cc|\u5c3e|\u5904|%|\uff05)"
)
_ENGLISH_MONTH = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b(?=(?:\s+\d{1,2},?)?\s+\d{4}\b)",
    flags=re.IGNORECASE,
)
_ENGLISH_NUMBER_WORD = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|billion|trillion|first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|"
    r"seventeenth|eighteenth|nineteenth|twentieth|thirtieth|fortieth|fiftieth|"
    r"sixtieth|seventieth|eightieth|ninetieth|hundredth|thousandth|millionth|"
    r"billionth)(?:[ -]+and)?(?:[ -]+(?:zero|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|billion|trillion))*\b",
    flags=re.IGNORECASE,
)
_CHINESE_NUMBER = re.compile(
    r"(?<![另每某任这那哪各])第?(?P<number>[零〇一二两三四五六七八九十百千万亿]+)"
    r"(?=(?:个|颗|只|本|张|幅|条|面|次|号|年|月|日|天|岁|位|名|层|章|节|卷|"
    r"页|行|列|倍|成|分|米|公里|英里|磅|度|周|种|端|版|大道|街|尾|处|％|%))"
)

_SMALL_ENGLISH_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_ENGLISH_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
    "seventieth": 70, "eightieth": 80, "ninetieth": 90,
    "hundredth": 100, "thousandth": 1_000, "millionth": 1_000_000,
    "billionth": 1_000_000_000,
}
_CHINESE_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_MONTH_NUMBERS = {
    name: index
    for index, name in enumerate(
        (
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ),
        start=1,
    )
}


def visible_segment_text(text: str) -> str:
    """Remove pipeline-owned inline markers and normalize compatibility forms."""
    return unicodedata.normalize("NFKC", _INLINE_MARKER.sub("", text)).strip()


def full_span_inline_wrapper_ids(text: str) -> list[str]:
    """Return marker IDs whose balanced wrappers enclose the complete segment."""
    wrapper_ids: list[str] = []
    inner = text
    while True:
        match = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner,
            flags=re.DOTALL,
        )
        if match is None:
            return wrapper_ids
        wrapper_ids.append(match.group("id"))
        inner = match.group("inner")


def classify_segment(
    source_text: str,
    target_text: str,
    direction: TranslationDirection,
    glossary: Iterable[GlossaryEntry] = (),
) -> SegmentKind:
    """Classify a segment using conservative, language-aware mechanical evidence."""
    source = visible_segment_text(source_text)
    target = visible_segment_text(target_text)
    if source and _SEPARATOR.fullmatch(source):
        return SegmentKind.STRUCTURAL
    if source and _URI.fullmatch(source):
        return SegmentKind.PROTECTED_IDENTIFIER
    if source and _ABBREVIATED_METADATA_FIELD.fullmatch(source):
        return SegmentKind.PROTECTED_IDENTIFIER
    if source and _PUBLICATION_METADATA.match(source):
        return SegmentKind.PROTECTED_IDENTIFIER
    if _is_glossary_approved_literal(source, target, direction, glossary):
        return SegmentKind.PROTECTED_IDENTIFIER
    if (
        source == target
        and _LATIN.search(source)
        and (
            _is_conservative_technical_identifier(source)
            or _is_conservative_technical_identifier_sequence(source)
        )
    ):
        return SegmentKind.PROTECTED_IDENTIFIER
    has_source_prose = (
        bool(_LATIN.search(source))
        if direction is TranslationDirection.EN_TO_ZH
        else bool(_CJK.search(source))
    )
    if has_source_prose and _looks_like_translated_heading(source, target, direction):
        return SegmentKind.STRUCTURAL
    return SegmentKind.PROSE if has_source_prose else SegmentKind.LANGUAGE_NEUTRAL


def is_intentionally_preserved(
    source_text: str,
    target_text: str,
    direction: TranslationDirection,
    glossary: Iterable[GlossaryEntry] = (),
) -> bool:
    """Return whether equal visible content is valid under the shared policy."""
    if visible_segment_text(source_text) != visible_segment_text(target_text):
        return False
    return classify_segment(source_text, target_text, direction, glossary) is not SegmentKind.PROSE


def should_run_language_check(kind: SegmentKind) -> bool:
    return kind is SegmentKind.PROSE


def should_run_length_check(kind: SegmentKind) -> bool:
    return kind is SegmentKind.PROSE


def should_run_semantic_audit(kind: SegmentKind) -> bool:
    return kind is SegmentKind.PROSE


def number_tokens(text: str) -> Counter[str]:
    """Return conservative, cross-language objective number facts."""
    # Marker boundaries must not concatenate prose into synthetic identifiers such
    # as ``LLC<I000></I000>175`` -> ``LLC175``.
    visible = unicodedata.normalize("NFKC", _INLINE_MARKER.sub(" ", text)).strip()
    countdown = _countdown_number_tokens(visible)
    if countdown:
        return countdown
    facts: list[str] = []
    masked = list(visible)
    # ``万一`` is normally the lexical conjunction/adverb "if/by any chance",
    # not the malformed numeral 10,001. Mask the standalone idiom while leaving
    # conventional forms such as ``一万一千`` available to the number parser.
    for match in _CHINESE_NONNUMERIC_WANYI.finditer(visible):
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    # Indefinite-article ordinals describe an item in a sequence (for example,
    # "a third species" or "a fifth columnist"), not fractional quantities.
    # Leave them to the lower-confidence lexical-number comparison instead of
    # turning them into objective 1/3 or 1/5 facts.
    for match in _ENGLISH_ARTICLE_ORDINAL_NOUN.finditer(visible):
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    identifier_matches = [
        *_SPACED_NUMBERED_IDENTIFIER.finditer(visible),
        *_IDENTIFIER_WITH_DIGIT.finditer(visible),
    ]
    for match in sorted(identifier_matches, key=lambda item: item.start()):
        value = match.group(0)
        if not any(character.isdigit() for character in value):
            continue
        if any(character != " " for character in masked[match.start():match.end()]):
            facts.append(_canonical_identifier_fact(value))
        masked[match.start():match.end()] = " " * len(value)

    def add_matches(pattern, convert) -> None:
        current = "".join(masked)
        for match in pattern.finditer(current):
            value = convert(match)
            if value is not None:
                facts.append(_canonical_numeric_fact(value))
            masked[match.start():match.end()] = " " * (match.end() - match.start())

    add_matches(
        _SPACED_WORD_IDENTIFIER,
        lambda match: _SMALL_ENGLISH_NUMBERS[match.group("number").casefold()],
    )
    add_matches(
        _NAMED_WORD_IDENTIFIER,
        lambda match: _parse_english_number(match.group("number")),
    )
    add_matches(
        _ARABIC_PERCENT,
        lambda match: _parse_arabic_number(match.group("number")) / 100,
    )
    add_matches(
        _ENGLISH_PERCENT_RANGE,
        lambda match: _ordered_range_fact(
            _parse_english_number(match.group("start")) / 100,
            _parse_english_number(match.group("end")) / 100,
        ),
    )
    add_matches(_ENGLISH_HALF_PERCENT, lambda match: 0.005)
    add_matches(
        _ENGLISH_WRITTEN_PERCENT,
        lambda match: _parse_english_number(match.group("number")) / 100,
    )
    add_matches(
        _ENGLISH_SIMPLE_PERCENT,
        lambda match: _parse_english_number(match.group("number")) / 100,
    )
    add_matches(
        _ENGLISH_MIXED_HALF_MAGNITUDE,
        lambda match: (
            _parse_english_number(match.group("whole")) + 0.5
        )
        * {
            "hundred": 100,
            "thousand": 1_000,
            "million": 1_000_000,
            "billion": 1_000_000_000,
            "trillion": 1_000_000_000_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ENGLISH_DURATION_HALF_MAGNITUDE,
        lambda match: 0.5
        * {
            "decade": 10,
            "century": 100,
            "millennium": 1_000,
        }[match.group("scale").casefold()],
    )
    add_matches(_ENGLISH_BY_HALF_AGAIN, lambda match: 0.5)
    add_matches(_ENGLISH_HALF_AGAIN, lambda match: 1.5)
    add_matches(_ENGLISH_HALF_AS_MUCH, lambda match: 0.5)
    add_matches(_ENGLISH_BISECTION, lambda match: 2)
    add_matches(_ENGLISH_PARTIAL_STATE_HALF, lambda match: 0.5)
    add_matches(_ENGLISH_HALF_COMPOUND_TENTH, lambda match: 0.05)
    add_matches(
        _ENGLISH_REPEATED_APPROXIMATE_MAGNITUDE,
        lambda match: {
            "tens": 10,
            "hundreds": 100,
            "thousands": 1_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ENGLISH_APPROXIMATE_MAGNITUDE,
        lambda match: {
            "tens": 10,
            "hundreds": 100,
            "thousands": 1_000,
        }[match.group("count").casefold()]
        * {
            "hundreds": 100,
            "thousands": 1_000,
            "millions": 1_000_000,
            "billions": 1_000_000_000,
            "trillions": 1_000_000_000_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ENGLISH_BARE_APPROXIMATE_MAGNITUDE,
        lambda match: {
            "hundreds": 100,
            "thousands": 1_000,
            "millions": 1_000_000,
            "billions": 1_000_000_000,
            "trillions": 1_000_000_000_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ENGLISH_QUANTIFIED_DURATION,
        lambda match: (
            _parse_arabic_number(match.group("count"))
            if match.group("count")[0].isdigit()
            else _parse_english_number(match.group("count"))
        )
        * (
            10
            if match.group("scale").casefold().startswith("decade")
            else 100
            if match.group("scale").casefold().startswith("centur")
            else 1_000
        ),
    )
    add_matches(
        _ENGLISH_STANDALONE_APPROXIMATE_MAGNITUDE,
        lambda match: {
            "hundreds": 100,
            "thousands": 1_000,
            "millions": 1_000_000,
            "billions": 1_000_000_000,
            "trillions": 1_000_000_000_000,
            "centuries": 100,
        }[match.group("scale").casefold()],
    )
    add_matches(_ENGLISH_SIMPLE_APPROXIMATE_MAGNITUDE, lambda match: 100)
    add_matches(
        _ENGLISH_SCORE_COUNT,
        lambda match: _parse_english_number(match.group("count")) * 20,
    )
    add_matches(
        _ENGLISH_FRACTION_OF_MAGNITUDE,
        _parse_english_fraction_of_magnitude_match,
    )
    add_matches(
        _ENGLISH_HALF_MAGNITUDE,
        lambda match: 0.5
        * {
            "thousand": 1_000,
            "million": 1_000_000,
            "billion": 1_000_000_000,
            "trillion": 1_000_000_000_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ARABIC_ENGLISH_MAGNITUDE,
        lambda match: _parse_arabic_number(match.group("number"))
        * {
            "thousand": 1_000,
            "million": 1_000_000,
            "billion": 1_000_000_000,
            "trillion": 1_000_000_000_000,
        }[match.group("scale").casefold()],
    )
    add_matches(
        _ENGLISH_COMPOUND_MAGNITUDE,
        lambda match: (
            _parse_english_number(match.group("count"))
            if match.group("count")
            else 1
        )
        * {
            "thousand": 1_000,
            "million": 1_000_000,
            "billion": 1_000_000_000,
            "trillion": 1_000_000_000_000,
        }[match.group("left").casefold()]
        * {
            "thousand": 1_000,
            "million": 1_000_000,
            "billion": 1_000_000_000,
            "trillion": 1_000_000_000_000,
        }[match.group("right").casefold()],
    )
    add_matches(
        _ENGLISH_WRITTEN_MAGNITUDE,
        lambda match: _parse_english_number(match.group("number")),
    )
    add_matches(_ENGLISH_CLOCK_TIME, _parse_english_clock_match)
    add_matches(_ENGLISH_WORD_DECIMAL, _parse_english_word_decimal_match)
    add_matches(_ENGLISH_FRACTION, _parse_english_fraction_match)
    add_matches(
        _CHINESE_FRACTION_OF_MAGNITUDE_PREFIX,
        _parse_chinese_fraction_of_magnitude_match,
    )
    add_matches(
        _CHINESE_FRACTION_OF_MAGNITUDE_SUFFIX,
        _parse_chinese_fraction_of_magnitude_match,
    )
    add_matches(
        _CHINESE_PERCENT_RANGE,
        lambda match: _ordered_range_fact(
            _parse_chinese_numeric_value(match.group("start")) / 100,
            _parse_chinese_numeric_value(match.group("end")) / 100,
        ),
    )
    add_matches(
        _CHINESE_FRACTION,
        _parse_chinese_fraction_match,
    )
    add_matches(_CHINESE_CONTEXTUAL_ZERO, lambda match: 0)
    add_matches(_CHINESE_CLOCK_TIME, _parse_chinese_clock_match)
    add_matches(_CHINESE_DECIMAL, _parse_chinese_decimal_match)
    add_matches(_CHINESE_BISECTION, lambda match: 2)
    add_matches(_CHINESE_TWO_HALVES, lambda match: 1)
    add_matches(_CHINESE_STANDALONE_HALF, lambda match: 0.5)
    add_matches(_CHINESE_EXTRA_PREFIX_HALF, lambda match: 0.5)
    add_matches(_CHINESE_HALF, lambda match: 0.5)
    add_matches(_CHINESE_SUFFIX_HALF, lambda match: 0.5)
    add_matches(
        _ENGLISH_MONTH,
        lambda match: _MONTH_NUMBERS[match.group(0).casefold()],
    )
    add_matches(_ARABIC_NUMBER, _parse_arabic_match)
    add_matches(
        _CHINESE_APPROXIMATE_MAGNITUDE,
        lambda match: _parse_chinese_number(match.group("number")),
    )
    add_matches(
        _CHINESE_SIMPLE_APPROXIMATE_MAGNITUDE,
        lambda match: {
            "百": 100,
            "千": 1_000,
            "万": 10_000,
            "亿": 100_000_000,
        }[match.group("scale")],
    )
    add_matches(
        _CHINESE_MAGNITUDE,
        _parse_chinese_magnitude_match,
    )

    result = Counter(facts)
    # Repeated fractional wording is routinely collapsed by distributive Chinese
    # constructions (for example, "half an hour each way" -> one written half).
    # Preserve the objective value without treating lexical repetition as a hard fact.
    if result["0.5"] > 1:
        result["0.5"] = 1
    return result


def _canonical_identifier_fact(value: str) -> str:
    """Keep the numeric payload, leaving identifier spelling to semantic QA."""
    return re.search(r"\d+", value).group(0)


def numeric_content_matches(source_text: str, target_text: str) -> bool:
    """Require every source fact without hard-failing implied target additions."""
    source = number_tokens(source_text)
    target = number_tokens(target_text)
    # Literary English commonly expresses a total ratio ("half again" = 1.5x),
    # while idiomatic Chinese expresses the same relation as a relative increase
    # ("增加百分之五十" or "大出一半" = +0.5). Keep the objective relation
    # equivalent without globally treating unrelated 1.5 and 0.5 quantities alike.
    if (
        source["1.5"]
        and target["0.5"]
        and _CHINESE_RELATIVE_HALF_INCREASE.search(
            unicodedata.normalize("NFKC", target_text)
        )
    ):
        target["1.5"] += target["0.5"]
    # Explicit percentage predicates can be translated through their logical
    # complement: "0% useful" is "100% useless", and vice versa. Keep the
    # alias narrow so unrelated zero/one facts remain hard failures.
    normalized_target = unicodedata.normalize("NFKC", target_text)
    if (
        source["0"]
        and target["1"]
        and _ENGLISH_ZERO_POSITIVE_PERCENT.search(source_text)
        and _CHINESE_FULL_NEGATIVE_PERCENT.search(normalized_target)
    ):
        target["0"] += target["1"]
    if (
        source["1"]
        and target["0"]
        and _ENGLISH_FULL_NEGATIVE_PERCENT.search(source_text)
        and _CHINESE_ZERO_POSITIVE_PERCENT.search(normalized_target)
    ):
        target["1"] += target["0"]
    # Target languages routinely make implicit singulars, units, dates, or
    # classifiers explicit. Semantic audit handles suspicious additions; this hard
    # gate is deliberately limited to source facts that disappeared or changed.
    if source:
        return not (source - (target + _number_word_tokens(target_text)))
    source_words = _number_word_tokens(source_text)
    if not source_words:
        return True
    target_facts = target + _number_word_tokens(target_text)
    # A lexical count with no mechanically recognizable target counterpart is too
    # ambiguous to block cross-language prose. It remains eligible for semantic audit.
    if not target_facts:
        return True
    # Multiple lexical quantities in prose are difficult to align mechanically
    # (ellipsis and repeated classifiers are routine). Reserve hard failure for the
    # credible single-fact case; semantic audit still sees the full segment.
    if sum(source_words.values()) != 1:
        return True
    return not (source_words - target_facts)


def repair_preserves_numbers(
    source_text: str, accepted_text: str, candidate_text: str
) -> bool:
    """Prevent a repair from changing number facts already aligned with the source."""
    source = _credible_number_facts(source_text)
    accepted_objective = number_tokens(accepted_text)
    candidate_objective = number_tokens(candidate_text)
    if numeric_content_matches(source_text, accepted_text):
        # Ordinary target-language classifiers are lower-confidence lexical hints,
        # not immutable quantities. Treat only objective facts as additions here;
        # otherwise a localized prose repair can be rejected merely for changing
        # an indefinite article into a natural Chinese classifier.
        return numeric_content_matches(source_text, candidate_text) and not (
            candidate_objective - (accepted_objective + source)
        )
    # A pre-existing mismatch must not prevent an unrelated localized repair. It may
    # improve to the source facts, but it may never introduce a third set of facts.
    return candidate_objective == accepted_objective or (
        numeric_content_matches(source_text, candidate_text)
        and not (candidate_objective - source)
    )


def _credible_number_facts(text: str) -> Counter[str]:
    """Use the same confidence ordering as ``numeric_content_matches``."""
    objective = number_tokens(text)
    return objective if objective else _number_word_tokens(text)


def _number_word_tokens(text: str) -> Counter[str]:
    """Return lower-confidence lexical counts used only when both sides expose them."""
    visible = unicodedata.normalize("NFKC", _INLINE_MARKER.sub(" ", text)).strip()
    masked = list(visible)
    for match in _ENGLISH_ARTICLE_ORDINAL_NOUN.finditer(visible):
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    visible = "".join(masked)
    if "\u5012\u8ba1\u65f6" in visible or "\u5012\u6570" in visible:
        countdown_facts = [
            _canonical_numeric_fact(_parse_chinese_number(match.group(0)))
            for match in _CHINESE_COUNTDOWN_NUMBER.finditer(visible)
        ]
        if countdown_facts:
            return Counter(countdown_facts)
    facts: list[str] = []
    for match in _ENGLISH_NUMBER_WORD.finditer(visible):
        prefix = visible[max(0, match.start() - 12):match.start()].casefold()
        suffix = visible[match.end():match.end() + 12].casefold()
        if re.match(r"-[a-z]", suffix):
            continue
        if re.search(r"\b(?:several|many|few|some)\s+$", prefix):
            continue
        if match.group(0).casefold() == "one" and re.match(
            r"\s+(?:more|of|another|thing|way|time)\b", suffix
        ):
            continue
        facts.append(_canonical_numeric_fact(_parse_english_number(match.group(0))))
    facts.extend(
        _canonical_numeric_fact(_parse_chinese_number(match.group("number")))
        for match in _CHINESE_NUMBER.finditer(visible)
    )
    return Counter(facts)


def _countdown_number_tokens(text: str) -> Counter[str]:
    """Extract an explicitly labelled countdown as an ordered set of hard facts."""
    if not (re.search(r"\bcountdown\b", text, flags=re.IGNORECASE) or "\u5012\u8ba1\u65f6" in text or "\u5012\u6570" in text):
        return Counter()
    facts = [
        _canonical_numeric_fact(_parse_english_number(match.group(0)))
        for match in _ENGLISH_NUMBER_WORD.finditer(text)
    ]
    facts.extend(
        _canonical_numeric_fact(_parse_chinese_number(match.group(0)))
        for match in _CHINESE_COUNTDOWN_NUMBER.finditer(text)
    )
    facts.extend(
        _canonical_numeric_fact(_parse_arabic_match(match))
        for match in _ARABIC_NUMBER.finditer(text)
    )
    return Counter(facts)


def _canonical_numeric_fact(value: int | float | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float) and abs(value) >= 1_000:
        return str(round(value))
    return str(value)


def _ordered_range_fact(start: int | float, end: int | float) -> str:
    return f"range:{_canonical_numeric_fact(start)}:{_canonical_numeric_fact(end)}"


def _parse_arabic_match(match: re.Match[str]) -> int | float:
    value = match.group(0)
    if (
        value.startswith(("-", "+"))
        and match.start() > 0
        and _CJK.fullmatch(match.string[match.start() - 1])
    ):
        value = value[1:]
    return _parse_arabic_number(value)


def _parse_english_word_decimal_match(match: re.Match[str]) -> float:
    digits = [
        0 if word in {"zero", "oh"} else _SMALL_ENGLISH_NUMBERS[word]
        for word in re.split(r"[ -]+", match.group("digits").casefold())
    ]
    return float("0." + "".join(str(item) for item in digits))


def _parse_english_clock_match(match: re.Match[str]) -> str:
    hour = int(_parse_english_number(match.group("hour")))
    minute_words = re.sub(r"^oh[ -]+", "", match.group("minute"), flags=re.IGNORECASE)
    minute = int(_parse_english_number(minute_words))
    return f"time:{hour:02d}:{minute:02d}"


def _parse_chinese_clock_match(match: re.Match[str]) -> str:
    hour = int(_parse_chinese_number(match.group("hour")))
    minute = int(_parse_chinese_number(match.group("minute")))
    if match.group("period") in {"\u4e0b\u5348", "\u665a\u4e0a"} and hour < 12:
        hour += 12
    elif match.group("period") == "\u51cc\u6668" and hour == 12:
        hour = 0
    return f"time:{hour:02d}:{minute:02d}"


def _parse_chinese_magnitude_match(match: re.Match[str]) -> int | None:
    suffix = match.string[match.end() :]
    if (
        len(match.group(0)) == 1
        and suffix
        and _CJK.match(suffix)
        and not _CHINESE_MAGNITUDE_UNIT.match(suffix)
    ):
        return None
    return _parse_chinese_number(match.group(0))


def _parse_english_fraction_match(match: re.Match[str]) -> float | None:
    suffix = match.string[match.end() :]
    lexical_half = bool(match.group("standalone_half")) or (
        (match.group("numerator") or "").casefold() in {"a", "one"}
        and (match.group("denominator") or "").casefold() in {"half", "halves"}
    )
    if lexical_half:
        if re.match(
            r"-(?!(?:hour|day|week|month|year|meter|kilometer|mile|"
            r"thousand|million|billion|trillion)\b)[A-Za-z]",
            suffix,
            flags=re.IGNORECASE,
        ):
            return None
        if re.match(r"\s+[A-Za-z]", suffix) and not re.match(
            r"\s+(?:a|an|the|my|your|his|her|its|our|their|of|hours?|minutes?|seconds?|days?|weeks?|months?|years?|meters?|kilometers?|miles?|"
            r"foot|feet|inch|inches|laps?|circuits?|thousand|million|billion|trillion)\b",
            suffix,
            flags=re.IGNORECASE,
        ):
            return None
    numerator_word = (match.group("numerator") or "one").casefold()
    denominator_word = (match.group("denominator") or "").casefold()
    if (
        numerator_word == "a"
        and denominator_word == "third"
        and re.match(r"\s*(?:$|[.!?,;:])", suffix)
    ):
        return None
    numerator = 1 if numerator_word == "a" else _SMALL_ENGLISH_NUMBERS[numerator_word]
    if match.group("standalone_half"):
        return numerator / 2
    denominator = {
        "half": 2,
        "halves": 2,
        "third": 3,
        "thirds": 3,
        "quarter": 4,
        "quarters": 4,
        "fifth": 5,
        "fifths": 5,
        "sixth": 6,
        "sixths": 6,
        "seventh": 7,
        "sevenths": 7,
        "eighth": 8,
        "eighths": 8,
        "ninth": 9,
        "ninths": 9,
        "tenth": 10,
        "tenths": 10,
    }[denominator_word]
    return numerator / denominator


def _parse_english_fraction_of_magnitude_match(match: re.Match[str]) -> float:
    numerator_word = match.group("numerator").casefold()
    numerator = 1 if numerator_word == "a" else _SMALL_ENGLISH_NUMBERS[numerator_word]
    denominator = {
        "half": 2,
        "halves": 2,
        "third": 3,
        "thirds": 3,
        "quarter": 4,
        "quarters": 4,
        "fifth": 5,
        "fifths": 5,
        "sixth": 6,
        "sixths": 6,
        "seventh": 7,
        "sevenths": 7,
        "eighth": 8,
        "eighths": 8,
        "ninth": 9,
        "ninths": 9,
        "tenth": 10,
        "tenths": 10,
    }[match.group("denominator").casefold()]
    fraction = numerator / denominator
    return fraction * {
        "thousand": 1_000,
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "trillion": 1_000_000_000_000,
    }[match.group("scale").casefold()]


def _parse_chinese_fraction_match(match: re.Match[str]) -> float | None:
    denominator = _parse_chinese_number(match.group("denominator"))
    if denominator == 0:
        return None
    return _parse_chinese_numeric_value(match.group("numerator")) / denominator


def _parse_chinese_fraction_of_magnitude_match(
    match: re.Match[str],
) -> float | None:
    fraction = _parse_chinese_fraction_match(match)
    if fraction is None:
        return None
    return fraction * _parse_chinese_number(match.group("magnitude"))


def _parse_chinese_decimal_match(match: re.Match[str]) -> float:
    whole = _parse_chinese_number(match.group("whole"))
    digits = "".join(str(_CHINESE_DIGITS[item]) for item in match.group("digits"))
    return float(f"{whole}.{digits}")


def _parse_chinese_numeric_value(value: str) -> int | float:
    if "点" not in value:
        return _parse_chinese_number(value)
    whole, digits_text = value.split("点", 1)
    digits = "".join(str(_CHINESE_DIGITS[item]) for item in digits_text)
    return float(f"{_parse_chinese_number(whole)}.{digits}")


def repair_preserves_glossary(
    source_text: str,
    accepted_text: str,
    candidate_text: str,
    direction: TranslationDirection,
    glossary: Iterable[GlossaryEntry] = (),
) -> bool:
    """Keep applicable approved terms already present in last-known-good text."""
    from .preprocessing import select_relevant_glossary_entries

    source_visible = visible_segment_text(source_text)
    accepted_visible = visible_segment_text(accepted_text)
    candidate_visible = visible_segment_text(candidate_text)
    applicable = select_relevant_glossary_entries(
        source_visible,
        list(glossary),
        direction,
    )
    for entry in applicable:
        source_term = (
            entry.english
            if direction is TranslationDirection.EN_TO_ZH
            else entry.chinese
        )
        target_term = (
            entry.chinese
            if direction is TranslationDirection.EN_TO_ZH
            else entry.english
        )
        if (
            glossary_term_count(source_visible, source_term)
            and glossary_term_count(accepted_visible, target_term)
            and glossary_term_count(candidate_visible, target_term)
            != glossary_term_count(accepted_visible, target_term)
        ):
            return False
    return True


def glossary_term_count(text: str, term: str) -> int:
    """Count exact glossary terms without matching inside larger Latin words."""
    if not term:
        return 0
    visible = visible_segment_text(text)
    normalized_term = unicodedata.normalize("NFKC", term)
    escaped = re.escape(normalized_term)
    if _LATIN.search(normalized_term):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
            flags=re.IGNORECASE,
        )
        return len(pattern.findall(visible))
    return visible.count(normalized_term)


def glossary_target_matches(text: str, term: str) -> bool:
    """Accept an exact target term or a conservative Chinese demonym stem."""
    if glossary_term_count(text, term):
        return True
    normalized = unicodedata.normalize("NFKC", term)
    suffix = next(
        (item for item in ("\u5c45\u6c11", "\u4eba") if normalized.endswith(item)),
        "",
    )
    if not suffix:
        return False
    stem = normalized[: -len(suffix)]
    return len(stem) >= 3 and bool(_CJK.search(stem)) and stem in visible_segment_text(text)


def _parse_arabic_number(value: str) -> int | float:
    multiplier = 1
    if value.endswith("万"):
        multiplier, value = 10_000, value[:-1]
    elif value.endswith("亿"):
        multiplier, value = 100_000_000, value[:-1]
    normalized = value.replace(",", "")
    parsed = float(normalized) if "." in normalized else int(normalized)
    result = parsed * multiplier
    return int(result) if isinstance(result, float) and result.is_integer() else result


def _parse_english_number(value: str) -> int:
    words = [item for item in re.split(r"[ -]+", value.casefold()) if item != "and"]
    total = current = 0
    for word in words:
        if word in _SMALL_ENGLISH_NUMBERS:
            current += _SMALL_ENGLISH_NUMBERS[word]
        elif word in _ENGLISH_ORDINALS:
            current += _ENGLISH_ORDINALS[word]
        elif word == "hundred":
            current = max(1, current) * 100
        elif word in {"thousand", "million", "billion", "trillion"}:
            scale = {
                "thousand": 1_000,
                "million": 1_000_000,
                "billion": 1_000_000_000,
                "trillion": 1_000_000_000_000,
            }[word]
            total += max(1, current) * scale
            current = 0
    return total + current


def _parse_chinese_number(value: str) -> int:
    if not any(character in "十百千万亿" for character in value):
        return int("".join(str(_CHINESE_DIGITS[character]) for character in value))
    total = section = digit = 0
    for character in value:
        if character in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[character]
        elif character in "十百千":
            unit = {"十": 10, "百": 100, "千": 1_000}[character]
            section += max(1, digit) * unit
            digit = 0
        elif character == "万":
            total += max(1, section + digit) * 10_000
            section = digit = 0
        elif character == "亿":
            total = max(1, total + section + digit) * 100_000_000
            section = digit = 0
    return total + section + digit


def _is_conservative_technical_identifier(value: str) -> bool:
    """Require stronger evidence than capitalization alone for alphabetic literals."""
    if _is_bibliographic_identifier(value):
        return True
    if not is_preservable_technical_identifier(value):
        return False
    if any(character.isdigit() for character in value):
        return True
    if _TECHNICAL_PUNCTUATION.search(value):
        return True
    return value.isupper() and 2 <= len(value) <= 5


def _is_conservative_technical_identifier_sequence(value: str) -> bool:
    """Recognize metadata rows composed entirely of invariant identifiers.

    EPUB copyright pages sometimes place multiple ISBNs and format labels in one
    segment.  The complete row is not one identifier, but every whitespace token
    is.  Requiring both an all-identifier token set and strong technical
    punctuation avoids exempting ordinary capitalized prose with a number.
    """
    tokens = value.split()
    if len(tokens) < 2:
        return False
    if not all(_is_conservative_technical_identifier(token) for token in tokens):
        return False
    return any(
        any(character.isdigit() for character in token)
        and bool(_TECHNICAL_PUNCTUATION.search(token))
        for token in tokens
    )


def _is_bibliographic_identifier(value: str) -> bool:
    """Recognize exact publication identifiers that must remain untranslated."""
    stripped = value.strip()
    return bool(
        _ISBN_IDENTIFIER.fullmatch(stripped)
        or _LIBRARY_CALL_IDENTIFIER.fullmatch(stripped)
        or _PUBLICATION_REVISION_IDENTIFIER.fullmatch(stripped)
    )


def _looks_like_translated_heading(
    source: str, target: str, direction: TranslationDirection
) -> bool:
    if len(source) > 40 or re.search(r"[.!?。！？]$", source):
        return False
    source_words = re.findall(r"[A-Za-z]+|[\u3400-\u9fff]+", source)
    if not 1 <= len(source_words) <= 4:
        return False
    return (
        bool(_CJK.search(target))
        if direction is TranslationDirection.EN_TO_ZH
        else bool(_LATIN.search(target))
    )


def _is_glossary_approved_literal(
    source: str,
    target: str,
    direction: TranslationDirection,
    glossary: Iterable[GlossaryEntry],
) -> bool:
    source_core = _strip_outer_punctuation(source)
    target_core = _strip_outer_punctuation(target)
    for entry in glossary:
        approved_source = entry.english if direction is TranslationDirection.EN_TO_ZH else entry.chinese
        approved_target = entry.chinese if direction is TranslationDirection.EN_TO_ZH else entry.english
        if (
            source_core.casefold() == approved_source.casefold()
            and target_core == approved_target
            and approved_source == approved_target
        ):
            return True
    return False


def _strip_outer_punctuation(text: str) -> str:
    return re.sub(r"^\W+|\W+$", "", text.strip(), flags=re.UNICODE)
