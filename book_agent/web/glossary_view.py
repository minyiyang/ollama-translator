"""Glossary gate data for the browser: draft, LLM decisions, and evidence text."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_text
from ..pipeline_state import WorkflowStage
from ..schemas import GlossaryCategory, GlossaryResult
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


def glossary_payload(workspace: JobWorkspace) -> dict[str, Any]:
    """Everything the glossary page needs, keyed on the approval stage state."""
    connection = connect_state(workspace.state_file)
    try:
        resolve = get_stage_status(connection, WorkflowStage.RESOLVE_GLOSSARY.value)
        approve = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
        draft = _metadata_json(workspace, connection, "glossary_draft")
        approved = _metadata_json(workspace, connection, "glossary_approved")
        report = _metadata_json(workspace, connection, "glossary_approval_report")
        quality = _metadata_json(workspace, connection, "glossary_draft_quality_report")
        review_mode = get_job_metadata(connection, "glossary_review_mode") or ""
    finally:
        connection.close()
    approve_status = str(approve["status"]) if approve else "pending"
    entries = (approved if approve_status == "completed" and approved else draft) or {"entries": []}
    ids = {item for entry in entries["entries"] for item in entry.get("evidence", [])}
    return {
        "ready": bool(resolve and resolve["status"] == "completed" and draft),
        "approve_status": approve_status,
        "approve_message": str(approve["message"]) if approve else "",
        "editable": approve_status in {"paused", "pending", "failed"} and bool(draft),
        # Metadata from an earlier approval outlives a reset gate; only report it when current.
        "review_mode": review_mode if approve_status == "completed" else "",
        "entries": entries["entries"],
        "draft_entries": (draft or {"entries": []})["entries"],
        "approval_records": (report or {}).get("records", []),
        "approval_summary": {k: v for k, v in (report or {}).items() if k != "records"},
        "quality": quality or {},
        "evidence": _evidence_text(workspace, ids),
        "categories": [category.value for category in GlossaryCategory],
        # Stored values stay as the schema defines them; the UI shows English names.
        "category_labels": {category.value: category.name.title() for category in GlossaryCategory},
        "other_category": GlossaryCategory.OTHER.value,
    }


def write_reviewed_glossary(workspace: JobWorkspace, entries: list[dict[str, Any]]) -> Path:
    """Validate reviewer edits with the canonical schema and store them in the job."""
    result = GlossaryResult.model_validate({"entries": entries})
    if not result.entries:
        raise ValueError("the reviewed glossary is empty; keep at least one entry")
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = workspace.directory(f"{REVIEW_DIR}/glossary.reviewed-{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, result.model_dump_json(indent=2))
    return path
