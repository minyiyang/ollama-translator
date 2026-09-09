import tempfile
from pathlib import Path

import pytest

from book_agent.atomic_io import atomic_write_text
from book_agent.config import AppConfig
from book_agent.hashing import sha256_file
from book_agent.pipeline_state import WorkflowStage, build_stage_output_hash
from book_agent.schemas import GlossaryCategory, GlossaryEntry, GlossaryResult
from book_agent.stages.decompile import run_decompile_stage
from book_agent.stages.preprocess import (
    load_preprocessed_documents,
    load_preprocessing_report,
    run_preprocessing_stage,
)
from book_agent.state import (
    StageStatus,
    connect_state,
    get_stage_status,
    get_work_unit,
    record_artifact,
    set_job_metadata,
    set_stage_status,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import CHAPTER, make_epub


def glossary_entry(english: str, chinese: str, aliases: list[str] | None = None) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        category=GlossaryCategory.OTHER,
        aliases=aliases or [],
    )


def publish_approved_glossary(workspace, entries: list[GlossaryEntry]) -> None:
    result = GlossaryResult(entries=entries)
    path = workspace.root / "glossary" / "approved-test.json"
    atomic_write_text(path, result.model_dump_json(indent=2))
    connection = connect_state(workspace.state_file)
    try:
        record_artifact(
            connection,
            path.relative_to(workspace.root).as_posix(),
            WorkflowStage.APPROVE_GLOSSARY.value,
            "approved_glossary_json",
            sha256_file(path),
            path.stat().st_size,
        )
        output_hash = build_stage_output_hash(connection, WorkflowStage.APPROVE_GLOSSARY)
        set_job_metadata(
            connection,
            "glossary_approved",
            path.relative_to(workspace.root).as_posix(),
        )
        set_stage_status(
            connection,
            WorkflowStage.APPROVE_GLOSSARY.value,
            StageStatus.COMPLETED,
            input_hash="test-input",
            output_hash=output_hash,
        )
    finally:
        connection.close()


class PreprocessStageTests:
    def make_workspace(self, base: Path, *, chapter: bytes = CHAPTER):
        epub = make_epub(base / "fixture.epub", chapter=chapter)
        workspace = create_job_workspace(
            epub, base / "runs", AppConfig(), job_id="fixture"
        )
        run_decompile_stage(workspace)
        return workspace

    def test_stage_preprocesses_all_segments_selects_relevant_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            publish_approved_glossary(
                workspace,
                [
                    glossary_entry("Chapter One", "第一章"),
                    glossary_entry("Hello", "你好"),
                    glossary_entry("Unused", "未使用"),
                ],
            )
            config = AppConfig.model_validate(
                {"workflow": {"require_glossary_review": False}}
            )
            first = run_preprocessing_stage(workspace, config)
            second = run_preprocessing_stage(workspace, config)
            assert first == second
            assert first.document_count == 1
            assert first.segment_count == 4
            assert first.mode == "annotate"
            assert first.replacement_count == 0
            documents = load_preprocessed_documents(workspace)
            assert len(documents) == 1
            assert [item.english for item in documents[0].relevant_glossary] == ["Chapter One", "Hello"]
            assert documents[0].segments[0].processed_text == "Chapter One"
            assert documents[0].segments[1].processed_text.startswith("Hello")
            assert load_preprocessing_report(workspace) == first
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, "preprocess")
                assert stage["status"] == "completed"
                assert stage["attempts"] == 1
                unit = get_work_unit(connection, "document:chapter", "preprocess")
                assert unit["validation"]["segment_count"] == 4
            finally:
                connection.close()

    def test_loader_ignores_stale_artifacts_from_interrupted_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, AppConfig())
            current = load_preprocessed_documents(workspace)[0]

            stale_path = workspace.root / "preprocessed" / "stale-generation" / "duplicate.json"
            atomic_write_text(stale_path, current.model_dump_json(indent=2))
            connection = connect_state(workspace.state_file)
            try:
                record_artifact(
                    connection,
                    stale_path.relative_to(workspace.root).as_posix(),
                    WorkflowStage.PREPROCESS.value,
                    "preprocessed_document_json",
                    sha256_file(stale_path),
                    stale_path.stat().st_size,
                )
            finally:
                connection.close()

            documents = load_preprocessed_documents(workspace)
            assert len(documents) == 1
            assert documents[0].manifest_id == current.manifest_id

    def test_ambiguous_translation_is_reported_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            publish_approved_glossary(
                workspace,
                [
                    glossary_entry("Hello", "你好"),
                    glossary_entry("Hello", "您好"),
                ],
            )
            report = run_preprocessing_stage(workspace, AppConfig())
            assert set(report.ambiguous_terms["Hello"]) == {"你好", "您好"}
            document = load_preprocessed_documents(workspace)[0]
            assert document.segments[1].processed_text.startswith("Hello")

    def test_conflict_error_policy_marks_stage_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base)
            publish_approved_glossary(
                workspace,
                [glossary_entry("Hello", "你好"), glossary_entry("Hello", "您好")],
            )
            config = AppConfig.model_validate(
                {"preprocessing": {"conflict_policy": "error"}}
            )
            with pytest.raises(Exception):
                run_preprocessing_stage(workspace, config)
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "preprocess")["status"] == "failed"
            finally:
                connection.close()

    def test_chinese_to_english_stage(self) -> None:
        chinese_chapter = CHAPTER.replace(
            b"Hello <em>small</em> world.",
            "阿斯特遇见了<em>石鹭</em>。".encode("utf-8"),
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base, chapter=chinese_chapter)
            publish_approved_glossary(
                workspace,
                [
                    glossary_entry("Aster", "阿斯特"),
                    glossary_entry("Stone Heron", "石鹭"),
                ],
            )
            config = AppConfig.model_validate(
                {
                    "translation": {"direction": "zh-en"},
                    "preprocessing": {"mode": "replace"},
                }
            )
            report = run_preprocessing_stage(workspace, config)
            assert report.mode == "replace"
            assert report.replacement_count == 2
            text = load_preprocessed_documents(workspace)[0].segments[1].processed_text
            assert text == "Aster遇见了<I000>Stone Heron</I000>。"

    def test_stage_requires_approved_glossary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.make_workspace(Path(directory))
            with pytest.raises(RuntimeError, match="approve_glossary"):
                run_preprocessing_stage(workspace, AppConfig())

    def test_series_glossary_is_late_bound_with_book_precedence(self) -> None:
        chapter = CHAPTER.replace(b"Hello", b"Cipher Spruce").replace(
            b"small", b"Vector Harbor"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = self.make_workspace(base, chapter=chapter)
            publish_approved_glossary(
                workspace,
                [glossary_entry("Cipher Spruce", "\u7532\u672c")],
            )
            series_path = base / "series-obfuscated.json"
            atomic_write_text(
                series_path,
                GlossaryResult(entries=[]).model_dump_json(indent=2),
            )
            config = AppConfig.model_validate(
                {"glossary": {"series_glossaries": [str(series_path)]}}
            )

            run_preprocessing_stage(workspace, config)
            connection = connect_state(workspace.state_file)
            try:
                first_input_hash = get_stage_status(connection, "preprocess")[
                    "input_hash"
                ]
            finally:
                connection.close()
            atomic_write_text(
                series_path,
                GlossaryResult(
                    entries=[
                        glossary_entry("Cipher Spruce", "\u7532\u5171"),
                        glossary_entry("Vector Harbor", "\u4e59\u5171"),
                    ]
                ).model_dump_json(indent=2),
            )
            run_preprocessing_stage(workspace, config)

            relevant = {
                entry.english: entry.chinese
                for entry in load_preprocessed_documents(workspace)[0].relevant_glossary
            }
            assert relevant == {"Cipher Spruce": "\u7532\u672c", "Vector Harbor": "\u4e59\u5171"}
            connection = connect_state(workspace.state_file)
            try:
                assert get_stage_status(connection, "preprocess")["input_hash"] != first_input_hash
            finally:
                connection.close()

