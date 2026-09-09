import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.hashing import sha256_file
from book_agent.pipeline_state import WorkflowStage
from book_agent.state import connect_state, get_job_metadata, list_stage_statuses
from book_agent.workspace import (
    WORKSPACE_DIRECTORIES,
    build_job_id,
    create_job_workspace,
    open_job_workspace,
    slugify_job_name,
    validate_job_id,
)


class WorkspaceTests:
    def test_slugify_job_name(self) -> None:
        assert slugify_job_name("Aster's Adventures!") == "aster-s-adventures"
        assert slugify_job_name("中文") == "book"

    def test_build_job_id_is_deterministic_with_supplied_time(self) -> None:
        now = datetime(2026, 8, 27, 12, 34, 56, tzinfo=timezone.utc)
        assert build_job_id("Aster.epub", now=now) == "aster-20260827T123456Z"

    def test_validate_job_id_accepts_safe_and_rejects_traversal(self, subtests) -> None:
        assert validate_job_id("aster_01.test") == "aster_01.test"
        for invalid in ("", "../aster", "aster/book", " aster"):
            with subtests.test(invalid=invalid):
                with pytest.raises(ValueError):
                    validate_job_id(invalid)

    def test_create_and_open_workspace_captures_source_config_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "Aster.epub"
            source.write_bytes(b"epub fixture")
            workspace = create_job_workspace(
                source,
                base / "runs",
                AppConfig(),
                job_id="aster-test",
                now=datetime(2026, 8, 27, tzinfo=timezone.utc),
            )
            assert workspace.root.name == "aster-test"
            assert workspace.source_file.read_bytes() == b"epub fixture"
            assert workspace.source_file != source
            assert workspace.config_file.is_file()
            assert workspace.state_file.is_file()
            for relative in WORKSPACE_DIRECTORIES:
                assert (workspace.root / relative).is_dir()
            opened = open_job_workspace(workspace.root)
            assert opened == workspace
            connection = connect_state(workspace.state_file)
            try:
                assert get_job_metadata(connection, "job_id") == "aster-test"
                assert get_job_metadata(connection, "source_sha256") == sha256_file(source)
                assert len(list_stage_statuses(connection)) == len(WorkflowStage)
            finally:
                connection.close()

    def test_create_workspace_refuses_existing_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "book.epub"
            source.write_bytes(b"book")
            create_job_workspace(source, base / "runs", AppConfig(), job_id="same")
            with pytest.raises(FileExistsError):
                create_job_workspace(source, base / "runs", AppConfig(), job_id="same")

    def test_create_workspace_requires_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(FileNotFoundError):
                create_job_workspace(
                    Path(directory, "missing.epub"),
                    Path(directory, "runs"),
                    AppConfig(),
                )

    def test_workspace_directory_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "book.epub"
            source.write_bytes(b"book")
            workspace = create_job_workspace(
                source, base / "runs", AppConfig(), job_id="safe"
            )
            assert workspace.directory("translated") == workspace.root / "translated"
            with pytest.raises(ValueError, match="escapes"):
                workspace.directory("../outside")

    def test_open_workspace_rejects_invalid_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(ValueError, match="valid"):
                open_job_workspace(directory)


