import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from book_agent.atomic_io import (
    atomic_copy_file,
    atomic_write_bytes,
    atomic_write_text,
    promote_temporary_file,
)


class AtomicIoTests:
    def test_atomic_write_bytes_creates_parent_and_replaces_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "nested", "artifact.bin")
            assert atomic_write_bytes(target, b"first") == target
            atomic_write_bytes(target, b"second")
            assert target.read_bytes() == b"second"
            assert list(target.parent.glob("*.tmp")) == []

    def test_atomic_write_text_uses_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "text.txt")
            atomic_write_text(target, "中 English")
            assert target.read_text(encoding="utf-8") == "中 English"

    def test_atomic_copy_file_preserves_source_and_replaces_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "source.epub")
            destination = Path(directory, "captured", "source.epub")
            source.write_bytes(b"book")
            destination.parent.mkdir()
            destination.write_bytes(b"old")
            atomic_copy_file(source, destination)
            assert source.read_bytes() == b"book"
            assert destination.read_bytes() == b"book"

    def test_atomic_copy_requires_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(FileNotFoundError):
                atomic_copy_file(Path(directory, "missing"), Path(directory, "out"))

    def test_promote_temporary_file_moves_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory, "result.partial")
            destination = Path(directory, "final", "result.txt")
            temporary.write_text("complete", encoding="utf-8")
            assert promote_temporary_file(temporary, destination) == destination
            assert not temporary.exists()
            assert destination.read_text(encoding="utf-8") == "complete"

    def test_promote_requires_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(FileNotFoundError):
                promote_temporary_file(Path(directory, "missing"), Path(directory, "final"))

    def test_failed_atomic_replace_preserves_existing_target_and_cleans_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "artifact.txt")
            target.write_text("old", encoding="utf-8")
            with patch("book_agent.atomic_io.os.replace", side_effect=OSError("interrupted")):
                with pytest.raises(OSError, match="interrupted"):
                    atomic_write_text(target, "new")
            assert target.read_text(encoding="utf-8") == "old"
            assert list(Path(directory).glob("*.tmp")) == []

