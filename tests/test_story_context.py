"""Phase 3 of docs/BOOK_CONSISTENCY.md: chapter summaries as translation context."""

import json
import tempfile
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.pipeline_state import ADDED_LATER_MESSAGE, WorkflowStage, initialize_pipeline_stages
from book_agent.state import StageStatus, connect_state, get_stage_status, initialize_state, set_stage_status
from book_agent.story_context import (
    ChapterSummary,
    DocumentSummary,
    format_story_context,
    story_context_by_document,
)
from tests.epub_fixture import make_epub
from tests.test_style_sheet import SchemaFakeClient


def _summary(document_id: str, order: int, events: str, **extra) -> DocumentSummary:
    return DocumentSummary(
        document_id=document_id, order=order, title=f"Chapter {order}",
        summary=ChapterSummary(events=events, **extra),
    )


class FormatTests:
    def test_each_document_sees_its_previous_chapters_and_itself(self):
        summaries = [
            _summary("c3", 3, "The Queen holds a croquet game."),
            _summary("c1", 1, "Alice falls down a rabbit hole.", characters=["Alice", "White Rabbit"]),
            _summary("c2", 2, "Alice meets the Mouse.", open_threads=["The Mouse's history"]),
        ]
        blocks = story_context_by_document(summaries, chapters_before=1)
        assert "Earlier, Chapter 1" not in blocks["c3"]
        assert "Earlier, Chapter 2: Alice meets the Mouse. Open threads: The Mouse's history." in blocks["c3"]
        assert "This chapter, Chapter 3: The Queen holds a croquet game." in blocks["c3"]
        assert "Characters: Alice, White Rabbit." in blocks["c1"] and "Earlier" not in blocks["c1"]
        assert "context only" in blocks["c1"] and "the source wording and the scene decide" in blocks["c1"]

    def test_no_earlier_chapters_when_set_to_zero(self):
        blocks = story_context_by_document(
            [_summary("c1", 1, "One."), _summary("c2", 2, "Two.")], chapters_before=0
        )
        assert "Earlier" not in blocks["c2"] and "This chapter" in blocks["c2"]

    def test_nothing_to_say_is_empty(self):
        assert format_story_context([], None) == ""


def _config(**story) -> AppConfig:
    return AppConfig.model_validate({
        "consistency": {"story_context": {"enabled": True, **story}},
        "workflow": {"require_glossary_review": False},
    })


def _decompiled(directory, config):
    from book_agent.stages.decompile import run_decompile_stage
    from book_agent.workspace import create_job_workspace

    base = Path(directory)
    workspace = create_job_workspace(make_epub(base / "story.epub"), base / "runs", config, job_id="story")
    run_decompile_stage(workspace)
    return workspace


SUMMARY = {"characters": ["Alice"], "events": "Alice reads a small world.", "open_threads": ["What the world is"]}


class StageTests:
    def test_summarizes_each_chapter_once_and_reuses_it(self):
        from book_agent.stages.story_context import load_story_summaries, run_story_context_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _decompiled(directory, config)
            client = SchemaFakeClient([SUMMARY])
            report = run_story_context_stage(workspace, config, client)
            assert (report.enabled, report.document_count, report.summarized_count) == (True, 1, 1)
            assert "Summarize this book chapter" in client.prompts[0] and "Hello" in client.prompts[0]
            (item,) = load_story_summaries(workspace)
            assert item.summary.events == "Alice reads a small world."

            # How many earlier chapters a chunk sees is preprocessing's business: no new calls.
            run_story_context_stage(workspace, _config(chapters_before=1), SchemaFakeClient([]))

            # After an interruption the finished chapters are reused.
            connection = connect_state(workspace.state_file)
            try:
                set_stage_status(connection, WorkflowStage.BUILD_STORY_CONTEXT.value, StageStatus.RUNNING, attempts=1)
            finally:
                connection.close()
            report = run_story_context_stage(workspace, config, SchemaFakeClient([]))
            assert (report.summarized_count, report.reused_count) == (0, 1)

    def test_switched_off_it_publishes_an_empty_report_without_a_model(self):
        from book_agent.stages.story_context import load_story_summaries, run_story_context_stage

        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig()
            workspace = _decompiled(directory, config)
            report = run_story_context_stage(workspace, config, None)
            assert not report.enabled and report.summarized_count == 0
            assert load_story_summaries(workspace) == []

    def test_switched_on_it_needs_a_model(self):
        from book_agent.stages.story_context import run_story_context_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _decompiled(directory, config)
            with pytest.raises(ValueError, match="Ollama client"):
                run_story_context_stage(workspace, config, None)

    def test_the_story_reaches_preprocessing_and_the_translation_prompt(self):
        from book_agent.stages.preprocess import load_preprocessed_documents, run_preprocessing_stage
        from book_agent.stages.story_context import run_story_context_stage
        from book_agent.stages.translate import _build_chunk_prompt
        from book_agent.translation import build_translation_chunks
        from tests.test_preprocess_stage import publish_approved_glossary

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _decompiled(directory, config)
            run_story_context_stage(workspace, config, SchemaFakeClient([SUMMARY]))
            publish_approved_glossary(workspace, [])
            run_preprocessing_stage(workspace, config)
            (document,) = load_preprocessed_documents(workspace)
            assert "Alice reads a small world." in document.story_context
            chunk = build_translation_chunks(document, 4000)[0]
            prompt = _build_chunk_prompt(document, chunk, [], config)
            assert "Story so far (context only" in prompt and "Alice reads a small world." in prompt
            off = AppConfig.model_validate({"workflow": {"require_glossary_review": False}})
            assert "Story so far" not in _build_chunk_prompt(document, chunk, [], off)


def test_a_document_without_story_context_serializes_as_before():
    from book_agent.preprocessing import PreprocessedDocument

    document = PreprocessedDocument(order=1, manifest_id="m", archive_path="a", source_sha256="x", segments=[])
    assert set(json.loads(document.model_dump_json())) == {
        "order", "manifest_id", "archive_path", "source_sha256", "segments", "relevant_glossary",
    }


def test_a_job_preprocessed_before_the_stage_existed_stays_complete():
    with tempfile.TemporaryDirectory() as directory:
        connection = connect_state(Path(directory) / "state.sqlite3")
        try:
            initialize_state(connection)
            set_stage_status(connection, WorkflowStage.PREPROCESS.value, StageStatus.COMPLETED)
            initialize_pipeline_stages(connection)
            record = get_stage_status(connection, WorkflowStage.BUILD_STORY_CONTEXT.value)
            assert (record["status"], record["message"]) == ("completed", ADDED_LATER_MESSAGE)
        finally:
            connection.close()


def test_the_workflow_creates_a_model_client_only_when_story_context_is_on():
    from book_agent.workflow import _stage_uses_ollama

    assert not _stage_uses_ollama(WorkflowStage.BUILD_STORY_CONTEXT, AppConfig())
    assert _stage_uses_ollama(WorkflowStage.BUILD_STORY_CONTEXT, _config())


def test_a_summary_at_the_longest_configured_length_is_accepted():
    """max_summary_words goes up to 300; the model follows the prompt, so the schema must too."""
    from book_agent.config import StoryContextConfig

    longest = StoryContextConfig.model_fields["max_summary_words"].metadata
    limit = next(item.le for item in longest if getattr(item, "le", None) is not None)
    events = " ".join(["wandering"] * limit)  # longer than average English words
    assert ChapterSummary(events=events).events == events
