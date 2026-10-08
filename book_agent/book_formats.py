"""Books that are not EPUB: plain text, Markdown, HTML, Word documents, and PDFs that hold text.

Reading: each format is read into one simple shape, a list of blocks (a
heading, a paragraph, a list item, ...) whose text keeps emphasis, links, and
line breaks. `write_source_package` writes those blocks as an EPUB package,
and from there the pipeline treats the book as it does any EPUB.

Writing: `export_book` reads a compiled EPUB back into blocks and writes them
as text, Markdown, HTML, or a Word document.

What is kept is the text and its structure: headings, paragraphs, lists,
quotations, emphasis, links, pictures, and notes. Page layout, fonts, tables
as tables, and tracked changes are not.

Pictures: a Word document's own, and the files a Markdown or HTML file names
beside it (or holds as data URIs). They are kept as they are, the same files
from the source to the package the pipeline translates, to the compiled EPUB,
to an export.

Notes: a Word document's footnotes and endnotes, and a Markdown file's
`[^1]` notes, numbered through the book. Each is kept at the end of the
chapter that first refers to it, as plain text, and marked so that it is no passage
of the book: like the table of contents, a note is translated by the title
stage and given in translation at the compile.
"""

from __future__ import annotations

import base64
import re
import struct
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
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
    ".pdf": "pdf",
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
# The pictures a book can carry, by suffix, with the media type an EPUB gives them.
PICTURE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
_MAX_PICTURE_BYTES = 16 * 1024 * 1024
_MAX_PICTURES_BYTES = 128 * 1024 * 1024
# The attribute that marks a paragraph of a note in a converted book's package.
NOTE_ATTRIBUTE = "data-book-agent-note"


class BookFormatError(ValueError):
    """The file is not a book this module can read or write."""


@dataclass(frozen=True)
class Block:
    """One block of a book. `kind` is h1 to h6, p, li (a bullet), oli (a
    numbered item), quote, pre, hr, img (a picture), or note (a paragraph of
    a note); `html` is its inline XHTML: escaped text with <em>, <strong>,
    <code>, <a href>, <sub>, <sup>, and <br/>. A note refers to itself as
    <sup><a href="#note-3">3</a></sup>.

    `src` is, for a picture, its name among the book's `images`, and `html`
    its description; for a note, its id ("note-3"), and `html` its plain text."""

    kind: str
    html: str = ""
    src: str = ""


@dataclass
class Book:
    title: str = ""
    author: str = ""
    language: str = ""
    blocks: list[Block] = field(default_factory=list)
    # The pictures, by the name a picture block gives as its `src`.
    images: dict[str, bytes] = field(default_factory=dict)


def _kept(block: Block) -> bool:
    """Whether a block is worth keeping: a rule or a picture, or anything with text."""
    return block.kind in {"hr", "img"} or bool(_plain(block.html).strip())


# -- reading -----------------------------------------------------------------------------------


def read_book(path: str | Path, pictures: str | Path | None = None) -> Book:
    """Read a text, Markdown, HTML, Word, or PDF file. `pictures` is the
    folder the pictures a Markdown or HTML file names are found under, by the
    paths it names them by: the file's own folder, unless they were captured
    elsewhere with the file (`capture_pictures`)."""
    source = Path(path)
    kind = CONVERTED_SUFFIXES.get(source.suffix.casefold())
    if kind is None:
        raise BookFormatError(f"cannot read a {source.suffix or 'file without a suffix'} as a book")
    if source.stat().st_size > _MAX_SOURCE_BYTES:
        raise BookFormatError(f"{source.name} exceeds the {_MAX_SOURCE_BYTES} byte limit")
    base = Path(pictures) if pictures is not None else source.parent
    data = b"" if kind == "pdf" else source.read_bytes()
    if kind == "pdf":
        book = _read_pdf(source)
    elif kind == "docx":
        book = _read_docx(source)
    elif kind == "html":
        book = _read_html(data, base)
    elif kind == "md":
        book = _read_markdown(decode_text(data), base)
    else:
        book = _read_text(decode_text(data))
    book.blocks = [block for block in book.blocks if _kept(block)]
    if not any(block.kind not in {"hr", "img"} for block in book.blocks):
        raise BookFormatError(f"{source.name} contains no text")
    if not book.title:
        first = next((block for block in book.blocks if block.kind == "h1"), None)
        book.title = _plain(first.html).strip() if first else source.stem
    return book


_DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|gif|webp|svg\+xml));base64,(.*)$", re.IGNORECASE | re.DOTALL)
_DATA_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg"}
_MD_PICTURE = re.compile(r"!\[(?P<alt>[^\]]*)\]\(\s*<?(?P<src>[^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _picture_path(base: Path, src: str) -> Path | None:
    """The file a picture's `src` names, when it is one: a path relative to
    `base`, inside it, to a file of a picture type. Not an address on the
    web, an absolute path, or a way out of the folder."""
    src = src.strip()
    if not src or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", src) or src.startswith(("/", "\\")):
        return None
    relative = unquote(src.split("#", 1)[0].split("?", 1)[0])
    if Path(relative).suffix.casefold() not in PICTURE_TYPES:
        return None
    try:
        root = base.resolve()
        path = (root / relative).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() and path.stat().st_size <= _MAX_PICTURE_BYTES else None


def _picture_file(base: Path | None, src: str) -> tuple[str, bytes] | None:
    """The picture `src` names, as a name and its bytes: a data URI's, or a
    file's under `base`. None for one that is not there."""
    found = _DATA_URI.match(src.strip())
    if found:
        try:
            data = base64.b64decode(re.sub(r"\s+", "", found[2]), validate=True)
        except ValueError:
            return None
        if not data or len(data) > _MAX_PICTURE_BYTES:
            return None
        return f"picture{_DATA_SUFFIXES[found[1].casefold()]}", data
    path = _picture_path(base, src) if base is not None else None
    return (path.name, path.read_bytes()) if path is not None else None


def _keep_picture(images: dict[str, bytes], name: str, data: bytes) -> str:
    """Keep a picture's bytes among `images`, and return the name it is kept
    under: its own made safe, numbered where another picture has it, the same
    for the same bytes. "" when the book's pictures would be too large."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "picture"
    suffix = {".jpeg": ".jpg"}.get(Path(name).suffix.casefold(), Path(name).suffix.casefold())
    candidate, number = f"{stem}{suffix}", 1
    while candidate in images and images[candidate] != data:
        number += 1
        candidate = f"{stem}-{number}{suffix}"
    if candidate not in images:
        if sum(len(kept) for kept in images.values()) + len(data) > _MAX_PICTURES_BYTES:
            return ""
        images[candidate] = data
    return candidate


def _picture_block(images: dict[str, bytes], base: Path | None, src: str, alt: str) -> Block | None:
    """A picture block for the picture `src` names, kept among `images`; None for one that is not there."""
    found = _picture_file(base, src)
    name = _keep_picture(images, *found) if found else ""
    return Block("img", escape(normalize_space(alt), quote=False), name) if name else None


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def capture_pictures(source: str | Path) -> dict[str, Path]:
    """The picture files a Markdown or HTML file names beside it, by their
    path relative to its folder: what a job keeps with its copy of the file,
    so that the book it makes has them, wherever the file was."""
    path = Path(source)
    kind = CONVERTED_SUFFIXES.get(path.suffix.casefold())
    if kind not in {"md", "html"} or path.stat().st_size > _MAX_SOURCE_BYTES:
        return {}
    text = decode_text(path.read_bytes())
    if kind == "md":
        named = [match["src"] for match in _MD_PICTURE.finditer(text)]
    else:
        named = [str(image.get("src") or "") for image in BeautifulSoup(text, "html.parser").find_all("img")]
    root = path.parent.resolve()
    found: dict[str, Path] = {}
    for src in named:
        picture = _picture_path(root, src)
        if picture is not None:
            found.setdefault(picture.relative_to(root).as_posix(), picture)
    return found


class _Notes:
    """A book's notes as its text refers to them: numbered from 1 through
    the book in the order they are first referred to, and each placed after
    the block that first refers to it. `texts` holds each note's paragraphs,
    as plain text, by the label its source gives it."""

    def __init__(self, texts: dict[str, list[str]], blocks: list[Block]):
        self.texts = texts
        self.blocks = blocks
        self.numbers: dict[str, int] = {}
        self.after: list[tuple[int, str]] = []

    def refer(self, label: str) -> str:
        """The inline XHTML of a reference to the note `label`; "" for a note the book does not have."""
        if not any(paragraph.strip() for paragraph in self.texts.get(label, [])):
            return ""
        if label not in self.numbers:
            self.numbers[label] = len(self.numbers) + 1
            self.after.append((len(self.blocks), label))
        number = self.numbers[label]
        return f'<sup><a href="#note-{number}">{number}</a></sup>'

    def placed(self) -> list[Block]:
        """The blocks, each note after the block that first refers to it."""
        following: defaultdict[int, list[str]] = defaultdict(list)
        for index, label in self.after:
            following[index].append(label)
        placed: list[Block] = []
        for index in range(len(self.blocks) + 1):
            if index < len(self.blocks):
                placed.append(self.blocks[index])
            for label in following[index]:
                placed.extend(
                    Block("note", escape(normalize_space(paragraph), quote=False), f"note-{self.numbers[label]}")
                    for paragraph in self.texts[label]
                    if paragraph.strip()
                )
        return placed


def decode_text(data: bytes) -> str:
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


_SENTENCE_END = re.compile(r"[.!?…。！？,;:，；：\"'”’»)]$")


def _headed(lines: list[str]) -> list[str]:
    """The lines of a group, a chapter heading set on two lines joined into one:
    "CHAPTER VII." and "A Mad Tea-Party" under it, as Project Gutenberg sets
    them. Only a short line that ends no sentence goes with the heading above it."""
    joined: list[str] = []
    headed = False  # the last line is a heading that has its second line already
    for line in lines:
        previous = joined[-1] if joined else ""
        if (
            previous
            and not headed
            and len(previous) <= 80
            and _CHAPTER_HEADING.match(previous)
            and len(line) <= 60
            and not _SENTENCE_END.search(line)
        ):
            joined[-1] = f"{previous} {line}"
            headed = True
        else:
            joined.append(line)
            headed = False
    return joined


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
    paragraphs = [_join_wrapped(group) for group in groups] if wrapped else [line for group in groups for line in _headed(group)]
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
    r"|\[\^(?P<note>[^\]\s]+)\]"
    r"|!\[(?P<alt>[^\]]*)\]\([^)]*\)"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|(?P<strong>\*\*|__)(?=\S)(?P<strong_text>.+?)(?<=\S)(?P=strong)"
    r"|(?<![\w*])(?P<em>[*_])(?=\S)(?P<em_text>.+?)(?<=\S)(?P=em)(?![\w*])"
    r"|\\(?P<escaped>[\\`*_{}\[\]()#+\-.!>~|])"
)


def _markdown_inline(text: str, notes: _Notes | None = None) -> str:
    """Markdown inside a block as inline XHTML. `notes` turns a `[^1]` into a reference to that note."""
    parts: list[str] = []
    position = 0
    for match in _MD_INLINE.finditer(text):
        parts.append(escape(text[position : match.start()], quote=False))
        position = match.end()
        if match["code"]:
            parts.append(f"<code>{escape(match['code_text'].strip(), quote=False)}</code>")
        elif match["note"]:
            parts.append((notes.refer(match["note"]) if notes else "") or escape(match.group(0), quote=False))
        elif match["alt"] is not None:
            parts.append(escape(match["alt"], quote=False))  # a picture within a line of text: its description
        elif match["link_text"]:
            parts.append(f'<a href="{escape(match["href"])}">{_markdown_inline(match["link_text"], notes)}</a>')
        elif match["strong"]:
            parts.append(f"<strong>{_markdown_inline(match['strong_text'], notes)}</strong>")
        elif match["em"]:
            parts.append(f"<em>{_markdown_inline(match['em_text'], notes)}</em>")
        else:
            parts.append(escape(match["escaped"], quote=False))
    parts.append(escape(text[position:], quote=False))
    return "".join(parts)


_MD_NOTE = re.compile(r"^\[\^([^\]\s]+)\]:\s?(.*)$")


def _markdown_notes(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """The lines without their notes' definitions (`[^1]: ...`, and the
    indented lines that go on with one), and each note's paragraphs as plain
    text, by its label."""
    kept: list[str] = []
    notes: dict[str, list[str]] = {}
    label = ""
    blank = False
    for line in lines:
        definition = _MD_NOTE.match(line)
        if definition:
            label, blank = definition[1], False
            notes[label] = [definition[2].strip()]
            continue
        if label and not line.strip():
            blank = True
            continue
        if label and line[:1] in {" ", "\t"}:
            # An indented line goes on with the note; after a blank line, as a paragraph of its own.
            if blank:
                notes[label].append("")
            notes[label][-1] = (notes[label][-1] + " " + line.strip()).strip()
            blank = False
            continue
        if label:
            label = ""
            kept.append("")  # the note ended a paragraph, as the blank line it swallowed did
        kept.append(line)
    plain ={name: [_plain(_markdown_inline(paragraph)) for paragraph in paragraphs] for name, paragraphs in notes.items()}
    return kept, plain


def _read_markdown(text: str, pictures: Path | None = None) -> Book:
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
    lines, note_texts = _markdown_notes(lines)
    blocks: list[Block] = []
    notes = _Notes(note_texts, blocks)
    paragraph: list[str] = []
    kind = "p"

    def pictures_only() -> list[Block] | None:
        """A paragraph that is pictures and nothing else, as picture blocks; None for any other."""
        text = " ".join(line.strip() for line in paragraph)
        if kind != "p" or not re.fullmatch(rf"(?:\s*{_MD_PICTURE.pattern}\s*)+", text):
            return None
        found = [_picture_block(book.images, pictures, match["src"], match["alt"]) for match in _MD_PICTURE.finditer(text)]
        return [block for block in found if block] if any(found) else None

    def close() -> None:
        nonlocal kind
        if paragraph and (shown := pictures_only()):
            blocks.extend(shown)
            paragraph.clear()
        elif paragraph:
            # Two spaces or a backslash at the end of a line is a line break.
            pieces = [_markdown_inline(line.rstrip("\\").rstrip(), notes) for line in paragraph]
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
            blocks.append(Block(f"h{len(heading[1])}", _markdown_inline(heading[2], notes)))
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
    book.blocks = notes.placed()
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


Picture = Callable[[str, str], "Block | None"]


def _pictures_of(element: Tag, picture: Picture | None) -> list[Block]:
    """An element that holds pictures and no text, as picture blocks: as many
    of its pictures as are there to keep. None kept, or text beside them, is []."""
    images = element.find_all("img") if element.name != "img" else [element]
    if picture is None or not images or (element.name != "img" and element.get_text(strip=True)):
        return []
    return [block for image in images if (block := picture(str(image.get("src") or ""), str(image.get("alt") or "")))]


def _blocks_from_html(node: Tag, blocks: list[Block], inside: str = "", picture: Picture | None = None) -> None:
    """Walk an element: its block children become Blocks, and text standing
    loose between them becomes a paragraph. `inside` is li, oli, or quote
    while within a list or a quotation. `picture` makes a picture block of an
    image's src and alt, or None where there is no picture to keep: the
    image is then its description, as text."""
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
        elif name == "p" and child.has_attr(NOTE_ATTRIBUTE):
            flush()
            text = normalize_space(child.get_text())
            if text:
                blocks.append(Block("note", escape(text, quote=False), f"note-{child[NOTE_ATTRIBUTE]}"))
        elif (name == "img" or name in _HTML_BLOCKS or name in _HTML_CONTAINERS) and (shown := _pictures_of(child, picture)):
            flush()
            blocks.extend(shown)
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
            _blocks_from_html(child, blocks, within, picture)
        else:
            loose.append(_inline_html(child))
    flush()


def _read_html(data: bytes, pictures: Path | None = None) -> Book:
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
    _blocks_from_html(
        soup.body or soup, book.blocks, picture=lambda src, alt: _picture_block(book.images, pictures, src, alt)
    )
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


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_V = "{urn:schemas-microsoft-com:vml}"
_O = "{urn:schemas-microsoft-com:office:office}"
_NOTE_REFERENCES = {f"{_W}footnoteReference": "f", f"{_W}endnoteReference": "e"}


@dataclass
class _DocxContext:
    """What the runs of a paragraph refer to: the document's notes, and its
    pictures by relationship id. `pictures` collects the picture blocks of
    the paragraph being read."""

    archive: zipfile.ZipFile
    media: dict[str, str]
    images: dict[str, bytes]
    notes: _Notes
    pictures: list[Block] = field(default_factory=list)

    def picture(self, relationship: str, alt: str) -> None:
        name = self.media.get(relationship, "")
        try:
            info = self.archive.getinfo(name) if name else None
        except KeyError:
            info = None
        if info is None or info.file_size > _MAX_PICTURE_BYTES or Path(name).suffix.casefold() not in PICTURE_TYPES:
            return
        kept = _keep_picture(self.images, PurePosixPath(name).name, self.archive.read(info))
        if kept:
            self.pictures.append(Block("img", escape(normalize_space(alt), quote=False), kept))


def _drawn(element):
    """The drawings and VML pictures in a run, a fallback's copy and a text box's contents passed over."""
    for child in element:
        if child.tag == _FALLBACK or child.tag == f"{_W}txbxContent":
            continue
        if child.tag in {f"{_W}drawing", f"{_W}pict"}:
            yield child
        else:
            yield from _drawn(child)


def _docx_run(run, context: _DocxContext | None = None) -> str:
    """One run of a paragraph as inline XHTML. With `context`, a reference
    to a note becomes a reference to the book's note, and a picture is kept
    among the paragraph's pictures."""
    properties = run.find(f"{_W}rPr")
    text = ""
    references = ""
    for child in run:
        if child.tag == f"{_W}t":
            text += escape(child.text or "", quote=False)
        elif child.tag == f"{_W}tab":
            text += " "
        elif child.tag in {f"{_W}br", f"{_W}cr"}:
            text += "" if child.get(f"{_W}type") == "page" else "<br/>"
        elif child.tag == f"{_W}noBreakHyphen":
            text += chr(0x2011)
        elif child.tag in _NOTE_REFERENCES and context is not None:
            references += context.notes.refer(_NOTE_REFERENCES[child.tag] + child.get(f"{_W}id", ""))
    if context is not None:
        for drawing in _drawn(run):
            if drawing.tag == f"{_W}drawing":
                blip = drawing.find(f".//{_A}blip")
                described = drawing.find(f".//{_WP}docPr")
                alt = (described.get("descr") or described.get("title") or "") if described is not None else ""
                if blip is not None and blip.get(f"{_R}embed"):
                    context.picture(blip.get(f"{_R}embed", ""), alt)
            else:
                image = drawing.find(f".//{_V}imagedata")
                if image is not None and image.get(f"{_R}id"):
                    context.picture(image.get(f"{_R}id", ""), image.get(f"{_O}title", ""))
    if not text.replace("<br/>", "").strip():
        return text + references
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
    return text + references


def _docx_runs(paragraph, links: dict[str, str], context: _DocxContext | None = None) -> str:
    """A paragraph's text as inline XHTML. `links` are the document's
    hyperlink targets by relationship id."""
    parts: list[str] = []
    for child in paragraph:
        if child.tag == f"{_W}r":
            parts.append(_docx_run(child, context))
        elif child.tag == f"{_W}hyperlink":
            inner = "".join(_docx_run(run, context) for run in child.iter(f"{_W}r"))
            href = links.get(child.get(f"{_R}id", ""), "")
            parts.append(f'<a href="{escape(href)}">{inner}</a>' if inner.strip() and _SAFE_LINK.match(href) else inner)
        elif child.tag not in {f"{_W}pPr", f"{_W}del", f"{_W}moveFrom"}:
            # An insertion, a smart tag, a content control: its runs are the paragraph's.
            parts.extend(_docx_run(run, context) for run in child.iter(f"{_W}r"))
    html = "".join(parts)
    # Word splits a phrase into runs; put back together what has the same emphasis.
    for tag in ("em", "strong"):
        html = re.sub(rf"</{tag}>((?:\s|<br/>)*)<{tag}>", r"\1", html)
    return html


_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _docx_paragraphs(element):
    """Every paragraph under `element`, in order, a text box's among them.
    Word writes a text box twice, as a drawing and again as a fallback for
    programs that cannot draw it; the fallback is passed over."""
    for child in element:
        if child.tag == _FALLBACK:
            continue
        if child.tag == f"{_W}p":
            yield child
        yield from _docx_paragraphs(child)


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
        media: dict[str, str] = {}
        relationships = _docx_part(archive, "word/_rels/document.xml.rels")
        for relationship in relationships.iter(f"{_REL}Relationship") if relationships is not None else []:
            if relationship.get("Type", "").endswith("/hyperlink"):
                links[relationship.get("Id", "")] = relationship.get("Target", "")
            elif relationship.get("Type", "").endswith("/image") and relationship.get("TargetMode") != "External":
                media[relationship.get("Id", "")] = _docx_target(relationship.get("Target", ""))
        book = Book()
        core = _docx_part(archive, "docProps/core.xml")
        if core is not None:
            book.title = (core.findtext(f"{_DC}title") or "").strip()
            book.author = (core.findtext(f"{_DC}creator") or "").strip()
            book.language = (core.findtext(f"{_DC}language") or "").strip()
        blocks: list[Block] = []
        context = _DocxContext(archive, media, book.images, _Notes(_docx_notes(archive, links), blocks))
        body = document.find(f"{_W}body")
        for paragraph in _docx_paragraphs(body) if body is not None else []:
            _docx_block(paragraph, links, context, names, lists, book, blocks)
        book.blocks = context.notes.placed()
    return book


def _docx_target(target: str) -> str:
    """A relationship's target as the name of a part of the document: they are relative to word/."""
    parts: list[str] = []
    for part in (target.lstrip("/") if target.startswith("/") else f"word/{target}").split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def _docx_notes(archive: zipfile.ZipFile, links: dict[str, str]) -> dict[str, list[str]]:
    """The document's footnotes ("f2") and endnotes ("e1"), each as its paragraphs in plain text."""
    notes: dict[str, list[str]] = {}
    for part, tag, prefix in (("word/footnotes.xml", "footnote", "f"), ("word/endnotes.xml", "endnote", "e")):
        root = _docx_part(archive, part)
        for note in root.iter(f"{_W}{tag}") if root is not None else []:
            # The separator lines Word keeps among the notes are no notes.
            if note.get(f"{_W}type") in {"separator", "continuationSeparator", "continuationNotice"}:
                continue
            paragraphs = [normalize_space(_plain(_docx_runs(paragraph, links))) for paragraph in _docx_paragraphs(note)]
            notes[prefix + note.get(f"{_W}id", "")] = [paragraph for paragraph in paragraphs if paragraph]
    return notes


def _docx_block(paragraph, links, context: _DocxContext, names, lists, book: Book, blocks: list[Block]) -> None:
    """One paragraph of the document as a block, and its pictures after it."""
    context.pictures = []
    spaced = _docx_runs(paragraph, links, context)
    html = spaced.strip()
    pictures = context.pictures
    if not html:
        blocks.extend(pictures)
        return
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
    blocks.append(Block(kind, html))
    blocks.extend(pictures)


@dataclass
class _PdfLine:
    page: int
    x0: float
    x1: float
    top: float  # y of the line's top edge; larger is higher on the page
    size: float
    html: str  # the line's text, with what is set in italics or bold marked
    text: str
    margin: bool = False  # in the top or bottom band of its page


_ITALIC_FONT = re.compile(r"italic|oblique", re.IGNORECASE)
_BOLD_FONT = re.compile(r"bold|black|heavy", re.IGNORECASE)
_SENTENCE_STOP = re.compile(r"[.!?:;…\"'”’»)\]。！？：；」』）]\s*$")


def _pdf_lines(path: Path) -> tuple[list[_PdfLine], int, int]:
    """Every line of text in the PDF, in reading order; its number of pages;
    and how many of them are a picture of a page (one image over most of it)."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LAParams, LTAnno, LTChar, LTFigure, LTImage, LTTextContainer, LTTextLine
        from pdfminer.pdfparser import PDFSyntaxError
    except ImportError as error:  # the library is a dependency of the package; say so if it is missing
        raise BookFormatError("reading a PDF needs the pdfminer.six package: pip install pdfminer.six") from error
    lines: list[_PdfLine] = []
    pages = pictures = 0
    try:
        for pages, page in enumerate(extract_pages(str(path), laparams=LAParams()), start=1):
            pictures += any(
                isinstance(item, (LTFigure, LTImage)) and item.width * item.height > page.width * page.height * 0.7
                for item in page
            )
            for box in page:
                if not isinstance(box, LTTextContainer):
                    continue
                for line in box:
                    if not isinstance(line, LTTextLine):
                        continue
                    runs: list[tuple[str, bool, bool]] = []
                    sizes: dict[float, int] = {}
                    for item in line:
                        if isinstance(item, LTChar):
                            style = (bool(_ITALIC_FONT.search(item.fontname)), bool(_BOLD_FONT.search(item.fontname)))
                            sizes[round(item.size, 1)] = sizes.get(round(item.size, 1), 0) + 1
                        elif isinstance(item, LTAnno):
                            style = runs[-1][1:] if runs else (False, False)
                        else:
                            continue
                        character = item.get_text()
                        if runs and runs[-1][1:] == style:
                            runs[-1] = (runs[-1][0] + character, *style)
                        else:
                            runs.append((character, *style))
                    text = re.sub(r"\s+", " ", "".join(run[0] for run in runs)).strip()
                    if not text or not sizes:
                        continue
                    html = ""
                    for piece, italic, bold in runs:
                        piece = escape(re.sub(r"\s+", " ", piece), quote=False)
                        if piece.strip() and italic:
                            piece = f"<em>{piece}</em>"
                        if piece.strip() and bold:
                            piece = f"<strong>{piece}</strong>"
                        html += piece
                    lines.append(
                        _PdfLine(
                            page=pages,
                            x0=line.x0,
                            x1=line.x1,
                            top=line.y1,
                            size=max(sizes, key=sizes.get),
                            html=re.sub(r"\s+", " ", html).strip(),
                            text=text,
                            margin=line.y1 > page.height * 0.93 or line.y0 < page.height * 0.07,
                        )
                    )
    except (PDFSyntaxError, ValueError, KeyError, TypeError) as error:
        raise BookFormatError(f"{path.name} is not a PDF this can read: {error}") from error
    return lines, pages, pictures


def _common(values: list[float], step: float = 1.0) -> float:
    """The value most of `values` are near."""
    counts: dict[float, int] = {}
    for value in values:
        key = round(value / step) * step
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get) if counts else 0.0


def _without_running_heads(lines: list[_PdfLine], pages: int) -> list[_PdfLine]:
    """The lines without page numbers and the title repeated at the top or
    bottom of the pages: what stands in a page's margin band and comes back,
    digits aside, on a quarter of the pages."""
    seen: dict[str, set[int]] = {}
    for line in lines:
        if line.margin:
            seen.setdefault(re.sub(r"\d+", "#", line.text.casefold()), set()).add(line.page)
    repeated = {key for key, where in seen.items() if len(where) >= max(3, pages // 4)}
    return [
        line
        for line in lines
        if not (line.margin and (re.sub(r"\d+", "#", line.text.casefold()) in repeated or re.fullmatch(r"[\divxlc\s.\-–—]+", line.text.casefold())))
    ]


def _read_pdf(path: Path) -> Book:
    """A PDF that holds text (not pictures of pages) as headings and paragraphs.

    A PDF has no paragraphs, only lines at places on a page. They are put
    together again from how a book is set: a new paragraph where a line is
    indented or follows a wider gap than the lines of a paragraph have, a
    heading where the type is larger. Running heads and page numbers are left
    out, and a word divided at the end of a line is joined."""
    lines, pages, pictures = _pdf_lines(path)
    if sum(len(line.text) for line in lines) < 40 * max(pages, 1):
        raise BookFormatError(
            f"{path.name} has almost no text in it: it looks like scanned pages, which need OCR first"
        )
    if pictures * 2 > pages:
        # A scan with the text a machine read off it laid underneath: that
        # text has the machine's misreadings and no paragraphs to find.
        raise BookFormatError(
            f"{path.name} is scanned pages ({pictures} of {pages} are pictures of a page); "
            "a scanned PDF is not read, even with a text layer under the pictures"
        )
    lines = _without_running_heads(lines, pages)
    body = _common([line.size for line in lines for _ in range(len(line.text))], step=0.1)
    plain = [line for line in lines if abs(line.size - body) <= body * 0.06]
    left = _common([line.x0 for line in plain])
    right = max((line.x1 for line in plain), default=0.0)
    gaps = [
        before.top - after.top
        for before, after in zip(plain, plain[1:])
        if before.page == after.page and 0 < before.top - after.top < body * 3
    ]
    gap = _common(gaps, step=0.5) or body * 1.2
    indented = sum(line.x0 > left + body * 0.5 for line in plain) >= len(plain) * 0.03
    # Words of the book as they stand whole in a line, to tell "exam-/ple" from "three-/dimensional".
    words = {word.casefold() for line in lines for word in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", line.text)}

    paragraphs: list[list[_PdfLine]] = []
    for line in lines:
        previous = paragraphs[-1][-1] if paragraphs else None
        if previous is None:
            new = True
        elif abs(line.size - previous.size) > body * 0.08:
            new = True  # the type changes: into or out of a heading
        elif line.page == previous.page and previous.top - line.top > gap * 1.2 * max(1.0, line.size / body):
            new = True  # more room than between the lines of a paragraph (larger type has more of its own)
        elif line.page == previous.page and line.top > previous.top:
            new = True  # further up the page: another column or a box
        elif abs(line.size - body) <= body * 0.08 and line.x0 > left + body * 0.5 and line.x0 > previous.x0 + body * 0.5:
            new = True  # a first line set in (a heading's lines are centred, each where it falls)
        elif not indented and previous.x1 < right - body * 4 and _SENTENCE_STOP.search(previous.text):
            new = True  # no indents in this book: a short line that ends a sentence ends a paragraph
        else:
            new = False
        if new:
            paragraphs.append([line])
        else:
            paragraphs[-1].append(line)

    larger = sorted({round(group[0].size, 1) for group in paragraphs if group[0].size > body * 1.15}, reverse=True)
    blocks: list[Block] = []
    for group in paragraphs:
        html = group[0].html
        for before, line in zip(group, group[1:]):
            divided = re.search(r"([^\W\d_]+)-$", before.text)
            first = re.match(r"[^\W\d_]+", line.text)
            if divided and first and first.group(0)[:1].islower():
                whole = (divided.group(1) + first.group(0)).casefold()
                kept = f"{divided.group(1)}-{first.group(0)}".casefold()
                # Joined without its hyphen when the book has that word whole and not the hyphenated one.
                html = html[:-1] + line.html if whole in words and kept not in words else html + line.html
            elif before.text.endswith("-") or (_UNSPACED.match(before.text[-1:]) and _UNSPACED.match(line.text[:1])):
                html += line.html
            else:
                html += " " + line.html
        for tag in ("em", "strong"):
            html = re.sub(rf"</{tag}>(\s*)<{tag}>", r"\1", html)
        html = re.sub(r"\s+", " ", html)
        text = _plain(html).strip()
        size = round(group[0].size, 1)
        if size in larger and len(text) <= 200:
            kind = f"h{min(larger.index(size) + 1, 6)}"
            html = re.sub(r"</?(?:strong|em)>", "", html)
        elif len(group) == 1 and len(text) <= 80 and _CHAPTER_HEADING.match(text):
            kind = "h2"
            html = re.sub(r"</?(?:strong|em)>", "", html)
        else:
            kind = "p"
        blocks.append(Block(kind, html.strip()))

    # A contents page names every chapter before the chapter does: of two
    # headings that read the same, the later one is the heading.
    last = {_plain(block.html).casefold().strip(): index for index, block in enumerate(blocks) if block.kind != "p"}
    blocks = [
        Block("p", block.html) if block.kind != "p" and last[_plain(block.html).casefold().strip()] != index else block
        for index, block in enumerate(blocks)
    ]
    book = Book(blocks=blocks)
    try:
        book.title, book.author = pdf_title_and_author(path)
    except BookFormatError:
        pass  # the title is a nicety; the text has been read
    return book


def pdf_title_and_author(path: str | Path) -> tuple[str, str]:
    """The title and author a PDF records about itself; "" for what it does not."""
    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfparser import PDFParser
    except ImportError as error:
        raise BookFormatError("reading a PDF needs the pdfminer.six package: pip install pdfminer.six") from error

    def field(info: dict, name: str) -> str:
        value = info.get(name, b"")
        if isinstance(value, bytes):
            value = value.decode("utf-16" if value.startswith((b"\xfe\xff", b"\xff\xfe")) else "latin-1", errors="replace")
        return str(value).strip()

    try:
        with Path(path).open("rb") as handle:
            for info in PDFDocument(PDFParser(handle)).info:
                return field(info, "Title"), field(info, "Author")
    except Exception as error:  # noqa: BLE001 - whatever is wrong with the file, it has no title to give
        raise BookFormatError(f"{Path(path).name} is not a PDF this can read: {error}") from error
    return "", ""


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
.picture { text-align: center; margin: 1em 0; }
.picture img { max-width: 100%; }
aside { font-size: 0.9em; }
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


_NOTE_REFERENCE = re.compile(r'<a href="#(note-[^"]*)">')


def _chapter_body(
    blocks: list[Block], picture_src: Callable[[str], str], package: bool = False, homes: dict[str, str] | None = None
) -> str:
    """A chapter's blocks as XHTML, its notes at its end. `picture_src` is
    where a picture is found by its name. In a `package`, a note's paragraphs
    are marked as no passage of the book, and notes and references to them
    say what they are, for a reader that shows a note beside the text.
    `homes` is the document each note of the book stands in, by the note's
    id: a reference to a note another chapter holds links into that document."""
    lines: list[str] = []
    notes: dict[str, list[str]] = {}
    own = {block.src for block in blocks if block.kind == "note"}

    def referred(match: re.Match[str]) -> str:
        document = "" if match[1] in own else (homes or {}).get(match[1], "")
        return f'<a epub:type="noteref" href="{escape(document)}#{match[1]}">'

    open_list = ""
    for block in blocks:
        if block.kind == "note":
            notes.setdefault(block.src, []).append(block.html)
            continue
        wanted = {"li": "ul", "oli": "ol"}.get(block.kind, "")
        if open_list != wanted:
            if open_list:
                lines.append(f"</{open_list}>")
            if wanted:
                lines.append(f"<{wanted}>")
            open_list = wanted
        html = _NOTE_REFERENCE.sub(referred, block.html) if package else block.html
        if block.kind == "hr":
            lines.append("<hr/>")
        elif block.kind == "img":
            lines.append(f'<div class="picture"><img src="{escape(picture_src(block.src))}" alt="{escape(_plain(block.html))}"/></div>')
        elif wanted:
            lines.append(f"<li>{html}</li>")
        elif block.kind == "quote":
            lines.append(f"<blockquote><p>{html}</p></blockquote>")
        else:
            lines.append(f"<{block.kind}>{html}</{block.kind}>")
    if open_list:
        lines.append(f"</{open_list}>")
    for note, paragraphs in notes.items():
        number = note.removeprefix("note-")
        kind = ' epub:type="footnote"' if package else ' class="note"'
        marked = f' {NOTE_ATTRIBUTE}="{escape(number)}"' if package else ""
        lines.append(
            f'<aside id="{escape(note)}"{kind}>'
            + "".join(
                f"<p{marked}>{paragraph if package or index else f'{escape(number)}. {paragraph}'}</p>"
                for index, paragraph in enumerate(paragraphs)
            )
            + "</aside>"
        )
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
    # A note stands in the chapter that first refers to it: one that refers to it again finds it there.
    homes = {
        block.src: f"chapter-{number:04d}.xhtml"
        for number, chapter in enumerate(chapters, start=1)
        for block in chapter
        if block.kind == "note"
    }
    for number, chapter in enumerate(chapters, start=1):
        name = f"chapter-{number:04d}"
        heading = next((block for block in chapter if re.fullmatch(r"h[1-6]", block.kind)), None)
        label = escape(_plain(heading.html).strip(), quote=False) if heading else f"{title} ({number})" if len(chapters) > 1 else title
        files[f"OEBPS/text/{name}.xhtml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'xml:lang="{escape(language)}" lang="{escape(language)}">\n'
            f'<head><title>{label}</title><link rel="stylesheet" type="text/css" href="../styles.css"/></head>\n'
            f"<body>\n{_chapter_body(chapter, lambda picture: f'../images/{picture}', package=True, homes=homes)}\n</body>\n</html>\n"
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
        + "".join(
            f'\n    <item id="picture-{number:04d}" href="images/{escape(name)}" '
            f'media-type="{PICTURE_TYPES[PurePosixPath(name).suffix]}"/>'
            for number, name in enumerate(sorted(book.images), start=1)
        )
        + "\n  </manifest>\n  <spine>\n"
        + "\n".join(spine)
        + "\n  </spine>\n</package>\n"
    )
    for name, content in files.items():
        path = package.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    for name, data in book.images.items():
        path = package / "OEBPS" / "images" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


# -- writing a compiled book in another format ----------------------------------------------------

_OPF = "{http://www.idpf.org/2007/opf}"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
# A link into another document of the book, at a note: not an address on the web.
_NOTE_ELSEWHERE = re.compile(r"^[^#:]+#note-")


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
        notes: set[str] = set()
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
                # A reference to a note an earlier chapter holds (`write_source_package`) is,
                # in the book read as one text, a reference to the book's note again.
                notes.update(f"note-{paragraph[NOTE_ATTRIBUTE]}" for paragraph in soup.find_all("p", attrs={NOTE_ATTRIBUTE: True}))
                for link in soup.find_all("a", href=_NOTE_ELSEWHERE):
                    fragment = str(link["href"]).split("#", 1)[1]
                    if fragment in notes:
                        link["href"] = f"#{fragment}"
                _blocks_from_html(
                    soup.body or soup,
                    book.blocks,
                    picture=lambda src, alt, document=name: _epub_picture(archive, names, book.images, document, src, alt),
                )
    book.blocks = [block for block in book.blocks if _kept(block)]
    return book


def _epub_picture(
    archive: zipfile.ZipFile, names: set[str], images: dict[str, bytes], document: str, src: str, alt: str
) -> Block | None:
    """A picture block for the picture a document of the EPUB shows, by its src relative to that document."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", src) or src.startswith("/"):
        found = _picture_file(None, src)  # a data URI, or nothing
        name = _keep_picture(images, *found) if found else ""
        return Block("img", escape(normalize_space(alt), quote=False), name) if name else None
    parts: list[str] = []
    for part in (PurePosixPath(document).parent / unquote(src.split("#", 1)[0])).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    target = "/".join(parts)
    if target not in names or PurePosixPath(target).suffix.casefold() not in PICTURE_TYPES:
        return None
    info = archive.getinfo(target)
    if info.file_size > _MAX_PICTURE_BYTES:
        return None
    name = _keep_picture(images, PurePosixPath(target).name, archive.read(info))
    return Block("img", escape(normalize_space(alt), quote=False), name) if name else None


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


def _note_number(run: _Run) -> str:
    """The number of the note a run refers to, or "" for a run that is no reference to a note."""
    return run.href.removeprefix("#note-") if run.href.startswith("#note-") else ""


def _picture_uri(book: Book, name: str) -> str:
    """A picture as a data URI, for a file that is to hold its pictures itself."""
    data = base64.b64encode(book.images.get(name, b"")).decode("ascii")
    return f"data:{PICTURE_TYPES.get(PurePosixPath(name).suffix, 'application/octet-stream')};base64,{data}"


def _as_text(book: Book) -> str:
    lines: list[str] = []
    number = 0
    previous = ""
    for block in book.blocks:
        number = number + 1 if block.kind == "oli" else 0
        text = "".join(
            "\n" if run.line_break else f"[{_note_number(run)}]" if _note_number(run) else run.text
            for run in _runs(block.html)
        ).strip()
        if block.kind == "img":
            if not text:
                continue
            text = f"[{text}]"
        elif block.kind == "note":
            # The first paragraph of a note begins with its number.
            text = text if previous == block.src else f"[{block.src.removeprefix('note-')}] {text}"
        previous = block.src if block.kind == "note" else ""
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
    previous = ""
    for block in book.blocks:
        number = number + 1 if block.kind == "oli" else 0
        following, previous = previous == block.src, block.src if block.kind == "note" else ""
        if block.kind == "hr":
            lines.append("---")
            continue
        if block.kind == "pre":
            lines.append("```\n" + "".join(run.text for run in _runs(block.html)) + "\n```")
            continue
        if block.kind == "img":
            lines.append(f"![{_md_escape(_plain(block.html))}]({_picture_uri(book, block.src)})")
            continue
        if block.kind == "note":
            # A note as Markdown writes one; a paragraph after its first, indented under it.
            text = _md_escape(_plain(block.html))
            lines.append(f"    {text}" if following else f"[^{block.src.removeprefix('note-')}]: {text}")
            continue
        one_line = block.kind not in {"p", "quote"}
        text = ""
        for run in _runs(block.html):
            if run.line_break:
                text += " " if one_line else "\\\n"
                continue
            if _note_number(run):
                text += f"[^{_note_number(run)}]"
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
        "<body>\n"
        # Chapter by chapter, so that each chapter's notes follow it; its pictures are in the file.
        + "\n".join(_chapter_body(chapter, lambda name: _picture_uri(book, name)) for chapter in split_chapters(book.blocks))
        + "\n</body>\n</html>\n"
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


def picture_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's, GIF's, JPEG's, or WebP's width and height in pixels, as its header gives them."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in {b"GIF87a", b"GIF89a"}:
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    return width, height
                if marker == 0xFF or 0xD0 <= marker <= 0xD9:
                    index += 1 if marker == 0xFF else 2
                    continue
                index += 2 + struct.unpack(">H", data[index + 2 : index + 4])[0]
            return None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if chunk == b"VP8 ":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except struct.error:
        return None
    return None


_EMU_PER_PIXEL = 9525  # at 96 pixels to the inch
_MAX_PICTURE_WIDTH = 6 * 914400  # six inches, the width of a page's text


def _docx_picture(number: int, relationship: str, size: tuple[int, int], alt: str) -> str:
    """A paragraph holding one picture, as wide as it is at 96 pixels to the inch, or the page's text."""
    width, height = (max(1, value) * _EMU_PER_PIXEL for value in size)
    if width > _MAX_PICTURE_WIDTH:
        width, height = _MAX_PICTURE_WIDTH, height * _MAX_PICTURE_WIDTH // width
    described = escape(alt)
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{width}" cy="{height}"/>'
        f'<wp:docPr id="{number}" name="Picture {number}" descr="{described}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{number}" name="Picture {number}" descr="{described}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{relationship}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def _docx_paragraph(block: Block, list_id: int, links: dict[str, str], notes: set[str] = frozenset()) -> str:
    """One block as a Word paragraph. `list_id` is the list a list item
    belongs to; `links` collects the targets of its hyperlinks by relationship
    id; `notes` are the numbers of the notes the book has, each a footnote."""
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
        if _note_number(run) in notes:
            # Word numbers its footnotes itself.
            body += f'<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="{_note_number(run)}"/></w:r>'
            index += 1
        elif run.href and _SAFE_LINK.match(run.href) and not run.href.startswith("#"):
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


_DOCX_NOTE_STYLES = (
    '<w:style w:type="paragraph" w:styleId="FootnoteText"><w:name w:val="footnote text"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="FootnoteReference"><w:name w:val="footnote reference"/>'
    '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
)


def _docx_footnotes(notes: dict[str, list[str]]) -> str:
    """The footnotes part: Word's two separators, then each note by its number."""
    separators = (
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
    )
    written = []
    for number, paragraphs in notes.items():
        body = ""
        for index, paragraph in enumerate(paragraphs):
            mark = '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>' if index == 0 else ""
            body += (
                f'<w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>{mark}'
                f'<w:r><w:t xml:space="preserve">{" " if index == 0 else ""}{_xml_text(_plain(paragraph))}</w:t></w:r></w:p>'
            )
        written.append(f'<w:footnote w:id="{number}">{body}</w:footnote>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:footnotes xmlns:w="{_W_NS}">{separators}{"".join(written)}</w:footnotes>\n'
    )


def _write_docx(book: Book, output: Path) -> None:
    paragraphs: list[str] = []
    lists: list[int] = []
    links: dict[str, str] = {}
    pictures: dict[str, str] = {}  # relationship id: the part a picture is kept in
    notes: dict[str, list[str]] = {}
    for block in book.blocks:
        if block.kind == "note" and block.src.removeprefix("note-").isdigit():
            notes.setdefault(block.src.removeprefix("note-"), []).append(block.html)
    previous = ""
    for block in book.blocks:
        if block.kind == "note":
            continue
        if block.kind in {"li", "oli"} and block.kind != previous:
            lists.append(_BULLET_LIST if block.kind == "li" else _NUMBERED_LIST)
        previous = block.kind
        if block.kind == "img":
            data = book.images.get(block.src, b"")
            size = picture_size(data)
            if size is None:  # an SVG, or a picture Word could not show: its description stands for it
                if _plain(block.html).strip():
                    paragraphs.append(_docx_paragraph(Block("p", f"[{block.html}]"), len(lists), links))
                continue
            relationship = f"rIdPicture{len(pictures) + 1}"
            pictures[relationship] = f"media/{block.src}"
            paragraphs.append(_docx_picture(len(pictures), relationship, size, _plain(block.html)))
            continue
        paragraphs.append(_docx_paragraph(block, len(lists), links, set(notes)))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}" xmlns:r="{_OFFICE_RELATIONSHIPS}" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>\n"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f'<Relationship Id="rId1" Type="{_OFFICE_RELATIONSHIPS}/styles" Target="styles.xml"/>\n'
        f'<Relationship Id="rId2" Type="{_OFFICE_RELATIONSHIPS}/numbering" Target="numbering.xml"/>\n'
        + (f'<Relationship Id="rId3" Type="{_OFFICE_RELATIONSHIPS}/footnotes" Target="footnotes.xml"/>\n' if notes else "")
        + "".join(
            f'<Relationship Id="{key}" Type="{_OFFICE_RELATIONSHIPS}/hyperlink" Target="{escape(target)}" TargetMode="External"/>\n'
            for key, target in links.items()
        )
        + "".join(
            f'<Relationship Id="{key}" Type="{_OFFICE_RELATIONSHIPS}/image" Target="{escape(target)}"/>\n'
            for key, target in pictures.items()
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
    types = _DOCX_TYPES
    suffixes = sorted({PurePosixPath(target).suffix.lstrip(".") for target in pictures.values()})
    defaults = "".join(
        f'<Default Extension="{suffix}" ContentType="{PICTURE_TYPES["." + suffix]}"/>\n' for suffix in suffixes
    )
    footnotes = (
        '<Override PartName="/word/footnotes.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>\n'
        if notes
        else ""
    )
    types = types.replace('<Default Extension="xml" ContentType="application/xml"/>\n', f'<Default Extension="xml" ContentType="application/xml"/>\n{defaults}')
    types = types.replace("</Types>", f"{footnotes}</Types>")
    styles = _docx_styles()
    if notes:
        styles = styles.replace("</w:styles>", f"{_DOCX_NOTE_STYLES}</w:styles>")
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": types,
        "_rels/.rels": _DOCX_RELS,
        "word/document.xml": document,
        "word/_rels/document.xml.rels": relationships,
        "word/styles.xml": styles,
        "word/numbering.xml": _docx_numbering(lists),
        "docProps/core.xml": core,
    }
    if notes:
        parts["word/footnotes.xml"] = _docx_footnotes(notes)
    for target in pictures.values():
        parts[f"word/{target}"] = book.images[PurePosixPath(target).name]
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in parts.items():
            # A fixed date and platform: the same book gives the same file.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 0
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8") if isinstance(content, str) else content)


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
