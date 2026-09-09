"""Helpers for loading only the currently published stage generation."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable, Iterable, TypeVar

from .state import get_job_metadata, list_artifacts


T = TypeVar("T")


def list_active_stage_artifacts(
    connection,
    stage: str,
    *,
    root_metadata_key: str = "",
    report_metadata_key: str = "",
) -> list[dict[str, object]]:
    """Return artifacts below the active generation root.

    Stage artifacts are immutable and older generations intentionally remain on
    disk for resumability and diagnostics.  Consumers must therefore resolve the
    single published generation through job metadata instead of reading every
    historical artifact for the stage.
    """
    root = get_job_metadata(connection, root_metadata_key) if root_metadata_key else None
    if not root and report_metadata_key:
        report = get_job_metadata(connection, report_metadata_key)
        if report:
            root = PurePosixPath(report).parent.as_posix()
    if not root:
        raise FileNotFoundError(f"active artifact root is not recorded for stage: {stage}")

    normalized_root = PurePosixPath(root).as_posix().rstrip("/")
    prefix = normalized_root + "/"
    return [
        artifact
        for artifact in list_artifacts(connection, stage=stage)
        if str(artifact["path"]) == normalized_root
        or str(artifact["path"]).startswith(prefix)
    ]


def require_unique_items(
    items: Iterable[T],
    identity: Callable[[T], str],
    *,
    label: str,
) -> list[T]:
    """Materialize items and fail closed when an active generation is corrupt."""
    result = list(items)
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in result:
        item_id = identity(item)
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise RuntimeError(f"duplicate {label} IDs in active artifact generation: {joined}")
    return result


def require_document_totals(
    items: Iterable[T],
    *,
    expected_documents: int,
    expected_segments: int | None = None,
    segment_count: Callable[[T], int] | None = None,
    label: str,
) -> list[T]:
    """Fail closed when a published generation disagrees with its report."""
    result = list(items)
    if len(result) != expected_documents:
        raise RuntimeError(
            f"active {label} generation contains {len(result)} documents; "
            f"report declares {expected_documents}"
        )
    if expected_segments is not None and segment_count is not None:
        actual_segments = sum(segment_count(item) for item in result)
        if actual_segments != expected_segments:
            raise RuntimeError(
                f"active {label} generation contains {actual_segments} segments; "
                f"report declares {expected_segments}"
            )
    return result
