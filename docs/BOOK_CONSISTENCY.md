# Book-level consistency: proposal

Status: **phases 0–3 implemented** (measurement; translation memory and the
audit_consistency stage; the style sheet; story context). Phase 4 unscheduled
(decision 7.8). Phase 3 is opt-in and not recommended: no measurable benefit
in two benchmarks (section 8.5).
Last updated: 2026-09-29.
Updated for the tracked edit log, Text tab, and XLIFF round trip
(docs/FULL_TEXT_REVIEW.md, docs/XLIFF_IMPORT.md) that landed after the first draft.

This document plans how the pipeline keeps a whole book (and a series)
consistent, not just each passage. It lists what exists today, the gaps, and
proposed stages and configuration options, all opt-in. Decisions still needed
are listed at the end.

## 1. Where continuity comes from today

The translation unit is a **chunk**, not a segment. Chunks are sized by the
token budget (`budget.source_tokens`, `translation.max_prompt_tokens`); in the
*Alice* demo, 875 segments were translated in 14 chunks, roughly one chapter
per LLM call. Inside a chunk the model sees the whole passage, so local
continuity is already handled. The later passes are much narrower:

| Mechanism | Keeps consistent | Scope |
|---|---|---|
| Approved glossary | Names, places, items, concepts: nouns with a fixed rendering | Book |
| Series glossary (`build-series-glossary`) | The same across volumes | Series |
| Style prompt + naturalness guidance (`translation.style`, `de_ai_*`) | Tone and register *instructions*, identical for every call | Book (instructions only) |
| `translation.boundary_context: adjacent-read-only` | One source segment either side of a chunk | Chunk edges |
| Semantic audit neighbors | Immediate neighbors of each risky passage | ±1 segment |
| Targeted repair context | One neighbor each side, read-only | ±1 segment |
| Prose rewrite | Small batches with previous-source context | ~4 segments |
| Fallback harmonization | Voice of rescued passages vs. the primary model | Local |
| Tracked manual edits (`edits/segment-edits.jsonl`) | Human fixes from the Text tab, Final review, and XLIFF import; kept across reruns and applied at compile | Segment |
| Edit check (`text_edits.check_edits`) | The deterministic audit re-run on a chapter with the proposed edits applied | Chapter |

Nothing carries a *decision* made in chapter 3 into chapter 9 unless it is a
glossary noun. A human can now fix drift by hand on the Text tab, but nothing
points them at it, and nothing stops a manual edit or an XLIFF import from
introducing new drift.

## 2. Gaps

1. **Voice and forms of address.** Register such as 你 vs 您 (in the demo
   Alice addresses the Mouse as 您), and how each character speaks (childlike,
   pompous, archaic).
2. **Pronouns and gender** of characters, especially animals and
   personifications (它 / 他 / 她).
3. **Titles and forms of address** that are not glossary nouns. The demo's
   quality report already flags recurring *Mouse* (27) and *Majesty* (12)
   without an entry.
4. **Repeated source text:** refrains, catchphrases, verse, repeated
   dialogue, chapter-heading patterns. The same source line can come out
   differently in different chapters.
5. **Conventions:** quotation marks, ellipsis (……), dashes, numerals.
6. **Story context:** a chunk does not know who was introduced chapters ago,
   or what a callback refers back to.

Gaps 1–5 are consistency problems (the same thing rendered differently); gap
6 is a context problem (the model lacks information to choose well).

## 3. Proposed additions

Four components, all switchable. Proposed stage placement:

```text
decompile
  -> extract_glossary        (+ style-sheet candidates, same pass)
  -> resolve_glossary        (+ style sheet resolution)
  -> approve_glossary        (+ style sheet review, same gate)
  -> build_story_context     NEW  source-side chapter summaries
  -> preprocess              (+ translation-memory index, style-sheet selection)
  -> translate               (+ story context, style-sheet entries per chunk)
  -> rescue_translation
  -> audit_translation
  -> audit_consistency       NEW  book-level checks; findings join the audit's
  -> repair_translation      (repairs semantic AND consistency findings, one pass)
  -> reprose_translation     (+ style-sheet items as protected signatures)
  -> review_repaired -> repair_review
  -> validate_repaired       (+ deterministic consistency re-check: blocking -> review queue)
  -> compile                 (overlays the edit log; + consistency re-check on the edited text)
  -> validate_epub
```

Outside the stage list, the edit check (Text tab, Final review, XLIFF import)
gains the same deterministic consistency checks; see 3.5.

Placing `audit_consistency` **before** `repair_translation` means consistency
fixes reuse the existing repair and verification machinery in the same pass.
Re-checking in `validate_repaired` catches drift reintroduced by prose
rewrite.

### 3.1 Translation memory: deterministic, no model calls

- **Index** (in `preprocess`): normalized source segments and sentences that
  occur more than once (above a minimum length), with every occurrence.
- **Check** (in `audit_consistency`): every occurrence of a repeated source
  unit must have the same rendering. Differences become findings whose fix is
  the rendering chosen by majority, or the first in reading order when tied.
- **Series:** approved renderings from earlier volumes are loaded as a
  read-only memory; a mismatch with them is a finding too.
- Intentional variation (the author repeating a line with a different sense)
  is handled like any finding: the reviewer can accept the difference.

### 3.2 Book style sheet: everything the glossary does not hold

A structured, reviewed record alongside the glossary:

| Section | Example entry |
|---|---|
| Characters | Mouse: pronoun 它; addressed by Alice as 您; speaks formally, easily offended |
| Address forms | Queen → "Your Majesty" = 陛下 |
| Recurring expressions | "Off with her head!" = 砍掉她的头！ |
| Conventions | Quotation marks “”; ellipsis ……; numerals in Chinese characters in dialogue |

- **Built with the glossary.** Candidates come from the same extraction
  chunks (one extra section in the structured output), so there is no extra
  pass over the book. Resolution merges them per character or phrase.
- **Reviewed at the glossary gate.** Same LLM or human review as the
  glossary, shown as a new section on the Glossary tab.
- **Used per chunk.** Only entries relevant to a chunk (its characters and
  phrases) go into the translate, repair, and prose-rewrite prompts, the way
  glossary entries are chosen today, so the extra prompt text stays small.
- **Series.** Style sheets merge across volumes like series glossaries.

### 3.3 Story context: chapter summaries

- `build_story_context` makes a short, source-side summary per chapter: who
  appears, what happens, open threads. It depends only on the source, so it
  can be made up front, checkpointed, and resumed; translation does not have
  to run chapter by chapter.
- Each translation chunk receives a bounded "story so far": the summaries of
  the last *N* chapters plus the style-sheet entries of the characters
  present.
- The cost is about one LLM call per chapter.

### 3.4 Book-level consistency audit

- **Deterministic** (cheap, always worth running):
  - glossary compliance across the whole book, not just per chunk
  - translation-memory mismatches (3.1)
  - style-sheet compliance: pronoun and address register near each
    character's name, recurring expressions
  - convention checks: punctuation, numerals
- **LLM voice check** (optional): for each main character, sample lines from
  across the book and ask the audit model whether voice or register drifts.
  The cost is a few calls per main character, not per segment.
- **Output:** findings in the existing audit format (segment, severity,
  category `consistency`, explanation, fix), so repair, verification, the
  review queue, and the Final review tab work unchanged.

### 3.5 Human edits and the edit log

Human changes are not written into `validate_repaired`'s output. They are
events in `edits/segment-edits.jsonl`, applied on top of it at compile
(docs/FULL_TEXT_REVIEW.md). A consistency check that reads only stage output
therefore checks the wrong text. Three consequences:

- **Check the text as compiled.** The book-level audit and the
  `validate_repaired` re-check read the validated draft with active edits
  applied (`overlay_active_edits`). Compile runs the deterministic
  consistency checks again on that text, because an edit made after
  `validate_repaired` never passes through it.
- **An edited rendering is the reference** (decision 7.4). When occurrences
  of a repeated line disagree, the proposed fix for the others is, in order:
  a human edit (active in the edit log), the majority rendering, and on a tie
  a verified LLM repair, then the first in reading order. A repair only
  breaks a tie: a repair made for another finding would otherwise turn every
  occurrence that agrees into a finding. Two human edits that disagree go to
  the reviewer rather than being resolved automatically.
- **Edits are checked book-wide.** `check_edits` audits chapter by chapter
  today. The translation-memory and style-sheet checks need the whole book,
  so the edit check gains a book-level pass: an edit in chapter 9 is compared
  with chapter 3. Consistency findings on an edit are overridable with a
  reason (category `consistency`), not hard-blocking, since intentional
  variation exists. An XLIFF import batch is checked as one set, so imported
  occurrences that agree with each other but not with the rest of the book
  are still caught.

**UI.** Consistency findings appear where findings already do: the Text
tab's status column and Flagged filter, the Final review queue when
blocking, and the editor's live check. The Text tab gains a **Consistency**
filter, and a finding links to the other occurrences so they can be fixed
together. Because one fix may touch several chapters, the batch edit API
(`apply_edit_batch`) applies one decision to every occurrence with a single
reason.

## 4. Proposed configuration

All keys are new and optional. The defaults below keep today's behavior,
except for the cheap deterministic checks.

```yaml
consistency:
  translation_memory:
    enabled: true              # deterministic; no model calls
    min_source_characters: 12  # ignore very short repeats ("Yes.", "Oh!")
    series_memories: []        # approved memories from earlier volumes
  style_sheet:
    enabled: false
    review: glossary           # glossary: same gate as the glossary | human: always pause
    max_entries_per_chunk: 12
    series_style_sheets: []
  story_context:
    enabled: false
    chapters_before: 2         # summaries included in each translation chunk
    max_summary_tokens: 250
    model: null                # default: ollama.model
  audit:
    enabled: true              # deterministic book-level checks
    blocking_severity: medium  # at or above: repaired, then review queue if unresolved
    voice_check: false         # LLM check per main character
    voice_min_appearances: 20  # characters with fewer lines are skipped
    voice_samples_per_character: 12
    model: null                # default: audit.model
```

In the dashboard these would form a **Consistency** group on the Config tab's
Options view, with the same help-text treatment as other options.

## 5. Artifacts and UI

| Artifact | Where | Shown in |
|---|---|---|
| Translation-memory index and mismatches | `preprocessed/`, `reports/consistency.*` | Progress stage tooltip; Final review findings |
| Style sheet draft / approved | `glossary/…/style-sheet.{draft,approved}.json` | Glossary tab, new "Style sheet" section |
| Chapter summaries | `story/summaries.json` | Progress stage tooltip; optional read-only panel |
| Consistency findings | per-document audit findings, category `consistency` | Final review tab (existing); Text tab status column and Consistency filter |
| Phase 0 measurement | stdout, or a file you name (`book-agent consistency-report`) | Terminal; nothing is written into the job folder |

Stage descriptions for the Progress tab's ⓘ tooltips are added to
`frontend/src/lib/stageInfo.ts` together with each stage.

## 6. Rollout

| Phase | Scope | Model cost | Value |
|---|---|---|---|
| 0 | **Measure first:** `book-agent consistency-report` on existing runs (see 6.1) | none | Confirms which gaps matter on real books before building |
| 1 | Translation memory (3.1) + deterministic consistency audit (3.4) | none | Catches repeated-line drift and cross-chapter glossary misses |
| 2 | Style sheet (3.2) | ~none (same extraction pass); review at the gate | Largest quality gain: voice, pronouns, address, refrains |
| 3 | Story context (3.3) | ~1 call per chapter | Better choices at chunk boundaries and for callbacks |
| 4 | LLM voice check (3.4) | a few calls per main character | Catches register drift the style sheet cannot encode |

Each phase ships with tests on synthetic fixtures (the suite stays offline)
and is verified on the *Alice* sample before the next phase starts.

**Estimated added runtime**, scaled from measured per-call times. Alice: 875
segments, 12 chapters, 1.3 h today. Crusoe: 832 segments, 23 chapters, 3.4 h,
2.5 h of it semantic audit at ~17 s per call. Estimates, not measurements:

| Phase | Alice | Crusoe |
|---|---|---|
| 0–1 (deterministic) | seconds | seconds |
| 2 style sheet | +2–5 min | +2–5 min |
| 3 story context (~1 call per chapter, mostly prompt reading) | +3–8 min | +6–15 min |
| 4 voice check | +3–7 min | +3–7 min |
| Repairing the new findings (~2.6 s per repair call) | +1–5 min | +2–8 min |
| **All on** | **~+15–25 min (+20–30%)** | **~+20–35 min (+10–15%)** |

### 6.1 Phase 0: the measurement report

`book-agent consistency-report <job> [--json] [--output FILE]` reads the
current translation and the approved glossary. The current translation is
the validated draft with active edits applied, or, before
`validate_repaired`, the latest stage that produced output. It makes no
model calls and writes nothing into the job folder.

| Section | What it measures | Limits |
|---|---|---|
| Repeated source lines | Segments whose normalized source (markers and whitespace removed, at least `--min-chars` characters) occurs more than once, and how many distinct renderings each has | Whole segments only |
| Repeated sentences (source only) | Sentences recurring across segments, speech tags (*said the King*) and symbol rows left out | Renderings are not compared: that needs sentence alignment |
| Glossary compliance, book-wide | Per entry: segments whose source contains the term but whose translation contains neither its rendering nor a Chinese alias; a term inside a longer matched term is not checked on its own | Word-boundary match, case-sensitive for capitalized terms; a single capitalized word the book also uses in lowercase (*Two*) is skipped at the start of a sentence |
| Recurring names without an entry | `find_unglossed_proper_nouns` over the source | The same list the glossary stage already reports |
| Formal address (EN→ZH) | Segments using 你 and 您 in quoted speech, and every 您 segment listed | Does not know who is speaking to whom; the list is for a reader to judge |
| Pronouns and address per character (EN→ZH) | For each person entry (entries sharing a rendering merged): 他 / 她 / 它 in segments naming only that character, beside he / she / it in the source; 你 / 您 in quoted speech in segments naming them | Only 他 vs 她 is flagged as mixed, since 它 is mostly an object; a pronoun can still refer to someone else |
| Conventions (EN→ZH) | Quotation marks, nested quotes (never flagged), ellipses, dashes, and Arabic digits in the translation, with example segments | Counts styles; does not decide which is right |

**First results, *Alice* (2026-09-28, validated draft with 5 manual edits;
the report takes about 1 s):**

- Exact repeated segments are rare: 2, both rendered consistently. Repeated
  *sentences* are not: 36, including the Queen's "Off with her/his head!"
  (8 segments) and "Give your evidence" (3), plus every chapter title shared
  by the table of contents and the chapter heading. So phase 1's translation
  memory needs sentence-level matching (or the style sheet's recurring
  expressions) to be worth much on literary prose.
- The glossary is followed book-wide: one miss, in the Gutenberg licence.
- Recurring names without an entry: Mouse (27), Majesty (12). The Mouse's
  pronoun and form of address go unchecked, the case the style sheet targets.
- 您 appears in 8 segments of quoted speech, against 你 in 212: Alice to
  the Mouse (chapter II), the Caterpillar and the Pigeon (chapter V), and the
  Mock Turtle, and the White Rabbit's 陛下，您 to the King. Whether each
  character is addressed the same way throughout still needs a reader; it is
  the register choice a style sheet would record.
- Mixed 他/她: the Queen (she 12, he 7), the Mock Turtle, Bill; to be read
  in context, since a pronoun can point at another character.
- Conventions: 16 segments use a single `—` where the rest use `——` (for
  example `唱道：—`, a copied English colon-dash), and one straight `"`.

Characters without a glossary entry (in *Alice*: the Mouse, the Cat, the
Rabbit) get no per-character rows. That is itself a finding, and it is what
the style sheet (phase 2) is for.

## 7. Decisions (2026-09-28)

1. **Scope:** build through phase 2 (translation memory, the deterministic
   consistency audit, and the style sheet). Phases 3 and 4 wait.
2. **Style-sheet review:** at the same gate as the glossary. Revised
   2026-09-29 after the benchmark: a human reviews it by default
   (`consistency.style_sheet.review: human`), on the Glossary tab's Style
   sheet section or with `approve --style FILE`, even when the glossary is
   LLM-reviewed; `review: glossary` follows the glossary's own setting.
6. **Source wording first** (2026-09-29): pronouns and forms of address
   (你/您) change with the scene, so character entries are context notes, not
   rules. Prompts say so, translation uses a note only where the source leaves
   the choice open, and nothing checks or enforces them. Book-level
   continuity is enforced for recurring expressions and repeated lines.
7. **Similarity rule** (confirmed 2026-09-29): the medium/low split by
   similarity applies whatever the reference is. A human edit that fixes a
   slip (他→它) makes the other copies close variants, which are queued; a
   substantial rewrite of one copy is treated as context-specific, so the
   other copies are listed (low), not queued (section 8.2).
8. **Phase 3 next, phase 4 unscheduled** (2026-09-29): story context gives
   the translator more of the scene, in line with decision 6; a per-character
   voice check (phase 4) conflicts with it, since voice and register change
   with the scene.

### 8.5 Phase 3: story context

**Cost probe (decision 7.3, 2026-09-29):** one structured summary call per
chapter with the translation model (`qwen3.8:latest`, thinking off) on the
three benchmark chapters (`runs/bench/measure_story.py`, read-only):

| Chapter | Segments | Prompt tokens | Output tokens | Time |
|---|---:|---:|---:|---:|
| II (`item5`) | 26 | 2,769 | 354 | 19.5 s (13.9 s model load) |
| VIII (`item11`) | 72 | 3,344 | 367 | 5.7 s |
| IX (`item12`) | 93 | 3,214 | 361 | 6.3 s |

About 6 s per chapter once the model is loaded (translation loads the same
model anyway): roughly 1.2 min for *Alice* (12 chapters) and 2.3 min for
*Crusoe* (23), well under the 3–15 min estimated in section 6. The summaries
were accurate: characters, events in order, and open threads (for example
"the Mock Turtle's history regarding games, which the Gryphon demands he tell
next"). Each translation chunk's prompt grows by about 1,000 tokens (two
previous chapters and the current one), about +20% prompt tokens; prompt
reading takes seconds, so translation time barely changes.

**Built (2026-09-29), opt-in:** `consistency.story_context.enabled: true`
(default off; with it off, prompts and preprocessed files are unchanged).
`build_story_context` runs after `approve_glossary` and depends only on the
decompiled source, so a glossary change does not redo it; each chapter is a
resumable work unit. Preprocessing turns the summaries into each document's
`story_context` (the `chapters_before` earlier chapters and its own), and the
translation prompt adds it as "Story so far (context only …)". Settings:
`chapters_before` (2), `max_summary_words` (120), `max_source_characters`
(40,000; longer chapters are summarized from their beginning), `model`
(default `ollama.model`). A job preprocessed before the stage existed records
it as completed ("added after this job had passed this point"); *Rerun from
here* applies it. Code: `book_agent/story_context.py`,
`book_agent/stages/story_context.py`; tests: `tests/test_story_context.py`.

**End to end on the benchmark subset (2026-09-29, `runs/bench/alice-story.yaml`,
story context only):**

| | Baseline | Story context |
|---|---|---|
| build_story_context | — | 3 calls, 0.3 min |
| translate | 2.7 min, 22,002 prompt tokens | 3.0 min, 24,005 (+9%) |
| whole run | 14.0 min, 51 calls | 10.4 min, 58 calls |
| semantic audit findings | 8 | 7 |
| review queue | empty | empty |

The shorter total is the audit writing less (7,286 → 2,768 output tokens),
not a measured quality gain. 139 of 191 segments came out differently, almost
all as paraphrase (奔向 / 赶往, 蠢货 / 傻瓜): a longer prompt shifts the
wording even at temperature 0. Two changes went against the source: the
Mock Turtle's old teacher, "him" in English, became 它 instead of 他
(`D0002-S000057`/`058`), and "Once, … I was a real Turtle" became 有一次
instead of 曾经. The "Off with his head!" drift happened here too, and
`audit_consistency` repaired it, as in phase 1.

**Conclusion:** the cost is small (about +0.6 min on the subset), but this
subset cannot show the benefit. Its chapters (II, VIII, IX) are not
contiguous, so each "story so far" skips the chapters in between, and the
benefit story context aims at (callbacks to people and events from chapters
ago) needs a continuous book. It stays opt-in and off by default until a run
on a whole book, or a contiguous stretch, shows fewer context errors than
without it.

**Contiguous test (2026-09-29): *The Sign of the Four*, chapters I–VI**
(303 segments, full of callbacks: Captain Morstan, Major Sholto, the yearly
pearls, Thaddeus and Bartholomew, the Agra treasure). Two runs of the Holmes
demo configuration with LLM glossary approval, identical except for story
context (`runs/bench/holmes-base.yaml`, `holmes-story.yaml`; comparison
script `runs/bench/compare_story.py`, output `runs/bench/sign-comparison.md`).

- **Cost:** `build_story_context` took 6 calls and 0.6 min for six chapters;
  the story run's whole pipeline took 23.5 min. (The base run was stopped
  for low memory and resumed, and its first session's metrics were lost, so
  its total is not comparable.)
- **Source agreement:** one segment in each run uses a pronoun the English
  does not; the same count, with no story-only deviations.
- **Semantic audit:** 17 findings without, 22 with; 8 segments flagged in
  both, 9 only without, 11 only with. The extra findings are local slips
  (too literal, a word left untranslated, a glossary miss), none about
  referents or earlier chapters.
- **Callback passages:** 228 of 303 segments differ, almost all paraphrase.
  One improved: Thaddeus's "the course which has seemed right to me" became
  我认为 instead of 他认为. One got worse: "Your servant, Miss Morstan" became
  您是我的仆人 ("you are my servant") instead of 愿为您效劳.

**Conclusion:** no measurable benefit on a contiguous, callback-heavy
stretch either; the differences are the variance of changed wording. The
translator already sees whole chapters as its chunk context, and at this
book length that is evidently enough for referents. Phase 3 stays built,
opt-in, and off by default; it is not recommended, and no further work is
planned on it unless a longer book shows context errors that it would fix.
3. **Cost:** before any model-calling part is built, benchmark it on a 2–3
   chapter subset of a real book (`scripts/create_epub_spine_subset.py`) to
   measure actual calls, tokens, and time instead of relying on the
   estimates in section 6.
4. **Tie-breaking** for translation-memory mismatches: an edited rendering
   wins, first a human edit (Text tab, Final review, XLIFF import), then a
   verified LLM repair; then the majority rendering; then the first in
   reading order.
5. **Consistency findings on manual edits:** overridable with a reason, never
   hard-blocking.

## 8. Implementation design (phases 1–2)

Written after mapping the code the new work plugs into. Phase 1 is
deterministic and needs no benchmark; phase 2 calls models, so the 2–3
chapter benchmark (decision 7.3) runs before and after it.

### 8.1 What phase 0 changed in the plan

- **Repeats must be matched below the segment.** On *Alice* only 2 whole
  segments repeat, but 36 sentences do. Quoted speech gives a reliable,
  model-free alignment: 675 of 680 quoted segments (99%) have the same number
  of `“…”` spans in source and translation, so spans pair up in order. Of 26
  repeated quoted lines, 5 are rendered more than one way, including "Off with
  his head!" as 砍掉他的头！ twice and 砍掉它的头！ once. Narration outside
  quotes is left to the style sheet's recurring expressions (phase 2).
- **Book-wide glossary compliance adds almost nothing**: the per-document
  audit already checks the glossary, and phase 0 found one miss, in the
  Gutenberg licence. It stays in the measurement report and is not a new
  finding source.

### 8.2 Phase 1: the `audit_consistency` stage

A deterministic stage between `audit_translation` and `repair_translation`
(`STAGE_DEPENDENCIES`: after the audit; repair depends on it).

**Detectors** (reusing `consistency_report`):

| Detector | Finding on | Severity |
|---|---|---|
| Repeated segments (normalized source ≥ `min_repeat_characters`) | every occurrence whose rendering differs from the reference | medium when the variant is close to the reference (similarity ≥ `close_variant_similarity`, i.e. drift such as 他→它); otherwise low (likely intentional, context-dependent wording) |
| Aligned quoted speech (same span count in source and translation) | as above, per quoted span | as above |
| Conventions | a single `—` where the book uses `——`; a straight `"` in Chinese text | medium |

The **reference** rendering follows decision 7.4: an active human edit, then
the majority, then on a tie (in the `validate_repaired` re-check, where
repairs exist) a verified repair, then the first in reading order. Two human
edits that disagree produce a finding on both, with no automatic fix.

Convention and style-sheet findings are raised on unedited segments only: a
human edit is checked when it is proposed (point 5 below), because repair
works on the pipeline text and cannot fix the edit.

The severity rule applies whatever the reference is: when a human rewrites
one occurrence substantially, the other copies differ clearly from it, so
they are listed (low) rather than queued. A human edit that fixes a slip
(他→它) makes the other copies close variants, which are queued.

Findings are ordinary `AuditIssue`s with a new category `consistency` and
`source="consistency"`, a real `segment_id`, the reference in
`suggested_fix` ("render as in D0009-S000019: 砍掉她的头！"), and the other
occurrences named in the message. Low findings are listed in the stage
report and the Text tab only; they are not repaired and do not block.

**Artifacts:** `consistency/<hash16>/NNNN-<document>.consistency.json` per
document with findings, plus `consistency.report.json` (always written, since
a stage must record at least one artifact). Metadata: `consistency_root`,
`consistency_report`.

**Flow into repair and review** (each point below closes a path by which
the findings would otherwise be dropped silently):

1. `repair_translation` merges the consistency findings per document after
   `reconcile_document_audit` (which keeps only semantic findings) and before
   `select_repair_targets`. Its input hash gains the consistency output hash
   only when that stage produced findings, so jobs without findings keep
   their cached repair units.
2. `repair_review` does not retire a review item whose issue has
   `source="consistency"`; today it restores the original text for any
   non-semantic finding it cannot reproduce.
3. `validate_repaired` runs the book-level detectors again on the final
   drafts and merges the medium findings into the final deterministic audit,
   so unresolved drift joins `defect_ids` (the human review queue) and a
   repair that did not really fix it is caught. It is merged into the final
   audit only, not the preflight, which would revert targeted repairs.
   `_accept_unreproduced_deterministic_reviews` is left as it is: the re-check
   is what reproduces them. The consistency settings join its input hash.
4. **Compile** runs the detectors once more on the text as compiled (the
   validated draft with active edits). A new finding, for example drift
   introduced by an edit in another chapter, adds its segment to the
   `unresolved_review_gate`; like any review segment, a Final review decision
   or a Text tab edit with a reason clears it (decision 7.5: overridable).
   Only findings the edits introduced are added: drift already in the
   validated draft is in the validation report (point 3), and a job validated
   before these checks existed is not blocked by it on its next compile.
   The check is cached in the process by validated draft, config, style
   sheet, and active edits, since the Text tab and the gate ask for it
   several times per request.
5. **Edit check:** `check_edits` adds a book-level pass. A proposed text is
   compared with the other occurrences of its repeated segment or quoted
   span, with the other active edits applied, and a mismatch is an
   overridable `consistency` finding. An XLIFF import is checked as one
   batch.

**Existing jobs.** A new stage would make every finished job read as
`pending`. When `initialize_pipeline_stages` adds `audit_consistency` to a
workspace whose `repair_translation` has already completed, it records the
stage as completed with the message "added after this job was repaired;
rerun from here to apply", and no artifacts. Resume then skips it, and the
job stays complete. *Rerun from here* on the Progress tab applies it.

**Configuration**, a new top-level section (not under `audit` or
`translation`, whose settings feed existing checkpoint hashes):

```yaml
consistency:
  enabled: true                   # deterministic; no model calls
  min_repeat_characters: 12
  quoted_speech: true
  conventions: true
  close_variant_similarity: 0.6   # at or above: medium (repaired); below: low (listed only)
```

**Registration:** `WorkflowStage` and `STAGE_DEPENDENCIES`; the runner in
`workflow.default_stage_runners`; `web/estimate.py` `_QUICK_STAGES` and
`_stage_enabled`; `web/setup.py` section title; the frontend `STAGE_LABELS`,
`STAGE_INFO` (the ⓘ tooltip, required by a test), and a Consistency group
in the Config tab's Options; the stage lists in the READMEs and docs;
the bundle rebuilt. The CLI's stage lists are already generated from the
enum.

**UI:** consistency findings show on the Text tab like other findings, with
a Consistency filter; a finding lists the other occurrences (linked).

### 8.3 Phase 2: the style sheet

**Built (2026-09-28), opt-in:** `consistency.style_sheet.enabled: true`
(default off, so existing prompts, checkpoints, and preprocessed files are
byte-for-byte unchanged). As built, it differs from the plan below in three
ways: address forms ("Your Majesty" = 陛下) are recurring expressions rather
than a separate list; conventions are the target language's defaults rather
than extracted (they cannot be read from the source); and translation picks
each chunk's entries from `PreprocessedDocument.relevant_style`, filled by
preprocessing. Code: `book_agent/style_sheet.py`; the extraction, resolution
(`style.draft.json`) and approval (`style.approved.json`; in LLM mode one
review call, or one per `glossary.approval_chunk_tokens` batch for a long
sheet; `approve --style FILE` for a reviewed file) steps in
`book_agent/stages/glossary.py`; the Glossary tab's **Style sheet** section;
expression checks in `audit_consistency`, the `validate_repaired` re-check,
compile, and the edit check; tests in `tests/test_style_sheet.py`. Measured on
the benchmark (8.4).

The original plan:

- **Model:** a separate artifact, not new fields on `GlossaryResult` (which
  forbids extra fields and is read by many loaders): `StyleSheet` with
  `characters` (name, pronoun, how others address them, voice note),
  `address_forms`, `expressions` (source → rendering), and `conventions`
  (quotes, ellipsis, dash, numerals).
- **Extraction:** one more optional section in the glossary extraction's
  structured output, so no extra pass over the book. This changes the
  extraction prompt, so every extraction unit reruns once
  (`EXTRACTION_STAGE_VERSION` bump). The benchmark measures the added output
  tokens and time.
- **Resolution:** deterministic merge per character and expression; entries
  with conflicting values are kept as alternatives for review.
- **Approval:** the same gate and settings as the glossary (decision 7.2).
  The LLM reviewer reviews style entries with their own decision schema; a
  human reviews them in a new **Style sheet** section of the Glossary tab,
  and `book-agent approve --style FILE` accepts an edited file. The approved
  `style.approved.json` is recorded under `approve_glossary`, so a change
  re-runs preprocessing.
- **Use:** preprocess selects the entries relevant to each document
  (`relevant_style`, next to `relevant_glossary`); translate and repair
  prompts get a short "Book style sheet" block (characters present,
  expressions present, conventions), capped by `max_entries_per_chunk`;
  reprose treats expression renderings as protected.
- **Checks:** `audit_consistency` gains style-sheet compliance: an
  expression's approved rendering where its source occurs, and the declared
  quote/dash conventions. Pronoun and address checks stay in the measurement
  report until the benchmark shows how noisy they are.

### 8.4 Benchmark (decision 7.3)

A 3-chapter *Alice* subset built with `scripts/create_epub_spine_subset.py`:
chapters II (`item5`: the Mouse, 您), VIII (`item11`: "Off with her head!")
and IX (`item12`: the Mock Turtle, the Queen), 191 segments, about 22% of
the book. Run once on the current pipeline for the baseline, and again after
phase 2, comparing calls, tokens, and time per stage from
`reports/session-performance-*.json`.

```powershell
python scripts/create_epub_spine_subset.py "sample/Alice's Adventures in Wonderland by Lewis Carroll.epub" runs/bench/alice-3ch.epub --idref item5 --idref item11 --idref item12
book-agent run runs/bench/alice-3ch.epub --config configs/demo-alice.yaml --job-id bench-alice-3ch-baseline --plain
```

**Baseline (2026-09-28, before phase 1):** 14.0 min and 56 model calls in
total.

| Stage | Calls | Time | Output tokens |
|---|---:|---:|---:|
| extract_glossary | 2 | 1.0 min | 2,864 |
| resolve_glossary | 1 | 0.5 min | 2,448 |
| approve_glossary | 1 | 3 s | 202 |
| translate | 5 | 2.7 min | 12,818 |
| audit_translation | 17 | 5.8 min | 7,286 |
| repair_translation | 6 | 0.5 min | 607 |
| reprose_translation | 13 | 1.5 min | 6,421 |
| review_repaired | 6 | 1.8 min | 863 |

Phase 2 adds its style-sheet section to the two extraction calls, so
`extract_glossary` (1.0 min, 2,864 output tokens) is the line to compare. On
this baseline's output, the phase 1 detectors find the same drift as on the
whole book: "Off with his head!" once as 砍掉它的头！ against 砍掉他的头！
twice (medium), and "Come on, then" reworded (low).

**Phase 1 on the same subset (2026-09-28):** `audit_consistency` reported
exactly those two findings, sent the medium one to repair, and listed the low
one. Repair rewrote `D0001-S000060` to “砍掉他的头！”, matching the other two
occurrences; the `validate_repaired` re-check found no remaining drift, the
review queue stayed empty, and the book compiled and validated.

**Phase 2 on the same subset (2026-09-28, `runs/bench/alice-style.yaml`, LLM
review):**

| Stage | Baseline | With style sheet | Change |
|---|---|---|---|
| extract_glossary | 2 calls, 1.0 min, 2,864 output tokens | 2 calls, 1.2 min, 4,312 output tokens | +0.2 min, +1,448 tokens (+51%) |
| approve_glossary | 1 call, 3 s | 2 calls, 0.4 min | +1 call (the style review) |
| translate prompts | 22,002 prompt tokens | 22,700 | +3% |

The run took 53.9 min, but not because of the style sheet: the semantic
audit's generation speed fell from about 30 to 1–4 tokens/s with the same
prompts and context (16,384), a machine-memory slowdown in a stage the style
sheet does not touch. The style sheet's own cost is about **+0.6 min and one
call** on this subset, so roughly +2–3 min on a whole book.

What it produced: 15 characters and 14 expressions drafted; the LLM review
rejected Dinah (a pet that does not act as a person) and a poem fragment. The
effect on the translation:

- **Refrain drift prevented upstream.** "Off with his head!" came out as
  砍掉他的头！ everywhere; the 砍掉它的头！ variant that phase 1 had to repair
  never appeared (0 findings for repair, against 1).
- **Register followed the sheet.** It approved the Mouse as addressed with
  你, so quoted 您 fell from 6 segments to 1. That is a register choice the
  gate should surface to a human; the demo config approves it by LLM.
- **Pronouns only partly.** The sheet says 他 for the Mouse, but chapter II
  still refers to it with 它 where the English says "it": the source wording
  outweighed the style-sheet line.
- **Bug found and fixed:** the LLM reviewer answered "revise" for the Gryphon
  (它 → 他) but left the pronoun field empty and put the correction only in
  its reason, so nothing changed. Review decisions now require the final
  values for every entry (`StyleCharacterDecision`, `StyleExpressionDecision`),
  with a regression test.

**Follow-up (2026-09-29, decisions 7.2 and 7.6):** the Mouse's 它 where the
English says "it", and 你 against the baseline's 您, are both the source and
the scene deciding, which is now the intended behaviour: character entries
became context notes, and the style sheet is reviewed by a person at the gate
by default.

### 8.6 Whole-book tests (2026-09-30)

Two books from the candidate list: *The Wind in the Willows* (talking
animals, for the style sheet) and *The Return of Sherlock Holmes* (many
stock lines, a false-positive stress test for phase 1). Both use the Holmes
demo configuration made unattended (`runs/bench/unattended-base.yaml`: LLM
glossary approval, prose rewrite off, 2-minute model keep-alive); the runs
are scripted in `runs/bench/run-consistency-tests.ps1` and evaluated by
`runs/bench/evaluate_tests.py`.

**The Wind in the Willows** (Project Gutenberg #289, 911 segments of text),
without and with the style sheet (`runs/bench/wind-style.yaml`, LLM-reviewed
because nobody was at the gate). Results: `runs/bench/wind-results.md`.

- **Pronouns needed no help.** Without the style sheet the translator
  already refers to Mole, Rat, Toad, and Badger with 他 in every chapter, and
  never uses 它 where the English says "he"; with it, the same. The English
  wording decides, as decision 7.6 intends.
- **The style sheet made review worse.** The review queue grew from 4 to 28
  segments. All 24 extra entries came from the expression check. The LLM
  review had approved all 101 extracted "recurring expressions", but 84 of
  them occur once or never in the source (one-off sentences, "Good night!"),
  and the check demanded the approved wording verbatim. Of the five distinct
  expectations behind the 24, one was arguably real ("Ratty" rendered 拉蒂
  instead of 水鼠, a nickname that belongs in the glossary); the rest were
  legitimate variation ("Poop-poop!" as 噗噗声 in narration) or not
  recurring at all.
- **Fixes, from this data:**
  1. Resolution keeps an expression only if it occurs verbatim at least twice
     in the source and has at least 6 characters
     (`keep_recurring_expressions`). On this book, 101 become 16, all real
     refrains and set phrases ("Poop-poop!", "When the Toad—came—home!",
     "Who comes there?", "Mole End", "old chap").
  2. A missed expression is a low finding (listed, not queued). The style
     sheet's value is prevention in the prompt, as with "Off with his head!"
     on *Alice*; agreement between the actual renderings of a repeated line
     is already enforced by `repeated_line_issues` with the similarity rule
     (decision 7.7).
  With both, the 24 extra entries would not have been queued.
- **Cost:** the style run took 116 minutes in all (13 extraction calls, 2
  approval calls, 30 translation calls). The run without the style sheet was
  stopped twice for low memory and resumed, so only its later stages were
  timed, and totals are not comparable.

**The Return of Sherlock Holmes** (sample #108, 13 stories, 2,563 segments),
phase 1 defaults. Results: `runs/bench/return-results.md`.

- **Time:** 389 minutes (6.5 h), of which the semantic audit took 322
  (425 calls, about 45 s each). The audit model ran at 23 tokens/s on
  average against 35 on a healthy run, and 22% of its calls fell below
  10 tokens/s: the 31B audit model spilling out of GPU memory. At normal
  speed the audit alone is about 2.5 h for a book this size. On this machine,
  large books need other applications closed during the audit, or
  `audit.model: gemma4:26b`.
- **Consistency:** 9 findings, 4 medium (repaired) and 5 low. The false
  positives this book was chosen to expose appeared among the medium ones:
  "How do you know?" and "Whom do you suspect" rendered with 您 in one story
  and 你 in another, and "Certainly not." rendered 当然不知道 and 当然不会
  as answers to different questions. Repair made them match, which is wrong:
  the register and the meaning of a short reply follow the scene. The
  fourth ("Holmes smiled." 微笑了 / 笑了) was harmless.
- **Fixes** (refining decisions 7.6 and 7.7):
  1. 你 and 您 compare equal: English "you" does not decide register.
  2. A line of three words or fewer (six CJK characters or fewer in Chinese)
     is a reply or interjection: listed (low), never queued, however similar.
     Four-word refrains such as "Off with his head!" still count.
  Replayed on every benchmark's text: *Return* goes from 4 medium findings
  to 0; *Alice* keeps "Off with his head!" (砍掉它的头) as medium; *Wind*
  keeps two, one a real catch (the chapter title "LIKE SUMMER TEMPESTS CAME
  HIS TEARS" is 泪如夏日骤雨 in the table of contents but 泪如夏日风暴骤至 in
  the heading).
- **Otherwise clean:** no punctuation-convention findings, and no
  consistency finding left in the review queue after validation (the 7
  queued segments are semantic ones).
- **Audit context (2026-09-30):** Ollama already evicts the translation model
  before loading the audit model (its log: "predicted to exceed available
  memory, evicting", then all layers on the GPU). The slowdown came in
  bursts by time of day (20 of 21 calls slow in one hour, 1 of 66 in
  another): the 31B model at 16K needs about 21 GiB of the 24 GiB card, and
  other applications' GPU memory pushed it into Windows shared memory
  (system RAM). A 10K semantic-audit context was tried and reverted
  (2026-10-01): it cut the 31B model from 21.0 to 18.9 GiB (measured), but
  other applications then held 4.7 GB of GPU memory, the card was full again
  (24,045 of 24,564 MiB), and calls still ran at 1–4 tokens/s. The audit keeps
  16K; the reliable remedy is freeing GPU memory before long runs
  (docs/OPERATIONS.md, "GPU memory").

### 8.7 Glossary quality and enforcement (2026-10-01)

The whole-book tests showed that the glossary, not the book-level stages,
prevents most drift, and that its gaps were quality and enforcement rather
than coverage. Measured on the finished runs (read-only), three changes:

1. **A missed name is repaired.** A glossary miss was always a low finding,
   and repair handles medium and up, so the audit saw "Dr. Armstrong" as
   阿姆斯特朗博士 instead of 阿姆斯特朗医生 four times and nothing fixed it.
   A miss on a person, place, or organization is now medium when the name is
   unambiguous: capitalized, and either several words or a word the chapter
   never uses in lowercase (Alice's playing card "Two" stays low: the chapter
   says "two days"). A multi-part transliterated name shortened to one part
   ("Sherlock Holmes" as 福尔摩斯) stays low. Replayed: *Return* 15 medium
   (Lady Hilda as 希尔德 instead of 希尔达 ×7, Dr. Armstrong ×5), *Wind* 3
   (one "the Rat" translated as 水獭, an otter), *Alice* and *Sign* 0,
   *Crusoe* mostly untranslated or omitted names (and 9 in the Gutenberg
   licence).
2. **Ordinary words leave the glossary under LLM or automatic approval.** The
   generic-word screen flagged 144 of *Wind*'s 364 entries ("anchor",
   "cheese"), yet all were approved; ordinary-word entries caused 52 of its 54
   glossary misses and were pushed into every chunk as fixed renderings. They
   are now dropped unless a configured glossary file supplies them, listed in
   `glossary.dropped-generic.json`; human review still decides on its own
   (`glossary.drop_generic_terms`, default on). When a reviewed file is sent
   for LLM review, the entries it adds to the draft or changes in it are
   kept; ordinary words it leaves as drafted are still dropped.
3. **Titles with a name are extracted.** The extraction prompt asks for
   recurring titles and forms of address. A/B on three *Return* stories (six
   chunks, same model): the new wording added "Lady Hilda" (希尔达夫人, the
   name that drifted), "Inspector Lestrade", "Inspector Stanley Hopkins", and
   "Mrs. Hudson", for about 9% more output tokens. It did not add honorifics
   that stand in for a name ("His Grace"); that remains a known gap. It also
   proposed more ordinary words, 29 of 33 of which change 2 removes.
