"""Create an EPUB pilot holding one section of one spine document.

Some books keep many chapters in one XHTML file (Project Gutenberg's Fortunata y
Jacinta keeps Part One in one). This keeps a single spine document and, inside
its <body>, only the elements from the heading matching --start up to (not
including) the heading matching --end:

    python scripts/create_epub_section_subset.py book.epub pilot.epub \
        --idref pg-header --start "^-I-$" --end "^-III-$"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from xml.etree import ElementTree as etree

from create_epub_spine_subset import create_spine_subset

XHTML = "{http://www.w3.org/1999/xhtml}"
HEADINGS = {f"{XHTML}h{level}" for level in range(1, 7)}
# Written back without prefixes, as the reader of the EPUB expects XHTML.
etree.register_namespace("", "http://www.w3.org/1999/xhtml")
etree.register_namespace("epub", "http://www.idpf.org/2007/ops")


def _text(element) -> str:
    return " ".join("".join(element.itertext()).split())


def _cut(document: bytes, start: str, end: str | None) -> bytes:
    root = etree.fromstring(document)
    body = root.find(f"{XHTML}body")
    if body is None:
        raise ValueError("document has no <body>")
    # The section is cut among the body's children; a heading nested in a
    # wrapper <div> counts as that wrapper.
    children = list(body)

    def index_of(pattern: str, after: int) -> int | None:
        regex = re.compile(pattern)
        for index, child in enumerate(children[after:], start=after):
            headings = [child] if child.tag in HEADINGS else [e for e in child.iter() if e.tag in HEADINGS]
            if any(regex.search(_text(heading)) for heading in headings):
                return index
        return None

    first = index_of(start, 0)
    if first is None:
        raise ValueError(f"no heading matches --start {start!r}")
    last = index_of(end, first + 1) if end else None
    if end and last is None:
        raise ValueError(f"no heading after the start matches --end {end!r}")
    for child in children[:first] + (children[last:] if last is not None else []):
        body.remove(child)
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def create_section_subset(source: Path, output: Path, idref: str, start: str, end: str | None) -> Path:
    import zipfile

    create_spine_subset(source, output, [idref])
    with zipfile.ZipFile(output) as archive:
        container = etree.fromstring(archive.read("META-INF/container.xml"))
        opf_path = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
        package = etree.fromstring(archive.read(opf_path))
        item = next(
            element for element in package.iter("{http://www.idpf.org/2007/opf}item") if element.get("id") == idref
        )
        href = (Path(opf_path).parent / item.get("href")).as_posix()
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    info, data = entries[href]
    entries[href] = (info, _cut(data, start, end))
    temporary = output.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        for name, (info, data) in entries.items():
            archive.writestr(info, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    temporary.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--idref", required=True, help="the spine item to keep")
    parser.add_argument("--start", required=True, help="regex for the first heading kept")
    parser.add_argument("--end", help="regex for the first heading left out (default: to the end)")
    args = parser.parse_args(argv)
    print(create_section_subset(args.source.resolve(), args.output.resolve(), args.idref, args.start, args.end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
