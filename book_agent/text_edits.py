"""Append-only tracked manual edits to translated segments (docs/FULL_TEXT_REVIEW.md).

Edits live in ``edits/segment-edits.jsonl`` in the job folder, owned by no
pipeline stage, so rerunning any stage never wipes them. A per-job sidecar
lock serializes writers (the dashboard and any CLI command), and writers
atomically replace the complete log so readers see either the old or new
batch, never a partially appended batch.

An edit is always made against the current ``validate_repaired`` draft: an
edit stores the hash of the pipeline text it was based on
(``base_target_sha256``), and a later event for the same segment is only
"active" (state ``edited``) while the current pipeline text still hashes to
that value. If the pipeline text has since changed (typically after a
rerun), the segment is a ``conflict``: it still overlays onto compile, but
also blocks compile like an unresolved review segment until unblocked with
``apply_keep`` (re-base against the new pipeline text), ``apply_take_pipeline``
(drop the edit), or a fresh ``apply_edit``.
"""

from __future__ import annotations

import getpass
import re
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .atomic_io import atomic_write_text
from .audit import (
    AuditCategory,
    AuditIssue,
    AuditSeverity,
    audit_translated_document,
    reapply_quantity_adjudications,
)
from .config import AppConfig
from .consistency import (
    book_consistency_issues,
    book_segments,
    convention_issues,
    expression_issues,
)
from .pipeline_state import WorkflowStage
from .state import StageStatus, connect_state, get_stage_status
from .style_sheet import StyleSheet, enabled_style_sheet
from .hashing import hash_named_values, sha256_text
from .numeric_adjudication import rule_numeric_findings
from .repair import RepairDisposition, RepairedDocument, RepairedValidationReport
from .stages.audit import load_document_audits
from .stages.preprocess import load_preprocessed_documents
from .stages.validate_repaired import load_validated_repaired_documents
from .workspace import JobWorkspace

LOG_RELATIVE = "edits/segment-edits.jsonl"
# What the book says of itself shares the log under ids of its own
# (book_agent.book_edits): a note's paragraph (D0001-N000002), and the title,
# a contents entry, a picture's description (BOOK-...), which stand in no chapter.
BOOK_DOCUMENT = "BOOK"
_NOTE_ID = re.compile(r"D\d{4}-N\d{6}")


def is_book_item(segment_id: str) -> bool:
    """Whether an id in the log is one of the book's own items rather than a passage."""
    return segment_id.startswith("BOOK-") or bool(_NOTE_ID.fullmatch(segment_id))


class EditAction(str, Enum):
    EDIT = "edit"
    REVERT = "revert"
    KEEP = "keep"
    TAKE_PIPELINE = "take_pipeline"


class SegmentEditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    at: str = Field(min_length=1)
    author: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    action: EditAction
    text: str = ""
    previous_text: str = ""
    base_source_sha256: str = Field(min_length=1)
    base_target_sha256: str = Field(min_length=1)
    # Kept alongside the hash so a later conflict can show the actual pipeline
    # text the edit was based on. Empty is the backward-compatible value for
    # schema-v1 events written before this field existed.
    base_target_text: str = ""
    reason: str = Field(min_length=3)
    overrides: list[str] = Field(default_factory=list)


class SegmentEditState(str, Enum):
    PIPELINE = "pipeline"
    EDITED = "edited"
    CONFLICT = "conflict"
    ORPHANED = "orphaned"


@dataclass(frozen=True)
class SegmentEditStatus:
    state: SegmentEditState
    text: str
    last_event: SegmentEditEvent | None


class StaleEditError(ValueError):
    """The edit was made against a pipeline text that has since changed."""


class BlockingCheckError(ValueError):
    """A deterministic check blocks this edit without an override reason."""

    def __init__(self, segment_id: str, findings: list[dict[str, str]]) -> None:
        super().__init__(f"blocking findings for {segment_id} need an override reason")
        self.findings = findings


# -- cross-platform advisory lock on a per-log sidecar -----------------------
#
# The OS-level lock alone is not enough: on Windows, msvcrt.locking() raises
# "resource deadlock avoided" (not a block-and-wait) when a *different* handle
# in the *same process* re-locks a region it already holds, which two threads
# of one dashboard process would trigger. An in-process lock keyed by path
# ensures only one thread per process ever attempts the OS lock at a time;
# the OS lock still serializes across processes (the dashboard and the CLI).

if sys.platform == "win32":
    import msvcrt

    def _os_lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _os_unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _os_lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _os_unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_process_locks: dict[str, threading.Lock] = {}
_process_locks_guard = threading.Lock()


def _process_lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _process_locks_guard:
        lock = _process_locks.get(key)
        if lock is None:
            lock = _process_locks[key] = threading.Lock()
        return lock


def _log_path(workspace: JobWorkspace) -> Path:
    return workspace.root / LOG_RELATIVE


def _lock_path(workspace: JobWorkspace) -> Path:
    return _log_path(workspace).with_suffix(".lock")


def default_author() -> str:
    """The OS user name; the dashboard may let a reviewer override it later."""
    try:
        return getpass.getuser() or "unknown"
    except Exception:
        return "unknown"


def load_events(workspace: JobWorkspace) -> list[SegmentEditEvent]:
    """Every recorded event, oldest first. A corrupt trailing line raises, not drops."""
    path = _log_path(workspace)
    if not path.is_file():
        return []
    return _parse_events(path.read_text(encoding="utf-8"))


def _parse_events(payload: str) -> list[SegmentEditEvent]:
    """Parse a complete edit log, failing closed on any malformed line."""
    events = []
    for index, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(SegmentEditEvent.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"segment edit log is corrupt at line {index}: {error}") from error
    return events


def events_by_segment(workspace: JobWorkspace) -> dict[str, list[SegmentEditEvent]]:
    """Every segment's events, oldest first, keyed by segment id."""
    grouped: dict[str, list[SegmentEditEvent]] = {}
    for event in load_events(workspace):
        grouped.setdefault(event.segment_id, []).append(event)
    return grouped


def history_for_segment(workspace: JobWorkspace, segment_id: str) -> list[SegmentEditEvent]:
    """One segment's events, oldest first."""
    return [event for event in load_events(workspace) if event.segment_id == segment_id]


def segment_status(
    events: list[SegmentEditEvent],
    *,
    pipeline_text: str | None,
    source_text: str | None,
) -> SegmentEditStatus:
    """This segment's current state from its history and the live pipeline text.

    ``pipeline_text``/``source_text`` are ``None`` when the segment id no
    longer exists in the current validated draft / preprocessed source.
    """
    if not events:
        return SegmentEditStatus(SegmentEditState.PIPELINE, pipeline_text or "", None)
    last = events[-1]
    if last.action in (EditAction.REVERT, EditAction.TAKE_PIPELINE):
        return SegmentEditStatus(SegmentEditState.PIPELINE, pipeline_text or "", last)
    if (
        pipeline_text is None
        or source_text is None
        or sha256_text(source_text) != last.base_source_sha256
    ):
        return SegmentEditStatus(SegmentEditState.ORPHANED, last.text, last)
    if sha256_text(pipeline_text) == last.base_target_sha256:
        return SegmentEditStatus(SegmentEditState.EDITED, last.text, last)
    return SegmentEditStatus(SegmentEditState.CONFLICT, last.text, last)


def edited_segment_statuses(workspace: JobWorkspace) -> dict[str, SegmentEditStatus]:
    """Every segment with any event, keyed by id, with its live derived status.
    A passage's: the book's own items are book_agent.book_edits's."""
    grouped = {segment_id: events for segment_id, events in events_by_segment(workspace).items() if not is_book_item(segment_id)}
    if not grouped:
        return {}
    source_by_id = {
        segment.segment_id: segment.original_text
        for document in load_preprocessed_documents(workspace)
        for segment in document.segments
    }
    pipeline_by_id = {
        segment.segment_id: segment.translated_text
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }
    return {
        segment_id: segment_status(
            events,
            pipeline_text=pipeline_by_id.get(segment_id),
            source_text=source_by_id.get(segment_id),
        )
        for segment_id, events in grouped.items()
    }


def active_edit_texts(workspace: JobWorkspace) -> dict[str, str]:
    """segment_id -> text for every segment currently in state ``edited`` or ``conflict``.

    These are the edits compile overlays onto the validated draft.
    """
    return {
        segment_id: status.text
        for segment_id, status in edited_segment_statuses(workspace).items()
        if status.state in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
    }


def latest_human_texts(workspace: JobWorkspace) -> dict[str, str]:
    """segment_id -> the latest human text, for segments whose source is unchanged.

    Unlike ``active_edit_texts`` this does not need the validated draft, so a
    stage that runs before ``validate_repaired`` (including during a rerun) can
    treat a human decision as the reference (docs/BOOK_CONSISTENCY.md, 7.4).
    """
    grouped = {segment_id: events for segment_id, events in events_by_segment(workspace).items() if not is_book_item(segment_id)}
    if not grouped:
        return {}
    source_by_id = {
        segment.segment_id: segment.original_text
        for document in load_preprocessed_documents(workspace)
        for segment in document.segments
    }
    texts: dict[str, str] = {}
    for segment_id, events in grouped.items():
        last = events[-1]
        source = source_by_id.get(segment_id)
        if last.action in (EditAction.REVERT, EditAction.TAKE_PIPELINE) or source is None:
            continue
        if sha256_text(source) == last.base_source_sha256:
            texts[segment_id] = last.text
    return texts


def conflicted_segment_ids(workspace: JobWorkspace) -> set[str]:
    """Segment ids currently blocked on a conflict: the pipeline text changed under an edit."""
    return {
        segment_id
        for segment_id, status in edited_segment_statuses(workspace).items()
        if status.state is SegmentEditState.CONFLICT
    }


@dataclass(frozen=True)
class UnresolvedReviewGate:
    """What still blocks compile: review-queue segments with no active edit, plus conflicts.

    A review-queue segment counts as resolved as soon as it has an active edit
    (``edited`` or ``conflict``) — from the Text tab or from a Final review
    resolution, which both go through the same edit log. A conflict is separately
    still listed, since it needs its own unblock action even though it also
    resolved the segment's review-queue membership.
    """

    unresolved_review_ids: list[str]
    conflict_ids: list[str]
    defect_count: int
    approval_count: int

    @property
    def total_count(self) -> int:
        return len(self.unresolved_review_ids) + len(self.conflict_ids)

    def passes(self, limit: int) -> bool:
        return self.total_count <= limit


def unresolved_review_gate(
    workspace: JobWorkspace, report: RepairedValidationReport
) -> UnresolvedReviewGate:
    """The current compile gate state, computed dynamically from the edit log.

    ``report`` is the (unchanging) validate_repaired validation report; its
    ``review_segment_ids``/``defect_segment_ids``/``approval_segment_ids`` are the
    original classification and never get rewritten by a resolution.
    """
    statuses = edited_segment_statuses(workspace)
    resolved_ids = {
        segment_id
        for segment_id, status in statuses.items()
        if status.state in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
    }
    conflict_ids = sorted(
        segment_id for segment_id, status in statuses.items()
        if status.state is SegmentEditState.CONFLICT
    )
    # Drift the edits introduced, e.g. an edit in one chapter that now disagrees
    # with an unedited occurrence in another (docs/BOOK_CONSISTENCY.md, 8.2).
    consistency_ids = _edit_introduced_consistency_ids(workspace, statuses)
    unresolved_ids = sorted((set(report.review_segment_ids) | consistency_ids) - resolved_ids)
    defect_count = len(
        (set(report.defect_segment_ids) | consistency_ids) & set(unresolved_ids)
    )
    approval_count = len(set(report.approval_segment_ids) & set(unresolved_ids))
    if unresolved_ids and defect_count == 0 and approval_count == 0:
        defect_count = len(unresolved_ids)
    return UnresolvedReviewGate(unresolved_ids, conflict_ids, defect_count, approval_count)


def compiled_consistency_issues(
    workspace: JobWorkspace,
    statuses: Mapping[str, SegmentEditStatus] | None = None,
) -> list[AuditIssue]:
    """Medium-or-worse consistency findings on the text as compiled.

    That text is the validated draft with active edits applied; a human edit is
    the reference (decision 7.4), so the findings fall on unedited occurrences
    that disagree with it, or on human edits that disagree with each other.
    Empty when the checks are disabled or the validated draft is unavailable.
    """
    check = _consistency_check(workspace)
    if check is None:
        return []
    if statuses is None:
        statuses = edited_segment_statuses(workspace)
    return _draft_consistency_issues(workspace, check, _active_texts(statuses))


def _edit_introduced_consistency_ids(
    workspace: JobWorkspace, statuses: Mapping[str, SegmentEditStatus]
) -> set[str]:
    """Segments with consistency drift in the text as compiled but not in the validated draft.

    Drift in the draft itself is ``validate_repaired``'s to queue: its re-check
    put it in the validation report. Counting it again here would add book-wide
    findings to a job validated before the checks existed, on its next compile.
    """
    human_texts = _active_texts(statuses)
    if not human_texts:
        return set()  # the text as compiled is the validated draft
    check = _consistency_check(workspace)
    if check is None:
        return set()
    compiled = {
        issue.segment_id for issue in _draft_consistency_issues(workspace, check, human_texts)
    }
    if not compiled:
        return set()
    return compiled - {
        issue.segment_id for issue in _draft_consistency_issues(workspace, check, {})
    }


def _active_texts(statuses: Mapping[str, SegmentEditStatus]) -> dict[str, str]:
    return {
        segment_id: status.text
        for segment_id, status in statuses.items()
        if status.state in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
    }


@dataclass(frozen=True)
class _ConsistencyCheck:
    """What a consistency check of the validated draft depends on, besides the edits."""

    config: AppConfig
    style: StyleSheet | None
    key: tuple[str, ...]  # workspace, validated draft, config, style sheet


def _consistency_check(workspace: JobWorkspace) -> _ConsistencyCheck | None:
    """The check to run, or None when disabled or the validated draft is unavailable."""
    try:
        config_text = workspace.config_file.read_text(encoding="utf-8")
        config = AppConfig.model_validate_json(config_text)
        if not config.consistency.enabled:
            return None
        connection = connect_state(workspace.state_file)
        try:
            validated = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        finally:
            connection.close()
        if validated is None or validated["status"] != StageStatus.COMPLETED.value:
            return None
        style = enabled_style_sheet(workspace, config)
    except (FileNotFoundError, RuntimeError, ValueError):
        return None
    return _ConsistencyCheck(
        config,
        style,
        (
            str(workspace.root),
            str(validated["output_hash"]),
            sha256_text(config_text),
            sha256_text(style.model_dump_json()) if style is not None else "",
        ),
    )


# The Text tab, the review gate, and compile each ask for the same whole-book
# check several times per request; it is recomputed only when the validated
# draft, the config, the style sheet, or the active edits change.
_CONSISTENCY_CACHE_SIZE = 8
_consistency_cache: OrderedDict[tuple[str, ...], tuple[AuditIssue, ...]] = OrderedDict()
_consistency_cache_guard = threading.Lock()


def _draft_consistency_issues(
    workspace: JobWorkspace, check: _ConsistencyCheck, human_texts: Mapping[str, str]
) -> list[AuditIssue]:
    key = (*check.key, hash_active_edits(dict(human_texts)))
    with _consistency_cache_guard:
        cached = _consistency_cache.get(key)
        if cached is not None:
            _consistency_cache.move_to_end(key)
            return list(cached)
    try:
        documents = load_validated_repaired_documents(workspace)
    except (FileNotFoundError, RuntimeError, ValueError):
        return []
    segments = book_segments(
        [item.document for item in documents],
        human_texts=human_texts,
        repaired_ids={
            repair.segment_id
            for item in documents
            for repair in item.repairs
            if repair.disposition is RepairDisposition.REPAIRED
        },
    )
    config = check.config
    settings = config.consistency.settings(config.translation.direction.target_language.value)
    issues = [
        issue
        for issue in book_consistency_issues(segments, settings, check.style)
        if issue.severity.rank >= AuditSeverity.MEDIUM.rank
    ]
    with _consistency_cache_guard:
        _consistency_cache[key] = tuple(issues)
        _consistency_cache.move_to_end(key)
        while len(_consistency_cache) > _CONSISTENCY_CACHE_SIZE:
            _consistency_cache.popitem(last=False)
    return issues


def overlay_active_edits(
    documents: list[RepairedDocument], active: dict[str, str]
) -> list[RepairedDocument]:
    """Replace each segment's translated text with its active edit, if any."""
    if not active:
        return documents
    result = []
    for repaired in documents:
        segments = repaired.document.segments
        if not any(segment.segment_id in active for segment in segments):
            result.append(repaired)
            continue
        updated_document = repaired.document.model_copy(
            update={
                "segments": [
                    segment.model_copy(update={"translated_text": active[segment.segment_id]})
                    if segment.segment_id in active
                    else segment
                    for segment in segments
                ]
            }
        )
        result.append(repaired.model_copy(update={"document": updated_document}))
    return result


def hash_active_edits(active: dict[str, str]) -> str:
    """A stable hash of active edit texts, for a compile stage's input hash."""
    return hash_named_values(active) if active else ""


def active_edit_hash(workspace: JobWorkspace) -> str:
    """The current active-edit hash, recomputed from the edit log and live pipeline text."""
    return hash_active_edits(active_edit_texts(workspace))


def draft_revision_hash(validated_output_hash: str, edit_hash: str) -> str:
    """Revision reviewed by humans: validated draft plus its active edit overlay.

    Preserve the historical validated-stage hash while no edits are active so
    existing approvals and worksheets do not become stale merely by upgrading.
    """
    if not edit_hash:
        return validated_output_hash
    return hash_named_values(
        {"validated_output": validated_output_hash, "active_edits": edit_hash}
    )


def current_draft_revision(workspace: JobWorkspace, validated_output_hash: str) -> str:
    """Return the current human-review revision for a validated stage output."""
    return draft_revision_hash(validated_output_hash, active_edit_hash(workspace))


# Categories no reviewer reason can waive, here and in Final review
# (docs/FULL_TEXT_REVIEW.md): mechanically checkable defects, not judgment calls.
NON_OVERRIDABLE_CATEGORIES = {
    AuditCategory.STRUCTURE,
    AuditCategory.EMPTY,
    AuditCategory.UNTRANSLATED,
    AuditCategory.DUPLICATION,
    AuditCategory.PUNCTUATION,
}


@dataclass(frozen=True)
class _CheckContext:
    """Everything the deterministic check reads, loaded once for any number of segments."""

    config: AppConfig
    drafts: dict[str, RepairedDocument]
    owners: dict[str, str]
    sources: dict[str, Any]
    initial_audits: dict[str, Any]
    rulings: Any


def _load_check_context(workspace: JobWorkspace) -> _CheckContext:
    config = AppConfig.model_validate_json(
        workspace.config_file.read_text(encoding="utf-8")
    )
    drafts = {
        item.document.manifest_id: item for item in load_validated_repaired_documents(workspace)
    }
    return _CheckContext(
        config=config,
        drafts=drafts,
        owners={
            segment.segment_id: document_id
            for document_id, item in drafts.items()
            for segment in item.document.segments
        },
        sources={item.manifest_id: item for item in load_preprocessed_documents(workspace)},
        initial_audits={item.document_id: item for item in load_document_audits(workspace)},
        rulings=rule_numeric_findings(workspace, config, None, []),
    )


def _chapter_issues(
    context: _CheckContext,
    document_id: str,
    replacements: Mapping[str, str],
    segment_ids: set[str],
) -> list[AuditIssue]:
    """Audit one chapter of the validated draft with ``replacements`` applied.

    Returns the medium-or-worse issues on ``segment_ids``, in audit order.
    """
    document = context.drafts[document_id].document
    if replacements:
        document = document.model_copy(
            update={
                "segments": [
                    item.model_copy(update={"translated_text": replacements[item.segment_id]})
                    if item.segment_id in replacements
                    else item
                    for item in document.segments
                ]
            }
        )
    audit = reapply_quantity_adjudications(
        audit_translated_document(
            context.sources[document_id],
            document,
            context.config.audit,
            context.rulings,
        ),
        context.initial_audits.get(document_id),
    )
    return [
        issue
        for issue in audit.issues
        if issue.segment_id in segment_ids
        and issue.severity.rank >= AuditSeverity.MEDIUM.rank
    ]


def _proposal_consistency_issues(
    workspace: JobWorkspace, context: _CheckContext, proposed: Mapping[str, str]
) -> list[AuditIssue]:
    """Book-level consistency findings on proposed human texts (overridable).

    A proposal is a human edit, and a human edit is the reference for the
    other occurrences (decision 7.4), so it is flagged only where it disagrees
    with another human edit, or breaks the book's punctuation conventions.
    Unedited occurrences it now differs from are queued at compile instead.
    """
    if not proposed or not context.config.consistency.enabled:
        return []
    human_texts = {
        segment_id: text
        for segment_id, text in active_edit_texts(workspace).items()
        if segment_id not in proposed
    }
    human_texts.update(proposed)
    segments = book_segments(
        [item.document for item in context.drafts.values()],
        human_texts=human_texts,
        repaired_ids={
            repair.segment_id
            for item in context.drafts.values()
            for repair in item.repairs
            if repair.disposition is RepairDisposition.REPAIRED
        },
    )
    settings = context.config.consistency.settings(
        context.config.translation.direction.target_language.value
    )
    issues = book_consistency_issues(segments, settings)
    # A proposed edit is human text, so book_consistency_issues skips it for
    # the conventions and the style sheet; check it here, overridable like the rest.
    if settings.conventions:
        issues += convention_issues(segments, settings)
    style = enabled_style_sheet(workspace, context.config)
    if style is not None:
        issues += expression_issues(
            ((s.segment_id, s.source, s.target) for s in segments if s.segment_id in proposed), style
        )
    return [
        issue
        for issue in issues
        if issue.segment_id in proposed and issue.severity.rank >= AuditSeverity.MEDIUM.rank
    ]


def _finding(issue: AuditIssue) -> dict[str, str]:
    return {
        "category": issue.category.value,
        "severity": issue.severity.value,
        "message": issue.message,
    }


def preview_manual_resolution(
    workspace: JobWorkspace,
    segment_id: str,
    translated_text: str | None,
) -> list[dict[str, str]]:
    """Return the deterministic issues that would block one proposed resolution.

    ``translated_text=None`` previews an acceptance of the current translation,
    which overrides overridable findings exactly as an ``edit`` event's check does.
    Nothing is written.
    """
    context = _load_check_context(workspace)
    document_id = context.owners.get(segment_id)
    if document_id is None:
        raise ValueError(f"segment is absent from the validated draft: {segment_id}")
    accepting = translated_text is None
    replacements = {} if accepting else {segment_id: translated_text}
    return [
        _finding(issue)
        for issue in [
            *_chapter_issues(context, document_id, replacements, {segment_id}),
            *_proposal_consistency_issues(workspace, context, replacements),
        ]
        if not (accepting and issue.category not in NON_OVERRIDABLE_CATEGORIES)
    ]


def check_edits(
    workspace: JobWorkspace, proposed: Mapping[str, str]
) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Check several proposed texts together, split like ``classify_check``.

    Each affected chapter is audited once, as the validated draft with every
    proposed text in it applied, so a batch is checked as the draft it would
    produce. For a single proposal this is exactly ``classify_check``.
    """
    if not proposed:
        return {}
    context = _load_check_context(workspace)
    missing = sorted(segment_id for segment_id in proposed if segment_id not in context.owners)
    if missing:
        raise ValueError(f"segment is absent from the validated draft: {missing[0]}")
    by_document: dict[str, dict[str, str]] = {}
    for segment_id, text in proposed.items():
        by_document.setdefault(context.owners[segment_id], {})[segment_id] = text
    result: dict[str, dict[str, list[dict[str, str]]]] = {
        segment_id: {"hard": [], "overridable": []} for segment_id in proposed
    }
    for document_id, replacements in by_document.items():
        for issue in _chapter_issues(context, document_id, replacements, set(replacements)):
            bucket = "hard" if issue.category in NON_OVERRIDABLE_CATEGORIES else "overridable"
            result[issue.segment_id][bucket].append(_finding(issue))
    # The whole batch counts as human text at once, so an import is checked as one set.
    for issue in _proposal_consistency_issues(workspace, context, proposed):
        result[issue.segment_id]["overridable"].append(_finding(issue))
    return result


def check_edit(workspace: JobWorkspace, segment_id: str, text: str | None) -> list[dict[str, str]]:
    """The deterministic findings an edit (or ``text=None`` acceptance) would raise."""
    return preview_manual_resolution(workspace, segment_id, text)


_HARD_BLOCK_CATEGORIES = {category.value for category in NON_OVERRIDABLE_CATEGORIES}


def _split_blocking(findings: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(hard, overridable): hard findings are mechanically checkable defects no reason waives."""
    hard = [f for f in findings if f["category"] in _HARD_BLOCK_CATEGORIES]
    overridable = [f for f in findings if f["category"] not in _HARD_BLOCK_CATEGORIES]
    return hard, overridable


def classify_check(workspace: JobWorkspace, segment_id: str, text: str | None) -> dict[str, list[dict[str, str]]]:
    """The check preview an editor shows: findings split by whether a reason can waive them."""
    hard, overridable = _split_blocking(check_edit(workspace, segment_id, text))
    return {"hard": hard, "overridable": overridable}


_BookTexts = tuple[dict[str, tuple[str, str]], dict[str, str]]


def _book_texts(workspace: JobWorkspace) -> _BookTexts:
    """(segment id -> (document id, source text), segment id -> validated-draft text)."""
    sources = {
        segment.segment_id: (document.manifest_id, segment.original_text)
        for document in load_preprocessed_documents(workspace)
        for segment in document.segments
    }
    pipeline = {
        segment.segment_id: segment.translated_text
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }
    return sources, pipeline


def _context_from(texts: _BookTexts, segment_id: str) -> tuple[str, str, str]:
    sources, pipeline = texts
    if segment_id not in sources:
        raise ValueError(f"no such segment: {segment_id}")
    document_id, source_text = sources[segment_id]
    if segment_id not in pipeline:
        raise ValueError(f"segment is absent from the validated draft: {segment_id}")
    return document_id, source_text, pipeline[segment_id]


def _segment_context(workspace: JobWorkspace, segment_id: str) -> tuple[str, str, str]:
    """The owning document id, current source text, and current validated-draft text."""
    return _context_from(_book_texts(workspace), segment_id)


def _last_event_id(events: Sequence[SegmentEditEvent], segment_id: str) -> str:
    return next(
        (event.event_id for event in reversed(events) if event.segment_id == segment_id),
        "",
    )


def _assert_expected_event(
    events: Sequence[SegmentEditEvent], segment_id: str, expected_event_id: str
) -> None:
    current = _last_event_id(events, segment_id)
    if current != expected_event_id:
        raise StaleEditError(
            f"{segment_id} was edited after this page was loaded; reload the segment"
        )


@dataclass(frozen=True)
class SegmentEditRequest:
    """One checked edit to append, optionally guarded by a caller's revision."""

    segment_id: str
    text: str
    reason: str
    base_target_sha256: str
    override_reason: str = ""
    author: str | None = None
    expected_event_id: str | None = None


@dataclass(frozen=True)
class _PreparedEdit:
    request: SegmentEditRequest
    document_id: str
    source_text: str
    pipeline_text: str
    overrides: list[str]
    expected_event_id: str


def _check_request(request: SegmentEditRequest, texts: _BookTexts) -> tuple[str, str, str]:
    """Reason and staleness gates; returns the segment's (document, source, pipeline) texts."""
    if len(request.reason.strip()) < 3:
        raise ValueError("an edit needs a reason of at least 3 characters")
    document_id, source_text, pipeline_text = _context_from(texts, request.segment_id)
    if sha256_text(pipeline_text) != request.base_target_sha256:
        raise StaleEditError(
            f"{request.segment_id} has changed since this edit was based on it; "
            "reload the segment"
        )
    return document_id, source_text, pipeline_text


def _prepare_edit(
    request: SegmentEditRequest,
    context: tuple[str, str, str],
    findings: dict[str, list[dict[str, str]]],
    current_events: Sequence[SegmentEditEvent],
) -> _PreparedEdit:
    document_id, source_text, pipeline_text = context
    hard, overridable = findings["hard"], findings["overridable"]
    if hard:
        raise BlockingCheckError(request.segment_id, hard)
    overrides: list[str] = []
    if overridable:
        if len(request.override_reason.strip()) < 3:
            raise BlockingCheckError(request.segment_id, overridable)
        overrides = [issue["message"] for issue in overridable]
    expected = (
        request.expected_event_id
        if request.expected_event_id is not None
        else _last_event_id(current_events, request.segment_id)
    )
    return _PreparedEdit(
        request=request,
        document_id=document_id,
        source_text=source_text,
        pipeline_text=pipeline_text,
        overrides=overrides,
        expected_event_id=expected,
    )


def apply_edit_batch(
    workspace: JobWorkspace, requests: Sequence[SegmentEditRequest]
) -> list[SegmentEditEvent]:
    """Validate every edit, then append the complete batch under one lock.

    No event is written unless every deterministic and optimistic-concurrency
    check passes. The batch is checked as the draft it would produce: each
    affected chapter is audited once with every edit in it applied
    (``check_edits``), which for a single edit is the ordinary per-edit check.
    The log bytes are emitted in one write so readers observe the complete
    batch or fail closed on a corrupt trailing write.
    """
    if not requests:
        return []
    segment_ids = [request.segment_id for request in requests]
    if len(segment_ids) != len(set(segment_ids)):
        raise ValueError("an edit batch cannot contain duplicate segment IDs")
    before = load_events(workspace)
    texts = _book_texts(workspace)
    contexts = [_check_request(request, texts) for request in requests]
    findings = check_edits(workspace, {request.segment_id: request.text for request in requests})
    prepared = [
        _prepare_edit(request, context, findings[request.segment_id], before)
        for request, context in zip(requests, contexts)
    ]
    path = _log_path(workspace)
    lock_path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock_for(lock_path), open(lock_path, "a+b") as handle:
        _os_lock(handle)
        try:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            locked_events = _parse_events(existing)
            locked_texts = _book_texts(workspace)
            for item in prepared:
                _assert_expected_event(
                    locked_events,
                    item.request.segment_id,
                    item.expected_event_id,
                )
                document_id, source_text, pipeline_text = _context_from(
                    locked_texts, item.request.segment_id
                )
                if (
                    document_id != item.document_id
                    or source_text != item.source_text
                    or pipeline_text != item.pipeline_text
                ):
                    raise StaleEditError(
                        f"{item.request.segment_id} changed while the edit was being "
                        "checked; reload the segment"
                    )
            new_events: list[SegmentEditEvent] = []
            starting_count = len(locked_events)
            for offset, item in enumerate(prepared, start=1):
                request = item.request
                segment_events = [
                    event
                    for event in locked_events
                    if event.segment_id == request.segment_id
                ]
                previous = segment_status(
                    segment_events,
                    pipeline_text=item.pipeline_text,
                    source_text=item.source_text,
                ).text
                event = SegmentEditEvent(
                    event_id=f"E{starting_count + offset:06d}",
                    at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    author=request.author or default_author(),
                    segment_id=request.segment_id,
                    document_id=item.document_id,
                    action=EditAction.EDIT,
                    text=request.text,
                    previous_text=previous,
                    base_source_sha256=sha256_text(item.source_text),
                    base_target_sha256=request.base_target_sha256,
                    base_target_text=item.pipeline_text,
                    reason=request.reason.strip(),
                    overrides=item.overrides,
                )
                new_events.append(event)
                locked_events.append(event)
            payload = "".join(event.model_dump_json() + "\n" for event in new_events)
            atomic_write_text(path, existing + payload)
            return new_events
        finally:
            _os_unlock(handle)


def apply_edit(
    workspace: JobWorkspace,
    *,
    segment_id: str,
    text: str,
    reason: str,
    base_target_sha256: str,
    override_reason: str = "",
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Append a tracked edit, after a staleness gate and a deterministic-check gate."""
    return apply_edit_batch(
        workspace,
        [
            SegmentEditRequest(
                segment_id=segment_id,
                text=text,
                reason=reason,
                base_target_sha256=base_target_sha256,
                override_reason=override_reason,
                author=author,
                expected_event_id=expected_event_id,
            )
        ],
    )[0]


def _append_action_event(
    workspace: JobWorkspace,
    *,
    segment_id: str,
    expected_event_id: str | None,
    build: Callable[[int, list[SegmentEditEvent]], SegmentEditEvent],
) -> SegmentEditEvent:
    """Append one non-edit action with its state check inside the log lock."""
    before = load_events(workspace)
    expected = (
        expected_event_id
        if expected_event_id is not None
        else _last_event_id(before, segment_id)
    )
    path = _log_path(workspace)
    lock_path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock_for(lock_path), open(lock_path, "a+b") as handle:
        _os_lock(handle)
        try:
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            locked_events = _parse_events(existing)
            _assert_expected_event(locked_events, segment_id, expected)
            segment_events = [
                event for event in locked_events if event.segment_id == segment_id
            ]
            event = build(len(locked_events) + 1, segment_events)
            atomic_write_text(path, existing + event.model_dump_json() + "\n")
            return event
        finally:
            _os_unlock(handle)


def apply_revert(
    workspace: JobWorkspace,
    *,
    segment_id: str,
    reason: str,
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Append a revert, dropping back to the current pipeline text."""
    if len(reason.strip()) < 3:
        raise ValueError("a revert needs a reason of at least 3 characters")
    document_id, source_text, pipeline_text = _segment_context(workspace, segment_id)
    def build(count: int, events: list[SegmentEditEvent]) -> SegmentEditEvent:
        status = segment_status(
            events, pipeline_text=pipeline_text, source_text=source_text
        )
        if status.state not in (SegmentEditState.EDITED, SegmentEditState.CONFLICT):
            raise ValueError(f"segment has no active edit to revert: {segment_id}")
        return SegmentEditEvent(
            event_id=f"E{count:06d}",
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
            author=author or default_author(),
            segment_id=segment_id,
            document_id=document_id,
            action=EditAction.REVERT,
            text="",
            previous_text=status.text,
            base_source_sha256=sha256_text(source_text),
            base_target_sha256=sha256_text(pipeline_text),
            base_target_text=pipeline_text,
            reason=reason.strip(),
            overrides=[],
        )

    return _append_action_event(
        workspace,
        segment_id=segment_id,
        expected_event_id=expected_event_id,
        build=build,
    )


def apply_keep(
    workspace: JobWorkspace,
    *,
    segment_id: str,
    reason: str,
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Unblock a conflict: keep the edit, re-based against the new pipeline text."""
    if len(reason.strip()) < 3:
        raise ValueError("keeping an edit needs a reason of at least 3 characters")
    document_id, source_text, pipeline_text = _segment_context(workspace, segment_id)
    def build(count: int, events: list[SegmentEditEvent]) -> SegmentEditEvent:
        status = segment_status(
            events, pipeline_text=pipeline_text, source_text=source_text
        )
        if status.state is not SegmentEditState.CONFLICT:
            raise ValueError(f"segment is not a conflict: {segment_id}")
        return SegmentEditEvent(
            event_id=f"E{count:06d}",
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
            author=author or default_author(),
            segment_id=segment_id,
            document_id=document_id,
            action=EditAction.KEEP,
            text=status.text,
            previous_text=status.text,
            base_source_sha256=sha256_text(source_text),
            base_target_sha256=sha256_text(pipeline_text),
            base_target_text=pipeline_text,
            reason=reason.strip(),
        )

    return _append_action_event(
        workspace,
        segment_id=segment_id,
        expected_event_id=expected_event_id,
        build=build,
    )


def apply_take_pipeline(
    workspace: JobWorkspace,
    *,
    segment_id: str,
    reason: str,
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Unblock a conflict: drop the edit and take the new pipeline text."""
    if len(reason.strip()) < 3:
        raise ValueError("taking the pipeline text needs a reason of at least 3 characters")
    document_id, source_text, pipeline_text = _segment_context(workspace, segment_id)
    def build(count: int, events: list[SegmentEditEvent]) -> SegmentEditEvent:
        status = segment_status(
            events, pipeline_text=pipeline_text, source_text=source_text
        )
        if status.state is not SegmentEditState.CONFLICT:
            raise ValueError(f"segment is not a conflict: {segment_id}")
        return SegmentEditEvent(
            event_id=f"E{count:06d}",
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
            author=author or default_author(),
            segment_id=segment_id,
            document_id=document_id,
            action=EditAction.TAKE_PIPELINE,
            text="",
            previous_text=status.text,
            base_source_sha256=sha256_text(source_text),
            base_target_sha256=sha256_text(pipeline_text),
            base_target_text=pipeline_text,
            reason=reason.strip(),
        )

    return _append_action_event(
        workspace,
        segment_id=segment_id,
        expected_event_id=expected_event_id,
        build=build,
    )
