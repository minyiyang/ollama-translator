# Full-text review and tracked manual edits: plan

Status: **plan, decided; not implemented.** Last updated: 2026-09-19.

A dashboard tab for reading the whole book as source and translation side by
side and editing any translated segment, with every manual edit tracked, kept
across pipeline reruns, and applied when the book is compiled.

## 1. Request

- A separate tab. On the left, the book's content (EPUB or RTF) with chapters
  that expand into segments. On the right, the source and translated text side
  by side.
- The translated text can be edited.
- Manual edits must be tracked, which needs a data format of their own.

## 2. How the final text flows today

- The final translation is the `validate_repaired` stage output, and compile
  reads it from there (`load_validated_repaired_documents`).
- Final review (`resolve-review`) writes its decisions into that same output,
  after deterministic checks. It already accepts edits outside the review
  queue when they carry new text and a reason.
- Because those edits live inside a stage's output, rerunning
  `validate_repaired` or anything earlier discards them, and there is no
  per-segment history.
- Size: *Robinson Crusoe* has 23 chapter documents and 832 segments
  (paragraphs), at most 81 per chapter.

## 3. Reference projects

| Project | What we take from it |
|---|---|
| OmegaT (open-source translation editor) | Edits stored outside the document (`project_save.tmx`), keyed to the segment with author and date, so they survive re-importing the source. |
| Weblate / Pootle | A state per unit (needs editing, translated, approved), full change history, quality checks shown inline. |
| XLIFF 2.x (Okapi, memoQ, Trados) | Standard bilingual format (`<unit><segment state=…><source/><target/>`, inline `<ph>`/`<pc>` tags). Our segments and `<I000>` markers map onto it; planned as an export format, not the store. |
| Calibre ebook editor | Edits the book's HTML directly, with no segment link and no tracking. The approach to avoid. |
| bilingual_book_maker, Immersive Translate | Side-by-side and interleaved bilingual layouts. |

## 4. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Where edits live | **An append-only edit log** in the job folder, owned by no stage, so reruns never wipe it. XLIFF export later. |
| 2 | How edits reach the book | **Applied when compiling**: compile builds the book from the validated draft plus active edits. No audit or LLM stage reruns. |
| 3 | A rerun changes the pipeline text under an edit | **The edit stays applied, and the segment becomes a conflict.** Conflicts count as unresolved segments and block compile until you unblock each one. |
| 4 | Checks when saving | **Deterministic check, warn, and require a reason to override** a blocking problem. No LLM re-check. |
| 5 | Final review | **Unchanged for now.** Its decisions become edit-log events in a later phase. |

## 5. Edit log format

File: `edits/segment-edits.jsonl` in the job folder. One JSON object per line,
appended and never rewritten. A per-job lock serializes writers (the dashboard
and any CLI command).

```json
{
  "schema_version": 1,
  "event_id": "E000042",
  "at": "2026-09-19T10:21:03-04:00",
  "author": "minyi",
  "segment_id": "D0004-S000015",
  "document_id": "chapter-4",
  "action": "edit",
  "text": "…大约二百二十个八里亚尔…",
  "previous_text": "…大约二百二十八里亚尔…",
  "base_source_sha256": "…",
  "base_target_sha256": "…",
  "reason": "Restored 220 pieces of eight.",
  "overrides": []
}
```

| Field | Meaning |
|---|---|
| `action` | `edit` (new text), `revert` (back to the pipeline text), `keep` (conflict unblocked, keeping the edit against the new pipeline text), `take_pipeline` (conflict unblocked by dropping the edit) |
| `text` | The segment's text after this event; empty for `revert` and `take_pipeline` |
| `previous_text` | What the reader saw before the event: the pipeline text or the prior edit |
| `base_source_sha256` | Hash of the segment's source text when the event was written |
| `base_target_sha256` | Hash of the **pipeline** text the edit was made against, not the prior edit. `keep` records the new pipeline text's hash. |
| `reason` | Required for every event |
| `overrides` | Blocking check messages the author saved over, with the reason covering them |
| `author` | The dashboard's reviewer name setting, defaulting to the OS user name |

**Current state of a segment** is its last event, compared with the current
pipeline text:

| State | Condition | Applied at compile |
|---|---|---|
| pipeline | no event, or last event is `revert` / `take_pipeline` | pipeline text |
| edited | last event is `edit`/`keep` and the pipeline text hash equals `base_target_sha256` | edit |
| **conflict** | last event is `edit`/`keep` and the pipeline text changed since | edit, but compile is blocked (section 7) |
| orphaned | the segment ID no longer exists, or its source hash changed | nothing; listed for information |

The full history of a segment is every event for its ID, oldest first.

## 6. Checks when saving

- An edit runs the same deterministic check as Final review
  (`preview_manual_resolution`): protected inline markers, leftover source
  language, numbers, empty text.
- A blocking problem is shown inline. Saving anyway needs a reason; the
  overridden messages are stored in `overrides`.
- The server rejects an edit whose `base_target_sha256` no longer matches the
  current pipeline text (the page is stale), and the page reloads that segment.

## 7. Compile, conflicts, and download

- **Applying edits:** compile overlays the active edits (edited and conflict
  states) on the validated draft. The compile input hash includes a hash of
  the active edits, so any change to them makes the compiled book outdated.
- **Conflicts block compile like unresolved segments:**
  - they are added to the compile gate's unresolved list and count against
    `workflow.compile_max_unresolved_review_segments`;
  - the pause message lists them as "edit conflicts" next to review defects;
  - each is unblocked in the Text tab by **Keep my edit** (a `keep` event),
    **Use the new pipeline text** (`take_pipeline`), or a new edit. Every
    choice needs a reason.
- **Edits after a completed compile:** the job header shows "N edits not in
  the compiled book" with **Recompile**, which reruns only `compile` and
  `validate_epub` (seconds, no LLM calls). Download keeps serving the last
  compiled file, with that notice beside it.
- **Reruns:** the rerun warning (docs/STAGE_CONTROL.md) adds "Manual text edits
  are kept; segments whose translation changes become conflicts."

Interim limitation until Final review is unified (phase 5): an edit does not
resolve a segment in the Final review queue. The Text tab marks queued
segments and links to Final review; the overlay is applied after Final review
decisions.

## 8. UI: the Text tab

Placed between Progress and Final review. Available once a translation exists:

- read-only before `validate_repaired` completes, showing the latest
  translation stage;
- editable afterwards, since edits are based on the validated draft.

**Left: book outline**

- Chapters in book order, with title and segment count, expandable into
  segments (ID and first words).
- Status dots per segment: edited, conflict, flagged by the audit or in the
  Final review queue.
- Filters: all, edited, conflicts, flagged. Search over source or translation
  with match counts per chapter.
- Its own scroll area, like the other sidebars.

**Right: the selected chapter as paired rows**

- Source on the left, translation on the right, one row per segment, aligned.
- Clicking a translation opens an in-place editor:
  - protected inline markers appear as chips that cannot be deleted;
  - the check result shows below the editor;
  - saving needs a reason, preset or custom as in Final review;
  - Ctrl+Enter saves, Esc cancels, and Alt+↑/↓ moves between rows.
- An edited row shows a diff against the pipeline text and a history popover
  (author, time, reason, text of each event) with **Revert**.
- A conflict row shows three texts: your edit, the text it was based on, and
  the new pipeline text, with the unblock actions.

**Header:** the tab shows a dot when conflicts exist, and a badge "N edits not
in the compiled book → Recompile" once the book has been compiled.

## 9. API

| Route | Purpose |
|---|---|
| `GET /api/jobs/<id>/text` | Outline: chapters (`document_id`, title, order, segment count, edited, conflicts, flagged), totals, `editable`, edits not yet compiled |
| `GET /api/jobs/<id>/text/<document_id>` | One chapter: per segment `segment_id`, `source`, `pipeline_text`, `text`, `state`, `base_target_sha256`, findings, in-review-queue |
| `POST /api/jobs/<id>/text/check` `{segment_id, text}` | Blocking and advisory check results |
| `POST /api/jobs/<id>/text/edit` `{segment_id, text, reason, base_target_sha256, override_reason?}` | Appends an `edit`; rejected when the base is stale or a blocking problem has no override reason |
| `POST /api/jobs/<id>/text/revert` `{segment_id, reason}` | Appends a `revert` |
| `POST /api/jobs/<id>/text/conflict` `{segment_id, choice: keep\|take_pipeline, reason}` | Unblocks a conflict |
| `GET /api/jobs/<id>/text/history?segment=ID` | The segment's events |
| `POST /api/jobs/<id>/rerun` `{stage: "compile"}` | Existing route, used by Recompile |

The author is sent by the page (reviewer name setting) and defaults to the OS
user name on the server.

## 10. Code layout

- `book_agent/text_edits.py`: event model, append with lock, current-state
  derivation, overlay onto documents, active-edit hash. No web dependencies, so
  the CLI can use it.
- `book_agent/stages/compile.py`: apply the overlay; include the active-edit
  hash in the input hash; add conflicts to the unresolved gate.
- `book_agent/web/text_view.py`: outline and chapter payloads.
- `frontend/src/pages/TextPage.tsx`, plus a `SegmentEditor` component shared
  with Final review where practical.
- CLI (later): `book-agent edits <job>` lists edits and conflicts;
  `book-agent edits <job> --export xliff`.

## 11. Phases

| Phase | Work | Visible result |
|---|---|---|
| 1 | Outline and chapter APIs, Text tab read-only: outline, paired rows, filters, search | Read the whole book side by side |
| 2 | Edit log module, check/edit/revert/history APIs, in-place editor with reasons, diff, history | Edit any segment with a tracked history |
| 3 | Compile overlay, active-edit hash, "edits not compiled" badge, Recompile | Download a book that contains the edits |
| 4 | Conflict detection after reruns, compile gate integration, unblock actions, rerun warning text | Edits survive reruns safely |
| 5 | Final review decisions written as edit-log events; XLIFF 2.1 export (then import) | One history for all human changes; CAT-tool round trips |

## 12. Tests

- Edit log: append and replay, last-event state, each state transition
  (edited → conflict after the pipeline text changes, `keep` re-bases,
  `take_pipeline`, orphaned), concurrent appends under the lock, a corrupt
  trailing line is reported and not silently dropped.
- Compile: the overlay reaches the EPUB and RTF output; the input hash changes
  with the edits; conflicts count against the compile limit and are listed in
  the pause message.
- API: every new route over HTTP (the route guard test enforces this), stale
  base rejection, override reason required.
- Frontend: state and filter helpers, marker-chip protection in the editor.

## 13. Open questions

1. Should the reviewer name be required before the first edit, or silently
   default to the OS user?
2. Should an edit of a segment in the Final review queue offer to resolve it
   there as well, before phase 5?
3. For XLIFF export: one file per book or per chapter, and should unedited
   segments be exported as `translated` or `reviewed`?
