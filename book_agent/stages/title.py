"""The book's title in the target language, for the compiled book to carry.

A title is seldom a passage of the book. Where it is one (a title page's
heading), the passage's translation is the title's, and the compile stage
finds it. Where it is not, the book would be translated and still be called
by its source title. This stage settles it before the compile:

1. `translation.translated_title` in the config, when set, is the title;
2. else, where the title reads the same as a translated passage, there is
   nothing to do;
3. else one short model call translates it, with the glossary. If that
   gives nothing usable, or no model answers, the book keeps its title and
   the stage says so: a title is not worth stopping a book for.

The table of contents is settled here too. An entry that reads the same as
a passage (most are a chapter's heading, word for word) takes that passage's
translation at the compile. The others ("Chapter 7" for a heading that reads
"CHAPTER VII. A Mad Tea-Party") are translated here, a batch to a call, with
the book's headings and their translations before the model so that an entry
is worded as its chapter is.

So are the book's footnotes and endnotes: an EPUB's (epub:type footnote,
endnote, ...), and a Word document's or a Markdown file's, which the
converted book marks the same way. They are the book's words, but kept apart
from its passages. They are translated here, a paragraph to a numbered line,
a batch to a call, with their links and emphasis as inline markers; a
paragraph is taken only with its markers all there, so that the compile can
put it back where it stands. And so are the pictures' descriptions (alt
text) that are no passage of the book.

Each call goes through the run's client, so the run's log and summary count
it under this stage, by usage role (title.book, title.contents,
title.notes, title.descriptions), and each is recorded as an attempt with
its metrics.

A subtitle file has no title, contents, or notes to carry, and the stage
does nothing for one.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from ..atomic_io import atomic_write_text
from ..config import AppConfig, translation_model
from ..epub import TextSegment, normalize_text
from ..epub_compile import package_descriptions, package_labels
from ..hashing import sha256_file
from ..languages import glossary_sides, profile
from ..ollama_client import OllamaClient
from ..pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from ..preprocessing import select_relevant_glossary_entries
from ..stage_progress import report_stage_plan
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    initialize_state,
    record_artifact,
    record_attempt,
    set_job_metadata,
    set_stage_status,
)
from ..workspace import JobWorkspace
from .decompile import load_decompile_manifest
from .glossary import load_approved_glossary
from .validate_repaired import load_validated_repaired_documents

TITLE_STAGE_VERSION = "5"
_INLINE_MARKER = re.compile(r"</?I\d{3}>")
_INLINE_TOKEN = re.compile(r"</?I\d{3}>")
_MAX_TITLE_CHARACTERS = 300


def book_title(workspace: JobWorkspace) -> str:
    """The source book's title as its package records it; "" when it has none to carry."""
    manifest = load_decompile_manifest(workspace)
    if manifest.source_format != "epub":
        return ""  # an RTF's compiled book takes its first heading; a subtitle file has no title
    values = manifest.metadata.values.get("title") or []
    return normalize_text(values[0]) if values else ""


def _passage_translation(workspace: JobWorkspace, title: str) -> str:
    """The translation of the passage that reads as the title does, or ""."""
    wanted = title.casefold()
    for repaired in load_validated_repaired_documents(workspace):
        for segment in repaired.document.segments:
            if normalize_text(_INLINE_MARKER.sub("", segment.source_text)).casefold() == wanted:
                return normalize_text(_INLINE_MARKER.sub("", segment.translated_text))
    return ""


_MAX_OCCURRENCES = 3
_MAX_OCCURRENCE_CHARACTERS = 300


def _occurrences(workspace: JobWorkspace, title: str) -> list[tuple[str, str]]:
    """The first short passages of the book the title stands in, each with its
    translation: a chapter heading that ends in the title, a line that names
    the book. The title should read as the book reads there."""
    wanted = title.casefold()
    found: list[tuple[str, str]] = []
    for repaired in load_validated_repaired_documents(workspace):
        for segment in repaired.document.segments:
            source = normalize_text(_INLINE_MARKER.sub("", segment.source_text))
            if wanted in source.casefold() and len(source) <= _MAX_OCCURRENCE_CHARACTERS:
                found.append((source, normalize_text(_INLINE_MARKER.sub("", segment.translated_text))))
                if len(found) == _MAX_OCCURRENCES:
                    return found
    return found


def build_title_prompt(
    title: str, config: AppConfig, glossary_lines: list[str], occurrences: list[tuple[str, str]] | None = None
) -> str:
    """`occurrences`: passages of the book the title stands in, with their translations."""
    direction = config.translation.direction
    source = profile(direction.source_language).display_name
    target = profile(direction.target_language).display_name
    glossary = "\n".join(glossary_lines) or "(none)"
    in_the_book = ""
    if occurrences:
        in_the_book = (
            "\n\nThe book's own translation of passages the title stands in. Word the title as the "
            "book words it there:\n" + "\n".join(f"- {before} => {after}" for before, after in occurrences)
        )
    return (
        f"Translate the title of a book from {source} into {target}. Give the title as a publisher "
        f"would print it on the {target} edition: faithful, natural, and no longer than it needs to be. "
        "Use the approved glossary rendering for any name or term in it. Output the translated title "
        "alone, on one line, without quotation marks, notes, or the original.\n\n"
        f"Approved glossary:\n{glossary}{in_the_book}\n\nTitle: {title}"
    )


_LABELS_PER_CALL = 40
_NOTES_PER_CALL = 20
_NOTE_CHARACTERS_PER_CALL = 4000
_MAX_NOTE_CHARACTERS = 4000
_MAX_HEADINGS_SHOWN = 80
_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)、:：]\s*(.+?)\s*$")


def _package_root(workspace: JobWorkspace):
    connection = connect_state(workspace.state_file)
    try:
        manifest_relative = get_job_metadata(connection, "decompile_manifest")
    finally:
        connection.close()
    return workspace.directory(manifest_relative).parent / "package" if manifest_relative else None


def _passages(workspace: JobWorkspace) -> set[str]:
    return {
        normalize_text(_INLINE_MARKER.sub("", segment.source_text)).casefold()
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }


def _unsettled(texts: list[str], passages: set[str]) -> list[str]:
    """Of `texts`, those with words in them that read as no passage does, once each, in order."""
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        key = text.casefold()
        # "7" or "IV" alone reads the same in any language.
        if text and key not in passages and key not in seen and any(c.isalpha() for c in text) and not re.fullmatch(r"[ivxlcdm\d\s.]+", key):
            seen.add(key)
            found.append(text)
    return found


def contents_entries(workspace: JobWorkspace) -> list[str]:
    """The entries of the book's table of contents, and its documents' own
    titles, that read as no passage of the book does: the compile cannot give
    them a passage's translation. In the order the book has them."""
    manifest = load_decompile_manifest(workspace)
    root = _package_root(workspace) if manifest.source_format == "epub" else None
    return _unsettled(package_labels(root, manifest), _passages(workspace)) if root else []


def note_paragraphs(workspace: JobWorkspace) -> list[TextSegment]:
    """The paragraphs of the book's footnotes and endnotes: the book's own
    words, but no passages, so translated here and not with the chapters.
    An EPUB's notes (epub:type footnote, endnote, ...), and a Word or
    Markdown book's, which its package marks the same way."""
    manifest = load_decompile_manifest(workspace)
    if manifest.source_format != "epub":
        return []
    return [note for document in manifest.documents for note in document.notes]


def note_entries(workspace: JobWorkspace) -> list[str]:
    """The notes' paragraphs to translate, once each, in the order the book
    has them, with their inline markers (<I000>...</I000>) where they have
    a link or emphasis: a paragraph without words (a lone "1") is left alone."""
    texts = [note.protected_text or note.text for note in note_paragraphs(workspace)]
    return [text for text in dict.fromkeys(texts) if any(c.isalpha() for c in _INLINE_MARKER.sub("", text))]


def description_entries(workspace: JobWorkspace, contents: list[str]) -> list[str]:
    """The descriptions of the book's pictures (alt text) that read as no
    passage and no contents entry does, in the order the book has them."""
    manifest = load_decompile_manifest(workspace)
    root = _package_root(workspace) if manifest.source_format == "epub" else None
    if root is None:
        return []
    settled = _passages(workspace) | {entry.casefold() for entry in contents}
    return _unsettled(package_descriptions(root, manifest), settled)


def _headings(workspace: JobWorkspace) -> list[tuple[str, str]]:
    """The book's headings with their translations, for the model to word a contents entry by."""
    translated = {
        segment.segment_id: normalize_text(_INLINE_MARKER.sub("", segment.translated_text))
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }
    pairs: list[tuple[str, str]] = []
    for document in load_decompile_manifest(workspace).documents:
        for segment in document.segments:
            if re.fullmatch(r"h[1-6]", segment.tag) and segment.segment_id in translated and len(segment.text) <= 200:
                pairs.append((normalize_text(segment.text), translated[segment.segment_id]))
    return pairs[:_MAX_HEADINGS_SHOWN]


def build_contents_prompt(
    entries: list[str], config: AppConfig, glossary_lines: list[str], headings: list[tuple[str, str]]
) -> str:
    direction = config.translation.direction
    source = profile(direction.source_language).display_name
    target = profile(direction.target_language).display_name
    glossary = "\n".join(glossary_lines) or "(none)"
    shown = "\n".join(f"- {before} => {after}" for before, after in headings) or "(none)"
    numbered = "\n".join(f"{number}. {entry}" for number, entry in enumerate(entries, start=1))
    return (
        f"Translate these entries of a book's table of contents from {source} into {target}. An entry "
        "names a chapter or a part of the book, often in fewer words than the chapter's own heading. "
        "Below are the book's headings as the book translates them: where an entry names one of those "
        "chapters, word it as the book words the heading. Use the approved glossary rendering for any "
        f"name or term. Answer with one line for each entry, numbered as it is here, in {target} "
        "only, and nothing else.\n\n"
        f"Approved glossary:\n{glossary}\n\nThe book's headings:\n{shown}\n\nEntries:\n{numbered}"
    )


def build_notes_prompt(notes: list[str], config: AppConfig, glossary_lines: list[str]) -> str:
    direction = config.translation.direction
    source = profile(direction.source_language).display_name
    target = profile(direction.target_language).display_name
    glossary = "\n".join(glossary_lines) or "(none)"
    numbered = "\n".join(f"{number}. {note}" for number, note in enumerate(notes, start=1))
    return (
        f"Translate these footnotes of a book from {source} into {target}. Each is one paragraph of a "
        "note, as the book prints it below the text or at the end of a chapter: translate it whole, "
        "faithfully, and keep a reference to a page, a book, or an author as it is written. Markers "
        "such as <I000> and </I000> stand for a link or emphasis: keep every one of them, in the same "
        "order, around the words they mark in your translation. Use the approved glossary rendering "
        f"for any name or term. Answer with one line for each paragraph, numbered as it is here, in "
        f"{target} only, and nothing else.\n\n"
        f"Approved glossary:\n{glossary}\n\nNotes:\n{numbered}"
    )


def build_descriptions_prompt(descriptions: list[str], config: AppConfig, glossary_lines: list[str]) -> str:
    direction = config.translation.direction
    source = profile(direction.source_language).display_name
    target = profile(direction.target_language).display_name
    glossary = "\n".join(glossary_lines) or "(none)"
    numbered = "\n".join(f"{number}. {text}" for number, text in enumerate(descriptions, start=1))
    return (
        f"Translate these descriptions of a book's pictures from {source} into {target}. Each says "
        "what a picture shows, for a reader who cannot see it: translate it plainly and faithfully. "
        "Use the approved glossary rendering for any name or term. Answer with one line for each "
        f"description, numbered as it is here, in {target} only, and nothing else.\n\n"
        f"Approved glossary:\n{glossary}\n\nDescriptions:\n{numbered}"
    )


class _Calls:
    """The stage's model calls, made and kept account of as the other stages
    make theirs: through the run's client, so that the run's log and its
    summary count them under this stage and a usage role of their own; each
    recorded as an attempt with its metrics; and summed for the stage's report.
    A run that comes here only to compile may have no client and no Ollama:
    one is made on the first call, and its failure is that call's failure."""

    def __init__(self, connection, client: Any, config: AppConfig):
        self.connection = connection
        self.client = client
        self.config = config
        self.usage = {"llm_calls": 0, "completed_llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0}

    def generate(self, prompt: str, *, scope: str, attempt: int, role: str, label: str) -> str:
        self.usage["llm_calls"] += 1
        try:
            if self.client is None:
                self.client = OllamaClient(self.config.ollama)
            generated = self.client.generate_text(
                prompt,
                model=translation_model(self.config),
                think=self.config.translation.thinking,
                progress_label=label,
                usage_role=role,
            )
        except Exception as error:
            record_attempt(
                self.connection, scope, WorkflowStage.TRANSLATE_TITLE.value, attempt, StageStatus.FAILED, message=str(error)
            )
            raise
        metrics = generated.metrics
        record_attempt(
            self.connection,
            scope,
            WorkflowStage.TRANSLATE_TITLE.value,
            attempt,
            StageStatus.COMPLETED,
            metrics=asdict(metrics) if metrics is not None else {},
        )
        if metrics is not None:
            self.usage["completed_llm_calls"] += 1
            self.usage["prompt_tokens"] += metrics.prompt_eval_count
            self.usage["output_tokens"] += metrics.eval_count
            self.usage["llm_seconds"] = round(self.usage["llm_seconds"] + metrics.total_duration_ns / 1_000_000_000, 3)
        return generated.content


def _numbered_answers(answer: str, batch: list[str], longest: int, marked: bool = False) -> dict[str, str]:
    """The lines of `answer` numbered as an entry of `batch`, by that entry.
    A `marked` entry (a note's paragraph) is taken only with its inline
    markers all there, in their order, and as the model wrote it."""
    found: dict[str, str] = {}
    for line in answer.splitlines():
        numbered = _NUMBERED.match(line)
        if numbered and 1 <= int(numbered.group(1)) <= len(batch):
            entry = batch[int(numbered.group(1)) - 1]
            text = numbered.group(2).strip() if marked else _clean(numbered.group(2))
            if marked and _INLINE_TOKEN.findall(text) != _INLINE_TOKEN.findall(entry):
                continue
            if text and len(text) <= longest:
                found.setdefault(entry, text)
    return found


def _batches(texts: list[str], count: int, characters: int) -> list[list[str]]:
    """`texts` in batches of at most `count`, and of about `characters` at most where they are long."""
    batches: list[list[str]] = []
    size = 0
    for text in texts:
        if not batches or len(batches[-1]) == count or (batches[-1] and size + len(text) > characters):
            batches.append([])
            size = 0
        batches[-1].append(text)
        size += len(text)
    return batches


def _translate_batches(
    workspace: JobWorkspace, calls: _Calls, texts: list[str], kind: str, resolved: AppConfig
) -> tuple[dict[str, str], str]:
    """`texts` (contents entries, notes, or pictures' descriptions)
    translated a batch to a call: as many as the model gave usably, and a
    note for the stage's message. No answer is no reason to stop: a text
    without a translation stays as it was."""
    direction = resolved.translation.direction
    approved = load_approved_glossary(workspace)
    headings: list[tuple[str, str]] = []
    if kind == "contents":
        batches = _batches(texts, _LABELS_PER_CALL, 10**9)
        headings = _headings(workspace)
        named, longest = "contents entries", _MAX_TITLE_CHARACTERS
    elif kind == "descriptions":
        batches = _batches(texts, _LABELS_PER_CALL, _NOTE_CHARACTERS_PER_CALL)
        named, longest = "picture descriptions", _MAX_NOTE_CHARACTERS
    else:
        batches = _batches(texts, _NOTES_PER_CALL, _NOTE_CHARACTERS_PER_CALL)
        named, longest = "notes", _MAX_NOTE_CHARACTERS
    translated: dict[str, str] = {}
    done = 0
    try:
        for number, batch in enumerate(batches, start=1):
            lines = [
                " => ".join(glossary_sides(entry, direction))
                for entry in select_relevant_glossary_entries("\n".join(batch), approved.entries, direction)
            ]
            if kind == "contents":
                prompt = build_contents_prompt(batch, resolved, lines, headings)
            elif kind == "descriptions":
                prompt = build_descriptions_prompt(batch, resolved, lines)
            else:
                prompt = build_notes_prompt(batch, resolved, lines)
            answer = calls.generate(
                prompt,
                scope=f"{WorkflowStage.TRANSLATE_TITLE.value}:{kind}:{number:04d}",
                attempt=1,
                role=f"title.{kind}",
                label=f"{named}={done + 1}-{done + len(batch)}/{len(texts)}",
            )
            translated.update(_numbered_answers(answer, batch, longest, marked=kind == "notes"))
            done += len(batch)
    except Exception as error:  # noqa: BLE001 - neither is worth a book: say why and go on
        return translated, f"; {len(translated)} of {len(texts)} {named} translated ({error})"
    return translated, f"; {len(translated)} of {len(texts)} {named} translated"


def _clean(answer: str) -> str:
    """The model's answer as a title: its first line, without the quotation marks a model likes to add."""
    line = next((item.strip() for item in answer.strip().splitlines() if item.strip()), "")
    line = re.sub(r"^(?:title|translation)\s*:\s*", "", line, flags=re.IGNORECASE)
    return normalize_text(line.strip("\"'“”‘’《》「」«»„"))


def run_title_stage(workspace: JobWorkspace, config: AppConfig | None = None, client: Any = None) -> dict[str, Any]:
    """Settle the translated title, the contents entries, and the notes, and
    record them; returns {source, translated, origin, labels, notes, usage}."""
    connection = connect_state(workspace.state_file)
    try:
        resolved = config or AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
        initialize_state(connection)
        validated = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
        if validated is None or validated["status"] != StageStatus.COMPLETED.value:
            raise RuntimeError(f"required stage is not complete: {WorkflowStage.VALIDATE_REPAIRED.value}")
        title = book_title(workspace)
        override = normalize_text(resolved.translation.translated_title or "")
        passage = _passage_translation(workspace, title) if title and not override else ""
        # The title is settled as the title, wherever in the contents it also stands.
        entries = [entry for entry in contents_entries(workspace) if entry.casefold() != title.casefold()]
        notes = note_entries(workspace)
        descriptions = description_entries(workspace, entries)
        title_call = bool(title and not override and not passage)
        input_hash = build_stage_input_hash(
            {
                "title": title,
                "override": override,
                # Only what a model call depends on: a title that is a passage follows the passage.
                "passage": passage,
                "direction": resolved.translation.direction.value,
                "model": translation_model(resolved) if title_call or entries or notes or descriptions else "",
                "contents": "\n".join(entries),
                **({"notes": "\n".join(notes)} if notes else {}),
                **({"descriptions": "\n".join(descriptions)} if descriptions else {}),
                "stage_version": TITLE_STAGE_VERSION,
            }
        )
        if stage_is_current(connection, WorkflowStage.TRANSLATE_TITLE, input_hash, artifact_root=workspace.root):
            return load_translated_title(workspace, connection=connection)
        previous = get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)
        if previous and previous["status"] == StageStatus.COMPLETED.value:
            invalidate_stage_and_dependents(connection, WorkflowStage.TRANSLATE_TITLE)
            previous = get_stage_status(connection, WorkflowStage.TRANSLATE_TITLE.value)
        attempts = int(previous["attempts"]) + 1 if previous else 1
        set_stage_status(
            connection, WorkflowStage.TRANSLATE_TITLE.value, StageStatus.RUNNING, attempts=attempts, input_hash=input_hash
        )
        try:
            # What there is to settle, and how much of it needs the model, as the other stages report their plans.
            planned = int(bool(title)) + len(entries) + len(notes) + len(descriptions)
            calls_planned = (
                int(title_call)
                + len(_batches(entries, _LABELS_PER_CALL, 10**9))
                + len(_batches(notes, _NOTES_PER_CALL, _NOTE_CHARACTERS_PER_CALL))
                + len(_batches(descriptions, _LABELS_PER_CALL, _NOTE_CHARACTERS_PER_CALL))
            )
            report_stage_plan(
                client,
                model=translation_model(resolved),
                stage=WorkflowStage.TRANSLATE_TITLE.value,
                prescreened=planned,
                llm_tasks=calls_planned,
                skipped=int(bool(title) and not title_call),
                role="title",
            )
            calls = _Calls(connection, client, resolved)
            if not title:
                translated, origin, message = "", "none", "no title to translate"
            elif override:
                translated, origin, message = override, "config", f"from the config: {override}"
            elif passage:
                # The compile stage gives the title its passage's translation, edits to it included.
                translated, origin, message = "", "passage", f"the title is a passage of the book: {passage}"
            else:
                direction = resolved.translation.direction
                approved = load_approved_glossary(workspace)
                relevant = select_relevant_glossary_entries(title, approved.entries, direction)
                lines = [" => ".join(glossary_sides(entry, direction)) for entry in relevant]
                translated, trouble = "", "the model gave no usable title"
                try:
                    for attempt in range(1, resolved.workflow.max_retries + 2):
                        answer = calls.generate(
                            build_title_prompt(title, resolved, lines, _occurrences(workspace, title)),
                            scope=f"{WorkflowStage.TRANSLATE_TITLE.value}:title",
                            attempt=attempt,
                            role="title.book",
                            label=f"title attempt={attempt}/{resolved.workflow.max_retries + 1}",
                        )
                        translated = _clean(answer)
                        if translated and len(translated) <= _MAX_TITLE_CHARACTERS:
                            break
                        translated = ""
                except Exception as error:  # noqa: BLE001 - a title is not worth a book: say why and go on
                    translated, trouble = "", f"the title could not be translated ({error})"
                if translated:
                    origin, message = "model", translated
                else:
                    origin = "none"
                    message = (
                        f"{trouble}; the book keeps its title. To give it one, set "
                        "translation.translated_title in the config and rerun from here"
                    )
            labels: dict[str, str] = {}
            if entries:
                labels, said = _translate_batches(workspace, calls, entries, "contents", resolved)
                message += said
            by_text: dict[str, str] = {}
            if notes:
                by_text, said = _translate_batches(workspace, calls, notes, "notes", resolved)
                message += said
            # Each paragraph by its id: the compile puts it back where it stands, links and emphasis in place.
            translated_notes = {
                note.segment_id: by_text[note.protected_text or note.text]
                for note in note_paragraphs(workspace)
                if (note.protected_text or note.text) in by_text
            }
            described: dict[str, str] = {}
            if descriptions:
                described, said = _translate_batches(workspace, calls, descriptions, "descriptions", resolved)
                message += said
            record = {
                "source": title,
                "translated": translated,
                "origin": origin,
                "labels": labels,
                "notes": translated_notes,
                "descriptions": described,
                "usage": calls.usage,
            }
            path = workspace.directory("reports") / f"title-{input_hash[:16]}.json"
            atomic_write_text(path, json.dumps(record, ensure_ascii=False, indent=2))
            connection.execute("DELETE FROM artifacts WHERE stage = ?", (WorkflowStage.TRANSLATE_TITLE.value,))
            connection.commit()
            record_artifact(
                connection,
                path.relative_to(workspace.root).as_posix(),
                WorkflowStage.TRANSLATE_TITLE.value,
                "translated_title",
                sha256_file(path),
                path.stat().st_size,
            )
            set_job_metadata(connection, "translated_title_report", path.relative_to(workspace.root).as_posix())
            set_stage_status(
                connection,
                WorkflowStage.TRANSLATE_TITLE.value,
                StageStatus.COMPLETED,
                attempts=attempts,
                input_hash=input_hash,
                output_hash=build_stage_output_hash(connection, WorkflowStage.TRANSLATE_TITLE),
                message=message,
            )
            return record
        except Exception as error:
            set_stage_status(
                connection,
                WorkflowStage.TRANSLATE_TITLE.value,
                StageStatus.FAILED,
                attempts=attempts,
                input_hash=input_hash,
                message=str(error),
            )
            raise
    finally:
        connection.close()


_NOTHING_SETTLED = {
    "source": "", "translated": "", "origin": "none", "labels": {}, "notes": {}, "descriptions": {}, "usage": {}
}


def settled_texts(settled: dict[str, Any]) -> dict[str, str]:
    """What the compile gives in translation, by its source text, besides the
    passages and the notes: the contents entries and the pictures'
    descriptions the title stage translated."""
    return {**settled["labels"], **settled["descriptions"]}


def load_translated_title(workspace: JobWorkspace, *, connection=None) -> dict[str, Any]:
    """What the title stage settled: {source, translated, origin, labels,
    notes, descriptions, usage}; empty values where it settled nothing.
    `labels` is the translation of each contents entry that is no passage of
    the book, by its text; `notes` of each paragraph of a note, by its id
    (with its inline markers); `descriptions` of each picture's description,
    by its text; and `usage` what the stage's model calls cost."""
    owns = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "translated_title_report")
        record = get_stage_status(active, WorkflowStage.TRANSLATE_TITLE.value)
        if not relative or record is None or record["status"] != StageStatus.COMPLETED.value:
            return dict(_NOTHING_SETTLED)
        path = workspace.directory(relative)
        if not path.is_file():
            return dict(_NOTHING_SETTLED)
        # A record from before the stage settled contents and notes has neither.
        return {**_NOTHING_SETTLED, **json.loads(path.read_text(encoding="utf-8"))}
    finally:
        if owns:
            active.close()
