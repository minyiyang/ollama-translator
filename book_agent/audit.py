"""Deterministic and selective semantic auditing for translated drafts."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from .config import AuditConfig
from .content_policy import (
    SegmentKind,
    classify_segment,
    full_span_inline_wrapper_ids,
    should_run_language_check,
    should_run_length_check,
    should_run_semantic_audit,
    visible_segment_text,
    glossary_target_matches,
    number_tokens,
    numeric_content_matches,
)
from .glossary import estimate_tokens, is_suspicious_generic_candidate
from .languages import (
    TUNED_PROFILES,
    SCRIPTS,
    SENTENCE_END,
    TranslationDirection,
    copied_source_run,
    source_worded_passage,
    copy_is_untranslated,
    lacks_target_script,
    glossary_pair,
    glossary_sides,
    leftover_scripts,
    profile,
    scripts_disjoint,
)
from .schemas import GlossaryCategory
from .numeric_adjudication import (
    NUMBER_RULE_SOURCE,
    NumericRuling,
    apply_numeric_rulings,
)
from .ollama_client import estimate_request_tokens
from .preprocessing import PreprocessedDocument, select_relevant_glossary_entries
from .subtitles import ReadingLimits, wrap_cue
from .quantities import (
    QuantityComparison,
    QuantityMismatch,
    compare_quantity_texts,
    contains_quantity_expression,
)
from .translation import TranslatedDocument, misplaced_passages, shifted_passages, unusual_length_passages


class AuditCategory(str, Enum):
    STRUCTURE = "structure"
    EMPTY = "empty"
    UNTRANSLATED = "untranslated"
    OMISSION = "omission"
    ADDITION = "addition"
    DUPLICATION = "duplication"
    GLOSSARY = "glossary"
    MISTRANSLATION = "mistranslation"
    NATURALNESS = "naturalness"
    AI_STYLE = "ai_style"
    PUNCTUATION = "punctuation"
    # Book-level drift found by audit_consistency (book_agent/consistency.py), never by a model.
    CONSISTENCY = "consistency"
    # A subtitle that cannot be read in its time or does not fit the screen (book_agent/subtitles.py).
    READABILITY = "readability"


# Categories only the pipeline assigns; hidden from the semantic auditor's response schema.
PIPELINE_ONLY_CATEGORIES = frozenset({AuditCategory.CONSISTENCY.value, AuditCategory.READABILITY.value})


class AuditSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {
            AuditSeverity.LOW: 1,
            AuditSeverity.MEDIUM: 2,
            AuditSeverity.HIGH: 3,
        }[self]


class AuditRiskTag(str, Enum):
    QUANTITY = "quantity"
    SPATIAL = "spatial"
    POLARITY = "polarity"
    ATTACHMENT = "attachment"
    RELATION = "relation"


_SEMANTIC_RISK_PATTERNS: dict[AuditRiskTag, re.Pattern[str]] = {
    AuditRiskTag.QUANTITY: re.compile(
        r"\b(?:number|quantity|count|unit|distance|duration|age|measure(?:ment)?)\b|"
        r"数量|数字|数值|单位|距离|时长|年龄|度量",
        re.I,
    ),
    AuditRiskTag.SPATIAL: re.compile(
        r"\b(?:spatial|location|direction|inside|outside|into|out of|above|below|"
        r"behind|ahead|toward|away from|between|across|through)\b|"
        r"空间|位置|方向|内外|进入|离开|上方|下方|后方|前方|朝向|远离|之间|穿过",
        re.I,
    ),
    AuditRiskTag.POLARITY: re.compile(
        r"\b(?:negation|polarity|opposite|revers(?:al|ed)|not|never|no longer|"
        r"fails? to|incorrectly affirms?|incorrectly denies?)\b|"
        r"否定|肯定|极性|相反|反转|颠倒|不再|没有|从未",
        re.I,
    ),
    AuditRiskTag.ATTACHMENT: re.compile(
        r"\b(?:attachment|attaches?|modifies?|modifier|referent|pronoun|antecedent|"
        r"qualifier|scope)\b|修饰|指代|代词|先行词|附着|辖域",
        re.I,
    ),
    AuditRiskTag.RELATION: re.compile(
        r"\b(?:subject|object|agent|patient|actor|recipient|possessor|ownership|"
        r"who (?:does|did)|caus(?:e|al|ality)|relation|role|material|substance|"
        r"container|contained|source and target)\b|"
        r"主语|宾语|施事|受事|动作主体|接受者|所有者|归属|因果|关系|角色|材料|材质|容器",
        re.I,
    ),
}


class AuditIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    segment_id: str = Field(min_length=1)
    category: AuditCategory
    severity: AuditSeverity
    message: str = Field(min_length=3)
    suggested_fix: str = ""
    source: str = "deterministic"
    source_quote: str = Field(
        default="",
        description="Short verbatim source excerpt grounding a semantic finding.",
    )
    translation_quote: str = Field(
        default="",
        description="Short verbatim translation excerpt grounding a semantic finding.",
    )
    risk_tags: set[AuditRiskTag] = Field(default_factory=set)
    quantity_mismatches: list[QuantityMismatch] = Field(default_factory=list)
    # What the pipeline found, for code that acts on a finding: the message is
    # for a reader and may be reworded or translated. Empty for a finding the
    # auditor model wrote and for one that nothing acts on.
    code: str = ""

    @model_serializer(mode="wrap")
    def _omit_absent_code(self, handler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("code"):
            data.pop("code", None)
        return data


def _without_decimal_patterns(schema):
    """Drop the regular expression newer pydantic releases attach to a decimal
    written as a string. What the auditor is sent must not change with the
    installed library, and the pattern uses a lookahead that grammar-based
    structured output does not support."""
    if isinstance(schema, dict):
        if schema.get("type") == "string" and str(schema.get("pattern", "")).startswith("^(?!^[-+.]*$)"):
            schema = {key: value for key, value in schema.items() if key != "pattern"}
        return {key: _without_decimal_patterns(value) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_without_decimal_patterns(item) for item in schema]
    return schema


class SemanticAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[AuditIssue] = Field(default_factory=list)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):  # type: ignore[override]
        """The schema sent to the semantic auditor, without pipeline-only categories.

        Keeps the auditor's structured output exactly as it was before those
        categories existed, so it cannot start labelling findings `consistency`.
        """
        schema = super().model_json_schema(*args, **kwargs)
        # The pipeline's own findings carry a code; the auditor does not write one.
        schema.get("$defs", {}).get("AuditIssue", {}).get("properties", {}).pop("code", None)
        category = schema.get("$defs", {}).get("AuditCategory")
        if category and "enum" in category:
            category["enum"] = [
                value for value in category["enum"] if value not in PIPELINE_ONLY_CATEGORIES
            ]
        return _without_decimal_patterns(schema)


class DocumentAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    archive_path: str
    direction: TranslationDirection
    segment_count: int = Field(ge=0)
    passed: bool
    issues: list[AuditIssue] = Field(default_factory=list)
    semantic_candidate_ids: list[str] = Field(default_factory=list)
    quantity_candidate_ids: list[str] = Field(default_factory=list)
    quantity_comparisons: list[QuantityComparison] = Field(default_factory=list)


class TranslationAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: TranslationDirection
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    passed: bool
    issue_count: int = Field(ge=0)
    review_segment_ids: list[str] = Field(default_factory=list)
    quantity_checked_segment_count: int = Field(default=0, ge=0)
    quantity_mismatch_count: int = Field(default=0, ge=0)
    quantity_uncertain_count: int = Field(default=0, ge=0)


# Han characters: Chinese number words in a target, and Han inside quotations.
_CJK = profile("zh").script_pattern
_LATIN = re.compile(r"[A-Za-z]")
# Letters of every profiled script, kept when comparing source and target prose.
_PROSE_CHARS = "".join(item.script_basic_chars for item in TUNED_PROFILES.values())
_NUMBER_OR_UNIT = re.compile(
    r"\d|\b(?:mile|miles|inch|inches|foot|feet|yard|yards|pound|pounds|"
    r"ounce|ounces|degree|degrees|percent|percentage)\b",
    flags=re.IGNORECASE,
)
_MOJIBAKE = re.compile(r"(?:â€|Ã.|Â.|�)")
_SEPARATOR = re.compile(r"^[\W_]+$", flags=re.UNICODE)
_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_SENTENCE_SPLIT = re.compile(rf"(?<=[{SENTENCE_END}])\s*")


def audit_translated_document(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    config: AuditConfig,
    numeric_rulings: Mapping[str, NumericRuling] | None = None,
) -> DocumentAudit:
    """Run deterministic structure, completeness, language, duplication, and style checks.

    ``numeric_rulings`` are stored model rulings on the rule-based numeric check;
    a ruling for the current text settles that segment's rule finding.
    """
    issues: list[AuditIssue] = []
    source_ids = [item.segment_id for item in source.segments]
    target_ids = [item.segment_id for item in translated.segments]
    if source_ids != target_ids:
        missing = [item for item in source_ids if item not in set(target_ids)]
        unexpected = [item for item in target_ids if item not in set(source_ids)]
        detail = f"segment order or identity differs; missing={missing}, unexpected={unexpected}"
        issues.append(_issue(source_ids[0] if source_ids else "document", AuditCategory.STRUCTURE, AuditSeverity.HIGH, detail))

    source_by_id = {item.segment_id: item for item in source.segments}
    translated_by_id = {item.segment_id: item for item in translated.segments}
    comparable_ids = [item for item in source_ids if item in translated_by_id]
    lowercase_words = lowercase_source_words(source)
    normalized_targets: dict[str, list[str]] = {}
    semantic_eligible_ids: set[str] = set()
    quantity_candidate_ids: list[str] = []
    quantity_comparisons: list[QuantityComparison] = []
    for segment_id in comparable_ids:
        source_text = source_by_id[segment_id].processed_text.strip()
        target_text = translated_by_id[segment_id].translated_text.strip()
        if not target_text:
            issues.append(_issue(segment_id, AuditCategory.EMPTY, AuditSeverity.HIGH, "translation is empty"))
            continue
        if _MOJIBAKE.search(target_text):
            issues.append(_issue(segment_id, AuditCategory.PUNCTUATION, AuditSeverity.HIGH, "translation contains likely mojibake characters"))
        source_inline_markers = re.findall(r"</?I\d{3}>", source_text)
        target_inline_markers = re.findall(r"</?I\d{3}>", target_text)
        if source_inline_markers != target_inline_markers:
            issues.append(
                _issue(
                    segment_id,
                    AuditCategory.STRUCTURE,
                    AuditSeverity.HIGH,
                    "protected inline marker sequence differs from source",
                )
            )
        elif (
            source_wrappers := full_span_inline_wrapper_ids(source_text)
        ) and full_span_inline_wrapper_ids(target_text)[: len(source_wrappers)] != source_wrappers:
            issues.append(
                _issue(
                    segment_id,
                    AuditCategory.STRUCTURE,
                    AuditSeverity.HIGH,
                    "full-span inline wrappers no longer enclose the complete translation",
                )
            )
        kind = classify_segment(
            source_text,
            target_text,
            translated.direction,
            source.relevant_glossary,
        )
        if config.quantity.enabled:
            comparison = compare_quantity_texts(
                source_text,
                target_text,
                segment_id=segment_id,
            )
            # Every semantic quantity candidate must retain the comparison that
            # caused it to be scheduled.  Relational language can be uncertain
            # even when neither side yields an exact typed fact.
            if (
                comparison.status != "match"
                or comparison.source_facts
                or comparison.target_facts
                or contains_quantity_expression(source_text)
            ):
                quantity_comparisons.append(comparison)
            # Every non-match is only a deterministic *candidate*.  Cross-language
            # wording such as ``next day`` -> ``第二天`` or ``a foot`` -> ``一只脚``
            # can look like a high-confidence typed mismatch while remaining a
            # faithful translation.  The audit stage contextually adjudicates both
            # mismatch and uncertain candidates before they become repair work.
            if comparison.status != "match":
                quantity_candidate_ids.append(segment_id)
            if comparison.status == "mismatch" and config.quantity.mode != "shadow":
                issues.append(_quantity_issue(comparison, config.quantity.mode))
            # Once the typed checker is enabled it is the sole numeric authority.
            # Shadow mode records comparisons without affecting pass/fail; running
            # the legacy token multiset here would reintroduce representation-only
            # false positives after semantic adjudication.
        else:
            _audit_number_integrity(segment_id, source_text, target_text, issues, translated.direction)
        if kind is SegmentKind.STRUCTURAL:
            continue
        if should_run_language_check(kind):
            _audit_language(
                segment_id,
                source_text,
                target_text,
                translated.direction,
                config,
                issues,
                source.relevant_glossary,
            )
        if should_run_length_check(kind):
            _audit_length(segment_id, source_text, target_text, config, issues)
        if should_run_semantic_audit(kind):
            semantic_eligible_ids.add(segment_id)
        _audit_glossary(
            segment_id, source_text, target_text, source, translated.direction, issues, lowercase_words
        )
        _audit_internal_duplication(segment_id, source_text, target_text, issues)
        _audit_ai_style(segment_id, target_text, translated.direction, issues)
        normalized = _normalize_prose(target_text)
        if len(normalized) >= 12:
            normalized_targets.setdefault(normalized, []).append(segment_id)

    for duplicate_ids in normalized_targets.values():
        if len(duplicate_ids) < 2:
            continue
        distinct_sources = {
            _normalize_prose(source_by_id[item].processed_text) for item in duplicate_ids
        }
        if len(distinct_sources) > 1:
            for segment_id in duplicate_ids[1:]:
                issues.append(
                    _issue(
                        segment_id,
                        AuditCategory.DUPLICATION,
                        AuditSeverity.HIGH,
                        f"translation duplicates a different source segment: {duplicate_ids[0]}",
                        DUPLICATES_OTHER_SEGMENT,
                    )
                )
    # The same passage worded twice: one of the two translations stands under
    # the wrong segment (a model translating a neighbouring passage).
    exact = {item for ids in normalized_targets.values() if len(ids) > 1 for item in ids}
    for segment_id, other_id in misplaced_passages(
        [
            (item.segment_id, source_by_id[item.segment_id].processed_text, item.translated_text)
            for item in translated.segments
            if item.segment_id in source_by_id
        ]
    ):
        if segment_id not in exact:
            issues.append(
                _issue(
                    segment_id,
                    AuditCategory.DUPLICATION,
                    AuditSeverity.HIGH,
                    f"translation closely matches that of a different source segment: {other_id}",
                    MATCHES_OTHER_SEGMENT,
                )
            )
    # A run of translations each standing some segments away from its source.
    for segment_id, offset in shifted_passages(
        [
            (item.segment_id, source_by_id[item.segment_id].processed_text, item.translated_text)
            for item in translated.segments
            if item.segment_id in source_by_id
        ]
    ):
        issues.append(
            _issue(
                segment_id,
                AuditCategory.STRUCTURE,
                AuditSeverity.HIGH,
                "translation fits the source segment "
                f"{abs(offset)} {'later' if offset > 0 else 'earlier'}, not this one: "
                "the translations here appear shifted",
                SHIFTED_TRANSLATION,
            )
        )

    # Far shorter or longer than this document's translations usually are. The
    # en/zh pairs keep their fixed token ratio (_audit_length).
    if not translated.direction.legacy:
        for segment_id, share in unusual_length_passages(
            [
                (item.segment_id, source_by_id[item.segment_id].processed_text, item.translated_text)
                for item in translated.segments
                if item.segment_id in source_by_id
            ]
        ):
            issues.append(
                _issue(
                    segment_id,
                    AuditCategory.OMISSION if share < 1 else AuditCategory.ADDITION,
                    AuditSeverity.HIGH,
                    f"translation length is far from usual here: {share} of the usual length for its source",
                    UNUSUAL_LENGTH,
                )
            )

    if source.cues and source.reading_limits:
        issues.extend(_audit_reading(source, translated))

    if numeric_rulings:
        issues = apply_numeric_rulings(
            issues,
            {
                segment_id: (
                    source_by_id[segment_id].processed_text,
                    translated_by_id[segment_id].translated_text,
                )
                for segment_id in comparable_ids
            },
            numeric_rulings,
        )
    issues = deduplicate_audit_issues(issues)
    candidates = select_semantic_audit_candidates(
        source,
        issues,
        config,
        eligible_ids=semantic_eligible_ids,
        # For the en/zh pairs the rules are strong enough to pick the risky
        # segments. For any other pair they are not: a short line translated
        # as another line passed them all (阿Q正传 into Japanese), so the model
        # reads every prose segment.
        every_segment=not translated.direction.legacy,
    )
    return DocumentAudit(
        document_id=source.manifest_id,
        archive_path=source.archive_path,
        direction=translated.direction,
        segment_count=len(source.segments),
        passed=not any(item.severity.rank >= AuditSeverity.MEDIUM.rank for item in issues),
        issues=issues,
        semantic_candidate_ids=candidates,
        quantity_candidate_ids=quantity_candidate_ids,
        quantity_comparisons=quantity_comparisons,
    )


def reconcile_document_audit(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    historical: DocumentAudit,
    config: AuditConfig,
    numeric_rulings: Mapping[str, NumericRuling] | None = None,
) -> DocumentAudit:
    """Recompute facts from current text and retain only still-applicable model judgments."""
    current = audit_translated_document(source, translated, config, numeric_rulings)
    target_by_id = {item.segment_id: item.translated_text for item in translated.segments}
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    retained: list[AuditIssue] = []
    for issue in historical.issues:
        if (
            issue.source == "translation-deferred"
            and issue.segment_id in source_by_id
        ):
            retained.append(issue)
            continue
        if issue.source != "semantic" or issue.segment_id not in source_by_id:
            continue
        kind = classify_segment(
            source_by_id[issue.segment_id],
            target_by_id.get(issue.segment_id, ""),
            translated.direction,
            source.relevant_glossary,
        )
        if not should_run_semantic_audit(kind):
            continue
        if issue.category is AuditCategory.PUNCTUATION:
            issue = issue.model_copy(update={"severity": AuditSeverity.LOW})
        retained.append(issue)
    issues = deduplicate_audit_issues([*current.issues, *retained])
    return current.model_copy(
        update={
            "issues": issues,
            "passed": not any(
                item.severity.rank >= AuditSeverity.MEDIUM.rank for item in issues
            ),
        }
    )


def reapply_quantity_adjudications(
    current: DocumentAudit,
    historical: DocumentAudit | None,
) -> DocumentAudit:
    """Carry forward model judgments when the underlying quantity facts are unchanged.

    Final validation deliberately recomputes deterministic facts from the repaired
    text.  A raw bilingual parser mismatch is only a candidate, however, and must
    not overwrite a contextual judgment already made against the exact same source
    and target facts.  Changed facts are left untouched so they can fail closed.
    """
    if historical is None:
        return current
    prior = {
        item.segment_id: item
        for item in historical.quantity_comparisons
        if item.adjudicated
    }
    accepted_ids: set[str] = set()
    comparisons: list[QuantityComparison] = []
    for comparison in current.quantity_comparisons:
        decision = prior.get(comparison.segment_id)
        if (
            decision is not None
            and _quantity_evidence_signature(comparison)
            == _quantity_evidence_signature(decision)
        ):
            comparison = comparison.model_copy(
                update={
                    "status": decision.status,
                    "reason": decision.reason,
                    "adjudicated": True,
                }
            )
            if decision.status == "match":
                accepted_ids.add(comparison.segment_id)
        comparisons.append(comparison)
    if not accepted_ids:
        return current.model_copy(update={"quantity_comparisons": comparisons})
    issues = [
        issue
        for issue in current.issues
        if not (
            issue.source == "quantity-deterministic"
            and issue.segment_id in accepted_ids
        )
    ]
    return current.model_copy(
        update={
            "quantity_comparisons": comparisons,
            "issues": issues,
            "passed": not any(
                issue.severity.rank >= AuditSeverity.MEDIUM.rank
                for issue in issues
            ),
        }
    )


def _quantity_evidence_signature(comparison: QuantityComparison) -> tuple:
    """Return the parser evidence independently of its later adjudication."""
    return (
        tuple(item.model_dump_json() for item in comparison.source_facts),
        tuple(item.model_dump_json() for item in comparison.target_facts),
        tuple(item.model_dump_json() for item in comparison.mismatches),
    )


def select_semantic_audit_candidates(
    document: PreprocessedDocument,
    issues: list[AuditIssue],
    config: AuditConfig,
    *,
    eligible_ids: set[str] | None = None,
    every_segment: bool = False,
) -> list[str]:
    """Select bounded high-risk segments for semantic review in document order.

    With `every_segment`, every eligible segment, without the per-document cap.
    """
    selected = {
        issue.segment_id
        for issue in issues
        if issue.segment_id != "document"
        and not (config.quantity.enabled and issue.source.startswith("quantity-"))
        and (eligible_ids is None or issue.segment_id in eligible_ids)
    }
    for index, segment in enumerate(document.segments, start=1):
        text = segment.processed_text
        if eligible_ids is not None and segment.segment_id not in eligible_ids:
            continue
        if is_decorative_separator(text):
            continue
        if (
            every_segment
            or estimate_tokens(text) >= config.semantic_min_source_tokens
            or (
                not config.quantity.enabled
                and _NUMBER_OR_UNIT.search(visible_segment_text(text))
            )
            or (config.semantic_sample_every and index % config.semantic_sample_every == 0)
        ):
            selected.add(segment.segment_id)
    ordered = [item.segment_id for item in document.segments if item.segment_id in selected]
    return ordered if every_segment else ordered[: config.max_semantic_candidates_per_document]


def build_semantic_audit_batches(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    candidate_ids: list[str],
    max_source_tokens: int,
    *,
    max_request_context: int | None = None,
    context_multiplier: float = 2.0,
    max_candidates_per_batch: int | None = None,
) -> list[list[str]]:
    """Batch IDs within both source-token and complete request-context ceilings."""
    if max_source_tokens <= 0:
        raise ValueError("max_source_tokens must be positive")
    if max_request_context is not None and max_request_context <= 0:
        raise ValueError("max_request_context must be positive")
    if context_multiplier <= 0:
        raise ValueError("context_multiplier must be positive")
    if max_candidates_per_batch is not None and max_candidates_per_batch <= 0:
        raise ValueError("max_candidates_per_batch must be positive")
    allowed = {item.segment_id for item in source.segments}
    unknown = [item for item in candidate_ids if item not in allowed]
    if unknown:
        raise ValueError(f"unknown semantic audit segment IDs: {unknown}")
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    batches: list[list[str]] = []
    current: list[str] = []
    tokens = 0

    def fits_request_context(ids: list[str]) -> bool:
        if max_request_context is None:
            return True
        if len(ids) > 1:
            prompt = build_semantic_audit_prompt(source, translated, ids)
            schema = SemanticAuditResult.model_json_schema()
            request_tokens = estimate_request_tokens(prompt, schema)
            required = math.ceil(request_tokens * context_multiplier) + 4_096
            return required <= max_request_context
        try:
            build_semantic_audit_prompt(
                source,
                translated,
                ids,
                max_request_context=max_request_context,
                context_multiplier=context_multiplier,
            )
        except ValueError as error:
            if "semantic audit prompt exceeds request context" not in str(error):
                raise
            return False
        return True

    for segment_id in candidate_ids:
        item_tokens = max(1, estimate_tokens(source_by_id[segment_id]))
        if item_tokens > max_source_tokens:
            raise ValueError(
                f"semantic audit segment exceeds batch budget: {segment_id}"
            )
        proposed = [*current, segment_id]
        if current and (
            tokens + item_tokens > max_source_tokens
            or not fits_request_context(proposed)
            or (
                max_candidates_per_batch is not None
                and len(proposed) > max_candidates_per_batch
            )
        ):
            batches.append(current)
            current = []
            tokens = 0
            proposed = [segment_id]
        if not fits_request_context(proposed):
            raise ValueError(
                f"semantic audit segment exceeds request context: {segment_id}"
            )
        current.append(segment_id)
        tokens += item_tokens
    if current:
        batches.append(current)
    return batches


def build_semantic_audit_prompt(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    candidate_ids: list[str],
    *,
    max_request_context: int | None = None,
    context_multiplier: float = 2.0,
) -> str:
    """Build a scoped audit prompt, shedding optional neighbors only when required."""
    if max_request_context is not None and max_request_context <= 0:
        raise ValueError("max_request_context must be positive")
    if context_multiplier <= 0:
        raise ValueError("context_multiplier must be positive")
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    target_by_id = {item.segment_id: item.translated_text for item in translated.segments}
    ordered_ids = [item.segment_id for item in source.segments]
    unknown = [item for item in candidate_ids if item not in set(ordered_ids)]
    if unknown:
        raise ValueError(f"unknown semantic audit segment IDs: {unknown}")
    candidate_set = set(candidate_ids)
    full_scope = expand_semantic_audit_scope(source, candidate_ids)

    def render(included_ids: set[str]) -> str:
        blocks = []
        for segment_id in ordered_ids:
            if segment_id not in included_ids:
                continue
            role = "AUDIT HIGH RISK" if segment_id in candidate_set else "CONTEXT ONLY"
            blocks.append(
                f"[{segment_id}] {role}\nSOURCE: {source_by_id[segment_id]}\nTRANSLATION: {target_by_id.get(segment_id, '(missing)')}"
            )
        allowed = ", ".join(item for item in ordered_ids if item in candidate_set)
        glossary = ""
        scoped_glossary = select_relevant_glossary_entries(
            "\n".join(source_by_id[item] for item in ordered_ids if item in included_ids),
            source.relevant_glossary,
            translated.direction,
        )
        if scoped_glossary:
            glossary_lines = [
                f"- {entry.source} => {entry.target}"
                for entry in scoped_glossary
            ]
            glossary = (
                "\n\nAPPROVED GLOSSARY (mandatory; never criticize an exact applicable "
                "rendering from this list):\n" + "\n".join(glossary_lines)
            )
        return (
        "Act as a strict bilingual literary translation auditor. Report only concrete "
        "errors in the included AUDIT segments. HIGH RISK marks triggered selection; CONTEXT "
        "ONLY marks adjacent passages supplied solely as evidence. Never report a finding "
        "against a CONTEXT ONLY segment. Audit each HIGH RISK segment by first comparing its "
        "atomic facts: who or what is the subject; action versus state; location, direction, "
        "distance, and spatial relationship; quantity and unit; negation, modality, causality, "
        "and temporal order. A translation must not turn a state, position, separation, or "
        "measurement into motion or another invented action merely because it preserves the "
        "same number and unit. After finding one defect, continue checking the rest of that "
        "segment and report every independent concrete defect as a separate issue. Check "
        "omissions, unsupported additions, mistranslation, "
        "numbers and units, names and wordplay, glossary consistency, awkward target-language "
        "prose, accidental repetition, and model-like formulaic phrasing. Intentional source "
        "repetition is not an error. Do not report a translation that is correct or "
        "grammatically acceptable merely because you prefer an alternative wording or word "
        "order. Report awkward prose only when it is a concrete target-language defect, not "
        "a matter of stylistic taste. A source phrase intentionally cut off by a dash before "
        "the following verse or passage is not an omission when the translation preserves "
        "that same fragment and cutoff. A quoted third-language phrase retained from the "
        "source is not untranslated target prose. A short source-owned italicized fictional "
        "name or coined term may either remain in Latin script or be consistently "
        "transliterated when no approved glossary rendering exists. Natural target wording "
        "may make an elliptical purpose such as 'to be sure' explicit; report an addition "
        "only when it introduces a new factual proposition. Before claiming that a number "
        "is omitted, verify whether its target-language equivalent is visibly present. "
        "Every issue must use an allowed segment ID, a precise "
        "explanation, and a complete actionable suggested fix. Every reported issue must "
        "label every applicable risk_tags value: quantity for numeric facts; spatial for "
        "location or direction; polarity for negation or reversal; attachment for modifier "
        "or referent scope; and relation for subject/object, agent/patient, ownership, "
        "causality, named roles, or material-versus-location confusion. It must also "
        "contain source_quote and/or translation_quote copied verbatim from that same "
        "segment; provide both whenever both sides contain the relevant wording. Never copy "
        "evidence from a neighboring segment. Do not score the translation. "
            f"{SUBTITLE_AUDIT_NOTE if source.cues is not None else ''}"
            f"Allowed IDs: {allowed}{glossary}\n\n" + "\n\n".join(blocks)
        )

    def fits(prompt: str) -> bool:
        if max_request_context is None:
            return True
        request_tokens = estimate_request_tokens(
            prompt,
            SemanticAuditResult.model_json_schema(),
        )
        required = math.ceil(request_tokens * context_multiplier) + 4_096
        return required <= max_request_context

    included = set(full_scope)
    prompt = render(included)
    if fits(prompt):
        return prompt

    # The adaptive bucket heuristic deliberately overestimates completion growth by
    # doubling the complete request and then reserving another 4K.  Do not discard
    # useful neighbor context when the real request still leaves a full 4K completion
    # window inside the model cap.
    if (
        max_request_context is not None
        and estimate_request_tokens(
            prompt,
            SemanticAuditResult.model_json_schema(),
        )
        + 4_096
        <= max_request_context
    ):
        return prompt

    # Immediate neighbors improve continuity judgments but are evidence only.  Under a
    # tight per-model cap, discard the largest optional block first while retaining all
    # high-risk source and translation text verbatim.  Normal prompts never enter this
    # path, so their work-unit hashes remain stable across resumes.
    optional_ids = [item for item in full_scope if item not in candidate_set]
    optional_ids.sort(
        key=lambda item: (
            estimate_tokens(source_by_id[item])
            + estimate_tokens(target_by_id.get(item, "")),
            -ordered_ids.index(item),
        ),
        reverse=True,
    )
    for segment_id in optional_ids:
        included.remove(segment_id)
        prompt = render(included)
        if fits(prompt):
            return prompt

    # The schema itself is sizeable.  A single moderate segment can therefore miss an
    # 8K cap by a few tokens even after all optional context is gone.  Compress only
    # redundant prose in the instructions; retain the complete candidate, glossary,
    # evidence requirements, risk taxonomy, and structured response schema.
    compact_prompt = prompt.replace(
        "Act as a strict bilingual literary translation auditor. Report only concrete "
        "errors in the included AUDIT segments. HIGH RISK marks triggered selection; CONTEXT "
        "ONLY marks adjacent passages supplied solely as evidence. Never report a finding "
        "against a CONTEXT ONLY segment. ",
        "Strictly audit only the allowed HIGH RISK segments for concrete bilingual "
        "translation errors. CONTEXT ONLY text is evidence, never an audit target. ",
    ).replace(
        "Intentional source repetition is not an error. Do not report a translation that "
        "is correct or grammatically acceptable merely because you prefer an alternative "
        "wording or word order. Report awkward prose only when it is a concrete "
        "target-language defect, not a matter of stylistic taste. ",
        "Do not flag intentional repetition, faithful grammatical wording, or mere style "
        "preference. Report awkward prose only for a concrete target-language defect. ",
    )
    if fits(compact_prompt):
        return compact_prompt

    raise ValueError(
        "semantic audit prompt exceeds request context for candidates: "
        + ", ".join(candidate_ids)
    )


def expand_semantic_audit_scope(
    document: PreprocessedDocument,
    candidate_ids: list[str],
) -> list[str]:
    """Include immediate neighbors already needed to judge candidate continuity."""
    ordered = [item.segment_id for item in document.segments]
    unknown = [item for item in candidate_ids if item not in set(ordered)]
    if unknown:
        raise ValueError(f"unknown semantic audit segment IDs: {unknown}")
    selected = set(candidate_ids)
    for candidate in candidate_ids:
        position = ordered.index(candidate)
        if position:
            selected.add(ordered[position - 1])
        if position + 1 < len(ordered):
            selected.add(ordered[position + 1])
    return [item for item in ordered if item in selected]


def validate_semantic_audit_scope(
    result: SemanticAuditResult,
    allowed_ids: list[str],
    source_text_by_id: dict[str, str] | None = None,
    translation_text_by_id: dict[str, str] | None = None,
    *,
    quantity_enabled: bool = False,
    direction: TranslationDirection | None = None,
) -> SemanticAuditResult:
    """Reject unsafe scope errors, ground evidence, and normalize owned metadata."""
    normalized: list[AuditIssue] = []
    for issue in result.issues:
        canonical_segment_id = _canonical_allowed_segment_id(
            issue.segment_id, allowed_ids
        )
        if canonical_segment_id is None:
            raise ValueError(f"semantic auditor returned out-of-scope segment: {issue.segment_id}")
        if issue.segment_id != canonical_segment_id:
            issue = issue.model_copy(update={"segment_id": canonical_segment_id})
        if _is_noop_suggested_fix(issue.suggested_fix):
            continue
        if source_text_by_id is not None and translation_text_by_id is not None:
            if not issue.source_quote and not issue.translation_quote:
                continue
            grounded_ids = [
                segment_id
                for segment_id in allowed_ids
                if (
                    not issue.source_quote
                    or _contains_audit_quote(
                        source_text_by_id.get(segment_id, ""), issue.source_quote
                    )
                )
                and (
                    not issue.translation_quote
                    or _contains_audit_quote(
                        translation_text_by_id.get(segment_id, ""), issue.translation_quote
                    )
                )
            ]
            if len(grounded_ids) != 1:
                continue
            if issue.segment_id != grounded_ids[0]:
                issue = issue.model_copy(update={"segment_id": grounded_ids[0]})
            source_text = source_text_by_id.get(issue.segment_id, "")
            translation_text = translation_text_by_id.get(issue.segment_id, "")
            lowered_message = issue.message.casefold()
            if (
                issue.category is AuditCategory.OMISSION
                and ("number" in lowered_message or "countdown" in lowered_message)
                and (
                    compare_quantity_texts(source_text, translation_text).status == "match"
                    if quantity_enabled
                    else numeric_content_matches(source_text, translation_text, direction)
                )
            ):
                continue
        if issue.source != "semantic":
            issue = issue.model_copy(update={"source": "semantic"})
        if issue.category is AuditCategory.PUNCTUATION:
            issue = issue.model_copy(update={"severity": AuditSeverity.LOW})
        if is_preference_only_audit_issue(issue) or _is_preserved_identifier_preference(
            issue
        ):
            continue
        if len(issue.suggested_fix.strip()) < 3:
            issue = issue.model_copy(
                update={
                    "suggested_fix": (
                        "Correct only this passage so it faithfully resolves the audit "
                        f"finding: {issue.message}"
                    )
                }
            )
        inferred_risk_tags = infer_semantic_risk_tags(issue)
        if inferred_risk_tags != issue.risk_tags:
            issue = issue.model_copy(update={"risk_tags": inferred_risk_tags})
        normalized.append(issue)
    return result.model_copy(update={"issues": normalized})


def infer_semantic_risk_tags(issue: AuditIssue) -> set[AuditRiskTag]:
    """Add stable high-risk labels from an auditor's grounded diagnosis.

    Models are allowed to emit the labels themselves, but downstream approval must
    not depend on a particular model remembering an optional JSON field.  We infer
    only from the diagnosis and requested fix, not from surrounding source prose,
    so incidental words such as ``inside`` do not make an unrelated issue high risk.
    """
    tags = set(issue.risk_tags)
    diagnosis = f"{issue.message}\n{issue.suggested_fix}"
    for tag, pattern in _SEMANTIC_RISK_PATTERNS.items():
        if pattern.search(diagnosis):
            tags.add(tag)
    if issue.quantity_mismatches:
        tags.update({AuditRiskTag.QUANTITY, AuditRiskTag.RELATION})
    return tags


def _canonical_allowed_segment_id(
    segment_id: str,
    allowed_ids: list[str],
) -> str | None:
    """Resolve only zero-padding variants of one uniquely allowed segment ID."""
    if segment_id in allowed_ids:
        return segment_id
    match = re.fullmatch(r"D(\d+)-S(\d+)", segment_id)
    if match is None:
        return None
    identity = (int(match.group(1)), int(match.group(2)))
    matches = []
    for allowed_id in allowed_ids:
        allowed_match = re.fullmatch(r"D(\d+)-S(\d+)", allowed_id)
        if allowed_match is None:
            continue
        if (int(allowed_match.group(1)), int(allowed_match.group(2))) == identity:
            matches.append(allowed_id)
    return matches[0] if len(matches) == 1 else None


def _contains_audit_quote(text: str, quote: str) -> bool:
    """Match verbatim evidence while ignoring only Unicode/whitespace variation."""
    normalized_text = " ".join(unicodedata.normalize("NFKC", text).split())
    normalized_quote = " ".join(unicodedata.normalize("NFKC", quote).split())
    return bool(normalized_quote) and normalized_quote in normalized_text


def _is_noop_suggested_fix(suggested_fix: str) -> bool:
    """Reject an explicit change instruction whose before/after text is equal."""
    if not re.search(r"\b(?:change|replace)\b|(?:改为|改成|替换)", suggested_fix, re.I):
        return False
    quoted: list[str] = []
    for pattern in (
        r"'([^'\r\n]+)'",
        r'"([^"\r\n]+)"',
        r"‘([^’\r\n]+)’",
        r"“([^”\r\n]+)”",
    ):
        quoted.extend(re.findall(pattern, suggested_fix))
    if len(quoted) < 2:
        return False
    normalize = lambda value: " ".join(unicodedata.normalize("NFKC", value).split())
    return normalize(quoted[0]) == normalize(quoted[1])


def is_preference_only_audit_issue(issue: AuditIssue) -> bool:
    """Return true when an issue concedes correctness and asks only for preference."""
    if issue.severity is AuditSeverity.HIGH:
        return False
    text = issue.message.casefold()
    return any(
        phrase in text
        for phrase in (
            "grammatically acceptable",
            "translation is acceptable",
            "correct but slightly",
            "stylistic preference",
            "语法正确",
            "可以接受",
            "仅属风格偏好",
        )
    )


def _is_preserved_identifier_preference(issue: AuditIssue) -> bool:
    """Drop requests to translate an exact source code or numbered designation.

    Exact identifiers are permitted to remain in target prose unless an approved
    glossary requires another rendering. This filter is deliberately limited to
    non-high findings whose evidence preserves the same qualified identifier and
    whose complaint is specifically that it was not expanded or translated.
    """
    if issue.severity is AuditSeverity.HIGH:
        return False
    if not issue.source_quote or not issue.translation_quote:
        return False
    identifier_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:[A-Z]{2,6}\s*#\s*\d+|"
        r"[A-Z]{2,12}(?:[-/.]\d+[A-Za-z0-9-]*)?)(?![A-Za-z0-9])"
    )
    source_identifiers = {
        re.sub(r"\s+", "", match.group(0)).casefold()
        for match in identifier_pattern.finditer(issue.source_quote)
    }
    target_identifiers = {
        re.sub(r"\s+", "", match.group(0)).casefold()
        for match in identifier_pattern.finditer(issue.translation_quote)
    }
    if not source_identifiers.intersection(target_identifiers):
        return False
    complaint = issue.message.casefold()
    return any(
        phrase in complaint
        for phrase in (
            "fails to translate",
            "failed to translate",
            "left untranslated",
            "leaving it as",
            "expand the abbreviation",
            "untranslated abbreviation",
            "untranslated acronym",
        )
    )


def merge_audit_issues(*groups: list[AuditIssue]) -> list[AuditIssue]:
    """Merge issue groups deterministically while retaining distinct categories."""
    return deduplicate_audit_issues([item for group in groups for item in group])


def deduplicate_audit_issues(issues: list[AuditIssue]) -> list[AuditIssue]:
    """Deduplicate issues by segment/category/message and sort in stable order."""
    unique: dict[tuple[str, str, str], AuditIssue] = {}
    for issue in issues:
        key = (issue.segment_id, issue.category.value, " ".join(issue.message.casefold().split()))
        unique.setdefault(key, issue)
    return sorted(
        unique.values(),
        key=lambda item: (item.segment_id, -item.severity.rank, item.category.value, item.message),
    )


def _audit_language(
    segment_id, source, target, direction, config, issues, glossary=()
) -> None:
    source_rules = profile(direction.source_language)
    target_rules = profile(direction.target_language)
    # When the two languages share a script (en/de, zh/ja), the script cannot
    # show that a passage was left untranslated: only an identical long passage
    # is flagged here, and the semantic audit covers the rest.
    disjoint = scripts_disjoint(direction)
    leftover = leftover_scripts(direction)
    if (
        disjoint
        and source_rules.script_pattern.search(source)
        and not target_rules.script_pattern.search(target)
    ):
        issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.HIGH, f"translation contains no {target_rules.short_name} text", NO_TARGET_TEXT))
    if not leftover and source_rules.function_words and target_rules.function_words:
        passage = _shared_script_leftover(target, source_rules, target_rules)
        if passage:
            issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"possible untranslated {source_rules.short_name} passage: {passage}"))
    if "Latin" in leftover:
        phrase_target = _strip_exact_preserved_inline_spans(source, target)
        phrase_target = _strip_intentional_foreign_quotations(source, phrase_target)
        phrase_target = re.sub(
            r"[（(][^()（）]*[A-Za-z][^()（）]*[)）]", "", phrase_target
        )
        phrase_target = re.sub(
            r"(?:https?://|www\.)[^\s<>]+"
            r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            r"|(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|io|co\.uk)\b",
            "",
            phrase_target,
            flags=re.IGNORECASE,
        )
        phrase = re.search(
            rf"\b[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*)"
            rf"{{{config.untranslated_min_words - 1},}}\b",
            phrase_target,
        )
        if phrase and not _is_preserved_titlecase_literal(
            source, phrase.group(0)
        ):
            issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"possible untranslated {source_rules.short_name} phrase: {phrase.group(0)}"))
        stray_word = _find_inflected_target_only_latin_word(source, phrase_target)
        if not stray_word:
            stray_word = _find_exact_source_latin_residue(
                source,
                phrase_target,
                glossary,
            )
        if stray_word:
            issues.append(
                _issue(
                    segment_id,
                    AuditCategory.UNTRANSLATED,
                    AuditSeverity.MEDIUM,
                    f"possible untranslated {source_rules.short_name} word: {stray_word}",
                )
            )
    elif leftover:
        chars = (
            source_rules.script_basic_chars
            if leftover == source_rules.scripts
            else "".join(SCRIPTS[item] for item in leftover)
        )
        run = re.search(f"[{chars}]{{4,}}", target)
        if run:
            issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"possible untranslated {source_rules.short_name} text: {run.group(0)}"))
    approved = [glossary_sides(entry, direction)[1] for entry in glossary]
    if lacks_target_script(_INLINE_MARKER.sub("", target), direction, approved):
        issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.HIGH, f"translation contains no {target_rules.short_name} text", NO_TARGET_TEXT))
    copied = copied_source_run(_INLINE_MARKER.sub("", source), _INLINE_MARKER.sub("", target), direction, approved)
    if copied and _normalize_prose(source) != _normalize_prose(target):
        issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"possible untranslated {source_rules.short_name} text: {copied[:40]}"))
    worded = source_worded_passage(_INLINE_MARKER.sub("", source), _INLINE_MARKER.sub("", target), direction, approved)
    if worded and _normalize_prose(source) != _normalize_prose(target):
        issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"possible untranslated {source_rules.short_name} passage: {worded}"))
    if not direction.legacy:
        stray = _foreign_script_text(source, target, source_rules, target_rules)
        if stray:
            issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.MEDIUM, f"text in a script neither language uses: {stray}"))
    if _normalize_prose(source) == _normalize_prose(target) and copy_is_untranslated(source, direction):
        issues.append(_issue(segment_id, AuditCategory.UNTRANSLATED, AuditSeverity.HIGH, "translation is identical to source", IDENTICAL_TO_SOURCE))


# Where a quoted line or a sentence ends, for the shared-script leftover check.
_PASSAGE_SPLIT = re.compile(r"[«»“”\"„‹›]|(?<=[.!?…])\s+")
_WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?")


def _foreign_script_text(source, target, source_rules, target_rules) -> str:
    """Letters of a script that neither the source nor the target language is
    written in, and that the source text does not contain: a model slipping
    into a third language (Chinese words in a Korean translation of English).
    Latin is left out: names and brands are written in it everywhere."""
    if not target_rules.scripts:
        return ""
    known = {*source_rules.scripts, *target_rules.scripts, "Latin"}
    chars = "".join(body for name, body in SCRIPTS.items() if name not in known)
    for run in re.finditer(f"[{chars}]+", _INLINE_MARKER.sub("", target)):
        text = run.group(0)
        if any(character.isalpha() for character in text) and not any(character in source for character in text):
            return text[:40]
    return ""


def _shared_script_leftover(target, source_rules, target_rules) -> str:
    """A passage of the translation still written in the source language, for a
    pair sharing a script (en/fr): two or more of the source's function words and
    none of the target's. Names and short exclamations have neither."""
    for passage in _PASSAGE_SPLIT.split(_INLINE_MARKER.sub("", target)):
        words = [word.casefold().replace("’", "'") for word in _WORD.findall(passage)]
        if len(words) < 3:
            continue
        # A word both languages use (es/fr "la", de/en "was") tells nothing.
        source_only = source_rules.function_words - target_rules.function_words
        source_hits = sum(word in source_only for word in words)
        target_hits = sum(word in target_rules.function_words for word in words)
        if source_hits >= 2 and target_hits == 0:
            return passage.strip()[:120]
    return ""


def _strip_intentional_foreign_quotations(source: str, target: str) -> str:
    """Hide clearly foreign quotations deliberately retained from the source.

    A Latin-script quotation with a diacritic is commonly a third-language phrase
    in an English-to-Chinese book (for example, a French lesson quoted verbatim).
    It is exempt only when the complete quoted content also occurs in the source.
    Ordinary copied English dialogue remains visible to the untranslated-text
    detector.
    """

    def replace(match: re.Match[str]) -> str:
        content = match.group("content")
        has_non_ascii_latin = any(
            character.isalpha()
            and ord(character) > 127
            and not _CJK.match(character)
            for character in content
        )
        normalized_content = unicodedata.normalize("NFKC", content)
        normalized_source = unicodedata.normalize("NFKC", source)
        if has_non_ascii_latin and normalized_content in normalized_source:
            return ""
        return match.group(0)

    return re.sub(
        r'["“«](?P<content>[^"”»\r\n]+)["”»]',
        replace,
        target,
    )


def _find_inflected_target_only_latin_word(source: str, target: str) -> str:
    """Find a long lowercase target word that is an altered form of source prose.

    Exact source tokens can be intentionally retained names or technical terms. An
    invented inflection with the same long stem is much stronger evidence that the
    model left prose untranslated, while remaining conservative enough for a
    deterministic gate.
    """
    source_words = {
        word.casefold()
        for word in re.findall(r"\b[A-Za-z][A-Za-z'-]{7,}\b", source)
    }
    for match in re.finditer(r"\b[a-z][a-z'-]{7,}\b", target):
        candidate = match.group(0).casefold()
        if candidate in source_words:
            continue
        for source_word in source_words:
            shared = 0
            for left, right in zip(candidate, source_word, strict=False):
                if left != right:
                    break
                shared += 1
            if shared >= 7:
                return match.group(0)
    return ""


def _find_exact_source_latin_residue(source: str, target: str, glossary=()) -> str:
    """Find one ordinary copied source word embedded in an otherwise Chinese target.

    The older phrase threshold caught long copied spans but allowed isolated model
    fallbacks such as ``clinging`` and ``sprawling`` to survive. Limit this hard
    signal to lowercase prose tokens of at least four letters. Protected inline
    spans, deliberate foreign quotations, and parenthetical glosses have already
    been removed by the caller; approved Latin-script glossary targets remain valid.
    A run of letters inside an alphanumeric identifier (``c429aaaa175d``) is part of
    that identifier, not a copied word, so digits break a candidate like letters do.
    """
    # Restrict the source side to words that actually occur in lowercase prose.
    # This excludes the common proper-name/title case without relying on a fragile
    # name list, while still catching copied words such as ``somehow`` or
    # ``occupant`` wherever they occur in an otherwise Chinese target.
    source_words = {
        match.group(0).casefold()
        for match in re.finditer(
            r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z'’-]{3,}(?![A-Za-z0-9])", source
        )
        if match.group(0)[0].islower()
    }
    approved_latin_targets: set[str] = set()
    for entry in glossary:
        target_term = getattr(entry, "target", "")
        approved_latin_targets.update(
            word.casefold()
            for word in re.findall(
                r"(?<![A-Za-z])[A-Za-z][A-Za-z'’-]{3,}(?![A-Za-z])",
                target_term,
            )
        )
    for match in re.finditer(
        r"(?<![A-Za-z0-9])[a-z][A-Za-z'’-]{3,}(?![A-Za-z0-9])", target
    ):
        candidate = match.group(0).casefold()
        if (
            candidate in source_words
            and candidate not in approved_latin_targets
            and candidate not in _VALID_LOWERCASE_LATIN_LITERALS
        ):
            return match.group(0)
    return ""


_VALID_LOWERCASE_LATIN_LITERALS = frozenset(
    {
        # Common machine-readable literals that legitimately remain Latin script
        # in Chinese prose and metadata. Book-specific names and technical terms
        # belong in the approved glossary instead of this global allowlist.
        "ascii",
        "email",
        "epub",
        "html",
        "http",
        "https",
        "isbn",
        "unicode",
        "wifi",
        "xhtml",
    }
)


def _strip_exact_preserved_inline_spans(source: str, target: str) -> str:
    """Hide exact Latin text deliberately retained inside protected inline markup."""
    pattern = re.compile(
        r"<I(?P<marker>\d{3})>(?P<content>[^<>\r\n]+)</I(?P=marker)>"
    )
    normalized_source = unicodedata.normalize("NFKC", source)

    def replace(match: re.Match[str]) -> str:
        marked = unicodedata.normalize("NFKC", match.group(0))
        content = match.group("content")
        if _LATIN.search(content) and marked in normalized_source:
            return ""
        return match.group(0)

    return pattern.sub(replace, target)


def _is_preserved_titlecase_literal(source: str, phrase: str) -> bool:
    """Treat an exact multiword title/name as ambiguous rather than a hard failure.

    A deterministic check cannot distinguish a deliberately retained code name or
    work title from untranslated prose. Ordinary copied sentences still fail because
    their non-initial words are not title-cased; semantic review remains available for
    the ambiguous proper-name case.
    """
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", phrase)
    return (
        len(words) >= 2
        and phrase in source
        and all(word[0].isupper() for word in words)
    )


# Added to the audit prompt of a subtitle job: what is a fault in a book is the craft here.
SUBTITLE_AUDIT_NOTE = (
    "These segments are subtitle cues, each on screen for a few seconds, and a sentence "
    "often runs on from one cue into the next: read a cue together with the cues before "
    "and after it before judging it. A subtitle is condensed on purpose. Do not report a "
    "dropped filler word, emphasis, or repetition, a phrase said more briefly, or wording "
    "that anticipates the next cue; report only a changed or lost fact, a wrong meaning, or "
    "a real error of the target language. A suggested fix must not be longer than the "
    "translation it replaces. Explain each issue in one sentence. "
)

# Subtitles run a little over the reading speed all the time, and a viewer
# keeps up; past this much over, the line is cut off for most of them.
READING_SPEED_TOLERANCE = 1.3
UNREADABLE_SUBTITLE = "unreadable_subtitle"


def _audit_reading(source: PreprocessedDocument, translated: TranslatedDocument) -> list[AuditIssue]:
    """A subtitle job: each cue's translation against its time on screen and the room on it."""
    limits = ReadingLimits(*source.reading_limits)
    by_cue: dict[int, list[tuple[str, str]]] = {}
    seconds: dict[int, tuple[float, bool]] = {}
    for item in translated.segments:
        if item.segment_id in source.cues:
            number, duration, speakers = source.cues[item.segment_id]
            by_cue.setdefault(number, []).append((item.segment_id, _INLINE_MARKER.sub("", item.translated_text)))
            seconds[number] = (duration, speakers)
    issues: list[AuditIssue] = []
    for number, passages in by_cue.items():
        duration, speakers = seconds[number]
        texts = [text for _, text in passages]
        # Each speaker's line is written with a dash and a space before it.
        lines = [f"- {text}" for text in texts] if speakers else wrap_cue(" ".join(texts), limits)
        count = sum(len("".join(text.split())) for text in texts)
        # The finding goes to the cue's longest passage: that is the one to shorten.
        segment_id = max(passages, key=lambda passage: len(passage[1]))[0]
        if duration > 0 and count > limits.characters_per_second * duration:
            over = count / (limits.characters_per_second * duration)
            issues.append(_issue(
                segment_id,
                AuditCategory.READABILITY,
                AuditSeverity.MEDIUM if over > READING_SPEED_TOLERANCE else AuditSeverity.LOW,
                f"subtitle is too long to read in its time: {count} characters in {duration:.1f} seconds, "
                f"where about {int(limits.characters_per_second * duration)} can be read; say it more briefly",
                UNREADABLE_SUBTITLE,
            ))
        # Its room is another matter than its time: a cue may fail either, or both.
        if len(lines) > limits.lines or any(len(line) > limits.line_characters for line in lines):
            issues.append(_issue(
                segment_id,
                AuditCategory.READABILITY,
                AuditSeverity.MEDIUM,
                f"subtitle does not fit the screen: {len(lines)} lines, the longest of {max(len(line) for line in lines)} "
                f"characters, where {limits.lines} of {limits.line_characters} fit; say it more briefly",
                UNREADABLE_SUBTITLE,
            ))
    return issues


def _audit_length(segment_id, source, target, config, issues) -> None:
    source_tokens = max(1, estimate_tokens(source))
    ratio = estimate_tokens(target) / source_tokens
    if ratio < config.min_length_ratio:
        issues.append(_issue(segment_id, AuditCategory.OMISSION, AuditSeverity.MEDIUM, f"translation/source token ratio is unusually low: {ratio:.2f}"))
    elif ratio > config.max_length_ratio:
        issues.append(_issue(segment_id, AuditCategory.ADDITION, AuditSeverity.MEDIUM, f"translation/source token ratio is unusually high: {ratio:.2f}"))


def _audit_number_integrity(segment_id, source, target, issues, direction=None) -> None:
    """Keep digit-bearing facts deterministic even for non-prose segments."""
    source_numbers = number_tokens(source, direction.source_language if direction else None)
    target_numbers = number_tokens(target, direction.target_language if direction else None)
    target_has_cjk = bool(_CJK.search(visible_segment_text(target)))
    if not numeric_content_matches(source, target, direction) and (target_numbers or not target_has_cjk):
        # Only a trigger: the audit stage asks the quantity model for a ruling.
        issues.append(
            _issue(
                segment_id,
                AuditCategory.MISTRANSLATION,
                AuditSeverity.HIGH,
                "numeric content differs from source "
                f"(source facts: {_format_number_facts(source_numbers)}; "
                f"translation facts: {_format_number_facts(target_numbers)})",
            ).model_copy(update={"source": NUMBER_RULE_SOURCE})
        )


def _quantity_issue(comparison: QuantityComparison, mode: str) -> AuditIssue:
    """Turn a provable typed mismatch into an ordinary pipeline audit issue."""
    detail = "; ".join(item.message for item in comparison.mismatches[:3])
    return AuditIssue(
        segment_id=comparison.segment_id,
        category=AuditCategory.MISTRANSLATION,
        severity=(AuditSeverity.HIGH if mode == "enforce" else AuditSeverity.LOW),
        message=f"typed quantity facts differ from source: {detail}",
        suggested_fix=(
            "Preserve the source quantity's value, unit, comparator, approximation, "
            "polarity, and attachment without changing unrelated wording."
        ),
        source="quantity-deterministic",
        risk_tags={AuditRiskTag.QUANTITY},
        quantity_mismatches=comparison.mismatches,
    )


def _format_number_facts(facts) -> str:
    if not facts:
        return "none detected"
    return ", ".join(
        f"{fact} x{count}" if count > 1 else fact
        for fact, count in sorted(facts.items())
    )


_NAME_CATEGORIES = frozenset(
    {GlossaryCategory.PERSON, GlossaryCategory.PLACE, GlossaryCategory.ORGANIZATION}
)


def lowercase_source_words(document) -> frozenset[str]:
    """Words a document's source also uses in lowercase ("two days", "a rat")."""
    return frozenset(
        word
        for segment in document.segments
        for word in re.findall(r"\b[a-z][a-z'’-]*\b", _INLINE_MARKER.sub("", segment.original_text))
    )


def _is_unambiguous_name(source_term: str, direction, lowercase_words: frozenset[str]) -> bool:
    """A name whose occurrence in the source cannot be an ordinary word.

    "Dr. Armstrong" or "Holmes" always means the character; "Two" (Alice's
    playing card), "Rat", or "Mouse" can be an ordinary word when the chapter
    also uses it in lowercase, so a miss on them stays a low finding.
    """
    if not profile(direction.source_language).cased:
        return len(source_term.strip()) >= 2
    if not any(character.isupper() for character in source_term):
        return False  # "the island": a lowercase entry is not a name, whatever its category
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", source_term)
    if len(words) != 1:
        return bool(words)
    return words[0].casefold() not in lowercase_words


WRONG_PASSAGE_MIN_TERMS = 4
WRONG_PASSAGE_BY_TERMS = "translation may be another passage's, or leaves most of this one out: it has"
# Findings that say the whole translation belongs elsewhere or is not a
# translation: repair translates the segment again instead of editing it.
NO_TARGET_TEXT = "no_target_text"
IDENTICAL_TO_SOURCE = "identical_to_source"
DUPLICATES_OTHER_SEGMENT = "duplicates_other_segment"
MATCHES_OTHER_SEGMENT = "matches_other_segment"
SHIFTED_TRANSLATION = "shifted_translation"
WRONG_PASSAGE_TERMS = "wrong_passage_terms"
UNUSUAL_LENGTH = "unusual_length"
WHOLE_TRANSLATION_CODES = frozenset({
    NO_TARGET_TEXT,
    IDENTICAL_TO_SOURCE,
    DUPLICATES_OTHER_SEGMENT,
    MATCHES_OTHER_SEGMENT,
    SHIFTED_TRANSLATION,
    WRONG_PASSAGE_TERMS,
    UNUSUAL_LENGTH,
})
# The wording of those findings, for an audit written before they had a code.
WHOLE_TRANSLATION_WRONG = re.compile(
    r"^(?:translation contains no .+ text$"
    r"|translation is identical to source$"
    r"|translation duplicates a different source segment"
    r"|translation closely matches that of a different source segment"
    r"|translation fits the source segment"
    r"|translation may be another passage's"
    r"|translation length is far from usual here)"
)


def _audit_glossary(
    segment_id, source_text, target_text, document, direction, issues, lowercase_words=frozenset()
) -> None:
    grouped: dict[str, tuple[str, set[str]]] = {}
    names: set[str] = set()
    applicable = select_relevant_glossary_entries(
        source_text,
        document.relevant_glossary,
        direction,
    )
    for entry in applicable:
        # Generic lowercase terms are useful prompt hints but are too polysemous
        # for strict deterministic enforcement. A semantic auditor may still flag
        # a concrete misuse with source-grounded evidence.
        if is_suspicious_generic_candidate(
            entry, None if glossary_pair(direction).legacy else direction.source_language
        ):
            continue
        source_term, target_term = glossary_sides(entry, direction)
        key = unicodedata.normalize("NFKC", source_term).casefold()
        if key not in grouped:
            grouped[key] = (source_term, set())
        grouped[key][1].add(target_term)
        if entry.category in _NAME_CATEGORIES and _is_unambiguous_name(
            source_term, direction, lowercase_words
        ):
            names.add(key)

    # A passage with several approved terms whose translation has almost none
    # of them is, as a rule, the translation of another passage (阿Q正传 into
    # Japanese: six terms, one kept, in a paragraph the audit model accepted
    # three times). A model that merely ignores the glossary still keeps the
    # names.
    kept = sum(
        any(glossary_target_matches(target_text, term, direction.target_language) for term in target_terms)
        for _, target_terms in grouped.values()
    )
    if len(grouped) >= WRONG_PASSAGE_MIN_TERMS and kept <= 1:
        issues.append(
            _issue(
                segment_id,
                AuditCategory.MISTRANSLATION,
                AuditSeverity.HIGH,
                f"{WRONG_PASSAGE_BY_TERMS} {kept} of the {len(grouped)} approved terms in this passage",
                WRONG_PASSAGE_TERMS,
            )
        )

    for key, (source_term, target_terms) in grouped.items():
        if not any(
            glossary_target_matches(target_text, target_term, direction.target_language)
            for target_term in target_terms
        ):
            required = " | ".join(sorted(target_terms))
            # A missed name is repaired: readers notice a character or place
            # renamed mid-book, and a name is rarely ambiguous. Other entries
            # stay low, since their wording legitimately follows the context.
            # A multi-part transliterated name shortened to one part
            # ("Sherlock Holmes" as 福尔摩斯) is ordinary style: listed only.
            shortened = any(
                part and part in target_text
                for target_term in target_terms
                if "·" in target_term
                for part in target_term.split("·")
            )
            name = key in names and not shortened
            issues.append(
                AuditIssue(
                    segment_id=segment_id,
                    category=AuditCategory.GLOSSARY,
                    severity=AuditSeverity.MEDIUM if name else AuditSeverity.LOW,
                    message=f"approved glossary term was not preserved: {required}",
                    suggested_fix=(
                        f"Render {source_term} as {required}, the approved glossary name."
                        if name
                        else ""
                    ),
                )
            )


def _audit_internal_duplication(segment_id, source, target, issues) -> None:
    source_duplicates = _adjacent_duplication_count(source)
    target_duplicates = _adjacent_duplication_count(target)
    if target_duplicates > source_duplicates:
        issues.append(
            _issue(
                segment_id,
                AuditCategory.DUPLICATION,
                AuditSeverity.HIGH,
                "adjacent translated sentence is duplicated beyond source repetition",
            )
        )


def _adjacent_duplication_count(text: str) -> int:
    """Count repeated adjacent sentences without treating source repetition as a defect."""
    sentences = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item.strip()]
    normalized = [_normalize_prose(item) for item in sentences]
    # A sentence said again after one that ends with it counts too: a quotation
    # repeated after its lead-in ("she went on saying, 'Do cats eat bats? Do
    # cats eat bats?'") is one sentence longer in a language that keeps the
    # lead-in with the quotation than in one that ends the sentence before it.
    return sum(
        len(second) >= 6 and first.endswith(second)
        for first, second in zip(normalized, normalized[1:])
    )


def _audit_ai_style(segment_id, target, direction, issues) -> None:
    lowered = target.casefold()
    matches = [pattern for pattern in profile(direction.target_language).stock_phrases if pattern.casefold() in lowered]
    if matches:
        issues.append(_issue(segment_id, AuditCategory.AI_STYLE, AuditSeverity.LOW, f"formulaic model-like phrase detected: {matches[0]}"))


def _issue(segment_id, category, severity, message, code="") -> AuditIssue:
    return AuditIssue(segment_id=segment_id, category=category, severity=severity, message=message, code=code)


def whole_translation_is_wrong(issue: AuditIssue) -> bool:
    """Whether a pipeline finding says the translation is another passage's or
    not a translation at all. Decided by the finding's code; by its wording
    only for an audit written before findings had one."""
    if issue.source == "semantic" or issue.severity is not AuditSeverity.HIGH:
        return False
    if issue.code:
        return issue.code in WHOLE_TRANSLATION_CODES
    return WHOLE_TRANSLATION_WRONG.match(issue.message) is not None


def _normalize_prose(text: str) -> str:
    return re.sub(f"[^\\w{_PROSE_CHARS}]+", "", text.casefold())


def is_decorative_separator(text: str) -> bool:
    """Return whether text contains only decoration and protected inline markers."""
    visible = _INLINE_MARKER.sub("", text).strip()
    return bool(visible) and bool(_SEPARATOR.fullmatch(visible))
