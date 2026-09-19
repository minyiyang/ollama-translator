# Dashboard stage control: resume, rerun, and early approval

Status: **implemented.** Last updated: 2026-09-18.

How the browser dashboard (`book-agent ui`) continues a stopped pipeline, reruns
a finished stage, and approves a final draft before every review segment is
decided. Each action launches the regular CLI, so logs, checkpoints, and exit
codes match a terminal run.

## 1. Request

- Retry a specific stage from the UI instead of the terminal.
- A retry redoes every downstream stage, so it must warn before it starts.
- A failed or paused stage needs **Resume**: nothing downstream has run, and
  finished work should be kept. It also offers a rerun, for when its partial
  work should not be trusted (for example after a config or model change).
- In final review, once the undecided segments fit the compile limit, ask
  whether to keep resolving or apply and approve now.
- Use one in-app dialog for all confirmations instead of the browser's
  `confirm()` box.

## 2. Stage actions (Progress tab)

Each row of the pipeline table offers:

| Stage status | Actions | What they run | Dialog |
|---|---|---|---|
| failed, or paused mid-pipeline | **Resume** and **Rerun** | `resume <job>` / `retry <job> --stage X --resume` | none for Resume; warning for Rerun |
| completed | **Rerun from here** | `retry <job> --stage X --resume` | warning (below) |
| paused at a human gate (`approve_glossary`, `compile`) | none | handled on the Glossary and Final review tabs | — |
| pending, running | none | — | — |

- **Resume keeps finished work.** The stopped stage runs again, and completed
  work units whose inputs are unchanged are reused, which is what
  `retry --failed-only` would do, without a reset.
- **Rerun discards finished work.** Stage X and every later stage return to
  pending, and their completed work units are cleared, so all their LLM calls
  are made again. For a failed or paused stage this is its own partial work;
  the later stages have not run yet.
- Both buttons are disabled while the job runs, with a tooltip saying to pause
  or stop first.

## 3. Rerun warning

Before a rerun, the dialog (Cancel is the default, Rerun is red) shows:

- the stages whose finished work is discarded, in order, with the active time
  each took so far and their total, plus how many later stages have not run
  yet; for a failed or paused stage, a reminder that Resume would keep its
  work;
- the warnings that apply to stage X:

| Warning | When |
|---|---|
| The glossary must be approved again; the run pauses at that gate | X is `approve_glossary` or earlier |
| The whole book is translated again | X is `translate` or earlier |
| Manual review decisions and the final-draft approval are discarded | X is `validate_repaired` or earlier, and review work exists: applied resolutions, a saved review draft, or an approval |
| The compiled EPUB is replaced | `compile` has completed |

Kept across a rerun: stored numeric-check rulings
(`audit/numeric-rulings.json`), which are keyed by exact text, so unchanged
segments are not asked again.

## 4. Early approval (Final review tab)

The compile limit is `workflow.compile_max_unresolved_review_segments`
(default 0).

- When the number of undecided segments first drops to the limit or below, a
  dialog offers **Continue resolving** (default) or **Apply & approve now**.
  It appears once per crossing, not on every edit.
- *Apply decisions* with undecided segments offers the same choice when they
  fit the limit, and otherwise asks you to decide every segment.
- *Apply & approve now* applies only the decided segments and approves the
  final draft. Undecided segments keep their current translation and stay
  listed in the review report; *Compile now* then continues.
- The server grants final approval whenever the remaining queue fits the
  limit, whether or not every segment was decided.

## 5. Confirmation dialog

`frontend/src/components/Dialog.tsx` provides `useDialog()` (custom actions)
and `useConfirm()` (Cancel / confirm). Escape or a click outside cancels. It
replaces every `window.confirm` in the dashboard: Discard and Stop in the job
header, the three glossary approval buttons, discarding an edit in final
review, early approval, and rerun.

## 6. API

| Route | Purpose |
|---|---|
| `GET /api/jobs/<id>/rerun?stage=X` | Preview: `status`, `stages` (name, status, seconds), `warnings` (code, message), `previous_seconds`. Allows completed, failed, and paused stages; refuses unknown, pending, or running stages and the review gates. |
| `POST /api/jobs/<id>/rerun` `{stage}` | Launches `retry --stage X --resume` (label `rerun`). Refuses while the job runs. |
| `POST /api/jobs/<id>/resume` | Existing: launches `resume`. |
| `GET /api/jobs/<id>/review` | Now includes `compile_limit`. |
| `POST /api/jobs/<id>/review/apply` `{worksheet, approve_final, partial}` | `partial: true` applies decided segments only; refused when more are pending than the limit allows. |

The preview reads the pipeline's own dependency map
(`pipeline_state.downstream_stages`), so the dialog always matches what the
reset will do. Code: `book_agent/web/rerun.py`, `UiApp.rerun_preview` and
`UiApp.rerun_job` in `book_agent/web/server.py`, `ReviewSession.apply` in
`book_agent/review_ui.py`.

## 7. Tests

- `tests/test_web_ui.py`
  - `RerunTests`: preview stages and warnings, rerun of a failed or
    interrupted stage, refusals (review gate, pending, unknown, job running),
    the exact launched command.
  - `EndpointCoverageTests`: every API route over HTTP, plus a guard test that
    fails when a route in `server.py` has no HTTP call in the test files.
- `tests/test_review_ui.py`, `PartialApplyTests`: refused beyond the limit;
  within it, the leftover segment stays queued and the draft is approved; a
  full apply still requires every decision.
- `frontend/src/lib/lib.test.ts`: `stageActions` picks Resume and Rerun,
  Rerun only, or nothing for each stage status and gate.

## 8. Limits

- Times in the dialog are the active time recorded in session logs, summed
  across sessions; a stage that never logged shows no time.
- Rerun always resets whole stages. To redo only failed units of a completed
  stage, use `book-agent retry <job> --stage X --failed-only --resume`.
- Only runs started from the dashboard can be stopped from it; a terminal run
  can be paused (`book-agent pause`).
