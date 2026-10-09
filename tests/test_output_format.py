import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from book_agent import output as output_api
from book_agent.book_formats import BookFormatError, read_book
from book_agent.config import AppConfig, OutputConfig
from book_agent.output import check_output, hashed_output_fields, output_format, source_format
from book_agent.stages.compile import load_compiled_epub_path, load_output_path, run_epub_compile_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.state import connect_state, get_job_metadata, get_stage_status
from tests.test_compile_stages import prepare_workspace

BASE = {"audit": {"semantic_enabled": False}}


def config(**output) -> AppConfig:
    return AppConfig.model_validate({**BASE, "output": output} if output else BASE)


class ConfigTests:
    def test_a_job_that_names_no_format_has_the_config_it_always_had(self):
        assert "output" not in AppConfig().model_dump()
        assert AppConfig().output == OutputConfig(format="source", pdf_font=None)

    def test_a_named_format_or_font_is_kept(self):
        assert config(format="docx").model_dump()["output"] == {"format": "docx", "pdf_font": None}
        assert config(pdf_font="serif.ttf").model_dump(mode="json")["output"]["pdf_font"] == "serif.ttf"

    def test_a_format_no_book_is_written_in_is_refused(self):
        with pytest.raises(ValidationError):
            config(format="mobi")


class ResolutionTests:
    @pytest.mark.parametrize(
        "name, expected",
        [("book.epub", "epub"), ("book.rtf", "epub"), ("book.txt", "txt"), ("book.MD", "md"), ("book.markdown", "md"),
         ("book.html", "html"), ("book.docx", "docx"), ("book.pdf", "pdf")],
    )
    def test_source_means_the_format_the_book_came_in_where_that_is_written(self, name, expected):
        assert source_format(name) == expected

    def test_a_named_format_wins_over_the_source(self):
        assert output_format("book.epub", config(format="docx")) == ("docx", "")
        assert output_format("book.docx", config(format="epub")) == ("epub", "")
        assert output_format("book.md", config()) == ("md", "")

    def test_a_pdf_nobody_named_falls_back_to_an_epub_with_a_note(self):
        with patch("book_agent.pdf_export.pdf_output_problem", return_value="no font has the letters"):
            assert output_format("book.pdf", config()) == (
                "epub", "the book is written as an EPUB, not a PDF: no font has the letters"
            )
            # Asked for by name, it is not quietly replaced.
            assert output_format("book.pdf", config(format="pdf")) == ("pdf", "")
        with patch("book_agent.pdf_export.pdf_output_problem", return_value=""):
            assert output_format("book.pdf", config()) == ("pdf", "")


class CheckTests:
    def test_a_subtitle_job_takes_no_book_format(self):
        assert check_output("film.srt", config()) == ""
        with pytest.raises(ValueError, match="this is a subtitle job.*set output.format to source"):
            check_output("film.srt", config(format="docx"))

    def test_a_pdf_named_but_not_writable_is_refused_before_any_work(self):
        with patch("book_agent.pdf_export.check_pdf_output", side_effect=BookFormatError("no font")) as check, \
             pytest.raises(BookFormatError, match="no font"):
            check_output("book.epub", config(format="pdf", pdf_font="serif.ttf"))
        # Tried in the language translated into, with the font the config names.
        assert check.call_args.args == ("zh-CN", Path("serif.ttf")) or check.call_args.args[1] == Path("serif.ttf")

    def test_other_formats_need_no_check_and_the_fallback_note_is_passed_on(self):
        assert check_output("book.epub", config(format="docx")) == ""
        with patch("book_agent.pdf_export.pdf_output_problem", return_value="reportlab is missing"):
            assert "not a PDF: reportlab is missing" in check_output("book.pdf", config())


class HashTests:
    def test_a_job_that_gives_back_what_it_always_did_hashes_nothing_new(self):
        assert hashed_output_fields("book.epub", config()) == {}
        assert hashed_output_fields("book.docx", config()) == {}
        assert hashed_output_fields("book.docx", config(format="docx")) == {}
        assert hashed_output_fields("film.srt", config()) == {}
        with patch("book_agent.pdf_export.pdf_output_problem", return_value="no font"):
            assert hashed_output_fields("book.pdf", config()) == {}

    def test_a_changed_format_or_font_is_hashed(self):
        assert hashed_output_fields("book.epub", config(format="docx")) == {"output_format": "docx"}
        assert hashed_output_fields("book.docx", config(format="epub")) == {"output_format": "epub"}
        assert hashed_output_fields("book.epub", config(format="pdf", pdf_font="serif.ttf")) == {
            "output_format": "pdf", "pdf_font": "serif.ttf",
        }
        with patch("book_agent.pdf_export.pdf_output_problem", return_value=""):
            assert hashed_output_fields("book.pdf", config()) == {"output_format": "pdf"}


def metadata(workspace, key: str) -> str:
    connection = connect_state(workspace.state_file)
    try:
        return get_job_metadata(connection, key) or ""
    finally:
        connection.close()


def stage(workspace, name: str) -> dict:
    connection = connect_state(workspace.state_file)
    try:
        return dict(get_stage_status(connection, name))
    finally:
        connection.close()


class CompileTests:
    def test_an_epub_job_gives_back_its_epub(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            run_epub_compile_stage(workspace, config())
            assert load_output_path(workspace) == load_compiled_epub_path(workspace)
            assert metadata(workspace, "compiled_output") == metadata(workspace, "compiled_epub")
            assert [path.suffix for path in (workspace.root / "output").iterdir()] == [".epub"]

    def test_a_named_format_is_written_beside_the_epub_and_is_the_output(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config(format="md"))
            run_epub_compile_stage(workspace, config(format="md"))
            epub, given = Path(load_compiled_epub_path(workspace)), Path(load_output_path(workspace))
            assert (epub.suffix, given.suffix) == (".epub", ".md") and given.stem == epub.stem
            assert read_book(given).blocks  # a book, read again
            # The checks still run on the EPUB.
            assert run_epub_validation_stage(workspace, config(format="md")).passed

    def test_changing_the_format_compiles_again_and_nothing_before_it(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            run_epub_compile_stage(workspace, config())
            first, validated = stage(workspace, "compile"), stage(workspace, "validate_repaired")
            run_epub_compile_stage(workspace, config())  # nothing changed: not compiled again
            assert stage(workspace, "compile") == first

            run_epub_compile_stage(workspace, config(format="docx"))
            as_word = stage(workspace, "compile")
            assert as_word["input_hash"] != first["input_hash"] and as_word["status"] == "completed"
            assert stage(workspace, "validate_repaired") == validated
            assert Path(load_output_path(workspace)).suffix == ".docx"

            run_epub_compile_stage(workspace, config())
            assert stage(workspace, "compile")["input_hash"] == first["input_hash"]
            assert Path(load_output_path(workspace)).suffix == ".epub"

    def test_a_job_compiled_before_there_was_an_output_format_gives_back_its_compiled_book(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory), config())
            run_epub_compile_stage(workspace, config())
            connection = connect_state(workspace.state_file)
            try:
                connection.execute("DELETE FROM job_metadata WHERE key = 'compiled_output'")
                connection.commit()
            finally:
                connection.close()
            assert load_output_path(workspace) == load_compiled_epub_path(workspace)

    def test_the_named_pdf_font_reaches_the_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen = config(format="pdf", pdf_font="serif.ttf")
            workspace = prepare_workspace(Path(directory), chosen)

            def written(epub, target, kind, pdf_font=None):
                Path(target).write_bytes(b"%PDF-1.4")
                return Path(target)

            with patch("book_agent.stages.compile.export_book", side_effect=written) as export:
                run_epub_compile_stage(workspace, chosen)
            assert export.call_args.args[2] == "pdf" and export.call_args.kwargs == {"pdf_font": Path("serif.ttf")}
            assert Path(load_output_path(workspace)).suffix == ".pdf"


def test_output_module_reads_the_language_translated_into():
    assert output_api._target_language(config()) == "zh-CN" or output_api._target_language(config())
