"""Read a CAT-tool XLIFF file back into tracked edits (docs/XLIFF_IMPORT.md).

Every changed segment becomes an ordinary ``edit`` event, so history,
revert, conflicts, and the compile overlay work unchanged. A segment is
matched by its unit id and its source text, never guessed; anything that
does not match, changed since export, or fails the edit checks is skipped
and reported. Nothing is written until an import is applied, and applying
re-checks against the current book.

Each import is stored as ``edits/imports/<import_id>.json``: the parsed
units, and after applying the result. The preview is recomputed from the
stored units whenever it is shown, so it always reflects the current book.
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .atomic_io import atomic_write_text
from .config import AppConfig
from .hashing import sha256_text
from .languages import TranslationDirection
from .pipeline_state import WorkflowStage
from .stages.preprocess import load_preprocessed_documents
from .stages.validate_repaired import load_validated_repaired_documents
from .state import StageStatus, connect_state, get_job_metadata, get_stage_status
from .text_edits import (
    BlockingCheckError,
    SegmentEditRequest,
    SegmentEditState,
    StaleEditError,
    apply_edit_batch,
    check_edits,
    edited_segment_statuses,
)
from .workspace import JobWorkspace
from .xliff_export import (
    CORE_NAMESPACE,
    LEGACY_CORE_NAMESPACE,
    METADATA_CATEGORY,
    METADATA_NAMESPACE,
)

IMPORTS_RELATIVE = "edits/imports"
_XLIFF_1_NAMESPACE = "urn:oasis:names:tc:xliff:document:1.2"
_IMPORT_ID = re.compile(r"I\d{8}T\d{6}-[0-9a-f]{6}")
_ACTIVE_STATES = (SegmentEditState.EDITED, SegmentEditState.CONFLICT)

# Categories in the order they are decided (docs/XLIFF_IMPORT.md section 4).
UNKNOWN_ID = "unknown_id"
UNSUPPORTED_MARKUP = "unsupported_markup"
SOURCE_DIFFERS = "source_differs"
NO_TARGET = "no_target"
UNCHANGED = "unchanged"
EDITED_SINCE_EXPORT = "edited_since_export"
STALE = "stale"
FAILS_CHECKS = "fails_checks"
NEEDS_OVERRIDE = "needs_override"
IMPORT = "import"
_ELIGIBLE = {IMPORT, NEEDS_OVERRIDE, STALE, EDITED_SINCE_EXPORT}


class XliffImportError(ValueError):
    """The file as a whole cannot be imported into this job."""


@dataclass(frozen=True)
class ImportedUnit:
    """One ``<unit>`` as read from the file, with markers mapped back to ours."""

    unit_id: str
    source: str
    target: str | None
    markup_error: str
    base_target_sha256: str | None  # None: the unit carries no export metadata
    edit_revision: str | None  # None: no export metadata; "" : exported with no edits


@dataclass(frozen=True)
class ImportOptions:
    include_stale: bool = False
    include_edited: bool = False
    include_overridable: bool = False


# -- reading the file --------------------------------------------------------


class _UnsupportedMarkup(Exception):
    pass


def _local(tag: str, namespace: str) -> str | None:
    prefix = f"{{{namespace}}}"
    return tag[len(prefix):] if tag.startswith(prefix) else None


def _inline_text(element: ElementTree.Element, namespace: str) -> str:
    """Inline content with ``<pc id="000">`` mapped back to our ``<I000>`` markers."""
    parts = [element.text or ""]
    for child in element:
        name = _local(child.tag, namespace)
        if name == "pc":
            marker = child.get("id", "")
            if not re.fullmatch(r"\d{3}", marker):
                raise _UnsupportedMarkup(f'<pc id="{marker}">')
            parts.append(f"<I{marker}>{_inline_text(child, namespace)}</I{marker}>")
        elif name == "mrk":
            parts.append(_inline_text(child, namespace))
        elif name in {"sm", "em"}:
            pass  # annotation boundaries carry no text
        elif name == "cp":
            try:
                parts.append(chr(int(child.get("hex", ""), 16)))
            except ValueError:
                raise _UnsupportedMarkup(f'<cp hex="{child.get("hex", "")}">') from None
        else:
            raise _UnsupportedMarkup(f"<{name or child.tag}>")
        parts.append(child.tail or "")
    return "".join(parts)


def _book_metadata(element: ElementTree.Element) -> dict[str, str] | None:
    group = element.find(
        f"{{{METADATA_NAMESPACE}}}metadata/"
        f"{{{METADATA_NAMESPACE}}}metaGroup[@category='{METADATA_CATEGORY}']"
    )
    if group is None:
        return None
    return {
        str(meta.get("type")): meta.text or ""
        for meta in group.findall(f"{{{METADATA_NAMESPACE}}}meta")
    }


def _primary_language(value: str | None) -> str:
    return re.split(r"[-_]", value or "")[0].lower()


def _read_unit(unit: ElementTree.Element, namespace: str) -> ImportedUnit:
    metadata = _book_metadata(unit)
    sources: list[str] = []
    targets: list[str] = []
    missing_target = False
    markup_error = ""
    for part in unit:
        name = _local(part.tag, namespace)
        if name not in {"segment", "ignorable"}:
            continue
        source = part.find(f"{{{namespace}}}source")
        target = part.find(f"{{{namespace}}}target")
        try:
            source_text = _inline_text(source, namespace) if source is not None else ""
            if target is not None:
                target_text = _inline_text(target, namespace)
            elif name == "ignorable":
                target_text = source_text  # whitespace between split segments
            else:
                missing_target = True
                target_text = ""
        except _UnsupportedMarkup as error:
            markup_error = str(error)
            break
        sources.append(source_text)
        targets.append(target_text)
    return ImportedUnit(
        unit_id=str(unit.get("id", "")),
        source="".join(sources),
        target=None if missing_target else "".join(targets),
        markup_error=markup_error,
        base_target_sha256=None if metadata is None else metadata.get("base_target_sha256"),
        edit_revision=None if metadata is None else metadata.get("edit_revision", ""),
    )


def parse_xliff(
    text: str, *, direction: TranslationDirection, source_sha256: str
) -> tuple[list[ImportedUnit], list[str]]:
    """Read an XLIFF 2 document into units and file-level warnings.

    Raises ``XliffImportError`` when the file as a whole cannot be imported:
    not XLIFF 2, another book or language pair, a duplicated unit id, or a
    DOCTYPE/ENTITY declaration (XLIFF needs neither; refusing them blocks
    entity-expansion attacks on the XML parser).
    """
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise XliffImportError("XLIFF files with a DOCTYPE or ENTITY declaration are not accepted")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise XliffImportError(f"the file is not well-formed XML: {error}") from None
    namespace = next(
        (
            candidate
            for candidate in (CORE_NAMESPACE, LEGACY_CORE_NAMESPACE)
            if root.tag == f"{{{candidate}}}xliff"
        ),
        None,
    )
    if namespace is None:
        if root.tag == f"{{{_XLIFF_1_NAMESPACE}}}xliff":
            raise XliffImportError("XLIFF 1.2 is not supported; export the file as XLIFF 2.x")
        raise XliffImportError("the file is not an XLIFF 2 document")
    expected = (direction.source_language.value, direction.target_language.value)
    found = (_primary_language(root.get("srcLang")), _primary_language(root.get("trgLang")))
    if found != expected:
        raise XliffImportError(
            f"the file translates {root.get('srcLang') or '?'} → {root.get('trgLang') or '?'}, "
            f"but this job translates {expected[0]} → {expected[1]}"
        )

    warnings: list[str] = []
    book_hashes = {
        metadata.get("source_sha256", "")
        for file in root.iter(f"{{{namespace}}}file")
        if (metadata := _book_metadata(file)) is not None
    } - {""}
    if book_hashes and book_hashes != {source_sha256}:
        job_ids = sorted({
            metadata.get("job_id", "")
            for file in root.iter(f"{{{namespace}}}file")
            if (metadata := _book_metadata(file)) is not None
        } - {""})
        origin = f" (job {', '.join(job_ids)})" if job_ids else ""
        raise XliffImportError(
            f"the file was exported from a different book{origin}; import it into that job's Text tab"
        )
    if not book_hashes:
        warnings.append(
            "The file has no export metadata from this dashboard, so no segment can be "
            "shown to be current; every changed segment counts as stale."
        )

    units: list[ImportedUnit] = []
    seen: set[str] = set()
    for element in root.iter(f"{{{namespace}}}unit"):
        unit = _read_unit(element, namespace)
        if not unit.unit_id:
            raise XliffImportError("a unit has no id")
        if unit.unit_id in seen:
            raise XliffImportError(f"unit id {unit.unit_id} appears more than once")
        seen.add(unit.unit_id)
        units.append(unit)
    if not units:
        raise XliffImportError("the file has no translation units")
    return units, warnings


# -- classifying ---------------------------------------------------------------


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _item(unit: ImportedUnit, category: str, message: str = "", **fields: Any) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "segment_id": None,
        "document_id": None,
        "category": category,
        "message": message,
        "imported_text": unit.target,
        "stale": False,
        "edited_since_export": False,
        "hard": [],
        "overridable": [],
        **fields,
    }


def classify(workspace: JobWorkspace, units: list[ImportedUnit]) -> list[dict[str, Any]]:
    """Decide every unit's category against the current book (section 4 of the design)."""
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
    statuses = edited_segment_statuses(workspace)
    items: list[dict[str, Any]] = []
    changed: dict[str, str] = {}
    stale_reasons: dict[str, str] = {}
    for unit in units:
        segment_id = unit.unit_id
        if segment_id not in sources or segment_id not in pipeline:
            items.append(_item(unit, UNKNOWN_ID, "No segment with this ID in the book."))
            continue
        document_id, source_text = sources[segment_id]
        pipeline_text = pipeline[segment_id]
        status = statuses.get(segment_id)
        current = status.text if status and status.state in _ACTIVE_STATES else pipeline_text
        located = {
            "segment_id": segment_id,
            "document_id": document_id,
            "source": source_text,
            "current_text": current,
            "pipeline_sha256": sha256_text(pipeline_text),
            "current_revision": status.last_event.event_id if status and status.last_event else "",
        }
        if unit.markup_error:
            items.append(_item(unit, UNSUPPORTED_MARKUP,
                               f"Unsupported inline markup {unit.markup_error}.", **located))
        elif _normalized(unit.source) != _normalized(source_text):
            items.append(_item(unit, SOURCE_DIFFERS, "The source text differs from this book's.",
                               imported_source=unit.source, **located))
        elif unit.target is None or not unit.target.strip():
            items.append(_item(unit, NO_TARGET, "No translation in the file.", **located))
        elif _normalized(unit.target) == _normalized(current):
            items.append(_item(unit, UNCHANGED, **located))
        else:
            stale = (
                unit.base_target_sha256 is None
                or unit.base_target_sha256 != located["pipeline_sha256"]
            )
            edited = (
                unit.edit_revision is not None
                and unit.edit_revision != located["current_revision"]
            )
            changed[segment_id] = unit.target
            items.append(_item(unit, IMPORT, stale=stale, edited_since_export=edited, **located))
            if unit.base_target_sha256 is None:
                stale_reasons[segment_id] = "No export metadata, so it cannot be shown to be current."

    findings = check_edits(workspace, changed)
    for item in items:
        if item["category"] != IMPORT:
            continue
        item["hard"] = findings[item["segment_id"]]["hard"]
        item["overridable"] = findings[item["segment_id"]]["overridable"]
        if item["edited_since_export"]:
            item["category"] = EDITED_SINCE_EXPORT
            item["message"] = "Edited in the Text tab after this file was exported."
        elif item["stale"]:
            item["category"] = STALE
            item["message"] = stale_reasons.get(
                item["segment_id"], "The pipeline translation changed after this file was exported."
            )
        elif item["hard"]:
            item["category"] = FAILS_CHECKS
            item["message"] = "; ".join(finding["message"] for finding in item["hard"])
        elif item["overridable"]:
            item["category"] = NEEDS_OVERRIDE
            item["message"] = "; ".join(finding["message"] for finding in item["overridable"])
    return items


def will_import(item: dict[str, Any], options: ImportOptions) -> bool:
    """Whether a classified unit is imported under the chosen opt-ins."""
    if item["category"] not in _ELIGIBLE:
        return False
    if item["stale"] and not options.include_stale:
        return False
    if item["edited_since_export"] and not options.include_edited:
        return False
    if item["hard"]:
        return False
    return not item["overridable"] or options.include_overridable


# -- storage -------------------------------------------------------------------


def _imports_dir(workspace: JobWorkspace) -> Path:
    return workspace.root / IMPORTS_RELATIVE


def _record_path(workspace: JobWorkspace, import_id: str) -> Path:
    if not _IMPORT_ID.fullmatch(import_id):
        raise ValueError(f"no such import: {import_id}")
    return _imports_dir(workspace) / f"{import_id}.json"


def _load(workspace: JobWorkspace, import_id: str) -> dict[str, Any]:
    path = _record_path(workspace, import_id)
    if not path.is_file():
        raise ValueError(f"no such import: {import_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(workspace: JobWorkspace, record: dict[str, Any]) -> None:
    path = _record_path(workspace, record["import_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def _records(workspace: JobWorkspace) -> list[dict[str, Any]]:
    root = _imports_dir(workspace)
    if not root.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("I*.json"))
        if _IMPORT_ID.fullmatch(path.stem)
    ]


def _units(record: dict[str, Any]) -> list[ImportedUnit]:
    return [ImportedUnit(**unit) for unit in record["units"]]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _require_validated_draft(workspace: JobWorkspace) -> None:
    connection = connect_state(workspace.state_file)
    try:
        stage = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
    finally:
        connection.close()
    if stage is None or stage["status"] != StageStatus.COMPLETED.value:
        raise ValueError("XLIFF import needs the validated draft; wait for Validate draft to complete")


# -- the import workflow ---------------------------------------------------------


def _preview(workspace: JobWorkspace, record: dict[str, Any]) -> dict[str, Any]:
    items = classify(workspace, _units(record))
    return {
        "import_id": record["import_id"],
        "file_name": record["file_name"],
        "created_at": record["created_at"],
        "warnings": record["warnings"],
        "counts": dict(Counter(item["category"] for item in items)),
        "items": items,
    }


def start_import(workspace: JobWorkspace, file_name: str, xliff: str) -> dict[str, Any]:
    """Read and check a file, store it as the pending preview, and return the preview.

    Uploading a new file replaces any pending preview.
    """
    _require_validated_draft(workspace)
    config = AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
    connection = connect_state(workspace.state_file)
    try:
        source_sha256 = get_job_metadata(connection, "source_sha256") or ""
    finally:
        connection.close()
    units, warnings = parse_xliff(
        xliff, direction=config.translation.direction, source_sha256=source_sha256
    )
    for record in _records(workspace):
        if record["status"] == "preview":
            record["status"] = "cancelled"
            _save(workspace, record)
    record = {
        "schema_version": 1,
        "import_id": f"I{datetime.now():%Y%m%dT%H%M%S}-{secrets.token_hex(3)}",
        "file_name": Path(file_name).name[:200] or "import.xlf",
        "created_at": _now(),
        "status": "preview",
        "dismissed": False,
        "warnings": warnings,
        "units": [asdict(unit) for unit in units],
        "result": None,
    }
    _save(workspace, record)
    return _preview(workspace, record)


def import_state(workspace: JobWorkspace) -> dict[str, Any]:
    """The pending preview (recomputed against the current book) and the last applied import."""
    pending: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    for record in _records(workspace):
        if record["status"] == "preview":
            try:
                _require_validated_draft(workspace)
                pending = _preview(workspace, record)
            except (ValueError, OSError) as error:
                pending = {
                    "import_id": record["import_id"],
                    "file_name": record["file_name"],
                    "created_at": record["created_at"],
                    "warnings": record["warnings"],
                    "counts": {},
                    "items": [],
                    "error": str(error),
                }
        elif record["status"] == "applied" and not record["dismissed"]:
            last = {
                "import_id": record["import_id"],
                "file_name": record["file_name"],
                **record["result"],
            }
    return {"pending": pending, "last": last}


def apply_import(
    workspace: JobWorkspace,
    import_id: str,
    reason: str,
    options: ImportOptions,
    *,
    author: str | None = None,
) -> dict[str, Any]:
    """Apply a pending import as one checked edit batch; return what was applied and skipped.

    The units are reclassified against the current book, the opt-ins applied,
    and the selection re-checked together until stable (dropping a unit can
    change a neighbour's result). The remainder is appended with
    ``apply_edit_batch``, all or nothing.
    """
    if len(reason.strip()) < 3:
        raise ValueError("an import needs a reason of at least 3 characters")
    _require_validated_draft(workspace)
    record = _load(workspace, import_id)
    if record["status"] != "preview":
        raise ValueError("this import is no longer pending; upload the file again")
    items = classify(workspace, _units(record))
    selected = [item for item in items if will_import(item, options)]
    dropped: dict[str, str] = {}
    while selected:
        findings = check_edits(
            workspace, {item["segment_id"]: item["imported_text"] for item in selected}
        )
        failing = {
            item["segment_id"]
            for item in selected
            if findings[item["segment_id"]]["hard"]
            or (findings[item["segment_id"]]["overridable"] and not options.include_overridable)
        }
        if not failing:
            break
        for item in selected:
            if item["segment_id"] in failing:
                blocking = findings[item["segment_id"]]["hard"] or findings[item["segment_id"]]["overridable"]
                dropped[item["segment_id"]] = "; ".join(finding["message"] for finding in blocking)
        selected = [item for item in selected if item["segment_id"] not in failing]
    if not selected:
        raise ValueError("nothing to import with these choices")

    event_reason = f"{reason.strip()} (imported from {record['file_name']})"
    try:
        events = apply_edit_batch(
            workspace,
            [
                SegmentEditRequest(
                    segment_id=item["segment_id"],
                    text=item["imported_text"],
                    reason=event_reason,
                    base_target_sha256=item["pipeline_sha256"],
                    override_reason=(
                        reason.strip() if findings[item["segment_id"]]["overridable"] else ""
                    ),
                    author=author,
                    expected_event_id=item["current_revision"],
                )
                for item in selected
            ],
        )
    except (StaleEditError, BlockingCheckError) as error:
        raise ValueError(
            f"the book changed while importing ({error}); nothing was imported, review the preview again"
        ) from None

    applied_ids = {item["segment_id"] for item in selected}
    skipped = []
    for item in items:
        if item["category"] == UNCHANGED or item["segment_id"] in applied_ids:
            continue
        message = dropped.get(item["segment_id"] or "", item["message"])
        category = FAILS_CHECKS if item["segment_id"] in dropped else item["category"]
        skipped.append({
            "unit_id": item["unit_id"],
            "segment_id": item["segment_id"],
            "document_id": item["document_id"],
            "category": category,
            "message": message,
            "imported_text": item["imported_text"],
        })
    result = {
        "applied_at": _now(),
        "applied": [
            {"segment_id": event.segment_id, "document_id": event.document_id, "event_id": event.event_id}
            for event in events
        ],
        "skipped": skipped,
        "unchanged_count": sum(1 for item in items if item["category"] == UNCHANGED),
        "dropped_at_apply": len(dropped),
    }
    record["status"] = "applied"
    record["result"] = result
    record["options"] = asdict(options)
    _save(workspace, record)
    return {"import_id": import_id, "file_name": record["file_name"], **result}


def cancel_import(workspace: JobWorkspace, import_id: str) -> None:
    record = _load(workspace, import_id)
    if record["status"] != "preview":
        raise ValueError("this import is no longer pending")
    record["status"] = "cancelled"
    _save(workspace, record)


def dismiss_import(workspace: JobWorkspace, import_id: str) -> None:
    record = _load(workspace, import_id)
    if record["status"] != "applied":
        raise ValueError("only an applied import can be dismissed")
    record["dismissed"] = True
    _save(workspace, record)


_OUTCOME = {
    IMPORT: "will import",
    UNCHANGED: "unchanged",
    STALE: "skipped unless opted in",
    EDITED_SINCE_EXPORT: "skipped unless opted in",
    NEEDS_OVERRIDE: "skipped unless opted in",
}


def import_report_csv(workspace: JobWorkspace, import_id: str) -> tuple[bytes, str]:
    """Every unit's outcome and reason as CSV, and a download name for it."""
    record = _load(workspace, import_id)
    rows: list[tuple[str, str, str, str, str, str]] = []
    if record["status"] == "applied":
        for item in record["result"]["applied"]:
            rows.append((item["segment_id"], item["segment_id"], item["document_id"], "imported", IMPORT, ""))
        for item in record["result"]["skipped"]:
            rows.append((item["unit_id"], item["segment_id"] or "", item["document_id"] or "",
                         "skipped", item["category"], item["message"]))
    else:
        _require_validated_draft(workspace)
        for item in classify(workspace, _units(record)):
            rows.append((item["unit_id"], item["segment_id"] or "", item["document_id"] or "",
                         _OUTCOME.get(item["category"], "skipped"), item["category"], item["message"]))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["unit_id", "segment_id", "chapter", "outcome", "category", "reason"])
    writer.writerows(rows)
    # A byte-order mark so spreadsheet apps read the UTF-8 (Chinese) text correctly.
    return ("﻿" + buffer.getvalue()).encode("utf-8"), f"{Path(record['file_name']).stem}.import-report.csv"
