"""A book written as a PDF: its text reflowed onto pages, not laid out as any
source was (docs/OUTPUT_AND_CONFIG_UX.md, 2.3).

Writing one needs the reportlab package, and a font that has the letters of
the language the book is in. The font is embedded, so the file reads the same
on every machine; none comes with this project. It is the file named by
`output.pdf_font`, or one of this system's found by name.

`check_pdf_output` says before a job starts whether its PDF can be written;
`write_pdf` writes it.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path

from .book_formats import _SAFE_LINK, Block, Book, BookFormatError, _note_number, _plain, _runs, picture_size
from .languages import SCRIPTS, profile

NEEDS_REPORTLAB = "writing a PDF needs the reportlab package: pip install reportlab (or this project's [pdf] extra)"

# Scripts reportlab sets as they are written: one letter after another, left to right.
# The others join their letters or run right to left, which it does not do.
_SET_SCRIPTS = {"Latin", "Greek", "Cyrillic", "Armenian", "Georgian", "Han", "Hiragana", "Katakana", "Hangul"}
# Scripts whose text has no spaces to break a line at.
_UNSPACED_SCRIPTS = {"Han", "Hiragana", "Katakana"}
# A few letters of each script, to try a font with before there is a book to try it with.
_PROBES = {
    "Latin": "AaZzÄäÉéÑñÖöÜüß",
    "Greek": "ΑαΒβΩω",
    "Cyrillic": "АаБбЖжЯя",
    "Armenian": "ԱաԲբ",
    "Georgian": "აბგდ",
    "Han": "的一是不了人我在有他这中大来上国",
    "Hiragana": "あいうえおかきくのは",
    "Katakana": "アイウエオカキクノハ",
    "Hangul": "가나다라마바사아자한국어",
}


@dataclass(frozen=True)
class _Family:
    """A font's files by weight and slope; a missing one is stood in for by the regular."""

    regular: str
    bold: str = ""
    italic: str = ""
    bold_italic: str = ""


# Fonts a system is likely to have, the more usual first, with TrueType outlines:
# reportlab does not embed the other kind (most .otf files, Noto CJK among them).
_LATIN = (
    _Family("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
    _Family(
        "LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"
    ),
    _Family("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    _Family("Times New Roman.ttf", "Times New Roman Bold.ttf", "Times New Roman Italic.ttf", "Times New Roman Bold Italic.ttf"),
    _Family("NotoSerif-Regular.ttf", "NotoSerif-Bold.ttf", "NotoSerif-Italic.ttf", "NotoSerif-BoldItalic.ttf"),
    _Family("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
    _Family("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    _Family("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf"),
    _Family("Arial Unicode.ttf"),
    _Family("FreeSerif.ttf", "FreeSerifBold.ttf", "FreeSerifItalic.ttf", "FreeSerifBoldItalic.ttf"),
    # The one font that comes with reportlab: Western European letters only.
    _Family("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf", "VeraBI.ttf"),
)
_FAMILIES: dict[str, tuple[_Family, ...]] = {
    "Han": (
        _Family("msyh.ttc", "msyhbd.ttc"),
        _Family("simsun.ttc"),
        _Family("simhei.ttf"),
        _Family("PingFang.ttc"),
        _Family("STHeiti Light.ttc", "STHeiti Medium.ttc"),
        _Family("Songti.ttc"),
        _Family("NotoSansSC-VF.ttf"),
        _Family("NotoSansSC-Regular.ttf", "NotoSansSC-Bold.ttf"),
        _Family("NotoSansTC-Regular.ttf", "NotoSansTC-Bold.ttf"),
        _Family("msjh.ttc", "msjhbd.ttc"),
        _Family("wqy-microhei.ttc"),
        _Family("wqy-zenhei.ttc"),
        _Family("DroidSansFallbackFull.ttf"),
        _Family("Arial Unicode.ttf"),
    ),
    "Hiragana": (
        _Family("YuGothR.ttc", "YuGothB.ttc"),
        _Family("meiryo.ttc", "meiryob.ttc"),
        _Family("msgothic.ttc"),
        _Family("NotoSansJP-Regular.ttf", "NotoSansJP-Bold.ttf"),
        _Family("ipaexg.ttf"),
        _Family("ipag.ttf"),
        _Family("TakaoPGothic.ttf"),
        _Family("VL-PGothic-Regular.ttf"),
        _Family("Arial Unicode.ttf"),
    ),
    "Hangul": (
        _Family("malgun.ttf", "malgunbd.ttf"),
        _Family("AppleGothic.ttf"),
        _Family("NotoSansKR-Regular.ttf", "NotoSansKR-Bold.ttf"),
        _Family("NanumGothic.ttf", "NanumGothicBold.ttf"),
        _Family("NanumMyeongjo.ttf", "NanumMyeongjoBold.ttf"),
        _Family("UnDotum.ttf", "UnDotumBold.ttf"),
        _Family("Arial Unicode.ttf"),
    ),
}
_FAMILIES["Katakana"] = _FAMILIES["Hiragana"]


def _font_folders() -> list[Path]:
    return [*_system_font_folders(), *_reportlab_fonts()]


def _reportlab_fonts() -> list[Path]:
    try:
        import reportlab
    except ImportError:
        return []
    return [Path(reportlab.__file__).parent / "fonts"]


def _system_font_folders() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        return [
            Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "Microsoft" / "Windows" / "Fonts",
        ]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".fonts", home / ".local" / "share" / "fonts"]


@lru_cache(maxsize=1)
def _system_fonts() -> dict[str, Path]:
    """This system's TrueType font files by their names, lower case."""
    found: dict[str, Path] = {}
    for folder in _font_folders():
        try:
            files = sorted(path for path in folder.rglob("*") if path.suffix.lower() in {".ttf", ".ttc"})
        except OSError:
            continue
        for path in files:
            found.setdefault(path.name.lower(), path)
    return found


def _reportlab():
    try:
        import reportlab  # noqa: F401
    except ImportError as error:
        raise BookFormatError(NEEDS_REPORTLAB) from error


@lru_cache(maxsize=32)
def _registered(path: Path) -> str:
    """The name reportlab knows this font file by, read and registered once.
    Raises BookFormatError for a file it cannot embed."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    name = "book-" + re.sub(r"[^A-Za-z0-9]+", "-", path.stem) + "-" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:6]
    try:
        pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
    except (TTFError, OSError, ValueError, KeyError) as error:
        raise BookFormatError(
            f"the font {path.name} cannot be put into a PDF ({error}); "
            "it needs TrueType outlines (.ttf or .ttc): name another with output.pdf_font"
        ) from error
    return name


def _letters(text: str) -> set[str]:
    return {character for character in text if not character.isspace() and character.isprintable()}


def _lacking(font: str, letters: set[str]) -> set[str]:
    from reportlab.pdfbase import pdfmetrics

    has = pdfmetrics.getFont(font).face.charToGlyph
    return {letter for letter in letters if ord(letter) not in has}


def _scripts(language: str) -> tuple[str, ...]:
    try:
        return profile(language).scripts
    except ValueError:
        return ()


def _unset_reason(language: str) -> str:
    """Why a book in `language` cannot be set as a PDF, or "" when it can."""
    unset = [script for script in _scripts(language) if script not in _SET_SCRIPTS]
    if not unset:
        return ""
    return (
        f"a PDF cannot be written in {unset[0]} script: its letters join or run right to left, "
        "which this writer does not do; choose another output.format"
    )


@dataclass(frozen=True)
class _Fonts:
    regular: str
    bold: str
    italic: str
    bold_italic: str


def _family_fonts(family: _Family, files: dict[str, Path]) -> _Fonts | None:
    """A family's fonts registered, or None when this system lacks it or it cannot be embedded."""
    regular = files.get(family.regular.lower())
    if regular is None:
        return None
    try:
        name = _registered(regular)
    except BookFormatError:
        return None

    def other(file: str, fallback: str) -> str:
        path = files.get(file.lower()) if file else None
        if path is None:
            return fallback
        try:
            return _registered(path)
        except BookFormatError:
            return fallback

    bold = other(family.bold, name)
    italic = other(family.italic, name)
    return _Fonts(name, bold, italic, other(family.bold_italic, bold if italic == name else italic))


def _choose_fonts(text: str, language: str, configured: Path | None) -> _Fonts:
    """The fonts a book in `language` is set in: the configured file, or the
    first of this system's that has (nearly) every letter of `text`."""
    _reportlab()
    reason = _unset_reason(language)
    if reason:
        raise BookFormatError(reason)
    letters = _letters(text)
    if configured is not None:
        path = Path(configured)
        if not path.is_file():
            raise BookFormatError(f"output.pdf_font names a file that is not there: {path}")
        name = _registered(path)
        lacking = _lacking(name, letters)
        if len(lacking) > max(3, len(letters) // 50):
            raise BookFormatError(
                f"the font {path.name} lacks {len(lacking)} of the book's letters "
                f"({''.join(sorted(lacking)[:12])}…); name another with output.pdf_font"
            )
        return _Fonts(name, name, name, name)
    scripts = _scripts(language)
    candidates = [family for script in scripts for family in _FAMILIES.get(script, ())] + list(_LATIN)
    files = _system_fonts()
    best: tuple[int, _Fonts] | None = None
    for family in dict.fromkeys(candidates):
        fonts = _family_fonts(family, files)
        if fonts is None:
            continue
        lacking = len(_lacking(fonts.regular, letters))
        if lacking <= max(3, len(letters) // 200):
            return fonts
        if best is None or lacking < best[0]:
            best = (lacking, fonts)
    # A font that has most of the letters still gives a readable book.
    if best is not None and best[0] <= len(letters) // 10:
        return best[1]
    named = profile(language).display_name if scripts else language or "this language"
    raise BookFormatError(
        f"no font of this system that can be put into a PDF has the letters of {named}; "
        "set output.pdf_font to a .ttf or .ttc file that does, or choose another output.format"
    )


def check_pdf_output(language: str, font: Path | None = None) -> None:
    """Raise BookFormatError, saying what to do, when a PDF in `language` cannot be written here."""
    probe = "".join(_PROBES.get(script, "") for script in _scripts(language)) or _PROBES["Latin"]
    _choose_fonts(probe, language, font)


def pdf_output_problem(language: str, font: Path | None = None) -> str:
    """Why a PDF in `language` cannot be written here, or "" when it can."""
    try:
        check_pdf_output(language, font)
    except BookFormatError as error:
        return str(error)
    return ""


def _xml(text: str) -> str:
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text), quote=False)


def _markup(html: str, fonts: _Fonts, keep_spaces: bool = False) -> str:
    """Inline XHTML as reportlab's paragraph markup."""
    out = ""
    for run in _runs(html):
        if run.line_break:
            out += "<br/>"
            continue
        if _note_number(run):
            out += f"<super>{_xml(_note_number(run))}</super>"
            continue
        text = _xml(run.text)
        if keep_spaces:
            text = text.replace(" ", "\u00a0")
        text = text.replace("\n", "<br/>")
        if not text:
            continue
        face = fonts.bold_italic if run.bold and run.italic else fonts.bold if run.bold else fonts.italic if run.italic else ""
        if run.code and run.text.isascii():
            face = "Courier"
        if face:
            text = f'<font face="{face}">{text}</font>'
        if run.script:
            text = f"<super>{text}</super>" if run.script == "sup" else f"<sub>{text}</sub>"
        if run.href and _SAFE_LINK.match(run.href) and not run.href.startswith("#"):
            text = f'<a href="{escape(run.href)}" color="#0563C1">{text}</a>'
        out += text
    return out


def _book_text(book: Book) -> str:
    return book.title + book.author + "".join(_plain(block.html) for block in book.blocks)


def write_pdf(book: Book, output: Path, font: Path | None = None) -> None:
    """Write `book` as a PDF at `output`, set in `font` or a font of this system."""
    fonts = _choose_fonts(_book_text(book), book.language, font)

    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    unspaced = any(script in _UNSPACED_SCRIPTS for script in _scripts(book.language))
    wrap = "CJK" if unspaced else None
    body = ParagraphStyle("body", fontName=fonts.regular, fontSize=10.5, leading=16.5, spaceAfter=7, wordWrap=wrap)
    styles = {
        "p": body,
        "quote": ParagraphStyle("quote", parent=body, fontName=fonts.italic, leftIndent=18, rightIndent=18),
        # The mark is set in the book's font too: no other font is embedded.
        "li": ParagraphStyle("li", parent=body, leftIndent=18, bulletIndent=6, spaceAfter=3, bulletFontName=fonts.regular, bulletFontSize=10.5),
        "pre": ParagraphStyle("pre", parent=body, fontSize=9, leading=13, leftIndent=12),
        "hr": ParagraphStyle("hr", parent=body, alignment=TA_CENTER, spaceBefore=8, spaceAfter=14),
        "note": ParagraphStyle("note", parent=body, fontSize=8.5, leading=13, spaceAfter=3),
        "caption": ParagraphStyle("caption", parent=body, fontName=fonts.italic, alignment=TA_CENTER),
    }
    for level, size in enumerate((20, 16, 13.5, 12, 11, 10.5), start=1):
        styles[f"h{level}"] = ParagraphStyle(
            f"h{level}", parent=body, fontName=fonts.bold, fontSize=size, leading=size * 1.35,
            spaceBefore=size * 0.9, spaceAfter=size * 0.6, keepWithNext=True,
        )

    margin = 18 * mm
    document = SimpleDocTemplate(
        str(output), pagesize=A5, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin + 4 * mm,
        title=book.title or None, author=book.author or None, lang=book.language or None,
        # A fixed date and identifier: the same book gives the same file.
        invariant=1,
    )
    # What a picture may fill: the page inside its margins, less the frame's own
    # padding on each side and a point for rounding. One any larger fits no page.
    width, height = document.width - 2 * _FRAME_PADDING - 1, document.height - 2 * _FRAME_PADDING - 1
    story: list = []
    if book.title and not any(block.kind == "h1" for block in book.blocks):
        story.append(Paragraph(_xml(book.title), styles["h1"]))
    number = 0
    previous_note = ""
    for block in book.blocks:
        number = number + 1 if block.kind == "oli" else 0
        if block.kind == "h1" and story:
            story.append(PageBreak())  # a chapter starts a page
        if block.kind == "hr":
            story.append(Paragraph("* * *", styles["hr"]))
        elif block.kind == "img":
            story.extend(_picture(book, block, width, height, styles["caption"], Image, Paragraph, Spacer))
        elif block.kind == "note":
            # The first paragraph of a note begins with its number.
            lead = "" if previous_note == block.src else f"{_xml(block.src.removeprefix('note-'))}. "
            story.append(Paragraph(lead + _markup(block.html, fonts), styles["note"]))
        elif block.kind in {"li", "oli"}:
            bullet = f"{number}." if block.kind == "oli" else "-" if _lacking(fonts.regular, {"\u2022"}) else "\u2022"
            story.append(Paragraph(_markup(block.html, fonts), styles["li"], bulletText=bullet))
        elif block.kind == "pre":
            story.append(Paragraph(_markup(block.html, fonts, keep_spaces=True), styles["pre"]))
        else:
            style = styles.get(block.kind, body)
            text = _markup(block.html, fonts)
            if text:
                story.append(Paragraph(text, style))
        previous_note = block.src if block.kind == "note" else ""
    if not story:
        story.append(Spacer(1, 1))

    def page_number(canvas, document) -> None:
        canvas.setFont(fonts.regular, 8)
        canvas.drawCentredString(A5[0] / 2, 10 * mm, str(document.page))

    document.build(story, onFirstPage=page_number, onLaterPages=page_number)


# The space reportlab's page frame leaves inside each of its edges, in points.
_FRAME_PADDING = 6


def _picture(book: Book, block: Block, width: float, height: float, caption, Image, Paragraph, Spacer) -> list:
    """A picture as wide as it is at 96 pixels to the inch, or the page's text;
    one that cannot be shown (an SVG) is stood in for by its description."""
    from reportlab.lib.utils import ImageReader

    data = book.images.get(block.src, b"")
    size = picture_size(data)
    described = _plain(block.html).strip()
    if size is not None:
        wide, high = (max(1, value) * 0.75 for value in size)  # pixels to points
        scale = min(1.0, width / wide, height / high)
        try:
            # Read in full now: a picture the imaging library cannot read is described instead.
            ImageReader(io.BytesIO(data)).getRGBData()
            return [Image(io.BytesIO(data), width=wide * scale, height=high * scale, hAlign="CENTER"), Spacer(1, 7)]
        except Exception:  # noqa: BLE001
            pass
    return [Paragraph(f"[{_xml(described)}]", caption)] if described else []
