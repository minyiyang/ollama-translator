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
from book_agent.stages.validate_repaired import run_repaired_validation_stage
from book_agent.state import connect_state, get_job_metadata, get_stage_status
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.rtf_fixture import make_rtf
from tests.test_audit_stage import FakeAuditClient
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient


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
                assert get_job_metadata(connection, "compiled_document") == "output/qelm.translated.epub"
                assert get_job_metadata(connection, "compiled_epub") == "output/qelm.translated.epub"
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

