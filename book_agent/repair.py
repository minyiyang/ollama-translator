"""Targeted repair selection, prompts, validation, and draft assembly."""

from __future__ import annotations

import hashlib
from enum import Enum
import math
import re
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from .audit import (
    AuditCategory,
    AuditIssue,
    AuditSeverity,
    DocumentAudit,
    audit_translated_document,
)
from .config import AppConfig
from .content_policy import (
    is_intentionally_preserved,
    repair_preserves_glossary,
    repair_preserves_numbers,
)
from .preprocessing import (
    PreprocessedDocument,
    PreprocessedSegment,
    select_relevant_glossary_entries,
)
from .ollama_client import estimate_request_tokens
from .quantities import compare_quantity_texts
from .styles import build_style_prompt, load_style_instruction
from .translation import (
    TranslatedDocument,
    TranslatedSegment,
    TranslationChunk,
    TranslationChunkPiece,
    TranslationIssue,
    TranslationValidation,
    format_relevant_glossary,
    validate_translation_output,
)


class RepairDisposition(str, Enum):
    REPAIRED = "repaired"
    ACCEPTED = "accepted"
    REVIEW = "review"


class SegmentRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    disposition: RepairDisposition
    original_translation: str
    repaired_translation: str
    issues: list[AuditIssue]
    attempts: int = Field(ge=0)
    message: str = ""


class RepairedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: TranslatedDocument
    repairs: list[SegmentRepair] = Field(default_factory=list)


class TranslationRepairReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_count: int = Field(ge=0)
    targeted_segment_count: int = Field(ge=0)
    repaired_segment_count: int = Field(ge=0)
    review_segment_ids: list[str] = Field(default_factory=list)


class RepairVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    segment_id: str = Field(min_length=1)
    passed: bool
    current_acceptable: bool = False
    message: str = Field(min_length=3)


class RepairVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verifications: list[RepairVerification]


class PairwiseRepairVerification(BaseModel):
    """Neutral A/B judgment returned by the independent verifier."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    segment_id: str = Field(min_length=1)
    a_acceptable: bool
    b_acceptable: bool
    winner: str = Field(pattern=r"^(a|b|tie|neither)$")
    message: str = Field(min_length=3)


class PairwiseRepairVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verifications: list[PairwiseRepairVerification]


class LocatedEdit(BaseModel):
    """One exact replacement proposed against immutable accepted target text."""

    model_config = ConfigDict(extra="forbid")
    old_span: str = Field(min_length=1)
    new_span: str
    reason: str = Field(min_length=3)


class LocatedRepairResult(BaseModel):
    """Bounded edit proposal applied only against immutable accepted text."""

    model_config = ConfigDict(extra="forbid")
    edits: list[LocatedEdit] = Field(min_length=1, max_length=3)


class RepairedValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    passed: bool
    remaining_issue_count: int = Field(ge=0)
    review_segment_ids: list[str] = Field(default_factory=list)
    remaining_defect_count: int = Field(default=0, ge=0)
    approval_required_count: int = Field(default=0, ge=0)
    defect_segment_ids: list[str] = Field(default_factory=list)
    approval_segment_ids: list[str] = Field(default_factory=list)


class RepairedDocumentValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    deterministic_audit: DocumentAudit
    semantic_verifications: list[RepairVerification] = Field(default_factory=list)
    passed: bool
    review_segment_ids: list[str] = Field(default_factory=list)
    defect_segment_ids: list[str] = Field(default_factory=list)
    approval_segment_ids: list[str] = Field(default_factory=list)


def apply_deterministic_repairs(
    text: str,
    issues: list[AuditIssue],
) -> tuple[str, list[str]]:
    """Apply only bounded, mechanically provable repairs requested by audit findings."""
    result = text
    applied: list[str] = []

    replaced = _apply_exact_glossary_replacements(result, issues)
    if replaced != result:
        result = replaced
        applied.append("exact_glossary_replacement")

    diagnosis = " ".join(
        [
            *(item.message for item in issues),
            *(item.suggested_fix for item in issues if item.suggested_fix),
        ]
    )
    if _feedback_requests_deduplication(diagnosis):
        deduplicated = _deduplicate_adjacent_marker_text(result)
        if deduplicated != result:
            result = deduplicated
            applied.append("adjacent_marker_deduplication")

    return result, applied


def apply_located_edits(text: str, edits: list[LocatedEdit]) -> str:
    """Apply unique, non-overlapping spans while preserving all outside text exactly."""
    located: list[tuple[int, int, str]] = []
    for edit in edits:
        starts = [match.start() for match in re.finditer(re.escape(edit.old_span), text)]
        if len(starts) != 1:
            raise ValueError("located edit old_span must occur exactly once")
        start = starts[0]
        located.append((start, start + len(edit.old_span), edit.new_span))
    located.sort()
    for previous, current in zip(located, located[1:]):
        if previous[1] > current[0]:
            raise ValueError("located edits must not overlap")
    cursor = 0
    pieces: list[str] = []
    for start, end, replacement in located:
        pieces.extend((text[cursor:start], replacement))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _feedback_requests_deduplication(feedback: str) -> bool:
    normalized = feedback.casefold()
    return any(
        term in normalized
        for term in ("duplicate", "duplicated", "redundant", "repetition", "重复", "多余")
    )


def _apply_exact_glossary_replacements(
    text: str,
    issues: list[AuditIssue],
) -> str:
    """Apply unambiguous quoted replace-all instructions from glossary findings."""
    result = text
    quote = r"['‘’\"]"
    patterns = (
        re.compile(
            rf"change all instances of\s+{quote}(?P<old>.+?){quote}\s+to\s+"
            rf"{quote}(?P<new>.+?){quote}",
            flags=re.IGNORECASE,
        ),
        re.compile(
            rf"replace all instances of\s+{quote}(?P<old>.+?){quote}\s+with\s+"
            rf"{quote}(?P<new>.+?){quote}",
            flags=re.IGNORECASE,
        ),
    )
    for issue in issues:
        if issue.category is not AuditCategory.GLOSSARY or not issue.suggested_fix:
            continue
        for pattern in patterns:
            match = pattern.search(issue.suggested_fix)
            if match and match.group("old") in result:
                result = result.replace(match.group("old"), match.group("new"))
                break
    return result


def _deduplicate_adjacent_marker_text(text: str) -> str:
    """Remove an exact word duplicated immediately outside one inline marker."""
    marker = re.compile(r"(<I\d+>)(.*?)(</I\d+>)", flags=re.DOTALL)
    result = text
    while True:
        changed = False
        for match in marker.finditer(result):
            _, emphasized, _ = match.groups()
            if not emphasized or "<" in emphasized or ">" in emphasized:
                continue
            start, end = match.span()
            prefix, suffix = result[:start], result[end:]
            escaped = re.escape(emphasized)
            prefix_match = re.search(rf"{escaped}\s*$", prefix)
            if prefix_match:
                result = prefix[:prefix_match.start()] + result[start:]
                changed = True
                break
            suffix_match = re.match(rf"^\s*{escaped}", suffix)
            if suffix_match:
                result = result[:end] + suffix[suffix_match.end():]
                changed = True
                break
        if not changed:
            return result


def select_repair_targets(
    audits: list[DocumentAudit],
    minimum_severity: str,
) -> dict[str, dict[str, list[AuditIssue]]]:
    """Group repairable issues by document and segment at or above a severity threshold."""
    try:
        threshold = AuditSeverity(minimum_severity).rank
    except ValueError as error:
        raise ValueError("minimum_severity must be low, medium, or high") from error
    grouped: dict[str, dict[str, list[AuditIssue]]] = {}
    for audit in audits:
        for issue in audit.issues:
            if issue.severity.rank < threshold or issue.segment_id == "document":
                continue
            grouped.setdefault(audit.document_id, {}).setdefault(issue.segment_id, []).append(issue)
    return grouped


def requires_full_segment_translation(issues: list[AuditIssue]) -> bool:
    """Identify a source safety copy that must be translated, not locally edited."""
    return any(
        item.source == "translation-deferred"
        or (
            item.category is AuditCategory.UNTRANSLATED
            and item.severity is AuditSeverity.HIGH
            and item.message
            in {
                "translation contains no Chinese text",
                "translation contains no English text",
                "translation is identical to source",
            }
        )
        for item in issues
    )


def build_repair_prompt(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    segment_id: str,
    issues: list[AuditIssue],
    config: AppConfig,
) -> str:
    """Build one bounded repair prompt with immediate context and unchanged style policy."""
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    target_by_id = {item.segment_id: item.translated_text for item in translated.segments}
    if segment_id not in source_by_id or segment_id not in target_by_id:
        raise ValueError(f"unknown repair segment: {segment_id}")
    ordered = [item.segment_id for item in source.segments]
    position = ordered.index(segment_id)
    context = []
    for neighbor in ordered[max(0, position - 1): position + 2]:
        role = "REPAIR" if neighbor == segment_id else "CONTEXT ONLY"
        context.append(
            f"[{neighbor}] {role}\nSOURCE: {source_by_id[neighbor]}\nCURRENT: {target_by_id[neighbor]}"
        )
    diagnostics = "\n".join(_format_repair_diagnostic(item) for item in issues)
    instruction = load_style_instruction(
        config.translation.style, config.translation.custom_style_file
    )
    style = build_style_prompt(
        instruction,
        config.translation.direction,
        de_ai_enabled=config.translation.de_ai_enabled,
        de_ai_strength=config.translation.de_ai_strength,
    )
    glossary = format_relevant_glossary(
        select_relevant_glossary_entries(
            source_by_id[segment_id],
            source.relevant_glossary,
            config.translation.direction,
        ),
        config.translation.direction,
    )
    related = retrieve_related_source_context(source, segment_id)
    related_text = "\n".join(
        f"[RELATED SOURCE ONLY]\nSOURCE: {item}" for item in related
    )
    context_text = "\n\n".join(context)
    if related_text:
        context_text += f"\n\n{related_text}"
    if requires_full_segment_translation(issues):
        task = (
            f"Translate the complete SOURCE for segment {segment_id}. CURRENT is only the "
            "source-language safety copy retained after first-draft retries were exhausted; "
            "it is not an accepted translation and must be replaced completely. Preserve "
            "every source detail, the surrounding voice, all applicable glossary terms, and "
            "every <I...> marker exactly once. Move markers with the natural target-language "
            "words carrying the same emphasis. Neighbor and related source passages are "
            "context only. Do not copy source-language prose into the result."
        )
    else:
        task = (
            f"Repair only segment {segment_id}. Correct the listed defects while preserving "
            "every other source detail and the surrounding voice. Do not broadly rewrite or "
            "optimize the passage. Neighbor and related source passages are context only. "
            "Treat audit suggestions as diagnoses, not text that must be copied. Preserve "
            "the source's grammatical subject and atomic subject-predicate facts. Before "
            "wording the edit, distinguish action from state and identify any location, "
            "direction, distance, spatial relationship, quantity, negation, and temporal "
            "relation. If the source asserts only a state, position, separation, or "
            "measurement, keep the correction stative: do not introduce motion verbs such "
            "as walking, crossing, or arriving, and do not invent an opposite side, endpoint, "
            "or other landmark. Preserve the number and unit without changing what they "
            "measure. Preserve every <I...> marker exactly once, but move it with the "
            "target-language words it "
            "formats when needed. Example: SOURCE `when <I000>I</I000> find`, broken "
            "CURRENT `当我<I000>我</I000>发现`, corrected `当<I000>我</I000>发现`."
        )
    return (
        f"{style}\n\n{task}\n\n"
        f"Audit findings:\n{diagnostics}\n\nRelevant glossary:\n{glossary or '(none)'}\n\n"
        f"Context:\n{context_text}\n\n"
        f"Output exactly <{segment_id}>corrected translation</{segment_id}> and nothing else."
    )


def build_located_repair_prompt(
    source: PreprocessedDocument,
    translated: TranslatedDocument,
    segment_id: str,
    issues: list[AuditIssue],
    config: AppConfig,
) -> str:
    """Request exact target spans while keeping accepted surrounding text immutable."""
    if requires_full_segment_translation(issues):
        raise ValueError("deferred translations require complete segment translation")
    base = build_repair_prompt(source, translated, segment_id, issues, config)
    contract = base.rsplit("Output exactly", maxsplit=1)[0]
    return (
        contract
        + "Propose one to three minimal located edits. Each old_span must be copied "
        "byte-for-byte from CURRENT and must occur there exactly once. Change only text "
        "needed for a concrete source-grounded defect. Do not include unchanged context "
        "inside a span, do not rewrite the full segment, and do not edit neighbor segments. "
        "Return structured edits only."
    )


def validate_repair_output(
    output: str,
    source_text: str,
    segment_id: str,
    original_translation: str,
    config: AppConfig,
    relevant_glossary=None,
    trigger_issues: list[AuditIssue] | None = None,
) -> tuple[str, TranslationValidation]:
    """Validate one repaired marker and require an actual bounded change."""
    chunk = TranslationChunk(
        chunk_id=f"repair-{segment_id}",
        document_id="repair",
        document_order=0,
        pieces=[
            TranslationChunkPiece(
                reference_id=segment_id,
                segment_id=segment_id,
                part_number=1,
                source_text=source_text,
            )
        ],
        estimated_source_tokens=0,
    )
    translations, validation = validate_translation_output(
        output,
        chunk,
        config.translation.direction,
        relevant_glossary=relevant_glossary,
    )
    repaired = translations.get(segment_id, "")
    issues = list(validation.issues)
    full_segment_recovery = requires_full_segment_translation(trigger_issues or [])
    if (
        validation.passed
        and config.audit.quantity.enabled
        and config.audit.quantity.verify_repairs
        # A deferred source-language safety copy is not an accepted translation
        # and cannot serve as the numeric baseline for a cross-language rewrite.
        # The normal post-repair quantity audit will adjudicate the completed
        # target-language text without discarding the recovery here.
        and not full_segment_recovery
        and not _typed_repair_preserves_quantities(
            source_text, original_translation, repaired
        )
    ):
        issues.append(
            TranslationIssue(
                code="quantity_integrity",
                reference_id=segment_id,
                message="repair introduced or changed a provable typed quantity fact",
            )
        )
    elif validation.passed and not config.audit.quantity.enabled and not repair_preserves_numbers(
        source_text, original_translation, repaired
    ):
        issues.append(
            TranslationIssue(
                code="number_integrity",
                reference_id=segment_id,
                message="repair changed digit-bearing facts from the accepted translation",
            )
        )
    if validation.passed and not repair_preserves_glossary(
        source_text,
        original_translation,
        repaired,
        config.translation.direction,
        relevant_glossary or [],
    ):
        issues.append(
            TranslationIssue(
                code="glossary_integrity",
                reference_id=segment_id,
                message="repair changed an applicable approved term from accepted text",
            )
        )
    if (
        not issues
        and repaired.strip() == original_translation.strip()
        and not is_intentionally_preserved(
            source_text,
            repaired,
            config.translation.direction,
            relevant_glossary or [],
        )
    ):
        issues.append(
            TranslationIssue(
                code="repair_unchanged",
                reference_id=segment_id,
                message="repair output is identical to the flagged translation",
            )
        )
    if not issues and trigger_issues:
        unresolved = _unresolved_deterministic_repair_triggers(
            source_text,
            repaired,
            segment_id,
            config,
            relevant_glossary or [],
            trigger_issues,
        )
        if unresolved:
            issues.append(
                TranslationIssue(
                    code="repair_trigger_unresolved",
                    reference_id=segment_id,
                    message=(
                        "repair left its deterministic trigger unresolved: "
                        + ", ".join(sorted(item.value for item in unresolved))
                    ),
                )
            )
    return repaired, TranslationValidation(passed=not issues, issues=issues)


def _unresolved_deterministic_repair_triggers(
    source_text: str,
    candidate_text: str,
    segment_id: str,
    config: AppConfig,
    relevant_glossary,
    trigger_issues: list[AuditIssue],
) -> set[AuditCategory]:
    """Re-run provable trigger categories against a proposed repair."""
    checkable = {
        AuditCategory.EMPTY,
        AuditCategory.STRUCTURE,
        AuditCategory.UNTRANSLATED,
        AuditCategory.DUPLICATION,
        AuditCategory.PUNCTUATION,
    }
    triggered = {item.category for item in trigger_issues} & checkable
    if not triggered:
        return set()
    source = PreprocessedDocument(
        order=0,
        manifest_id="repair-trigger-check",
        archive_path="repair-trigger-check",
        source_sha256="repair-trigger-check",
        segments=[
            PreprocessedSegment(
                segment_id=segment_id,
                original_text=source_text,
                processed_text=source_text,
            )
        ],
        relevant_glossary=relevant_glossary,
    )
    translated = TranslatedDocument(
        order=0,
        manifest_id=source.manifest_id,
        archive_path=source.archive_path,
        direction=config.translation.direction,
        style=config.translation.style,
        segments=[
            TranslatedSegment(
                segment_id=segment_id,
                source_text=source_text,
                translated_text=candidate_text,
            )
        ],
    )
    audit = audit_translated_document(source, translated, config.audit)
    return {
        item.category
        for item in audit.issues
        if item.category in triggered
        and item.severity.rank >= AuditSeverity.MEDIUM.rank
    }


def _format_repair_diagnostic(issue: AuditIssue) -> str:
    line = f"- {issue.category.value}/{issue.severity.value}: {issue.message}"
    if issue.suggested_fix:
        line += f" Suggested fix: {issue.suggested_fix}"
    for mismatch in issue.quantity_mismatches:
        source = mismatch.source_fact
        target = mismatch.target_fact
        line += (
            f"\n  Structured quantity defect: {mismatch.kind.value}; "
            f"source={source.quote if source else '(none)'}; "
            f"translation={target.quote if target else '(none)'}; "
            f"relation={source.relation if source else ''}; "
            f"unit={source.unit if source else ''}."
        )
    return line


def _typed_repair_preserves_quantities(
    source_text: str,
    accepted_text: str,
    candidate_text: str,
) -> bool:
    """Reject only a new or altered provable mismatch; defer ambiguity to verification."""
    accepted = compare_quantity_texts(source_text, accepted_text)
    candidate = compare_quantity_texts(source_text, candidate_text)
    if candidate.status != "mismatch":
        return True
    if accepted.status != "mismatch":
        return False
    accepted_signature = {
        (
            item.kind.value,
            item.source_fact.model_dump_json() if item.source_fact else "",
            item.target_fact.model_dump_json() if item.target_fact else "",
        )
        for item in accepted.mismatches
    }
    candidate_signature = {
        (
            item.kind.value,
            item.source_fact.model_dump_json() if item.source_fact else "",
            item.target_fact.model_dump_json() if item.target_fact else "",
        )
        for item in candidate.mismatches
    }
    return candidate_signature == accepted_signature


def apply_segment_repairs(
    document: TranslatedDocument,
    repairs: list[SegmentRepair],
) -> TranslatedDocument:
    """Apply only successful segment repairs and preserve document/segment order."""
    replacements = {
        item.segment_id: item.repaired_translation
        for item in repairs
        if item.disposition is RepairDisposition.REPAIRED
    }
    known = {item.segment_id for item in document.segments}
    unknown = sorted(set(replacements) - known)
    if unknown:
        raise ValueError(f"repair references unknown segments: {unknown}")
    return document.model_copy(
        update={
            "segments": [
                TranslatedSegment(
                    segment_id=item.segment_id,
                    source_text=item.source_text,
                    translated_text=replacements.get(item.segment_id, item.translated_text),
                )
                for item in document.segments
            ]
        }
    )


def build_repair_verification_prompt(
    source: PreprocessedDocument,
    repaired: RepairedDocument,
    segment_ids: list[str],
    *,
    candidate_first: bool = False,
) -> str:
    """Build a neutral, scoped A/B semantic verification prompt."""
    source_by_id = {item.segment_id: item.processed_text for item in source.segments}
    repaired_by_id = {
        item.segment_id: item.translated_text for item in repaired.document.segments
    }
    repair_by_id = {item.segment_id: item for item in repaired.repairs}
    unknown = [
        item
        for item in segment_ids
        if item not in source_by_id or item not in repaired_by_id or item not in repair_by_id
    ]
    if unknown:
        raise ValueError(f"unknown repaired verification segments: {unknown}")
    ordered_ids = [item.segment_id for item in source.segments]
    blocks = []
    for segment_id in segment_ids:
        repair = repair_by_id[segment_id]
        position = ordered_ids.index(segment_id)
        before_source = source_by_id[ordered_ids[position - 1]] if position else "(none)"
        after_source = (
            source_by_id[ordered_ids[position + 1]]
            if position + 1 < len(ordered_ids)
            else "(none)"
        )
        related = retrieve_related_source_context(source, segment_id)
        related_text = "\n".join(f"RELATED SOURCE: {item}" for item in related)
        current_text = repair.original_translation
        candidate_text = repaired_by_id[segment_id]
        translation_a = candidate_text if candidate_first else current_text
        translation_b = current_text if candidate_first else candidate_text
        blocks.append(
            f"[{segment_id}]\nPRECEDING SOURCE: {before_source}\n"
            f"SOURCE: {source_by_id[segment_id]}\nFOLLOWING SOURCE: {after_source}\n"
            f"TRANSLATION A: {translation_a}\n"
            f"TRANSLATION B: {translation_b}\n"
            f"{related_text}"
        )
    glossary = format_relevant_glossary(
        select_relevant_glossary_entries(
            "\n".join(source_by_id[item] for item in segment_ids),
            source.relevant_glossary,
            repaired.document.direction,
        ),
        repaired.document.direction,
    )
    return (
        "Independently compare TRANSLATION A and TRANSLATION B against SOURCE and its "
        "source context. Their provenance and order are intentionally hidden. Do not rely "
        "on any prior diagnosis and do not rank stylistic preferences. Return exactly one "
        "verification for every allowed ID. Set a_acceptable and b_acceptable independently. "
        "Judge absolute publication readiness before choosing a winner. A translation that "
        "is better than the alternative but still contains any concrete semantic, grammatical, "
        "attachment, terminology, or conspicuous translationese defect is unacceptable. Never "
        "label a lesser evil acceptable merely to select a winner; use winner=neither when both "
        "translations remain defective. "
        "Use winner=a or winner=b only when that translation is materially more faithful; "
        "use tie when both are acceptable without a concrete quality difference; use "
        "neither when both are unacceptable. An acceptable translation is faithful, "
        "fluent, contextually valid, and has no concrete "
        "omission, addition, mistranslation, terminology error, duplication, or unnatural "
        "model-like prose. Compare atomic subject-predicate facts explicitly, including "
        "action versus state and every location, direction, distance, spatial relation, "
        "quantity, negation, and temporal relation. A state, position, separation, or "
        "measurement must not become motion or another action. Reject a spatial phrase "
        "whose endpoint, landmark, or reference object is invented, unclear in the target "
        "grammar, or merely a plausible clarification rather than source-grounded. The "
        "number and unit must describe the same relation as in SOURCE. It need not be "
        "stylistically superior to a translation that is "
        "also correct. Recheck the complete preferred translation after comparing the pair; "
        "one repaired phrase does not excuse a separate remaining defect elsewhere in the same "
        "segment. The message must state the decisive concrete evidence. Each "
        "message must be one "
        "sentence of at most 40 words. Do not score or rewrite. Do not quote or restate "
        "the passages or show your analysis. "
        "A short source-owned italicized fictional name or coined term may remain in "
        "Latin script or be consistently transliterated when no approved glossary "
        "rendering exists. Natural target wording may make an elliptical purpose explicit "
        "without adding a new factual proposition. Apply glossary entries by sense: an "
        "adjectival target form is valid when the source occurrence is not the glossary's "
        "named-person or demonym sense. "
        "If evidence is insufficient for either translation, mark that translation "
        f"unacceptable. Relevant glossary:\n{glossary or '(none)'}\n"
        f"Allowed IDs: {', '.join(segment_ids)}\n\n" + "\n\n".join(blocks)
    )


def repair_candidate_first(segment_ids: list[str]) -> bool:
    """Choose a stable, balanced A/B order without exposing translation provenance."""
    digest = hashlib.sha256("\n".join(segment_ids).encode("utf-8")).digest()
    return bool(digest[0] & 1)


def validate_pairwise_repair_verification_scope(
    result: PairwiseRepairVerificationResult,
    expected_ids: list[str],
) -> PairwiseRepairVerificationResult:
    """Validate pairwise scope and internal winner/acceptability consistency."""
    actual = [item.segment_id for item in result.verifications]
    if actual != expected_ids:
        raise ValueError(
            "pairwise repair verification IDs must exactly match the allowed IDs"
        )
    for item in result.verifications:
        if item.winner == "a" and not item.a_acceptable:
            raise ValueError("winner a must be acceptable")
        if item.winner == "b" and not item.b_acceptable:
            raise ValueError("winner b must be acceptable")
        if item.winner == "tie" and not (item.a_acceptable and item.b_acceptable):
            raise ValueError("tie requires both translations to be acceptable")
        if item.winner == "neither" and (item.a_acceptable or item.b_acceptable):
            raise ValueError("neither requires both translations to be unacceptable")
    return result


def map_pairwise_repair_verification(
    result: PairwiseRepairVerificationResult,
    *,
    candidate_first: bool,
) -> RepairVerificationResult:
    """Map neutral A/B judgments back to the pipeline's repair terminology."""
    return RepairVerificationResult(
        verifications=[
            RepairVerification(
                segment_id=item.segment_id,
                passed=(item.a_acceptable if candidate_first else item.b_acceptable),
                current_acceptable=(
                    item.b_acceptable if candidate_first else item.a_acceptable
                ),
                message=item.message,
            )
            for item in result.verifications
        ]
    )


def build_repair_verification_batches(
    source: PreprocessedDocument,
    repaired: RepairedDocument,
    segment_ids: list[str],
    *,
    max_request_context: int,
    context_multiplier: float = 2.0,
) -> list[list[str]]:
    """Split verifier IDs so every complete structured request fits one context."""
    if max_request_context <= 0:
        raise ValueError("max_request_context must be positive")
    if context_multiplier <= 0:
        raise ValueError("context_multiplier must be positive")

    def fits(ids: list[str]) -> bool:
        prompt = build_repair_verification_prompt(
            source,
            repaired,
            ids,
            candidate_first=repair_candidate_first(ids),
        )
        request_tokens = estimate_request_tokens(
            prompt, PairwiseRepairVerificationResult.model_json_schema()
        )
        required = math.ceil(request_tokens * context_multiplier) + 4_096
        return required <= max_request_context

    batches: list[list[str]] = []
    current: list[str] = []
    for segment_id in segment_ids:
        proposed = [*current, segment_id]
        if current and not fits(proposed):
            batches.append(current)
            current = []
            proposed = [segment_id]
        if not fits(proposed):
            raise ValueError(
                f"repaired verification segment exceeds request context: {segment_id}"
            )
        current.append(segment_id)
    if current:
        batches.append(current)
    return batches


def repair_verification_output_token_limit(expected_count: int) -> int:
    """Leave enough room for complete per-segment structured decisions."""
    if expected_count <= 0:
        raise ValueError("expected verification count must be positive")
    return max(1_024, min(4_096, 512 + (512 * expected_count)))


def retrieve_related_source_context(
    document: PreprocessedDocument,
    segment_id: str,
    *,
    max_passages: int = 2,
    max_tokens: int = 400,
) -> list[str]:
    """Retrieve bounded same-document context by deterministic rare-token overlap."""
    if max_passages < 0 or max_tokens < 0:
        raise ValueError("context limits must be nonnegative")
    ordered = [item.segment_id for item in document.segments]
    if segment_id not in ordered:
        raise ValueError(f"unknown related-context segment: {segment_id}")
    position = ordered.index(segment_id)
    excluded = {segment_id}
    if position:
        excluded.add(ordered[position - 1])
    if position + 1 < len(ordered):
        excluded.add(ordered[position + 1])
    token_sets = {
        item.segment_id: set(_lexical_tokens(item.processed_text))
        for item in document.segments
    }
    frequencies = Counter(token for tokens in token_sets.values() for token in tokens)
    query = token_sets[segment_id]
    ranked: list[tuple[float, int, str]] = []
    for index, item in enumerate(document.segments):
        if item.segment_id in excluded:
            continue
        overlap = query & token_sets[item.segment_id]
        score = sum(1.0 / frequencies[token] for token in overlap)
        if score:
            ranked.append((-score, index, item.processed_text))
    selected: list[str] = []
    used = 0
    for _, _, text in sorted(ranked):
        tokens = max(1, len(_lexical_tokens(text)))
        if len(selected) >= max_passages or used + tokens > max_tokens:
            continue
        selected.append(text)
        used += tokens
    return selected


def _lexical_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z]{3,}|[\u3400-\u9fff]{2,}", text.casefold())
        if token not in {"the", "and", "that", "with", "this", "from", "was", "were"}
    ]


def validate_repair_verification_scope(
    result: RepairVerificationResult,
    expected_ids: list[str],
) -> RepairVerificationResult:
    """Require exactly one complete verifier decision for every repaired segment."""
    actual = [item.segment_id for item in result.verifications]
    if actual != expected_ids:
        raise ValueError(
            f"repair verification IDs must exactly match {expected_ids}; received {actual}"
        )
    return result
