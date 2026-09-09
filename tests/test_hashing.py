import hashlib
import tempfile
from pathlib import Path

import pytest

from book_agent.hashing import (
    hash_named_values,
    sha256_bytes,
    sha256_file,
    sha256_text,
)


class HashingTests:
    def test_bytes_and_text_use_standard_sha256(self) -> None:
        expected = hashlib.sha256("你好".encode("utf-8")).hexdigest()
        assert sha256_bytes("你好".encode("utf-8")) == expected
        assert sha256_text("你好") == expected

    def test_file_hash_streams_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "artifact.bin")
            path.write_bytes(b"abcdefgh")
            assert sha256_file(path, chunk_size=3) == sha256_bytes(b"abcdefgh")

    def test_file_hash_rejects_nonpositive_chunk(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            sha256_file("unused", chunk_size=0)

    def test_named_hash_is_order_independent_and_name_sensitive(self) -> None:
        first = hash_named_values({"source": "a", "prompt": "b"})
        second = hash_named_values({"prompt": "b", "source": "a"})
        changed = hash_named_values({"source": "b", "prompt": "a"})
        assert first == second
        assert first != changed


