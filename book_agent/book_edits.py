"""Edits to what the book says of itself (docs/FULL_TEXT_REVIEW.md).

The title stage settles the book's title, the contents entries that are no
passage, the paragraphs of its notes, and its pictures' descriptions
(``book_agent.stages.title``). This module lists them as items a person can
read and correct on the Text tab, beside the passages, and keeps their edits
in the same log as a passage's (``book_agent.text_edits``), under ids of
their own:

- a note's paragraph, by its id: ``D0001-N000002``;
- the title: ``BOOK-T``;
- a contents entry, and a picture's description, by their words:
  ``BOOK-C`` and ``BOOK-P`` and a hash of the text, so the same words are
  one item wherever they stand.

An item's "pipeline text" is what the title stage settled, or the source
where it settled nothing (no model answer, a note answered without its
markers). An edit is active while that text is unchanged, and a conflict
once a rerun of the title stage changes it, as for a passage. The compile
applies the active edits on top of the title stage's record. An item left
in the source language does not stop the compile: it is listed for review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .epub import is_note, local_name, parse_content_document
from .epub_compile import find_element_by_stable_path
from .hashing import sha256_text
from .pipeline_state import WorkflowStage
from .state import StageStatus, connect_state, get_job_metadata, get_stage_status, set_job_metadata
from .text_edits import (
    BOOK_DOCUMENT,
    BlockingCheckError,
    EditAction,
    SegmentEditEvent,
    SegmentEditState,
    SegmentEditStatus,
    StaleEditError,
    _append_action_event,
    default_author,
    events_by_segment,
    is_book_item,
    segment_status,
)
from .workspace import JobWorkspace
from .stages.decompile import load_decompile_manifest
from .stages.title import (
    _package_root,
    contents_entries,
    description_entries,
    load_translated_title,
    note_paragraphs,
    settled_texts,
)

TITLE_ID = "BOOK-T"
_MARKER = re.compile(r"</?I\d{3}>")
_MAX_LINE_CHARACTERS = 300
_MAX_NOTE_CHARACTERS = 4000


def contents_id(text: str) -> str:
    return f"BOOK-C{sha256_text(text)[:12]}"


def description_id(text: str) -> str:
    return f"BOOK-P{sha256_text(text)[:12]}"


@dataclass(frozen=True)
class BookItem:
    """One thing the book says of itself. `kind` is title, contents, note, or
    description; `document_id` the chapter it stands in (BOOK for the title
    and the contents); `pipeline` what the title stage settled, or the source
    where it settled nothing (`translated` False)."""

    item_id: str
    kind: str
    document_id: str
    source: str
    pipeline: str
    translated: bool


def title_stage_done(workspace: JobWorkspace) -> bool:
    connection = connect_state(workspace.state_file)
    try:
        record = get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)
    finally:
        connection.close()
    return bool(record and record["status"] == StageStatus.COMPLETED.value)


def book_items(workspace: JobWorkspace) -> list[BookItem]:
    """Every item, in the book's order: the title, the contents entries, the
    notes chapter by chapter, the pictures' descriptions. None before the
    title stage has run: until then there is nothing settled to correct."""
    if not title_stage_done(workspace):
        return []
    settled = load_translated_title(workspace)
    items: list[BookItem] = []
    title = settled["source"]
    if title and settled["origin"] != "passage":
        # A title that is a passage is corrected as that passage.
        items.append(BookItem(TITLE_ID, "title", BOOK_DOCUMENT, title, settled["translated"] or title, bool(settled["translated"])))
    entries = [entry for entry in contents_entries(workspace) if entry.casefold() != title.casefold()]
    for entry in entries:
        translated = settled["labels"].get(entry, "")
        items.append(BookItem(contents_id(entry), "contents", BOOK_DOCUMENT, entry, translated or entry, bool(translated)))
    manifest = load_decompile_manifest(workspace)
    owners = {note.segment_id: document.manifest_id for document in manifest.documents for note in document.notes}
    for note in note_paragraphs(workspace):
        source = note.protected_text or note.text
        if not any(c.isalpha() for c in _MARKER.sub("", source)):
            continue  # a lone "1" reads the same in any language
        translated = settled["notes"].get(note.segment_id, "")
        items.append(BookItem(note.segment_id, "note", owners[note.segment_id], source, translated or source, bool(translated)))
    first_place = {picture.alt: picture.document_id for picture in book_pictures(workspace)}
    for text in description_entries(workspace, entries):
        translated = settled["descriptions"].get(text, "")
        items.append(
            BookItem(description_id(text), "description", first_place.get(text, BOOK_DOCUMENT), text, translated or text, bool(translated))
        )
    return items


# -- where notes and pictures stand ------------------------------------------------------------


@dataclass(frozen=True)
class Picture:
    """A picture of a chapter: its file, its description, and the passage it follows ("" at the start)."""

    document_id: str
    archive_path: str
    alt: str
    after: str


@dataclass
class NoteLinks:
    """Which passages refer to which notes. A note is named by its first paragraph's id."""

    refers: dict[str, list[str]] = field(default_factory=dict)  # passage id -> notes it refers to
    referred: dict[str, list[str]] = field(default_factory=dict)  # note -> passages that refer to it
    first: dict[str, str] = field(default_factory=dict)  # each paragraph of a note -> its note


def _resolve(document_path: str, href: str) -> tuple[str, str]:
    """An href in a document as (the document it points into, the id it points at)."""
    target, _, fragment = href.partition("#")
    if not target:
        return document_path, unquote(fragment)
    parts: list[str] = []
    for part in (PurePosixPath(document_path).parent / unquote(target)).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts), unquote(fragment)


def _walk(element: ET.Element, path: str):
    """Every element under `element`, in document order, with the path the extractor gives it."""
    yield element, path
    counts: dict[str, int] = {}
    for child in list(element):
        tag = local_name(child.tag)
        if not tag:
            continue
        counts[tag] = counts.get(tag, 0) + 1
        yield from _walk(child, f"{path}/{tag}[{counts[tag]}]")


def _documents(workspace: JobWorkspace):
    """Each source document of the book with its parsed tree."""
    manifest = load_decompile_manifest(workspace)
    root = _package_root(workspace) if manifest.source_format == "epub" else None
    if root is None:
        return
    for document in manifest.documents:
        path = root.joinpath(*PurePosixPath(document.archive_path).parts)
        if path.is_file():
            yield document, parse_content_document(path.read_bytes(), document.archive_path)


def note_links(workspace: JobWorkspace) -> NoteLinks:
    """Which passage refers to which note, by the reference's own link (an
    href to the id of the note, or of an element within it)."""
    links = NoteLinks()
    targets: dict[tuple[str, str], str] = {}
    documents = list(_documents(workspace))
    for document, tree in documents:
        parents = {child: parent for parent in tree.iter() for child in parent}
        for note in document.notes:
            element = find_element_by_stable_path(tree, note.element_path)
            container = element
            ids: list[str] = []
            while container is not None:
                if container.attrib.get("id"):
                    ids.append(container.attrib["id"])
                if is_note(container):
                    break
                container = parents.get(container)
            # A note's paragraphs share its container: the first of them names the note.
            key = id(container) if container is not None else id(element)
            first = targets.setdefault((document.archive_path, f"#{key}"), note.segment_id)
            links.first[note.segment_id] = first
            for name in ids:
                targets.setdefault((document.archive_path, name), first)
    for document, tree in documents:
        for segment in document.segments:
            for marker in segment.inline_markers:
                if marker.tag != "a":
                    continue
                href = find_element_by_stable_path(tree, marker.element_path).attrib.get("href", "")
                note = targets.get(_resolve(document.archive_path, href)) if "#" in href else None
                if note and note not in links.refers.get(segment.segment_id, []):
                    links.refers.setdefault(segment.segment_id, []).append(note)
                    links.referred.setdefault(note, []).append(segment.segment_id)
    return links


def book_pictures(workspace: JobWorkspace) -> list[Picture]:
    """The book's pictures with a description, chapter by chapter, each with the passage it follows."""
    pictures: list[Picture] = []
    for document, tree in _documents(workspace):
        passages = {segment.element_path: segment.segment_id for segment in document.segments}
        after = ""
        root_path = f"/{local_name(tree.tag)}[1]"
        for element, path in _walk(tree, root_path):
            if path in passages:
                after = passages[path]
            elif local_name(element.tag) == "img" and " ".join(element.attrib.get("alt", "").split()):
                src = element.attrib.get("src", "")
                archive_path = _resolve(document.archive_path, src)[0] if src and ":" not in src else ""
                pictures.append(Picture(document.manifest_id, archive_path, " ".join(element.attrib["alt"].split()), after))
    return pictures


# -- states, checks, and edits ----------------------------------------------------------------


def book_statuses(workspace: JobWorkspace, items: list[BookItem] | None = None) -> dict[str, SegmentEditStatus]:
    """Each item's edit state, by id: from its history and what the title stage settled."""
    listed = {item.item_id: item for item in (book_items(workspace) if items is None else items)}
    statuses: dict[str, SegmentEditStatus] = {}
    for item_id, events in events_by_segment(workspace).items():
        if not is_book_item(item_id):
            continue
        item = listed.get(item_id)
        statuses[item_id] = segment_status(
            events,
            pipeline_text=item.pipeline if item else None,
            source_text=item.source if item else None,
        )
    return statuses


def book_edit_texts(workspace: JobWorkspace) -> dict[str, str]:
    """The active edits of the book's own items, without listing the items
    for a job none of whose items has ever been edited."""
    if not any(is_book_item(item_id) for item_id in events_by_segment(workspace)):
        return {}
    return active_book_edits(workspace)


def active_book_edits(workspace: JobWorkspace, items: list[BookItem] | None = None) -> dict[str, str]:
    """Item id -> the edited text, for every item edited, or in conflict (an edit still applies)."""
    return {
        item_id: status.text
        for item_id, status in book_statuses(workspace, items).items()
        if status.state in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
    }


def settle_with_edits(settled: dict[str, Any], items: list[BookItem], edits: dict[str, str]) -> dict[str, Any]:
    """The title stage's record with a person's edits in it, as the compile uses it."""
    if not edits:
        return settled
    result = {
        **settled,
        "labels": dict(settled["labels"]),
        "notes": dict(settled["notes"]),
        "descriptions": dict(settled["descriptions"]),
    }
    for item in items:
        text = edits.get(item.item_id)
        if text is None:
            continue
        if item.kind == "title":
            result["translated"] = text
        elif item.kind == "contents":
            result["labels"][item.source] = text
        elif item.kind == "note":
            result["notes"][item.item_id] = text
        else:
            result["descriptions"][item.source] = text
    return result


def settled_book(workspace: JobWorkspace, *, connection=None) -> tuple[dict[str, Any], dict[str, str]]:
    """What the book says of itself as the compile gives it: the title
    stage's record with a person's active edits in it; and those edits."""
    edits = book_edit_texts(workspace)
    settled = load_translated_title(workspace, connection=connection)
    return settle_with_edits(settled, book_items(workspace) if edits else [], edits), edits


def book_revision_hash(settled: dict[str, Any]) -> str:
    """A stable hash of what the compile takes from `settled`: the title, the
    contents entries and descriptions, the notes; "" for a book with none of
    them. A person who approves the final draft approves these with its
    passages (``text_edits.draft_revision_hash`` is the passages' revision)."""
    said = {"title": settled["translated"], "texts": settled_texts(settled), "notes": settled["notes"]}
    return sha256_text(json.dumps(said, ensure_ascii=False, sort_keys=True)) if any(said.values()) else ""


_APPROVED_BOOK = "final_review_approved_book"


def record_book_approval(connection, workspace: JobWorkspace) -> None:
    """Keep, with the approval of the final draft, what the book said of itself when it was given."""
    settled, _ = settled_book(workspace, connection=connection)
    set_job_metadata(connection, _APPROVED_BOOK, book_revision_hash(settled))


def book_approved(connection, settled: dict[str, Any]) -> bool:
    """Whether `settled`, what the book says of itself now, is what the final
    draft's approval was given for. An approval from before this was kept
    with it does not lapse because the job was upgraded: it stands for the
    book as it was last compiled, or, never compiled, as it is."""
    approved = get_job_metadata(connection, _APPROVED_BOOK)
    if approved is None:
        compiled = get_job_metadata(connection, "compiled_settled_title")
        if compiled is None:
            return True
        approved = book_revision_hash({**settled, **json.loads(compiled)})
    return approved == book_revision_hash(settled)


def check_book_text(item: BookItem, text: str) -> dict[str, list[dict[str, str]]]:
    """What stops an item's edit from being saved. A note keeps its links and
    emphasis, so that the compile can put it back where it stands; the rest
    is one line. No reason can waive either."""
    hard: list[dict[str, str]] = []
    if not text.strip():
        hard.append({"category": "empty", "severity": "high", "message": "The translation is empty."})
    if item.kind == "note":
        if _MARKER.findall(text) != _MARKER.findall(item.source):
            hard.append(
                {
                    "category": "structure",
                    "severity": "high",
                    "message": "Keep the note's link and emphasis markers ("
                    + " ".join(_MARKER.findall(item.source))
                    + "), each once and in the same order.",
                }
            )
        if len(text) > _MAX_NOTE_CHARACTERS:
            hard.append({"category": "structure", "severity": "high", "message": f"A note is at most {_MAX_NOTE_CHARACTERS} characters."})
    else:
        if "\n" in text.strip():
            hard.append({"category": "structure", "severity": "high", "message": "This is one line: it has no line breaks."})
        if len(text) > _MAX_LINE_CHARACTERS:
            hard.append({"category": "structure", "severity": "high", "message": f"This is at most {_MAX_LINE_CHARACTERS} characters."})
    return {"hard": hard, "overridable": []}


def _item(workspace: JobWorkspace, item_id: str) -> BookItem:
    item = next((item for item in book_items(workspace) if item.item_id == item_id), None)
    if item is None:
        raise ValueError(f"no such item of the book: {item_id}")
    return item


def check_book_edit(workspace: JobWorkspace, item_id: str, text: str) -> dict[str, list[dict[str, str]]]:
    return check_book_text(_item(workspace, item_id), text)


def _event(
    count: int, item: BookItem, action: EditAction, text: str, previous: str, reason: str, author: str | None, base: str
) -> SegmentEditEvent:
    return SegmentEditEvent(
        event_id=f"E{count:06d}",
        at=datetime.now().astimezone().isoformat(timespec="seconds"),
        author=author or default_author(),
        segment_id=item.item_id,
        document_id=item.document_id,
        action=action,
        text=text,
        previous_text=previous,
        base_source_sha256=sha256_text(item.source),
        base_target_sha256=sha256_text(base),
        base_target_text=base,
        reason=reason.strip(),
    )


def apply_book_edit(
    workspace: JobWorkspace,
    *,
    item_id: str,
    text: str,
    reason: str,
    base_target_sha256: str,
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Record a person's translation of an item, after the checks, against what the page showed."""
    if len(reason.strip()) < 3:
        raise ValueError("an edit needs a reason of at least 3 characters")
    item = _item(workspace, item_id)
    if sha256_text(item.pipeline) != base_target_sha256:
        raise StaleEditError(f"{item_id} has changed since this edit was based on it; reload the chapter")
    hard = check_book_text(item, text)["hard"]
    if hard:
        raise BlockingCheckError(item_id, hard)

    def build(count: int, events: list[SegmentEditEvent]) -> SegmentEditEvent:
        previous = segment_status(events, pipeline_text=item.pipeline, source_text=item.source).text
        return _event(count, item, EditAction.EDIT, text, previous, reason, author, item.pipeline)

    return _append_action_event(workspace, segment_id=item_id, expected_event_id=expected_event_id, build=build)


def apply_book_action(
    workspace: JobWorkspace,
    *,
    action: EditAction,
    item_id: str,
    reason: str,
    author: str | None = None,
    expected_event_id: str | None = None,
) -> SegmentEditEvent:
    """Revert an item to what the title stage settled; or, for a conflict,
    keep the edit (re-based) or take the title stage's new text."""
    if len(reason.strip()) < 3:
        raise ValueError("this needs a reason of at least 3 characters")
    item = _item(workspace, item_id)

    def build(count: int, events: list[SegmentEditEvent]) -> SegmentEditEvent:
        status = segment_status(events, pipeline_text=item.pipeline, source_text=item.source)
        if action is EditAction.REVERT and status.state not in (SegmentEditState.EDITED, SegmentEditState.CONFLICT):
            raise ValueError(f"item has no active edit to revert: {item_id}")
        if action in (EditAction.KEEP, EditAction.TAKE_PIPELINE) and status.state is not SegmentEditState.CONFLICT:
            raise ValueError(f"item is not a conflict: {item_id}")
        text = status.text if action is EditAction.KEEP else ""
        return _event(count, item, action, text, status.text, reason, author, item.pipeline)

    return _append_action_event(workspace, segment_id=item_id, expected_event_id=expected_event_id, build=build)


def untranslated_items(workspace: JobWorkspace) -> list[dict[str, Any]]:
    """The items the title stage left in the source language and no one has
    translated since, for the review page: what each is and where it stands."""
    items = book_items(workspace)
    statuses = book_statuses(workspace, items)
    titles = {document.manifest_id: document.title for document in load_decompile_manifest(workspace).documents}
    return [
        {
            "item_id": item.item_id,
            "kind": item.kind,
            "document_id": item.document_id,
            "chapter": titles.get(item.document_id, "Title and contents"),
            "source": item.source,
        }
        for item in items
        if not item.translated
        and statuses.get(item.item_id, SegmentEditStatus(SegmentEditState.PIPELINE, "", None)).state
        not in (SegmentEditState.EDITED, SegmentEditState.CONFLICT)
    ]
