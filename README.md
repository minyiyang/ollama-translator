# Ollama Agentic Translator

[![CI](https://github.com/minyiyang/ollama_translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama_translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A local, resumable book-translation pipeline for English-to-Chinese and
Chinese-to-English literary prose. It takes an EPUB or RTF and produces a
translated EPUB, running entirely against local Ollama models: no cloud API,
no MCP server, and no agent framework such as LangGraph or AutoGen.

Long-form translation fails in ways one model call cannot fix. A character's
name drifts between chapters, an inline emphasis tag is dropped, a quantity is
silently altered, or a "repair" replaces a good sentence with a worse one. This
pipeline treats each of those as a separate, individually checkpointed problem.

## How it works

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> preprocess -> translate
  -> audit_translation -> repair_translation -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> compile -> validate_epub
```

Every stage checkpoints its artifacts under `runs/<job-id>/`. `resume` skips
work that already completed and validated, so a run interrupted partway through
a book continues from the earliest affected stage rather than from the start.

## Design goals

- **Deterministic control of verifiable structure.** EPUB layout, inline
  markers, and identifiers are handled mechanically rather than delegated to a
  model.
- **Abstention over uncertain repair.** A repair that cannot be verified is
  discarded rather than allowed to overwrite a better existing translation; the
  segment escalates to human review.
- **Bounded retries.** The pipeline stops re-calling a model once validation
  stops converging, instead of looping on the same failure.
- **Generic failure handling.** Failure classes are addressed generically; no
  per-book or per-passage patches are hard-coded.
- **Adaptive context allocation.** The configured 131K context is a ceiling, not
  an allocation. Ordinary calls start at 16K and targeted repair and repair
  verification at 8K, growing through 16K, 32K, 64K, and 131K only as required,
  which keeps GPU memory proportional to each request.

## Model roles

The default split is conservative: `qwen3.8:27b` extracts glossary candidates;
`qwen3.8:latest` resolves terminology, translates, repairs, and proposes
reprose; `gemma4:31b` performs semantic audit and reprose verification; and
`gemma4:26b` handles bounded repair comparison and quantity work before an
optional 31B escalation. See [Production Operations](docs/OPERATIONS.md) for
the rationale, safe overrides, and model-loading behavior.

Set `audit.repair_model` to pin targeted repair explicitly; when omitted it uses
the primary translation model. `audit.repair_max_num_ctx` optionally gives
repair its own context ceiling instead of inheriting `audit.max_num_ctx`.

Thinking is disabled by default for semantic audit and repair verification. Set
`audit.thinking: true` only when a difficult text benefits enough from deeper
reasoning to justify the additional runtime.

## Project documentation

- [Current production operations](docs/OPERATIONS.md)
- [Design](docs/DESIGN.md)
- [Working plan](docs/PLAN.md)
- [Unrun inference-framework benchmark plan](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## Installation

From this directory:

```powershell
python -m pip install -e .
book-agent --version
```

For development, including the test and coverage tools:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Conventional requirements files are also provided for environments that use
requirements-based installation:

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

Use the development requirements when pytest and coverage are needed:

```powershell
python -m pip install -r requirements-dev.txt
```

The dependency declarations in `pyproject.toml` remain authoritative; the
requirements files mirror them for compatibility.

The project requires `ollama>=0.6.2`; that SDK version exposes Ollama's
top-level thinking control. Run the installation command again after pulling
dependency changes into an existing checkout.

Ollama must be running locally and the configured models must be installed.
Before the first model-backed stage, the CLI verifies both model names and
their configured context capacity.

## Configuration

Copy `config.example.yaml` and edit the copy. Validate and inspect the fully
resolved settings with:

```powershell
book-agent config --file .\my-book.yaml
book-agent styles
```

Relative glossary, custom-style, run, and prompt paths are resolved relative
to the configuration file. The resulting absolute paths and complete
configuration are captured in the job workspace so a later `resume` does not
depend on the current shell directory.

## Start a job

Inspect a prospective run without writing files or contacting Ollama:

```powershell
book-agent run "D:\books\source.epub" `
  --config .\config.example.yaml `
  --dry-run
```

Start the workflow:

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml
```

RTF uses the same command and pipeline:

```powershell
book-agent run "D:\books\source.rtf" --config .\my-book.yaml
```

EPUB compilation preserves package resources and document markup. RTF input is
normalized to chapter and paragraph structure, and compilation emits a valid
EPUB 3 with those accepted translations; source-specific fonts, styling,
embedded objects, headers, and metadata are intentionally not reproduced.
RTF parsing uses the lightweight `striprtf` package under a bounded input
contract; rendering and parse-back validation remain deterministic.

The command prints the workspace path. Save it; all later commands operate on
that directory. `--runs` overrides the configured workspace parent and
`--job-id` supplies a stable safe directory name.

Use `--plain` on `run`, `resume`, `approve`, or `retry` for log-friendly text.
An interactive terminal uses Rich progress output when available.

During streamed translation, the CLI prints a heartbeat as soon as the first
response chunk arrives, then at least every 30 seconds while further chunks
are arriving (or after roughly another 2,000 observed output tokens, whichever
comes first). Both visible output and separate reasoning activity count toward the
heartbeat, so a reasoning model no longer appears idle until it starts its
final answer. Request completion prints the exact Ollama prompt/output token
counts, decode speed, and elapsed time. Prompt text, generated prose, and
thinking text are never printed by this progress channel. The intervals are
configurable as `ollama.progress_interval_seconds` and
`ollama.progress_interval_tokens`; setting either to `0` disables that trigger.
`ollama.progress_interval_chars` remains as a legacy fallback and is used only
when token-based throttling is disabled.
Every progress line includes a timezone-aware timestamp. Completion also breaks
out model load, prompt evaluation, and aggregate token decoding. When Ollama
streams a separate thinking field, the client reports its character count and
the observed thinking-to-content and visible-content wall-clock phases; Ollama
does not expose separate authoritative token counts for those two phases.
The end-of-file stage table aggregates the same measurements in `load time`,
`prompt time`, and `decode time` columns, alongside total backend `LLM time`.
It is followed by an LLM-role table that separates semantic audit, base and
escalated quantity audit, repair, reprose, and verification usage, including
the model/context combinations used by each role.
`load time` makes cold loads and model swaps visible; the three components need
not exactly equal `LLM time` because the latter also includes backend overhead.
Operational lines also include the active workflow stage and an eight-character
session hash shared by that CLI invocation, for example:

```text
[2026-08-27T23:10:55-04:00] [stage=translate] [session=7fa31c20] [chunk=2/14 id=translate-0002-00001 attempt=1/4 mode=full] [llm] qwen3.8:latest: generation started
[2026-08-27T23:15:11-04:00] [stage=translate] [session=7fa31c20] [chunk=2/14 id=translate-0002-00001 attempt=1/4 mode=full] [llm] qwen3.8:latest: validation failed — protected_marker_mismatch; retrying
[2026-08-27T23:15:11-04:00] [stage=translate] [session=7fa31c20] [chunk=2/14 id=translate-0002-00001 attempt=2/4 mode=focused passages=1 after=protected_marker_mismatch] [llm] qwen3.8:latest: generation started
```

The same chunk/attempt label is repeated on streaming heartbeats and the
completion line. A retry identifies whether it is a full response retry or a
focused retry, how many passages remain, and the previous validation issue codes.
Glossary extraction uses the same overall counter, for example
`chunk=2/3 id=glossary-00002 attempt=1/4`.
All other model-backed loops expose the same bounded position rather than an
unqualified generation message. Examples include
`batch=1/3 id=glossary-resolution-local-00001 mode=local attempt=1/4`,
`batch=2/7 id=audit-0003-00001 document=4/14 attempt=1/4`,
`repair=3/8 id=repair-D0004-S000012 attempt=1/4`, and
`verification=1/2 id=verify-0004-chapter-4 attempt=1/4`. On resume, already
completed work is skipped while the counter retains its position in the full
stage plan.

Glossary candidate extraction groups up to eight complete chapters by default,
with a 9,000-token source ceiling and an explicit 32,768-token context ceiling.
Its schema-constrained
JSON is streamed for the same heartbeat, and model thinking is disabled for
this mechanical extraction role. High-confidence navigation and publication
matter is removed before chunking; summaries, appendices, and in-book
glossaries remain eligible. Before inference, the stage renders every
prompt, predicts its adaptive context bucket, reuses current checkpoints, and
runs equal-context requests together. The larger bounded batches reduce calls;
the prescan prevents shorter and longer batches from repeatedly reloading the
same model at different context allocations.

Glossary resolution is also bounded: it packs complete source-term groups into
8,000-token batches and uses only 16,384- to 32,768-token context windows by
default. All Chinese alternatives for the same source term remain in the same
batch. After the local passes, results are merged deterministically; only terms
that still have competing translations receive a compact conflict-resolution
pass. Resolution prompts are prescanned and grouped by context bucket, so the
main Qwen model stays loaded across similarly sized requests.

When `workflow.llm_glossary_review` is enabled, glossary approval follows the
same bounded pattern without sending the entire draft in one prompt. A cheap
deterministic screen approves evidence-backed, high-confidence entries that
have no source/target conflict. Only missing-evidence, low-confidence, generic,
`其他`, or colliding entries reach the model. Review batches use stable entry
IDs and return approve/reject/revise deltas, so the model cannot rewrite exact
English spellings or provenance. The stage checkpoints each decision batch,
groups equal 16,384- to 32,768-token context buckets, emits a per-entry approval
result, and writes `glossary.approval.report.json`. Human-file approval and
no-review approval remain model-free.

Translation performs the same content-free prescan after final chunking and
glossary selection. It groups pending chunks by predicted context bucket, pins
focused retries and marker placement to that bucket, and then restores spine
and paragraph order deterministically when publishing documents. Stable chunk
IDs and checkpoint hashes are independent of execution order.

Set `translation.boundary_context: adjacent-read-only` to append the immediate
previous and following source segments as immutable context. They carry no output
markers, are included when enforcing `translation.max_prompt_tokens`, and remain
read-only during initial generation, focused retries, and fallback harmonization.
The default is `none` for compatibility with existing job configurations.

By default, exhausting validation retries for an individual translation
passage does not abort the rest of the book. The stage checkpoints any valid
passages, preserves the exact marked source for each exhausted passage, reports
it as `result=pending-repair`, and continues with later chunks. These IDs are
forced into deterministic audit as high-severity `translation-deferred`
findings, then follow the normal repair, review, validation, and final human
review path. In repair, these source safety copies skip minimal located edits
and go directly through complete, glossary-aware segment translation; ordinary
findings retain the bounded-edit-first path. Compilation remains subject to
`workflow.compile_max_unresolved_review_segments`; deferred content therefore
cannot silently bypass the quality gate. Set
`workflow.defer_failed_translation_segments: false` to retain fail-fast
behavior for diagnostic runs.

### Build a shared series glossary

For a series that will be translated together, use a glossary-first phase for
all volumes before starting any translation. Create a valid empty placeholder at
the final series-glossary path and reference that same path from every volume's
configuration:

```json
{
  "entries": []
}
```

```yaml
glossary:
  series_glossaries:
    - ../glossaries/qel-series.json
workflow:
  require_glossary_review: true
  llm_glossary_review: false
```

Run every volume through decompilation, glossary extraction, and resolution so
each workspace is waiting at the glossary review gate. Before approving the
books independently, build one series review candidate from the resolved
drafts and emit synchronized per-volume overlays:

```powershell
book-agent build-series-glossary `
  --runs .\runs `
  --job-pattern "qel-series-*-en-zh" `
  --source-stage resolved `
  --output .\glossaries\qel-series.review.json `
  --book-output-dir .\glossaries\qel-book-reviews
```

This preparation pass is deterministic and model-free. It screens only
high-confidence boilerplate/ordinary-word noise, records every removal in the
series report, promotes exact cross-book consensus, and replaces matching terms
in every book overlay with that exact canon. At this pre-approval boundary,
lowercase generic terms are retained as `relevance_review_required` conflicts
rather than being silently removed or promoted merely because they recur. Review
`qel-series.review.report.json`, `series-overlays.report.json`, and the generated
`*.glossary.review.json` files. To run the deterministic prescreen and the
ID-constrained LLM reviewer against each synchronized overlay, combine
`--glossary` with `--llm-glossary`. Approve each overlay **without** `--resume`,
so preprocessing and translation remain pending:

```powershell
book-agent approve ".\runs\qel-series-01-en-zh" `
  --glossary ".\glossaries\qel-book-reviews\qel-series-01-en-zh.glossary.review.json" `
  --llm-glossary
book-agent approve ".\runs\qel-series-02-en-zh" `
  --glossary ".\glossaries\qel-book-reviews\qel-series-02-en-zh.glossary.review.json" `
  --llm-glossary
```

With both options, the external overlay—not the workspace's older resolved
draft—is the approval candidate. Its file hash participates in resumability,
and approved entries retain bounded evidence from the overlay and original
resolved draft. Using `--glossary` alone continues to mean that a human has
already reviewed the supplied file and therefore makes no approval LLM calls.

Once every volume has an approved glossary, rebuild the authoritative series
file from the approved boundary, overwriting the empty placeholder:

```powershell
book-agent build-series-glossary `
  --runs .\runs `
  --job-pattern "qel-series-*-en-zh" `
  --output .\glossaries\qel-series.json
```

The default approved-boundary command is deterministic and makes no LLM calls.
By default, a term must
occur in at least two approved book glossaries and every book containing it
must use the same translation. Single-book terms, ties, lower-consensus terms,
and books that approve multiple targets for one source term are excluded from
the usable glossary and recorded in the adjacent `.report.json`. An optional
`--consensus-ratio` above `0.5` permits majority promotion, but exact consensus
is the safer default. The command also writes a legacy `.txt` rendering.

To let Qwen adjudicate only the conflicts withheld by that deterministic pass,
add `--llm-conflicts` and optionally supply the same project configuration used
for the volumes:

```powershell
book-agent build-series-glossary `
  --runs .\runs `
  --job-pattern "qel-series-*-en-zh" `
  --output .\glossaries\qel-series.json `
  --llm-conflicts `
  --config .\configs\qel-series.yaml
```

This option does not send agreed or single-book terms to the model. It batches
only `report.conflicts` using the configured glossary-resolution chunk and
context limits, groups equal context buckets, and checkpoints results beside
the output in `<series-name>.conflict-checkpoints`. Conflict prompts use stable
pipeline IDs and require one compact decision per ID; English terms are input
context only and cannot be rewritten in model output. Each decision must select
an exact Chinese variant already present in the report or return `null` to keep
the term unresolved. Missing, duplicated, or out-of-scope IDs and unreported
variants fail deterministic validation. Repeated identical invalid responses
stop after their second occurrence. Accepted choices are materialized with the
exact original English and retained under `llm_decisions`.

Review the adjacent `.report.json`, then freeze the shared file for that series
run and resume every volume. Preprocessing late-binds the finalized shared file
and records its content hash, while each approved book glossary retains higher
precedence for documented volume-specific exceptions. Without
`--llm-conflicts`, this avoids a second LLM resolution/review pass. In either
mode, freeze the resulting shared file so every translation starts from the
same cross-volume terminology.

Do not rebuild the shared file while volumes are translating. If it must change
after preprocessing has completed, explicitly restart downstream work with
`book-agent retry WORKSPACE --stage preprocess --resume`; the changed series
hash then invalidates the affected preprocessing and translation artifacts.

Each extraction run writes `documents.screening.report.json` before inference
and `candidates.screening.report.json` afterward, retaining a raw merged
candidate artifact beside the screened one. Resolution deterministically
restores bounded source evidence that a model omits and writes
`glossary.draft.quality.report.json`. Approval writes
`glossary.quality.report.json` with category counts, evidence coverage, and
ambiguous generic-term warnings. These cheap reports support human quality
gates without adding another LLM pass.

When reviewed glossary assets already cover a book, extraction can be skipped
explicitly while retaining the normal resumable stage chain:

```yaml
glossary:
  extraction_enabled: false
  series_glossaries:
    - glossaries/my-series.json
  book_glossaries:
    - glossaries/my-book.reviewed.json
workflow:
  require_glossary_review: false
```

At least one seed, series, or book glossary is required when extraction is
disabled. The extraction stage records an empty candidate set without calling
Ollama; resolution then applies the normal seed/series/book precedence. Disable
the review gate only when every configured input has already been reviewed.

## Review and resume

With the default `require_glossary_review: true`, processing pauses after a
draft glossary is created. Review the recorded `glossary.draft.json` or legacy
text file, then approve it:

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

Alternatively, request an independent schema-constrained Qwen review of the
resolved draft and continue immediately:

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

To have the same reviewer evaluate an external candidate or generated series
overlay instead, supply both options:

```powershell
book-agent approve "D:\runs\my-job" `
  --glossary "D:\reviews\my-job.glossary.review.json" `
  --llm-glossary --resume
```

The reviewer may correct or remove supplied entries but cannot invent English
terms. It runs with thinking disabled and records its prompt/model hash,
attempts, metrics, review mode, and approved artifacts. For new jobs, setting
`workflow.llm_glossary_review: true` makes this automatic at the glossary gate;
the default remains human review. Setting `require_glossary_review: false` and
leaving LLM review disabled performs no independent review.

Qwen translation thinking is controlled separately by
`translation.thinking` and defaults to `false`. This is sent to Ollama as the
top-level SDK `think` parameter for every first-draft translation and targeted
repair request.

Targeted repair and semantic repair verification use a smaller adaptive 8K
context floor by default because they operate on individual passages. The
selector still grows to 16K or 32K when prompt and schema headroom require it:

```yaml
audit:
  repair_min_num_ctx: 8192
```

A 4K floor provides no practical reduction: the request selector reserves
4,096 completion/safety tokens and rounds upward, so every non-empty repair
request would still select at least 8K. Translation, glossary, and semantic
audit retain the shared `ollama.min_num_ctx` floor, which defaults to 16K.

Quantity validation has a separate typed, confidence-aware path. It extracts
values, units, ranges, approximation, comparators, polarity, and identifiers;
exact mismatches remain deterministic, while relational or multi-quantity
passages are sent to a structured semantic adjudicator. This avoids treating
literary number phrases as an ever-growing hard-coded equivalence list:

```yaml
audit:
  quantity:
    enabled: true
    mode: shadow
    model: gemma4:26b
    escalation_model: gemma4:31b
    max_num_ctx: 16384
    min_decision_confidence: 0.85
    batch_size: 1
    verify_repairs: true
```

When typed quantity validation is enabled it is the sole numeric authority;
the legacy raw-number checker is used only when this feature is disabled.
`shadow` records comparisons without publishing findings, `advisory` publishes
non-blocking findings, and `enforce` makes confirmed mismatches and unresolved
quantity judgments blocking. Audit reports include checked,
mismatched, and unresolved quantity counts; per-segment facts are stored in the
document audit and `audited/<hash>/quantity/` artifacts. Repair verification is
neutral A/B comparison with stable randomized ordering, so the verifier is not
told which translation is the repair.

Optional literary reprose runs after semantic repair and before its independent
review. Production profiles can limit calls to passages with observable risk
signals while still protecting markers, glossary terms, typed quantities,
negation, modality, comparisons, spatial relations, Latin identifiers, and
quotation structure:

```yaml
reprose:
  enabled: true
  model: qwen3.8:latest
  verifier_model: gemma4:31b
  candidate_mode: risk-filtered
  long_target_characters: 100
  candidate_sample_every: 0
  preserve_semantic_signatures: true
```

Every applied rewrite is still reviewed by the configured verifier and must be
materially better than the original. A protected-signature change keeps the
original text and records a rejected rewrite instead of relying on a later pass
to rediscover the drift.

First-draft model fallback is bounded and configurable. When a fallback is
configured, two failed Qwen contract attempts switch subsequent attempts to it:

```yaml
translation:
  fallback_models: []
  attempts_per_model: 2
  harmonize_fallback_with_primary: true
```

Inline formatting markers are source-owned. If a model invents marker IDs, the
translator removes them deterministically before validation. If a required source
marker is missing, already-valid passages are retained and only the affected passage
is sent back for a focused retry. A whole chapter-sized chunk is retried only when its
outer passage structure cannot be parsed.

For marker-only failures, the translator first reuses any saved complete draft and
requests schema-constrained marker placement. The common one-marker case requires
explicit `before`, `emphasized`, and `after` fields. Exact local windows may be expanded
with untouched draft prefixes/suffixes, but the marker-free result must remain
character-for-character identical to the saved translation.

To reset failed work units without discarding completed checkpoints:

```powershell
python -m book_agent retry "D:\runs\my-job" --stage translate --failed-only --resume
```

If the selected stage has no failed work units, this command prints a warning and
continues without resetting anything. With `--resume`, the workflow resumes from its
current checkpoint instead of being blocked.

Fallback is disabled by default to preserve a single translator's voice. If a
fallback model is explicitly configured and succeeds, its validated draft is
sent once to the primary Qwen model for style harmonization. The fallback
version remains the safety copy, and the harmonized version is used only when
it passes the same marker and language contract.
The shared `translation.thinking` switch is passed to primary, fallback, and
harmonization requests, so its default `false` also disables Gemma thinking.

Every raw model response—valid or invalid—is retained under
`translated/<translation-hash>/attempts/`. Validated chunk JSON is written to
`chunks/`, and assembled readable chapter drafts are the `.txt` files directly
under `translated/<translation-hash>/`. The active and attempts roots are also
stored in job metadata for `book-agent report --json`.

Glossary preprocessing defaults to non-mutating annotation mode:

```yaml
preprocessing:
  mode: annotate
  conflict_policy: skip
```

The original protected source text is preserved. The stage first selects
glossary entries relevant to each document, then narrows them again to
unambiguous longest matches in each translation chunk. Case-bearing English
terms require source-case agreement, and incompatible nested terms are left for
contextual translation instead of imposing contradictory targets. The prompt
supplies only that chunk's categories, aliases, and notes and requires sense
matching before applying an entry. `translation.use_glossary_strictly: true`
means the exact approved target is required only after the occurrence matches
the described sense. Set `preprocessing.mode: replace` only to reproduce the
legacy literal replacement workflow.

If `require_final_review: true`, the workflow pauses after repaired-draft
validation and before EPUB compilation:

```powershell
book-agent approve "D:\runs\my-job" --final --resume
```

Final approval applies only to the current repaired-validation hash. If the
translation or repair changes, approval is required again. Approval cannot
override unresolved validation or human-review issues.

When unresolved cases block compilation, the workflow now writes a complete
review package under `reports/`:

- `unresolved-review-segments.md` is the readable worksheet. It includes the
  immediate source context, raw/repair/reprose/current translation versions,
  audit findings, verifier history, and suggested fixes for every queued case.
- `unresolved-review-segments.json` contains the same evidence for tooling.
- `final-human-review.decisions.json` is the editable verdict file. It starts
  every case as `pending`, and records the current validation hash and exact
  source/translation text so stale decisions cannot be applied.

For each decision, change `pending` to `accept` or `replace` and add a concise
reason. A replacement must provide either the complete `translated_text` or
one or more exact `old_span`/`new_span` replacements. Pending, stale, or
text-mismatched worksheets fail closed. Apply the completed worksheet, approve
the resulting draft, and resume in one command:

```powershell
python -m book_agent.cli resolve-review `
    "D:\runs\my-job" `
    "D:\runs\my-job\reports\final-human-review.decisions.json" `
    --approve-final --resume
```

Application publishes `final-human-review-verdict.md` and `.json`, containing
the reviewer decision, reason, before/after translation, remaining queue, and
the final approval readiness state. The older compact
`{"resolutions": [...]}` input remains supported for automation.

Post-repair processing uses three resumable stages so Ollama does not switch
models for every rejected document:

- `review_repaired`: Gemma reviews all repaired drafts and collects failures.
- `repair_review`: Qwen repairs the complete collected batch.
- `validate_repaired`: deterministic checks run over every final draft, while
  Gemma independently verifies only problem segments changed by
  `repair_review`; repairs that already passed review are skipped. Review state
  derived solely from a deterministic rejection is retired when the current
  deterministic policy no longer reproduces that rejection.

The feedback prompt uses short English→Chinese or Chinese→English examples for
marker relocation, missing grammar, and full-clause rewriting. If Qwen's
correction is rejected by deterministic checks or final independent review, the
last accepted text is restored and the segment enters human review. Final
validation never starts another model repair loop.

Workflow progress lines report both execution state and quality outcome. For
example, a stage may print `[completed]` with `result=repair-required` or
`result=pending-review`; successful execution is therefore not confused with a
clean quality result. Skipped, paused, cancelled, failed, and unknown validation
outcomes are also labeled explicitly without printing book content.
At the end of each processing invocation, the same live metrics are written to
`reports/session-performance-<session>.json`. The file includes model-call and
token totals, stage plans, outcome counts, and time/calls/tokens normalized per
1,000 source segments, making differently sized books directly comparable.
When the diagnosis is specifically duplicate text adjacent to an inline marker,
the validator first applies a deterministic exact-match deduplication and checks
the normal marker contract before making another model call.
Unambiguous quoted glossary replacements use the same deterministic path.
Preference-only findings that explicitly describe the existing translation as
correct or grammatically acceptable are retained with an `accepted` disposition;
they remain visible in artifacts but do not block EPUB compilation.
Quoted third-language phrases retained verbatim from the source, such as French
dialogue containing accented letters, are not misclassified as untranslated
English. Copied English quotations are still reported.
repair-stage draft remains unchanged as a safety copy; successful corrections
are stored as separate `*.validated.json` and `*.validated.txt` artifacts and
are the only drafts accepted by compilation and final EPUB text validation. For
an older unresolved run:

```powershell
python -m book_agent retry "D:\runs\my-job" --stage repair_review --resume
```

Use `--stage review_repaired` instead when the initial Gemma decisions must also
be regenerated. Retrying `repair_review` preserves that completed review pass.

Resume an interrupted or paused job without repeating current artifacts:

```powershell
book-agent resume "D:\runs\my-job"
```

## Status, reports, and retry

```powershell
book-agent status "D:\runs\my-job"
book-agent status "D:\runs\my-job" --json
book-agent report "D:\runs\my-job"
book-agent report "D:\runs\my-job" --json
```

`report` includes captured metadata, every recorded artifact, and the report
artifact paths. JSON output is intended for scripts.

Named production configurations are captured with a profile version and source
file hash. `status` reports source-config drift, and the effective field-level
change can be inspected before retrying any stage:

```powershell
book-agent config-diff "D:\runs\my-job" --config configs\my-production.yaml
```

Reset one stage and all of its downstream dependents, then optionally resume:

```powershell
book-agent retry "D:\runs\my-job" --stage translate --resume
```

Valid stage names are shown by `book-agent retry --help`. Existing files are
retained for forensic inspection, but their state and artifact records are
invalidated so they cannot be mistaken for current output.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | Command succeeded or workflow completed |
| `1` | Validation, configuration, model, stage, or operational failure |
| `2` | Workflow paused at a review gate |
| `130` | User or cooperative model-generation cancellation |

## Tests

The suite is offline by design: no test contacts Ollama, so a local model
runtime is not required to run it.

```powershell
python -m pytest
```

`pyproject.toml` configures the run, so `pytest` alone discovers `tests/`.
Add coverage when checking the gate:

```powershell
python -m pytest --cov --cov-report=term-missing
```

The suite is pytest throughout: plain assertions, `pytest.raises` for expected
failures, and `pytest-subtests` for the table-driven cases. There is no
`unittest.TestCase` inheritance, so `python -m unittest` will not discover it.

Every workspace and EPUB test uses temporary paths. The EPUB under `sample`
is treated as read-only.

Continuous integration runs the suite on Ubuntu and Windows against Python
3.11 and 3.12, plus the coverage gate; see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## License

The project code and documentation are available under the [MIT License](LICENSE).
The EPUB fixtures in `sample/` retain their embedded Project Gutenberg terms
and are excluded from the MIT grant; see [sample/README.md](sample/README.md).
Ollama models are not distributed by this repository and remain subject to
their respective model licenses.
