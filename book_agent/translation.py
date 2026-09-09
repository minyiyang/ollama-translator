"""Translation chunking, prompt assembly, parsing, and first-pass validation."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from .config import AppConfig
from .content_policy import classify_segment, should_run_language_check
from .glossary import estimate_tokens, split_text_to_budget
from .languages import Language, TranslationDirection
from .preprocessing import PreprocessedDocument
from .schemas import (
    GlossaryEntry,
    is_preservable_technical_identifier,
    normalize_term,
)
from .styles import build_style_prompt, load_style_instruction


class TranslationOutputError(ValueError):
    """Model translation output violates the protected marker contract."""


@dataclass(frozen=True)
class _MarkerNode:
    marker_id: str
    parent_id: str | None
    opening_index: int
    closing_index: int
    content: str


class TranslationChunkPiece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: str
    segment_id: str
    part_number: int = Field(gt=0)
    source_text: str


class TranslationChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    document_id: str
    document_order: int
    pieces: list[TranslationChunkPiece]
    estimated_source_tokens: int = Field(ge=0)

    def render_source(self) -> str:
        """Render source pieces using exact protected reference markers."""
        return "\n\n".join(
            f"<{piece.reference_id}>{piece.source_text}</{piece.reference_id}>"
            for piece in self.pieces
        )


class TranslationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    reference_id: str = ""
    message: str


class TranslationValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    issues: list[TranslationIssue] = Field(default_factory=list)


class InlineMarkerPlacement(BaseModel):
    """Structured split points for one already-translated passage."""

    model_config = ConfigDict(extra="forbid")
    reference_id: str
    parts: list[str]


class InlineMarkerPlacementResult(BaseModel):
    """Schema-constrained marker placements for a focused recovery batch."""

    model_config = ConfigDict(extra="forbid")
    placements: list[InlineMarkerPlacement]


class SingleInlineMarkerPlacement(BaseModel):
    """Schema-enforced three-way split for the common one-marker case."""

    model_config = ConfigDict(extra="forbid")
    reference_id: str
    before: str
    emphasized: str
    after: str


class SingleInlineMarkerPlacementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placements: list[SingleInlineMarkerPlacement]


class SingleInlineMarkerSelection(BaseModel):
    """Exact immutable target span selected for one inline marker."""

    model_config = ConfigDict(extra="forbid")
    reference_id: str
    emphasized: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)


class SingleInlineMarkerSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[SingleInlineMarkerSelection]


class InlineMarkerSelection(BaseModel):
    """Exact immutable target span selected for one marker in a flat passage."""

    model_config = ConfigDict(extra="forbid")
    reference_id: str
    marker_id: str = Field(pattern=r"^I\d{3}$")
    emphasized: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)


class InlineMarkerSelectionResult(BaseModel):
    """Marker-ID keyed exact spans for flat passages with one or more markers."""

    model_config = ConfigDict(extra="forbid")
    selections: list[InlineMarkerSelection]


class TranslatedChunk(BaseModel):
    """Validated translation output persisted as one resumable work unit."""

    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    document_id: str
    translations: dict[str, str]
    validation: TranslationValidation


class TranslatedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    source_text: str
    translated_text: str


class TranslatedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    manifest_id: str
    archive_path: str
    direction: TranslationDirection
    style: str
    segments: list[TranslatedSegment]


class TranslationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: TranslationDirection
    style: str
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    generation_attempts: int = Field(ge=0)
    deferred_chunk_count: int = Field(default=0, ge=0)
    deferred_segment_count: int = Field(default=0, ge=0)
    deferred_segment_ids: list[str] = Field(default_factory=list)


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[A-Za-z]")
_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_SEPARATOR = re.compile(r"^[\W_]+$", flags=re.UNICODE)
_NONTRANSLATABLE_URI = re.compile(
    r"^(?:(?:https?|ftp)://|mailto:|www\.)\S+$",
    flags=re.IGNORECASE,
)


def calculate_translation_source_budget(
    config: AppConfig,
    style_prompt: str,
    glossary_text: str,
) -> int:
    """Calculate source capacity from actual prompt/glossary size and fixed output reserve."""
    prompt_tokens = estimate_tokens(style_prompt) + 1_000
    glossary_tokens = estimate_tokens(glossary_text)
    available = (
        min(config.budget.working_limit, config.translation.max_num_ctx)
        - prompt_tokens
        - glossary_tokens
        - config.budget.expected_output_tokens
        - config.budget.reserve_tokens
    )
    source_budget = min(config.budget.source_tokens, available)
    if source_budget <= 0:
        raise ValueError("prompt and glossary leave no translation source capacity")
    return source_budget


def build_translation_chunks(
    document: PreprocessedDocument,
    max_source_tokens: int,
) -> list[TranslationChunk]:
    """Cover every preprocessed segment, splitting only segments over budget."""
    if max_source_tokens <= 0:
        raise ValueError("max_source_tokens must be positive")
    pieces: list[TranslationChunkPiece] = []
    for segment in document.segments:
        if (
            estimate_tokens(segment.processed_text) > max_source_tokens
            and re.search(r"</?I\d{3}>", segment.processed_text)
        ):
            raise ValueError(
                f"inline-marked segment exceeds source budget: {segment.segment_id}"
            )
        parts = split_text_to_budget(segment.processed_text, max_source_tokens)
        for part_number, part in enumerate(parts, start=1):
            suffix = f"-P{part_number:03d}" if len(parts) > 1 else ""
            pieces.append(
                TranslationChunkPiece(
                    reference_id=f"{segment.segment_id}{suffix}",
                    segment_id=segment.segment_id,
                    part_number=part_number,
                    source_text=part,
                )
            )
    chunks: list[TranslationChunk] = []
    current: list[TranslationChunkPiece] = []
    current_tokens = 0
    for piece in pieces:
        tokens = estimate_tokens(piece.source_text)
        if current and current_tokens + tokens > max_source_tokens:
            chunks.append(
                TranslationChunk(
                    chunk_id=f"translate-{document.order:04d}-{len(chunks) + 1:05d}",
                    document_id=document.manifest_id,
                    document_order=document.order,
                    pieces=current,
                    estimated_source_tokens=current_tokens,
                )
            )
            current = []
            current_tokens = 0
        current.append(piece)
        current_tokens += tokens
    if current:
        chunks.append(
            TranslationChunk(
                chunk_id=f"translate-{document.order:04d}-{len(chunks) + 1:05d}",
                document_id=document.manifest_id,
                document_order=document.order,
                pieces=current,
                estimated_source_tokens=current_tokens,
            )
        )
    return chunks


def constrain_translation_chunks_by_prompt(
    chunks: list[TranslationChunk],
    max_prompt_tokens: int,
    prompt_builder: Callable[[TranslationChunk], str],
) -> list[TranslationChunk]:
    """Split chunks until each fully rendered prompt fits the configured ceiling."""
    if max_prompt_tokens <= 0:
        raise ValueError("max_prompt_tokens must be positive")
    pending = list(chunks)
    accepted: list[TranslationChunk] = []
    while pending:
        chunk = pending.pop(0)
        prompt_tokens = estimate_tokens(prompt_builder(chunk))
        if prompt_tokens <= max_prompt_tokens:
            accepted.append(chunk)
            continue
        if len(chunk.pieces) == 1:
            piece = chunk.pieces[0]
            raise ValueError(
                f"translation passage {piece.reference_id} alone requires an estimated "
                f"{prompt_tokens} prompt tokens, above the {max_prompt_tokens} limit"
            )
        split_at = _balanced_piece_split(chunk.pieces)
        left = chunk.model_copy(
            update={
                "pieces": chunk.pieces[:split_at],
                "estimated_source_tokens": sum(
                    estimate_tokens(piece.source_text)
                    for piece in chunk.pieces[:split_at]
                ),
            }
        )
        right = chunk.model_copy(
            update={
                "pieces": chunk.pieces[split_at:],
                "estimated_source_tokens": sum(
                    estimate_tokens(piece.source_text)
                    for piece in chunk.pieces[split_at:]
                ),
            }
        )
        pending[0:0] = [left, right]

    return [
        chunk.model_copy(
            update={
                "chunk_id": (
                    f"translate-{chunk.document_order:04d}-{index:05d}"
                )
            }
        )
        for index, chunk in enumerate(accepted, start=1)
    ]


def _balanced_piece_split(pieces: list[TranslationChunkPiece]) -> int:
    """Choose a nonempty split near half of the source-token weight."""
    weights = [max(1, estimate_tokens(piece.source_text)) for piece in pieces]
    target = sum(weights) / 2
    running = 0
    best_index = 1
    best_distance = float("inf")
    for index, weight in enumerate(weights[:-1], start=1):
        running += weight
        distance = abs(target - running)
        if distance < best_distance:
            best_index = index
            best_distance = distance
    return best_index


def format_relevant_glossary(
    entries: list[GlossaryEntry],
    direction: TranslationDirection,
) -> str:
    """Format only relevant canonical glossary entries for a translation prompt."""
    lines = []
    for entry in sorted(
        entries, key=lambda item: (normalize_term(item.english), normalize_term(item.chinese))
    ):
        source = entry.english if direction is TranslationDirection.EN_TO_ZH else entry.chinese
        target = entry.chinese if direction is TranslationDirection.EN_TO_ZH else entry.english
        alternatives = ""
        if entry.aliases and direction is TranslationDirection.EN_TO_ZH:
            alternatives = f" | aliases: {', '.join(entry.aliases)}"
        note = f" | note: {entry.note}" if entry.note else ""
        lines.append(
            f"[{entry.category.value}] {source} => {target}{alternatives}{note}"
        )
    return "\n".join(lines)


def build_translation_prompt(
    chunk: TranslationChunk,
    relevant_glossary: list[GlossaryEntry],
    config: AppConfig,
) -> str:
    """Assemble direction, style, naturalness, glossary, and marker constraints."""
    instruction = load_style_instruction(
        config.translation.style,
        config.translation.custom_style_file,
    )
    style_prompt = build_style_prompt(
        instruction,
        config.translation.direction,
        de_ai_enabled=config.translation.de_ai_enabled,
        de_ai_strength=config.translation.de_ai_strength,
    )
    glossary = format_relevant_glossary(
        relevant_glossary, config.translation.direction
    )
    target = config.translation.direction.target_language.display_name
    if config.translation.use_glossary_strictly:
        glossary_policy = (
            "- First determine whether each occurrence matches the named entity or sense "
            "described by its glossary category and note. When it matches, use the exact "
            "approved target consistently. When it has a different meaning, ignore that "
            "glossary entry."
        )
    else:
        glossary_policy = (
            "- Treat glossary targets as context-sensitive preferences. Apply an entry only "
            "when the occurrence matches its category and note; choose a natural contextual "
            "translation for other senses."
        )
    source_policy = (
        "- The source text is preserved in its original language; do not assume a spelling "
        "match always has the glossary sense."
        if config.preprocessing.mode == "annotate"
        else
        "- Legacy preprocessing may have inserted approved target terms; keep applicable "
        "inserted terms unchanged."
    )
    marker_manifest = "\n".join(
        f"- {piece.reference_id}: "
        + (
            " ".join(re.findall(r"</?I\d{3}>", piece.source_text))
            or "(no inline markers)"
        )
        for piece in chunk.pieces
    )
    marker_examples = _build_marker_examples(
        config.translation.direction,
        config.translation.marker_examples,
    )
    return (
        f"{style_prompt}\n\n"
        "Approved relevant glossary:\n"
        f"{glossary or '(none)'}\n\n"
        "Translation contract:\n"
        f"- Translate the content inside every marker into {target}.\n"
        f"{glossary_policy}\n"
        f"{source_policy}\n"
        "- Copy every opening and closing marker exactly, once, and in the same order.\n"
        "- Preserve nested inline markers such as <I000>...</I000>; translate their content.\n"
        "- For each segment, copy exactly the inline-marker sequence listed below. Never "
        "invent, omit, rename, or move a marker to another segment.\n"
        "- Output no commentary, reasoning, Markdown fences, or text outside the markers.\n"
        "- Do not merge, split, omit, duplicate, or reorder marked passages.\n\n"
        "Required inline-marker manifest:\n"
        f"{marker_manifest}\n\n"
        f"{marker_examples}"
        f"Source:\n{chunk.render_source()}"
    )


def _build_marker_examples(
    direction: TranslationDirection,
    mode: str,
) -> str:
    """Return direction-aware one/few-shot examples of the exact marker contract."""
    if mode == "none":
        return ""
    if direction is TranslationDirection.EN_TO_ZH:
        examples = [
            (
                "She was <I000>very</I000> tired.",
                "她<I000>非常</I000>疲倦。",
            ),
            (
                "<I000></I000>Chapter <I001></I001> One",
                "<I000></I000>第一章<I001></I001>",
            ),
        ]
    else:
        examples = [
            (
                "她<I000>非常</I000>疲倦。",
                "She was <I000>very</I000> tired.",
            ),
            (
                "<I000></I000>第一章<I001></I001>",
                "<I000></I000>Chapter <I001></I001> One",
            ),
        ]
    selected = examples[:1] if mode == "one-shot" else examples
    rendered = "\n".join(
        f"Example {index} source: {source}\n"
        f"Example {index} valid translation: {target}"
        for index, (source, target) in enumerate(selected, start=1)
    )
    return (
        "Marker examples—the translation changes prose but preserves the exact marker "
        f"sequence:\n{rendered}\n\n"
    )


def parse_marked_translation(
    output: str,
    expected_ids: list[str],
) -> dict[str, str]:
    """Parse output only when marker IDs, order, uniqueness, and outside text are exact."""
    cursor = 0
    translations: dict[str, str] = {}
    for reference_id in expected_ids:
        opening = f"<{reference_id}>"
        closing = f"</{reference_id}>"
        opening_at = output.find(opening, cursor)
        if opening_at < 0 or output[cursor:opening_at].strip():
            raise TranslationOutputError(
                f"missing, duplicated, or out-of-order opening marker: {reference_id}"
            )
        content_at = opening_at + len(opening)
        closing_at = output.find(closing, content_at)
        if closing_at < 0:
            raise TranslationOutputError(f"missing closing marker: {reference_id}")
        content = output[content_at:closing_at]
        if opening in content or closing in content:
            raise TranslationOutputError(f"duplicated marker: {reference_id}")
        translations[reference_id] = content.strip()
        cursor = closing_at + len(closing)
    if output[cursor:].strip():
        raise TranslationOutputError("output contains text outside protected markers")
    if len(translations) != len(expected_ids):
        raise TranslationOutputError("marker sequence contains duplicate IDs")
    return translations


def _parse_with_canonical_closing_markers(
    output: str,
    expected_ids: list[str],
) -> dict[str, str]:
    """Recover prose when only generated closing-marker IDs are wrong.

    Recovery is deliberately narrow: each passage must have either its exact
    opening or a preceding misplaced closing marker naming that immediately next
    passage. Every passage must then end at a well-formed closing marker. This
    repairs labels and one mechanically implied opening; it never guesses an
    unmarked passage boundary.
    """
    if not expected_ids or any(
        re.fullmatch(r"D\d+-S\d+", reference_id) is None
        for reference_id in expected_ids
    ):
        raise TranslationOutputError("closing-marker recovery is unavailable")

    closing_pattern = re.compile(r"</(D\d+-S\d+)>")
    outer_marker = re.compile(r"</?D\d+-S\d+>")
    translations: dict[str, str] = {}
    cursor = 0
    while cursor < len(output) and output[cursor].isspace():
        cursor += 1
    implied_opening = ""
    for index, reference_id in enumerate(expected_ids):
        opening = f"<{reference_id}>"
        if output.startswith(opening, cursor):
            cursor += len(opening)
            implied_opening = ""
        elif implied_opening == reference_id:
            implied_opening = ""
        else:
            raise TranslationOutputError(
                f"passage lacks a recoverable opening marker: {reference_id}"
            )

        closing = closing_pattern.search(output, cursor)
        if closing is None:
            raise TranslationOutputError(
                f"passage lacks a recoverable closing marker: {reference_id}"
            )
        content = output[cursor : closing.start()]
        if outer_marker.search(content):
            raise TranslationOutputError(
                f"passage contains an ambiguous outer marker: {reference_id}"
            )
        translations[reference_id] = content.strip()
        cursor = closing.end()
        closing_id = closing.group(1)
        if closing_id != reference_id:
            next_id = expected_ids[index + 1] if index + 1 < len(expected_ids) else ""
            if closing_id != next_id:
                raise TranslationOutputError(
                    f"closing marker does not imply the next passage: {reference_id}"
                )
            implied_opening = next_id
        while cursor < len(output) and output[cursor].isspace():
            cursor += 1
    if output[cursor:].strip():
        raise TranslationOutputError("output contains text outside recoverable markers")
    return translations


def _parse_complete_translation_prefix(
    output: str,
    expected_ids: list[str],
) -> tuple[dict[str, str], str]:
    """Return the exact, fully closed passage prefix before a failure.

    The unfinished passage is never retained, and recovery stops at the first
    malformed or missing marker rather than inferring a passage boundary.
    """
    translations: dict[str, str] = {}
    cursor = 0
    for reference_id in expected_ids:
        opening = f"<{reference_id}>"
        closing = f"</{reference_id}>"
        opening_at = output.find(opening, cursor)
        if opening_at < 0 or output[cursor:opening_at].strip():
            return translations, reference_id
        content_at = opening_at + len(opening)
        closing_at = output.find(closing, content_at)
        if closing_at < 0:
            return translations, reference_id
        content = output[content_at:closing_at]
        if opening in content or closing in content:
            return translations, reference_id
        translations[reference_id] = content.strip()
        cursor = closing_at + len(closing)
    return translations, ""


def validate_translation_output(
    output: str,
    chunk: TranslationChunk,
    direction: TranslationDirection,
    relevant_glossary: list[GlossaryEntry] | None = None,
) -> tuple[dict[str, str], TranslationValidation]:
    """Validate marker structure, nonempty content, language, and obvious untranslated output."""
    expected_ids = [piece.reference_id for piece in chunk.pieces]
    marker_issue: TranslationIssue | None = None
    try:
        translations = parse_marked_translation(output, expected_ids)
    except TranslationOutputError as error:
        try:
            translations = _parse_with_canonical_closing_markers(output, expected_ids)
        except TranslationOutputError:
            translations, failed_reference_id = _parse_complete_translation_prefix(
                output, expected_ids
            )
            marker_issue = TranslationIssue(
                code="marker_contract",
                reference_id=failed_reference_id,
                message=str(error),
            )
    issues: list[TranslationIssue] = [marker_issue] if marker_issue else []
    pieces_by_id = {piece.reference_id: piece for piece in chunk.pieces}
    for reference_id, translated in translations.items():
        source = pieces_by_id[reference_id].source_text
        translated = _strip_unexpected_inline_markers(source, translated)
        translations[reference_id] = translated
        if not translated:
            issues.append(
                TranslationIssue(
                    code="empty_translation",
                    reference_id=reference_id,
                    message="translated content is empty",
                )
            )
            continue
        kind = classify_segment(
            source, translated, direction, relevant_glossary or []
        )
        source_has_translatable = should_run_language_check(kind)
        target_has_language = (
            bool(_CJK.search(translated))
            if direction.target_language is Language.CHINESE
            else bool(_LATIN.search(translated))
        )
        if source_has_translatable and not target_has_language:
            issues.append(
                TranslationIssue(
                    code="target_language_missing",
                    reference_id=reference_id,
                    message=f"translation lacks {direction.target_language.display_name} text",
                )
            )
        if source_has_translatable and translated.casefold() == source.casefold():
            issues.append(
                TranslationIssue(
                    code="untranslated_exact",
                    reference_id=reference_id,
                    message="translation is identical to source",
                )
            )
        source_inline_markers = re.findall(r"</?I\d{3}>", source)
        translated_inline_markers = re.findall(r"</?I\d{3}>", translated)
        if source_inline_markers != translated_inline_markers:
            issues.append(
                TranslationIssue(
                    code="protected_marker_mismatch",
                    reference_id=reference_id,
                    message=(
                        "inline marker sequence differs from source: "
                        f"expected {source_inline_markers}, received {translated_inline_markers}"
                    ),
                )
            )
        else:
            source_wrappers, _ = _split_full_span_inline_wrappers(source)
            translated_wrappers, _ = _split_full_span_inline_wrappers(translated)
            if source_wrappers and translated_wrappers[: len(source_wrappers)] != source_wrappers:
                issues.append(
                    TranslationIssue(
                        code="protected_marker_mismatch",
                        reference_id=reference_id,
                        message=(
                            "full-span inline wrappers no longer enclose the complete "
                            f"translation: expected {source_wrappers}, received "
                            f"{translated_wrappers}"
                        ),
                    )
                )
    return translations, TranslationValidation(passed=not issues, issues=issues)


def build_inline_marker_placement_prompt(
    chunk: TranslationChunk,
    translations: dict[str, str],
) -> str:
    """Ask only for split points in immutable translated prose."""
    passages = []
    for piece in chunk.pieces:
        translated = _INLINE_MARKER.sub("", translations[piece.reference_id])
        wrapper_ids, marker_sequence = _split_full_span_inline_wrappers(
            piece.source_text
        )
        marker_ids = [
            marker[2:5]
            for marker in marker_sequence
            if not marker.startswith("</")
        ]
        passages.append(
            f"Reference: {piece.reference_id}\n"
            f"Source context: {piece.source_text}\n"
            f"Immutable translation: {translated}\n"
            f"Required marker IDs in order: {', '.join(marker_ids)}\n"
            f"Automatically restored full-span wrapper IDs: "
            f"{', '.join(wrapper_ids) or '(none)'}\n"
            f"Required marker sequence: "
            f"{', '.join(marker_sequence)}\n"
            f"Required parts count: {len(marker_ids) * 2 + 1}"
        )
    return (
        "Inline-emphasis placement task. Do not translate or rewrite anything. "
        "For each passage, split the Immutable translation into the requested number "
        "of ordered parts, one more part than there are tags in Required marker sequence. "
        "Concatenating every part must reproduce the Immutable translation exactly, "
        "character for character. The tags will be inserted between consecutive parts "
        "in the exact listed sequence, including when markers are nested. Do not put "
        "<I...> tags in any part. Return every reference exactly once.\n\n"
        + "\n\n".join(passages)
    )


def supports_single_inline_marker_placement(chunk: TranslationChunk) -> bool:
    """Return whether every passage contains exactly one canonical marker pair."""
    return bool(chunk.pieces) and all(
        len(_source_inline_marker_ids(piece.source_text)) == 1
        for piece in chunk.pieces
    )


def supports_single_inline_marker_selection(chunk: TranslationChunk) -> bool:
    """Return whether exact-span selection can recover every passage marker."""
    if not supports_single_inline_marker_placement(chunk):
        return False
    for piece in chunk.pieces:
        marker_id = _source_inline_marker_ids(piece.source_text)[0]
        match = re.search(
            rf"<I{marker_id}>(?P<content>.*?)</I{marker_id}>",
            piece.source_text,
            flags=re.DOTALL,
        )
        if match is None or not match.group("content").strip():
            return False
    return True


def supports_inline_marker_selection(chunk: TranslationChunk) -> bool:
    """Return whether exact-span selection can restore flat markers in every piece."""
    if not chunk.pieces:
        return False
    has_nonempty_marker = False
    for piece in chunk.pieces:
        marker_sequence = _source_inline_marker_sequence(piece.source_text)
        nodes = _build_marker_nodes(
            marker_sequence,
            _INLINE_MARKER.split(piece.source_text),
        )
        if (
            not nodes
            or any(node.parent_id is not None for node in nodes)
            or len({node.marker_id for node in nodes}) != len(nodes)
        ):
            return False
        has_nonempty_marker = has_nonempty_marker or any(
            node.content.strip() for node in nodes
        )
    return has_nonempty_marker


def build_inline_marker_selection_prompt(
    chunk: TranslationChunk,
    translations: dict[str, str],
) -> str:
    """Ask for one exact existing target substring per flat source marker."""
    if not supports_inline_marker_selection(chunk):
        raise TranslationOutputError(
            "exact marker selection requires flat marker pairs with a nonempty span"
        )
    cases = []
    for piece in chunk.pieces:
        marker_sequence = _source_inline_marker_sequence(piece.source_text)
        nodes = _build_marker_nodes(
            marker_sequence,
            _INLINE_MARKER.split(piece.source_text),
        )
        immutable = _INLINE_MARKER.sub("", translations[piece.reference_id])
        for node in nodes:
            if not node.content.strip():
                continue
            cases.append(
                f"Reference: {piece.reference_id}\n"
                f"Marker ID: I{node.marker_id}\n"
                f"Source emphasized text: {node.content}\n"
                f"Source context: {piece.source_text}\n"
                f"Immutable translation: {immutable}"
            )
    return (
        "Inline-emphasis exact-span selection task. Do not translate, rewrite, or "
        "return the complete translation. For every Reference and Marker ID case, "
        "copy into emphasized the smallest contiguous substring that already occurs "
        "verbatim in Immutable translation and corresponds to Source emphasized text. "
        "Copy Marker ID exactly. If the substring occurs more than once, set occurrence "
        "to its one-based occurrence from left to right; otherwise use 1. Do not include "
        "<I...> tags. Return every case exactly once and in the given order.\n\n"
        + "\n\n".join(cases)
    )


def apply_inline_marker_selections(
    chunk: TranslationChunk,
    translations: dict[str, str],
    result: InlineMarkerSelectionResult,
) -> dict[str, str]:
    """Insert flat markers around exact selected spans without rewriting prose."""
    if not supports_inline_marker_selection(chunk):
        raise TranslationOutputError(
            "exact marker selection requires flat marker pairs with a nonempty span"
        )
    expected: list[tuple[str, str]] = []
    nodes_by_reference: dict[str, list[_MarkerNode]] = {}
    for piece in chunk.pieces:
        nodes = _build_marker_nodes(
            _source_inline_marker_sequence(piece.source_text),
            _INLINE_MARKER.split(piece.source_text),
        )
        nodes_by_reference[piece.reference_id] = nodes
        expected.extend(
            (piece.reference_id, f"I{node.marker_id}")
            for node in nodes
            if node.content.strip()
        )
    received = [
        (selection.reference_id, selection.marker_id)
        for selection in result.selections
    ]
    if received != expected:
        raise TranslationOutputError(
            f"marker selections differ: expected {expected}, received {received}"
        )

    selections = {
        (item.reference_id, item.marker_id): item for item in result.selections
    }
    repaired: dict[str, str] = {}
    for piece in chunk.pieces:
        reference_id = piece.reference_id
        marked_translation = translations[reference_id]
        plain = _INLINE_MARKER.sub("", marked_translation)
        locations_by_marker: dict[str, tuple[int, int]] = {}
        for node in nodes_by_reference[reference_id]:
            if not node.content.strip():
                continue
            marker_id = f"I{node.marker_id}"
            selection = selections[(reference_id, marker_id)]
            if _INLINE_MARKER.search(selection.emphasized):
                raise TranslationOutputError(
                    f"marker selection contains inline tags: {reference_id}/{marker_id}"
                )
            occurrences = [
                (match.start(), match.end())
                for match in re.finditer(re.escape(selection.emphasized), plain)
            ]
            if selection.occurrence > len(occurrences):
                raise TranslationOutputError(
                    "marker selection is not an exact target occurrence: "
                    f"{reference_id}/{marker_id}"
                )
            start, end = occurrences[selection.occurrence - 1]
            locations_by_marker[marker_id] = (start, end)
        nonempty_locations = [
            (*locations_by_marker[f"I{node.marker_id}"], f"I{node.marker_id}")
            for node in nodes_by_reference[reference_id]
            if node.content.strip()
        ]
        ordered_nonempty = sorted(nonempty_locations)
        if ordered_nonempty != nonempty_locations or any(
            current[0] < previous[1]
            for previous, current in zip(
                ordered_nonempty, ordered_nonempty[1:]
            )
        ):
            raise TranslationOutputError(
                f"marker selections overlap or change source order: {reference_id}"
            )

        nodes = nodes_by_reference[reference_id]
        source_plain_length = len(_INLINE_MARKER.sub("", piece.source_text))
        previous_end = 0
        for index, node in enumerate(nodes):
            marker_id = f"I{node.marker_id}"
            if node.content.strip():
                previous_end = locations_by_marker[marker_id][1]
                continue
            next_start = len(plain)
            for following in nodes[index + 1 :]:
                following_id = f"I{following.marker_id}"
                if following.content.strip():
                    next_start = locations_by_marker[following_id][0]
                    break
            token = f"<{marker_id}></{marker_id}>"
            target_index = marked_translation.find(token)
            if target_index >= 0:
                desired = len(
                    _INLINE_MARKER.sub("", marked_translation[:target_index])
                )
            else:
                source_index = piece.source_text.find(token)
                source_offset = len(
                    _INLINE_MARKER.sub("", piece.source_text[:source_index])
                )
                desired = (
                    0
                    if source_plain_length == 0
                    else round(source_offset * len(plain) / source_plain_length)
                )
            empty_at = min(max(desired, previous_end), next_start)
            locations_by_marker[marker_id] = (empty_at, empty_at)
            previous_end = empty_at

        locations = [
            (*locations_by_marker[f"I{node.marker_id}"], f"I{node.marker_id}")
            for node in nodes
        ]
        rendered: list[str] = []
        cursor = 0
        for start, end, marker_id in locations:
            rendered.append(plain[cursor:start])
            rendered.append(f"<{marker_id}>")
            rendered.append(plain[start:end])
            rendered.append(f"</{marker_id}>")
            cursor = end
        rendered.append(plain[cursor:])
        repaired[reference_id] = "".join(rendered)
    return repaired


def build_single_inline_marker_selection_prompt(
    chunk: TranslationChunk,
    translations: dict[str, str],
) -> str:
    """Ask for an exact existing target substring rather than rewritten splits."""
    if not supports_single_inline_marker_selection(chunk):
        raise TranslationOutputError(
            "single-marker selection requires one non-empty marker pair per passage"
        )
    passages = []
    for piece in chunk.pieces:
        marker_id = _source_inline_marker_ids(piece.source_text)[0]
        source_match = re.search(
            rf"<I{marker_id}>(?P<content>.*?)</I{marker_id}>",
            piece.source_text,
            flags=re.DOTALL,
        )
        assert source_match is not None
        passages.append(
            f"Reference: {piece.reference_id}\n"
            f"Source emphasized text: {source_match.group('content')}\n"
            f"Source context: {piece.source_text}\n"
            f"Immutable translation: {_INLINE_MARKER.sub('', translations[piece.reference_id])}"
        )
    return (
        "Inline-emphasis exact-span selection task. Do not translate, rewrite, or return "
        "the complete translation. For every passage, copy into emphasized the smallest "
        "contiguous substring that already occurs verbatim in Immutable translation and "
        "corresponds to Source emphasized text. If that exact substring occurs more than "
        "once, set occurrence to its one-based occurrence from left to right; otherwise use "
        "1. Do not include <I...> tags. Return every reference exactly once.\n\n"
        + "\n\n".join(passages)
    )


def apply_single_inline_marker_selections(
    chunk: TranslationChunk,
    translations: dict[str, str],
    result: SingleInlineMarkerSelectionResult,
) -> dict[str, str]:
    """Insert one marker around an exact selected occurrence without rewriting prose."""
    if not supports_single_inline_marker_selection(chunk):
        raise TranslationOutputError(
            "single-marker selection requires one non-empty marker pair per passage"
        )
    expected_ids = [piece.reference_id for piece in chunk.pieces]
    received_ids = [selection.reference_id for selection in result.selections]
    if received_ids != expected_ids:
        raise TranslationOutputError(
            f"marker selection references differ: expected {expected_ids}, received {received_ids}"
        )

    selections = {item.reference_id: item for item in result.selections}
    repaired: dict[str, str] = {}
    for piece in chunk.pieces:
        reference_id = piece.reference_id
        plain = _INLINE_MARKER.sub("", translations[reference_id])
        selection = selections[reference_id]
        if _INLINE_MARKER.search(selection.emphasized):
            raise TranslationOutputError(
                f"marker selection contains inline tags: {reference_id}"
            )
        starts = [
            match.start()
            for match in re.finditer(re.escape(selection.emphasized), plain)
        ]
        if selection.occurrence > len(starts):
            raise TranslationOutputError(
                f"marker selection is not an exact target occurrence: {reference_id}"
            )
        start = starts[selection.occurrence - 1]
        end = start + len(selection.emphasized)
        marker_id = _source_inline_marker_ids(piece.source_text)[0]
        repaired[reference_id] = (
            plain[:start]
            + f"<I{marker_id}>{plain[start:end]}</I{marker_id}>"
            + plain[end:]
        )
    return repaired


def build_single_inline_marker_placement_prompt(
    chunk: TranslationChunk,
    translations: dict[str, str],
) -> str:
    """Build an explicit before/emphasized/after placement request."""
    if not supports_single_inline_marker_placement(chunk):
        raise TranslationOutputError(
            "single-marker placement requires exactly one marker pair per passage"
        )
    passages = []
    for piece in chunk.pieces:
        passages.append(
            f"Reference: {piece.reference_id}\n"
            f"Source context: {piece.source_text}\n"
            f"Immutable translation: {_INLINE_MARKER.sub('', translations[piece.reference_id])}"
        )
    return (
        "Inline-emphasis placement task. Do not translate or rewrite anything. "
        "For every passage, return its reference_id and split the Immutable translation "
        "into exactly three required strings: before, emphasized, and after. Their exact "
        "concatenation must reproduce the Immutable translation character for character. "
        "The emphasized string must correspond to the source text inside <I000>. Do not "
        "include any <I...> tags. Return every reference exactly once.\n\n"
        + "\n\n".join(passages)
    )


def apply_single_inline_marker_placements(
    chunk: TranslationChunk,
    translations: dict[str, str],
    result: SingleInlineMarkerPlacementResult,
) -> dict[str, str]:
    """Validate explicit three-way splits and insert the single canonical marker."""
    plain_by_id = {
        piece.reference_id: _INLINE_MARKER.sub("", translations[piece.reference_id])
        for piece in chunk.pieces
    }
    placements = []
    for item in result.placements:
        before, emphasized, after = item.before, item.emphasized, item.after
        plain = plain_by_id.get(item.reference_id, "")
        window = before + emphasized + after
        if window != plain:
            window_at = plain.find(window)
            if (
                not window
                or window_at < 0
                or plain.find(window, window_at + 1) >= 0
            ):
                raise TranslationOutputError(
                    f"marker placement altered immutable translation: {item.reference_id}"
                )
            before = plain[:window_at] + before
            after = after + plain[window_at + len(window) :]
        placements.append(
            InlineMarkerPlacement(
                reference_id=item.reference_id,
                parts=[before, emphasized, after],
            )
        )
    generic = InlineMarkerPlacementResult(placements=placements)
    return apply_inline_marker_placements(chunk, translations, generic)


def apply_inline_marker_placements(
    chunk: TranslationChunk,
    translations: dict[str, str],
    result: InlineMarkerPlacementResult,
) -> dict[str, str]:
    """Insert canonical source markers after exact structured split validation."""
    expected_ids = [piece.reference_id for piece in chunk.pieces]
    received_ids = [placement.reference_id for placement in result.placements]
    if received_ids != expected_ids:
        raise TranslationOutputError(
            f"marker placement references differ: expected {expected_ids}, received {received_ids}"
        )
    placements = {item.reference_id: item for item in result.placements}
    repaired: dict[str, str] = {}
    for piece in chunk.pieces:
        reference_id = piece.reference_id
        plain = _INLINE_MARKER.sub("", translations[reference_id])
        deterministic = _restore_deterministic_inline_markers_for_piece(
            piece.source_text,
            plain,
        )
        full_marker_sequence = _source_inline_marker_sequence(piece.source_text)
        wrapper_ids, marker_sequence = _split_full_span_inline_wrappers(
            piece.source_text
        )
        parts = placements[reference_id].parts
        if deterministic is not None:
            if any(_INLINE_MARKER.search(part) for part in parts):
                raise TranslationOutputError(
                    f"marker placement parts contain inline tags: {reference_id}"
                )
            if "".join(parts) != plain:
                raise TranslationOutputError(
                    f"marker placement altered immutable translation: {reference_id}"
                )
            repaired[reference_id] = deterministic
            continue
        expected_part_count = len(marker_sequence) + 1
        uses_full_sequence = len(parts) == len(full_marker_sequence) + 1
        if len(parts) != expected_part_count and not uses_full_sequence:
            raise TranslationOutputError(
                f"marker placement parts for {reference_id}: expected "
                f"{expected_part_count} after full-span wrappers or "
                f"{len(full_marker_sequence) + 1}, received {len(parts)}"
            )
        if any(_INLINE_MARKER.search(part) for part in parts):
            raise TranslationOutputError(
                f"marker placement parts contain inline tags: {reference_id}"
            )
        if "".join(parts) != plain:
            recovered = _recover_marker_spans_from_parts(
                piece.source_text,
                marker_sequence,
                parts,
                plain,
            )
            if recovered is None:
                raise TranslationOutputError(
                    f"marker placement altered immutable translation: {reference_id}"
                )
            rendered = recovered
            for marker_id in reversed(wrapper_ids):
                rendered = f"<I{marker_id}>{rendered}</I{marker_id}>"
            repaired[reference_id] = rendered
            continue
        active_sequence = full_marker_sequence if uses_full_sequence else marker_sequence
        rendered = parts[0]
        for marker, part in zip(active_sequence, parts[1:]):
            rendered += marker + part
        if not uses_full_sequence:
            for marker_id in reversed(wrapper_ids):
                rendered = f"<I{marker_id}>{rendered}</I{marker_id}>"
        repaired[reference_id] = rendered
    return repaired


def restore_deterministic_inline_markers(
    chunk: TranslationChunk,
    translations: dict[str, str],
) -> dict[str, str] | None:
    """Restore full-span and empty markers without an LLM when every piece permits it."""
    repaired: dict[str, str] = {}
    for piece in chunk.pieces:
        translated = translations.get(piece.reference_id)
        if translated is None:
            return None
        restored = _restore_deterministic_inline_markers_for_piece(
            piece.source_text,
            translated,
        )
        if restored is None:
            return None
        repaired[piece.reference_id] = restored
    return repaired


def _source_inline_marker_ids(source: str) -> list[str]:
    """Return opening marker IDs from a canonical balanced source sequence."""
    return [
        marker[2:5]
        for marker in _source_inline_marker_sequence(source)
        if not marker.startswith("</")
    ]


def _split_full_span_inline_wrappers(source: str) -> tuple[list[str], list[str]]:
    """Separate markers that deterministically wrap the complete passage."""
    _source_inline_marker_sequence(source)
    wrapper_ids: list[str] = []
    inner = source
    while True:
        match = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner,
            flags=re.DOTALL,
        )
        if match is None:
            break
        wrapper_ids.append(match.group("id"))
        inner = match.group("inner")
    return wrapper_ids, _source_inline_marker_sequence(inner)


def _restore_deterministic_inline_markers_for_piece(
    source: str,
    translation: str,
) -> str | None:
    """Restore wrappers and zero-width marker pairs at stable relative boundaries."""
    repeated_span_restoration = _restore_repeated_equivalent_inline_markers(
        source,
        translation,
    )
    if repeated_span_restoration is not None:
        return repeated_span_restoration

    plain_translation = _INLINE_MARKER.sub("", translation)
    _source_inline_marker_sequence(source)
    wrapper_ids: list[str] = []
    inner = source
    while True:
        match = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner,
            flags=re.DOTALL,
        )
        if match is None:
            break
        wrapper_ids.append(match.group("id"))
        inner = match.group("inner")

    events: list[tuple[int, str]] = []
    stack: list[tuple[str, int]] = []
    visible_offset = 0
    cursor = 0
    for match in _INLINE_MARKER.finditer(inner):
        visible_offset += len(inner[cursor : match.start()])
        marker = match.group(0)
        events.append((visible_offset, marker))
        opening = re.fullmatch(r"<I(?P<id>\d{3})>", marker)
        if opening is not None:
            stack.append((opening.group("id"), visible_offset))
        else:
            closing = re.fullmatch(r"</I(?P<id>\d{3})>", marker)
            assert closing is not None and stack
            marker_id, opening_offset = stack.pop()
            assert marker_id == closing.group("id")
            if opening_offset != visible_offset:
                return None
        cursor = match.end()
    visible_length = visible_offset + len(inner[cursor:])

    by_target_offset: dict[int, list[str]] = {}
    for source_offset, marker in events:
        target_offset = (
            0
            if visible_length == 0
            else round(source_offset * len(plain_translation) / visible_length)
        )
        by_target_offset.setdefault(target_offset, []).append(marker)
    rendered_parts: list[str] = []
    for offset in range(len(plain_translation) + 1):
        rendered_parts.extend(by_target_offset.get(offset, []))
        if offset < len(plain_translation):
            rendered_parts.append(plain_translation[offset])
    rendered = "".join(rendered_parts)
    for marker_id in reversed(wrapper_ids):
        rendered = f"<I{marker_id}>{rendered}</I{marker_id}>"
    return rendered


def _restore_repeated_equivalent_inline_markers(
    source: str,
    translation: str,
) -> str | None:
    """Propagate a surviving exact marker span to equivalent repeated source spans.

    This recovery is intentionally narrow. Source spans must be flat, nonempty, and
    identical after compatibility, case, whitespace, and punctuation normalization.
    A corresponding marker must survive in the model output, and its exact target
    substring must occur exactly as many times as the equivalent source span. These
    constraints make placement mechanical rather than a translation judgment.
    """
    wrapper_ids: list[str] = []
    inner_source = source
    while True:
        wrapper = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner_source,
            flags=re.DOTALL,
        )
        if wrapper is None:
            break
        wrapper_ids.append(wrapper.group("id"))
        inner_source = wrapper.group("inner")

    source_sequence = _source_inline_marker_sequence(inner_source)
    source_nodes = _build_marker_nodes(
        source_sequence,
        _INLINE_MARKER.split(inner_source),
    )
    if (
        not source_nodes
        or len(source_nodes) < 2
        or any(node.parent_id is not None or not node.content for node in source_nodes)
    ):
        return None

    inner_target = translation
    while True:
        wrapper = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner_target,
            flags=re.DOTALL,
        )
        if wrapper is None:
            break
        inner_target = wrapper.group("inner")
    try:
        target_sequence = _source_inline_marker_sequence(inner_target)
    except TranslationOutputError:
        return None
    target_nodes = _build_marker_nodes(
        target_sequence,
        _INLINE_MARKER.split(inner_target),
    )
    if not target_nodes:
        return None

    source_by_id = {node.marker_id: node for node in source_nodes}
    source_groups: dict[str, list[_MarkerNode]] = defaultdict(list)
    for node in source_nodes:
        key = _marker_span_equivalence_key(node.content)
        if not key:
            return None
        source_groups[key].append(node)

    target_span_by_key: dict[str, str] = {}
    for node in target_nodes:
        source_node = source_by_id.get(node.marker_id)
        if source_node is None or node.parent_id is not None or not node.content:
            continue
        key = _marker_span_equivalence_key(source_node.content)
        previous = target_span_by_key.setdefault(key, node.content)
        if previous != node.content:
            return None
    if any(key not in target_span_by_key for key in source_groups):
        return None

    plain_translation = _INLINE_MARKER.sub("", translation)
    locations: dict[str, tuple[int, int]] = {}
    for key, nodes in source_groups.items():
        target_span = target_span_by_key[key]
        occurrences = [
            (match.start(), match.end())
            for match in re.finditer(re.escape(target_span), plain_translation)
        ]
        if len(occurrences) != len(nodes):
            return None
        for node, location in zip(
            sorted(nodes, key=lambda item: item.opening_index),
            occurrences,
            strict=True,
        ):
            locations[node.marker_id] = location

    ordered_nodes = sorted(source_nodes, key=lambda item: item.opening_index)
    previous_end = 0
    rendered: list[str] = []
    for node in ordered_nodes:
        start, end = locations[node.marker_id]
        if start < previous_end:
            return None
        rendered.append(plain_translation[previous_end:start])
        rendered.append(f"<I{node.marker_id}>")
        rendered.append(plain_translation[start:end])
        rendered.append(f"</I{node.marker_id}>")
        previous_end = end
    rendered.append(plain_translation[previous_end:])
    restored = "".join(rendered)
    for marker_id in reversed(wrapper_ids):
        restored = f"<I{marker_id}>{restored}</I{marker_id}>"
    return restored


def _marker_span_equivalence_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _recover_marker_spans_from_parts(
    source: str,
    marker_sequence: list[str],
    parts: list[str],
    plain_translation: str,
) -> str | None:
    """Recover exact flat or nested spans when only surrounding context was duplicated."""
    if len(parts) != len(marker_sequence) + 1:
        return None

    inner_source = source
    while True:
        wrapper = re.fullmatch(
            r"<I(?P<id>\d{3})>(?P<inner>.*)</I(?P=id)>",
            inner_source,
            flags=re.DOTALL,
        )
        if wrapper is None:
            break
        inner_source = wrapper.group("inner")
    source_parts = _INLINE_MARKER.split(inner_source)
    source_nodes = _build_marker_nodes(marker_sequence, source_parts)
    target_nodes = _build_marker_nodes(marker_sequence, parts)
    if source_nodes is None or target_nodes is None:
        return None
    source_by_id = {node.marker_id: node for node in source_nodes}

    locations: dict[str, tuple[int, int]] = {}
    for node in target_nodes:
        source_node = source_by_id.get(node.marker_id)
        if source_node is None:
            return None
        if not node.content and source_node.content:
            return None
        before_context = parts[node.opening_index]
        after_context = parts[node.closing_index + 1]
        occurrences = (
            [(offset, offset) for offset in range(len(plain_translation) + 1)]
            if not node.content
            else [
                (match.start(), match.end())
                for match in re.finditer(re.escape(node.content), plain_translation)
            ]
        )
        if not occurrences:
            return None
        candidates: list[tuple[int, int, int]] = []
        for start, end in occurrences:
            score = _matching_suffix_length(
                before_context,
                plain_translation[:start],
            ) + _matching_prefix_length(
                after_context,
                plain_translation[end:],
            )
            candidates.append((start, end, score))
        best_score = max(score for _, _, score in candidates)
        best = [(start, end) for start, end, score in candidates if score == best_score]
        if len(best) != 1:
            return None
        locations[node.marker_id] = best[0]

    ordered_nodes = sorted(target_nodes, key=lambda node: node.opening_index)
    previous_by_parent: dict[str | None, tuple[int, int]] = {}
    for node in ordered_nodes:
        start, end = locations[node.marker_id]
        if node.parent_id is not None:
            parent_start, parent_end = locations[node.parent_id]
            if start < parent_start or end > parent_end:
                return None
        previous = previous_by_parent.get(node.parent_id)
        if previous is not None and start < previous[1]:
            return None
        previous_by_parent[node.parent_id] = (start, end)

    event_positions: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for node in target_nodes:
        start, end = locations[node.marker_id]
        event_positions[start].append(
            (node.opening_index, marker_sequence[node.opening_index])
        )
        event_positions[end].append(
            (node.closing_index, marker_sequence[node.closing_index])
        )
    rendered: list[str] = []
    for offset in range(len(plain_translation) + 1):
        rendered.extend(
            marker
            for _, marker in sorted(event_positions.get(offset, []))
        )
        if offset < len(plain_translation):
            rendered.append(plain_translation[offset])
    recovered = "".join(rendered)
    if _source_inline_marker_sequence(recovered) != marker_sequence:
        return None
    return recovered


def _build_marker_nodes(
    marker_sequence: list[str],
    parts: list[str],
) -> list[_MarkerNode] | None:
    if len(parts) != len(marker_sequence) + 1:
        return None
    stack: list[tuple[str, str | None, int]] = []
    nodes: list[_MarkerNode] = []
    seen: set[str] = set()
    for index, marker in enumerate(marker_sequence):
        opening = re.fullmatch(r"<I(?P<id>\d{3})>", marker)
        if opening is not None:
            marker_id = opening.group("id")
            if marker_id in seen:
                return None
            seen.add(marker_id)
            stack.append((marker_id, stack[-1][0] if stack else None, index))
            continue
        closing = re.fullmatch(r"</I(?P<id>\d{3})>", marker)
        if closing is None or not stack:
            return None
        marker_id, parent_id, opening_index = stack.pop()
        if marker_id != closing.group("id"):
            return None
        nodes.append(
            _MarkerNode(
                marker_id=marker_id,
                parent_id=parent_id,
                opening_index=opening_index,
                closing_index=index,
                content="".join(parts[opening_index + 1 : index + 1]),
            )
        )
    return None if stack else nodes


def _matching_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_character, right_character in zip(left, right):
        if left_character != right_character:
            break
        length += 1
    return length


def _matching_suffix_length(left: str, right: str) -> int:
    return _matching_prefix_length(left[::-1], right[::-1])


def _source_inline_marker_sequence(source: str) -> list[str]:
    """Return a canonical flat or nested marker sequence, rejecting crossed pairs."""
    markers = _INLINE_MARKER.findall(source)
    stack: list[str] = []
    for marker in markers:
        opening = re.fullmatch(r"<I(?P<id>\d{3})>", marker)
        if opening is not None:
            marker_id = opening.group("id")
            stack.append(marker_id)
            continue
        closing = re.fullmatch(r"</I(?P<id>\d{3})>", marker)
        if closing is None or not stack or stack.pop() != closing.group("id"):
            raise TranslationOutputError(
                f"source inline marker sequence is not canonical: {markers}"
            )
    if stack:
        raise TranslationOutputError("source inline marker sequence is unbalanced")
    return markers


def _strip_unexpected_inline_markers(source: str, translated: str) -> str:
    """Remove model-invented inline tags while retaining source-owned marker IDs."""
    allowed = set(_INLINE_MARKER.findall(source))
    return _INLINE_MARKER.sub(
        lambda match: match.group(0) if match.group(0) in allowed else "",
        translated,
    )


def _is_separator(text: str) -> bool:
    """Recognize decorative separators while ignoring protected inline markers."""
    visible = _INLINE_MARKER.sub("", text).strip()
    return bool(visible) and bool(_SEPARATOR.fullmatch(visible))


def _is_nontranslatable_uri(text: str) -> bool:
    """Recognize a URI occupying the entire segment, ignoring structural markers."""
    visible = _INLINE_MARKER.sub("", text).strip()
    return bool(_NONTRANSLATABLE_URI.fullmatch(visible))


def _is_approved_preserved_literal(
    source: str,
    translated: str,
    direction: TranslationDirection,
    glossary: list[GlossaryEntry],
) -> bool:
    """Allow an approved preserved identifier surrounded only by punctuation."""
    source_core = _strip_outer_punctuation(_INLINE_MARKER.sub("", source))
    target_core = _strip_outer_punctuation(_INLINE_MARKER.sub("", translated))
    if not source_core or not target_core:
        return False
    for entry in glossary:
        approved_source = (
            entry.english
            if direction is TranslationDirection.EN_TO_ZH
            else entry.chinese
        )
        approved_target = (
            entry.chinese
            if direction is TranslationDirection.EN_TO_ZH
            else entry.english
        )
        if (
            source_core.casefold() == approved_source.casefold()
            and target_core == approved_target
            and is_preservable_technical_identifier(approved_target)
        ):
            return True
    return False


def _strip_outer_punctuation(text: str) -> str:
    return re.sub(r"^\W+|\W+$", "", text.strip(), flags=re.UNICODE)


def assemble_translated_segments(
    document: PreprocessedDocument,
    translated_parts: dict[str, str],
    direction: TranslationDirection,
) -> list[TranslatedSegment]:
    """Reassemble split translation parts into original stable segment IDs."""
    grouped: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    for reference_id, translated in translated_parts.items():
        match = re.fullmatch(r"(?P<segment>.+)-P(?P<part>\d{3})", reference_id)
        if match:
            grouped[match.group("segment")].append((int(match.group("part")), translated))
        else:
            grouped[reference_id].append((1, translated))
    source_by_id = {segment.segment_id: segment.processed_text for segment in document.segments}
    assembled: list[TranslatedSegment] = []
    joiner = " " if direction.target_language is Language.ENGLISH else ""
    for source_segment in document.segments:
        parts = sorted(grouped.get(source_segment.segment_id, []))
        if not parts:
            raise TranslationOutputError(
                f"missing translated segment: {source_segment.segment_id}"
            )
        part_numbers = [part for part, _ in parts]
        if part_numbers != list(range(1, len(parts) + 1)):
            raise TranslationOutputError(
                f"non-contiguous translated parts: {source_segment.segment_id}"
            )
        assembled.append(
            TranslatedSegment(
                segment_id=source_segment.segment_id,
                source_text=source_by_id[source_segment.segment_id],
                translated_text=joiner.join(text for _, text in parts),
            )
        )
    return assembled


def render_translated_document(document: TranslatedDocument) -> str:
    """Render translated segments with their original stable markers."""
    return "\n\n".join(
        f"<{segment.segment_id}>{segment.translated_text}</{segment.segment_id}>"
        for segment in document.segments
    ) + ("\n" if document.segments else "")
