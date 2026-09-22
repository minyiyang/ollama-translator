# Series glossary in the dashboard: plan

Status: **phases 1–4 implemented** (versions, book pins, CLI; Series pages;
workbench, publish, approval on the series version; LLM suggestions); phase 5
planned.
Last updated: 2026-09-19.

Build and maintain one glossary for a book series from the dashboard: pick the
books, run their glossary stages, curate a shared glossary manually or with LLM
help, publish it as a version, and let each book continue its own pipeline on
that version. A later volume can extend the series without changing any book
that is already translated.

## 1. Request

- Select books of one series; the pipeline runs extraction, resolution, and
  approval for each.
- Pick, merge, or drop series terms, manually or with the LLM, and enhance
  each book's own glossary with the result.
- A new volume later: add it, improve the series glossary, and leave the
  glossaries of existing books untouched.
- Each book continues its translation on the series-optimized glossary.
- Build on the existing CLI workflow.

## 2. The existing CLI workflow

Documented in the README ("Build a shared series glossary"); code in
`book_agent/series_glossary.py` and `build-series-glossary` in
`book_agent/cli.py`.

1. Every volume's config points `glossary.series_glossaries` at one shared
   file, first an empty placeholder.
2. Each book runs decompile → extract → resolve and pauses at glossary
   approval.
3. `build-series-glossary --source-stage resolved --book-output-dir …` builds
   a candidate from the resolved drafts, deterministically and without an
   LLM:
   - a term in at least 2 books (`--minimum-books`) with one translation
     (`--consensus-ratio`, default exact) is promoted;
   - ties, disagreements, books with two translations for one term, and
     generic words become **conflicts** in the report;
   - each book gets an **overlay**: its glossary with matching terms rewritten
     to the series choice (`synchronize_book_glossaries`).
4. Each book is approved with its overlay (`approve --glossary overlay
   [--llm-glossary]`), without resuming.
5. The series file is rebuilt from the approved glossaries, overwriting the
   placeholder. `--llm-conflicts` lets Qwen choose among the reported
   variants, by conflict ID, and nothing else.
6. Every book resumes. Preprocessing merges seed < series < book (the book's
   own entries win) and records the series file's hash in its checkpoint.

**The gap:** the series file is one mutable path. Rebuilding it for a new
volume changes its hash, so every existing book re-runs preprocessing, and
therefore re-translates, on its next resume.

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Where the UI lives | A **top-level Series page** next to Jobs. Each job's Glossary tab shows its series and version and links there. |
| 2 | Updates vs translated books | **Immutable versions; each book pins one.** Existing books stay on their version unless you explicitly Upgrade one. |
| 3 | Which terms enter the series | Automatic candidates need **2+ books** (today's rule). You can **promote** a single-book term by hand, and the **LLM can suggest** promotions. |
| 4 | LLM help | **Resolve conflicts** (choose among existing variants), **flag generic terms**, **suggest promotions**, and **review each book's overlay** before approval. Every LLM output is a suggestion you accept or reject; nothing is applied silently. |

## 4. Data model

A series lives in `runs/.series/<series-id>/`, next to the jobs. The dot prefix
keeps it out of the job list.

```
runs/.series/qel/
  series.json                     manifest
  workbench.json                  curation in progress (next version)
  llm/…                           checkpointed LLM suggestion batches
  versions/
    v001.glossary.json            immutable, standard glossary format
    v001.report.json              build report + every curation decision
    v002.glossary.json
    v002.report.json
```

**`series.json`**

```json
{
  "schema_version": 1,
  "series_id": "qel",
  "name": "The Qel Cycle",
  "direction": "en-zh",
  "books": [
    {"job_id": "qel-01", "volume": 1, "added_at": "…"},
    {"job_id": "qel-02", "volume": 2, "added_at": "…"}
  ],
  "versions": [
    {"version": "v001", "created_at": "…", "source_jobs": ["qel-01", "qel-02"],
     "term_count": 214, "glossary_sha256": "…", "based_on": null}
  ]
}
```

- All books in a series share one direction.
- Versions are never rewritten or deleted; a version can only be marked
  retired.

**Version files**

- `vNNN.glossary.json` is a normal `GlossaryResult`, so preprocessing, the CLI,
  and `series_glossaries` configs can use it unchanged.
- `vNNN.report.json` extends today's `SeriesGlossaryReport`: counts, remaining
  conflicts, and each term's decision with who made it (`rule`, `user`, or
  `llm-accepted`), when, and why.

**`workbench.json`**: one row per candidate term.

```json
{
  "term_id": "T00012",
  "english": "Qelmar",
  "chinese": "凯尔玛",
  "category": "person",
  "aliases": [],
  "origin": "consensus",
  "books": {"qel-01": ["凯尔玛"], "qel-02": ["凯尔玛"]},
  "decision": "keep",
  "decided_by": "rule",
  "reason": "",
  "locked_from": null,
  "suggestion": null
}
```

| Field | Values |
|---|---|
| `origin` | `consensus` (promoted by the rule), `conflict`, `single_book`, `carried` (already in the current version), `manual` |
| `decision` | `pending`, `keep`, `drop` |
| `locked_from` | For `carried` terms, the version they come from. Changing a locked term needs an explicit unlock and is flagged as changing an existing series term. |
| `suggestion` | A pending LLM suggestion: kind (`resolve`, `drop_generic`, `promote`), proposed value, rationale, and model |

**Book binding**: `glossary/series-binding.json` in each job.

```json
{"series_id": "qel", "version": "v001", "path": "runs/.series/qel/versions/v001.glossary.json", "sha256": "…"}
```

- Preprocessing adds the bound version as a series source, after any
  `glossary.series_glossaries` paths, and records its hash like today. Book
  entries still take precedence.
- Jobs without a binding behave exactly as today, so CLI series configs keep
  working.

## 5. Workflow

1. **Create a series:** name it, then add books: existing jobs in the same
   direction, or new sources, which open the new-job flow with the glossary
   review gate on. Books run until they wait at glossary approval.
2. **Build the candidate:** the existing deterministic build over the member
   books.
   - Books at the gate contribute their resolved draft; books already approved
     contribute their approved glossary.
   - The result fills the workbench.
3. **Curate** in the workbench:
   - manual actions: keep, pick a variant, edit, merge aliases, drop, promote
     a single-book term;
   - LLM help, each a background run with checkpoints, bounded by term IDs
     like today's conflict resolver:
     - **Resolve conflicts:** choose one existing variant or none;
     - **Flag generic terms:** propose drops such as "hold" or "EIN";
     - **Suggest promotions:** propose single-book terms worth sharing, such
       as main characters or places, by ID, and never invent terms;
   - suggestions appear on their rows with **Accept** / **Reject**, singly or
     all of one kind.
4. **Publish `vNNN`:**
   - checks: no pending decision on kept terms, CJK targets, no duplicate
     English, alias clashes removed, direction matches;
   - unresolved conflicts are excluded and reported, and stay book-level as
     today;
   - the workbench is frozen into the version files, and a fresh workbench for
     the next version is left open: the published terms are carried and locked,
     and every decision a person made is preserved. No rebuild is needed to
     curate again (rebuild only to pick up new or changed book glossaries).
5. **Per-book glossaries:**
   - for each book waiting at the gate, its overlay is synchronized to the new
     version and becomes that book's approval candidate in its own Glossary
     tab, reviewed manually or with **LLM review**;
   - approving it writes the book's binding to the version, and the book
     continues its own pipeline.
6. **Add a book later:**
   - add the job; it runs to its gate;
   - **Update series** builds a *delta* workbench: the current version's
     terms are carried and locked, and only the new book's terms, newly
     recurring terms, and clashes with carried terms need decisions;
   - publish `vNNN+1`, and the new book binds to it;
   - books pinned to older versions are not touched.
7. **Upgrade a book** (optional, explicit):
   - shows the terms that differ between its version and the target version
     and occur in that book;
   - a book still before preprocessing just rebinds;
   - otherwise it rebinds and reruns from `preprocess`, with the standard
     rerun warning ("the whole book is translated again").

## 6. UI

**`/series`: series list.** Name, direction, books, latest version, and
whether a workbench has pending decisions. A **New series** button.

**`/series/<id>`: three tabs.**

- **Books**
  - Member books with volume, current stage, glossary state (at gate, or
    approved on `vNNN`), and bound version.
  - Actions: Add book (existing job or new source), remove before binding,
    Upgrade to the latest version, open the job.
- **Workbench**
  - A sidebar with views and counts: Promoted, Conflicts, Generic flags,
    Single-book, Category disagreements, Changes vs current version, and
    LLM suggestions. It collapses like the Glossary tab.
  - A stable term list (rows do not jump after a decision), each row showing
    the per-book variants with evidence and the actions of section 5.3.
  - Toolbar: Build / Update candidate, the three LLM actions, and
    **Publish vNNN** with a summary of what it includes and excludes.
- **Versions**
  - Each version's date, source books, term count, diff to the previous
    version, and which books pin it.
  - Download a version's JSON.

**Job pages:** the job header and Glossary tab show "Series Qel · v002"
linking to the series. The Glossary tab labels an overlay candidate as
"synchronized to series v002".

## 7. Background work and CLI

Like the rest of the dashboard, long work runs as CLI commands in a child
process, so logs, checkpoints, Pause, and Stop behave the same. New commands:

| Command | Purpose |
|---|---|
| `book-agent series create <id> --direction en-zh [--name …]` | Create a series *(phase 1)* |
| `book-agent series add <id> <job>…` | Add member books as the next volumes *(phase 1)* |
| `book-agent series build <id>` | Build or update the workbench, deterministically *(phase 1)* |
| `book-agent series decide <id> <term>… --decision keep\|drop\|pending --reason … [--chinese …] [--unlock]` | Manual decisions *(phase 1)* |
| `book-agent series publish <id>` | Freeze the workbench into the next version and write book overlays *(phase 1)* |
| `book-agent series bind <id> <job> [--version vNNN] [--upgrade]` | Pin a book; `--upgrade` confirms rebinding a preprocessed book *(phase 1)* |
| `book-agent series import <id> <file>` | Publish an existing CLI series file as the next version *(phase 1)* |
| `book-agent series status <id>` | Books with glossary state and pinned version, versions, workbench counts *(phase 1)* |
| `book-agent series suggest <id> --task conflicts\|generic\|promote` | LLM suggestions into the workbench *(phase 4)* |

All `series` commands take `--runs` (default `runs`).

`build-series-glossary` stays as it is for existing scripts.

## 8. API

| Route | Purpose |
|---|---|
| `GET /api/series` | Series list |
| `POST /api/series` `{series_id, name, direction}` | Create |
| `GET /api/series/<id>` | Manifest, books with state and binding, versions |
| `POST /api/series/<id>/books` `{job_ids}` / `…/books/remove` | Membership |
| `GET /api/series/<id>/workbench` | Terms, views, counts |
| `POST /api/series/<id>/workbench/decide` `{term_ids, decision, chinese?, aliases?, reason}` | Manual decisions |
| `POST /api/series/<id>/workbench/suggestions` `{term_ids, accept}` | Accept or reject LLM suggestions |
| `POST /api/series/<id>/build`, `…/suggest` `{task}`, `…/publish` | Launch CLI work |
| `GET /api/series/<id>/versions/<v>` | Version JSON and report |
| `GET /api/series/<id>/upgrade?job=…&version=…` / `POST …/upgrade` | Impact preview / upgrade |

## 9. Phases

| Phase | Work | Visible result |
|---|---|---|
| 1 | Series manifest, versions, book binding read by preprocessing; `series create/add/build/publish/bind/import` CLI | Versioned series glossaries from the CLI; new volumes no longer re-translate old ones |
| 2 | Series list and page, Books tab, create series, add books, build candidate | Set up a series in the dashboard |
| 3 | Workbench with manual decisions, publish, overlay candidates in each book's Glossary tab, binding on approval | Curate, publish, and continue each book |
| 4 | LLM suggestions (conflicts, generic flags, promotions), accept/reject, overlay LLM review | LLM-assisted curation |
| 5 | Add-book delta workbench with locked carried terms, Upgrade with impact preview | Grow a series safely |

## 10. Phase 1 as built

- Code: `book_agent/series.py` (manifest, workbench, publish, bind, status),
  `book_agent/series_binding.py` (the pin, read by
  `stages/preprocess.py::_load_effective_glossary`), and the `series` command
  in `book_agent/cli.py`.
- A book without a pin keeps the same preprocessing hash inputs as before, so
  existing jobs are unaffected.
- A pinned version file is verified by hash at preprocessing; a changed file
  is refused rather than silently used.
- Publishing refuses an unchanged glossary ("nothing changed since vNNN") and
  never overwrites an existing version file. It consumes the workbench; the
  next `build` starts from the new version.
- Decisions made by a user survive a rebuild while the term's variants are
  unchanged.
- Tests: `tests/test_series.py`, including the guarantee that publishing
  `v002` leaves a book pinned to `v001` with the same preprocessing checkpoint
  (status, attempts, input hash, and timestamp).

## 11. Phase 2 as built

- Pages: `/series` (list, New series dialog) and `/series/<id>` with **Books**
  (members, glossary state, pinned version, remove before pinning, add jobs
  as the next volumes, build or rebuild the candidate and its counts) and
  **Versions** (published versions, pins, term list). The header gains
  Jobs / Series navigation, and a member job's header shows
  "Series <name> · vNNN" (or "not pinned") linking to the series.
- Building the candidate is deterministic and quick, so it runs in the
  request rather than as a background CLI run.
- **New book** on the Books tab opens the new-job dialog in series mode: the
  upload becomes a draft job that joins the series right away as its next
  volume (listed as "draft · not started", linking to its Config tab). The
  config defaults to one shared `<series-id>.yaml` for all volumes and the job
  ID to `<series-id>-<book name>`. A config in another direction is refused
  before anything is created; discarding the draft removes it from the series.
  Validating a series book also requires the series direction and a glossary
  gate that pauses for review (`require_glossary_review: true`,
  `llm_glossary_review: false`), since an automatic approval would skip the
  series glossary.
- A job belongs to at most one series; the add list only offers jobs in the
  series direction that are in no series.
- Routes: `GET/POST /api/series`, and under `/api/series/<id>/`: `detail`,
  `books`, `books/remove`, `build`, `version?v=vNNN`. The route guard test now
  also covers series routes.
- Code: `frontend/src/pages/SeriesPage.tsx`, `lib/series.ts`,
  `UiApp.series_*` in `book_agent/web/server.py`, and `remove_book`,
  `series_summaries`, `series_of_job`, `addable_jobs` in `book_agent/series.py`.
- Tests: `SeriesApiTests` in `tests/test_web_ui.py`.

## 12. Phase 3 as built

- **Workbench tab:** sidebar views with counts (Needs a decision, Conflicts,
  To publish, Single-book terms, Already published, Dropped, All terms) and a
  search box. Each term shows its per-book variants; **Decide** opens an
  editor to pick a variant or type a translation, give a reason (presets or
  custom), and Keep, Drop, or Leave pending. Published terms need the "change
  the term published in vNNN" box to be changed or dropped. Selected terms
  can be kept or dropped together with one reason.
- **Publish vNNN:** a confirmation lists the terms added, changed, and removed
  versus the latest version and how many pending terms are left out. It is
  disabled when the candidate predates the latest version.
- **Book approval on the series version:** a book at its glossary gate in a
  series with a published version reviews its glossary synchronized to that
  version (the book's pin, else the latest), with a banner linking to the
  series. The overlay is written on demand for a book that became ready after
  publishing. "LLM review the draft" reviews that overlay. Submitting the
  approval pins the book to the version before the run starts, so its
  preprocessing uses it. Books outside a series are unchanged.
- The Books tab links each unpinned book at its gate to "approve with vNNN".
- Not yet: merging aliases and adding brand-new terms by hand (a single-book
  term can be promoted with Keep).
- Routes: `GET workbench`, `POST workbench/decide`, `POST publish` under
  `/api/series/<id>/`; `glossary` and `glossary/approve` under a job gain the
  series behaviour above.
- Tests: `SeriesWorkbenchApiTests` in `tests/test_web_ui.py`, and the
  workbench view helpers in `frontend/src/lib/lib.test.ts`.

## 13. Phase 4 as built

- `book_agent/series_llm.py`, run as `book-agent series suggest <id> --task
  conflicts|generic|promote` (the dashboard launches it in the background,
  like other model work, and follows it). The model and context settings come
  from `--config` or the first member book's configuration.
- Each task sends only eligible terms: pending conflicts; undecided,
  unpublished agreed or conflicting terms (generic); single-book terms
  (promote). Terms decided by a person, already suggested, or whose suggestion
  of that kind was rejected are skipped.
- Cases carry the term, category, note, each book's translations, and up to
  two source sentences of evidence. Answers are validated: every term ID once,
  no unlisted ID, and a conflict choice must be one of the listed variants.
  Batches of 30 are saved under `llm/`, so an interrupted run resumes without
  repeating calls.
- Answers are merged into the freshest workbench, so decisions made while the
  model runs win.
- Workbench: an "LLM suggestions" view, three LLM buttons with the number of
  terms each would send, a running banner, and Accept / Reject per term or for
  all shown. Accepting records the decision as `llm-accepted` with the model's
  rationale; rejecting remembers the kind so it is not asked again.
- CLI: `book-agent series accept <id> <term>… [--reject]`.
- Tests: `SuggestionTests` in `tests/test_series.py` (fake model client) and
  `SeriesSuggestionApiTests` in `tests/test_web_ui.py`.
- Demo: `configs/demo-holmes.yaml` and `scripts/demo-holmes.ps1` run the
  Sherlock Holmes sample books to their glossary gates and build the series
  candidate.

### Evidence and run log (after the Holmes run)

- The build records `mentions`: how often each term occurs, as a whole word or
  phrase, in every member book's source text, including books whose glossary
  missed it (about 0.8 s for 1,200 terms across five books). In the Holmes
  run, 106 of 1,128 single-book terms occur in the text of two or more books
  (Mrs Hudson, "hansom"), so they are strong promotion candidates. The
  promotion prompt passes these counts to the model.
- The sidebar splits single-book terms per book ("Vol. N · title") and adds
  **Found in other books**: terms in exactly one book's glossary whose English
  also appears in the source text of other books, which those books therefore
  translate ad hoc. Such a term carries an orange "in N books' text" chip.
- Every term lists all member books: the book's translation, or "not in
  glossary" with its mention count, or "—". **Evidence** loads, per book, its
  glossary entries with their evidence sentences, the mention count, and up
  to three passages (`GET /api/series/<id>/term?id=`). Long views show 150
  terms at a time.
- **Show LLM run log** shows the latest suggestion run's output (live while it
  runs, from its output file afterwards, so it survives a server restart):
  `GET /api/series/<id>/log`. Runs log under `stage=series_<task>`.
- A workbench built before this change has no mentions until **Rebuild
  candidate**; decisions and suggestions survive the rebuild.

## 14. Tests

- Binding: preprocessing includes the bound version and its hash; a book
  without a binding is unchanged; book entries override series entries.
- Versions: publish never rewrites an existing version; a new version leaves
  the preprocessing checkpoint of books pinned to older versions current (the
  key "no re-translation" guarantee).
- Workbench: decisions persist; locked carried terms need an unlock; publish
  checks (duplicates, alias clashes, CJK, direction); excluded conflicts are
  reported.
- LLM suggestions: out-of-scope or invented terms are rejected by validation;
  suggestions change nothing until accepted; batches resume from checkpoints.
- Delta build: only new or clashing terms need decisions.
- Upgrade: the impact preview lists only changed terms that occur in that book;
  rebinding before preprocessing does not trigger a rerun.
- API: every route over HTTP (enforced by the route guard test).

## 15. Open questions

1. Should removing a book from a series be allowed after it is bound, or only
   before?
2. For LLM promotion suggestions, which categories qualify (people, places,
   organizations, recurring items)?
3. Should the Versions tab offer a book-level "why is this term here" trail
   (which books and decisions produced it)?
