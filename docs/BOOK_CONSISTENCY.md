# Book-level consistency: proposal

Status: **proposal, not implemented.** Last updated: 2026-09-18.

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

Nothing carries a *decision* made in chapter 3 into chapter 9 unless it is a
glossary noun.

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
  -> compile -> validate_epub
```

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
| Consistency findings | per-document audit findings, category `consistency` | Final review tab (existing) |

Stage descriptions for the Progress tab's ⓘ tooltips are added to
`frontend/src/lib/stageInfo.ts` together with each stage.

## 6. Rollout

| Phase | Scope | Model cost | Value |
|---|---|---|---|
| 0 | **Measure first:** a read-only consistency report on existing runs (repeated-line variants; pronoun and 你/您 per character) | none | Confirms which gaps matter on real books before building |
| 1 | Translation memory (3.1) + deterministic consistency audit (3.4) | none | Catches repeated-line drift and cross-chapter glossary misses |
| 2 | Style sheet (3.2) | ~none (same extraction pass); review at the gate | Largest quality gain: voice, pronouns, address, refrains |
| 3 | Story context (3.3) | ~1 call per chapter | Better choices at chunk boundaries and for callbacks |
| 4 | LLM voice check (3.4) | a few calls per main character | Catches register drift the style sheet cannot encode |

Each phase ships with tests on synthetic fixtures (the suite stays offline)
and is verified on the *Alice* sample before the next phase starts.

## 7. Decisions needed

1. **Scope now:** phase 0 + 1 only, or through phase 2?
2. **Style-sheet review:** the same gate as the glossary (LLM or human per
   `workflow.llm_glossary_review`), or always a human pause?
3. **Cost budget:** is ~1 extra call per chapter (story context) plus a few
   per main character (voice check) acceptable when switched on?
4. **Tie-breaking** for translation-memory mismatches: majority, first
   occurrence, or always ask in the review queue?
