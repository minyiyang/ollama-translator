"""Atomic artifact writing and promotion within one filesystem."""

import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    """Write bytes to a sibling temporary file and atomically replace the target."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write UTF-8 text atomically."""
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_copy_file(source: str | Path, destination: str | Path) -> Path:
    """Copy a file to a sibling temporary path and atomically promote it."""
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output, source_path.open("rb") as input_file:
            temporary = Path(output.name)
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination_path)
        return destination_path
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def promote_temporary_file(temporary: str | Path, destination: str | Path) -> Path:
    """Atomically promote an existing temporary file on the same filesystem."""
    temporary_path = Path(temporary)
    destination_path = Path(destination)
    if not temporary_path.is_file():
        raise FileNotFoundError(temporary_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary_path, destination_path)
    return destination_path

