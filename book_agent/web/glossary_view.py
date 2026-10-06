"""Glossary gate data for the browser: draft, LLM decisions, and evidence text."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_text
from ..config import AppConfig
from ..languages import glossary_language_names, glossary_pair
from ..pipeline_state import WorkflowStage
from ..schemas import GlossaryApprovalRecord, GlossaryCategory, GlossaryResult
from ..style_sheet import StyleSheet, address_choices, check_style_choices, pronoun_choices
from ..stages.decompile import load_decompile_manifest
from ..state import connect_state, get_job_metadata, get_stage_status
from ..workspace import JobWorkspace

REVIEW_DIR = "glossary/ui-reviews"


def _metadata_json(workspace: JobWorkspace, connection, key: str) -> Any:
    relative = get_job_metadata(connection, key)
    if not relative:
        return None
    path = workspace.directory(relative)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _evidence_text(workspace: JobWorkspace, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        manifest = load_decompile_manifest(workspace)
    except (FileNotFoundError, ValueError):
        return {}
    return {
        segment.segment_id: segment.text
        for document in manifest.documents
        for segment in document.segments
        if segment.segment_id in ids
    }


def _config(workspace: JobWorkspace) -> AppConfig | None:
    try:
        return AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _entries(data: Any) -> list[dict[str, Any]]:
    """A stored glossary's entries with source/target sides (older files say english/chinese)."""
    if not data:
        return []
    return GlossaryResult.model_validate(data).model_dump(mode="json")["entries"]


def glossary_payload(workspace: JobWorkspace) -> dict[str, Any]:
    """Everything the glossary page needs, keyed on the approval stage state."""
    config = _config(workspace)
    connection = connect_state(workspace.state_file)
    try:
        resolve = get_stage_status(connection, WorkflowStage.RESOLVE_GLOSSARY.value)
        approve = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
        draft = _metadata_json(workspace, connection, "glossary_draft")
        approved = _metadata_json(workspace, connection, "glossary_approved")
        report = _metadata_json(workspace, connection, "glossary_approval_report")
        quality = _metadata_json(workspace, connection, "glossary_draft_quality_report")
        review_mode = get_job_metadata(connection, "glossary_review_mode") or ""
        style_draft = _metadata_json(workspace, connection, "style_draft")
        style_approved = _metadata_json(workspace, connection, "style_approved")
    finally:
        connection.close()
    approve_status = str(approve["status"]) if approve else "pending"
    entries = _entries(approved if approve_status == "completed" and approved else draft)
    ids = {item for entry in entries for item in entry.get("evidence", [])}
    direction = config.translation.direction if config is not None else None
    return {
        "ready": bool(resolve and resolve["status"] == "completed" and draft),
        "approve_status": approve_status,
        "approve_message": str(approve["message"]) if approve else "",
        "editable": approve_status in {"paused", "pending", "failed"} and bool(draft),
        # Metadata from an earlier approval outlives a reset gate; only report it when current.
        "review_mode": review_mode if approve_status == "completed" else "",
        "entries": entries,
        "draft_entries": _entries(draft),
        # The glossary's pair and the language of each side, for column labels.
        "glossary_pair": glossary_language_names(glossary_pair(direction)) if direction else None,
        "approval_records": [
            GlossaryApprovalRecord.model_validate(record).model_dump(mode="json")
            for record in (report or {}).get("records", [])
        ],
        "approval_summary": {k: v for k, v in (report or {}).items() if k != "records"},
        "quality": quality or {},
        "evidence": _evidence_text(workspace, ids),
        "categories": [category.value for category in GlossaryCategory],
        # Stored values stay as the schema defines them; the UI shows English names.
        "category_labels": {category.value: category.name.title() for category in GlossaryCategory},
        "other_category": GlossaryCategory.OTHER.value,
        # The book style sheet (docs/BOOK_CONSISTENCY.md, phase 2), when one was extracted.
        "style_sheet": {
            "draft": style_draft,
            "approved": style_approved if approve_status == "completed" else None,
            # "human": the gate waits for a person, so approving always sends the sheet.
            "review": _style_review(workspace),
            "pronouns": list(pronoun_choices(direction)) if direction else [],
            "addresses": list(address_choices(direction)) if direction else [],
        },
    }


def _style_review(workspace: JobWorkspace) -> str:
    config = _config(workspace)
    return config.consistency.style_sheet.review if config is not None else "human"


def write_reviewed_style_sheet(workspace: JobWorkspace, style: dict[str, Any]) -> Path:
    """Validate a reviewer's style-sheet edits and store them in the job."""
    sheet = StyleSheet.model_validate(style)
    config = _config(workspace)
    if config is not None:
        check_style_choices(sheet, config.translation.direction)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = workspace.directory(f"{REVIEW_DIR}/style.reviewed-{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, sheet.model_dump_json(indent=2))
    return path


def write_reviewed_glossary(workspace: JobWorkspace, entries: list[dict[str, Any]]) -> Path:
    """Validate reviewer edits with the canonical schema and store them in the job."""
    config = _config(workspace)
    pair = glossary_pair(config.translation.direction) if config is not None else None
    result = GlossaryResult.model_validate({"pair": pair, "entries": entries})
    if not result.entries:
        raise ValueError("the reviewed glossary is empty; keep at least one entry")
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = workspace.directory(f"{REVIEW_DIR}/glossary.reviewed-{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, result.model_dump_json(indent=2))
    return path
