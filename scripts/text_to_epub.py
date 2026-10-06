"""Turn a plain-text book into a minimal EPUB 3 the pipeline can read.

The pipeline reads EPUB and RTF. Some public-domain texts come as hard-wrapped
plain text: an indented line starts a paragraph, other lines continue it.

    python scripts/text_to_epub.py story.txt story.epub --lang ko \
        --title "운수 좋은 날" --author 현진건 --skip-lines 4 --join ""

--join is what joins a wrapped line to the one before: " " for text wrapped
between words, "" for Korean or Chinese wrapped at a fixed width (the wrap
then usually falls inside a word).
"""

from __future__ import annotations

import argparse
import html
import uuid
import zipfile
from pathlib import Path


def paragraphs(lines: list[str], join: str) -> list[str]:
    """Indented lines start paragraphs; a blank line ends one."""
    result: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line.strip():
            if current:
                result.append(join.join(current))
                current = []
            continue
        if line[:1].isspace() and current:
            result.append(join.join(current))
            current = []
        current.append(line.strip())
    if current:
        result.append(join.join(current))
    return result


def build_epub(text: str, output: Path, *, title: str, author: str, language: str, join: str, skip_lines: int) -> Path:
    lines = text.replace("\r\n", "\n").split("\n")[skip_lines:]
    body = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs(lines, join))
    chapter = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        f"<html xmlns='http://www.w3.org/1999/xhtml' xml:lang='{language}' lang='{language}'>"
        f"<head><title>{html.escape(title)}</title></head><body>\n"
        f"<h1>{html.escape(title)}</h1>\n<p>{html.escape(author)}</p>\n{body}\n</body></html>\n"
    )
    opf = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        f"<dc:identifier id='id'>urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, title + author)}</dc:identifier>"
        f"<dc:title>{html.escape(title)}</dc:title><dc:creator>{html.escape(author)}</dc:creator>"
        f"<dc:language>{language}</dc:language>"
        "<meta property='dcterms:modified'>2026-01-01T00:00:00Z</meta></metadata>"
        "<manifest><item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
        "<item id='chapter' href='text/chapter.xhtml' media-type='application/xhtml+xml'/></manifest>"
        "<spine><itemref idref='chapter'/></spine></package>\n"
    )
    nav = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        f"<head><title>{html.escape(title)}</title></head><body><nav epub:type='toc'><ol>"
        f"<li><a href='text/chapter.xhtml'>{html.escape(title)}</a></li></ol></nav></body></html>\n"
    )
    container = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
        "<rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>"
        "</rootfiles></container>\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/text/chapter.xhtml", chapter, compress_type=zipfile.ZIP_DEFLATED)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--lang", required=True, help="BCP 47 code of the text, e.g. ko")
    parser.add_argument("--join", default=" ", help='joins a wrapped line to the previous one (default " ")')
    parser.add_argument("--skip-lines", type=int, default=0, help="leading lines to drop (a title block)")
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args(argv)
    text = args.source.read_text(encoding=args.encoding)
    print(build_epub(text, args.output.resolve(), title=args.title, author=args.author, language=args.lang,
                     join=args.join, skip_lines=args.skip_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
