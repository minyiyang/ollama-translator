"""Build the benchmark excerpts in lang_benchmark/books from the books listed in BOOKS.md.

    python lang_benchmark/make_books.py                 # every excerpt that is missing
    python lang_benchmark/make_books.py --force alice-ch1-4.epub verwandlung-1.epub

The excerpts are not in the repository. Download each source (BOOKS.md has the links) into
`sample/`, which git ignores, then run this from the repository root. A Project Gutenberg EPUB
is found by its ebook number in the file name ("...pg22367-images-3.epub", the name the site
gives the "EPUB3 (E-readers incl. Send-to-Kindle)" download); 阿Q正传 is fetched from Chinese
Wikisource; the Korean story is the plain-text file from 공유마당 (gongu.copyright.or.kr).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from create_epub_section_subset import create_section_subset  # noqa: E402
from create_epub_spine_subset import create_spine_subset  # noqa: E402
from fetch_wikisource import page_text  # noqa: E402
from text_to_epub import build_epub  # noqa: E402

SAMPLE = ROOT / "sample"
BOOKS = ROOT / "lang_benchmark" / "books"


def gutenberg(number: int, fallback: str = "") -> Path:
    """The downloaded EPUB of a Project Gutenberg ebook in sample/."""
    found = sorted(SAMPLE.glob(f"*pg{number}-*.epub")) or ([SAMPLE / fallback] if fallback else [])
    if not found or not found[0].is_file():
        raise FileNotFoundError(
            f"Project Gutenberg ebook {number} is not in sample/: download the EPUB3 file from "
            f"https://www.gutenberg.org/ebooks/{number}"
        )
    return found[0]


def chapters(number: int, idrefs: list[str], fallback: str = ""):
    """Whole spine documents: one chapter a file in these books."""
    return lambda output: create_spine_subset(gutenberg(number, fallback), output, idrefs)


def section(number: int, start: str, end: str | None):
    """Part of the one spine document the book's text is in, between two headings."""
    return lambda output: create_section_subset(gutenberg(number), output, "pg-header", start, end)


def whole(number: int):
    return lambda output: shutil.copyfile(gutenberg(number), output)


def ah_q(output: Path) -> None:
    """阿Q正传, chapters 1 to 5, from Chinese Wikisource in Simplified Chinese."""
    paragraphs = [line.replace(chr(0x200B), "").strip() for line in page_text("阿Q正傳")]
    paragraphs = [line for line in paragraphs if line]
    first = next(index for index, line in enumerate(paragraphs) if line.startswith("第一章"))
    last = next(index for index, line in enumerate(paragraphs) if line.startswith("第六章"))
    text = "\n\n".join(paragraphs[first:last]) + "\n"
    output.with_suffix(".txt").write_text(text, encoding="utf-8", newline="\n")
    build_epub(text, output, title="阿Q正传", author="鲁迅", language="zh", join="", skip_lines=0)


def unsu_joeun_nal(output: Path) -> None:
    """운수 좋은 날 from the plain-text file: hard-wrapped inside words, hence the empty join."""
    found = sorted(SAMPLE.glob("*운수_좋은_날*.txt"))
    if not found:
        raise FileNotFoundError(
            "the text of 운수 좋은 날 is not in sample/: download the .txt file from "
            "https://gongu.copyright.or.kr/gongu/wrt/wrt/view.do?wrtSn=9002094&menuNo=200019"
        )
    text = found[0].read_text(encoding="utf-8-sig")
    build_epub(text, output, title="운수 좋은 날", author="현진건", language="ko", join="", skip_lines=4)


ALICE = "Alice's Adventures in Wonderland by Lewis Carroll.epub"
EXCERPTS = {
    "alice-ch1-4.epub": chapters(11, ["item4", "item5", "item6", "item7"], ALICE),
    "alice-3ch.epub": chapters(11, ["item5", "item11", "item12"], ALICE),  # chapters II, VIII, IX
    "sign-ch1-6.epub": chapters(2097, [f"item{index}" for index in range(4, 10)]),
    "wind-in-the-willows.epub": whole(289),
    "verwandlung-1.epub": section(22367, r"^I\.$", r"^II\.$"),
    "meteore-ch1-3.epub": chapters(76724, ["item7", "item8", "item9"]),
    "fortunata-1-2.epub": section(17013, r"^-I-$", r"^-III-$"),
    "rashomon.epub": chapters(1982, ["pg-header"]),  # the story and its header, without the licence pages
    "unsu-joeun-nal.epub": unsu_joeun_nal,
    "ah-q-ch1-5.epub": ah_q,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="excerpts to build (default: all)")
    parser.add_argument("--force", action="store_true", help="rebuild excerpts that exist")
    parser.add_argument("--out", type=Path, default=BOOKS, help="where to write (default: lang_benchmark/books)")
    args = parser.parse_args(argv)
    unknown = [name for name in args.names if name not in EXCERPTS]
    if unknown:
        parser.error(f"unknown excerpt {', '.join(unknown)}; known: {', '.join(EXCERPTS)}")
    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in args.names or EXCERPTS:
        output = args.out / name
        if output.exists() and not args.force:
            print(f"exists   {name}")
            continue
        try:
            EXCERPTS[name](output)
            print(f"built    {name}")
        except Exception as error:  # noqa: BLE001 - one missing source must not stop the others
            failed += 1
            print(f"missing  {name}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
