"""A converted book's pictures and notes: kept from the source to the translated book and its exports.

Pictures are the same files throughout. Notes are the book's words but no
passage of it: the title stage translates them, as it does the contents.
"""

from __future__ import annotations

import base64
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from book_agent.book_formats import export_book, picture_size, read_book, read_epub
from book_agent.config import AppConfig
from book_agent.ollama_client import GenerationMetrics, GenerationResult
from book_agent.stages.compile import load_compiled_epub_path, run_epub_compile_stage
from book_agent.stages.decompile import load_decompile_manifest, run_decompile_stage
from book_agent.stages.title import note_entries, run_title_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.workspace import PICTURES_DIRECTORY, create_job_workspace
from tests.test_compile_stages import prepare_workspace
from tests.test_translate_stage import FakeTranslationClient

# Saved by Word: a heading, a paragraph with a footnote, a picture with its description, a paragraph with an endnote.
WORD = Path(__file__).parent / "data" / "word-pictures-and-notes.docx"
CONFIG = {"audit": {"semantic_enabled": False}}  # English into Chinese


def _map() -> bytes:
    """The picture Word kept in the document: a sketch of the grounds, 240 by 160."""
    with zipfile.ZipFile(WORD) as archive:
        return archive.read("word/media/image1.png")


class KeepsWhatInlineElementsHold(FakeTranslationClient):
    """Translates every passage as the usual stand-in does ("译文。"), but keeps
    what the passage's inline elements hold, as a model keeps a note's number."""

    def generate_text(self, prompt, **options):
        source = prompt.split("Source:\n", 1)[1]
        rendered = []
        for item, text in re.findall(r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", source, flags=re.DOTALL):
            first = text.find("<I")
            kept = text[first : text.rfind(">") + 1] if first >= 0 else ""
            rendered.append(f"<{item}>译文。{kept}</{item}>")
        self.prompts.append(prompt)
        return GenerationResult(content="\n".join(rendered), thinking="", metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20))


class NoteTranslator:
    """Stands in for the model at the title stage: gives the notes, and the
    picture's description, it is asked for, numbered, in Chinese."""

    NOTES = {
        "Pondicherry Lodge stood in Upper Norwood, south of London.": "樱沼别墅位于伦敦南部的上诺伍德。",
        "The fog of London was famous in its day.": "伦敦的雾在当年很有名。",
    }
    DESCRIPTIONS = {"A map of the grounds of Pondicherry Lodge": "樱沼别墅庭院地图"}

    def __init__(self):
        self.prompts = []

    def generate_text(self, prompt, **_):
        self.prompts.append(prompt)
        heading, known = ("Notes:\n", self.NOTES) if "Notes:\n" in prompt else ("Descriptions:\n", self.DESCRIPTIONS)
        asked = prompt.split(heading, 1)[-1].splitlines()
        lines = [f"{line.split('. ', 1)[0]}. {known.get(line.split('. ', 1)[-1], '')}" for line in asked]
        return GenerationResult(content="\n".join(lines), thinking="", metrics=GenerationMetrics(prompt_eval_count=30, eval_count=12))


def test_a_word_documents_footnote_endnote_and_picture_are_read_where_they_stand():
    book = read_book(WORD)
    assert [(block.kind, block.src) for block in book.blocks] == [
        ("h1", ""),
        ("p", ""),
        ("note", "note-1"),  # the footnote, after the paragraph that refers to it
        ("img", "image1.png"),
        ("p", ""),
        ("note", "note-2"),  # the endnote, numbered on through the book
    ]
    assert book.blocks[1].html.endswith('adventures.<sup><a href="#note-1">1</a></sup>')
    assert book.blocks[2].html == "Pondicherry Lodge stood in Upper Norwood, south of London."
    assert book.blocks[3].html == "A map of the grounds of Pondicherry Lodge"  # what Word was told the picture shows
    assert book.images == {"image1.png": _map()}


def test_a_word_books_picture_and_notes_reach_the_translated_book_and_its_word_document():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = shutil.copy(WORD, base / "pondicherry.docx")
        workspace = prepare_workspace(base, config, source=Path(source), translator=KeepsWhatInlineElementsHold())
        # The notes are no passages: the chapters are translated without them.
        manifest = load_decompile_manifest(workspace)
        passages = [segment.text for document in manifest.documents for segment in document.segments]
        assert not any("Upper Norwood" in passage or "famous in its day" in passage for passage in passages)
        assert note_entries(workspace) == list(NoteTranslator.NOTES)

        translator = NoteTranslator()
        record = run_title_stage(workspace, config, translator)
        # Each paragraph of a note by its place in the book; the picture's description by its words.
        assert record["notes"] == dict(zip(["D0000-N000001", "D0000-N000002"], NoteTranslator.NOTES.values()))
        assert record["descriptions"] == NoteTranslator.DESCRIPTIONS
        assert len(translator.prompts) == 2 and "footnotes of a book from English into Simplified Chinese" in translator.prompts[0]
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed

        compiled = load_compiled_epub_path(workspace)
        with zipfile.ZipFile(compiled) as archive:
            assert archive.read("OEBPS/images/image1.png") == _map()  # the same picture, byte for byte
            chapter = archive.read("OEBPS/text/chapter-0001.xhtml").decode("utf-8")
        assert re.search(r'<img src="\.\./images/image1\.png" alt="樱沼别墅庭院地图" ?/>', chapter)
        assert '<a epub:type="noteref" href="#note-1">1</a>' in chapter  # a reader can show the note beside the text
        assert "樱沼别墅位于伦敦南部的上诺伍德。" in chapter and "Upper Norwood" not in chapter

        # The Word document made beside it has the picture, and the notes as Word's own footnotes.
        word = Path(compiled).with_suffix(".docx")
        with zipfile.ZipFile(word) as archive:
            assert archive.read("word/media/image1.png") == _map()
            footnotes = archive.read("word/footnotes.xml").decode("utf-8")
            document = archive.read("word/document.xml").decode("utf-8")
        assert "伦敦的雾在当年很有名。" in footnotes
        assert document.count("<w:footnoteReference") == 2 and "<w:drawing>" in document
        again = read_book(word)
        assert [block.kind for block in again.blocks] == ["h1", "p", "note", "img", "p", "note"]
        assert again.images == {"image1.png": _map()}


def test_markdown_pictures_beside_the_file_are_kept_with_the_job_after_the_folder_is_gone():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        folder = base / "manuscript"
        (folder / "plates").mkdir(parents=True)
        (folder / "plates" / "norwood.png").write_bytes(_map())
        (base / "elsewhere.png").write_bytes(_map())
        source = folder / "pondicherry.md"
        source.write_text(
            "# Chapter V\n\n"
            "It was nearly eleven o'clock when we reached Pondicherry Lodge.[^lodge]\n\n"
            "![The grounds of Pondicherry Lodge](plates/norwood.png)\n\n"
            "![A plate kept outside the manuscript](../elsewhere.png)\n\n"
            "![A plate on the web](https://example.com/plate.png)\n\n"
            "![A plate that was lost](plates/lost.png)\n\n"
            "[^lodge]: Pondicherry Lodge stood in Upper Norwood.\n\n"
            "    It was named for the town in India.\n",
            encoding="utf-8",
        )
        workspace = create_job_workspace(source, base / "runs", config, job_id="pondicherry")
        kept = workspace.root / PICTURES_DIRECTORY
        assert [path.relative_to(kept).as_posix() for path in kept.rglob("*") if path.is_file()] == ["plates/norwood.png"]
        shutil.rmtree(folder)
        (base / "elsewhere.png").unlink()

        run_decompile_stage(workspace)
        package = next(workspace.root.rglob("package"))
        assert (package / "OEBPS" / "images" / "norwood.png").read_bytes() == _map()
        chapter = (package / "OEBPS" / "text" / "chapter-0001.xhtml").read_text(encoding="utf-8")
        assert '<img src="../images/norwood.png" alt="The grounds of Pondicherry Lodge"/>' in chapter
        # A picture the job could not keep is its description, a passage to translate.
        passages = [segment.text for document in load_decompile_manifest(workspace).documents for segment in document.segments]
        assert {"A plate kept outside the manuscript", "A plate on the web", "A plate that was lost"} <= set(passages)
        # The note's two paragraphs, marked as a note, after the chapter.
        assert '<aside id="note-1" epub:type="footnote">' in chapter
        assert '<p data-book-agent-note="1">Pondicherry Lodge stood in Upper Norwood.</p>' in chapter
        assert '<p data-book-agent-note="1">It was named for the town in India.</p>' in chapter


def test_a_picture_held_in_an_html_file_is_kept_and_its_caption_is_a_passage():
    encoded = base64.b64encode(_map()).decode("ascii")
    with tempfile.TemporaryDirectory() as directory:
        page = Path(directory) / "plates.html"
        page.write_text(
            "<html><body><h1>Plates</h1><figure>"
            f'<img src="data:image/png;base64,{encoded}" alt="The grounds"/>'
            "<figcaption>The grounds of Pondicherry Lodge.</figcaption></figure></body></html>",
            encoding="utf-8",
        )
        book = read_book(page)
    assert [(block.kind, block.html) for block in book.blocks] == [
        ("h1", "Plates"),
        ("img", "The grounds"),
        ("p", "The grounds of Pondicherry Lodge."),
    ]
    assert list(book.images.values()) == [_map()]


def test_a_book_with_a_picture_and_notes_is_written_in_each_format_with_both():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        from book_agent.book_formats import write_source_package

        package = base / "package"
        write_source_package(read_book(WORD), package, "x")
        epub = base / "book.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package).as_posix())
        # In the EPUB, a chapter's notes are at its end.
        assert [block.kind for block in read_epub(epub).blocks] == ["h1", "p", "img", "p", "note", "note"]

        text = export_book(epub, base / "book.txt", "txt").read_text(encoding="utf-8")
        assert "adventures.[1]" in text and "[1] Pondicherry Lodge stood in Upper Norwood" in text
        assert "[A map of the grounds of Pondicherry Lodge]" in text

        markdown = export_book(epub, base / "book.md", "md").read_text(encoding="utf-8")
        assert "adventures.[^1]" in markdown and "[^2]: The fog of London was famous in its day." in markdown
        assert "![A map of the grounds of Pondicherry Lodge](data:image/png;base64," in markdown
        # Read back, the Markdown is the same book.
        again = read_book(base / "book.md")
        assert [block.kind for block in again.blocks] == ["h1", "p", "note", "img", "p", "note"]
        assert list(again.images.values()) == [_map()]

        html = export_book(epub, base / "book.html", "html").read_text(encoding="utf-8")
        assert '<aside id="note-1" class="note"><p>1. Pondicherry Lodge stood' in html
        assert f'src="data:image/png;base64,{base64.b64encode(_map()).decode("ascii")}"' in html


def test_the_size_of_a_picture_is_read_from_its_header_as_word_needs_it():
    assert picture_size(_map()) == (240, 160)
    assert picture_size(b"GIF89a" + (300).to_bytes(2, "little") + (200).to_bytes(2, "little") + b"\x00" * 8) == (300, 200)
    # A JPEG: the start of the image, a section that says nothing of its size, then the frame that does.
    jpeg = b"\xff\xd8" + b"\xff\xe0\x00\x04ab" + b"\xff\xc0\x00\x11\x08" + (90).to_bytes(2, "big") + (120).to_bytes(2, "big") + b"\x00" * 12
    assert picture_size(jpeg) == (120, 90)
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
    assert picture_size(webp) == (640, 480)
    assert picture_size(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None  # a drawing has no size in pixels
