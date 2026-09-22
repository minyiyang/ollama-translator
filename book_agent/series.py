"""Versioned series glossaries: manifest, curation workbench, published versions.

A series lives in ``<runs>/.series/<series-id>/``.  Published versions are never
rewritten; each book pins one (``series_binding``), so publishing a new version
for a later volume leaves every translated book's checkpoints current.  See
docs/SERIES_GLOSSARY_UI.md.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .atomic_io import atomic_write_text
from .glossary import (
    GlossarySource,
    GlossarySourceKind,
    load_glossary_file,
    screen_glossary_candidates,
    sort_glossary_entries,
)
from .hashing import sha256_file
from .languages import TranslationDirection
from .pipeline_state import WorkflowStage
from .schemas import GlossaryCategory, GlossaryEntry, GlossaryResult, normalize_term
from .series_binding import load_series_binding, write_series_binding
from .series_glossary import (
    _preferred_text,
    _remove_cross_term_alias_conflicts,
    build_series_glossary,
    synchronize_book_glossaries,
    write_series_book_overlays,
)
from .state import StageStatus, connect_state, get_stage_status
from .workspace import JobWorkspace, open_job_workspace

SERIES_DIR = ".series"
_SERIES_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

Origin = Literal["consensus", "conflict", "single_book", "carried", "manual"]
Decision = Literal["pending", "keep", "drop"]
DecidedBy = Literal["rule", "user", "llm-accepted"]


class SeriesBook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    volume: int | None = None
    added_at: str


class SeriesVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    created_at: str
    source_jobs: list[str]
    term_count: int = Field(ge=0)
    glossary_sha256: str
    based_on: str | None = None


class SeriesManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    series_id: str
    name: str
    direction: TranslationDirection
    books: list[SeriesBook] = Field(default_factory=list)
    versions: list[SeriesVersion] = Field(default_factory=list)

    @property
    def latest(self) -> SeriesVersion | None:
        return self.versions[-1] if self.versions else None


class TermSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["resolve", "drop_generic", "promote"]
    chinese: str | None = None
    rationale: str
    model: str


class WorkbenchTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_id: str
    english: str
    chinese: str
    category: GlossaryCategory
    aliases: list[str] = Field(default_factory=list)
    note: str = ""
    origin: Origin
    books: dict[str, list[str]] = Field(default_factory=dict)
    decision: Decision
    decided_by: DecidedBy
    reason: str = ""
    locked_from: str | None = None
    suggestion: TermSuggestion | None = None
    # Suggestion kinds a person rejected; that task does not ask about the term again.
    dismissed: list[Literal["resolve", "drop_generic", "promote"]] = Field(default_factory=list)
    # Occurrences of the English term in each member book's source text (capped),
    # including books whose glossary missed it: evidence for promoting it.
    mentions: dict[str, int] = Field(default_factory=dict)

    def variants(self) -> list[str]:
        """Every distinct target rendering, carried value first."""
        seen = [self.chinese] if self.origin == "carried" else []
        for values in self.books.values():
            seen.extend(values)
        return list(dict.fromkeys(seen))


class Workbench(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    series_id: str
    based_on: str | None
    built_at: str
    source_jobs: list[str]
    boundaries: dict[str, Literal["approved", "resolved"]]
    not_ready: list[str] = Field(default_factory=list)
    minimum_books: int = 2
    terms: list[WorkbenchTerm] = Field(default_factory=list)


# -- paths and persistence ---------------------------------------------------


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def series_root(runs: Path, series_id: str) -> Path:
    if not _SERIES_ID.fullmatch(series_id):
        raise ValueError(f"invalid series id: {series_id!r}")
    return Path(runs).resolve() / SERIES_DIR / series_id


def _manifest_path(runs: Path, series_id: str) -> Path:
    return series_root(runs, series_id) / "series.json"


def _workbench_path(runs: Path, series_id: str) -> Path:
    return series_root(runs, series_id) / "workbench.json"


def version_glossary_path(runs: Path, series_id: str, version: str) -> Path:
    return series_root(runs, series_id) / "versions" / f"{version}.glossary.json"


def load_manifest(runs: Path, series_id: str) -> SeriesManifest:
    path = _manifest_path(runs, series_id)
    if not path.is_file():
        raise ValueError(f"no series named {series_id}")
    return SeriesManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _save_manifest(runs: Path, manifest: SeriesManifest) -> None:
    atomic_write_text(_manifest_path(runs, manifest.series_id), manifest.model_dump_json(indent=2))


def load_workbench(runs: Path, series_id: str) -> Workbench | None:
    path = _workbench_path(runs, series_id)
    if not path.is_file():
        return None
    return Workbench.model_validate_json(path.read_text(encoding="utf-8"))


def _save_workbench(runs: Path, workbench: Workbench) -> None:
    atomic_write_text(_workbench_path(runs, workbench.series_id), workbench.model_dump_json(indent=2))


def list_series(runs: Path) -> list[SeriesManifest]:
    root = Path(runs).resolve() / SERIES_DIR
    if not root.is_dir():
        return []
    manifests = []
    for path in sorted(root.glob("*/series.json")):
        try:
            manifests.append(SeriesManifest.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return manifests


# -- membership ----------------------------------------------------------------


def _book_workspace(runs: Path, job_id: str) -> JobWorkspace:
    if job_id.startswith(".") or "/" in job_id or "\\" in job_id:
        raise ValueError(f"invalid job id: {job_id!r}")
    return open_job_workspace(Path(runs).resolve() / job_id)


def _book_direction(workspace: JobWorkspace) -> TranslationDirection:
    from .workflow import load_workspace_config  # local: workflow imports stages

    return load_workspace_config(workspace).translation.direction


def create_series(runs: Path, series_id: str, name: str, direction: str) -> SeriesManifest:
    root = series_root(runs, series_id)
    if (root / "series.json").exists():
        raise ValueError(f"a series named {series_id} already exists")
    manifest = SeriesManifest(
        series_id=series_id,
        name=name.strip() or series_id,
        direction=TranslationDirection(direction),
    )
    root.mkdir(parents=True, exist_ok=True)
    _save_manifest(runs, manifest)
    return manifest


def add_books(
    runs: Path,
    series_id: str,
    job_ids: list[str],
    *,
    drafts: dict[str, str] | None = None,
) -> SeriesManifest:
    """Add jobs as volumes, in order; each must translate in the series direction.

    ``drafts`` maps not-yet-started jobs (dashboard drafts, no workspace yet) to
    the direction their config declares; they join now and keep their volume.
    """
    manifest = load_manifest(runs, series_id)
    present = {book.job_id for book in manifest.books}
    elsewhere = {
        book.job_id: other.series_id
        for other in list_series(runs)
        if other.series_id != series_id
        for book in other.books
    }
    next_volume = max((book.volume or 0 for book in manifest.books), default=0) + 1
    for job_id in job_ids:
        if job_id in present:
            raise ValueError(f"{job_id} is already in series {series_id}")
        if job_id in elsewhere:
            raise ValueError(f"{job_id} is already in series {elsewhere[job_id]}")
        if drafts and job_id in drafts and not (Path(runs).resolve() / job_id).exists():
            if drafts[job_id] != manifest.direction.value:
                raise ValueError(
                    f"{job_id}'s config translates {drafts[job_id] or 'an unreadable direction'}; "
                    f"series {series_id} is {manifest.direction.value}"
                )
            manifest.books.append(SeriesBook(job_id=job_id, volume=next_volume, added_at=_now()))
            present.add(job_id)
            next_volume += 1
            continue
        workspace = _book_workspace(runs, job_id)
        direction = _book_direction(workspace)
        if direction is not manifest.direction:
            raise ValueError(
                f"{job_id} translates {direction.value}; series {series_id} is {manifest.direction.value}"
            )
        binding = load_series_binding(workspace.root)
        if binding is not None and binding.series_id != series_id:
            raise ValueError(f"{job_id} is already bound to series {binding.series_id}")
        manifest.books.append(SeriesBook(job_id=job_id, volume=next_volume, added_at=_now()))
        present.add(job_id)
        next_volume += 1
    _save_manifest(runs, manifest)
    return manifest


def book_boundary(workspace: JobWorkspace) -> Literal["approved", "resolved"] | None:
    """Which book glossary a series build can read: approved, else the resolved draft."""
    connection = connect_state(workspace.state_file)
    try:
        approve = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
        resolve = get_stage_status(connection, WorkflowStage.RESOLVE_GLOSSARY.value)
    finally:
        connection.close()
    if approve and approve["status"] == StageStatus.COMPLETED.value:
        return "approved"
    if resolve and resolve["status"] == StageStatus.COMPLETED.value:
        return "resolved"
    return None


def _book_sources(
    runs: Path, manifest: SeriesManifest
) -> tuple[list[GlossarySource], dict[str, Literal["approved", "resolved"]], list[str]]:
    from .stages.glossary import load_approved_glossary, load_glossary_draft

    sources: list[GlossarySource] = []
    boundaries: dict[str, Literal["approved", "resolved"]] = {}
    not_ready: list[str] = []
    for book in manifest.books:
        workspace = _book_workspace(runs, book.job_id)
        boundary = book_boundary(workspace)
        if boundary is None:
            not_ready.append(book.job_id)
            continue
        loaded = load_approved_glossary(workspace) if boundary == "approved" else load_glossary_draft(workspace)
        screened, _ = screen_glossary_candidates(
            loaded, remove_ordinary_terms=boundary == "resolved"
        )
        sources.append(
            GlossarySource(
                name=book.job_id, kind=GlossarySourceKind.SERIES, entries=tuple(screened.entries)
            )
        )
        boundaries[book.job_id] = boundary
    return sources, boundaries, not_ready


# -- source text ---------------------------------------------------------------

MENTION_CAP = 999


def book_texts(runs: Path, job_ids: list[str]) -> dict[str, str]:
    """Each book's decompiled source text, for term occurrence evidence."""
    from .stages.decompile import load_decompile_manifest

    texts = {}
    for job_id in job_ids:
        try:
            manifest = load_decompile_manifest(_book_workspace(runs, job_id))
        except (FileNotFoundError, ValueError, OSError):
            continue
        texts[job_id] = "\n".join(
            segment.text for document in manifest.documents for segment in document.segments
        )
    return texts


def _mention_positions(text: str, term: str, limit: int):
    """Start offsets of ``term`` as a whole word or phrase (not inside a longer word)."""
    start = 0
    found = 0
    while found < limit:
        index = text.find(term, start)
        if index < 0:
            return
        end = index + len(term)
        before = text[index - 1] if index else " "
        after = text[end] if end < len(text) else " "
        if not before.isalpha() and not after.isalpha():
            found += 1
            yield index
        start = index + 1


def count_mentions(text: str, term: str) -> int:
    return sum(1 for _ in _mention_positions(text, term, MENTION_CAP))


def mention_snippets(text: str, term: str, limit: int = 3, width: int = 110) -> list[str]:
    """Up to ``limit`` passages around the term, trimmed to whole words where possible."""
    snippets = []
    for index in _mention_positions(text, term, limit):
        start = max(0, index - width)
        end = min(len(text), index + len(term) + width)
        passage = text[start:end].replace("\n", " ").strip()
        snippets.append(("…" if start else "") + passage + ("…" if end < len(text) else ""))
    return snippets


# -- workbench -----------------------------------------------------------------


def build_workbench(
    runs: Path,
    series_id: str,
    *,
    minimum_books: int = 2,
    consensus_ratio: float = 1.0,
) -> Workbench:
    """Build the next version's candidate from the member books.

    Terms of the latest version are carried and locked. Promoted terms that
    agree with them merge into the carried row; a different translation becomes
    a conflict. Earlier user and accepted-LLM decisions survive a rebuild when the
    term's variants are unchanged.
    """
    manifest = load_manifest(runs, series_id)
    sources, boundaries, not_ready = _book_sources(runs, manifest)
    if not sources:
        raise ValueError("no member book has a resolved or approved glossary yet")
    build = build_series_glossary(
        sources,
        minimum_sources=minimum_books,
        consensus_ratio=consensus_ratio,
        defer_generic_terms="resolved" in boundaries.values(),
    )

    per_term: dict[str, dict[str, list[GlossaryEntry]]] = defaultdict(lambda: defaultdict(list))
    for source in sources:
        for entry in source.entries:
            per_term[normalize_term(entry.english)][source.name].append(entry)

    def books_of(key: str) -> dict[str, list[str]]:
        return {
            job: list(dict.fromkeys(entry.chinese for entry in entries))
            for job, entries in sorted(per_term.get(key, {}).items())
        }

    def representative(key: str) -> GlossaryEntry:
        entries = [entry for group in per_term[key].values() for entry in group]
        preferred = _preferred_text(entry.english for entry in entries)
        return next(entry for entry in entries if entry.english == preferred)

    rows: dict[str, WorkbenchTerm] = {}
    latest = manifest.latest
    if latest is not None:
        carried = load_glossary_file(version_glossary_path(runs, series_id, latest.version))
        for entry in carried.entries:
            key = normalize_term(entry.english)
            rows[key] = WorkbenchTerm(
                term_id="",
                english=entry.english,
                chinese=entry.chinese,
                category=entry.category,
                aliases=list(entry.aliases),
                note=entry.note,
                origin="carried",
                books=books_of(key),
                decision="keep",
                decided_by="rule",
                reason=f"published in {latest.version}",
                locked_from=latest.version,
            )

    for entry in build.glossary.entries:
        key = normalize_term(entry.english)
        if key in rows:
            carried_row = rows[key]
            if normalize_term(carried_row.chinese) != normalize_term(entry.chinese):
                rows[key] = carried_row.model_copy(
                    update={
                        "origin": "conflict",
                        "decision": "pending",
                        "reason": (
                            f"books agree on {entry.chinese}, but {carried_row.locked_from} "
                            f"has {carried_row.chinese}"
                        ),
                    }
                )
            continue
        rows[key] = WorkbenchTerm(
            term_id="",
            english=entry.english,
            chinese=entry.chinese,
            category=entry.category,
            aliases=list(entry.aliases),
            note=entry.note,
            origin="consensus",
            books=books_of(key),
            decision="keep",
            decided_by="rule",
            reason=f"same translation in {len(per_term[key])} books",
        )

    for conflict in build.report.conflicts:
        key = normalize_term(conflict.english)
        if key in rows:
            continue  # a carried term stays locked; its row lists the book variants
        entry = representative(key)
        rows[key] = WorkbenchTerm(
            term_id="",
            english=conflict.english,
            chinese=conflict.variants[0].chinese,
            category=entry.category,
            aliases=list(entry.aliases),
            note=entry.note,
            origin="conflict",
            books=books_of(key),
            decision="pending",
            decided_by="rule",
            reason=conflict.reason.replace("_", " "),
        )

    for key, by_book in per_term.items():
        if key in rows:
            continue
        entry = representative(key)
        rows[key] = WorkbenchTerm(
            term_id="",
            english=entry.english,
            chinese=entry.chinese,
            category=entry.category,
            aliases=list(entry.aliases),
            note=entry.note,
            origin="single_book",
            books=books_of(key),
            decision="drop",
            decided_by="rule",
            reason=(
                "appears in one book; stays in its book glossary"
                if len(by_book) < minimum_books
                else "not promoted by the consensus rule"
            ),
        )

    previous = load_workbench(runs, series_id)
    if previous is not None:
        prior = {normalize_term(term.english): term for term in previous.terms}
        for key, row in rows.items():
            old = prior.get(key)
            if (
                old is not None
                and old.decided_by != "rule"
                and set(old.variants()) == set(row.variants())
                and old.locked_from == row.locked_from
            ):
                rows[key] = row.model_copy(
                    update={
                        field: getattr(old, field)
                        for field in ("chinese", "category", "aliases", "note", "decision", "decided_by", "reason", "origin")
                    }
                )
            elif old is not None and old.decided_by == "rule" and (old.suggestion or old.dismissed):
                rows[key] = row.model_copy(update={"suggestion": old.suggestion, "dismissed": old.dismissed})
        for key, old in prior.items():
            if old.origin == "manual" and key not in rows:
                rows[key] = old

    texts = book_texts(runs, [book.job_id for book in manifest.books])
    for key, row in rows.items():
        rows[key] = row.model_copy(
            update={
                "mentions": {
                    job: count
                    for job, text in texts.items()
                    if (count := count_mentions(text, row.english))
                }
            }
        )

    ordered = sorted(rows.items(), key=lambda item: item[0])
    workbench = Workbench(
        series_id=series_id,
        based_on=latest.version if latest else None,
        built_at=_now(),
        source_jobs=[source.name for source in sources],
        boundaries=boundaries,
        not_ready=not_ready,
        minimum_books=minimum_books,
        terms=[row.model_copy(update={"term_id": f"T{index:05d}"}) for index, (_, row) in enumerate(ordered, 1)],
    )
    _save_workbench(runs, workbench)
    return workbench


def decide_terms(
    runs: Path,
    series_id: str,
    term_ids: list[str],
    decision: Decision,
    *,
    reason: str,
    chinese: str | None = None,
    category: GlossaryCategory | str | None = None,
    unlock: bool = False,
    decided_by: DecidedBy = "user",
) -> Workbench:
    """Record a manual (or accepted LLM) decision on workbench terms.

    ``chinese`` and ``category`` change the term itself, so they apply to one term.
    """
    workbench = load_workbench(runs, series_id)
    if workbench is None:
        raise ValueError("build the series workbench first")
    if len(reason.strip()) < 3:
        raise ValueError("a decision needs a reason (at least 3 characters)")
    if (chinese is not None or category is not None) and len(term_ids) != 1:
        raise ValueError("a new translation or category applies to one term at a time")
    if category is not None and not isinstance(category, GlossaryCategory):
        # The stored value (e.g. 地名) or its English name (place).
        by_name = {item.name.casefold(): item for item in GlossaryCategory}
        try:
            category = GlossaryCategory(category)
        except ValueError:
            if str(category).casefold() not in by_name:
                raise ValueError(f"unknown glossary category: {category}") from None
            category = by_name[str(category).casefold()]
    by_id = {term.term_id: term for term in workbench.terms}
    missing = [term_id for term_id in term_ids if term_id not in by_id]
    if missing:
        raise ValueError("unknown term IDs: " + ", ".join(missing))
    for term_id in term_ids:
        term = by_id[term_id]
        changes_published = term.locked_from and (
            decision == "drop"
            or (chinese is not None and chinese != term.chinese)
            or (category is not None and category is not term.category)
        )
        if changes_published and not unlock:
            raise ValueError(
                f"{term.english} was published in {term.locked_from}; unlock it to change it"
            )
        update: dict[str, object] = {
            "decision": decision,
            "decided_by": decided_by,
            "reason": reason.strip(),
            "suggestion": None,
        }
        if chinese is not None:
            GlossaryEntry(english=term.english, chinese=chinese, category=term.category)
            update["chinese"] = chinese
        if category is not None:
            update["category"] = category
        by_id[term_id] = term.model_copy(update=update)
    workbench = workbench.model_copy(
        update={"terms": [by_id[term.term_id] for term in workbench.terms]}
    )
    _save_workbench(runs, workbench)
    return workbench


# -- publishing ----------------------------------------------------------------


def _next_version(manifest: SeriesManifest) -> str:
    return f"v{len(manifest.versions) + 1:03d}"


def _write_version(
    runs: Path,
    manifest: SeriesManifest,
    glossary: GlossaryResult,
    report: dict[str, object],
    source_jobs: list[str],
) -> SeriesVersion:
    latest = manifest.latest
    if latest is not None:
        previous = load_glossary_file(version_glossary_path(runs, manifest.series_id, latest.version))
        if previous.model_dump_json() == glossary.model_dump_json():
            raise ValueError(f"nothing changed since {latest.version}; no new version published")
    version = _next_version(manifest)
    path = version_glossary_path(runs, manifest.series_id, version)
    if path.exists():
        raise ValueError(f"{path} already exists; published versions are never rewritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, glossary.model_dump_json(indent=2))
    atomic_write_text(
        path.with_name(f"{version}.report.json"),
        json.dumps({"version": version, **report}, ensure_ascii=False, indent=2),
    )
    record = SeriesVersion(
        version=version,
        created_at=_now(),
        source_jobs=source_jobs,
        term_count=len(glossary.entries),
        glossary_sha256=sha256_file(path),
        based_on=latest.version if latest else None,
    )
    manifest.versions.append(record)
    _save_manifest(runs, manifest)
    return record


def publish_workbench(runs: Path, series_id: str) -> SeriesVersion:
    """Freeze the kept workbench terms into the next immutable version.

    Pending and dropped terms are left out and listed in the version report;
    each member book's glossary is also synchronized to the new version as an
    approval overlay under ``overlays/<version>/``.
    """
    manifest = load_manifest(runs, series_id)
    workbench = load_workbench(runs, series_id)
    if workbench is None:
        raise ValueError("build the series workbench first")
    if workbench.based_on != (manifest.latest.version if manifest.latest else None):
        raise ValueError("the workbench predates the latest version; rebuild it")
    kept = [term for term in workbench.terms if term.decision == "keep"]
    entries = [
        GlossaryEntry(
            english=term.english,
            chinese=term.chinese,
            note=term.note,
            category=term.category,
            aliases=term.aliases,
            evidence=[f"series:{job}" for job in sorted(term.books)],
        )
        for term in kept
    ]
    keys = [normalize_term(entry.english) for entry in entries]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError("duplicate English terms: " + ", ".join(duplicates))
    glossary = GlossaryResult(
        entries=sort_glossary_entries(_remove_cross_term_alias_conflicts(entries))
    )
    report = {
        "series_id": series_id,
        "source_jobs": workbench.source_jobs,
        "boundaries": workbench.boundaries,
        "term_count": len(glossary.entries),
        "pending_excluded": [term.english for term in workbench.terms if term.decision == "pending"],
        "dropped_count": sum(term.decision == "drop" for term in workbench.terms),
        "decisions": [
            {
                "english": term.english,
                "chinese": term.chinese,
                "origin": term.origin,
                "decision": term.decision,
                "decided_by": term.decided_by,
                "reason": term.reason,
            }
            for term in workbench.terms
            if term.decided_by != "rule" or term.decision == "keep"
        ],
    }
    record = _write_version(runs, manifest, glossary, report, workbench.source_jobs)
    write_book_overlays(runs, series_id, record.version)
    # Keep a workbench for the next version: the published terms are carried and
    # locked, and decisions a person made are preserved.
    try:
        build_workbench(runs, series_id, minimum_books=workbench.minimum_books)
    except ValueError:
        _workbench_path(runs, series_id).unlink(missing_ok=True)
    return record


def write_book_overlays(runs: Path, series_id: str, version: str) -> dict[str, Path]:
    """Each ready book's glossary synchronized to ``version``, for its approval."""
    manifest = load_manifest(runs, series_id)
    sources, _, _ = _book_sources(runs, manifest)
    if not sources:
        return {}
    glossary = load_glossary_file(version_glossary_path(runs, series_id, version))
    overlays, reports = synchronize_book_glossaries(sources, glossary)
    written, _ = write_series_book_overlays(
        overlays, series_root(runs, series_id) / "overlays" / version, reports
    )
    return {job: paths[0] for job, paths in written.items()}


def import_version(runs: Path, series_id: str, glossary_file: Path) -> SeriesVersion:
    """Publish an existing series glossary file (e.g. from the CLI workflow)."""
    manifest = load_manifest(runs, series_id)
    glossary = load_glossary_file(glossary_file)
    report = {"series_id": series_id, "imported_from": str(Path(glossary_file).resolve())}
    return _write_version(runs, manifest, glossary, report, [])


# -- binding -------------------------------------------------------------------


def bind_book(
    runs: Path,
    series_id: str,
    job_id: str,
    version: str | None = None,
    *,
    upgrade: bool = False,
) -> dict[str, object]:
    """Pin a member book to a version (default: the latest).

    Before preprocessing has completed this is free. Afterwards it changes the
    book's glossary, so it needs ``upgrade`` and resets preprocessing and every
    later stage (the book is translated again on resume).
    """
    from .workflow import retry_from_stage  # local: workflow imports stages

    manifest = load_manifest(runs, series_id)
    if job_id not in {book.job_id for book in manifest.books}:
        raise ValueError(f"{job_id} is not in series {series_id}")
    target = version or (manifest.latest.version if manifest.latest else None)
    if target is None or target not in {item.version for item in manifest.versions}:
        raise ValueError(f"series {series_id} has no version {target or '(none published)'}")
    workspace = _book_workspace(runs, job_id)
    current = load_series_binding(workspace.root)
    if current is not None and current.series_id == series_id and current.version == target:
        return {"job_id": job_id, "version": target, "changed": False, "reset": []}
    connection = connect_state(workspace.state_file)
    try:
        preprocess = get_stage_status(connection, WorkflowStage.PREPROCESS.value)
    finally:
        connection.close()
    preprocessed = bool(preprocess and preprocess["status"] == StageStatus.COMPLETED.value)
    if preprocessed and not upgrade:
        raise ValueError(
            f"{job_id} is already preprocessed; binding it to {target} re-translates it "
            "(use --upgrade to confirm)"
        )
    write_series_binding(
        workspace.root, series_id, target, version_glossary_path(runs, series_id, target)
    )
    reset = [stage.value for stage in retry_from_stage(workspace, WorkflowStage.PREPROCESS)] if preprocessed else []
    return {"job_id": job_id, "version": target, "changed": True, "reset": reset}


def series_status(runs: Path, series_id: str) -> dict[str, object]:
    """Manifest plus each book's glossary boundary and pinned version."""
    manifest = load_manifest(runs, series_id)
    books = []
    for book in manifest.books:
        try:
            workspace = _book_workspace(runs, book.job_id)
            binding = load_series_binding(workspace.root)
            boundary = book_boundary(workspace)
        except (ValueError, OSError):
            draft = Path(runs).resolve() / ".drafts" / f"{book.job_id}.json"
            binding, boundary = None, "draft" if draft.is_file() else "missing"
        books.append(
            {
                **book.model_dump(),
                "glossary": boundary or "not ready",
                "version": binding.version if binding and binding.series_id == series_id else None,
            }
        )
    workbench = load_workbench(runs, series_id)
    return {
        **json.loads(manifest.model_dump_json()),
        "books": books,
        "workbench": (
            {
                "based_on": workbench.based_on,
                "built_at": workbench.built_at,
                "terms": len(workbench.terms),
                "pending": sum(term.decision == "pending" for term in workbench.terms),
                "keep": sum(term.decision == "keep" for term in workbench.terms),
            }
            if workbench
            else None
        ),
    }


def remove_book(runs: Path, series_id: str, job_id: str) -> SeriesManifest:
    """Remove a member book that is not yet pinned to a version."""
    manifest = load_manifest(runs, series_id)
    if job_id not in {book.job_id for book in manifest.books}:
        raise ValueError(f"{job_id} is not in series {series_id}")
    try:
        binding = load_series_binding(_book_workspace(runs, job_id).root)
    except (ValueError, OSError):
        binding = None  # the job folder is gone; removing it is always safe
    if binding is not None and binding.series_id == series_id:
        raise ValueError(f"{job_id} is pinned to {binding.version}; a bound book stays in its series")
    manifest.books = [book for book in manifest.books if book.job_id != job_id]
    _save_manifest(runs, manifest)
    return manifest


def series_summaries(runs: Path) -> list[dict[str, object]]:
    """One row per series for the dashboard list."""
    rows = []
    for manifest in list_series(runs):
        workbench = load_workbench(runs, manifest.series_id)
        rows.append(
            {
                "series_id": manifest.series_id,
                "name": manifest.name,
                "direction": manifest.direction.value,
                "books": len(manifest.books),
                "latest": manifest.latest.version if manifest.latest else None,
                "pending": (
                    sum(term.decision == "pending" for term in workbench.terms) if workbench else None
                ),
            }
        )
    return rows


def series_of_job(runs: Path, job_id: str) -> dict[str, object] | None:
    """The series a job belongs to, with its pinned version (None before binding)."""
    for manifest in list_series(runs):
        if job_id in {book.job_id for book in manifest.books}:
            try:
                binding = load_series_binding(_book_workspace(runs, job_id).root)
            except (ValueError, OSError):
                binding = None
            return {
                "series_id": manifest.series_id,
                "name": manifest.name,
                "version": binding.version if binding and binding.series_id == manifest.series_id else None,
                "latest": manifest.latest.version if manifest.latest else None,
            }
    return None


def addable_jobs(runs: Path, series_id: str) -> list[dict[str, object]]:
    """Jobs in the series direction that belong to no series yet."""
    manifest = load_manifest(runs, series_id)
    taken = {book.job_id for series in list_series(runs) for book in series.books}
    rows = []
    for path in sorted(Path(runs).resolve().iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_dir() or path.name.startswith(".") or path.name in taken:
            continue
        try:
            workspace = open_job_workspace(path)
            if _book_direction(workspace) is not manifest.direction:
                continue
            boundary = book_boundary(workspace)
        except (ValueError, OSError):
            continue
        rows.append(
            {"job_id": path.name, "source": workspace.source_file.name, "glossary": boundary or "not ready"}
        )
    return rows


def overlay_for_job(runs: Path, job_id: str) -> dict[str, object] | None:
    """The series overlay a book at its glossary gate should approve, if any.

    The version is the book's pin, else the series' latest. The overlay is
    written on demand for books that became ready after that version was
    published. Returns None outside a series or before any version exists.
    """
    membership = series_of_job(runs, job_id)
    if membership is None:
        return None
    version = membership["version"] or membership["latest"]
    if version is None:
        return None
    series_id = str(membership["series_id"])
    path = series_root(runs, series_id) / "overlays" / str(version) / f"{job_id}.glossary.review.json"
    if not path.is_file():
        written = write_book_overlays(runs, series_id, str(version))
        if job_id not in written:
            return None
        path = written[job_id]
    return {
        "series_id": series_id,
        "name": membership["name"],
        "version": version,
        "path": str(path),
        "entries": json.loads(path.read_text(encoding="utf-8"))["entries"],
    }


_PG_SUFFIX = re.compile(r"[ .]pg\d+.*$", re.IGNORECASE)


def book_title(workspace: JobWorkspace) -> str:
    """A readable title from the source file name (Project Gutenberg suffixes dropped)."""
    return _PG_SUFFIX.sub("", workspace.source_file.stem).strip() or workspace.root.name


def series_books(runs: Path, series_id: str) -> list[dict[str, object]]:
    """Member books in volume order with a short title for the workbench."""
    rows = []
    for book in load_manifest(runs, series_id).books:
        try:
            title = book_title(_book_workspace(runs, book.job_id))
        except (ValueError, OSError):
            title = book.job_id
        rows.append({"job_id": book.job_id, "volume": book.volume, "title": title})
    return rows


def term_evidence(runs: Path, series_id: str, term_id: str) -> dict[str, object]:
    """For one workbench term, what every member book says about it.

    Per book: its glossary entries for the term (with their evidence sentences),
    how often the term occurs in its source text, and a few passages. A book whose
    glossary missed the term can still show mentions: evidence for promoting it.
    """
    from .stages.decompile import load_decompile_manifest

    workbench = load_workbench(runs, series_id)
    if workbench is None:
        raise ValueError("build the series workbench first")
    term = next((item for item in workbench.terms if item.term_id == term_id), None)
    if term is None:
        raise ValueError(f"unknown term ID: {term_id}")
    manifest = load_manifest(runs, series_id)
    sources, _, _ = _book_sources(runs, manifest)
    entries_by_book = {
        source.name: [entry for entry in source.entries if normalize_term(entry.english) == normalize_term(term.english)]
        for source in sources
    }
    books = []
    for row in series_books(runs, series_id):
        job_id = str(row["job_id"])
        try:
            decompiled = load_decompile_manifest(_book_workspace(runs, job_id))
        except (FileNotFoundError, ValueError, OSError):
            books.append({**row, "glossary": [], "mentions": 0, "snippets": []})
            continue
        segments = {
            segment.segment_id: segment.text
            for document in decompiled.documents
            for segment in document.segments
        }
        text = "\n".join(segments.values())
        books.append(
            {
                **row,
                "glossary": [
                    {
                        "chinese": entry.chinese,
                        "category": entry.category.value,
                        "note": entry.note,
                        "evidence": [segments[ref][:400] for ref in entry.evidence[:2] if ref in segments],
                    }
                    for entry in entries_by_book.get(job_id, [])
                ],
                "mentions": count_mentions(text, term.english),
                "snippets": mention_snippets(text, term.english),
            }
        )
    return {"term_id": term.term_id, "english": term.english, "books": books}


def workbench_view(runs: Path, series_id: str) -> dict[str, object] | None:
    """Workbench terms plus what publishing them would change versus the latest version."""
    workbench = load_workbench(runs, series_id)
    if workbench is None:
        return None
    manifest = load_manifest(runs, series_id)
    kept = {normalize_term(term.english): term for term in workbench.terms if term.decision == "keep"}
    previous: dict[str, GlossaryEntry] = {}
    if manifest.latest is not None:
        published = load_glossary_file(version_glossary_path(runs, series_id, manifest.latest.version))
        previous = {normalize_term(entry.english): entry for entry in published.entries}
    added = sorted(term.english for key, term in kept.items() if key not in previous)
    changed = sorted(
        term.english
        for key, term in kept.items()
        if key in previous and normalize_term(previous[key].chinese) != normalize_term(term.chinese)
    )
    removed = sorted(entry.english for key, entry in previous.items() if key not in kept)
    return {
        **json.loads(workbench.model_dump_json()),
        "next_version": _next_version(manifest),
        "latest": manifest.latest.version if manifest.latest else None,
        "books": [book.job_id for book in manifest.books],
        "book_info": series_books(runs, series_id),
        "publish": {
            "keep": len(kept),
            "pending": sum(term.decision == "pending" for term in workbench.terms),
            "added": added,
            "changed": changed,
            "removed": removed,
            "stale": workbench.based_on != (manifest.latest.version if manifest.latest else None),
        },
    }
