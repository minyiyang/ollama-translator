# XLIFF import: design

Status: **implemented.** The Text tab's **⤒ Import XLIFF** button (beside
Export) uploads a file into review mode; the backend is
`book_agent/xliff_import.py`. Last updated: 2026-09-28.

Import an XLIFF file that went through a CAT tool (memoQ, Trados, OmegaT, …)
back into a job. Each changed segment becomes an ordinary tracked `edit` in
the edit log (docs/FULL_TEXT_REVIEW.md), so history, revert, conflicts, the
compile overlay, and Recompile all work unchanged. Export is the other half
and already exists (Text tab **⤓ Export XLIFF**).

## 1. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | What identifies a segment | The unit `id` **and** its source text. No fuzzy re-matching: writing a translation into the wrong segment is worse than skipping one. |
| 2 | Segments that fail | Skip them and report why; import the rest. Nothing is written until you confirm. |
| 3 | Segment changed since export (a rerun, or a newer edit in the Text tab) | Skipped by default, with an opt-in to import anyway. |
| 4 | Where the preview lives | A review mode of the Text tab, not a modal: the chapter sidebar, paired rows, filters, and diffs are already there. |
| 5 | How the check runs | Each chapter is audited once, as the draft the import would produce. |
| 6 | Where the preview is kept | On the server, so a page reload resumes the review; it is recomputed from the uploaded units whenever it is shown or applied. |

## 2. Export changes that import relies on

- **Namespace fix.** XLIFF 2.1 keeps the 2.0 core namespace:
  `<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.1">`. The
  first export used `…:document:2.1`, which CAT tools may reject. Import
  still accepts that namespace so files exported before the fix work.
- **Export metadata** in the standard Metadata module
  (`urn:oasis:names:tc:xliff:metadata:2.0`), which the XLIFF spec says tools
  **must** preserve (custom extensions only *should* be):
  - per `<file>`: `metaGroup category="book-agent"` with `source_sha256` (the
    captured source book) and `job_id`;
  - per `<unit>`: `base_target_sha256` (the pipeline text the export was
    based on) and `edit_revision` (the segment's last edit event, omitted
    when it has none).

## 3. Reading the file

- Rejected outright, with nothing previewed:
  - not well-formed XML, or a `<!DOCTYPE>`/`<!ENTITY>` declaration (XLIFF
    needs neither, and refusing them blocks entity-expansion attacks);
  - not XLIFF 2 (XLIFF 1.2 gets a message asking for a 2.x export);
  - `srcLang`/`trgLang` whose primary language differs from the job's
    direction (`zh-CN` matches `zh`);
  - a `source_sha256` that differs from this job's source book ("exported
    from a different book");
  - a duplicated unit `id`.
- A file with no book-agent metadata at all is accepted with a warning; its
  units cannot be proven current, so they count as stale (section 4).
- Units are read from any depth (tools may wrap them in `<group>`). A unit a
  tool split into several `<segment>`s is joined back in order, including
  `<ignorable>` whitespace between them.
- Inline content: `<pc id="000">…</pc>` becomes our `<I000>…</I000>` marker;
  `<mrk>` annotations are transparent; `<cp hex>` becomes its character. Any
  other inline code (`<ph>`, `<sc>`/`<ec>`, a `<pc>` with a foreign id) makes
  the unit **unsupported markup** rather than guessing where it belongs.

## 4. Classifying each unit

The first matching row decides a unit's category.

| Category | Condition | Default |
|---|---|---|
| unknown ID | the `id` is not a segment of this book | skip |
| unsupported markup | inline content that cannot map to our markers | skip |
| source differs | source text differs after marker conversion, Unicode NFC, and whitespace collapsing | skip |
| no target | the target is missing or blank | skip |
| unchanged | the target equals the current text (same normalization) | nothing to do |
| edited since export | the segment's edit revision differs from the exported one | skip, opt-in |
| stale | the pipeline text changed since export, or the unit has no export metadata | skip, opt-in |
| fails checks | a hard check finding (e.g. a missing inline marker) | skip, always |
| needs override | only overridable findings (e.g. naturalness) | skip, opt-in; your import reason is recorded as the override |
| import | everything else | import |

- A book segment absent from the file is untouched: import only adds edits.
- The check is the same deterministic check a Text tab edit runs, but done
  per chapter with every changed unit in it applied at once, so imported
  segments are checked next to each other rather than next to the old
  pipeline text. For a single edit this is exactly the per-edit check.
  Findings are computed for stale and edited-since-export units too, so an
  opted-in unit that fails a check is still skipped.

## 5. Applying

- One reason for the whole import, at least 3 characters. Each event's
  reason is `<your reason> (imported from <file name>)`; the author is the
  dashboard's user as for any edit.
- Apply reclassifies against the current book, applies the opt-ins, then
  re-checks the selected units together and drops any that now fail
  (repeated until stable, since dropping one can change a neighbour's
  result). The remaining units are appended as **one batch under the edit
  lock** (`apply_edit_batch`): if the book changes at that exact moment the
  whole apply fails and nothing is written; preview again.
- Apply requires the validated draft (`validate_repaired` complete) and an
  idle job.

## 6. UI (Text tab)

**Starting:** **⤒ Import XLIFF** sits beside **⤓ Export XLIFF** in the totals
card and appears once the validated draft exists. It opens a file picker;
the card shows "Checking alice.en-zh.xlf…" while the server reads and checks
the file (about a second per ten chapters for a full-book file).

**Reviewing** (nothing written yet):

```
┌ Import preview · alice.en-zh.xlf ──────────────────────────────────┐
│ ● Will import 42   ○ Unchanged 820   ○ Skipped 13                    │
│   Skipped: stale 5 · edited since export 3 · source differs 2 ·      │
│            unknown ID 1 · fails checks 2          [Download report]  │
│ ☐ Also import the 5 stale segments  ☐ Also import the 3 edited…      │
│ Reason [Translator pass, Sept 2026_________]  [Import 42] [Cancel]   │
└──────────────────────────────────────────────────────────────────────┘
```

- The preview card replaces the totals card. Its counts filter the table
  (Will import / Skipped / Unchanged / Not in file / All) and the "Will
  import" count follows the opt-in checkboxes live.
- Rows show a diff from the current text to the imported text; skipped rows
  show why (the failed finding, both source texts, or what changed since
  export). Editing, history, and conflict actions are hidden until the
  review ends.
- The chapter sidebar shows how many segments each chapter would import.
- Units with an unknown ID belong to no chapter; they are listed in the card
  and in the downloadable CSV report.
- A page reload resumes the review. Uploading another file replaces a
  pending preview.

**After importing:** a toast confirms the count (and how many were skipped
at apply because something changed). The table returns to normal with the
imported rows now `edited`, and the Recompile banner appears. The card
collapses to a **Last import** summary listing the skipped segments until
dismissed; **Open** jumps to a skipped segment and, where there is imported
text, opens the editor pre-filled with it so you can fix it by hand.

## 7. Storage and API

Each import is `edits/imports/<import_id>.json` in the job folder: the file
name, the parsed units (ids, texts, export metadata), file-level warnings,
status (`preview`, `applied`, `cancelled`), and after applying the result and
a `dismissed` flag. Nothing here is read by the pipeline; the edit log stays
the only source of applied text.

| Route | Purpose |
|---|---|
| `GET /api/jobs/<id>/text/import` | `{pending, last}`: the pending preview (recomputed) and the latest applied, undismissed import |
| `POST /api/jobs/<id>/text/import` `{file_name, xliff}` | Read and check a file; store it as the pending preview; return the preview |
| `POST /api/jobs/<id>/text/import/apply` `{import_id, reason, include_stale, include_edited, include_overridable}` | Apply as in section 5; return the result |
| `POST /api/jobs/<id>/text/import/cancel` `{import_id}` | Discard a pending preview |
| `POST /api/jobs/<id>/text/import/dismiss` `{import_id}` | Hide the Last import summary |
| `GET /api/jobs/<id>/text/import/report?import_id=` | CSV download: every unit's outcome and reason |

The XLIFF text travels in the JSON body, so the dashboard's 8 MB request
limit applies (a full-book export is well under 1 MB).

## 8. Code layout

- `book_agent/text_edits.py`: `check_edits` (the per-chapter batch check);
  `apply_edit_batch` checks its batch with it, so a single edit behaves as
  before and a batch is checked as the draft it produces. Final review's
  `resolve_manual_review` checks its resolutions the same way.
- `book_agent/xliff_export.py`: namespace fix and export metadata.
- `book_agent/xliff_import.py`: parsing, classification, storage, apply. No
  web dependencies.
- `book_agent/web/server.py`: the routes above.
- `frontend/src/pages/TextPage.tsx` with the review mode;
  `frontend/src/lib/xliffImport.ts` for the "will import" predicate and
  per-chapter counts.

## 9. Tests

- Export: core 2.0 namespace with `version="2.1"`, file and unit metadata.
- Import parsing: groups, split segments with ignorable whitespace, `<pc>`
  nesting, `<mrk>`, `<cp>`, unsupported codes, DOCTYPE refusal, wrong
  language, wrong book, duplicate ids, missing metadata warning, the
  pre-fix namespace.
- Classification: every category in section 4, and the order between them.
- Apply: opt-ins, one reason with the file name, overrides recorded, the
  fixed-point re-check, a concurrent change failing the whole apply, status
  transitions, dismiss.
- `check_edits`: equals the single-edit check for one edit; checks a batch
  together (two imported segments that duplicate each other are caught).
- HTTP: every route (the route guard enforces the JSON ones) and the report
  download.
- Frontend: import button placement, upload and busy state, review-mode
  counts and filters, opt-ins updating the count live, apply request body,
  cancel, resume after reload, Last import with Open and Dismiss.

## 10. Not in this version

- Undo a whole import at once (per-segment revert already exists).
- CLI import (`book-agent edits <job> --import file.xlf`).
- XLIFF 1.2.
