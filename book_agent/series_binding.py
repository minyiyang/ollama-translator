"""A book's pin to one published series glossary version.

Kept free of workflow imports so preprocessing can read it.  The binding lives
in the job folder; its path is stored relative to the job so a whole runs
directory can move.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .atomic_io import atomic_write_text
from .hashing import sha256_file

BINDING_FILE = "glossary/series-binding.json"


class SeriesBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(min_length=1)
    version: str = Field(pattern=r"^v\d{3,}$")
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)


def binding_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / BINDING_FILE


def load_series_binding(workspace_root: Path) -> SeriesBinding | None:
    """The book's series pin, or None for a book outside any dashboard series."""
    path = binding_path(workspace_root)
    if not path.is_file():
        return None
    return SeriesBinding.model_validate_json(path.read_text(encoding="utf-8"))


def write_series_binding(
    workspace_root: Path, series_id: str, version: str, glossary: Path
) -> SeriesBinding:
    binding = SeriesBinding(
        series_id=series_id,
        version=version,
        path=Path(os.path.relpath(glossary.resolve(), Path(workspace_root).resolve())).as_posix(),
        sha256=sha256_file(glossary),
    )
    path = binding_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, binding.model_dump_json(indent=2))
    return binding


def bound_glossary_file(workspace_root: Path, binding: SeriesBinding) -> Path:
    """The pinned version file, verified unchanged since the book was bound."""
    path = (Path(workspace_root) / binding.path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"series glossary {binding.series_id} {binding.version} is missing: {path}")
    if sha256_file(path) != binding.sha256:
        raise ValueError(
            f"series glossary {binding.series_id} {binding.version} changed after it was "
            f"published: {path}"
        )
    return path
