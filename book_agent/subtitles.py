"""Subtitle files: SubRip (.srt), WebVTT (.vtt), and Advanced SubStation (.ass, .ssa).

A subtitle job is a kind of its own. Its unit is the cue, a line or two shown
for a few seconds, and what it gives back is the same file with the same cues
at the same times, each with its text translated. Nothing else in the file is
touched: a cue's text is the only thing replaced.

Reading: the file is kept as the pieces it is made of, text to copy and cues.
A cue's text is taken without its markup. Markup around the whole cue (an
italic pair, a position code) is put back around the translation; markup
inside it is dropped. Two lines that each open with a dash are two speakers,
and are translated and written as two lines; any other cue is one passage,
and its translation is broken into lines again to fit the screen.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .book_formats import SOURCE_SUFFIXES, BookFormatError, decode_text
from .epub import ChapterDocument, EpubMetadata, EpubPackageManifest, TextSegment
from .hashing import sha256_bytes, sha256_file
from .languages import SCRIPTS, profile

SUBTITLE_SUFFIXES = {".srt": "srt", ".vtt": "vtt", ".ass": "ass", ".ssa": "ass"}
SUBTITLE_MEDIA_TYPES = {
    ".srt": "application/x-subrip; charset=utf-8",
    ".vtt": "text/vtt; charset=utf-8",
    ".ass": "text/x-ssa; charset=utf-8",
    ".ssa": "text/x-ssa; charset=utf-8",
}
_MAX_SUBTITLE_BYTES = 32 * 1024 * 1024

# A new part of the film begins after this long without a cue, once the part
# has this many cues; no part is longer than the last number. Parts are what
# the dashboard lists where a book has chapters.
_PART_GAP_MS = 4_000
_PART_MIN_CUES = 40
_PART_MAX_CUES = 150


# Everything a job can be made from, and the two kinds of job.
JOB_SOURCE_SUFFIXES = SOURCE_SUFFIXES | set(SUBTITLE_SUFFIXES)
JOB_SOURCE_NAMES = "a book (EPUB, RTF, text, Markdown, HTML, Word .docx, PDF) or a subtitle file (.srt, .vtt, .ass)"


def job_type(source: str | Path) -> str:
    """The kind of job a source file makes: "subtitles" or "book"."""
    return "subtitles" if Path(source).suffix.casefold() in SUBTITLE_SUFFIXES else "book"


def book_only_settings(source: str | Path, config) -> list[str]:
    """The settings switched on in `config` that are written for a book's
    prose and chapters and do nothing useful to subtitle cues; none for a book."""
    if job_type(source) != "subtitles":
        return []
    return [
        name
        for name, enabled in (
            ("consistency.story_context.enabled", config.consistency.story_context.enabled),
            ("consistency.style_sheet.enabled", config.consistency.style_sheet.enabled),
            ("reprose.enabled", config.reprose.enabled),
        )
        if enabled
    ]


def book_only_problem(source: str | Path, config) -> str:
    """What to tell someone who starts a subtitle job with book-only settings on, or ""."""
    names = book_only_settings(source, config)
    if not names:
        return ""
    return (
        f"{', '.join(names)} {'is' if len(names) == 1 else 'are'} for a book's chapters and prose; "
        "set to false for a subtitle job"
    )


class SubtitleError(BookFormatError):
    """The file is not a subtitle file this module can read."""


@dataclass
class Cue:
    """One cue. `lines` is its visible text, a line each, without markup;
    `prefix` and `suffix` are the markup that stood around all of it; `raw`
    is its text as the file has it, for a cue that is written back unchanged."""

    number: int
    start: int  # milliseconds
    end: int
    lines: list[str]
    prefix: str = ""
    suffix: str = ""
    raw: str = ""

    @property
    def speakers(self) -> bool:
        """Whether each line is another speaker's: two or more, each opening with a dash."""
        return len(self.lines) > 1 and all(_DASH.match(line) for line in self.lines)

    @property
    def translatable(self) -> bool:
        return any(character.isalpha() for line in self.lines for character in line)

    @property
    def duration(self) -> float:
        return max(0, self.end - self.start) / 1000


@dataclass
class SubtitleFile:
    kind: str  # srt, vtt, ass
    # The file in order: text copied as it is, and cues where their text stood.
    parts: list[str | Cue] = field(default_factory=list)
    newline: str = "\n"

    @property
    def cues(self) -> list[Cue]:
        return [part for part in self.parts if isinstance(part, Cue)]


_DASH = re.compile(r"\s*([-\u2010-\u2015])\s*(?=\S)")
_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_TIMING = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_TAG = r"<[^<>]*>|\{[^{}]*\}"
_LEADING = re.compile(rf"^(?:\s*(?:{_TAG}))+")
_TRAILING = re.compile(rf"(?:(?:{_TAG})\s*)+$")
_ANY_TAG = re.compile(_TAG)
_ASS_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[.:](\d{1,3})")


def _milliseconds(hours, minutes, seconds, fraction) -> int:
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(fraction.ljust(3, "0")[:3])


def format_time(milliseconds: int) -> str:
    """A cue's time as a reader would say it: 0:12:03."""
    seconds = milliseconds // 1000
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _cue(number: int, start: int, end: int, raw: str, kind: str) -> Cue:
    """A cue from its text as the file has it."""
    written = raw
    if kind == "ass":
        raw = raw.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    leading = _LEADING.match(raw)
    prefix = leading.group(0) if leading else ""
    rest = raw[len(prefix) :]
    trailing = _TRAILING.search(rest)
    suffix = trailing.group(0) if trailing else ""
    body = rest[: len(rest) - len(suffix)] if suffix else rest
    body = _ANY_TAG.sub("", body)
    if kind == "vtt":
        body = html.unescape(body)
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.split("\n")]
    return Cue(number, start, end, [line for line in lines if line], prefix.strip(), suffix.strip(), written)


def parse_subtitles(text: str, kind: str) -> SubtitleFile:
    """Read a subtitle file's text. `kind` is srt, vtt, or ass."""
    result = SubtitleFile(kind=kind, newline="\r\n" if "\r\n" in text else "\n")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    literal: list[str] = []

    def flush() -> None:
        if literal:
            result.parts.append("\n".join(literal) + "\n")
            literal.clear()

    number = 0
    index = 0
    if kind == "ass":
        # Text is the last field of a Dialogue line; the Format line says how many come before it.
        fields = 10
        for line in lines:
            if line.lower().startswith("format:") and "text" in line.lower() and "start" in line.lower():
                fields = len(line.split(":", 1)[1].split(","))
            if line.startswith("Dialogue:"):
                head, _, body = line.partition(":")
                values = body.split(",", fields - 1)
                times = [_ASS_TIME.search(value) for value in values[1:3]] if len(values) == fields else []
                if len(times) == 2 and all(times):
                    number += 1
                    literal.append(f"{head}:{','.join(values[:-1])},")
                    # The cue's text stands on the same line as what comes before it.
                    result.parts.append("\n".join(literal))
                    literal.clear()
                    start, end = (_milliseconds(*time.groups()) for time in times)
                    result.parts.append(_cue(number, start, end, values[-1], kind))
                    literal.append("")
                    continue
            literal.append(line)
        if literal and literal != [""]:
            result.parts.append("\n".join(literal))
        return result
    while index < len(lines):
        line = lines[index]
        index += 1
        timing = _TIMING.match(line)
        if not timing:
            literal.append(line)
            continue
        literal.append(line)
        flush()
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1
        number += 1
        groups = timing.groups()
        result.parts.append(_cue(number, _milliseconds(*groups[:4]), _milliseconds(*groups[4:]), "\n".join(text_lines), kind))
        literal.append("")  # the line break after the cue's text
    if literal and literal != [""]:
        result.parts.append("\n".join(literal))
    if not result.cues:
        raise SubtitleError("the file has no subtitle cues")
    return result


def read_subtitles(path: str | Path) -> SubtitleFile:
    source = Path(path)
    kind = SUBTITLE_SUFFIXES.get(source.suffix.casefold())
    if kind is None:
        raise SubtitleError(f"{source.name} is not a subtitle file (.srt, .vtt, .ass)")
    if source.stat().st_size > _MAX_SUBTITLE_BYTES:
        raise SubtitleError(f"{source.name} exceeds the {_MAX_SUBTITLE_BYTES} byte limit")
    parsed = parse_subtitles(decode_text(source.read_bytes()), kind)
    if not any(cue.translatable for cue in parsed.cues):
        raise SubtitleError(f"{source.name} has no text to translate")
    return parsed


# -- what a subtitle may be ----------------------------------------------------------------------


@dataclass(frozen=True)
class ReadingLimits:
    """What fits on the screen and can be read in the time: characters a
    line, lines a cue, characters a second."""

    line_characters: int
    lines: int
    characters_per_second: float


def reading_limits(target_language: str, line_characters=None, lines=None, characters_per_second=None) -> ReadingLimits:
    """The limits for a language, as the streaming services publish them, with
    any the job's config sets in their place. A script written without
    spaces says more in a character and is given fewer of them."""
    rules = profile(target_language)
    if not rules.spaced_words:
        defaults = (16, 2, 9.0)
    elif "Hangul" in rules.scripts:
        defaults = (16, 2, 12.0)
    else:
        defaults = (42, 2, 20.0)
    return ReadingLimits(
        int(line_characters or defaults[0]), int(lines or defaults[1]), float(characters_per_second or defaults[2])
    )


def subtitle_limits(config) -> ReadingLimits:
    """The limits of a job: its target language's, with what its config sets."""
    return reading_limits(
        config.translation.direction.target_language,
        config.subtitles.line_characters,
        config.subtitles.lines,
        config.subtitles.characters_per_second,
    )


_UNSPACED = re.compile(
    "[" + "".join(SCRIPTS[name] for name in ("Han", "Hiragana", "Katakana", "Thai", "Lao", "Khmer", "Myanmar", "Tibetan")) + "\u3000-\u303f\uff00-\uffef]"
)
_BREAK_AFTER = "，。！？、；：,.;:!?…—"
_NO_LINE_START = "，。！？、；：,.;:!?…）」』】》〉”’)]}"


def join_lines(lines: list[str]) -> str:
    """The lines of one cue as one passage."""
    text = lines[0] if lines else ""
    for line in lines[1:]:
        text += ("" if _UNSPACED.match(text[-1:]) and _UNSPACED.match(line[:1]) else " ") + line
    return text


def _break(rest: str, target: int, least: int, reach: int) -> int:
    """Where to end a line of `rest`: the place a line may end that is nearest
    `target`, no earlier than `least` and no later than `reach`; 0 if there is none."""
    best, best_cost = 0, None
    for position in range(max(1, least), reach + 1):
        before, after = rest[position - 1], rest[position]
        if after in _NO_LINE_START and after != " ":
            continue
        if before == " " or after == " ":
            cost = abs(position - target)
        elif _UNSPACED.match(before) or _UNSPACED.match(after) or before in _BREAK_AFTER:
            # Between two characters of a script without spaces; better after punctuation.
            cost = abs(position - target) + (0 if before in _BREAK_AFTER else 2)
        else:
            continue
        if before in _BREAK_AFTER:
            cost -= 4
        if best_cost is None or cost < best_cost:
            best, best_cost = position, cost
    return best


def wrap_cue(text: str, limits: ReadingLimits) -> list[str]:
    """`text` as the lines of a cue: as few as hold it, of about the same
    length, broken at a space or after punctuation and never inside a word
    that fits a line. A text too long for the limits still gets lines no
    longer than the limit, and more of them."""
    rest = re.sub(r"\s+", " ", text).strip()
    width = limits.line_characters
    lines: list[str] = []
    while len(rest) > width:
        remaining = math.ceil(len(rest) / width)
        target = math.ceil(len(rest) / remaining)
        reach = min(width, len(rest) - 1)
        # First where the lines still to come can hold the rest; failing that,
        # anywhere a line may end, and the rest takes a line more.
        best = _break(rest, target, len(rest) - (remaining - 1) * width, reach) or _break(rest, target, 1, reach)
        if not best:  # one unbroken word longer than a line
            best = reach
        lines.append(rest[:best].strip())
        rest = rest[best:].strip()
    lines.append(rest)
    return [line for line in lines if line]


# -- into the pipeline ---------------------------------------------------------------------------


def cue_passages(cue: Cue) -> list[str]:
    """What is translated of a cue: each speaker's line without its dash, or the one passage it is."""
    if not cue.translatable:
        return []
    if cue.speakers:
        return [_DASH.sub("", line, count=1) for line in cue.lines]
    return [join_lines(cue.lines)]


def _parts(cues: list[Cue]) -> list[list[Cue]]:
    parts: list[list[Cue]] = [[]]
    previous_end = None
    for cue in cues:
        gap = cue.start - previous_end if previous_end is not None else 0
        if (len(parts[-1]) >= _PART_MIN_CUES and gap >= _PART_GAP_MS) or len(parts[-1]) >= _PART_MAX_CUES:
            parts.append([])
        parts[-1].append(cue)
        previous_end = cue.end
    return [part for part in parts if part]


def inspect_subtitle_file(path: str | Path, source_sha256: str) -> EpubPackageManifest:
    """A subtitle file as the pipeline's document inventory: a document for
    each part of the film, a segment for each passage of a cue."""
    source = Path(path)
    parsed = read_subtitles(source)
    documents: list[ChapterDocument] = []
    for part in _parts([cue for cue in parsed.cues if cue.translatable]):
        order = len(documents)
        segments: list[TextSegment] = []
        for cue in part:
            passages = cue_passages(cue)
            for line, passage in enumerate(passages, start=1):
                segments.append(
                    TextSegment(
                        segment_id=f"D{order:04d}-S{len(segments) + 1:06d}",
                        element_path=f"cue[{cue.number}]" + (f"/line[{line}]" if len(passages) > 1 else ""),
                        tag="p",
                        text=passage,
                        protected_text=passage,
                    )
                )
        documents.append(
            ChapterDocument(
                order=order,
                manifest_id=f"part-{order + 1:04d}",
                archive_path=f"subtitles/part-{order + 1:04d}.txt",
                media_type="text/plain",
                linear=True,
                title=f"{format_time(part[0].start)} \u2013 {format_time(part[-1].end)}",
                source_sha256=sha256_bytes("\n".join(segment.text for segment in segments).encode("utf-8")),
                segments=segments,
            )
        )
    return EpubPackageManifest(
        source_format="subtitle",
        source_sha256=source_sha256,
        opf_path="",
        package_version="1",
        unique_identifier=source_sha256,
        metadata=EpubMetadata(values={"format": [parsed.kind], "title": [source.stem]}),
        manifest_items=[],
        spine=[],
        navigation=[],
        documents=documents,
        resources=[],
    )


def cue_views(source_file: str | Path, documents, texts: dict[str, str]) -> tuple[dict | None, dict[str, dict]]:
    """For the dashboard: a subtitle job's reading limits, and for each segment
    its cue: the cue's number, when it starts, its seconds on screen, whether
    it is one of two speakers' lines, and how many characters the cue's other
    passages have in `texts` (a cue is read as a whole). `documents` are the
    job's preprocessed documents; a book has neither limits nor cues."""
    documents = [document for document in documents if document.cues]
    if not documents:
        return None, {}
    starts = {cue.number: cue.start for cue in read_subtitles(source_file).cues}
    views: dict[str, dict] = {}
    for document in documents:
        by_cue: dict[int, list[str]] = {}
        for segment_id, (number, _, _) in document.cues.items():
            by_cue.setdefault(number, []).append(segment_id)
        for segment_id, (number, seconds, speakers) in document.cues.items():
            others = [texts.get(other, "") for other in by_cue[number] if other != segment_id]
            views[segment_id] = {
                "number": number,
                "start": format_time(starts.get(number, 0)),
                "seconds": round(seconds, 1),
                "speakers": speakers,
                "other_characters": sum(len(_squeezed(text)) for text in others),
            }
    line_characters, lines, characters_per_second = documents[0].reading_limits
    limits = {"line_characters": line_characters, "lines": lines, "characters_per_second": characters_per_second}
    return limits, views


def cue_of(element_path: str) -> int:
    """The number of the cue a segment came from."""
    match = re.match(r"cue\[(\d+)\]", element_path)
    return int(match.group(1)) if match else 0


def _render(cue: Cue, translations: list[str], kind: str, limits: ReadingLimits) -> str:
    """A cue's text for the file, from the translation of each of its passages."""
    if cue.speakers and len(translations) == len(cue.lines):
        dashes = [_DASH.match(line).group(0) for line in cue.lines]
        lines = [dash + _DASH.sub("", text.strip(), count=1) for dash, text in zip(dashes, translations)]
    else:
        lines = wrap_cue(" ".join(translations), limits)
    if kind == "vtt":
        lines = [html.escape(line, quote=False) for line in lines]
    return cue.prefix + ("\\N" if kind == "ass" else "\n").join(lines) + cue.suffix


def render_subtitles(parsed: SubtitleFile, translations: dict[int, list[str]], limits: ReadingLimits) -> str:
    """The file again, each cue in `translations` (by its number) with its
    text replaced, and every other piece as it was."""
    pieces: list[str] = []
    for part in parsed.parts:
        if isinstance(part, str):
            pieces.append(part)
        elif part.number in translations:
            pieces.append(_render(part, translations[part.number], parsed.kind, limits))
        else:
            pieces.append(part.raw)
    text = "".join(pieces)
    return text.replace("\n", parsed.newline) if parsed.newline != "\n" else text


def _translations(manifest: EpubPackageManifest, repaired_documents) -> dict[int, list[str]]:
    """Each cue's translated passages, by cue number, from the pipeline's documents."""
    from .epub_compile import EpubCompilationError  # imports the audit, which imports this module

    expected = [item.manifest_id for item in manifest.documents]
    actual = [item.document.manifest_id for item in repaired_documents]
    if actual != expected:
        raise EpubCompilationError(f"repaired document order or set differs: expected {expected}, received {actual}")
    by_cue: dict[int, list[str]] = {}
    for source, repaired in zip(manifest.documents, repaired_documents, strict=True):
        translated = {segment.segment_id: segment.translated_text for segment in repaired.document.segments}
        for segment in source.segments:
            if segment.segment_id not in translated:
                raise EpubCompilationError(f"no translation for {segment.segment_id}")
            by_cue.setdefault(cue_of(segment.element_path), []).append(translated[segment.segment_id])
    return by_cue


def compile_subtitle_file(
    source_path: str | Path,
    manifest: EpubPackageManifest,
    repaired_documents,
    output_path: str | Path,
    limits: ReadingLimits,
) -> EpubCompilationReport:
    """Write the translated subtitle file: the source's cues and times, the pipeline's text."""
    from .epub_compile import EpubCompilationError, EpubCompilationReport

    if manifest.source_format != "subtitle":
        raise EpubCompilationError("the subtitle compiler received another kind of manifest")
    parsed = read_subtitles(source_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(render_subtitles(parsed, _translations(manifest, repaired_documents), limits).encode("utf-8"))
    temporary.replace(output)
    return EpubCompilationReport(
        output_path=str(output.resolve()),
        document_count=len(repaired_documents),
        segment_count=sum(len(item.document.segments) for item in repaired_documents),
        resource_count=0,
        output_sha256=sha256_file(output),
        output_size=output.stat().st_size,
    )


def _squeezed(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate_compiled_subtitles(
    output_path: str | Path,
    source_path: str | Path,
    manifest: EpubPackageManifest,
    repaired_documents,
) -> EpubValidationReport:
    """Read the written file back: the same cues at the same times as the
    source, and in each the text the pipeline accepted."""
    from .epub_compile import EpubValidationReport

    errors: list[str] = []
    cues: list[Cue] = []
    try:
        source = read_subtitles(source_path)
        written = parse_subtitles(decode_text(Path(output_path).read_bytes()), source.kind)
        cues = written.cues
        if len(cues) != len(source.cues):
            errors.append(f"the file has {len(cues)} cues; the source has {len(source.cues)}")
        else:
            expected = _translations(manifest, repaired_documents)
            for before, after in zip(source.cues, cues, strict=True):
                if (before.start, before.end) != (after.start, after.end):
                    errors.append(f"cue {before.number}: its time changed")
                if (before.prefix, before.suffix) != (after.prefix, after.suffix):
                    errors.append(f"cue {before.number}: its markup changed")
                wanted = expected.get(before.number)
                text = _squeezed("".join(wanted)) if wanted is not None else _squeezed("".join(before.lines))
                if wanted is not None and before.speakers:
                    text = _squeezed("".join(_DASH.sub("", item, count=1) for item in wanted))
                    found = _squeezed("".join(_DASH.sub("", line, count=1) for line in after.lines))
                else:
                    found = _squeezed("".join(after.lines))
                if found != text:
                    errors.append(f"cue {before.number}: its text differs from the accepted translation")
    except (OSError, ValueError) as error:
        errors.append(f"the subtitle file could not be read back: {error}")
    return EpubValidationReport(
        passed=not errors,
        errors=errors[:50],
        document_count=len(manifest.documents),
        segment_count=sum(1 for cue in cues if cue.translatable),
        resource_count=0,
    )


def reading_problems(cue: Cue, translations: list[str], limits: ReadingLimits) -> list[str]:
    """What a viewer could not read of a translated cue: too much for its
    time on screen, or more lines than fit."""
    text = " ".join(translations)
    lines = [text] if cue.speakers else wrap_cue(text, limits)
    if cue.speakers:
        lines = list(translations)
    problems: list[str] = []
    count = sum(len(_squeezed(line)) for line in lines)
    if cue.duration > 0 and count / cue.duration > limits.characters_per_second:
        fits = int(limits.characters_per_second * cue.duration)
        problems.append(
            f"too long to read in the time: {count} characters in {cue.duration:.1f} s; about {fits} can be read"
        )
    if len(lines) > limits.lines or any(len(line) > limits.line_characters for line in lines):
        problems.append(
            f"does not fit the screen: {len(lines)} line(s), the longest of {max(len(line) for line in lines)} characters; "
            f"the limit is {limits.lines} of {limits.line_characters}"
        )
    return problems
