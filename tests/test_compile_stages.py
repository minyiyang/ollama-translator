import json
import tempfile
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from book_agent.config import AppConfig
from book_agent.epub_compile import EpubCompilationError
from book_agent.stages.audit import run_translation_audit_stage
from book_agent.stages.compile import (
    load_compiled_document_path,
    load_compiled_epub_path,
    load_document_compilation_report,
    load_epub_compilation_report,
    run_document_compile_stage,
    run_epub_compile_stage,
)
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import run_preprocessing_stage
from book_agent.stages.repair import run_translation_repair_stage
from book_agent.stages.repair_review import run_review_repair_stage
from book_agent.stages.review_repaired import run_repaired_review_stage
from book_agent.stages.translate import run_translation_stage
from book_agent.stages.validate_epub import (
    load_document_validation_report,
    load_epub_validation_report,
    run_document_validation_stage,
    run_epub_validation_stage,
)
from book_agent.stages.validate_repaired import (
    load_validated_repaired_documents,
    run_repaired_validation_stage,
)
from book_agent.pipeline_state import WorkflowStage
from book_agent.repair import RepairedDocument
from book_agent.stage_artifacts import list_active_stage_artifacts
from book_agent.state import connect_state, get_job_metadata, get_stage_status
from book_agent.text_edits import active_edit_hash, apply_edit
from book_agent.atomic_io import atomic_write_text
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.rtf_fixture import make_rtf
from tests.test_audit_stage import FakeAuditClient
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient


def _retarget_pipeline_text(workspace, segment_id: str, new_text: str) -> None:
    """Directly rewrite one segment's validated-draft text, as a real rerun would."""
    connection = connect_state(workspace.state_file)
    try:
        artifacts = list_active_stage_artifacts(
            connection,
            WorkflowStage.VALIDATE_REPAIRED.value,
            root_metadata_key="validated_repaired_root",
            report_metadata_key="repaired_validation_report",
        )
    finally:
        connection.close()
    for artifact in artifacts:
        if artifact["kind"] != "validated_repaired_document_json":
            continue
        path = workspace.directory(str(artifact["path"]))
        document = RepairedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        if not any(s.segment_id == segment_id for s in document.document.segments):
            continue
        document.document = document.document.model_copy(
            update={
                "segments": [
                    s.model_copy(update={"translated_text": new_text})
                    if s.segment_id == segment_id
                    else s
                    for s in document.document.segments
                ]
            }
        )
        atomic_write_text(path, document.model_dump_json(indent=2))
        return
    raise AssertionError(f"segment not found in the validated draft: {segment_id}")


class CompileStageTests:
    def prepare_workspace(self, base, config, *, unresolved=False, source=None):
        from tests.test_validate_repaired_stage import (
            FakeUnchangedFeedbackClient,
            FakeVerificationClient,
        )
        source = source or make_epub(base / "fixture.epub")
        workspace = create_job_workspace(source, base / "runs", config, job_id="fixture")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, FakeTranslationClient())
        if config.audit.semantic_enabled:
            run_translation_audit_stage(workspace, config, FakeAuditClient())
            run_translation_repair_stage(
                workspace,
                config,
                FakeRepairClient(invalid_calls=10 if unresolved else 0),
            )
            client = (
                FakeUnchangedFeedbackClient(passed=True)
                if unresolved else FakeVerificationClient()
            )
        else:
            run_translation_audit_stage(workspace, config)
            run_translation_repair_stage(workspace, config)
            client = FakeVerificationClient()
        run_repaired_review_stage(workspace, config, client)
        run_review_repair_stage(workspace, config, client)
        run_repaired_validation_stage(workspace, config, client)
        return workspace

    def test_compile_and_validate_obfuscated_rtf_pipeline(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.prepare_workspace(
                base,
                config,
                source=make_rtf(base / "qelm.rtf"),
            )
            compilation = run_epub_compile_stage(workspace, config)
            output = Path(load_compiled_epub_path(workspace))
            assert output.suffix == ".epub"
            assert compilation.segment_count > 0
            assert run_epub_validation_stage(workspace).passed
            connection = connect_state(workspace.state_file)
            try:
                assert get_job_metadata(connection, "source_format") == "rtf"
                compile_hash = str(get_stage_status(connection, "compile")["input_hash"])
                expected = f"output/qelm.translated-{compile_hash[:6]}.epub"
                assert get_job_metadata(connection, "compiled_document") == expected
                assert get_job_metadata(connection, "compiled_epub") == expected
            finally:
                connection.close()

    def test_compile_and_validate_stages_publish_load_and_resume(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            first_compile = run_epub_compile_stage(workspace, config)
            second_compile = run_epub_compile_stage(workspace, config)
            assert run_document_compile_stage(workspace, config) == first_compile
            assert first_compile == second_compile
            assert load_epub_compilation_report(workspace) == first_compile
            assert load_document_compilation_report(workspace) == first_compile
            output = Path(load_compiled_epub_path(workspace))
            assert Path(load_compiled_document_path(workspace)) == output
            assert output.is_file()
            with ZipFile(output) as archive:
                assert archive.infolist()[0].filename == "mimetype"
                assert archive.infolist()[0].compress_type == ZIP_STORED
                chapter = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
                assert "<em>" in chapter
                assert "译文" in chapter
            first_validation = run_epub_validation_stage(workspace)
            second_validation = run_epub_validation_stage(workspace)
            assert run_document_validation_stage(workspace) == first_validation
            assert first_validation == second_validation
            assert first_validation.passed
            assert load_epub_validation_report(workspace) == first_validation
            assert load_document_validation_report(workspace) == first_validation
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "compile")["attempts"] == 1
                assert get_stage_status(connection, "validate_epub")["status"] == "completed"
            finally:
                connection.close()

    def test_compile_overlays_active_edits_and_recompiles_when_they_change(self):
        from book_agent.hashing import sha256_text

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            repaired = load_validated_repaired_documents(workspace)[0]
            segment = next(
                s for s in repaired.document.segments if s.source_text == "Chapter One"
            )

            first = run_epub_compile_stage(workspace, config)
            first_output = Path(load_compiled_epub_path(workspace))
            connection = connect_state(workspace.state_file)
            try:
                compile_stage = get_stage_status(connection, "compile")
                assert compile_stage["attempts"] == 1
                assert first_output.name == (
                    f"fixture.translated-{str(compile_stage['input_hash'])[:6]}.epub"
                )
                assert get_job_metadata(connection, "compiled_active_edit_hash") == ""
            finally:
                connection.close()
            with ZipFile(Path(load_compiled_epub_path(workspace))) as archive:
                before = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
                assert "译文" in before and "手改文本" not in before

            # Recompiling with no edit-log changes is a cache hit: no new attempt.
            assert run_epub_compile_stage(workspace, config) == first
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "compile")["attempts"] == 1
            finally:
                connection.close()

            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="手改文本。",
                reason="Testing the compile overlay.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            current_hash = active_edit_hash(workspace)
            assert current_hash

            second = run_epub_compile_stage(workspace, config)
            assert second != first
            connection = connect_state(workspace.state_file)
            try:
                # A changed input hash invalidates and restarts the stage, so attempts
                # resets to 1; the content check below is what proves it actually reran.
                compile_stage = get_stage_status(connection, "compile")
                assert compile_stage["attempts"] == 1
                second_output = Path(load_compiled_epub_path(workspace))
                assert second_output.name == (
                    f"fixture.translated-{str(compile_stage['input_hash'])[:6]}.epub"
                )
                assert second_output.name != first_output.name
                assert get_job_metadata(connection, "compiled_active_edit_hash") == current_hash
            finally:
                connection.close()
            with ZipFile(Path(load_compiled_epub_path(workspace))) as archive:
                after = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
                assert "手改文本" in after
            assert run_epub_validation_stage(workspace).passed

    def test_validation_uses_compiled_edit_snapshot_not_newer_live_edit(self):
        from book_agent.hashing import sha256_text

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            repaired = load_validated_repaired_documents(workspace)[0]
            segment = next(
                s for s in repaired.document.segments if s.source_text == "Chapter One"
            )
            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="编译时的手改文本。",
                reason="Testing the compiled snapshot.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            run_epub_compile_stage(workspace, config)

            # A later save belongs to the next build and must not change what
            # validation expects from the already-compiled EPUB.
            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="编译之后保存的文本。",
                reason="Testing a post-compile edit.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            assert run_epub_validation_stage(workspace).passed

    def test_compile_overlays_active_edits_onto_rtf_derived_output(self):
        """RTF sources compile through a distinct code path (compile_rtf_document); the
        overlay must reach it too, not just the EPUB package path exercised above."""
        from book_agent.hashing import sha256_text

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.prepare_workspace(base, config, source=make_rtf(base / "qelm.rtf"))
            segment = load_validated_repaired_documents(workspace)[0].document.segments[0]

            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="手改文本。",
                reason="Testing the RTF compile overlay.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            run_epub_compile_stage(workspace, config)
            output = Path(load_compiled_epub_path(workspace))
            assert output.suffix == ".epub"  # RTF sources still package as EPUB 3
            with ZipFile(output) as archive:
                combined = "".join(
                    archive.read(name).decode("utf-8", errors="ignore")
                    for name in archive.namelist()
                )
            assert "手改文本" in combined
            assert run_epub_validation_stage(workspace).passed

    def test_compile_refuses_and_then_accepts_an_unblocked_edit_conflict(self):
        from book_agent.hashing import sha256_text
        from book_agent.text_edits import apply_keep

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            repaired = load_validated_repaired_documents(workspace)[0]
            segment = next(
                s for s in repaired.document.segments if s.source_text == "Chapter One"
            )
            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="手改文本。",
                reason="Setting up a conflict.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            _retarget_pipeline_text(workspace, segment.segment_id, "全新翻译。")

            with pytest.raises(RuntimeError) as excinfo:
                run_epub_compile_stage(workspace, config)
            message = str(excinfo.value)
            assert "1 edit conflict(s)" in message
            assert segment.segment_id in message

            apply_keep(workspace, segment_id=segment.segment_id, reason="Keeping my wording.")
            report = run_epub_compile_stage(workspace, config)
            assert report is not None
            with ZipFile(Path(load_compiled_epub_path(workspace))) as archive:
                chapter = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
                assert "手改文本" in chapter

    def test_compile_refuses_unresolved_review_queue(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {"max_retries": 0},
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config, unresolved=True)
            with pytest.raises(RuntimeError, match="unresolved"):
                run_epub_compile_stage(workspace, config)
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "compile")["status"] == "failed"
                relative = get_job_metadata(
                    connection, "unresolved_review_segments_report"
                )
                assert relative == "reports/unresolved-review-segments.md"
            finally:
                connection.close()
            markdown = workspace.directory(relative).read_text(encoding="utf-8")
            assert "# Unresolved translation review segments" in markdown
            assert "### Source" in markdown
            assert "### Current translation" in markdown
            assert "### Manual fix suggestions" in markdown
            assert "### Previous source context" in markdown
            assert "### Translation history" in markdown
            structured = json.loads(
                workspace.directory("reports/unresolved-review-segments.json").read_text(
                    encoding="utf-8"
                )
            )
            assert structured["unresolved_count"] == 1
            assert structured["unresolved_defect_count"] == 1
            assert structured["approval_required_count"] == 0
            assert len(structured["segments"]) == 1
            assert structured["segments"][0]["review_kind"] == "defect"
            assert structured["segments"][0]["segment_id"]
            assert structured["segments"][0]["source_text"]
            assert structured["segments"][0]["current_translation"]
            assert structured["segments"][0]["fix_suggestions"]
            assert structured["segments"][0]["translation_versions"]
            worksheet = json.loads(
                workspace.directory("reports/final-human-review.decisions.json").read_text(
                    encoding="utf-8"
                )
            )
            assert worksheet["draft_output_hash"]
            assert worksheet["resolutions"][0]["decision"] == "pending"

    def test_compile_can_allow_a_bounded_unresolved_review_queue(self):
        config = AppConfig.model_validate(
            {
                "audit": {"semantic_sample_every": 2},
                "workflow": {
                    "max_retries": 0,
                    "compile_max_unresolved_review_segments": 1,
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config, unresolved=True)
            report = run_epub_compile_stage(workspace, config)
            assert report.output_size > 0

    def test_validation_failure_persists_report_and_failed_state(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.prepare_workspace(Path(directory), config)
            run_epub_compile_stage(workspace, config)
            Path(load_compiled_epub_path(workspace)).write_bytes(b"broken zip")
            with pytest.raises(EpubCompilationError):
                run_epub_validation_stage(workspace)
            report = load_epub_validation_report(workspace)
            assert not report.passed
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "validate_epub")["status"] == "failed"
            finally:
                connection.close()

    def test_compile_requires_repaired_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = AppConfig()
            epub = make_epub(base / "fixture.epub")
            workspace = create_job_workspace(epub, base / "runs", config, job_id="fixture")
            run_decompile_stage(workspace)
            with pytest.raises(FileNotFoundError):
                load_document_compilation_report(workspace)
            with pytest.raises(FileNotFoundError):
                load_document_validation_report(workspace)
            with pytest.raises(RuntimeError, match="validate_repaired"):
                run_epub_compile_stage(workspace, config)

