"""The title stage: the book's title and its table of contents in the target language, for the compiled book to carry."""

from __future__ import annotations

import tempfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from book_agent.book_formats import read_epub
from book_agent.config import AppConfig, TranslationConfig
from book_agent.ollama_client import GenerationMetrics, GenerationResult
from book_agent.pipeline_state import WorkflowStage, initialize_pipeline_stages
from book_agent.stages.compile import load_compiled_epub_path, run_epub_compile_stage
from book_agent.stages.title import (
    book_title,
    build_contents_prompt,
    build_title_prompt,
    contents_entries,
    load_translated_title,
    run_title_stage,
)
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.state import StageStatus, connect_state, get_stage_status, set_stage_status
from tests.epub_fixture import CHAPTER, OPF, make_epub
from tests.test_compile_stages import prepare_workspace

CONFIG = {"audit": {"semantic_enabled": False}}  # English into Chinese


def _answer(content: str) -> GenerationResult:
    return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=20, eval_count=5))


class Publisher:
    """Stands in for the model: gives the title as a model does, in quotation
    marks, and the contents entries it is asked for as a numbered list. Keeps
    what it was asked, the title and the contents apart."""

    def __init__(self, answer="《狄更斯小说》", contents="1. 第一部"):
        self.answer = answer
        self.contents = contents
        self.prompts = []
        self.contents_prompts = []

    def generate_text(self, prompt, **_):
        if "table of contents" in prompt:
            self.contents_prompts.append(prompt)
            return _answer(self.contents)
        self.prompts.append(prompt)
        return _answer(self.answer)


def _book(base: Path, title: bytes, config: AppConfig, chapter: bytes = CHAPTER):
    """The fixture book: one chapter headed "Chapter One", and a table of
    contents with that heading and an entry "Part", which no passage reads."""
    source = make_epub(base / "book.epub", opf=OPF.replace(b"Fixture Book", title), chapter=chapter)
    return prepare_workspace(base, config, source=source)


def _package(workspace) -> str:
    with ZipFile(load_compiled_epub_path(workspace)) as archive:
        return archive.read("OEBPS/content.opf").decode("utf-8")


def _contents(workspace) -> str:
    with ZipFile(load_compiled_epub_path(workspace)) as archive:
        return archive.read("OEBPS/nav.xhtml").decode("utf-8")


def _stage_message(workspace) -> str:
    connection = connect_state(workspace.state_file)
    try:
        return get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)["message"]
    finally:
        connection.close()


def test_a_title_that_is_no_passage_of_the_book_is_translated_by_one_call_and_carried_by_the_compiled_book():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"A Dickens Novel", config)
        assert book_title(workspace) == "A Dickens Novel"
        publisher = Publisher()
        record = run_title_stage(workspace, config, publisher)
        assert record == {
            "source": "A Dickens Novel",
            "translated": "狄更斯小说",
            "origin": "model",
            "labels": {"Part": "第一部"},
            "notes": {},
            "descriptions": {},
            # Two calls, title and contents, as the stand-in counts its tokens.
            "usage": {"llm_calls": 2, "completed_llm_calls": 2, "prompt_tokens": 40, "output_tokens": 10, "llm_seconds": 0.0},
        }
        assert len(publisher.contents_prompts) == 1  # and one for the contents
        assert len(publisher.prompts) == 1
        assert "from English into Simplified Chinese" in publisher.prompts[0] and publisher.prompts[0].endswith("Title: A Dickens Novel")
        assert "The book's own translation" not in publisher.prompts[0]  # this title stands in no passage
        # Asked again, it answers from what it recorded: no second call.
        assert run_title_stage(workspace, config, publisher) == record and len(publisher.prompts) == 1
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        assert "<dc:title>狄更斯小说</dc:title>" in _package(workspace)
        assert read_epub(load_compiled_epub_path(workspace)).title == "狄更斯小说"


def test_a_title_that_is_a_passage_of_the_book_needs_no_call():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"Chapter One", config)  # the fixture's heading
        publisher = Publisher()
        record = run_title_stage(workspace, config, publisher)
        assert record["origin"] == "passage" and record["translated"] == "" and publisher.prompts == []
        run_epub_compile_stage(workspace, config)
        assert "<dc:title>译文。</dc:title>" in _package(workspace)  # the passage's translation


def test_a_contents_entry_that_is_no_passage_of_the_book_is_translated_and_the_others_follow_their_passages():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"Oliver Twist", config)
        # "Chapter One" is the chapter's heading; "Fixture" is what the chapter's file calls itself.
        assert contents_entries(workspace) == ["Part", "Fixture"]
        publisher = Publisher(answer="雾都孤儿", contents="1. 第一部\n2. 样书")
        record = run_title_stage(workspace, config, publisher)
        assert record["labels"] == {"Part": "第一部", "Fixture": "样书"}
        # One call for the contents. It asks for the entry alone: the title is settled as the title,
        # and the heading has its translation already, which the model is shown.
        assert len(publisher.contents_prompts) == 1
        asked = publisher.contents_prompts[0]
        assert asked.endswith("Entries:\n1. Part\n2. Fixture") and "The book's headings:\n- Chapter One => 译文。" in asked
        assert _stage_message(workspace) == "雾都孤儿; 2 of 2 contents entries translated"
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        contents = _contents(workspace)
        assert ">第一部</a>" in contents and ">译文。</a>" in contents
        assert "Part" not in contents and "Chapter One" not in contents
        # Asked again, nothing is asked of the model.
        assert run_title_stage(workspace, config, publisher) == record and len(publisher.contents_prompts) == 1


def test_a_book_whose_contents_are_all_headings_of_the_book_asks_the_model_for_none_of_them():
    config = AppConfig.model_validate({**CONFIG, "translation": {"translated_title": "雾都孤儿"}})
    with tempfile.TemporaryDirectory() as directory:
        chapter = CHAPTER.replace(b"<ul>", b"<h2 id='part'>Part</h2><ul>").replace(b"<title>Fixture<", b"<title>Chapter One<")
        workspace = _book(Path(directory), b"Oliver Twist", config, chapter)
        assert contents_entries(workspace) == []
        record = run_title_stage(workspace, config, None)  # no client at all, and none is made
        assert record == {
            "source": "Oliver Twist",
            "translated": "雾都孤儿",
            "origin": "config",
            "labels": {},
            "notes": {},
            "descriptions": {},
            "usage": {"llm_calls": 0, "completed_llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0},
        }
        assert _stage_message(workspace) == "from the config: 雾都孤儿"
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        assert "Part" not in _contents(workspace)


def test_contents_the_model_cannot_give_stay_as_they_are_and_the_book_still_compiles():
    config = AppConfig.model_validate({**CONFIG, "translation": {"translated_title": "雾都孤儿"}})

    class Down:
        def generate_text(self, prompt, **_):
            raise ConnectionError("Ollama is not running")

    # No answer at all; an answer that is not the list asked for; one that numbers an entry the book has not.
    for client, said in (
        (Down(), "0 of 2 contents entries translated (Ollama is not running)"),
        (Publisher(contents="Here is the table of contents in Chinese."), "0 of 2 contents entries translated"),
        (Publisher(contents="3. 第三部\n0. 序"), "0 of 2 contents entries translated"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _book(Path(directory), b"Oliver Twist", config)
            record = run_title_stage(workspace, config, client)
            assert record["labels"] == {} and record["translated"] == "雾都孤儿"
            assert _stage_message(workspace) == f"from the config: 雾都孤儿; {said}"
            run_epub_compile_stage(workspace, config)
            assert run_epub_validation_stage(workspace).passed
            assert ">Part</a>" in _contents(workspace) and "<dc:title>雾都孤儿</dc:title>" in _package(workspace)


def test_the_contents_prompt_numbers_the_entries_and_an_answer_is_read_by_its_numbers():
    config = AppConfig.model_validate({"translation": {"direction": "en>de"}})
    prompt = build_contents_prompt(
        ["Chapter 7", "Notes"], config, ["Alice => Alice"], [("CHAPTER VII. A Mad Tea-Party", "KAPITEL VII. Eine verrückte Teegesellschaft")]
    )
    assert "from English into German" in prompt and "Approved glossary:\nAlice => Alice" in prompt
    assert "- CHAPTER VII. A Mad Tea-Party => KAPITEL VII. Eine verrückte Teegesellschaft" in prompt
    assert prompt.endswith("Entries:\n1. Chapter 7\n2. Notes")
    assert "The book's headings:\n(none)" in build_contents_prompt(["Notes"], config, [], [])

    # A model answers out of order, with a word of its own before the list, and in quotation marks.
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        chapter = CHAPTER.replace(b"<title>Fixture</title>", b"<title>Notes on the text</title>")
        workspace = _book(Path(directory), b"Chapter One", config, chapter)
        assert contents_entries(workspace) == ["Part", "Notes on the text"]
        publisher = Publisher(contents="Here are the entries:\n2) “文本说明”\n1. 第一部\n3. 多出来的一行")
        assert run_title_stage(workspace, config, publisher)["labels"] == {"Part": "第一部", "Notes on the text": "文本说明"}
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        with ZipFile(load_compiled_epub_path(workspace)) as archive:
            assert "<title>文本说明</title>" in archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")


def test_the_configs_title_is_used_as_it_is_and_no_model_is_asked():
    config = AppConfig.model_validate({**CONFIG, "translation": {"translated_title": "雾都孤儿"}})
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"Oliver Twist", config)
        publisher = Publisher()
        record = run_title_stage(workspace, config, publisher)
        assert (record["source"], record["translated"], record["origin"]) == ("Oliver Twist", "雾都孤儿", "config")
        assert publisher.prompts == []  # asked for the contents only
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        assert "<dc:title>雾都孤儿</dc:title>" in _package(workspace)
        assert _stage_message(workspace).startswith("from the config: 雾都孤儿")


def test_a_title_the_model_cannot_give_does_not_stop_the_book():
    config = AppConfig.model_validate(CONFIG)

    class Down:
        def generate_text(self, prompt, **_):
            raise ConnectionError("Ollama is not running")

    for client, why in ((Publisher(answer="  \n"), "the model gave no usable title"), (Down(), "Ollama is not running")):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _book(Path(directory), b"Oliver Twist", config)
            record = run_title_stage(workspace, config, client)
            assert (record["source"], record["translated"], record["origin"]) == ("Oliver Twist", "", "none")
            connection = connect_state(workspace.state_file)
            try:
                stage = get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)
            finally:
                connection.close()
            # The stage is done, and says what happened and what to do about it.
            assert stage["status"] == StageStatus.COMPLETED.value and why in stage["message"]
            assert "set translation.translated_title in the config and rerun from here" in stage["message"]
            run_epub_compile_stage(workspace, config)
            assert "<dc:title>Oliver Twist</dc:title>" in _package(workspace)  # under the title it has


def test_the_title_prompt_names_the_languages_and_the_glossary():
    config = AppConfig.model_validate({"translation": {"direction": "en>de"}})
    prompt = build_title_prompt("The Sign of the Four", config, ["Holmes => Holmes"])
    assert "from English into German" in prompt and "Approved glossary:\nHolmes => Holmes" in prompt
    assert prompt.endswith("Title: The Sign of the Four")
    assert "(none)" in build_title_prompt("Alice", config, [])
    # Where the title stands in a passage of the book, the model is shown how the book put it there.
    shown = build_title_prompt(
        "A Mad Tea-Party", config, [], [("CHAPTER VII. A Mad Tea-Party", "KAPITEL VII. Eine verr\u00fcckte Teegesellschaft")]
    )
    assert "- CHAPTER VII. A Mad Tea-Party => KAPITEL VII. Eine verr\u00fcckte Teegesellschaft" in shown
    assert "Word the title as the book words it there" in shown and shown.endswith("Title: A Mad Tea-Party")


def test_a_books_config_is_stored_as_before_unless_it_sets_a_title():
    assert "translated_title" not in TranslationConfig().model_dump()
    assert TranslationConfig(translated_title="Alice im Wunderland").model_dump()["translated_title"] == "Alice im Wunderland"


def test_a_job_compiled_before_there_was_a_title_stage_still_reads_as_complete():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"Oliver Twist", config)
        run_epub_compile_stage(workspace, config)
        connection = connect_state(workspace.state_file)
        try:
            # As an older job's state has it: every stage done, and no row for the new one.
            connection.execute("DELETE FROM stages WHERE name = ?", (WorkflowStage.TRANSLATE_TITLE.value,))
            connection.commit()
            initialize_pipeline_stages(connection)
            record = get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)
            assert record["status"] == StageStatus.COMPLETED.value
            # One that had not compiled yet gets the stage to run.
            connection.execute("DELETE FROM stages WHERE name = ?", (WorkflowStage.TRANSLATE_TITLE.value,))
            set_stage_status(connection, WorkflowStage.COMPILE.value, StageStatus.PAUSED)
            connection.commit()
            initialize_pipeline_stages(connection)
            assert get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)["status"] == StageStatus.PENDING.value
        finally:
            connection.close()
        assert load_translated_title(workspace) == {
            "source": "", "translated": "", "origin": "none", "labels": {}, "notes": {}, "descriptions": {}, "usage": {}
        }


class Counted(Publisher):
    """The stand-in as a run's client is: it hears which part of the stage a
    call is for, and is told the stage's plan, as the run's log is."""

    def __init__(self, **options):
        super().__init__(**options)
        self.roles = []
        self.events = []

    def generate_text(self, prompt, **options):
        self.roles.append(options.get("usage_role"))
        return super().generate_text(prompt, **options)

    def report_progress(self, event):
        self.events.append(event)


def test_the_stages_calls_are_counted_and_recorded_as_every_stages_are():
    from book_agent.state import list_attempts

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"A Dickens Novel", config)
        client = Counted(contents="1. 第一部\n2. 样书")
        record = run_title_stage(workspace, config, client)
        # Each call under its own role, for the run's summary to show beneath the stage.
        assert client.roles == ["title.book", "title.contents"]
        # The plan, as the other stages give theirs: the title and two entries to settle, in two calls.
        (plan,) = [event for event in client.events if event.kind == "stage_plan"]
        assert (plan.prescreened, plan.llm_tasks, plan.skipped, plan.role) == (3, 2, 0, "title")
        assert record["usage"] == {
            "llm_calls": 2, "completed_llm_calls": 2, "prompt_tokens": 40, "output_tokens": 10, "llm_seconds": 0.0
        }
        connection = connect_state(workspace.state_file)
        try:
            (title_call,) = list_attempts(connection, "translate_title:title")
            (contents_call,) = list_attempts(connection, "translate_title:contents:0001")
        finally:
            connection.close()
        assert title_call["status"] == contents_call["status"] == StageStatus.COMPLETED.value
        assert (title_call["metrics"]["prompt_eval_count"], title_call["metrics"]["eval_count"]) == (20, 5)


def test_a_call_that_fails_is_recorded_as_a_failed_attempt():
    from book_agent.state import list_attempts

    class Down:
        def generate_text(self, prompt, **_):
            raise ConnectionError("Ollama is not running")

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"A Dickens Novel", config)
        record = run_title_stage(workspace, config, Down())
        # No answer is not worth asking again: one call for the title, one for the contents.
        assert record["usage"]["llm_calls"] == 2
        assert record["usage"]["completed_llm_calls"] == 0
        connection = connect_state(workspace.state_file)
        try:
            (failed,) = list_attempts(connection, "translate_title:title")
        finally:
            connection.close()
        assert failed["status"] == StageStatus.FAILED.value and "Ollama is not running" in failed["message"]


def test_a_run_hands_the_title_stage_its_logged_client_without_checking_the_models(monkeypatch):
    from book_agent.workflow import default_stage_runners, run_workflow

    made = []

    class RunsClient:
        """In place of the client a run makes: it remembers where its progress goes, and checks no model."""

        def __init__(self, config, progress=None, pause_check=None):
            self.progress = progress
            made.append(self)

    monkeypatch.setattr("book_agent.workflow.OllamaClient", RunsClient)
    config = AppConfig.model_validate(CONFIG)
    heard = []
    given = []
    runners = dict(default_stage_runners())
    runners[WorkflowStage.TRANSLATE_TITLE] = lambda workspace, config, client: (
        given.append(client),
        run_title_stage(workspace, config, Publisher()),
    )
    with tempfile.TemporaryDirectory() as directory:
        workspace = _book(Path(directory), b"A Dickens Novel", config)
        # A job whose chapters are done, as one paused before the compile is: every stage before the title's complete.
        connection = connect_state(workspace.state_file)
        try:
            for stage in WorkflowStage:
                if stage is WorkflowStage.TRANSLATE_TITLE:
                    break
                set_stage_status(connection, stage.value, StageStatus.COMPLETED)
            connection.commit()
        finally:
            connection.close()
        run_workflow(workspace, config, generation_progress=heard.append, stage_runners=runners)
    # The title stage was the first to want a model in this run, and was given the run's client.
    assert len(made) == 1 and given == made and made[0].progress == heard.append


def test_the_runs_summary_shows_the_title_stages_calls_beneath_it_by_part():
    from book_agent.cli import CliProgressContext, _format_processing_summary, _record_generation_progress
    from book_agent.ollama_client import GenerationProgressEvent

    context = CliProgressContext(stage=WorkflowStage.TRANSLATE_TITLE.value)
    for role in ("title.book", "title.contents", "title.notes"):
        _record_generation_progress(context, GenerationProgressEvent("started", "qwen3.8", role=role))
        _record_generation_progress(
            context,
            GenerationProgressEvent(
                "completed", "qwen3.8", role=role, metrics=GenerationMetrics(prompt_eval_count=200, eval_count=30, total_duration_ns=2 * 10**9)
            ),
        )
    lines = _format_processing_summary(context)
    stage = next(line for line in lines if line.startswith("translate_title"))
    assert stage.split()[2:5] == ["0.0s", "3", "3"] and "600" in stage and "90" in stage
    parts = [line.split()[0] for line in lines[lines.index(stage) + 1 : lines.index(stage) + 4]]
    assert parts == ["title.book", "title.contents", "title.notes"]
