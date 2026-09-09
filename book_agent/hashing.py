"""Canonical SHA-256 helpers for reproducible workflow inputs and artifacts."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hash text using its UTF-8 representation."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading it entirely into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def hash_named_values(values: Mapping[str, str]) -> str:
    """Hash named values canonically, independent of mapping insertion order."""
    serialized = json.dumps(
        dict(values),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(serialized)

