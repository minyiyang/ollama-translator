"""The format a job's result is written in (docs/OUTPUT_AND_CONFIG_UX.md).

A book is always compiled as an EPUB, which is what is checked and what every
other format is made from. `output.format` says which format the job gives
back: the one the book came in (`source`, the default), or a named one.

A subtitle job is compiled as a file of the kind it was given, the source's
cues with their text replaced, and that is what every other subtitle format
is made from. Its `output.format` is `source`, `srt`, `vtt`, or `ass`.
"""

from __future__ import annotations

from pathlib import Path

from .book_formats import CONVERTED_SUFFIXES, EXPORT_FORMATS
from .config import AppConfig
from .languages import profile
from .subtitles import SUBTITLE_FORMATS, job_type, subtitle_kind

# What a job gave back before it had an output format: its hashes are kept as they were.
_FORMATS_BEFORE = ("txt", "md", "html", "docx")


def _target_language(config: AppConfig) -> str:
    return profile(config.translation.direction.target_language).code


def source_format(source: str | Path) -> str:
    """The format `output.format: source` asks for: the source's own where it can be written, else EPUB."""
    kind = CONVERTED_SUFFIXES.get(Path(source).suffix.casefold(), "epub")
    return kind if kind in EXPORT_FORMATS else "epub"


def output_format(source: str | Path, config: AppConfig) -> tuple[str, str]:
    """The format a job on `source` gives back, and a note for the person
    running it when that is not what they might expect ("" otherwise).

    A PDF nobody asked for by name is not insisted on: a book that came as a
    PDF comes back as an EPUB where a PDF cannot be written. A subtitle file
    asked for in the format it came in is the source's own (an .ssa file
    stays .ssa)."""
    if job_type(source) == "subtitles":
        own = Path(source).suffix.casefold().lstrip(".")
        return (own if config.output.format in {"source", subtitle_kind(source)} else config.output.format), ""
    if config.output.format != "source":
        return config.output.format, ""
    wanted = source_format(source)
    if wanted == "pdf":
        from .pdf_export import pdf_output_problem

        problem = pdf_output_problem(_target_language(config), config.output.pdf_font)
        if problem:
            return "epub", f"the book is written as an EPUB, not a PDF: {problem}"
    return wanted, ""


def check_output(source: str | Path, config: AppConfig) -> str:
    """Refuse (ValueError) an output format this job cannot be given, before
    any work is done; otherwise the note of `output_format`."""
    if job_type(source) == "subtitles":
        if config.output.format not in {"source", *SUBTITLE_FORMATS}:
            raise ValueError(
                f"output.format is {config.output.format}, a book's format, and this is a subtitle job: "
                "its output is a subtitle file; set output.format to source, srt, vtt, or ass"
            )
        return ""
    if config.output.format in SUBTITLE_FORMATS:
        raise ValueError(
            f"output.format is {config.output.format}, a subtitle format, and this is a book: "
            "set output.format to source, epub, pdf, docx, html, md, or txt"
        )
    if config.output.format == "pdf":
        from .pdf_export import check_pdf_output

        check_pdf_output(_target_language(config), config.output.pdf_font)
    return output_format(source, config)[1]


def hashed_output_fields(source: str | Path, config: AppConfig) -> dict[str, str]:
    """What the compile stage hashes of the output format: nothing for a job
    that gives back what it always did, so that it is not compiled again."""
    chosen, _ = output_format(source, config)
    if job_type(source) == "subtitles":
        # A subtitle job always gave back a file of the kind it was given.
        return {} if chosen == Path(source).suffix.casefold().lstrip(".") else {"output_format": chosen}
    before = CONVERTED_SUFFIXES.get(Path(source).suffix.casefold(), "epub")
    fields: dict[str, str] = {}
    if chosen != (before if before in _FORMATS_BEFORE else "epub"):
        fields["output_format"] = chosen
    if chosen == "pdf" and config.output.pdf_font is not None:
        fields["pdf_font"] = str(config.output.pdf_font)
    return fields
