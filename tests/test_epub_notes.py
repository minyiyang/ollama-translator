"""An EPUB's own footnotes and endnotes, and its pictures' descriptions: translated by the title stage.

A note is the book's words but no passage: it is kept apart from the
chapters, translated with what the book says about itself, and put back at
the compile where it stands, its links and emphasis in place.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

from book_agent.config import AppConfig
from book_agent.ollama_client import GenerationMetrics, GenerationResult
from book_agent.stages.compile import load_compiled_epub_path, run_epub_compile_stage
from book_agent.stages.decompile import load_decompile_manifest
from book_agent.stages.title import note_entries, run_title_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from tests.test_book_pictures_and_notes import KeepsWhatInlineElementsHold, _map
from tests.test_compile_stages import prepare_workspace

CONFIG = {"audit": {"semantic_enabled": False}}  # English into Chinese

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">sign-of-the-four</dc:identifier>
    <dc:title>The Sign of the Four</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="endnotes" href="endnotes.xhtml" media-type="application/xhtml+xml"/>
    <item id="plan" href="plan.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="chapter"/><itemref idref="endnotes"/></spine>
</package>"""

NAV = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>
<body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">The Science of Deduction</a></li>
<li><a href="endnotes.xhtml">Endnotes</a></li></ol></nav></body></html>"""

# As Standard Ebooks and many publishers set them: a reference in the text,
# a footnote beside it with a link back, and endnotes in a document of their own.
CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Chapter I</title></head>
<body><h1>The Science of Deduction</h1>
<p>Sherlock Holmes took his bottle from the corner of the mantelpiece.<a href="endnotes.xhtml#note-1" id="noteref-1" epub:type="noteref">1</a></p>
<p>Mrs. Hudson brought up the tray.<a href="#fn-2" id="ref-2" epub:type="noteref">2</a></p>
<p><img src="plan.png" alt="A plan of the sitting-room"/></p>
<aside id="fn-2" epub:type="footnote"><p><a href="#ref-2">2</a> Mrs. Hudson kept the house in <i>Baker Street</i>.</p></aside>
</body></html>"""

ENDNOTES = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Endnotes</title></head>
<body><section epub:type="endnotes"><h2>Endnotes</h2><ol>
<li id="note-1" epub:type="endnote"><p>A seven-per-cent solution of cocaine. <a href="chapter.xhtml#noteref-1" epub:type="backlink">Back</a></p></li>
</ol></section></body></html>"""


def _epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        for name, content in (("content.opf", OPF), ("nav.xhtml", NAV), ("chapter.xhtml", CHAPTER), ("endnotes.xhtml", ENDNOTES)):
            archive.writestr(f"OEBPS/{name}", content)
        archive.writestr("OEBPS/plan.png", _map())
    return path


class Annotator:
    """Stands in for the model at the title stage: translates the notes into
    Chinese, keeping their markers, and the picture's description.
    `drops_markers` answers one note without them, as a careless model might."""

    NOTES = {
        "<I000>2</I000> Mrs. Hudson kept the house in <I001>Baker Street</I001>.": "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。",
        "A seven-per-cent solution of cocaine. <I000>Back</I000>": "百分之七的可卡因溶液。<I000>返回</I000>",
        "Endnotes": "尾注",
    }
    DESCRIPTIONS = {"A plan of the sitting-room": "起居室平面图"}

    def __init__(self, drops_markers: bool = False):
        self.drops_markers = drops_markers
        self.prompts = []

    def generate_text(self, prompt, **_):
        self.prompts.append(prompt)
        if "Notes:\n" in prompt:
            asked, known = prompt.split("Notes:\n", 1)[1].splitlines(), self.NOTES
        elif "Descriptions:\n" in prompt:
            asked, known = prompt.split("Descriptions:\n", 1)[1].splitlines(), self.DESCRIPTIONS
        else:
            return GenerationResult(content="四签名", thinking="", metrics=GenerationMetrics(prompt_eval_count=20, eval_count=5))
        lines = []
        for line in asked:
            number, text = line.split(". ", 1)
            answer = known.get(text, "")
            if self.drops_markers and "Baker Street" in text:
                answer = re.sub(r"</?I\d{3}>", "", answer)
            lines.append(f"{number}. {answer}")
        return GenerationResult(content="\n".join(lines), thinking="", metrics=GenerationMetrics(prompt_eval_count=40, eval_count=15))


def _job(base: Path, config: AppConfig):
    return prepare_workspace(base, config, source=_epub(base / "sign.epub"), translator=KeepsWhatInlineElementsHold())


def test_an_epubs_footnotes_and_endnotes_are_no_passages_but_notes_of_their_own():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _job(Path(directory), config)
        chapter, endnotes = load_decompile_manifest(workspace).documents
        # The chapter's passages: its heading and its two paragraphs; the footnote is not among them.
        assert [segment.text for segment in chapter.segments] == [
            "The Science of Deduction",
            "Sherlock Holmes took his bottle from the corner of the mantelpiece.1",
            "Mrs. Hudson brought up the tray.2",
        ]
        assert [note.segment_id for note in chapter.notes] == ["D0000-N000001"]
        # The endnotes document has no passage at all: its heading and its note are the notes'.
        assert endnotes.segments == [] and [note.text for note in endnotes.notes] == ["Endnotes", "A seven-per-cent solution of cocaine. Back"]
        # What the title stage asks of the model, in the book's order, links and emphasis as markers.
        assert note_entries(workspace) == [
            "<I000>2</I000> Mrs. Hudson kept the house in <I001>Baker Street</I001>.",
            "Endnotes",
            "A seven-per-cent solution of cocaine. <I000>Back</I000>",
        ]


def test_the_title_stage_translates_the_notes_and_the_compile_puts_them_back_with_their_links_and_emphasis():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _job(Path(directory), config)
        annotator = Annotator()
        record = run_title_stage(workspace, config, annotator)
        assert record["notes"] == {
            "D0000-N000001": "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。",
            "D0001-N000001": "尾注",
            "D0001-N000002": "百分之七的可卡因溶液。<I000>返回</I000>",
        }
        assert record["descriptions"] == {"A plan of the sitting-room": "起居室平面图"}
        # The markers are explained to the model.
        notes_prompt = next(prompt for prompt in annotator.prompts if "Notes:\n" in prompt)
        assert "keep every one of them, in the same order" in notes_prompt
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        with zipfile.ZipFile(load_compiled_epub_path(workspace)) as archive:
            chapter = archive.read("OEBPS/chapter.xhtml").decode("utf-8")
            endnotes = archive.read("OEBPS/endnotes.xhtml").decode("utf-8")
        assert '<aside id="fn-2" epub:type="footnote"><p><a href="#ref-2">2</a> 哈德森太太在<i>贝克街</i>管家。</p></aside>' in chapter
        assert re.search(r'<img src="plan.png" alt="起居室平面图" ?/>', chapter)
        assert "<h2>尾注</h2>" in endnotes
        assert '百分之七的可卡因溶液。<a href="chapter.xhtml#noteref-1" epub:type="backlink">返回</a>' in endnotes


def test_a_note_answered_without_its_markers_stays_as_the_book_has_it():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _job(Path(directory), config)
        record = run_title_stage(workspace, config, Annotator(drops_markers=True))
        # Without its link and emphasis the footnote could not be put back: it is left as it was.
        assert "D0000-N000001" not in record["notes"] and "D0001-N000002" in record["notes"]
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        with zipfile.ZipFile(load_compiled_epub_path(workspace)) as archive:
            chapter = archive.read("OEBPS/chapter.xhtml").decode("utf-8")
        assert "Mrs. Hudson kept the house in <i>Baker Street</i>." in chapter


# -- a person reads and corrects them on the Text tab ---------------------------------------------


def _translated_job(base: Path, config: AppConfig, annotator: Annotator | None = None):
    workspace = _job(base, config)
    run_title_stage(workspace, config, annotator or Annotator())
    return workspace


def test_the_text_tab_shows_each_note_with_the_passages_that_refer_to_it_and_each_picture_where_it_stands():
    from book_agent.web.text_view import text_chapter, text_outline

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated_job(Path(directory), config)
        outline = text_outline(workspace)
        # The book's title and the contents entries come first, then the chapters.
        assert [chapter["document_id"] for chapter in outline["chapters"]] == ["BOOK", "chapter", "endnotes"]
        assert outline["totals"]["notes"] == 3 and outline["totals"]["segments"] == 3
        book = text_chapter(workspace, "BOOK")
        assert book["segments"][0]["source"] == "The Sign of the Four" and book["segments"][0]["text"] == "四签名"

        chapter = text_chapter(workspace, "chapter")
        # The passage about the tray refers to the footnote beside it; the one about the bottle to the endnote.
        refers = {row["segment_id"]: row["notes"] for row in chapter["segments"]}
        assert refers == {"D0000-S000001": [], "D0000-S000002": ["D0001-N000002"], "D0000-S000003": ["D0000-N000001"]}
        (footnote,) = chapter["notes"]
        assert footnote["referred_from"] == ["D0000-S000003"]
        assert footnote["text"] == "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。" and not footnote["untranslated"]
        (picture,) = chapter["pictures"]
        assert (picture["picture_path"], picture["after"], picture["source"], picture["text"]) == (
            "OEBPS/plan.png", "D0000-S000003", "A plan of the sitting-room", "起居室平面图"
        )
        # The endnote, in its own document, names the passage that refers to it.
        endnotes = text_chapter(workspace, "endnotes")
        assert [(note["source"], note["referred_from"]) for note in endnotes["notes"]] == [
            ("Endnotes", []),
            ("A seven-per-cent solution of cocaine. <I000>Back</I000>", ["D0000-S000002"]),
        ]


def test_a_corrected_note_is_in_the_compiled_book_and_a_correction_that_loses_its_link_is_refused():
    from book_agent.book_edits import apply_book_edit, check_book_edit
    from book_agent.hashing import sha256_text
    from book_agent.text_edits import BlockingCheckError, active_edit_texts
    from book_agent.web.text_view import text_chapter, text_outline

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated_job(Path(directory), config)
        run_epub_compile_stage(workspace, config)
        settled = "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。"
        # Without the emphasis on the street, the note could not be put back as the book has it.
        assert check_book_edit(workspace, "D0000-N000001", "<I000>2</I000> 哈德森太太在贝克街管家。")["hard"]
        try:
            apply_book_edit(
                workspace, item_id="D0000-N000001", text="<I000>2</I000> 哈德森太太在贝克街管家。",
                reason="smoother", base_target_sha256=sha256_text(settled),
            )
        except BlockingCheckError as error:
            assert "markers" in error.findings[0]["message"]
        else:
            raise AssertionError("a note without its emphasis was saved")
        apply_book_edit(
            workspace, item_id="D0000-N000001", text="<I000>2</I000> 哈德森太太是<I001>贝克街</I001>那所房子的房东。",
            reason="She owned the house", base_target_sha256=sha256_text(settled),
        )
        # The edit is the note's, not a passage's: the passage edits are as they were.
        assert active_edit_texts(workspace) == {}
        (footnote,) = text_chapter(workspace, "chapter")["notes"]
        assert footnote["state"] == "edited" and footnote["pipeline_text"] == settled
        assert text_outline(workspace)["uncompiled_edit_count"] == 1
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        with zipfile.ZipFile(load_compiled_epub_path(workspace)) as archive:
            chapter = archive.read("OEBPS/chapter.xhtml").decode("utf-8")
        assert '<p><a href="#ref-2">2</a> 哈德森太太是<i>贝克街</i>那所房子的房东。</p>' in chapter
        assert text_outline(workspace)["uncompiled_edit_count"] == 0


def test_what_the_title_stage_left_in_the_source_language_is_listed_until_someone_translates_it():
    from book_agent.book_edits import apply_book_edit, untranslated_items
    from book_agent.hashing import sha256_text

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated_job(Path(directory), config, Annotator(drops_markers=True))
        left = {item["item_id"]: item for item in untranslated_items(workspace)}
        # The footnote answered without its markers, and the contents entries the stand-in did not answer.
        assert left["D0000-N000001"]["chapter"] == "The Science of Deduction"
        assert {item["kind"] for item in left.values()} == {"note", "contents"}
        source = "<I000>2</I000> Mrs. Hudson kept the house in <I001>Baker Street</I001>."
        apply_book_edit(
            workspace, item_id="D0000-N000001", text="<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。",
            reason="translated by hand", base_target_sha256=sha256_text(source),
        )
        assert "D0000-N000001" not in {item["item_id"] for item in untranslated_items(workspace)}
        # It is no reason to stop the book: the compile goes ahead with what is there.
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed


def test_an_edited_description_meets_a_new_translation_as_a_conflict_and_the_person_chooses():
    from book_agent.book_edits import apply_book_action, apply_book_edit, book_statuses, description_id
    from book_agent.hashing import sha256_text
    from book_agent.text_edits import EditAction, SegmentEditState

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated_job(Path(directory), config)
        item_id = description_id("A plan of the sitting-room")
        apply_book_edit(
            workspace, item_id=item_id, text="起居室的平面图", reason="clearer", base_target_sha256=sha256_text("起居室平面图"),
        )
        # The title stage is run again (the config now names the title) and words the description otherwise.
        rerun = Annotator()
        rerun.DESCRIPTIONS = {"A plan of the sitting-room": "客厅平面图"}
        changed = AppConfig.model_validate({**CONFIG, "translation": {"translated_title": "四签名"}})
        run_title_stage(workspace, changed, rerun)
        assert book_statuses(workspace)[item_id].state is SegmentEditState.CONFLICT
        apply_book_action(workspace, action=EditAction.KEEP, item_id=item_id, reason="mine reads better")
        assert book_statuses(workspace)[item_id].state is SegmentEditState.EDITED
        run_epub_compile_stage(workspace, changed)
        assert run_epub_validation_stage(workspace).passed
        with zipfile.ZipFile(load_compiled_epub_path(workspace)) as archive:
            assert 'alt="起居室的平面图"' in archive.read("OEBPS/chapter.xhtml").decode("utf-8")


def test_only_a_picture_the_book_lists_is_served():
    from book_agent.web.text_view import text_picture

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated_job(Path(directory), config)
        assert text_picture(workspace, "OEBPS/plan.png") == (_map(), "image/png")
        for path in ("OEBPS/content.opf", "OEBPS/chapter.xhtml", "../state.sqlite3"):
            try:
                text_picture(workspace, path)
            except ValueError:
                continue
            raise AssertionError(f"served {path}")
