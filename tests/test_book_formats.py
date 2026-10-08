"""Books that come as text, Markdown, HTML, or a Word document, and books written out as those."""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

import pytest

from book_agent.book_formats import (
    EXPORT_FORMATS,
    Block,
    Book,
    BookFormatError,
    export_book,
    read_book,
    read_epub,
    split_chapters,
    write_source_package,
)
from book_agent.config import AppConfig
from book_agent.epub import inspect_epub_package
from book_agent.stages.compile import load_compiled_epub_path, run_epub_compile_stage
from book_agent.stages.decompile import load_decompile_manifest, run_decompile_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.workspace import create_job_workspace

MARKDOWN = """---
title: The Sign of the Four
author: Arthur Conan Doyle
---

# The Sign of the Four

## Chapter I. The Science of Deduction

Sherlock Holmes took his bottle from the corner of the mantelpiece
and his hypodermic syringe from its *neat* morocco case.

"Which is it to-day," I asked, "**morphine** or cocaine?"

> It is cocaine, a seven-per-cent solution.
> Would you care to try it?

- his bottle
- his syringe

1. the wrist
2. the forearm

* * *

## Chapter II. The Statement of the Case

Miss Morstan entered the room with a firm step. See [the agony column](https://example.org/times) and `7%`.
"""

HTML = """<!DOCTYPE html>
<html lang="en"><head><title>The Sign of the Four</title><meta name="author" content="Arthur Conan Doyle">
<style>p { color: red }</style><script>alert(1)</script></head>
<body><nav><a href="#c1">Contents</a></nav>
<div class="chapter"><h2>Chapter I. The Science of Deduction</h2>
<p>Sherlock Holmes took his bottle from the corner of the <i>mantelpiece</i>.</p>
<blockquote><p>It is cocaine, a seven-per-cent solution.</p></blockquote>
<ul><li>his bottle</li><li><p>his syringe</p></li></ul>
Loose words between the blocks.
<p><a href="javascript:alert(1)">An unsafe link</a> and <a href="https://example.org/times">a safe one</a>.<br>A second line.</p>
</div>
<div class="chapter"><h2>Chapter II. The Statement of the Case</h2><p>Miss Morstan entered the room.</p>
<table><tr><td>Agra</td><td>1857</td></tr></table></div>
</body></html>"""

# As Word writes it: a German copy's style ids, one phrase split over runs, a numbered list.
WORD_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
<w:p><w:pPr><w:pStyle w:val="Titel"/></w:pPr><w:r><w:t>Das Zeichen der Vier</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="berschrift1"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Kapitel I. Die Wissenschaft der Deduktion</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Sherlock Holmes nahm seine </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>Fla</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>sche</w:t></w:r><w:r><w:t xml:space="preserve"> vom Kaminsims.</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>Nicht fett.</w:t></w:r><w:r><w:br/><w:t>Zweite Zeile.</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>das Handgelenk</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>seine Flasche</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Siehe </w:t></w:r><w:hyperlink r:id="rId7"><w:r><w:t>die Zeitung</w:t></w:r></w:hyperlink><w:del><w:r><w:delText> gestern</w:delText></w:r></w:del><w:ins><w:r><w:t xml:space="preserve"> von heute</w:t></w:r></w:ins><w:r><w:t>.</w:t></w:r></w:p>
<w:p></w:p>
<w:p><w:pPr><w:pStyle w:val="berschrift1"/></w:pPr><w:r><w:t>Kapitel II. Die Darlegung des Falles</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Agra</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:sectPr/></w:body></w:document>"""
WORD_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Titel"><w:name w:val="Title"/></w:style>
<w:style w:type="paragraph" w:styleId="berschrift1"><w:name w:val="heading 1"/></w:style>
</w:styles>"""
WORD_CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Arthur Conan Doyle</dc:creator></cp:coreProperties>"""


WORD_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="5"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="5"/></w:num>
</w:numbering>"""
WORD_RELATIONSHIPS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
 Target="https://example.org/times" TargetMode="External"/>
</Relationships>"""


def _word_file(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", WORD_DOCUMENT)
        archive.writestr("word/numbering.xml", WORD_NUMBERING)
        archive.writestr("word/_rels/document.xml.rels", WORD_RELATIONSHIPS)
        archive.writestr("word/styles.xml", WORD_STYLES)
        archive.writestr("docProps/core.xml", WORD_CORE)
    return path


def _kinds(book: Book) -> list[str]:
    return [block.kind for block in book.blocks]


def test_a_markdown_book_keeps_its_headings_emphasis_lists_and_quotations():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sign.md"
        path.write_text(MARKDOWN, encoding="utf-8")
        book = read_book(path)
    assert (book.title, book.author) == ("The Sign of the Four", "Arthur Conan Doyle")
    assert _kinds(book) == ["h1", "h2", "p", "p", "quote", "li", "li", "oli", "oli", "hr", "h2", "p"]
    # A paragraph wrapped over two lines is one paragraph.
    assert book.blocks[2].html == (
        "Sherlock Holmes took his bottle from the corner of the mantelpiece "
        "and his hypodermic syringe from its <em>neat</em> morocco case."
    )
    assert "<strong>morphine</strong>" in book.blocks[3].html
    assert book.blocks[4].html == "It is cocaine, a seven-per-cent solution. Would you care to try it?"
    assert book.blocks[-1].html.endswith('See <a href="https://example.org/times">the agony column</a> and <code>7%</code>.')
    # Two chapters under the one title: the book is divided at the level used more than once.
    assert [chapter[0].html for chapter in split_chapters(book.blocks)][1:] == [
        "Chapter I. The Science of Deduction",
        "Chapter II. The Statement of the Case",
    ]


def test_a_chapter_heading_set_on_two_lines_is_one_heading():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "alice.txt"
        # A chapter's number, its name on the line under it, then one line a paragraph, as a
        # book saved from the web is; a short line of dialogue among them.
        path.write_text(
            "CHAPTER VII.\n A Mad Tea-Party\n"
            "There was a table set out under a tree in front of the house.\n"
            "“No room! No room!”\n"
            "CHAPTER VIII.\nThe Queen’s Croquet-Ground\nThe Rose-Tree\n"
            "A large rose-tree stood near the entrance of the garden.\n",
            encoding="utf-8",
        )
        book = read_book(path)
    assert [(block.kind, block.html) for block in book.blocks] == [
        ("h1", "CHAPTER VII. A Mad Tea-Party"),
        ("p", "There was a table set out under a tree in front of the house."),
        ("p", "“No room! No room!”"),  # a short line that ends a sentence is a line of its own
        ("h1", "CHAPTER VIII. The Queen’s Croquet-Ground"),
        ("p", "The Rose-Tree"),  # a heading takes one line under it, not two
        ("p", "A large rose-tree stood near the entrance of the garden."),
    ]
    assert book.title == "CHAPTER VII. A Mad Tea-Party"


def test_a_text_file_is_read_as_wrapped_paragraphs_or_one_paragraph_a_line():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        wrapped = base / "wrapped.txt"
        wrapped.write_text(
            "CHAPTER I\n\nAlice was beginning to get very tired of sitting by her\nsister on the bank, and of having "
            "nothing to do.\n\nSo she was considering in her own mind\nwhether the pleasure of making a daisy-chain "
            "would be worth the trouble.\n\nCHAPTER II\n\nCuriouser and curiouser!\n",
            encoding="utf-8",
        )
        book = read_book(wrapped)
        assert _kinds(book) == ["h1", "p", "p", "h1", "p"]
        assert book.blocks[1].html.startswith("Alice was beginning to get very tired of sitting by her sister on the bank")
        # A Chinese novel saved from the web: a paragraph a line, no blank lines, in the encoding of its day.
        chinese = base / "ah-q.txt"
        chinese.write_bytes("第一章 序\n我要给阿Q做正传，已经不止一两年了。\n但一面要做，一面又往回想。\n第二章 优胜记略\n阿Q不独是姓名籍贯有些渺茫。\n".encode("gb18030"))
        book = read_book(chinese)
        assert _kinds(book) == ["h1", "p", "p", "h1", "p"]
        assert book.blocks[1].html == "我要给阿Q做正传，已经不止一两年了。" and book.title == "第一章 序"
        # Chinese wrapped at a fixed width is joined without a space.
        narrow = base / "narrow.txt"
        narrow.write_text("我要给阿Q做正传，\n已经不止一两年了。\n\n但一面要做，\n一面又往回想。\n", encoding="utf-8-sig")
        assert [block.html for block in read_book(narrow).blocks] == ["我要给阿Q做正传，已经不止一两年了。", "但一面要做，一面又往回想。"]
        empty = base / "empty.txt"
        empty.write_text("\n \n", encoding="utf-8")
        with pytest.raises(BookFormatError, match="contains no text"):
            read_book(empty)
        with pytest.raises(BookFormatError, match="cannot read"):
            read_book(base / "book.mobi")


def test_an_html_book_gives_its_text_without_scripts_styles_or_unsafe_links():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sign.html"
        path.write_text(HTML, encoding="utf-8")
        book = read_book(path)
    assert (book.title, book.author, book.language) == ("The Sign of the Four", "Arthur Conan Doyle", "en")
    assert _kinds(book) == ["h2", "p", "quote", "li", "li", "p", "p", "h2", "p", "p", "p"]
    text = " ".join(block.html for block in book.blocks)
    assert "alert" not in text and "color" not in text and "Contents" not in text and "javascript" not in text
    assert "<em>mantelpiece</em>" in text and "Loose words between the blocks." in text
    assert 'An unsafe link and <a href="https://example.org/times">a safe one</a>.<br/>A second line.' in text
    assert [block.html for block in book.blocks[-2:]] == ["Agra", "1857"]  # a table's cells, as paragraphs


def test_a_word_document_gives_its_headings_and_emphasis_whatever_its_style_ids():
    with tempfile.TemporaryDirectory() as directory:
        book = read_book(_word_file(Path(directory) / "zeichen.docx"))
        with pytest.raises(BookFormatError, match="not a Word document"):
            bad = Path(directory) / "bad.docx"
            bad.write_text("not a zip", encoding="utf-8")
            read_book(bad)
    assert (book.title, book.author) == ("Das Zeichen der Vier", "Arthur Conan Doyle")
    assert _kinds(book) == ["h1", "h1", "p", "p", "oli", "li", "p", "h1", "p"]  # a numbered list and a bullet list
    # The link is kept; a word struck out under tracked changes is gone, and the one put in its place is there.
    assert book.blocks[6].html == 'Siehe <a href="https://example.org/times">die Zeitung</a> von heute.'
    assert book.blocks[1].html == "Kapitel I. Die Wissenschaft der Deduktion"  # a heading's bold is its style
    assert book.blocks[2].html == "Sherlock Holmes nahm seine <em>Flasche</em> vom Kaminsims."  # one phrase, two runs
    assert book.blocks[3].html == "Nicht fett.<br/>Zweite Zeile."
    assert book.blocks[-1].html == "Agra"


def test_a_converted_book_is_an_epub_the_pipeline_can_read_and_the_same_every_time():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        path = base / "sign.md"
        path.write_text(MARKDOWN, encoding="utf-8")
        book = read_book(path)
        write_source_package(book, base / "one", "abc")
        write_source_package(book, base / "two", "abc")
        files = sorted(p.relative_to(base / "one").as_posix() for p in (base / "one").rglob("*") if p.is_file())
        assert files == [
            "META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/styles.css",
            "OEBPS/text/chapter-0001.xhtml", "OEBPS/text/chapter-0002.xhtml", "OEBPS/text/chapter-0003.xhtml", "mimetype",
        ]
        assert all((base / "one" / name).read_bytes() == (base / "two" / name).read_bytes() for name in files)
        manifest = inspect_epub_package(base / "one", "abc")
        assert manifest.metadata.values["title"] == ["The Sign of the Four"]
        assert [document.archive_path for document in manifest.documents] == [
            f"OEBPS/text/chapter-000{number}.xhtml" for number in (1, 2, 3)
        ]
        segments = [segment.text for document in manifest.documents for segment in document.segments]
        assert "Chapter I. The Science of Deduction" in segments and "his syringe" in segments
        assert any("neat" in segment for segment in segments)


@pytest.mark.parametrize("suffix", [".md", ".txt", ".html", ".docx"])
def test_a_book_in_each_format_goes_through_the_pipeline_and_comes_back_in_its_own_format(suffix):
    from tests.test_compile_stages import CompileStageTests

    config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / f"sign{suffix}"
        if suffix == ".docx":
            _word_file(source)
        elif suffix == ".html":
            source.write_text(
                "<html><body><h2>Chapter I</h2><p>Holmes took his <i>bottle</i>.</p>"
                "<h2>Chapter II</h2><p>Miss Morstan entered the room.</p></body></html>",
                encoding="utf-8",
            )
        elif suffix == ".md":
            source.write_text("## Chapter I\n\nHolmes took his *bottle*.\n\n## Chapter II\n\nMiss Morstan entered the room.\n", encoding="utf-8")
        else:
            source.write_text("CHAPTER I\n\nHolmes took his bottle.\n\nCHAPTER II\n\nMiss Morstan entered the room.\n", encoding="utf-8")
        workspace = CompileStageTests().prepare_workspace(base, config, source=source)
        assert len(load_decompile_manifest(workspace).documents) >= 2
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        compiled = Path(load_compiled_epub_path(workspace))
        assert compiled.suffix == ".epub"
        # The stand-in translator answers every passage with the same two words.
        translated = [re.sub(r"<[^>]+>", "", block.html) for block in read_epub(compiled).blocks]
        assert len(translated) >= 4 and set(translated) == {"译文。"}, translated
        own = compiled.with_suffix({".md": ".md", ".txt": ".txt", ".html": ".html", ".docx": ".docx"}[suffix])
        assert own.is_file(), sorted(path.name for path in compiled.parent.iterdir())
        # And in any other format, on request: `book-agent export`.
        from book_agent.cli import main

        asked = base / "asked" / "sign.html"
        assert main(["export", str(workspace.root), "--format", "html", "--out", str(asked)]) == 0
        assert "译文。" in asked.read_text(encoding="utf-8")
        if suffix == ".docx":
            assert "译文。" in " ".join(block.html for block in read_book(own).blocks)
        else:
            assert "译文。" in own.read_text(encoding="utf-8")


def test_a_text_file_job_is_named_as_a_source_the_pipeline_takes():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / "alice.txt"
        source.write_text("CHAPTER I\n\nAlice was beginning to get very tired.\n", encoding="utf-8")
        workspace = create_job_workspace(source, base / "runs", AppConfig(), job_id="alice")
        run_decompile_stage(workspace)
        manifest = load_decompile_manifest(workspace)
        assert [segment.text for segment in manifest.documents[0].segments] == ["CHAPTER I", "Alice was beginning to get very tired."]
        kindle = base / "alice.mobi"
        kindle.write_bytes(b"BOOKMOBI")
        other = create_job_workspace(kindle, base / "runs", AppConfig(), job_id="alice-mobi")
        with pytest.raises(ValueError, match="a book .EPUB, RTF, text, Markdown, HTML, Word"):
            run_decompile_stage(other)


def _sample_epub(base: Path) -> Path:
    """An EPUB as the pipeline compiles one, with every kind of block."""
    book = Book(
        title="Das Zeichen der Vier",
        author="Arthur Conan Doyle",
        language="de",
        blocks=[
            Block("h1", "Kapitel I. Die Wissenschaft der Deduktion"),
            Block("p", "Sherlock Holmes nahm seine <em>Flasche</em> vom <strong>Kaminsims</strong>.<br/>Zweite Zeile."),
            Block("quote", "Es ist Kokain, eine siebenprozentige Lösung."),
            Block("li", "seine Flasche"),
            Block("oli", "das Handgelenk"),
            Block("oli", "der Unterarm"),
            Block("hr"),
            Block("p", '1. Mai * siehe <a href="https://example.org/times">die Zeitung</a> &amp; <code>7%</code>'),
            Block("h1", "Kapitel II. Die Darlegung des Falles"),
            Block("p", "Miss Morstan betrat das Zimmer."),
        ],
    )
    write_source_package(book, base / "package", "abc")
    epub = base / "zeichen.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        for path in sorted((base / "package").rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(base / "package").as_posix())
    return epub


def test_a_compiled_book_is_written_as_text_markdown_html_and_word():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        epub = _sample_epub(base)
        book = read_epub(epub)
        assert (book.title, book.author, book.language) == ("Das Zeichen der Vier", "Arthur Conan Doyle", "de")
        assert _kinds(book) == ["h1", "p", "quote", "li", "oli", "oli", "hr", "p", "h1", "p"]

        text = export_book(epub, base / "out" / "zeichen.txt", "txt").read_text(encoding="utf-8")
        assert text.startswith("Kapitel I. Die Wissenschaft der Deduktion\n\nSherlock Holmes nahm seine Flasche vom Kaminsims.\nZweite Zeile.\n\n")
        assert "- seine Flasche\n\n1. das Handgelenk\n\n2. der Unterarm\n\n* * *" in text and "<" not in text

        markdown = export_book(epub, base / "zeichen.md", "md").read_text(encoding="utf-8")
        assert markdown.startswith("# Kapitel I. Die Wissenschaft der Deduktion\n\nSherlock Holmes nahm seine *Flasche* vom **Kaminsims**.")
        assert "> Es ist Kokain, eine siebenprozentige Lösung." in markdown
        # What Markdown would take for a list or for emphasis is written so that it is not.
        assert "\\1. Mai \\* siehe [die Zeitung](https://example.org/times) & `7%`" in markdown
        # Read again, it is the same book.
        again = read_book(base / "zeichen.md")
        assert _kinds(again) == _kinds(book)
        assert [block.html for block in again.blocks][:3] == [block.html for block in book.blocks][:3]

        html = export_book(epub, base / "zeichen.html", "html").read_text(encoding="utf-8")
        assert '<html lang="de">' in html and "<title>Das Zeichen der Vier</title>" in html
        assert "<blockquote><p>Es ist Kokain, eine siebenprozentige Lösung.</p></blockquote>" in html
        assert _kinds(read_book(base / "zeichen.html")) == _kinds(book)

        word = export_book(epub, base / "zeichen.docx", "docx")
        with zipfile.ZipFile(word) as archive:
            assert archive.testzip() is None
            assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= set(archive.namelist())
        back = read_book(word)
        assert (back.title, back.author, back.language) == ("Das Zeichen der Vier", "Arthur Conan Doyle", "de")
        # Headings, the quotation, both kinds of list, the rule, and the link all come back as they were.
        assert _kinds(back) == _kinds(book)
        assert back.blocks[7].html == book.blocks[7].html
        assert "<em>Flasche</em> vom <strong>Kaminsims</strong>.<br/>Zweite Zeile." in back.blocks[1].html
        assert word.read_bytes() == export_book(epub, base / "again.docx", "docx").read_bytes()  # the same file every time

        assert set(EXPORT_FORMATS) == {"txt", "md", "html", "docx"}
        with pytest.raises(BookFormatError, match="cannot write a book as pdf"):
            export_book(epub, base / "zeichen.pdf", "pdf")


def test_a_document_written_in_word_itself_is_read_for_what_a_translator_needs():
    # Saved by Word: a title, two headings, italics, a footnote, a numbered list, a table,
    # a comment, a sentence typed with tracked changes on, and a text box.
    book = read_book(Path(__file__).parent / "data" / "word-authored.docx")
    assert book.title == "The Sign of the Four"
    assert [(block.kind, block.html) for block in book.blocks] == [
        ("h1", "The Sign of the Four"),
        ("h1", "Chapter I. The Science of Deduction"),
        (
            "p",
            "Sherlock Holmes took his bottle from the corner of the <em>mantelpiece</em> and his hypodermic syringe "
            'from its neat morocco case.<sup><a href="#note-1">1</a></sup>',
        ),
        ("note", "A seven-per-cent solution of cocaine."),  # the footnote, after the paragraph that refers to it
        ("p", "Three times a day for many months I had witnessed this performance."),
        ("oli", "the wrist"),
        ("oli", "the forearm"),
        ("p", "Agra"), ("p", "1857"), ("p", "Andaman Islands"), ("p", "1878"),  # the table, cell by cell
        ("h1", "Chapter II. The Statement of the Case"),
        ("p", "Miss Morstan entered the room with a firm step."),
        ("p", "She was a blonde young lady, small and dainty."),  # the tracked insertion
        ("p", "A caption in a text box."),  # once, though Word stores a text box twice
    ]
    assert book.blocks[3].src == "note-1"
    text = " ".join(block.html for block in book.blocks)
    assert "first edition" not in text  # the comment is not read


def test_a_converted_book_is_tagged_with_the_jobs_source_language_unless_the_file_names_its_own():
    config = AppConfig.model_validate({"translation": {"direction": "de>en"}})
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        text = base / "verwandlung.txt"
        text.write_text("Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte.\n", encoding="utf-8")
        page = base / "sign.html"
        page.write_text("<html lang='en-GB'><body><p>Holmes took his bottle.</p></body></html>", encoding="utf-8")
        for source, expected in ((text, "de"), (page, "en-GB")):
            workspace = create_job_workspace(source, base / "runs", config, job_id=source.stem)
            run_decompile_stage(workspace)
            assert load_decompile_manifest(workspace).metadata.values["language"] == [expected]
            chapter = next(workspace.root.rglob("chapter-0001.xhtml")).read_text(encoding="utf-8")
            assert f'xml:lang="{expected}"' in chapter and "und" not in chapter


# "The Sign of the Four", its opening, saved as a PDF by Word: a title, two chapters, a word in italics.
PDF = Path(__file__).parent / "data" / "sign-of-the-four.pdf"


def test_a_pdf_that_holds_text_is_read_as_headings_and_paragraphs():
    book = read_book(PDF)
    assert (book.title, book.author) == ("The Sign of the Four", "Arthur Conan Doyle")
    assert _kinds(book) == ["h1", "h2", "p", "p", "p", "h2", "p", "p"]
    assert [block.html for block in book.blocks if block.kind != "p"] == [
        "The Sign of the Four", "Chapter I. The Science of Deduction", "Chapter II. The Statement of the Case",
    ]
    # A paragraph set over several lines of the page is one paragraph again, each line joined to the next.
    first = book.blocks[2].html
    assert first.startswith("Sherlock Holmes took his bottle from the corner of the mantelpiece, and his hypodermic syringe")
    assert first.endswith("all dotted and scarred with innumerable puncture-marks.") and "  " not in first
    assert book.blocks[4].html == "“Which is it to-day,” I asked, “<em>morphine</em> or cocaine?”"
    assert [chapter[0].html for chapter in split_chapters(book.blocks)][1:] == [
        "Chapter I. The Science of Deduction", "Chapter II. The Statement of the Case",
    ]


def test_a_pdf_becomes_a_job_with_a_chapter_for_each_of_its_chapters():
    from book_agent.book_formats import CONVERTED_SUFFIXES

    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / "sign.pdf"
        source.write_bytes(PDF.read_bytes())
        workspace = create_job_workspace(source, base / "runs", AppConfig(), job_id="sign")
        run_decompile_stage(workspace)
        manifest = load_decompile_manifest(workspace)
        assert [document.title for document in manifest.documents][1:] == [
            "Chapter I. The Science of Deduction", "Chapter II. The Statement of the Case",
        ]
        segments = [segment.text for document in manifest.documents for segment in document.segments]
        assert len(segments) == 8 and segments[-1] == "“State your case,” said he, in brisk, business tones."
    # A PDF is read, never written: its translation is an EPUB, which `book-agent export` writes in other formats.
    assert CONVERTED_SUFFIXES[".pdf"] not in EXPORT_FORMATS


def test_what_is_not_a_readable_pdf_is_refused_with_the_reason():
    with tempfile.TemporaryDirectory() as directory:
        broken = Path(directory) / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7 not really")
        with pytest.raises(BookFormatError, match="broken.pdf"):
            read_book(broken)


def test_the_compiled_book_says_it_is_in_the_language_it_was_translated_into():
    from zipfile import ZipFile

    from tests.epub_fixture import OPF, make_epub
    from tests.test_compile_stages import CompileStageTests

    config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})  # English into Chinese
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        # A book named for its first chapter, so that its title is a passage that gets translated.
        source = make_epub(base / "book.epub", opf=OPF.replace(b"Fixture Book", b"Chapter One"))
        workspace = CompileStageTests().prepare_workspace(base, config, source=source)
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        with ZipFile(load_compiled_epub_path(workspace)) as archive:
            package = archive.read("OEBPS/content.opf").decode("utf-8")
            contents = archive.read("OEBPS/nav.xhtml").decode("utf-8")
            chapter = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
        # The package: its language, and its title where the title is a passage of the book.
        assert "<dc:language>zh-Hans</dc:language>" in package and "<dc:language>en</dc:language>" not in package
        assert "<dc:title>译文。</dc:title>" in package and "<dc:creator>Test Author</dc:creator>" in package
        # The contents: an entry that is a chapter's heading reads as the heading does now; one that is not stays.
        assert ">译文。</a>" in contents and ">Part</a>" in contents and "Chapter One" not in contents
        # The chapter itself is marked as Chinese, for the reader's fonts and voice.
        assert 'lang="zh-Hans"' in chapter.split(">", 2)[1] + chapter.split(">", 3)[2]
        # And what is written from the EPUB says the same.
        assert read_epub(load_compiled_epub_path(workspace)).language == "zh-Hans"
        word = export_book(load_compiled_epub_path(workspace), base / "book.docx", "docx")
        assert (read_book(word).language, read_book(word).title) == ("zh-Hans", "译文。")


def test_a_phrase_marked_as_another_language_keeps_its_mark():
    from book_agent.epub_compile import localize_package
    from book_agent.epub import EpubMetadata, EpubPackageManifest, ChapterDocument

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "c.xhtml").write_text(
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en-GB" lang="en-GB"><head><title>One</title></head>'
            '<body><p>He said <span lang="fr" xml:lang="fr">au revoir</span>.</p></body></html>', encoding="utf-8",
        )
        (root / "plain.xhtml").write_text('<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>', encoding="utf-8")
        documents = [
            ChapterDocument(order=index, manifest_id=name, archive_path=f"{name}.xhtml", media_type="application/xhtml+xml", linear=True, source_sha256="x")
            for index, name in enumerate(("c", "plain"))
        ]
        manifest = EpubPackageManifest(
            source_sha256="x", opf_path="", package_version="3.0", unique_identifier="x", metadata=EpubMetadata(values={}),
            manifest_items=[], spine=[], navigation=[], documents=documents, resources=[],
        )
        localize_package(root, manifest, [], "en", "de")
        chapter = (root / "c.xhtml").read_text(encoding="utf-8")
        assert chapter.startswith('<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="de" lang="de">')
        assert '<span lang="fr" xml:lang="fr">au revoir</span>' in chapter  # the French stays French
        # A document that named no language is given the target's.
        assert (root / "plain.xhtml").read_text(encoding="utf-8").startswith('<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="de" lang="de">')
