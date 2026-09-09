"""Typed, confidence-aware quantity extraction and cross-language comparison.

This module deliberately separates mechanically provable quantity facts from
context-dependent readings.  Exact literals, percentages, ranges, identifiers,
and recognized units can be enforced deterministically.  Literary ratios,
approximation, attachment, and other ambiguous readings are routed to semantic
adjudication instead of accumulating phrase-specific equivalence rules.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuantityKind(str, Enum):
    COUNT = "count"
    IDENTIFIER = "identifier"
    FRACTION = "fraction"
    RATIO = "ratio"
    PERCENTAGE = "percentage"
    RANGE = "range"
    DURATION = "duration"
    TIME = "time"
    DATE = "date"
    AGE = "age"
    DISTANCE = "distance"
    DIMENSION = "dimension"
    SPEED = "speed"
    TEMPERATURE = "temperature"
    MASS = "mass"
    CURRENCY = "currency"
    OTHER = "other"


class QuantityMismatchKind(str, Enum):
    MISSING = "missing"
    ADDED = "added"
    VALUE_CHANGE = "value_change"
    UNIT_CHANGE = "unit_change"
    RANGE_CHANGE = "range_change"
    COMPARATOR_CHANGE = "comparator_change"
    APPROXIMATION_CHANGE = "approximation_change"
    POLARITY_CHANGE = "polarity_change"
    ATTACHMENT_CHANGE = "attachment_change"
    RELATION_CHANGE = "relation_change"


class QuantityFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: QuantityKind
    value: Decimal | None = None
    lower: Decimal | None = None
    upper: Decimal | None = None
    unit: str = ""
    comparator: Literal["equal", "less", "less_equal", "greater", "greater_equal"] = (
        "equal"
    )
    approximate: bool = False
    polarity: Literal["positive", "negative"] = "positive"
    entity: str = ""
    relation: str = ""
    quote: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class QuantityMismatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: QuantityMismatchKind
    source_fact: QuantityFact | None = None
    target_fact: QuantityFact | None = None
    message: str = Field(min_length=3)


class QuantityComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = ""
    status: Literal["match", "mismatch", "uncertain"]
    source_facts: list[QuantityFact] = Field(default_factory=list)
    target_facts: list[QuantityFact] = Field(default_factory=list)
    mismatches: list[QuantityMismatch] = Field(default_factory=list)
    reason: str = ""
    adjudicated: bool = False


class QuantityAuditDecision(BaseModel):
    """A structured semantic judgment for one uncertain quantity comparison."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment_id: str = Field(min_length=1)
    status: Literal["match", "mismatch", "uncertain"]
    mismatch_types: list[QuantityMismatchKind] = Field(default_factory=list)
    message: str = Field(min_length=3, max_length=500)
    source_quote: str = ""
    translation_quote: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class QuantityAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[QuantityAuditDecision] = Field(default_factory=list)


_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_ARABIC = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_ARABIC_NUMBER = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<number>{_ARABIC})(?![A-Za-z0-9_])"
)
_ARABIC_PERCENT = re.compile(
    rf"(?P<number>{_ARABIC})\s*(?:%|percent(?:age)?(?:\s+points?)?)",
    flags=re.IGNORECASE,
)
_CHINESE_PERCENT = re.compile(
    rf"百分之\s*(?P<number>{_ARABIC}|[零〇一二两三四五六七八九十百千万亿点半]+)"
)
_CHINESE_FULL_PERCENT = re.compile(r"百分百")
_FRACTION = re.compile(r"(?<!\w)(?P<num>\d+)\s*/\s*(?P<den>\d+)(?!\w)")
_EN_VAGUE_SCALE = re.compile(
    r"\b(?P<scale>hundreds|thousands|millions|billions)\b",
    flags=re.IGNORECASE,
)
_ZH_VAGUE_SCALE = re.compile(r"数(?P<scale>百万|百|千|万|亿)")
_VAGUE_SCALE_VALUES = {
    "hundreds": Decimal("100"),
    "thousands": Decimal("1000"),
    "millions": Decimal("1000000"),
    "billions": Decimal("1000000000"),
    "百": Decimal("100"),
    "千": Decimal("1000"),
    "万": Decimal("10000"),
    "百万": Decimal("1000000"),
    "亿": Decimal("100000000"),
}
_RANGE = re.compile(
    rf"(?P<start>{_ARABIC})\s*(?:-|–|—|to|至|到)\s*(?P<end>{_ARABIC})",
    flags=re.IGNORECASE,
)
_IDENTIFIER = re.compile(
    r"(?<!\w)(?=[A-Za-z0-9._-]*[A-Za-z])(?=[A-Za-z0-9._-]*\d)"
    r"[A-Za-z][A-Za-z0-9._-]*(?!\w)"
)
_APPROXIMATE = re.compile(
    r"\b(?:about|around|approximately|roughly|nearly|almost|circa|some)\b|"
    r"大约|约莫|约有|将近|近乎|差不多|左右|上下|余|多(?:个|名|只|年|月|天|小时|分钟|秒)",
    flags=re.IGNORECASE,
)
_RELATIONAL_QUANTITY = re.compile(
    r"\b(?:half\s+again|half\s+as\s+(?:much|many)|twice|double|triple|"
    r"times?\s+as|more\s+than|less\s+than|fewer\s+than|at\s+least|at\s+most|"
    r"no\s+more\s+than|no\s+less\s+than|"
    r"(?:longer|shorter|older|younger|larger|smaller|higher|lower|faster|slower)\s+than)\b|"
    r"一倍|两倍|三倍|翻倍|一半|多于|少于|不少于|不多于|至少|至多|以上|以下",
    flags=re.IGNORECASE,
)
_STRONG_RELATIONAL_QUANTITY = re.compile(
    r"\b(?:half\s+again|half\s+as\s+(?:much|many)|twice|double|triple|"
    r"times?\s+as)\b|一倍|两倍|三倍|翻倍|一半",
    flags=re.IGNORECASE,
)
_NEGATIVE_QUANTITY = re.compile(
    r"(?:\b(?:not|never|without)\b|没有|并非|不是|未曾?|不曾)\s*$",
    flags=re.IGNORECASE,
)
_COMPARATOR_PATTERNS = (
    (re.compile(r"\b(?:at least|no less than)\b|不少于|至少|以上", re.I), "greater_equal"),
    (re.compile(r"\b(?:more than|greater than|over)\b|多于|超过", re.I), "greater"),
    (re.compile(r"\b(?:at most|no more than)\b|不多于|至多|以下", re.I), "less_equal"),
    (re.compile(r"\b(?:less than|fewer than|under)\b|少于|低于", re.I), "less"),
)

_UNIT_ALIASES: dict[str, tuple[str, QuantityKind, Decimal]] = {
    "second": ("second", QuantityKind.DURATION, Decimal("1")),
    "seconds": ("second", QuantityKind.DURATION, Decimal("1")),
    "秒": ("second", QuantityKind.DURATION, Decimal("1")),
    "minute": ("second", QuantityKind.DURATION, Decimal("60")),
    "minutes": ("second", QuantityKind.DURATION, Decimal("60")),
    "分钟": ("second", QuantityKind.DURATION, Decimal("60")),
    "hour": ("second", QuantityKind.DURATION, Decimal("3600")),
    "hours": ("second", QuantityKind.DURATION, Decimal("3600")),
    "小时": ("second", QuantityKind.DURATION, Decimal("3600")),
    "个小时": ("second", QuantityKind.DURATION, Decimal("3600")),
    "day": ("second", QuantityKind.DURATION, Decimal("86400")),
    "days": ("second", QuantityKind.DURATION, Decimal("86400")),
    "天": ("second", QuantityKind.DURATION, Decimal("86400")),
    "日": ("second", QuantityKind.DURATION, Decimal("86400")),
    "night": ("second", QuantityKind.DURATION, Decimal("86400")),
    "nights": ("second", QuantityKind.DURATION, Decimal("86400")),
    "夜": ("second", QuantityKind.DURATION, Decimal("86400")),
    "tenday": ("second", QuantityKind.DURATION, Decimal("864000")),
    "tendays": ("second", QuantityKind.DURATION, Decimal("864000")),
    "week": ("second", QuantityKind.DURATION, Decimal("604800")),
    "weeks": ("second", QuantityKind.DURATION, Decimal("604800")),
    "周": ("second", QuantityKind.DURATION, Decimal("604800")),
    "month": ("month", QuantityKind.DURATION, Decimal("1")),
    "months": ("month", QuantityKind.DURATION, Decimal("1")),
    "个月": ("month", QuantityKind.DURATION, Decimal("1")),
    "year": ("year", QuantityKind.DURATION, Decimal("1")),
    "years": ("year", QuantityKind.DURATION, Decimal("1")),
    "年": ("year", QuantityKind.DURATION, Decimal("1")),
    "岁": ("year", QuantityKind.AGE, Decimal("1")),
    "century": ("year", QuantityKind.DURATION, Decimal("100")),
    "centuries": ("year", QuantityKind.DURATION, Decimal("100")),
    "世纪": ("year", QuantityKind.DURATION, Decimal("100")),
    "millimeter": ("meter", QuantityKind.DISTANCE, Decimal("0.001")),
    "millimeters": ("meter", QuantityKind.DISTANCE, Decimal("0.001")),
    "mm": ("meter", QuantityKind.DISTANCE, Decimal("0.001")),
    "毫米": ("meter", QuantityKind.DISTANCE, Decimal("0.001")),
    "centimeter": ("meter", QuantityKind.DISTANCE, Decimal("0.01")),
    "centimeters": ("meter", QuantityKind.DISTANCE, Decimal("0.01")),
    "cm": ("meter", QuantityKind.DISTANCE, Decimal("0.01")),
    "厘米": ("meter", QuantityKind.DISTANCE, Decimal("0.01")),
    "meter": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "meters": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "metre": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "metres": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "m": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "米": ("meter", QuantityKind.DISTANCE, Decimal("1")),
    "kilometer": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "kilometers": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "kilometre": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "kilometres": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "km": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "公里": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "千米": ("meter", QuantityKind.DISTANCE, Decimal("1000")),
    "inch": ("meter", QuantityKind.DISTANCE, Decimal("0.0254")),
    "inches": ("meter", QuantityKind.DISTANCE, Decimal("0.0254")),
    "英寸": ("meter", QuantityKind.DISTANCE, Decimal("0.0254")),
    "foot": ("meter", QuantityKind.DISTANCE, Decimal("0.3048")),
    "feet": ("meter", QuantityKind.DISTANCE, Decimal("0.3048")),
    "英尺": ("meter", QuantityKind.DISTANCE, Decimal("0.3048")),
    "yard": ("meter", QuantityKind.DISTANCE, Decimal("0.9144")),
    "yards": ("meter", QuantityKind.DISTANCE, Decimal("0.9144")),
    "码": ("meter", QuantityKind.DISTANCE, Decimal("0.9144")),
    "mile": ("meter", QuantityKind.DISTANCE, Decimal("1609.344")),
    "miles": ("meter", QuantityKind.DISTANCE, Decimal("1609.344")),
    "英里": ("meter", QuantityKind.DISTANCE, Decimal("1609.344")),
    "gram": ("gram", QuantityKind.MASS, Decimal("1")),
    "grams": ("gram", QuantityKind.MASS, Decimal("1")),
    "克": ("gram", QuantityKind.MASS, Decimal("1")),
    "kilogram": ("gram", QuantityKind.MASS, Decimal("1000")),
    "kilograms": ("gram", QuantityKind.MASS, Decimal("1000")),
    "kg": ("gram", QuantityKind.MASS, Decimal("1000")),
    "千克": ("gram", QuantityKind.MASS, Decimal("1000")),
    "公斤": ("gram", QuantityKind.MASS, Decimal("1000")),
    "pound": ("gram", QuantityKind.MASS, Decimal("453.59237")),
    "pounds": ("gram", QuantityKind.MASS, Decimal("453.59237")),
    "磅": ("gram", QuantityKind.MASS, Decimal("453.59237")),
    "degree": ("degree", QuantityKind.TEMPERATURE, Decimal("1")),
    "degrees": ("degree", QuantityKind.TEMPERATURE, Decimal("1")),
    "度": ("degree", QuantityKind.TEMPERATURE, Decimal("1")),
}
_UNIT_PATTERN = re.compile(
    r"[\s-]*(?P<unit>" + "|".join(
        (
            re.escape(item) + (r"\b" if item.isascii() else "")
            for item in sorted(_UNIT_ALIASES, key=len, reverse=True)
        )
    ) + r")",
    flags=re.IGNORECASE,
)
_ZH_DOZEN_WITH_UNIT = re.compile(
    r"(?P<number>[0-9零〇一二两三四五六七八九十百千万亿点]+)打"
    r"(?P<unit>" + "|".join(
        re.escape(item)
        for item in sorted(
            (item for item in _UNIT_ALIASES if not item.isascii()),
            key=len,
            reverse=True,
        )
    ) + r")"
)
_EN_IMPLICIT_ONE_UNIT = re.compile(
    r"\b(?:a|an|another|last|previous|next|single)\s+(?P<unit>"
    + "|".join(
        re.escape(item)
        for item in sorted(
            (item for item in _UNIT_ALIASES if item.isascii()),
            key=len,
            reverse=True,
        )
    )
    + r")\b",
    flags=re.IGNORECASE,
)
_EN_DAY_AFTER = re.compile(r"\bthe\s+day\s+after\b(?!\s+tomorrow)", re.IGNORECASE)
_ZH_NEXT_DAY = re.compile(r"第(?:二|2)天")

_EN_SMALL = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_EN_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_EN_NUMBER_WORD = re.compile(
    r"\b(?:" + "|".join([*_EN_SMALL, *_EN_TENS, "hundred", *_EN_SCALES, "and", "half", "quarter", "dozen", "score"]) + r")(?:[ -]+(?:"
    + "|".join([*_EN_SMALL, *_EN_TENS, "hundred", *_EN_SCALES, "and", "half", "quarter", "dozen", "score"])
    + r"))*\b",
    flags=re.IGNORECASE,
)
_ZH_NUMBER_WITH_CONTEXT = re.compile(
    r"(?P<number>[负零〇一二两三四五六七八九十百千万亿点]+)"
    r"(?P<unit>秒|分钟|个小时|小时|天|日|周|个月|世纪|年|岁|毫米|厘米|米|千米|公里|英寸|英尺|码|英里|克|千克|公斤|磅|度|个|名|人|只|艘|架|本|次|倍|盏|台|辆|把|件|枚|颗|头|条|匹|扇|座|间|层|页|章|位|支|根|张|套|组|对|双|排|列|队|句|眼|下)"
)
_ZH_BARE_NUMBER = re.compile(r"[负零〇一二两三四五六七八九十百千万亿点]+")
_ZH_SINGULAR_CLASSIFIER = re.compile(
    r"一(?:个|名|人|只|艘|架|本|次|盏|台|辆|把|件|枚|颗|头|条|匹|扇|座|间|层|页|章|位|支|根|张|套|组|对|双|排|列|队|句|眼|下)"
)
_EN_PRONOMINAL_ONE_BEFORE = re.compile(
    r"\b(?:this|that|the|which|no|some|any|another|each|every)\s+$",
    flags=re.IGNORECASE,
)
_EN_PRONOMINAL_ONE_AFTER = re.compile(
    r"^\s*(?:another|of|among|amongst|and\b)",
    flags=re.IGNORECASE,
)


def contains_quantity_expression(text: str) -> bool:
    """Return whether text contains an explicit or relational quantity signal."""
    visible = _visible(text)
    return bool(extract_quantity_facts(visible) or _RELATIONAL_QUANTITY.search(visible))


def extract_quantity_facts(text: str) -> list[QuantityFact]:
    """Extract conservative typed facts without resolving literary relationships."""
    visible = _visible(text)
    occupied: list[tuple[int, int]] = []
    facts: list[QuantityFact] = []

    def available(start: int, end: int) -> bool:
        return not any(start < other_end and end > other_start for other_start, other_end in occupied)

    def add(fact: QuantityFact, start: int, end: int) -> None:
        if available(start, end):
            occupied.append((start, end))
            facts.append(fact)

    for match in _IDENTIFIER.finditer(visible):
        digits = re.findall(r"\d+", match.group(0))
        if digits:
            add(
                QuantityFact(
                    kind=QuantityKind.IDENTIFIER,
                    value=Decimal(digits[-1]),
                    relation=_identifier_stem(match.group(0)),
                    quote=match.group(0),
                    confidence=0.99,
                ),
                match.start(),
                match.end(),
            )

    for match in _RANGE.finditer(visible):
        start_value = _decimal(match.group("start"))
        end_value = _decimal(match.group("end"))
        if start_value is None or end_value is None:
            continue
        unit, kind, factor, end = _following_unit(visible, match.end())
        add(
            QuantityFact(
                kind=QuantityKind.RANGE if not unit else kind,
                lower=min(start_value, end_value) * factor,
                upper=max(start_value, end_value) * factor,
                unit=unit,
                approximate=_near_approximate(visible, match.start(), end),
                polarity=_polarity(visible, match.start()),
                quote=visible[match.start():end],
                confidence=0.98 if unit else 0.94,
            ),
            match.start(), end,
        )

    for match in _CHINESE_FULL_PERCENT.finditer(visible):
        add(
            QuantityFact(
                kind=QuantityKind.PERCENTAGE,
                value=Decimal("1"),
                unit="percent",
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), match.end()),
                polarity=_polarity(visible, match.start()),
                quote=match.group(0),
                confidence=0.99,
            ),
            match.start(), match.end(),
        )

    for pattern, chinese in ((_ARABIC_PERCENT, False), (_CHINESE_PERCENT, True)):
        for match in pattern.finditer(visible):
            value = (
                _parse_chinese_number(match.group("number"))
                if chinese and not match.group("number")[0].isdigit()
                else _decimal(match.group("number"))
            )
            if value is None:
                continue
            add(
                QuantityFact(
                    kind=QuantityKind.PERCENTAGE,
                    value=value / Decimal("100"),
                    unit="percent",
                    comparator=_comparator(visible, match.start()),
                    approximate=_near_approximate(visible, match.start(), match.end()),
                    polarity=_polarity(visible, match.start()),
                    quote=match.group(0),
                    confidence=0.99,
                ),
                match.start(), match.end(),
            )

    for match in _FRACTION.finditer(visible):
        denominator = Decimal(match.group("den"))
        if not denominator:
            continue
        add(
            QuantityFact(
                kind=QuantityKind.FRACTION,
                value=Decimal(match.group("num")) / denominator,
                polarity=_polarity(visible, match.start()),
                quote=match.group(0),
                confidence=0.98,
            ),
            match.start(), match.end(),
        )

    for pattern in (_EN_VAGUE_SCALE, _ZH_VAGUE_SCALE):
        for match in pattern.finditer(visible):
            if not available(match.start(), match.end()):
                continue
            add(
                QuantityFact(
                    kind=QuantityKind.COUNT,
                    value=_VAGUE_SCALE_VALUES[match.group("scale").casefold()],
                    approximate=True,
                    polarity=_polarity(visible, match.start()),
                    quote=match.group(0),
                    confidence=0.90,
                ),
                match.start(),
                match.end(),
            )

    for match in _EN_DAY_AFTER.finditer(visible):
        add(
            QuantityFact(
                kind=QuantityKind.DURATION,
                value=Decimal("86400"),
                unit="second",
                quote=match.group(0),
                confidence=0.94,
            ),
            match.start(),
            match.end(),
        )

    for match in _EN_IMPLICIT_ONE_UNIT.finditer(visible):
        if not available(match.start(), match.end()):
            continue
        if _is_nonliteral_implicit_unit(visible, match):
            continue
        raw_unit = match.group("unit")
        unit, kind, factor = _UNIT_ALIASES[raw_unit.casefold()]
        add(
            QuantityFact(
                kind=kind,
                value=factor,
                unit=unit,
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), match.end()),
                polarity=_polarity(visible, match.start()),
                quote=match.group(0),
                confidence=0.94,
            ),
            match.start(),
            match.end(),
        )

    # Chinese normally renders the relative English expression "the next day"
    # as 第二天.  Treat the whole ordinal phrase as a one-day relative interval;
    # otherwise the generic number+unit extractor sees the substring 二天 and
    # incorrectly reports a two-day duration.
    for match in _ZH_NEXT_DAY.finditer(visible):
        add(
            QuantityFact(
                kind=QuantityKind.DURATION,
                value=Decimal("86400"),
                unit="second",
                quote=match.group(0),
                confidence=0.94,
            ),
            match.start(),
            match.end(),
        )

    for match in _ZH_DOZEN_WITH_UNIT.finditer(visible):
        if not available(match.start(), match.end()):
            continue
        raw_number = match.group("number")
        value = (
            _decimal(raw_number)
            if raw_number[0].isdigit()
            else _parse_chinese_number(raw_number)
        )
        if value is None:
            continue
        unit, kind, factor = _UNIT_ALIASES[match.group("unit").casefold()]
        add(
            QuantityFact(
                kind=kind,
                value=value * Decimal("12") * factor,
                unit=unit,
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), match.end()),
                polarity=_polarity(visible, match.start()),
                quote=match.group(0),
                confidence=0.94,
            ),
            match.start(),
            match.end(),
        )

    for match in _ARABIC_NUMBER.finditer(visible):
        if not available(match.start(), match.end()):
            continue
        value = _decimal(match.group("number"))
        if value is None:
            continue
        unit, kind, factor, end = _following_unit(visible, match.end())
        add(
            QuantityFact(
                kind=kind if unit else QuantityKind.COUNT,
                value=value * factor,
                unit=unit,
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), end),
                polarity=_polarity(visible, match.start()),
                quote=visible[match.start():end],
                confidence=0.99 if unit else 0.95,
            ),
            match.start(), end,
        )

    for match in _EN_NUMBER_WORD.finditer(visible):
        if not available(match.start(), match.end()):
            continue
        raw_number = match.group(0)
        if re.search(r"(?:^|[\s-])and(?:$|[\s-])", raw_number, re.IGNORECASE) and (
            raw_number.casefold().startswith("and ")
            or raw_number.casefold().endswith(" and")
        ):
            continue
        if _is_pronominal_english_one(visible, match.start(), match.end(), raw_number):
            continue
        value = _parse_english_number(raw_number)
        if value is None:
            continue
        unit, kind, factor, end = _following_unit(visible, match.end())
        add(
            QuantityFact(
                kind=kind if unit else QuantityKind.COUNT,
                value=value * factor,
                unit=unit,
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), end),
                polarity=_polarity(visible, match.start()),
                quote=visible[match.start():end],
                confidence=0.94 if unit else 0.82,
            ),
            match.start(), end,
        )

    for match in _ZH_NUMBER_WITH_CONTEXT.finditer(visible):
        if not available(match.start(), match.end()):
            continue
        value = _parse_chinese_number(match.group("number"))
        if value is None:
            continue
        raw_unit = match.group("unit")
        canonical = _UNIT_ALIASES.get(raw_unit.casefold())
        if canonical:
            unit, kind, factor = canonical
            confidence = 0.94
        else:
            unit, kind, factor, confidence = "", QuantityKind.COUNT, Decimal("1"), 0.75
        add(
            QuantityFact(
                kind=QuantityKind.RATIO if raw_unit == "倍" else kind,
                value=value * factor,
                unit=unit,
                comparator=_comparator(visible, match.start()),
                approximate=_near_approximate(visible, match.start(), match.end()),
                polarity=_polarity(visible, match.start()),
                quote=match.group(0),
                confidence=confidence,
            ),
            match.start(), match.end(),
        )

    if not facts and (match := _ZH_BARE_NUMBER.fullmatch(visible)):
        value = _parse_chinese_number(match.group(0))
        if value is not None:
            add(
                QuantityFact(
                    kind=QuantityKind.COUNT,
                    value=value,
                    quote=match.group(0),
                    confidence=0.92,
                ),
                match.start(),
                match.end(),
            )

    return sorted(facts, key=lambda fact: visible.find(fact.quote))


def compare_quantity_texts(
    source_text: str,
    target_text: str,
    *,
    segment_id: str = "",
) -> QuantityComparison:
    """Align typed facts, hard-failing only clear high-confidence differences."""
    source = extract_quantity_facts(source_text)
    target = _discard_grammatical_target_classifiers(
        source,
        extract_quantity_facts(target_text),
    )
    source_relational = bool(_RELATIONAL_QUANTITY.search(_visible(source_text)))
    target_relational = bool(_RELATIONAL_QUANTITY.search(_visible(target_text)))
    strong_relational = bool(
        _STRONG_RELATIONAL_QUANTITY.search(_visible(source_text))
        or _STRONG_RELATIONAL_QUANTITY.search(_visible(target_text))
    )

    unmatched_target = set(range(len(target)))
    unmatched_source: list[int] = []
    for source_index, source_fact in enumerate(source):
        matched = next(
            (
                target_index
                for target_index in sorted(unmatched_target)
                if _facts_equivalent(source_fact, target[target_index])
            ),
            None,
        )
        if matched is None:
            unmatched_source.append(source_index)
        else:
            unmatched_target.remove(matched)

    if not unmatched_source and not unmatched_target:
        if (
            strong_relational
            or ((source_relational or target_relational) and (source or target))
            or any(item.polarity == "negative" for item in [*source, *target])
            or len(source) > 1
            or len(target) > 1
        ):
            return QuantityComparison(
                segment_id=segment_id,
                status="uncertain",
                source_facts=source,
                target_facts=target,
                reason=(
                    "multiple quantity facts require contextual attachment review"
                    if len(source) > 1 or len(target) > 1
                    else "relational quantity language requires contextual attachment review"
                ),
            )
        return QuantityComparison(
            segment_id=segment_id,
            status="match",
            source_facts=source,
            target_facts=target,
        )

    mismatches = _build_mismatches(source, target, unmatched_source, unmatched_target)
    high_confidence = all(
        fact.confidence >= 0.94
        for fact in [
            *(source[index] for index in unmatched_source),
            *(target[index] for index in unmatched_target),
        ]
    )
    complement_candidate = _possible_percentage_complement(
        source, target, unmatched_source, unmatched_target
    )
    explicit_relation = (
        source_relational
        or target_relational
        or complement_candidate
        or any(item.polarity == "negative" for item in [*source, *target])
    )
    status = "mismatch" if mismatches and high_confidence and not explicit_relation else "uncertain"
    return QuantityComparison(
        segment_id=segment_id,
        status=status,
        source_facts=source,
        target_facts=target,
        mismatches=mismatches,
        reason=(
            "high-confidence typed quantity facts do not align"
            if status == "mismatch"
            else "quantity facts require contextual semantic adjudication"
        ),
    )


def build_quantity_audit_prompt(
    segment_id: str,
    source_text: str,
    target_text: str,
    comparison: QuantityComparison,
    *,
    preceding_source: str = "(none)",
    following_source: str = "(none)",
) -> str:
    """Build an extraction-first prompt for one uncertain quantity segment."""
    source_hints = _format_facts(comparison.source_facts)
    target_hints = _format_facts(comparison.target_facts)
    return (
        "Audit only the quantity-bearing facts in the allowed translation segment. "
        "First independently identify what every quantity measures in SOURCE and in "
        "TRANSLATION, including value, unit, range, approximation, comparator, polarity, "
        "subject/object attachment, and whether a ratio means a total or a change. Then "
        "compare those facts. Do not reject harmless target-language classifiers or "
        "idiomatic reformulation. Mark mismatch only for a concrete changed, missing, "
        "added, reversed, or misattached fact. Use uncertain when context is genuinely "
        "insufficient. Quotes must be short verbatim excerpts from their respective text. "
        "Return exactly one decision for the allowed ID. The deterministic facts below "
        "are hints, not authoritative semantic conclusions.\n\n"
        f"Allowed ID: {segment_id}\n"
        f"PRECEDING SOURCE: {preceding_source}\n"
        f"SOURCE: {source_text}\n"
        f"FOLLOWING SOURCE: {following_source}\n"
        f"TRANSLATION: {target_text}\n"
        f"SOURCE FACT HINTS: {source_hints}\n"
        f"TRANSLATION FACT HINTS: {target_hints}"
    )


def validate_quantity_audit_scope(
    result: QuantityAuditResult,
    segment_id: str,
    source_text: str,
    target_text: str,
) -> QuantityAuditResult:
    """Require one grounded decision for the requested segment."""
    if len(result.decisions) != 1 or result.decisions[0].segment_id != segment_id:
        raise ValueError("quantity audit must return exactly the allowed segment ID")
    decision = result.decisions[0]
    if decision.source_quote and decision.source_quote not in source_text:
        raise ValueError("quantity audit source_quote is not grounded in source")
    if decision.translation_quote and decision.translation_quote not in target_text:
        raise ValueError("quantity audit translation_quote is not grounded in translation")
    if decision.status == "mismatch" and not decision.mismatch_types:
        raise ValueError("quantity mismatch requires at least one mismatch type")
    if decision.status != "mismatch" and decision.mismatch_types:
        raise ValueError("non-mismatch quantity decision cannot declare mismatch types")
    return result


def _visible(text: str) -> str:
    return unicodedata.normalize("NFKC", _INLINE_MARKER.sub(" ", text)).strip()


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _identifier_stem(value: str) -> str:
    return re.sub(r"\d+", "#", value).casefold()


def _following_unit(text: str, end: int) -> tuple[str, QuantityKind, Decimal, int]:
    match = _UNIT_PATTERN.match(text, end)
    if not match:
        return "", QuantityKind.COUNT, Decimal("1"), end
    unit, kind, factor = _UNIT_ALIASES[match.group("unit").casefold()]
    return unit, kind, factor, match.end()


def _near_approximate(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 24):start]
    after = text[end:min(len(text), end + 12)]
    prefix = re.compile(
        r"(?:\b(?:about|around|approximately|roughly|nearly|almost|circa|some)\b|"
        r"大约|约莫|约有|将近|近乎|差不多)\s*$",
        flags=re.IGNORECASE,
    )
    suffix = re.compile(
        r"^\s*(?:or\s+so\b|左右|上下|余|多(?:个|名|只|年|月|天|小时|分钟|秒))",
        flags=re.IGNORECASE,
    )
    return bool(prefix.search(before) or suffix.search(after))


def _comparator(text: str, start: int):
    context = text[max(0, start - 28):start]
    for pattern, value in _COMPARATOR_PATTERNS:
        matches = list(pattern.finditer(context))
        if matches and not context[matches[-1].end():].strip():
            return value
    return "equal"


def _polarity(text: str, start: int):
    context = text[max(0, start - 64):start]
    context = re.split(r"[.!?。！？;；]", context)[-1]
    if re.search(r"\bno\s+(?:more|less)\s+than\s*$", context, re.I):
        return "positive"
    return (
        "negative"
        if _NEGATIVE_QUANTITY.search(context)
        or re.search(r"\b(?:not|never|without)\b|没有|并非|不是|未曾?|不曾", context, re.I)
        else "positive"
    )


def _parse_english_number(value: str) -> Decimal | None:
    words = [item for item in re.split(r"[\s-]+", value.casefold()) if item != "and"]
    if not words:
        return None
    if words == ["half"]:
        return Decimal("0.5")
    if words == ["quarter"]:
        return Decimal("0.25")
    total = 0
    current = 0
    multiplier = 1
    for word in words:
        if word in _EN_SMALL:
            current += _EN_SMALL[word]
        elif word in _EN_TENS:
            current += _EN_TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word in _EN_SCALES:
            total += max(current, 1) * _EN_SCALES[word]
            current = 0
        elif word == "dozen":
            multiplier *= 12
        elif word == "score":
            multiplier *= 20
        else:
            return None
    base = total + current
    if multiplier != 1 and base == 0:
        base = 1
    return Decimal(base * multiplier)


_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
_ZH_LARGE_UNITS = {"万": 10_000, "亿": 100_000_000}


def _parse_chinese_number(value: str) -> Decimal | None:
    negative = value.startswith("负")
    if negative:
        value = value[1:]
    if "点" in value:
        whole, fraction = value.split("点", 1)
        if not fraction:
            return None
        whole_value = _parse_chinese_integer(whole) if whole else 0
        if any(item not in _ZH_DIGITS for item in fraction):
            return None
        decimal = Decimal("0." + "".join(str(_ZH_DIGITS[item]) for item in fraction))
        result = Decimal(whole_value) + decimal
    else:
        parsed = _parse_chinese_integer(value)
        if parsed is None:
            return None
        result = Decimal(parsed)
    return -result if negative else result


def _is_pronominal_english_one(
    text: str,
    start: int,
    end: int,
    raw_number: str,
) -> bool:
    """Exclude referential ``one`` uses that are not numeric assertions."""
    words = [item for item in re.split(r"[\s-]+", raw_number.casefold()) if item != "and"]
    if words != ["one"]:
        return False
    return bool(
        _EN_PRONOMINAL_ONE_BEFORE.search(text[max(0, start - 24):start])
        or _EN_PRONOMINAL_ONE_AFTER.search(text[end:end + 16])
    )


def _is_nonliteral_implicit_unit(text: str, match: re.Match[str]) -> bool:
    """Exclude unit-shaped words used as body parts or fixed proximity idioms."""
    phrase = match.group(0).casefold()
    unit = match.group("unit").casefold()
    before = text[max(0, match.start() - 48):match.start()]
    after = text[match.end():match.end() + 48]
    if unit == "foot":
        if re.search(
            r"\b(?:feel|feels|felt|look|looks|looked|seem|seems|seemed)\s+like\s*$",
            before,
            re.IGNORECASE,
        ):
            return True
        if re.match(r"\s+(?:that|which|with|without)\b", after, re.IGNORECASE):
            return True
        if re.match(r"\s+in\s+the\s+mire\b", after, re.IGNORECASE):
            return True
    return bool(
        phrase == "an inch"
        and re.search(r"\bwithin\s*$", before, re.IGNORECASE)
        and re.match(r"\s+of\s+[A-Za-z]+ing\b", after, re.IGNORECASE)
    )


def _discard_grammatical_target_classifiers(
    source: list[QuantityFact],
    target: list[QuantityFact],
) -> list[QuantityFact]:
    """Ignore surplus Chinese singular classifiers introduced by grammar.

    Chinese commonly requires ``一`` plus a classifier where English uses an
    indefinite article.  Such forms are not added numeric claims.  Preserve as
    many singular classifier facts as the source explicitly asserts so genuine
    ``one`` comparisons still align.
    """
    available_source_ones = sum(
        fact.kind is QuantityKind.COUNT
        and fact.value == Decimal("1")
        and not fact.unit
        for fact in source
    )
    kept: list[QuantityFact] = []
    for fact in target:
        if (
            fact.kind is QuantityKind.COUNT
            and fact.value == Decimal("1")
            and not fact.unit
            and _ZH_SINGULAR_CLASSIFIER.fullmatch(fact.quote)
        ):
            if available_source_ones:
                available_source_ones -= 1
                kept.append(fact)
            continue
        kept.append(fact)
    return kept


def _parse_chinese_integer(value: str) -> int | None:
    if not value:
        return 0
    if all(item in _ZH_DIGITS for item in value):
        return int("".join(str(_ZH_DIGITS[item]) for item in value))
    total = section = number = 0
    for character in value:
        if character in _ZH_DIGITS:
            number = _ZH_DIGITS[character]
        elif character in _ZH_SMALL_UNITS:
            section += max(number, 1) * _ZH_SMALL_UNITS[character]
            number = 0
        elif character in _ZH_LARGE_UNITS:
            section += number
            total += max(section, 1) * _ZH_LARGE_UNITS[character]
            section = number = 0
        else:
            return None
    return total + section + number


def _facts_equivalent(source: QuantityFact, target: QuantityFact) -> bool:
    if source.kind is QuantityKind.IDENTIFIER or target.kind is QuantityKind.IDENTIFIER:
        return (
            source.kind is target.kind
            and source.value == target.value
            and (not source.relation or not target.relation or source.relation == target.relation)
        )
    if source.lower is not None or target.lower is not None:
        return (
            source.lower == target.lower
            and source.upper == target.upper
            and source.unit == target.unit
        )
    if source.value != target.value:
        return False
    if (
        source.value is not None
        and source.value == source.value.to_integral_value()
        and Decimal("1000") <= source.value <= Decimal("2999")
        and {source.unit, target.unit} == {"", "year"}
    ):
        return True
    if source.unit and target.unit and source.unit != target.unit:
        return False
    if source.unit and not target.unit or target.unit and not source.unit:
        return False
    return (
        source.comparator == target.comparator
        and source.approximate == target.approximate
        and source.polarity == target.polarity
    )


def _build_mismatches(source, target, unmatched_source, unmatched_target):
    mismatches: list[QuantityMismatch] = []
    paired = min(len(unmatched_source), len(unmatched_target))
    for offset in range(paired):
        source_fact = source[unmatched_source[offset]]
        target_fact = target[sorted(unmatched_target)[offset]]
        kind = _difference_kind(source_fact, target_fact)
        mismatches.append(
            QuantityMismatch(
                kind=kind,
                source_fact=source_fact,
                target_fact=target_fact,
                message=f"{kind.value}: {source_fact.quote!r} versus {target_fact.quote!r}",
            )
        )
    for source_index in unmatched_source[paired:]:
        fact = source[source_index]
        mismatches.append(
            QuantityMismatch(
                kind=QuantityMismatchKind.MISSING,
                source_fact=fact,
                message=f"source quantity is missing from translation: {fact.quote!r}",
            )
        )
    remaining_target = sorted(unmatched_target)[paired:]
    for target_index in remaining_target:
        fact = target[target_index]
        mismatches.append(
            QuantityMismatch(
                kind=QuantityMismatchKind.ADDED,
                target_fact=fact,
                message=f"translation adds an explicit quantity: {fact.quote!r}",
            )
        )
    return mismatches


def _possible_percentage_complement(source, target, source_indexes, target_indexes):
    """Route generic 0–100% logical complements to semantic adjudication."""
    if len(source_indexes) != 1 or len(target_indexes) != 1:
        return False
    source_fact = source[source_indexes[0]]
    target_fact = target[sorted(target_indexes)[0]]
    return (
        source_fact.kind is QuantityKind.PERCENTAGE
        and target_fact.kind is QuantityKind.PERCENTAGE
        and source_fact.value is not None
        and target_fact.value is not None
        and source_fact.value + target_fact.value == Decimal("1")
    )


def _difference_kind(source: QuantityFact, target: QuantityFact) -> QuantityMismatchKind:
    if source.lower != target.lower or source.upper != target.upper:
        if source.lower is not None or target.lower is not None:
            return QuantityMismatchKind.RANGE_CHANGE
    if source.value != target.value:
        return QuantityMismatchKind.VALUE_CHANGE
    if source.unit != target.unit:
        return QuantityMismatchKind.UNIT_CHANGE
    if source.comparator != target.comparator:
        return QuantityMismatchKind.COMPARATOR_CHANGE
    if source.approximate != target.approximate:
        return QuantityMismatchKind.APPROXIMATION_CHANGE
    if source.polarity != target.polarity:
        return QuantityMismatchKind.POLARITY_CHANGE
    return QuantityMismatchKind.RELATION_CHANGE


def _format_facts(facts: list[QuantityFact]) -> str:
    if not facts:
        return "(none extracted)"
    return "; ".join(
        f"{fact.kind.value}: value={fact.value}, range={fact.lower}..{fact.upper}, "
        f"unit={fact.unit or '(none)'}, comparator={fact.comparator}, "
        f"approximate={fact.approximate}, quote={fact.quote!r}"
        for fact in facts
    )
