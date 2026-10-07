"""Write one chapter of a book in every format a job can be made from, to try each with a model.

    python lang_benchmark/make_format_samples.py

From the Alice EPUB in sample/ (lang_benchmark/BOOKS.md) this writes, into
lang_benchmark/books/, "A Mad Tea-Party" as

    alice-tea-party.txt  .md  .html  .docx      the chapter as a book
    alice-tea-party.srt  .vtt  .ass             the chapter as subtitles

The seven files hold the same text, so their runs can be compared.
"""

from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from book_agent.book_formats import EXPORT_FORMATS, Book, export_book, read_epub, write_source_package  # noqa: E402
from book_agent.subtitles import parse_subtitles  # noqa: E402
from make_subtitles import BOOKS, SAMPLE, chapter, script, subtitles  # noqa: E402

HEADING = r"CHAPTER VII\."


def as_ass(srt: str) -> str:
    """A SubRip file's cues as an Advanced SubStation file: italics as an override, a line break as \\N."""
    def clock(milliseconds: int) -> str:
        return f"{milliseconds // 3_600_000}:{milliseconds // 60_000 % 60:02d}:{milliseconds // 1000 % 60:02d}.{milliseconds % 1000 // 10:02d}"

    lines = [
        "[Script Info]", "Title: A Mad Tea-Party", "ScriptType: v4.00+", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,48,&H00FFFFFF,0,0,2,20,20,40,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in parse_subtitles(srt, "srt").cues:
        text = cue.raw.replace("<i>", "{\\i1}").replace("</i>", "{\\i0}").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{clock(cue.start)},{clock(cue.end)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def main() -> int:
    book = next(iter(sorted(SAMPLE.glob("Alice*Wonderland*.epub"))), None)
    if book is None:
        raise SystemExit("the book is not in sample/: see lang_benchmark/BOOKS.md for where to download it")
    BOOKS.mkdir(parents=True, exist_ok=True)
    whole = read_epub(book)
    start = next(i for i, block in enumerate(whole.blocks) if block.kind.startswith("h") and re.search(HEADING, block.html))
    end = next((i for i in range(start + 1, len(whole.blocks)) if whole.blocks[i].kind == whole.blocks[start].kind), len(whole.blocks))
    excerpt = Book(title="A Mad Tea-Party", author=whole.author, language=whole.language, blocks=whole.blocks[start:end])
    with tempfile.TemporaryDirectory() as directory:
        # The writers start from an EPUB, as they do for a finished job.
        package = Path(directory) / "package"
        write_source_package(excerpt, package, "alice-tea-party")
        epub = Path(directory) / "alice-tea-party.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package).as_posix())
        for export_format in EXPORT_FORMATS:
            print("wrote", export_book(epub, BOOKS / f"alice-tea-party.{export_format}", export_format))
    lines = script(chapter(book, HEADING))
    srt = subtitles(lines, "srt")
    for name, text in (("srt", srt), ("vtt", subtitles(lines, "vtt")), ("ass", as_ass(srt))):
        (BOOKS / f"alice-tea-party.{name}").write_bytes(text.encode("utf-8"))
        print("wrote", BOOKS / f"alice-tea-party.{name}", f"({text.count('-->') or text.count('Dialogue:')} cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
