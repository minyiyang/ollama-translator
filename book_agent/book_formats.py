"""Books that are not EPUB: plain text, Markdown, HTML, and Word documents.

Reading: each format is read into one simple shape, a list of blocks (a
heading, a paragraph, a list item, ...) whose text keeps emphasis, links, and
line breaks. `write_source_package` writes those blocks as an EPUB package,
and from there the pipeline treats the book as it does any EPUB.

Writing: `export_book` reads a compiled EPUB back into blocks and writes them
as text, Markdown, HTML, or a Word document.

What is kept is the text and its structure: headings, paragraphs, lists,
quotations, emphasis, links. Page layout, fonts, images, tables as tables,
footnote anchors, and tracked changes are not.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from html import escape
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from bs4 import BeautifulSoup, NavigableString, Tag

from .languages import SCRIPTS

# What `book-agent run` and the dashboard accept besides EPUB and RTF.
CONVERTED_SUFFIXES = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".docx": "docx",
}
SOURCE_SUFFIXES = {".epub", ".rtf", *CONVERTED_SUFFIXES}
# What a compiled book can be written as, besides the EPUB it is.
EXPORT_FORMATS = ("txt", "md", "html", "docx")
EXPORT_MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_MAX_SOURCE_BYTES = 64 * 1024 * 1024


class BookFormatError(ValueError):
    """The file is not a book this module can read or write."""


@dataclass(frozen=True)
class Block:
    """One block of a book. `kind` is h1 to h6, p, li (a bullet), oli (a
    numbered item), quote, pre, or hr; `html` is its inline XHTML: escaped text
    with <em>, <strong>, <code>, <a href>, <sub>, <sup>, and <br/>."""

    kind: str
    html: str = ""


@dataclass
class Book:
    title: str = ""
    author: str = ""
    language: str = ""
    blocks: list[Block] = field(default_factory=list)


# -- reading -----------------------------------------------------------------------------------


def read_book(path: str | Path) -> Book:
    """Read a text, Markdown, HTML, or Word file."""
    source = Path(path)
    kind = CONVERTED_SUFFIXES.get(source.suffix.casefold())
    if kind is None:
        raise BookFormatError(f"cannot read a {source.suffix or 'file without a suffix'} as a book")
    if source.stat().st_size > _MAX_SOURCE_BYTES:
        raise BookFormatError(f"{source.name} exceeds the {_MAX_SOURCE_BYTES} byte limit")
    data = source.read_bytes()
    if kind == "docx":
        book = _read_docx(source)
    elif kind == "html":
        book = _read_html(data)
    elif kind == "md":
        book = _read_markdown(_decode(data))
    else:
        book = _read_text(_decode(data))
    book.blocks = [block for block in book.blocks if block.kind == "hr" or _plain(block.html).strip()]
    if not any(block.kind != "hr" for block in book.blocks):
        raise BookFormatError(f"{source.name} contains no text")
    if not book.title:
        first = next((block for block in book.blocks if block.kind == "h1"), None)
        book.title = _plain(first.html).strip() if first else source.stem
    return book


def _decode(data: bytes) -> str:
    """Text as its bytes say: a byte-order mark, UTF-8, then the encodings
    Chinese and Western European text files are most often saved in."""
    for mark, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(mark):
            return data.decode(encoding, errors="replace")
    for encoding in ("utf-8", "gb18030", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


_CHAPTER_HEADING = re.compile(
    r"^(?:(?:chapter|part|book|volume|prologue|epilogue|kapitel|chapitre|capítulo|capitolo|глава|часть)\b.{0,70}"
    r"|第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[章回节卷部篇集話话].{0,40}"
    r"|제\s*[0-9]+\s*[장편부권].{0,40}"
    r"|[IVXLC]{1,7}\.?)$",
    flags=re.IGNORECASE,
)
_WRAP_WIDTH = 120
# Scripts written without spaces between words, and the full-width forms and
# punctuation that go with them: two lines of a wrapped paragraph meet
# without a space where one of these ends a line and another begins the next.
_UNSPACED = re.compile(
    "["
    + "".join(SCRIPTS[name] for name in ("Han", "Hiragana", "Katakana", "Thai", "Lao", "Khmer", "Myanmar", "Tibetan"))
    + "\u3000-\u303f\uff00-\uffef"
    + "]"
)


def _join_wrapped(lines: list[str]) -> str:
    """The lines of one paragraph that was wrapped at a fixed width: joined
    with a space, or with nothing between two characters of a script written
    without spaces."""
    text = lines[0]
    for line in lines[1:]:
        text += ("" if _UNSPACED.match(text[-1:]) and _UNSPACED.match(line[:1]) else " ") + line
    return text


def _read_text(text: str) -> Book:
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    groups: list[list[str]] = [[]]
    for line in lines:
        if line:
            groups[-1].append(line)
        elif groups[-1]:
            groups.append([])
    groups = [group for group in groups if group]
    # A file wrapped at a fixed width has paragraphs of several short lines
    # set apart by blank lines. Otherwise every line is a paragraph of its
    # own, as novels saved from the web usually are.
    wrapped = (
        len(groups) > 1
        and sum(len(group) > 1 for group in groups) * 4 >= len(groups)
        and max(len(line) for group in groups for line in group) <= _WRAP_WIDTH
    )
    paragraphs = [_join_wrapped(group) for group in groups] if wrapped else [line for group in groups for line in group]
    blocks = [
        Block("h1" if len(paragraph) <= 80 and _CHAPTER_HEADING.match(paragraph) else "p", escape(paragraph, quote=False))
        for paragraph in paragraphs
    ]
    return Book(blocks=blocks)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_MD_ITEM = re.compile(r"^(?:([-*+])|(\d{1,9})[.)])\s+(.*)$")
_MD_RULE = re.compile(r"^(?:\*\s*){3,}$|^(?:-\s*){3,}$|^(?:_\s*){3,}$")
_MD_FENCE = re.compile(r"^(`{3,}|~{3,})")
_MD_INLINE = re.compile(
    r"(?P<code>`+)(?P<code_text>.+?)(?P=code)"
    r"|!\[(?P<alt>[^\]]*)\]\([^)]*\)"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|(?P<strong>\*\*|__)(?=\S)(?P<strong_text>.+?)(?<=\S)(?P=strong)"
    r"|(?<![\w*])(?P<em>[*_])(?=\S)(?P<em_text>.+?)(?<=\S)(?P=em)(?![\w*])"
    r"|\\(?P<escaped>[\\`*_{}\[\]()#+\-.!>~|])"
)


def _markdown_inline(text: str) -> str:
    """Markdown inside a block as inline XHTML."""
    parts: list[str] = []
    position = 0
    for match in _MD_INLINE.finditer(text):
        parts.append(escape(text[position : match.start()], quote=False))
        position = match.end()
        if match["code"]:
            parts.append(f"<code>{escape(match['code_text'].strip(), quote=False)}</code>")
        elif match["alt"] is not None:
            parts.append(escape(match["alt"], quote=False))  # the picture is not carried over; its description is
        elif match["link_text"]:
            parts.append(f'<a href="{escape(match["href"])}">{_markdown_inline(match["link_text"])}</a>')
        elif match["strong"]:
            parts.append(f"<strong>{_markdown_inline(match['strong_text'])}</strong>")
        elif match["em"]:
            parts.append(f"<em>{_markdown_inline(match['em_text'])}</em>")
        else:
            parts.append(escape(match["escaped"], quote=False))
    parts.append(escape(text[position:], quote=False))
    return "".join(parts)


def _read_markdown(text: str) -> Book:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    book = Book()
    # Front matter: title and author between two lines of three dashes.
    if lines and lines[0].strip() == "---" and "---" in [line.strip() for line in lines[1:40]]:
        end = [line.strip() for line in lines[1:40]].index("---") + 1
        for line in lines[1:end]:
            key, _, value = line.partition(":")
            if key.strip().casefold() in {"title", "author", "lang", "language"} and value.strip():
                name = {"lang": "language"}.get(key.strip().casefold(), key.strip().casefold())
                setattr(book, name, value.strip().strip("\"'"))
        lines = lines[end + 1 :]
    blocks: list[Block] = []
    paragraph: list[str] = []
    kind = "p"

    def close() -> None:
        nonlocal kind
        if paragraph:
            # Two spaces or a backslash at the end of a line is a line break.
            pieces = [_markdown_inline(line.rstrip("\\").rstrip()) for line in paragraph]
            breaks = [line.endswith(("  ", "\\")) for line in paragraph]
            html = pieces[0]
            for piece, broken in zip(pieces[1:], breaks):
                html += ("<br/>" if broken else " ") + piece
            blocks.append(Block(kind, html))
            paragraph.clear()
        kind = "p"

    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1
        fence = _MD_FENCE.match(line)
        if fence:
            close()
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith(fence[1]):
                code.append(lines[index])
                index += 1
            index += 1
            blocks.append(Block("pre", escape("\n".join(code), quote=False)))
        elif not line:
            close()
        elif _MD_RULE.match(line) and not paragraph:
            blocks.append(Block("hr"))
        elif paragraph and kind == "p" and re.fullmatch(r"=+|-+", line):
            # A line of = or - under a paragraph makes it a heading.
            kind = "h1" if line.startswith("=") else "h2"
            close()
        elif _MD_RULE.match(line):
            close()
            blocks.append(Block("hr"))
        elif heading := _MD_HEADING.match(line):
            close()
            blocks.append(Block(f"h{len(heading[1])}", _markdown_inline(heading[2])))
        elif item := _MD_ITEM.match(line):
            close()
            kind = "li" if item[1] else "oli"
            paragraph.append(item[3] + raw[len(raw.rstrip()) :])
        elif line.startswith(">"):
            if kind != "quote":
                close()
                kind = "quote"
            paragraph.append(line.lstrip(">").strip() + raw[len(raw.rstrip()) :])
        else:
            if kind == "quote" and not paragraph:
                kind = "p"
            paragraph.append(raw.strip(" \t") if not raw.endswith("  ") else raw.lstrip())
    close()
    book.blocks = blocks
    return book


_HTML_SKIP = {"script", "style", "noscript", "template", "nav", "head", "svg", "math", "iframe", "object", "form", "button"}
_HTML_BLOCKS = {
    "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4", "h5": "h5", "h6": "h6",
    "p": "p", "pre": "pre", "dt": "p", "dd": "p", "figcaption": "p", "caption": "p", "address": "p",
    "td": "p", "th": "p",
}
_HTML_CONTAINERS = {
    "body", "html", "main", "article", "section", "div", "aside", "header", "footer", "figure", "ul", "ol", "dl",
    "table", "thead", "tbody", "tfoot", "tr", "blockquote", "li", "center", "details", "summary", "hgroup",
}
_HTML_INLINE = {"em": "em", "i": "em", "strong": "strong", "b": "strong", "code": "code", "tt": "code", "sub": "sub", "sup": "sup"}
_SAFE_LINK = re.compile(r"^(?:https?:|mailto:|#)", flags=re.IGNORECASE)


def _inline_html(node, keep_space: bool = False) -> str:
    """The inline content of an element as the inline XHTML a Block holds."""
    if isinstance(node, NavigableString):
        if type(node) is not NavigableString:  # a comment, a doctype, a CDATA section
            return ""
        text = str(node)
        return escape(text if keep_space else re.sub(r"\s+", " ", text), quote=False)
    if not isinstance(node, Tag) or node.name in _HTML_SKIP:
        return ""
    if node.name == "br":
        return "<br/>"
    if node.name == "img":
        return escape(str(node.get("alt") or ""), quote=False)
    inner = "".join(_inline_html(child, keep_space) for child in node.children)
    if node.name in _HTML_INLINE and inner.strip():
        name = _HTML_INLINE[node.name]
        return f"<{name}>{inner}</{name}>"
    if node.name == "a" and inner.strip() and _SAFE_LINK.match(str(node.get("href") or "")):
        return f'<a href="{escape(str(node.get("href")))}">{inner}</a>'
    return inner


def _blocks_from_html(node: Tag, blocks: list[Block], inside: str = "") -> None:
    """Walk an element: its block children become Blocks, and text standing
    loose between them becomes a paragraph. `inside` is li, oli, or quote
    while within a list or a quotation."""
    loose: list[str] = []

    def flush() -> None:
        html = "".join(loose).strip()
        loose.clear()
        if _plain(html).strip():
            blocks.append(Block(inside or "p", html))

    for child in node.children:
        if isinstance(child, Tag) and child.name in _HTML_SKIP:
            continue
        name = child.name if isinstance(child, Tag) else ""
        if name == "hr":
            flush()
            blocks.append(Block("hr"))
        elif name in _HTML_BLOCKS:
            flush()
            kind = _HTML_BLOCKS[name]
            html = _inline_html(child, keep_space=name == "pre").strip("\n" if name == "pre" else None)
            # A paragraph inside a list item or a quotation is that item or quotation.
            blocks.append(Block(inside if kind == "p" and inside else kind, html if name == "pre" else html.strip()))
        elif name in _HTML_CONTAINERS:
            flush()
            within = inside
            if name == "li":
                within = "oli" if child.parent is not None and child.parent.name == "ol" else "li"
            elif name == "blockquote":
                within = "quote"
            _blocks_from_html(child, blocks, within)
        else:
            loose.append(_inline_html(child))
    flush()


def _read_html(data: bytes) -> Book:
    soup = BeautifulSoup(data, "html.parser")
    book = Book()
    if soup.title and soup.title.get_text(strip=True):
        book.title = soup.title.get_text(" ", strip=True)
    author = soup.find("meta", attrs={"name": re.compile("^author$", re.IGNORECASE)})
    if isinstance(author, Tag) and author.get("content"):
        book.author = str(author["content"]).strip()
    root = soup.find("html")
    if isinstance(root, Tag) and root.get("lang"):
        book.language = str(root["lang"]).strip()
    _blocks_from_html(soup.body or soup, book.blocks)
    return book


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_DOCX_HEADING = re.compile(r"^(?:heading|überschrift|titre|título|titolo|标题|標題|見出し|제목|заголовок)\s*([1-6])$", re.IGNORECASE)
_DOCX_RULE = re.compile(r"(?:\*\s*){3,}|[-_\u2013\u2014]{3,}")
_MAX_DOCX_PART = 96 * 1024 * 1024
# Fonts a writer sets on a word to mark it as code.
_MONOSPACED = {"consolas", "courier", "courier new", "menlo", "monaco", "lucida console", "cascadia code", "cascadia mono", "source code pro"}


def _docx_part(archive: zipfile.ZipFile, name: str):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_DOCX_PART:
        raise BookFormatError(f"{name} in the document exceeds the {_MAX_DOCX_PART} byte limit")
    # ElementTree does not fetch external entities; a Word part has none to expand.
    return ElementTree.fromstring(archive.read(info))


def _on(properties, name: str) -> bool:
    """Whether a run property (bold, italic) is set and not switched off."""
    element = properties.find(f"{_W}{name}") if properties is not None else None
    return element is not None and element.get(f"{_W}val", "true") not in {"0", "false", "off", "none"}


def _docx_run(run) -> str:
    """One run of a paragraph as inline XHTML."""
    properties = run.find(f"{_W}rPr")
    text = ""
    for child in run:
        if child.tag == f"{_W}t":
            text += escape(child.text or "", quote=False)
        elif child.tag == f"{_W}tab":
            text += " "
        elif child.tag in {f"{_W}br", f"{_W}cr"}:
            text += "" if child.get(f"{_W}type") == "page" else "<br/>"
        elif child.tag == f"{_W}noBreakHyphen":
            text += chr(0x2011)
    if not text.replace("<br/>", "").strip():
        return text
    fonts = properties.find(f"{_W}rFonts") if properties is not None else None
    if fonts is not None and fonts.get(f"{_W}ascii", "").casefold() in _MONOSPACED:
        text = f"<code>{text}</code>"
    if _on(properties, "i"):
        text = f"<em>{text}</em>"
    if _on(properties, "b"):
        text = f"<strong>{text}</strong>"
    align = properties.find(f"{_W}vertAlign") if properties is not None else None
    if align is not None and align.get(f"{_W}val") in {"superscript", "subscript"}:
        tag = "sup" if align.get(f"{_W}val") == "superscript" else "sub"
        text = f"<{tag}>{text}</{tag}>"
    return text


def _docx_runs(paragraph, links: dict[str, str]) -> str:
    """A paragraph's text as inline XHTML. `links` are the document's
    hyperlink targets by relationship id."""
    parts: list[str] = []
    for child in paragraph:
        if child.tag == f"{_W}r":
            parts.append(_docx_run(child))
        elif child.tag == f"{_W}hyperlink":
            inner = "".join(_docx_run(run) for run in child.iter(f"{_W}r"))
            href = links.get(child.get(f"{_R}id", ""), "")
            parts.append(f'<a href="{escape(href)}">{inner}</a>' if inner.strip() and _SAFE_LINK.match(href) else inner)
        elif child.tag not in {f"{_W}pPr", f"{_W}del", f"{_W}moveFrom"}:
            # An insertion, a smart tag, a content control: its runs are the paragraph's.
            parts.extend(_docx_run(run) for run in child.iter(f"{_W}r"))
    html = "".join(parts)
    # Word splits a phrase into runs; put back together what has the same emphasis.
    for tag in ("em", "strong"):
        html = re.sub(rf"</{tag}>((?:\s|<br/>)*)<{tag}>", r"\1", html)
    return html


def _docx_list_kinds(archive: zipfile.ZipFile) -> dict[str, str]:
    """Each list of the document (by its numbering id) as li, a bullet list, or oli, a numbered one."""
    numbering = _docx_part(archive, "word/numbering.xml")
    if numbering is None:
        return {}
    formats: dict[str, str] = {}
    for abstract in numbering.iter(f"{_W}abstractNum"):
        level = abstract.find(f"{_W}lvl")
        number_format = level.find(f"{_W}numFmt") if level is not None else None
        formats[abstract.get(f"{_W}abstractNumId", "")] = number_format.get(f"{_W}val", "") if number_format is not None else ""
    kinds: dict[str, str] = {}
    for number in numbering.iter(f"{_W}num"):
        abstract = number.find(f"{_W}abstractNumId")
        number_format = formats.get(abstract.get(f"{_W}val", ""), "") if abstract is not None else ""
        kinds[number.get(f"{_W}numId", "")] = "li" if number_format in {"bullet", "none", ""} else "oli"
    return kinds


def _read_docx(path: Path) -> Book:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise BookFormatError(f"{path.name} is not a Word document (.docx)") from error
    with archive:
        document = _docx_part(archive, "word/document.xml")
        if document is None:
            raise BookFormatError(f"{path.name} is not a Word document (.docx)")
        # Style ids are whatever the author's Word made them; their names say what they are.
        names: dict[str, str] = {}
        styles = _docx_part(archive, "word/styles.xml")
        for style in styles.iter(f"{_W}style") if styles is not None else []:
            name = style.find(f"{_W}name")
            if style.get(f"{_W}styleId") and name is not None:
                names[style.get(f"{_W}styleId", "")] = name.get(f"{_W}val", "")
        lists = _docx_list_kinds(archive)
        links: dict[str, str] = {}
        relationships = _docx_part(archive, "word/_rels/document.xml.rels")
        for relationship in relationships.iter(f"{_REL}Relationship") if relationships is not None else []:
            if relationship.get("Type", "").endswith("/hyperlink"):
                links[relationship.get("Id", "")] = relationship.get("Target", "")
        book = Book()
        core = _docx_part(archive, "docProps/core.xml")
        if core is not None:
            book.title = (core.findtext(f"{_DC}title") or "").strip()
            book.author = (core.findtext(f"{_DC}creator") or "").strip()
            book.language = (core.findtext(f"{_DC}language") or "").strip()
    body = document.find(f"{_W}body")
    for paragraph in body.iter(f"{_W}p") if body is not None else []:
        spaced = _docx_runs(paragraph, links)
        html = spaced.strip()
        if not html:
            continue
        properties = paragraph.find(f"{_W}pPr")
        style_id, list_id, outline = "", "", None
        if properties is not None:
            style = properties.find(f"{_W}pStyle")
            style_id = style.get(f"{_W}val", "") if style is not None else ""
            number = properties.find(f"{_W}numPr/{_W}numId")
            list_id = number.get(f"{_W}val", "") if number is not None else ""
            outline = properties.find(f"{_W}outlineLvl")
        name = names.get(style_id, style_id)
        folded = name.casefold()
        heading = _DOCX_HEADING.match(name) or re.fullmatch(r"(?:Heading|berschrift|Titre)\s?([1-6])", style_id)
        if heading:
            kind = f"h{heading[1]}"
        elif folded in {"title", "titel", "titre", "título", "titolo"}:
            kind = "h1"
            book.title = book.title or _plain(html)
        elif outline is not None and outline.get(f"{_W}val", "").isdigit() and int(outline.get(f"{_W}val")) < 6:
            kind = f"h{int(outline.get(f'{_W}val')) + 1}"
        elif "preformatted" in folded or folded in {"code", "source code", "plain text"}:
            kind = "pre"
            html = spaced.replace("<br/>", "\n").strip("\n")  # its indentation is part of it
        elif "quote" in folded or "zitat" in folded or "citation" in folded:
            kind = "quote"
        elif list_id and list_id != "0":
            kind = lists.get(list_id, "li")
        elif folded.startswith("list"):
            kind = "oli" if "number" in folded else "li"
        elif _DOCX_RULE.fullmatch(_plain(html).strip()):
            kind, html = "hr", ""
        else:
            kind = "p"
        if kind.startswith("h") and kind != "hr":
            html = re.sub(r"</?strong>", "", html)  # a heading's bold is its style, not emphasis
        book.blocks.append(Block(kind, html))
    return book


# -- the EPUB package a converted book becomes ---------------------------------------------------

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
_STYLES = """body { line-height: 1.5; }
h1, h2, h3 { text-align: center; }
blockquote { margin: 1em 2em; }
pre { white-space: pre-wrap; }
"""


def _plain(html: str) -> str:
    """The text of inline XHTML."""
    return BeautifulSoup(html.replace("<br/>", " "), "html.parser").get_text() if "<" in html or "&" in html else html


def split_chapters(blocks: list[Block]) -> list[list[Block]]:
    """Blocks grouped into chapters: a new one at each heading of the level
    the book is divided by, which is the highest level used more than once,
    or else the highest there is. A book without headings is one chapter."""
    levels = [int(block.kind[1]) for block in blocks if re.fullmatch(r"h[1-6]", block.kind)]
    if not levels:
        return [blocks]
    repeated = [level for level in sorted(set(levels)) if levels.count(level) > 1]
    level = repeated[0] if repeated else min(levels)
    chapters: list[list[Block]] = [[]]
    for block in blocks:
        if block.kind == f"h{level}" and chapters[-1]:
            chapters.append([])
        chapters[-1].append(block)
    return [chapter for chapter in chapters if chapter]


def _chapter_body(blocks: list[Block]) -> str:
    lines: list[str] = []
    open_list = ""
    for block in blocks:
        wanted = {"li": "ul", "oli": "ol"}.get(block.kind, "")
        if open_list != wanted:
            if open_list:
                lines.append(f"</{open_list}>")
            if wanted:
                lines.append(f"<{wanted}>")
            open_list = wanted
        if block.kind == "hr":
            lines.append("<hr/>")
        elif wanted:
            lines.append(f"<li>{block.html}</li>")
        elif block.kind == "quote":
            lines.append(f"<blockquote><p>{block.html}</p></blockquote>")
        else:
            lines.append(f"<{block.kind}>{block.html}</{block.kind}>")
    if open_list:
        lines.append(f"</{open_list}>")
    return "\n".join(lines)


def write_source_package(book: Book, root: str | Path, identifier: str) -> None:
    """Write `book` under `root` as the files of an EPUB 3 (not zipped: the
    pipeline works on the unpacked package). The same book gives the same
    bytes, so stage hashes repeat."""
    package = Path(root)
    chapters = split_chapters(book.blocks)
    language = book.language or "und"
    title = escape(book.title or "Untitled", quote=False)
    files: dict[str, str] = {"mimetype": "application/epub+zip", "META-INF/container.xml": _CONTAINER, "OEBPS/styles.css": _STYLES}
    items, spine, toc = [], [], []
    for number, chapter in enumerate(chapters, start=1):
        name = f"chapter-{number:04d}"
        heading = next((block for block in chapter if re.fullmatch(r"h[1-6]", block.kind)), None)
        label = escape(_plain(heading.html).strip(), quote=False) if heading else f"{title} ({number})" if len(chapters) > 1 else title
        files[f"OEBPS/text/{name}.xhtml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}" lang="{escape(language)}">\n'
            f'<head><title>{label}</title><link rel="stylesheet" type="text/css" href="../styles.css"/></head>\n'
            f"<body>\n{_chapter_body(chapter)}\n</body>\n</html>\n"
        )
        items.append(f'    <item id="{name}" href="text/{name}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'    <itemref idref="{name}"/>')
        toc.append(f'      <li><a href="text/{name}.xhtml">{label}</a></li>')
    files["OEBPS/nav.xhtml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"<head><title>{title}</title></head>\n<body>\n"
        '  <nav epub:type="toc">\n    <ol>\n' + "\n".join(toc) + "\n    </ol>\n  </nav>\n</body>\n</html>\n"
    )
    creator = f"    <dc:creator>{escape(book.author, quote=False)}</dc:creator>\n" if book.author else ""
    files["OEBPS/content.opf"] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="book-id">urn:sha256:{escape(identifier)}</dc:identifier>\n'
        f"    <dc:title>{title}</dc:title>\n{creator}"
        f"    <dc:language>{escape(language, quote=False)}</dc:language>\n"
        '    <meta property="dcterms:modified">2000-01-01T00:00:00Z</meta>\n'
        "  </metadata>\n  <manifest>\n"
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        '    <item id="css" href="styles.css" media-type="text/css"/>\n'
        + "\n".join(items)
        + "\n  </manifest>\n  <spine>\n"
        + "\n".join(spine)
        + "\n  </spine>\n</package>\n"
    )
    for name, content in files.items():
        path = package.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))


# -- writing a compiled book in another format ----------------------------------------------------

_OPF = "{http://www.idpf.org/2007/opf}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"


def read_epub(path: str | Path) -> Book:
    """The text of an EPUB, in reading order, as blocks."""
    book = Book()
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise BookFormatError(f"{Path(path).name} is not an EPUB") from error
    with archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise BookFormatError("the EPUB names no package document")
        opf_path = PurePosixPath(rootfile.get("full-path", ""))
        package = ElementTree.fromstring(archive.read(str(opf_path)))
        book.title = (package.findtext(f".//{_DC}title") or "").strip()
        book.author = (package.findtext(f".//{_DC}creator") or "").strip()
        book.language = (package.findtext(f".//{_DC}language") or "").strip()
        hrefs = {
            item.get("id"): item.get("href", "")
            for item in package.iter(f"{_OPF}item")
            if "nav" not in (item.get("properties") or "").split()
        }
        names = set(archive.namelist())
        for reference in package.iter(f"{_OPF}itemref"):
            href = hrefs.get(reference.get("idref"))
            if not href:
                continue
            parts: list[str] = []
            for part in (opf_path.parent / href.split("#", 1)[0]).parts:
                if part == "..":
                    if parts:
                        parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            name = "/".join(parts)
            if name in names:
                soup = BeautifulSoup(archive.read(name), "html.parser")
                _blocks_from_html(soup.body or soup, book.blocks)
    book.blocks = [block for block in book.blocks if block.kind == "hr" or _plain(block.html).strip()]
    return book


@dataclass(frozen=True)
class _Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str = ""
    script: str = ""  # "sup" or "sub"
    line_break: bool = False


def _runs(html: str) -> list[_Run]:
    """Inline XHTML as runs of text with their emphasis."""
    runs: list[_Run] = []

    def walk(node, style: dict) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                if str(child):
                    runs.append(_Run(str(child), **style))
            elif isinstance(child, Tag):
                if child.name == "br":
                    runs.append(_Run("", line_break=True))
                    continue
                inner = dict(style)
                if child.name == "em":
                    inner["italic"] = True
                elif child.name == "strong":
                    inner["bold"] = True
                elif child.name == "code":
                    inner["code"] = True
                elif child.name in {"sup", "sub"}:
                    inner["script"] = child.name
                elif child.name == "a":
                    inner["href"] = str(child.get("href") or "")
                walk(child, inner)

    walk(BeautifulSoup(f"<span>{html}</span>", "html.parser").span, {})
    return runs


def _as_text(book: Book) -> str:
    lines: list[str] = []
    number = 0
    for block in book.blocks:
        number = number + 1 if block.kind == "oli" else 0
        text = "".join("\n" if run.line_break else run.text for run in _runs(block.html)).strip()
        if block.kind == "hr":
            text = "* * *"
        elif block.kind == "li":
            text = f"- {text}"
        elif block.kind == "oli":
            text = f"{number}. {text}"
        lines.append(text)
    return "\n\n".join(lines) + "\n"


_MD_SPECIAL = re.compile(r"([\\`*_\[\]])")


def _md_escape(text: str) -> str:
    """Text with a backslash before what Markdown would read as markup."""
    return _MD_SPECIAL.sub(lambda match: chr(92) + match.group(1), text)


def _as_markdown(book: Book) -> str:
    lines: list[str] = []
    number = 0
    for block in book.blocks:
        number = number + 1 if block.kind == "oli" else 0
        if block.kind == "hr":
            lines.append("---")
            continue
        if block.kind == "pre":
            lines.append("```\n" + "".join(run.text for run in _runs(block.html)) + "\n```")
            continue
        one_line = block.kind not in {"p", "quote"}
        text = ""
        for run in _runs(block.html):
            if run.line_break:
                text += " " if one_line else "\\\n"
                continue
            piece = f"`{run.text}`" if run.code else _md_escape(run.text)
            lead = piece[: len(piece) - len(piece.lstrip())]
            trail = piece[len(piece.rstrip()) :]
            core = piece.strip()
            if core and run.italic:
                core = f"*{core}*"
            if core and run.bold:
                core = f"**{core}**"
            if core and run.href:
                core = f"[{core}]({run.href})"
            text += lead + core + trail
        text = text.strip()
        # A line that would read as a heading, a list, or a quotation and is not one.
        if block.kind in {"p", "quote"} and re.match(r"(?:#{1,6}\s|[-+>]\s|\d+[.)]\s)", text):
            text = "\\" + text
        if block.kind.startswith("h"):
            text = "#" * int(block.kind[1]) + " " + text
        elif block.kind == "li":
            text = f"- {text}"
        elif block.kind == "oli":
            text = f"{number}. {text}"
        elif block.kind == "quote":
            text = "> " + text.replace("\n", "\n> ")
        lines.append(text)
    front = ""
    if book.title and not any(block.kind == "h1" for block in book.blocks):
        front = f"# {_md_escape(book.title)}\n\n"
    return front + "\n\n".join(lines) + "\n"


def _as_html(book: Book) -> str:
    language = escape(book.language or "und")
    author = f'<meta name="author" content="{escape(book.author)}"/>\n' if book.author else ""
    return (
        f'<!DOCTYPE html>\n<html lang="{language}">\n<head>\n<meta charset="utf-8"/>\n'
        f"<title>{escape(book.title or 'Untitled', quote=False)}</title>\n{author}"
        f"<style>\nbody {{ max-width: 40em; margin: 2em auto; padding: 0 1em; }}\n{_STYLES}</style>\n</head>\n"
        f"<body>\n{_chapter_body(book.blocks)}\n</body>\n</html>\n"
    )


_DOCX_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>
"""
_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>
"""
_OFFICE_RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
# The two kinds of list a book has, as Word's list definitions 1 and 2.
_BULLET_LIST, _NUMBERED_LIST = 1, 2


def _docx_styles() -> str:
    styles = [
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="160" w:line="300" w:lineRule="auto"/></w:pPr></w:style>'
    ]
    for level, size in enumerate((36, 30, 26, 24, 22, 22), start=1):
        styles.append(
            f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/>'
            f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="160"/>'
            f'<w:outlineLvl w:val="{level - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
        )
    styles.append(
        '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:ind w:left="720" w:right="720"/></w:pPr><w:rPr><w:i/></w:rPr></w:style>'
    )
    styles.append(
        '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>'
    )
    styles.append(
        '<w:style w:type="paragraph" w:styleId="HTMLPreformatted"><w:name w:val="HTML Preformatted"/><w:basedOn w:val="Normal"/>'
        '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="20"/></w:rPr></w:style>'
    )
    styles.append(
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{_W_NS}">' + "".join(styles) + "</w:styles>\n"
    )


def _docx_numbering(lists: list[int]) -> str:
    """The numbering part: the two list definitions, and one list for each
    run of items in the book (`lists` holds its definition), so that each
    numbered list starts again at one."""
    bullet = chr(0x2022)
    definitions = (
        f'<w:abstractNum w:abstractNumId="{_BULLET_LIST}"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
        f'<w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="{bullet}"/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
        f'<w:abstractNum w:abstractNumId="{_NUMBERED_LIST}"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0">'
        '<w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    )
    numbers = "".join(
        f'<w:num w:numId="{number}"><w:abstractNumId w:val="{definition}"/>'
        '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
        for number, definition in enumerate(lists, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:numbering xmlns:w="{_W_NS}">{definitions}{numbers}</w:numbering>\n'
    )


def _xml_text(text: str) -> str:
    """Text for an XML element: escaped, without the control characters XML forbids."""
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text), quote=False)


def _docx_run_xml(run: _Run, link: bool = False) -> str:
    if run.line_break:
        return "<w:r><w:br/></w:r>"
    marks = ('<w:rStyle w:val="Hyperlink"/>' if link else "") + ("<w:b/>" if run.bold else "") + ("<w:i/>" if run.italic else "")
    if run.code:
        marks += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
    if run.script:
        marks += f'<w:vertAlign w:val="{"superscript" if run.script == "sup" else "subscript"}"/>'
    body = ""
    for index, piece in enumerate(run.text.split("\n")):
        if index:
            body += "<w:r><w:br/></w:r>"
        if piece:
            body += (
                f"<w:r>{f'<w:rPr>{marks}</w:rPr>' if marks else ''}"
                f'<w:t xml:space="preserve">{_xml_text(piece)}</w:t></w:r>'
            )
    return body


def _docx_paragraph(block: Block, list_id: int, links: dict[str, str]) -> str:
    """One block as a Word paragraph. `list_id` is the list a list item
    belongs to; `links` collects the targets of its hyperlinks by relationship id."""
    if block.kind == "hr":
        return '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>* * *</w:t></w:r></w:p>'
    style = {"quote": "Quote", "li": "ListParagraph", "oli": "ListParagraph", "pre": "HTMLPreformatted"}.get(block.kind, "")
    if re.fullmatch(r"h[1-6]", block.kind):
        style = f"Heading{block.kind[1]}"
    properties = f'<w:pStyle w:val="{style}"/>' if style else ""
    if block.kind in {"li", "oli"}:
        properties += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{list_id}"/></w:numPr>'
    body = ""
    runs = _runs(block.html)
    index = 0
    while index < len(runs):
        run = runs[index]
        if run.href and _SAFE_LINK.match(run.href) and not run.href.startswith("#"):
            # Runs of one link, whatever their emphasis, are one hyperlink.
            end = index
            while end < len(runs) and runs[end].href == run.href:
                end += 1
            relationship = next((key for key, target in links.items() if target == run.href), "")
            if not relationship:
                relationship = f"rId{len(links) + 100}"
                links[relationship] = run.href
            inner = "".join(_docx_run_xml(item, link=True) for item in runs[index:end])
            body += f'<w:hyperlink r:id="{relationship}">{inner}</w:hyperlink>'
            index = end
        else:
            body += _docx_run_xml(run)
            index += 1
    return f"<w:p>{f'<w:pPr>{properties}</w:pPr>' if properties else ''}{body}</w:p>"


def _write_docx(book: Book, output: Path) -> None:
    paragraphs: list[str] = []
    lists: list[int] = []
    links: dict[str, str] = {}
    previous = ""
    for block in book.blocks:
        if block.kind in {"li", "oli"} and block.kind != previous:
            lists.append(_BULLET_LIST if block.kind == "li" else _NUMBERED_LIST)
        previous = block.kind
        paragraphs.append(_docx_paragraph(block, len(lists), links))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}" xmlns:r="{_OFFICE_RELATIONSHIPS}"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>\n"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f'<Relationship Id="rId1" Type="{_OFFICE_RELATIONSHIPS}/styles" Target="styles.xml"/>\n'
        f'<Relationship Id="rId2" Type="{_OFFICE_RELATIONSHIPS}/numbering" Target="numbering.xml"/>\n'
        + "".join(
            f'<Relationship Id="{key}" Type="{_OFFICE_RELATIONSHIPS}/hyperlink" Target="{escape(target)}" TargetMode="External"/>\n'
            for key, target in links.items()
        )
        + "</Relationships>\n"
    )
    language = f"<dc:language>{_xml_text(book.language)}</dc:language>" if book.language else ""
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{_xml_text(book.title)}</dc:title><dc:creator>{_xml_text(book.author)}</dc:creator>{language}"
        "</cp:coreProperties>\n"
    )
    parts = {
        "[Content_Types].xml": _DOCX_TYPES,
        "_rels/.rels": _DOCX_RELS,
        "word/document.xml": document,
        "word/_rels/document.xml.rels": relationships,
        "word/styles.xml": _docx_styles(),
        "word/numbering.xml": _docx_numbering(lists),
        "docProps/core.xml": core,
    }
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in parts.items():
            # A fixed date and platform: the same book gives the same file.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))


def export_book(epub: str | Path, output: str | Path, export_format: str) -> Path:
    """Write the text of a compiled EPUB as `export_format` (txt, md, html, docx)."""
    if export_format not in EXPORT_FORMATS:
        raise BookFormatError(f"cannot write a book as {export_format}; choose one of {', '.join(EXPORT_FORMATS)}")
    book = read_epub(epub)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if export_format == "docx":
        _write_docx(book, target)
    else:
        text = {"txt": _as_text, "md": _as_markdown, "html": _as_html}[export_format](book)
        target.write_bytes(text.encode("utf-8"))
    return target
