"""Draft jobs: a source and a config chosen in the dashboard but not yet started.

A draft is a small JSON record under ``<runs>/.drafts``. The real workspace is
created by the regular ``run`` command when the draft starts, so everything
after that point is identical to a terminal run.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..atomic_io import atomic_write_text
from ..hashing import sha256_text
from ..workspace import slugify_job_name, validate_job_id
from . import setup as setup_api

DRAFT_DIR = ".drafts"
UPLOAD_DIR = ".uploads"
_SOURCE_SUFFIXES = {".epub", ".rtf"}


def _draft_path(runs: Path, job_id: str) -> Path:
    validate_job_id(job_id)
    return runs / DRAFT_DIR / f"{job_id}.json"


def load_draft(runs: Path, job_id: str) -> dict[str, Any] | None:
    path = _draft_path(runs, job_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def save_draft(runs: Path, draft: dict[str, Any]) -> None:
    path = _draft_path(runs, draft["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(draft, indent=2, ensure_ascii=False))


def delete_draft(runs: Path, job_id: str) -> None:
    _draft_path(runs, job_id).unlink(missing_ok=True)


def list_drafts(runs: Path) -> list[dict[str, Any]]:
    directory = runs / DRAFT_DIR
    if not directory.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def config_hash(config_dir: Path, name: str) -> str:
    path = setup_api.config_path(config_dir, name)
    return sha256_text(path.read_text(encoding="utf-8")) if path.is_file() else ""


def is_validated(config_dir: Path, draft: dict[str, Any]) -> bool:
    """Validation holds only while the config file is byte-identical to what passed."""
    return bool(draft.get("validated_hash")) and draft["validated_hash"] == config_hash(config_dir, draft["config"])


def suggest_names(source: str) -> dict[str, str]:
    stem = slugify_job_name(Path(source).stem) if source else "book"
    return {"config": f"{stem}.yaml", "job_id": stem}


def create_draft(
    runs: Path,
    config_dir: Path,
    *,
    source: str,
    config_name: str,
    job_id: str,
    template: Path | None,
) -> dict[str, Any]:
    """Record a new job; create its config from the template unless it already exists."""
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"source file not found: {source_path}")
    if source_path.suffix.casefold() not in _SOURCE_SUFFIXES:
        raise ValueError("source must be an EPUB or RTF file")
    config_path = setup_api.config_path(config_dir, config_name)
    job_id = job_id or Path(config_name).stem
    validate_job_id(job_id)
    if (runs / job_id).exists() or _draft_path(runs, job_id).exists():
        raise ValueError(f"a job named {job_id} already exists")
    created_config = False
    if not config_path.is_file():
        text = template.read_text(encoding="utf-8") if template and template.is_file() else ""
        setup_api.save_config(config_dir, config_name, text or "{}\n")
        created_config = True
    draft = {
        "job_id": job_id,
        "source": str(source_path.resolve()),
        "config": config_name,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validated_hash": "",
        "launched": "",
    }
    save_draft(runs, draft)
    return {**draft, "created_config": created_config}


def store_upload(runs: Path, filename: str, stream, length: int) -> Path:
    """Copy an uploaded EPUB/RTF into ``<runs>/.uploads`` without overwriting a different file."""
    name = Path(filename).name
    stem, suffix = Path(name).stem, Path(name).suffix
    if suffix.casefold() not in _SOURCE_SUFFIXES:
        raise ValueError("only .epub and .rtf files can be uploaded")
    safe_stem = re.sub(r"[^\w .'()-]+", "_", stem).strip() or "book"
    directory = runs / UPLOAD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    data = bytearray()
    remaining = length
    while remaining > 0:
        chunk = stream.read(min(remaining, 1 << 20))
        if not chunk:
            raise ValueError("upload ended early")
        data.extend(chunk)
        remaining -= len(chunk)
    target = directory / f"{safe_stem}{suffix}"
    counter = 1
    while target.exists() and target.read_bytes() != data:
        counter += 1
        target = directory / f"{safe_stem} ({counter}){suffix}"
    target.write_bytes(data)
    return target
