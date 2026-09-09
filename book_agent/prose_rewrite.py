"""Source-grounded selection, prompting, and validation for literary reprose."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .audit import AuditCategory, AuditIssue, AuditSeverity
from .config import AppConfig
from .content_policy import SegmentKind, classify_segment, visible_segment_text
from .preprocessing import PreprocessedDocument, select_relevant_glossary_entries
from .repair import (
    RepairDisposition,
    RepairedDocument,
    SegmentRepair,
    apply_segment_repairs,
    validate_repair_output,
)
from .styles import build_style_prompt, load_style_instruction
from .translation import TranslatedDocument, format_relevant_glossary


class ProseRewriteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment_id: str = Field(min_length=1)
    action: Literal["keep", "rewrite"]
    rewritten_text: str = ""
    reason: str = Field(min_length=3, max_length=300)


class ProseRewriteBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[ProseRewriteDecision]


class ProseRewriteReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_count: int = Field(ge=0)
    candidate_segment_count: int = Field(ge=0)
    proposed_rewrite_count: int = Field(ge=0)
    applied_rewrite_count: int = Field(ge=0)
    rejected_rewrite_count: int = Field(ge=0)
    changed_segment_ids: list[str] = Field(default_factory=list)
    rejected_segment_ids: list[str] = Field(default_factory=list)
    decision_failure_segment_ids: list[str] = Field(default_factory=list)


_TRANSLATIONESE_PATTERNS = (
    re.compile(r"(?:进行(?:了|着)?|予以|加以)(?:[^，。！？；]{0,18})(?:处理|考虑|观察|检查|分析|行动)"),
    re.compile(r"在[^，。！？；]{2,24}的情况下"),
    re.compile(r"被[^，。！？；]{1,24}所"),
    re.compile(r"对于[^，。！？；]{1,20}而言"),
    re.compile(r"从某种意义上(?:来说)?"),
    re.compile(r"(?:事实上|值得注意的是|总而言之|这意味着)"),
    # High-yield English-shaped constructions observed across production books.
    # These only select a segment for source-grounded reprose; they never mutate
    # text deterministically, so a legitimate technical use remains safe.
    re.compile(
        r"(?:我|你|他|她|它|这|那|一切)"
        r"[^，。！？；]{0,8}全(?:都)?(?:是)?关于"
    ),
    re.compile(r"(?:并非|不是|没有)(?:原本)?(?:计划|打算)被"),
    re.compile(r"(?:标本|样本|工具|器具|设备|尸体|岩石)们"),
    re.compile(r"物理上(?:地)?[^，。！？；]{0,8}(?:闭|紧|抓|按|压|拉|推)"),
)
_NEGATION_PATTERNS = (
    re.compile(r"不|没(?:有)?|未(?:曾)?|无(?:法|须|需|人|物)?|并非|绝非|休想"),
    re.compile(r"\b(?:not|never|no|without|neither|nor)\b", re.I),
)
_MODAL_PATTERNS = (
    re.compile(r"必须|不得|应该|应当|可能|也许|或许|能够|可以|无法|不可能"),
    re.compile(r"\b(?:must|shall|should|may|might|could|can(?:not)?|cannot)\b", re.I),
)
_COMPARATIVE_PATTERNS = (
    re.compile(r"(?:比|更|较|最|不如|少于|多于|至少|至多|以上|以下)"),
    re.compile(r"\b(?:more|less|fewer|than|least|most|better|worse|larger|smaller)\b", re.I),
)
_SPATIAL_PATTERNS = (
    re.compile(r"(?:里面|内部|外面|外部|上方|下方|前方|后方|之间|之中|穿过|越过|进入|离开|朝向|远离|靠近|在[^，。！？；]{0,16}(?:中|里|上|下))"),
    re.compile(r"\b(?:inside|outside|above|below|behind|before|between|through|across|into|out of|towards?|away from|near)\b", re.I),
)
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def prose_rewrite_candidate_reasons(
    source_text: str,
    target_text: str,
    *,
    target_characters: int,
) -> list[str]:
    """Return generic, source-independent reasons to spend a reprose call."""
    del source_text
    visible = visible_segment_text(target_text).strip()
    reasons: list[str] = []
    if len(visible) >= target_characters:
        reasons.append("long-prose")
    if sum(visible.count(mark) for mark in "，；：——") >= 5:
        reasons.append("clause-density")
    if any(pattern.search(visible) for pattern in _TRANSLATIONESE_PATTERNS):
        reasons.append("translationese")
    if re.search(r"([^，。！？；]{1,8})\1{2,}", visible):
        reasons.append("repetition")
    if re.search(r"[\"']", visible) and not re.search(r"[“”‘’]", visible):
        reasons.append("typography")
    return reasons


def protected_rewrite_changes(
    source_text: str,
    original_text: str,
    candidate_text: str,
) -> list[str]:
    """Detect fact-bearing signatures a stylistic rewrite is not allowed to change."""
    del source_text  # Reserved for richer source-to-target mappings; target delta is conservative.
    changes: list[str] = []
    signature_groups = {
        "negation/polarity": _NEGATION_PATTERNS,
        "modality": _MODAL_PATTERNS,
        "comparison": _COMPARATIVE_PATTERNS,
        "spatial relation": _SPATIAL_PATTERNS,
    }
    for label, patterns in signature_groups.items():
        before = tuple(len(pattern.findall(original_text)) for pattern in patterns)
        after = tuple(len(pattern.findall(candidate_text)) for pattern in patterns)
        if before != after:
            changes.append(label)

    original_latin = Counter(token.casefold() for token in _LATIN_TOKEN.findall(original_text))
    candidate_latin = Counter(token.casefold() for token in _LATIN_TOKEN.findall(candidate_text))
    if original_latin != candidate_latin:
        changes.append("Latin name/term")
    if original_text.count("“") != candidate_text.count("“") or original_text.count(
        "”"
    ) != candidate_text.count("”"):
        changes.append("quotation boundary")
    return changes


def select_prose_rewrite_candidates(
    source: PreprocessedDocument,
    repaired: RepairedDocument,
    config: AppConfig,
) -> list[str]:
    """Select substantial prose in document order; zero cap means all candidates."""
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    selected: list[str] = []
    prose_index = 0
    for segment in repaired.document.segments:
        source_text = source_by_id.get(segment.segment_id)
        if source_text is None:
            continue
        if len(visible_segment_text(segment.translated_text).strip()) < (
            config.reprose.min_target_characters
        ):
            continue
        kind = classify_segment(
            source_text,
            segment.translated_text,
            repaired.document.direction,
            source.relevant_glossary,
        )
        if kind is SegmentKind.PROSE:
            prose_index += 1
            if config.reprose.candidate_mode == "risk-filtered":
                reasons = prose_rewrite_candidate_reasons(
                    source_text,
                    segment.translated_text,
                    target_characters=config.reprose.long_target_characters,
                )
                sampled = bool(
                    config.reprose.candidate_sample_every
                    and prose_index % config.reprose.candidate_sample_every == 0
                )
                if not reasons and not sampled:
                    continue
            selected.append(segment.segment_id)
        cap = config.reprose.max_candidates_per_document
        if cap and len(selected) >= cap:
            break
    return selected


def build_prose_rewrite_prompt(
    source: PreprocessedDocument,
    document: TranslatedDocument,
    segment_ids: list[str],
    config: AppConfig,
    *,
    rejection_feedback: str = "",
) -> str:
    """Build a conservative batch prompt with read-only adjacent context."""
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    target_by_id = {item.segment_id: item.translated_text for item in document.segments}
    ordered = [item.segment_id for item in source.segments]
    unknown = [item for item in segment_ids if item not in source_by_id or item not in target_by_id]
    if unknown:
        raise ValueError(f"unknown reprose segments: {unknown}")
    payload = []
    for segment_id in segment_ids:
        position = ordered.index(segment_id)
        relevant = select_relevant_glossary_entries(
            source_by_id[segment_id],
            source.relevant_glossary,
            document.direction,
        )
        payload.append(
            {
                "segment_id": segment_id,
                "previous_source_context_read_only": (
                    source_by_id[ordered[position - 1]] if position else "(none)"
                ),
                "source": source_by_id[segment_id],
                "next_source_context_read_only": (
                    source_by_id[ordered[position + 1]]
                    if position + 1 < len(ordered)
                    else "(none)"
                ),
                "previous_translation_context_read_only": (
                    target_by_id[ordered[position - 1]] if position else "(none)"
                ),
                "current_translation": target_by_id[segment_id],
                "next_translation_context_read_only": (
                    target_by_id[ordered[position + 1]]
                    if position + 1 < len(ordered)
                    else "(none)"
                ),
                "applicable_glossary": (
                    format_relevant_glossary(relevant, document.direction) or "(none)"
                ),
            }
        )
    style = build_style_prompt(
        load_style_instruction(
            config.translation.style,
            config.translation.custom_style_file,
        ),
        document.direction,
        de_ai_enabled=config.translation.de_ai_enabled,
        de_ai_strength=config.translation.de_ai_strength,
    )
    retry = ""
    if rejection_feedback:
        retry = (
            "\n\nThe previous response was rejected: "
            + rejection_feedback
            + ". Return every supplied ID exactly once and obey the output contract."
        )
    return (
        f"{style}\n\n"
        "You are the conservative final prose editor for a published speculative-fiction "
        "translation. Judge each CURRENT TRANSLATION independently. Choose rewrite only "
        "for a concrete source-grounded defect: translationese, broken Chinese syntax, "
        "bad modifier attachment, awkward passives, duplicated meaning, unnatural dialogue, "
        "damaged rhythm, semantic error, or terminology used in the wrong sense. Choose keep "
        "when the passage already reads as competent native literary Chinese; mere preference "
        "is not enough. Never embellish, summarize, sanitize, intensify, or introduce facts. "
        "Preserve every proper name, technical term, quantity, relationship, paragraph "
        "boundary, and <I000>-style inline marker. Context fields are read-only and must not "
        "be merged into the target. For keep, rewritten_text must be empty. For rewrite, "
        "return the complete replacement for only that segment. Return exactly one decision "
        "per supplied segment_id, in the same order. The reason must identify the concrete "
        "defect or say why the existing prose should remain unchanged."
        f"{retry}\n\nITEMS:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def validate_prose_rewrite_batch(
    result: ProseRewriteBatch,
    expected_ids: list[str],
) -> ProseRewriteBatch:
    """Require complete ordered scope and internally consistent keep decisions."""
    actual = [item.segment_id for item in result.decisions]
    if actual != expected_ids:
        raise ValueError(
            f"reprose decision IDs must exactly match {expected_ids}; received {actual}"
        )
    for decision in result.decisions:
        if decision.action == "keep" and decision.rewritten_text:
            raise ValueError(f"keep decision returned rewritten text: {decision.segment_id}")
        if decision.action == "rewrite" and not decision.rewritten_text.strip():
            raise ValueError(f"rewrite decision is empty: {decision.segment_id}")
    return result


def apply_validated_prose_decision(
    source: PreprocessedDocument,
    repaired: RepairedDocument,
    decision: ProseRewriteDecision,
    config: AppConfig,
) -> tuple[RepairedDocument, bool, str]:
    """Apply one safe rewrite or retain the exact last-known-good translation."""
    if decision.action == "keep":
        return repaired, False, ""
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    target_by_id = {
        item.segment_id: item.translated_text for item in repaired.document.segments
    }
    source_text = source_by_id[decision.segment_id]
    original = target_by_id[decision.segment_id]
    candidate, validation = validate_repair_output(
        f"<{decision.segment_id}>{decision.rewritten_text}</{decision.segment_id}>",
        source_text,
        decision.segment_id,
        original,
        config,
        source.relevant_glossary,
    )
    if not validation.passed:
        return (
            repaired,
            False,
            "; ".join(item.message for item in validation.issues),
        )
    if config.reprose.preserve_semantic_signatures:
        protected_changes = protected_rewrite_changes(
            source_text,
            original,
            candidate,
        )
        if protected_changes:
            return (
                repaired,
                False,
                "prose rewrite changed protected semantic signatures: "
                + ", ".join(protected_changes),
            )
    issue = AuditIssue(
        segment_id=decision.segment_id,
        category=AuditCategory.NATURALNESS,
        severity=AuditSeverity.MEDIUM,
        message=f"Prose rewrite proposed: {decision.reason}",
        source="reprose",
    )
    prose_repair = SegmentRepair(
        segment_id=decision.segment_id,
        disposition=RepairDisposition.REPAIRED,
        original_translation=original,
        repaired_translation=candidate,
        issues=[issue],
        attempts=1,
        message="pending independent post-reprose review",
    )
    existing = [
        item for item in repaired.repairs if item.segment_id != decision.segment_id
    ]
    rewritten_document = apply_segment_repairs(repaired.document, [prose_repair])
    return (
        RepairedDocument(
            document=rewritten_document,
            repairs=[*existing, prose_repair],
        ),
        True,
        "",
    )
