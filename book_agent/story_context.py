"""Story context for translation (docs/BOOK_CONSISTENCY.md, phase 3).

A chunk is translated without the rest of the book, so it does not know who
was introduced chapters ago or what a callback refers to. One short,
source-side summary per chapter fixes that cheaply: each translation chunk
gets a bounded "story so far" (the previous few chapters and its own), marked
as context only. The source wording and the scene still decide everything.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

STORY_PROMPT_VERSION = "1"


class ChapterSummary(BaseModel):
    """What a translator needs to know about one chapter, from the source only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    characters: list[str] = Field(default_factory=list, max_length=12)
    # Room for consistency.story_context.max_summary_words at its maximum (300 words).
    events: str = Field(default="", max_length=3000)
    open_threads: list[str] = Field(default_factory=list, max_length=5)


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    order: int
    title: str = ""
    summary: ChapterSummary


def build_summary_prompt(text: str, *, max_words: int, truncated: bool) -> str:
    note = (
        " The chapter is long and only its beginning is shown; summarize what is shown."
        if truncated
        else ""
    )
    return (
        "You prepare context for a literary translator. Summarize this book chapter in the "
        "language it is written in, from the text only; do not add outside knowledge. "
        "characters: who appears or is referred to, by name as written (at most 12). "
        f"events: what happens, in at most {max_words} words, in story order. open_threads: "
        "at most five things left unresolved that later chapters may refer back to. Do not "
        f"quote long passages.{note}\n\n{text}"
    )


def _describe(summary: ChapterSummary) -> str:
    parts = [summary.events]
    if summary.characters:
        parts.append("Characters: " + ", ".join(summary.characters) + ".")
    if summary.open_threads:
        parts.append("Open threads: " + "; ".join(summary.open_threads) + ".")
    return " ".join(part for part in parts if part)


def format_story_context(
    previous: Sequence[DocumentSummary], current: DocumentSummary | None
) -> str:
    """The prompt block for one document, or "" when there is nothing to say."""
    lines = []
    for item in previous:
        label = item.title or item.document_id
        lines.append(f"- Earlier, {label}: {_describe(item.summary)}")
    if current is not None:
        label = current.title or current.document_id
        lines.append(f"- This chapter, {label}: {_describe(current.summary)}")
    if not lines:
        return ""
    return (
        "Story so far (context only: do not translate, quote, or summarize it; the source "
        "wording and the scene decide every choice):\n" + "\n".join(lines)
    )


def story_context_by_document(
    summaries: Sequence[DocumentSummary], *, chapters_before: int
) -> dict[str, str]:
    """document id -> its "story so far" block, from summaries in reading order."""
    ordered = sorted(summaries, key=lambda item: item.order)
    blocks = {}
    for index, item in enumerate(ordered):
        previous = ordered[max(0, index - chapters_before): index] if chapters_before else []
        blocks[item.document_id] = format_story_context(previous, item)
    return blocks
