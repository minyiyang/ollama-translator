# Full-text review and tracked manual edits: plan

Status: **all five phases implemented.** The Text tab reads and edits
segments, with a tracked history that compile overlays onto the book, that
survives reruns as conflicts you unblock, and that Final review now shares:
a resolution is an edit-log event, not a rewrite of validate_repaired's
stored draft. XLIFF 2.1 export (not import) is available via
`book-agent edits <job> --export xliff`. Last updated: 2026-09-21.

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
| 5 | Final review | **Unified (phase 5).** `resolve_manual_review` appends edit-log events instead of rewriting validate_repaired's stored draft; a review-queue segment counts as resolved once it has an active edit (`text_edits.unresolved_review_gate`). |

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
  "base_target_text": "…大约二百二十八里亚尔…",
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
| `base_target_text` | Snapshot of that pipeline text, used for an accurate three-way conflict diff after any number of manual edits. Older events without it fall back to `previous_text`. |
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
- Every mutation also sends the last edit event ID. The server compares it
  while holding the append lock, so a second tab cannot overwrite a newer
  edit, revert, or conflict decision.

## 7. Compile, conflicts, and download

- **Applying edits:** compile overlays the active edits (edited and conflict
  states) on the validated draft. The compile input hash includes a hash of
  the active edits, so any change to them makes the compiled book outdated.
  When final review is required, compile rechecks that this exact validated
  draft plus captured edit snapshot is the approved revision.
- **Conflicts block compile like unresolved segments:**
  - they are added to the compile gate's unresolved list and count against
    `workflow.compile_max_unresolved_review_segments`;
  - the pause message lists them as "edit conflicts" next to review defects;
  - each is unblocked in the Text tab by **Keep my edit** (a `keep` event),
    **Use the new pipeline text** (`take_pipeline`), or a new edit. Every
    choice needs a reason.
- **Edits after a completed compile:** compile stores the active per-segment
  edit snapshot. The job header shows the exact number of changed segments as "N edits not in
  the compiled book" with **Recompile**, which reruns only `compile` and
  `validate_epub` (seconds, no LLM calls). Download keeps serving the last
  compiled file, with that notice beside it. Compiled downloads include the
  first six hexadecimal characters of the compile input hash, for example
  `Book.translated-a1b2c3.epub`; the complete hash remains the authoritative
  build identity in pipeline state. Final document validation compares the
  EPUB or RTF-derived EPUB with the same captured edit snapshot, so edits
  saved after compilation correctly remain pending for the next build.
- **Reruns:** the rerun warning (docs/STAGE_CONTROL.md) adds "Manual text edits
  are kept; segments whose translation changes become conflicts."

Unified (phase 5): a Text tab edit **does** resolve a segment in the Final
review queue, and a Final review resolution is itself an edit-log event —
both go through `resolve_manual_review` / `apply_edit` and the same dynamic
gate (`text_edits.unresolved_review_gate`), computed from the edit log
against the (never rewritten) validate_repaired report rather than by
mutating it. The Text tab marks queued segments and links to Final review
for its richer per-case context (suggested fixes, source/target versions),
but resolving is no longer exclusive to either tab.

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
  - a plain textarea for now; protected inline markers are **not** yet shown
    as undeletable chips (phase 2 shipped without that polish — dropping a
    marker is still caught and hard-blocked by the deterministic check, so
    nothing unsafe saves, but the editor doesn't yet prevent the keystroke);
  - the check result shows below the editor;
  - saving needs a reason (free text; no preset list yet, unlike Final review);
  - Ctrl+Enter saves, Esc cancels, and Alt+↑/↓ moves between rows.
- An edited row shows a diff against the pipeline text and a history popover
  (author, time, reason, text of each event) with **Revert**.
- A conflict row shows your edit, the text it was based on (with a diff
  against the new pipeline text), and the new pipeline text, with **Keep my
  edit** (`keep`) and **Use the new pipeline text** (`take_pipeline`)
  unblock actions; a fresh edit also unblocks it, re-based against the
  current pipeline text.

**Header:** a badge "N edits not in the compiled book → Recompile" shows
once the book has been compiled (implemented). The Shell nav dot for
conflicts is not wired up: `tabStates()` only sees `/info`'s stage data,
not the Text tab's own outline; the totals bar and the per-chapter sidebar
counts already surface conflicts once you're on the tab.

## 9. API

| Route | Purpose |
|---|---|
| `GET /api/jobs/<id>/text` | Outline: chapters (`document_id`, title, order, segment count, edited, conflicts, flagged), totals, `editable`, edits not yet compiled |
| `GET /api/jobs/<id>/text/<document_id>` | One chapter: per segment `segment_id`, `source`, `pipeline_text`, `text`, `state`, `edit_revision`, `base_target_sha256`, findings, dynamic `flagged`, in-review-queue |
| `POST /api/jobs/<id>/text/check` `{segment_id, text}` | Blocking and advisory check results |
| `POST /api/jobs/<id>/text/edit` `{segment_id, text, reason, base_target_sha256, expected_event_id, override_reason?}` | Appends an `edit`; rejected when the pipeline or edit revision is stale, or a blocking problem has no override reason |
| `POST /api/jobs/<id>/text/revert` `{segment_id, reason, expected_event_id}` | Appends a `revert` if the active event still matches |
| `POST /api/jobs/<id>/text/conflict` `{segment_id, choice: keep\|take_pipeline, reason, expected_event_id}` | Unblocks a conflict if the active event still matches |
| `GET /api/jobs/<id>/text/history?segment=ID` | The segment's events |
| `POST /api/jobs/<id>/rerun` `{stage: "compile"}` | Existing route, used by Recompile |

The author is sent by the page (reviewer name setting) and defaults to the OS
user name on the server.

## 10. Code layout

- `book_agent/text_edits.py`: event model, append with lock, current-state
  derivation, overlay onto documents, active-edit hash, optimistic event
  revisions, atomic batch append, the deterministic
  check (`preview_manual_resolution`, `NON_OVERRIDABLE_CATEGORIES` — moved
  here from `manual_review.py` in phase 5 so `manual_review.py` could depend
  on this module without a cycle), and the shared compile/approval gate
  (`unresolved_review_gate`). No web dependencies, so the CLI can use it.
- `book_agent/manual_review.py`: `resolve_manual_review` now checks every
  resolution with `classify_check` and appends the set with `apply_edit_batch`, instead
  of mutating validate_repaired's stored documents/validations and bumping
  its stage hash.
- `book_agent/stages/compile.py`: apply the overlay; include the active-edit
  hash in the input hash; the unresolved gate (queue + conflicts) via
  `unresolved_review_gate`.
- `book_agent/workflow.py`: the pre-compile pause (`_unresolved_compile_review_message`)
  and `approve_final_draft` use the same gate. Final approval and Final-review
  worksheet staleness are bound to a composite validated-draft + active-edit revision.
- `book_agent/web/text_view.py`: outline and chapter payloads; `in_review_queue`
  and `flagged` are dynamic (excluded once a segment has an active edit).
- `book_agent/xliff_export.py`: `export_xliff` — one `.xlf` for the book, one
  `<file>` per chapter, `<I000>` markers mapped to paired `<pc>` elements,
  `state="reviewed"` for an active edit else `"translated"`. Export only;
  import is not built.
- `frontend/src/pages/TextPage.tsx`, with a link to Final review from a
  queued segment. Not a shared `SegmentEditor` component with Final review —
  ReviewPage.tsx's is a full-page one-segment-at-a-time editor; TextPage's is
  inline per-row. Kept separate rather than force a shared abstraction across
  differing layouts.
- CLI: `book-agent edits <job>` lists edit events and conflicts (`--json` for
  machine-readable); `book-agent edits <job> --export xliff [--output PATH]`.

## 11. Phases

| Phase | Work | Visible result |
|---|---|---|
| 1 ✅ | Outline and chapter APIs, Text tab read-only: outline, paired rows, filters, search | Read the whole book side by side |
| 2 ✅ | Edit log module, check/edit/revert/history APIs, in-place editor with reasons, diff, history | Edit any segment with a tracked history |
| 3 ✅ | Compile overlay, active-edit hash, "edits not compiled" badge, Recompile | Download a book that contains the edits |
| 4 ✅ | Conflict detection after reruns, compile gate integration, unblock actions, rerun warning text | Edits survive reruns safely |
| 5 ✅ | Final review decisions written as edit-log events; XLIFF 2.1 export (import not built) | One history for all human changes; CAT-tool hand-off |

## 12. Tests

- Edit log: append and replay, last-event state, each state transition
  (edited → conflict after the pipeline text changes, `keep` re-bases,
  `take_pipeline`, orphaned), concurrent appends under the lock, a corrupt
  trailing line is reported and not silently dropped.
- Compile: the overlay reaches both the EPUB package path and the
  RTF-derived path (`compile_rtf_document`, a distinct code path — see
  `test_compile_overlays_active_edits_onto_rtf_derived_output`); the input
  hash changes with the edits; conflicts count against the compile limit and
  are listed in the pause message, for both EPUB and RTF.
- API: every new route over HTTP (the route guard test enforces this), stale
  base rejection, override reason required.
- Frontend: `frontend/src/lib/text.test.ts` covers Text-tab view filters,
  dynamic flagged state, source/translation search, and row-style precedence.
  The repository still has no component-level browser test harness.
- `unresolved_review_gate` (`tests/test_text_edits.py::UnresolvedReviewGateTests`):
  defect/approval classification, the "uncategorized review ids" fallback,
  an edit resolving queue membership without mutating the report object
  passed in, and a conflict excluded from the queue but still counted.
- Final review unification (`tests/test_manual_review.py`,
  `tests/test_review_ui.py`, `tests/test_workflow.py`): a resolution appends
  an edit-log event and leaves validate_repaired's stored draft and stage
  record untouched; the compile gate and `approve_final_draft` both resolve
  dynamically; a bounded out-of-queue correction and an obfuscated accept
  both still route through the same deterministic check.
- Text tab conflict payload (`tests/test_text_view.py`): the `last_edit.based_on`
  field is asserted against the actual pipeline base after multiple manual
  edits; compiled-delta counts include additions and reverts; dynamic flagged
  state clears after an active edit.
- Edit concurrency (`tests/test_text_edits.py`, `tests/test_web_ui.py`): stale
  event revisions are refused under concurrent writes and a stale member
  prevents an entire Final-review edit batch from being appended.
- Approval revision (`tests/test_review_ui.py`): applying a worksheet advances
  its revision, a later Text-tab edit requires final approval again, and an
  already-open worksheet cannot save over that edit.
- XLIFF export (`tests/test_xliff_export.py`): one `<file>` per chapter,
  `<I000>` markers round-trip through `<pc>`, an active edit exports
  `state="reviewed"` with its edited text.
- CLI (`tests/test_cli.py`): `edits --json` and the plain listing both show
  events and conflicts; `edits --export xliff --output` writes the file.

## 13. Open questions

1. ~~Should the reviewer name be required before the first edit, or silently
   default to the OS user?~~ Decided for now: silently defaults to the OS
   user (`getpass.getuser()`); there is no dashboard reviewer-name setting
   yet, so revisit if that's added later.
2. ~~Should an edit of a segment in the Final review queue offer to resolve
   it there as well, before phase 5?~~ Resolved by phase 5's unification:
   yes, automatically — any active edit (from either tab) resolves the
   segment's review-queue membership, since both are the same edit log read
   through the same gate.
3. ~~For XLIFF export: one file per book or per chapter, and should unedited
   segments be exported as `translated` or `reviewed`?~~ Decided: one file
   per book (chapters as separate `<file>` elements inside it); unedited
   segments export `state="translated"`, only actively-edited ones
   `state="reviewed"`.
4. XLIFF import is not built. If it's wanted later: does an imported
   `state="reviewed"` segment become an `edit` event (indistinguishable from
   a Text tab edit) or its own event kind, and how do foreign `<unit>` ids
   (from a CAT tool) map back onto our `segment_id`s if a translator
   renumbers or splits units?
