import tempfile
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.hashing import sha256_file
from book_agent.pipeline_state import WorkflowStage
from book_agent.stages.decompile import (
    load_decompile_manifest,
    render_document_segments,
    run_decompile_stage,
)
from book_agent.state import (
    connect_state,
    get_stage_status,
    list_artifacts,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.rtf_fixture import make_rtf


class DecompileStageTests:
    def test_stage_decompiles_obfuscated_rtf_into_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_rtf(base / "qelm.rtf")
            workspace = create_job_workspace(
                source, base / "runs", AppConfig(), job_id="qelm"
            )
            manifest = run_decompile_stage(workspace)
            assert manifest.source_format == "rtf"
            assert len(manifest.documents) == 2
            assert list(workspace.directory("decompiled").glob("*/chapters/*.txt"))

    def test_render_document_segments_uses_protected_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="fixture")
            document = run_decompile_stage(workspace).documents[0]
            rendered = render_document_segments(document)
            assert "<D0000-S000001>Chapter One</D0000-S000001>" in rendered

    def test_stage_publishes_package_chapters_manifest_and_resume_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="fixture")
            first = run_decompile_stage(workspace)
            second = run_decompile_stage(workspace)
            assert first == second
            assert load_decompile_manifest(workspace) == first
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, WorkflowStage.DECOMPILE.value)
                assert stage["status"] == "completed"
                assert stage["attempts"] == 1
                artifacts = list_artifacts(connection, stage="decompile")
                assert len(artifacts) == 9
            finally:
                connection.close()

    def test_tampered_artifact_forces_safe_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="fixture")
            run_decompile_stage(workspace)
            manifest = load_decompile_manifest(workspace)
            package_file = next(workspace.root.rglob("package/OEBPS/styles.css"))
            package_file.write_text("tampered", encoding="utf-8")
            rebuilt = run_decompile_stage(workspace)
            assert rebuilt == manifest
            assert package_file.read_bytes() == b"body {}"

    def test_invalid_epub_marks_stage_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = base / "invalid.epub"
            epub.write_bytes(b"not a zip")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="invalid")
            with pytest.raises(Exception):
                run_decompile_stage(workspace)
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "decompile")
                assert stage["status"] == "failed"
                assert stage["attempts"] == 1
            finally:
                connection.close()

    def test_load_manifest_requires_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", AppConfig(), job_id="fixture")
            with pytest.raises(FileNotFoundError):
                load_decompile_manifest(workspace)

