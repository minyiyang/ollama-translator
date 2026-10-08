"""Make a subtitle file out of a chapter of a book, to try subtitle jobs on real text.

    python lang_benchmark/make_subtitles.py                      # Alice, "A Mad Tea-Party"
    python lang_benchmark/make_subtitles.py --heading "CHAPTER V\\." --out lang_benchmark/books/alice-caterpillar.srt
    python lang_benchmark/make_subtitles.py --format vtt

No film is involved: the chapter is cut up the way a subtitler would cut a
script. What a character says becomes a cue; what the narrator describes
becomes a cue in italics; "said Alice" and the like are left out; two short
lines by different speakers share a cue, a dash before each. Each cue stays
on screen for as long as it takes to read, and a new scene begins after a
pause wherever the book has a longer stretch of narration.

The book is the EPUB in sample/ (lang_benchmark/BOOKS.md says where it comes
from); the file is written to lang_benchmark/books/, which git ignores.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from book_agent.book_formats import _plain, read_epub  # noqa: E402
from book_agent.subtitles import ReadingLimits, wrap_cue  # noqa: E402

SAMPLE = ROOT / "sample"
BOOKS = ROOT / "lang_benchmark" / "books"
LIMITS = ReadingLimits(42, 2, 17.0)
_QUOTE = re.compile("“([^”]+)”")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[“‘A-Z])")
# Narration this short between two things said is who said it, and how.
_ATTRIBUTION_WORDS = 9
_ROOM = LIMITS.line_characters * LIMITS.lines


def _pieces(text: str, room: int = _ROOM) -> list[str]:
    """`text` as passages that each fit a cue: whole sentences where they fit, clauses where they do not."""
    result: list[str] = []
    for sentence in _SENTENCE.split(text.strip()):
        while len(sentence) > room:
            # At a comma or the like, where one stands late enough; else at a
            # space, and not so late that a word or two is left over.
            cut = max(sentence.rfind(mark, 0, room) for mark in (", ", "; ", ": ", "—"))
            cut = cut + 1 if cut > room // 3 else sentence.rfind(" ", 0, min(room, max(room // 2, len(sentence) - room // 3)))
            result.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if result and len(result[-1]) + len(sentence) + 1 <= LIMITS.line_characters:
            result[-1] += " " + sentence  # two short sentences read as one cue
        elif sentence:
            result.append(sentence)
    return result


_VERB = "said|cried|thought|asked|added|replied|remarked|repeated|continued|began|exclaimed|interrupted|shouted|whispered|went on"
_SAID = re.compile(
    rf"^(?:(?:{_VERB})\b|(?:[A-Z]\w+|the \w+(?: \w+)?|she|he|it|they) (?:{_VERB})\b)[^,;:.]*[,;:.]?\s*(?:and |but )?"
)


def _described(text: str) -> str:
    """Narration after something said, without the words that say who said it."""
    rest = _SAID.sub("", text, count=1)
    return rest[:1].upper() + rest[1:] if rest != text else text


def script(paragraphs: list[str]) -> list[tuple[str, str]]:
    """The chapter as a script: ("speech" or "narration" or "pause", text), in order."""
    lines: list[tuple[str, str]] = []
    for paragraph in paragraphs:
        position = 0
        spoken = False
        for quote in _QUOTE.finditer(paragraph):
            between = _described(paragraph[position : quote.start()].strip(" ,;:—"))
            if len(between.split()) > _ATTRIBUTION_WORDS:
                lines.extend(("narration", piece) for piece in _pieces(between))
            lines.extend(("speech", piece) for piece in _pieces(quote.group(1)))
            position = quote.end()
            spoken = True
        rest = paragraph[position:].strip(" ,;:—")
        rest = _described(rest) if spoken else rest
        if len(rest.split()) > _ATTRIBUTION_WORDS:
            if not spoken and len(rest) > 3 * _ROOM:
                lines.append(("pause", ""))  # a long description: the scene changes
            lines.extend(("narration", piece) for piece in _pieces(rest))
        lines.append(("turn", ""))  # the next paragraph is another speaker
    return lines


def _clock(milliseconds: int, kind: str) -> str:
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, fraction = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{',' if kind == 'srt' else '.'}{fraction:03d}"


def subtitles(lines: list[tuple[str, str]], kind: str = "srt") -> str:
    """The script as a subtitle file, timed by how long each cue takes to read."""
    cues: list[tuple[int, int, str]] = []
    clock = 2_000
    cues.append((clock, clock + 3_000, "{\\an8}♪ ♪"))  # the titles' music: a cue with nothing to translate
    clock += 4_000
    index = 0
    while index < len(lines):
        what, text = lines[index]
        index += 1
        if what == "turn":
            clock += 300
            continue
        if what == "pause":
            clock += 5_000
            continue
        body = "\n".join(wrap_cue(text, LIMITS))
        # Two short lines by two speakers, one after the other, share a cue.
        following = lines[index : index + 2]
        if (
            what == "speech"
            and len(text) <= 28
            and len(following) == 2
            and following[0][0] == "turn"
            and following[1][0] == "speech"
            and len(following[1][1]) <= 28
        ):
            body = f"- {text}\n- {following[1][1]}"
            text = text + following[1][1]
            index += 2
        if what == "narration":
            body = f"<i>{body}</i>"
        duration = int(min(7_000, max(1_200, 1000 * len(text) / LIMITS.characters_per_second + 400)))
        cues.append((clock, clock + duration, body))
        clock += duration + 150
    if kind == "vtt":
        return "WEBVTT\n\n" + "".join(
            f"{_clock(start, kind)} --> {_clock(end, kind)}\n{body.replace('&', '&amp;')}\n\n" for start, end, body in cues
        )
    return "".join(
        f"{number}\n{_clock(start, kind)} --> {_clock(end, kind)}\n{body}\n\n"
        for number, (start, end, body) in enumerate(cues, start=1)
    )


def chapter(book: Path, heading: str) -> list[str]:
    """The paragraphs of the chapter whose heading matches `heading` (a regular expression)."""
    blocks = read_epub(book).blocks
    starts = [index for index, block in enumerate(blocks) if block.kind.startswith("h") and re.search(heading, _plain(block.html))]
    if not starts:
        raise SystemExit(f"no heading matching {heading!r} in {book.name}")
    start = starts[0]
    level = blocks[start].kind
    end = next((index for index in range(start + 1, len(blocks)) if blocks[index].kind == level), len(blocks))
    return [_plain(block.html) for block in blocks[start + 1 : end] if block.kind == "p"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--book", help="an EPUB in sample/ (default: Alice's Adventures in Wonderland)")
    parser.add_argument("--heading", default=r"CHAPTER VII\.", help="regular expression matching the chapter's heading")
    parser.add_argument("--format", choices=("srt", "vtt"), default="srt")
    parser.add_argument("--out", help="output path (default: lang_benchmark/books/alice-tea-party.<format>)")
    args = parser.parse_args(argv)
    book = Path(args.book) if args.book else next(iter(sorted(SAMPLE.glob("Alice*Wonderland*.epub"))), None)
    if book is None or not book.is_file():
        raise SystemExit("the book is not in sample/: see lang_benchmark/BOOKS.md for where to download it")
    output = Path(args.out) if args.out else BOOKS / f"alice-tea-party.{args.format}"
    output.parent.mkdir(parents=True, exist_ok=True)
    text = subtitles(script(chapter(book, args.heading)), args.format)
    output.write_bytes(text.encode("utf-8"))
    print(f"wrote {output} ({text.count('-->')} cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
