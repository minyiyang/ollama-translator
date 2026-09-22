"""Book outline and paired source/translation view for the Text tab.

The whole book, source and translation side by side, using whichever
translation stage has produced output (docs/FULL_TEXT_REVIEW.md, phase 1).
Once ``validate_repaired`` completes, the view is editable: a segment's
``state`` and ``text`` then reflect its tracked edit history
(``book_agent.text_edits``) rather than always mirroring ``pipeline_text``.
"""

from __future__ import annotations

import json
from typing import Any

from ..audit import AuditSeverity, DocumentAudit
from ..hashing import sha256_text
from ..pipeline_state import WorkflowStage
from ..repair import RepairedDocumentValidation
from ..state import StageStatus, connect_state, get_job_metadata, get_stage_status
from ..text_edits import (
    SegmentEditEvent,
    SegmentEditState,
    active_edit_texts,
    active_edit_hash,
    events_by_segment,
    segment_status,
    unresolved_review_gate,
)
from ..translation import TranslatedDocument
from ..workspace import JobWorkspace
from ..stages.audit import load_document_audits
from ..stages.decompile import load_decompile_manifest
from ..stages.preprocess import load_preprocessed_documents
from ..stages.repair import load_repaired_documents
from ..stages.reprose import load_reprosed_documents
from ..stages.translate import load_translated_documents
from ..stages.validate_repaired import (
    load_repaired_document_validations,
    load_repaired_validation_report,
    load_validated_repaired_documents,
)


def _safe_load(loader: Any, workspace: JobWorkspace) -> list[Any]:
    try:
        return loader(workspace)
    except (FileNotFoundError, RuntimeError, ValueError):
        return []


def _latest_translated_documents(
    workspace: JobWorkspace,
) -> dict[str, TranslatedDocument]:
    """The most recent translation per document: validated, else the latest stage that ran."""
    for loader in (
        load_validated_repaired_documents,
        load_reprosed_documents,
        load_repaired_documents,
    ):
        items = _safe_load(loader, workspace)
        if items:
            return {item.document.manifest_id: item.document for item in items}
    return {item.manifest_id: item for item in _safe_load(load_translated_documents, workspace)}


def _document_findings(
    document_id: str,
    validations: dict[str, RepairedDocumentValidation],
    initial_audits: dict[str, DocumentAudit],
) -> dict[str, list[dict[str, str]]]:
    """Blocking-and-up findings per segment: the final audit if it ran, else the initial one.

    A segment can also reach the review queue on a failed semantic verification with a
    clean deterministic audit (approval-required); those are reported too.
    """
    validation = validations.get(document_id)
    audit = validation.deterministic_audit if validation is not None else initial_audits.get(document_id)
    findings: dict[str, list[dict[str, str]]] = {}
    if audit is not None:
        for issue in audit.issues:
            if issue.severity.rank < AuditSeverity.MEDIUM.rank:
                continue
            findings.setdefault(issue.segment_id, []).append(
                {
                    "category": issue.category.value,
                    "severity": issue.severity.value,
                    "message": issue.message,
                }
            )
    if validation is not None:
        for verification in validation.semantic_verifications:
            if verification.passed:
                continue
            findings.setdefault(verification.segment_id, []).append(
                {
                    "category": "verification",
                    "severity": "high",
                    "message": verification.message,
                }
            )
    return findings


def _last_edit_summary(event: SegmentEditEvent | None) -> dict[str, str] | None:
    if event is None:
        return None
    return {
        "action": event.action.value,
        "author": event.author,
        "at": event.at,
        "reason": event.reason,
        # For a conflict, the text the active edit was based on: the pipeline text
        # before it changed under the edit.
        "based_on": event.base_target_text or event.previous_text,
    }


def text_outline(workspace: JobWorkspace) -> dict[str, Any]:
    """Chapters in book order with segment, flagged, and review-queue counts."""
    connection = connect_state(workspace.state_file)
    try:
        translate_stage = get_stage_status(connection, WorkflowStage.TRANSLATE.value)
        validate_stage = get_stage_status(
            connection, WorkflowStage.VALIDATE_REPAIRED.value
        )
        compile_stage = get_stage_status(connection, WorkflowStage.COMPILE.value)
        compiled = bool(
            compile_stage and compile_stage["status"] == StageStatus.COMPLETED.value
        )
        compiled_edit_hash = (
            get_job_metadata(connection, "compiled_active_edit_hash") if compiled else None
        )
        compiled_edits_json = (
            get_job_metadata(connection, "compiled_active_edits") if compiled else None
        )
    finally:
        connection.close()
    available = bool(
        translate_stage and translate_stage["status"] == StageStatus.COMPLETED.value
    )
    editable = bool(
        validate_stage and validate_stage["status"] == StageStatus.COMPLETED.value
    )
    empty_totals = {
        "documents": 0,
        "segments": 0,
        "flagged": 0,
        "in_review_queue": 0,
        "edited": 0,
        "conflicts": 0,
    }
    if not available:
        return {
            "available": False,
            "editable": False,
            "chapters": [],
            "totals": empty_totals,
            "uncompiled_edit_count": 0,
        }

    manifest = load_decompile_manifest(workspace, connection=None)
    chapters_by_id = {item.manifest_id: item for item in manifest.documents}
    sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
    translations = _latest_translated_documents(workspace)
    validations = {
        item.document_id: item
        for item in _safe_load(load_repaired_document_validations, workspace)
    }
    initial_audits = {
        item.document_id: item for item in _safe_load(load_document_audits, workspace)
    }
    try:
        review_queue_ids = set(
            unresolved_review_gate(workspace, load_repaired_validation_report(workspace)).unresolved_review_ids
        )
    except (FileNotFoundError, RuntimeError, ValueError):
        review_queue_ids = set()
    # Edits are only possible once validate_repaired has completed (they are made
    # against its draft), so there is nothing to look up before then.
    edit_events = events_by_segment(workspace) if editable else {}

    chapters: list[dict[str, Any]] = []
    for document_id, source in sources.items():
        translation = translations.get(document_id)
        if translation is None:
            continue
        chapter = chapters_by_id.get(document_id)
        segment_ids = [segment.segment_id for segment in source.segments]
        findings = _document_findings(document_id, validations, initial_audits)
        queue_count = sum(1 for segment_id in segment_ids if segment_id in review_queue_ids)
        translated_by_id = {segment.segment_id: segment.translated_text for segment in translation.segments}
        edited_count = 0
        conflict_count = 0
        active_ids: set[str] = set()
        for segment in source.segments:
            events = edit_events.get(segment.segment_id)
            if not events:
                continue
            state = segment_status(
                events,
                pipeline_text=translated_by_id.get(segment.segment_id),
                source_text=segment.original_text,
            ).state
            if state is SegmentEditState.EDITED:
                edited_count += 1
                active_ids.add(segment.segment_id)
            elif state is SegmentEditState.CONFLICT:
                conflict_count += 1
                active_ids.add(segment.segment_id)
        # A segment with an active edit has been handled, so it no longer counts
        # as flagged even though the original audit finding is still on record.
        flagged_count = sum(
            1 for segment_id in segment_ids
            if segment_id in findings and segment_id not in active_ids
        )
        chapters.append(
            {
                "document_id": document_id,
                "order": source.order,
                "title": chapter.title if chapter is not None else document_id,
                "segment_count": len(segment_ids),
                "flagged_count": flagged_count,
                "in_review_queue_count": queue_count,
                "edited_count": edited_count,
                "conflict_count": conflict_count,
            }
        )
    chapters.sort(key=lambda item: item["order"])
    totals = {
        "documents": len(chapters),
        "segments": sum(item["segment_count"] for item in chapters),
        "flagged": sum(item["flagged_count"] for item in chapters),
        "in_review_queue": sum(item["in_review_queue_count"] for item in chapters),
        "edited": sum(item["edited_count"] for item in chapters),
        "conflicts": sum(item["conflict_count"] for item in chapters),
    }
    uncompiled_edit_count = 0
    current_edits = active_edit_texts(workspace) if editable else {}
    if compiled_edits_json is not None:
        compiled_edits = {
            str(key): str(value)
            for key, value in json.loads(compiled_edits_json).items()
        }
        uncompiled_edit_count = sum(
            current_edits.get(segment_id) != compiled_edits.get(segment_id)
            for segment_id in set(current_edits) | set(compiled_edits)
        )
    elif (
        compiled_edit_hash is not None
        and active_edit_hash(workspace) != compiled_edit_hash
    ):
        # Legacy compiled jobs have only a global hash, so an exact delta is
        # unavailable until their next compile stores the per-segment snapshot.
        uncompiled_edit_count = len(current_edits)
    return {
        "available": True,
        "editable": editable,
        "chapters": chapters,
        "totals": totals,
        "uncompiled_edit_count": uncompiled_edit_count,
    }


def text_chapter(workspace: JobWorkspace, document_id: str) -> dict[str, Any]:
    """One chapter's segments, source and translation paired, oldest translation stage last."""
    if not document_id:
        raise ValueError("document_id is required")
    sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
    source = sources.get(document_id)
    if source is None:
        raise ValueError(f"no such document: {document_id}")
    translations = _latest_translated_documents(workspace)
    translation = translations.get(document_id)
    manifest = load_decompile_manifest(workspace)
    chapter = next((item for item in manifest.documents if item.manifest_id == document_id), None)
    validations = {
        item.document_id: item
        for item in _safe_load(load_repaired_document_validations, workspace)
    }
    initial_audits = {
        item.document_id: item for item in _safe_load(load_document_audits, workspace)
    }
    findings = _document_findings(document_id, validations, initial_audits)
    try:
        review_queue_ids = set(
            unresolved_review_gate(workspace, load_repaired_validation_report(workspace)).unresolved_review_ids
        )
    except (FileNotFoundError, RuntimeError, ValueError):
        review_queue_ids = set()

    connection = connect_state(workspace.state_file)
    try:
        validate_stage = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
    finally:
        connection.close()
    editable = bool(validate_stage and validate_stage["status"] == StageStatus.COMPLETED.value)
    edit_events = events_by_segment(workspace) if editable else {}

    translated_by_id = (
        {segment.segment_id: segment.translated_text for segment in translation.segments}
        if translation is not None
        else {}
    )
    segments = []
    for segment in source.segments:
        pipeline_text = translated_by_id.get(segment.segment_id, "")
        status = segment_status(
            edit_events.get(segment.segment_id, []),
            pipeline_text=pipeline_text,
            source_text=segment.original_text,
        )
        flagged = (
            segment.segment_id in findings
            and status.state not in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
        )
        segments.append(
            {
                "segment_id": segment.segment_id,
                "source": segment.original_text,
                "pipeline_text": pipeline_text,
                "text": status.text,
                "state": status.state.value,
                "edit_revision": status.last_event.event_id if status.last_event else "",
                "base_target_sha256": sha256_text(pipeline_text),
                "findings": findings.get(segment.segment_id, []),
                "flagged": flagged,
                "in_review_queue": segment.segment_id in review_queue_ids,
                "last_edit": _last_edit_summary(status.last_event),
            }
        )
    return {
        "document_id": document_id,
        "title": chapter.title if chapter is not None else document_id,
        "order": source.order,
        "editable": editable,
        "segments": segments,
    }
