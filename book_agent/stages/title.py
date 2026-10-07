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

A subtitle file has no title or contents to carry, and the stage does nothing
for one.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..atomic_io import atomic_write_text
from ..config import AppConfig, translation_model
from ..epub import normalize_text
from ..epub_compile import package_labels
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
from ..state import (
    StageStatus,
    connect_state,
    get_job_metadata,
    get_stage_status,
    initialize_state,
    record_artifact,
    set_job_metadata,
    set_stage_status,
)
from ..workspace import JobWorkspace
from .decompile import load_decompile_manifest
from .glossary import load_approved_glossary
from .validate_repaired import load_validated_repaired_documents

TITLE_STAGE_VERSION = "3"
_INLINE_MARKER = re.compile(r"</?I\d{3}>")
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
_MAX_HEADINGS_SHOWN = 80
_NUMBERED = re.compile(r"^\s*(\d+)\s*[.)\u3001:\uff1a]\s*(.+?)\s*$")


def contents_entries(workspace: JobWorkspace) -> list[str]:
    """The entries of the book's table of contents, and its documents' own
    titles, that read as no passage of the book does: the compile cannot give
    them a passage's translation. In the order the book has them."""
    manifest = load_decompile_manifest(workspace)
    if manifest.source_format != "epub":
        return []
    connection = connect_state(workspace.state_file)
    try:
        manifest_relative = get_job_metadata(connection, "decompile_manifest")
    finally:
        connection.close()
    if not manifest_relative:
        return []
    package_root = workspace.directory(manifest_relative).parent / "package"
    passages = {
        normalize_text(_INLINE_MARKER.sub("", segment.source_text)).casefold()
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
    }
    entries: list[str] = []
    seen: set[str] = set()
    for text in package_labels(package_root, manifest):
        key = text.casefold()
        # "7" or "IV" alone reads the same in any language.
        if text and key not in passages and key not in seen and any(c.isalpha() for c in text) and not re.fullmatch(r"[ivxlcdm\d\s.]+", key):
            seen.add(key)
            entries.append(text)
    return entries


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


def _translate_contents(workspace: JobWorkspace, entries: list[str], resolved: AppConfig, client: Any) -> tuple[dict[str, str], str]:
    """The entries' translations, as many as the model gave usably, and a note
    for the stage's message. No answer is no reason to stop: an entry without
    a translation stays as it was."""
    direction = resolved.translation.direction
    approved = load_approved_glossary(workspace)
    headings = _headings(workspace)
    translated: dict[str, str] = {}
    try:
        model = client if client is not None else OllamaClient(resolved.ollama)
        for start in range(0, len(entries), _LABELS_PER_CALL):
            batch = entries[start : start + _LABELS_PER_CALL]
            lines = [
                " => ".join(glossary_sides(entry, direction))
                for entry in select_relevant_glossary_entries("\n".join(batch), approved.entries, direction)
            ]
            generated = model.generate_text(
                build_contents_prompt(batch, resolved, lines, headings),
                model=translation_model(resolved),
                think=resolved.translation.thinking,
                progress_label=f"contents entries={start + 1}-{start + len(batch)}/{len(entries)}",
            )
            for line in generated.content.splitlines():
                found = _NUMBERED.match(line)
                if found and 1 <= int(found.group(1)) <= len(batch):
                    text = _clean(found.group(2))
                    if text and len(text) <= _MAX_TITLE_CHARACTERS:
                        translated.setdefault(batch[int(found.group(1)) - 1], text)
    except Exception as error:  # noqa: BLE001 - the contents are not worth a book either
        return translated, f"; {len(translated)} of {len(entries)} contents entries translated ({error})"
    return translated, f"; {len(translated)} of {len(entries)} contents entries translated"


def _clean(answer: str) -> str:
    """The model's answer as a title: its first line, without the quotation marks a model likes to add."""
    line = next((item.strip() for item in answer.strip().splitlines() if item.strip()), "")
    line = re.sub(r"^(?:title|translation)\s*:\s*", "", line, flags=re.IGNORECASE)
    return normalize_text(line.strip("\"'“”‘’《》「」«»„"))


def run_title_stage(workspace: JobWorkspace, config: AppConfig | None = None, client: Any = None) -> dict[str, Any]:
    """Settle the translated title and the contents entries, and record them;
    returns {source, translated, origin, labels}."""
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
        input_hash = build_stage_input_hash(
            {
                "title": title,
                "override": override,
                # Only what a model call depends on: a title that is a passage follows the passage.
                "passage": passage,
                "direction": resolved.translation.direction.value,
                "model": translation_model(resolved) if (title and not override and not passage) or entries else "",
                "contents": "\n".join(entries),
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
                    # A run that comes here only to compile has no client yet, and may have no Ollama.
                    model = client if client is not None else OllamaClient(resolved.ollama)
                    for attempt in range(1, resolved.workflow.max_retries + 2):
                        generated = model.generate_text(
                            build_title_prompt(title, resolved, lines, _occurrences(workspace, title)),
                            model=translation_model(resolved),
                            think=resolved.translation.thinking,
                            progress_label=f"title attempt={attempt}/{resolved.workflow.max_retries + 1}",
                        )
                        translated = _clean(generated.content)
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
                labels, note = _translate_contents(workspace, entries, resolved, client)
                message += note
            record = {"source": title, "translated": translated, "origin": origin, "labels": labels}
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


def load_translated_title(workspace: JobWorkspace, *, connection=None) -> dict[str, Any]:
    """What the title stage settled: {source, translated, origin, labels};
    empty values where it settled nothing. `labels` is the translation of each
    contents entry that is no passage of the book."""
    owns = connection is None
    active = connection or connect_state(workspace.state_file)
    try:
        relative = get_job_metadata(active, "translated_title_report")
        record = get_stage_status(active, WorkflowStage.TRANSLATE_TITLE.value)
        if not relative or record is None or record["status"] != StageStatus.COMPLETED.value:
            return {"source": "", "translated": "", "origin": "none", "labels": {}}
        path = workspace.directory(relative)
        if not path.is_file():
            return {"source": "", "translated": "", "origin": "none", "labels": {}}
        return {"labels": {}, **json.loads(path.read_text(encoding="utf-8"))}
    finally:
        if owns:
            active.close()
