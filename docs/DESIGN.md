# Ollama Translator Design

> **Current-operation note (2026-09-09):** This document records the durable
> architectural rationale. For executable defaults, role assignments, review
> commands, context recovery, and EPUB options, use
> [Production Operations](OPERATIONS.md) and `config.example.yaml`. Historical
> benchmark timings and model assignments below are not release guarantees.

## 1. Purpose

Ollama Translator automates EPUB-to-EPUB and RTF-to-EPUB
English-to-Chinese and Chinese-to-English translation workflows using local Ollama models. It replaces a sequence of
manually invoked scripts with a resumable, observable, and testable pipeline.

The initial target model is `qwen3.8:latest`. The installed model reports a
262,144-token context window. The application will configure Ollama with a
131,072-token context and keep the normal working budget at or below 100,000
tokens so prompts, glossaries, generated output, and safety headroom all fit.
Per-request adaptive context allocation starts at 16,384 tokens and grows by
powers of two up to that ceiling, retaining completion headroom while avoiding
the GPU-memory cost of allocating 131,072 tokens for ordinary chapter calls.
Targeted repair and repaired-draft semantic verification override only that
floor to 8,192 tokens; larger requests still grow through the same adaptive
power-of-two tiers. A 4K tier is intentionally omitted because the selector's
4,096-token completion/safety reserve makes 8K the smallest usable tier.
Glossary candidate extraction, semantic auditing, repair comparison, quantity
adjudication, and reprose are independently configurable. The current example
configuration uses Qwen for extraction/translation/repair, Gemma 31B for the
semantic gate and reprose verification, and Gemma 26B for bounded comparison
and first-level quantity work. This separation is intentional: a role change
must be validated by a controlled benchmark rather than inferred from model
size or throughput.
Audit and verification thinking default to disabled because these are scoped,
schema-constrained classification tasks; it remains an explicit opt-in.
Semantic issue provenance is assigned by the audit stage rather than trusted to
model output. Missing repair guidance is normalized from the concrete finding;
unknown or out-of-scope segment IDs remain validation failures.
Glossary resolution, first-pass translation, and targeted repair remain on
`qwen3.8:latest`. Each role is explicit in configuration and included in stage
input hashes.
First-draft contract retries stay on the primary Qwen model by default.
Optional bounded failover moves through an explicitly configured fallback
list. A valid fallback draft may receive one primary-Qwen style-harmonization
pass; it replaces the safety draft only after the same structural validation.
Raw responses are retained and the actual model is recorded with each attempt.

## 2. Design principles

1. **Deterministic orchestration.** A Python state machine owns paths, stage
   order, validation, retries, checkpoints, and compilation.
2. **Bounded agency.** The LLM performs semantic work and selects only from
   explicitly allowed recovery actions. It never executes arbitrary commands.
3. **Resume safely.** Every completed artifact is validated, hashed, and
   recorded before its stage is marked complete.
4. **Structured data internally.** Glossaries and decisions use validated JSON
   models. The legacy `English:Chinese:note` representation is an export format.
5. **Fidelity before style.** Prose style can change expression but cannot
   override completeness, glossary consistency, structure, viewpoint, or tone.
6. **Tests are part of the interface.** Every public function needs success,
   boundary, and failure-path tests before a stage is considered complete.
7. **Local-first.** Translation works without network services other than the
   local Ollama API. Web verification is an explicit optional capability.

The initial language directions are strictly `en-zh` and `zh-en`. Language
direction is stored in job configuration and is never inferred independently
for each chunk. Additional languages are out of scope until both initial
directions pass the same end-to-end validation standards.

## 3. Why no agent framework initially

The workflow is mostly a fixed sequence with a small number of validation
branches. LangGraph and AutoGen would add runtime concepts and dependencies
without improving the translation itself. The first implementation uses plain
Python, Pydantic, SQLite, and the official Ollama client.

MCP is also unnecessary internally. A future MCP adapter may expose controlled
operations such as `translate_book`, `get_job_status`, and
`retry_failed_chapters` to external AI applications without changing the core.

## 4. High-level workflow

```text
EPUB or RTF
  -> inspect and decompile
  -> discover and normalize chapters
  -> extract glossary candidates by chunk
  -> merge, resolve, categorize, and validate glossary
  -> optional independent Qwen or human glossary approval
  -> preserve source and select context-relevant glossary entries
  -> first-pass translation
  -> deterministic and semantic audit
       -> pass
       -> repair only flagged paragraphs
       -> human review
  -> validate repaired paragraphs
  -> compile EPUB
  -> parse and validate the compiled document and generate report
```

## 5. Components

```text
ollama-translator/
  book_agent/
    cli.py               CLI boundary
    config.py            validated configuration
    workflow.py          state-machine coordinator
    state.py             SQLite job and artifact state
    ollama_client.py     common Ollama request policy
    schemas.py           structured domain objects
    token_budget.py      context and chunk budgeting
    styles.py            built-in/custom prose profiles
    stages/              one module per workflow stage
    validators/          deterministic artifact checks
  prompts/               versioned task and style prompts
  tests/                 unit, integration, and fixture tests
```

Each stage accepts typed inputs and returns typed results. Stages do not infer
global paths and do not silently continue after failures. The workflow layer is
the only component allowed to advance job state.

## 6. Context and chunk budgeting

`num_ctx` is not the permitted source length. Input and output share the model
context. The default budget is:

| Allocation | Tokens |
|---|---:|
| Working limit | 100,000 |
| System/task/style prompts | 10,000 |
| Relevant glossary | 5,000 |
| Source chunk | 40,000 |
| Expected output | 40,000 |
| Working reserve | 5,000 |
| Additional model-context headroom | 31,072 |

The chunker computes the source allowance rather than assuming it. Glossary
extraction defaults to up to eight complete spine documents per independently
checkpointed request with a 9,000-token source ceiling and a 32,768-token
context ceiling. For ordinary published
chapters of roughly 2,000–6,000 tokens, this yields a bounded multi-chapter
working scale without opaque whole-book calls. A content-free prescan renders
each extraction prompt, predicts its adaptive context allocation, and groups
equal context buckets before inference. Translation performs the same prescan
after prompt-constrained chunking; publication still restores spine order.
Translation chunks must split at structural boundaries, preferably chapters,
scenes, and paragraphs, never in the middle of a marker or HTML element.

## 7. Glossary model

Candidate extraction defaults to `glossary.extraction_model: qwen3.8:27b` with
thinking disabled so bounded source chunks receive restrained,
schema-constrained output. Its default source/context ceilings are 9,000 and
32,768 tokens respectively, favoring faster local inference over sparse 64K
requests. Structured JSON is streamed and accumulated for
final schema validation, allowing the CLI's 2,000-character heartbeat to remain
active. Candidate
resolution remains on the main `ollama.model` because it performs the more
interpretive work of choosing translations, categories, aliases, and conflicts.
It does not receive the whole merged candidate list at once. Complete
source-term groups are packed into 8,000-token local batches with a default
16,384- to 32,768-token context range, then merged deterministically. A second
compact model pass is scheduled only for source terms that still have multiple
Chinese translations. This preserves cross-entry comparison where it matters
without requiring a large global context or repeatedly revisiting already
resolved terms. Resolution requests are prescanned and ordered by context
bucket in the same way as extraction and translation.
Changing either model invalidates only its affected stage and downstream work.
The approval gate can accept a human-edited file or run a second independent,
schema-constrained Qwen review. Before inference, deterministic checks approve
evidence-backed, high-confidence entries without source/target conflicts and
route only questionable entries to the reviewer. LLM approval packs complete
source-term groups into 8,000-token batches and returns one approve, reject,
revise, or pending delta for each stable pipeline ID. English spelling and
evidence never appear in model output and are restored by the pipeline. The
stage uses a default 16,384- to 32,768-token context range, checkpoints each
decision batch, emits per-entry results plus an approval report, and merges the
accepted entries deterministically. Repeated identical invalid decisions stop
after the second occurrence. Human and no-review approval do not invoke a
model. Qwen thinking is disabled for this mechanical review. First-draft
translation exposes its own `translation.thinking` switch, which defaults to
false and is passed directly to Ollama.

The canonical glossary is JSON with entries containing:

- English term
- Chinese translation
- Chinese note
- category
- aliases
- evidence locations
- confidence

Allowed categories are `人名`, `地名`, `组织`, `物品`, `技术`, `概念`, `术语`,
and `其他`. Validation rejects empty terms, unsupported categories, forbidden
parentheses in terms, Chinese-only source terms, and duplicate normalized pairs.

Alternative Chinese translations are stored as distinct entries. A renderer
creates the categorized colon-separated format required by legacy scripts.

Configured glossary precedence is explicit and term-local:

```text
book glossary > series glossary > seed glossary > extracted candidates
```

Alternatives at the same priority are retained. A higher-priority source
replaces all lower-priority alternatives for that English term. The resolver
may change a candidate translation or discard a false positive, but it may not
invent English terms outside the extracted candidate set.

Approved book glossaries are aggregated deterministically by the
`build-series-glossary` command. Promotion is evidence-based: a term must recur
in a configured minimum number of books and its target must meet the configured
cross-book consensus ratio. Exact consensus is the default. Translation ties,
within-book alternatives, and below-threshold variants are withheld in a
machine-readable conflict report. Category disagreement is reported but does
not discard an otherwise unanimous translation; ambiguous aliases are removed.
The optional `--llm-conflicts` mode runs only after this deterministic pass and
receives only withheld conflicts plus their approved per-book glossary entries.
It may select an exact reported Chinese variant or leave a term unresolved; it
cannot invent a source term or target. Requests use the glossary-resolution
batch/context settings, are ordered by predicted context bucket, and are
content-addressed in an adjacent checkpoint directory. Every choice and short
rationale remains in the report for audit. Ordinary merging never calls a
model.

Candidate extraction has deterministic hygiene boundaries before and after
inference. A conservative title/content classifier removes navigation,
dedications, praise, author catalogues/biographies, acknowledgements, and
copyright pages before chunking, even when EPUB filenames are generic; its
decisions are written to `documents.screening.report.json`. Summaries,
appendices, and in-book glossaries remain content. The raw merged candidates
remain as an artifact; candidates supported only by recognized publication
documents and unmistakable lowercase ordinary terms are removed into
`candidates.screening.report.json`. Borderline generic terms are retained and
reported. Resolution and LLM review restore evidence from their exact input
candidates rather than trusting model-authored provenance, and resolution
writes `glossary.draft.quality.report.json`. Approval similarly emits a cheap
`glossary.quality.report.json` covering evidence, categories, and suspicious
generic terms, so quality accounting does not require an additional model.

For series-first preparation, `build-series-glossary --source-stage resolved`
aggregates the resolved drafts before independent book approval. With
`--book-output-dir`, it writes review overlays in which every matching shared
term is replaced by the exact promoted series entry while book-only terms are
preserved. Lowercase generic candidates are withheld as
`relevance_review_required` conflicts at this boundary; cross-book recurrence
alone is not treated as proof that an ordinary-looking word is durable series
terminology. Humans review and approve those overlays, after which the command is
run again at its default `approved` boundary to publish the authoritative
series glossary. This makes cross-volume canonicalization precede translation
without granting an LLM authority to rewrite already-agreed terms.
Optional series-conflict adjudication is also ID-based: the model returns one
variant choice per pipeline-owned conflict ID, while the pipeline restores the
exact English term and rejects incomplete, duplicated, or unreported choices.
Decision checkpoints are versioned independently from the final report shape.

For a coordinated series run, preprocessing late-binds the current configured
series glossary after every volume has passed its glossary approval gate. The
approved per-book glossary has higher precedence, and the shared file's content
hash participates in both stage and document hashes. This permits one
cross-volume merge, with optional conflict-only adjudication, before
translation; a later shared-file change requires an explicit retry
from `preprocess` so translated artifacts cannot be mixed across glossary
versions.

Preprocessing defaults to `annotate`: it preserves protected source text and
selects the glossary subset relevant to each document. Categories, aliases,
and notes are passed to the translator, which must first match the described
sense before applying an approved target. This avoids corrupting polysemous
ordinary uses. A source term with multiple approved targets is reported so the
translation prompt can choose contextually; configurations may instead make
such ambiguity a hard error. `replace` remains an explicit legacy mode using
longest-match precedence. English matching is case-insensitive with word
boundaries, while Chinese matching is exact and longest-first.

## 8. Translation styles

Built-in profiles:

- `faithful`: literal where natural, maximum semantic and structural fidelity
- `natural`: fluent contemporary Chinese
- `literary`: polished literary prose with controlled imagery and rhythm
- `concise`: direct prose without deleting source information
- `classic`: restrained and moderately formal diction
- `young_adult`: accessible contemporary diction
- `fantasy`: literary fantasy register and stable world-building terminology
- `science_fiction`: precise technical register and stable scientific terms
- `custom`: content loaded from a user-supplied UTF-8 prompt file

The assembled prompt has a strict precedence order:

1. completeness and source fidelity
2. structural marker preservation
3. approved glossary
4. source characterization, viewpoint, tense, and register
5. selected prose style

The prose profile is applied during the first translation pass. Auditing does
not restyle acceptable prose. Repair prompts receive the same profile only to
keep corrected segments consistent with their surrounding translation.

There is no general A/B translation picker or whole-book optimization pass.
Post-translation work is diagnostic and targeted: detect untranslated source,
duplicate sentences or paragraphs, omissions, broken identifiers/markup,
glossary violations, and clear semantic errors; then regenerate only the
affected segments and revalidate them.

Style profiles describe prose qualities independently of language. Prompt
assembly adds direction-specific instructions so English source becomes Chinese
output for `en-zh`, while Chinese source becomes English output for `zh-en`.

Each profile may include a bounded naturalness/de-AI overlay. This is not a
separate rewriting pass. It tells the translator to retain the author's
idiolect and sentence variation while avoiding model-added formulaic
transitions, rhetorical triplets, uniform rhythm, inflated abstraction,
generic lyrical padding, over-explanation, and repeated restatement. The
default strength is `conservative`; `moderate` is available for texts showing
stronger model habits. Intentional source repetition must remain intact.

## 9. Validation and recovery

Every translated chunk carries stable paragraph identifiers. Deterministic
validation checks identifiers, ordering, emptiness, length ratios, Chinese text
presence, protected tokens, markup, and required glossary substitutions.

Inline XHTML structure uses nested protected markers (`<I000>...</I000>`) in
translation text. Each marker maps to an original inline element and stable XML
path. Translation validation requires the exact marker sequence. Compilation
parses the protected marker tree and places translated text back into the
original emphasis, link, span, ruby, and other inline elements without changing
their tags or attributes. This keeps package structure and formatting while
preventing untranslated source fragments from surviving inside inline nodes.

Validation produces one of four controlled outcomes:

- `PASS`: save and advance
- `RETRY`: regenerate the complete chunk with diagnostics
- `REPAIR`: regenerate only identified paragraphs
- `REVIEW`: pause after the configured retry limit

Deterministic audit runs over every segment. Semantic audit is selective: it
reviews deterministic failures, long passages, number/unit-sensitive passages,
and optional periodic samples. Immediate neighboring segments included for
continuity are also valid finding targets, so their review cost is not wasted.
Semantic results do not use subjective numeric quality scores; every finding
must identify an in-scope segment, severity, category, concrete explanation,
and actionable fix. Targeted repair changes only flagged segments. Repairs
originating from semantic findings receive a separate semantic verification,
while the complete assembled document is always re-run through deterministic
audit. Exhausted or unresolved repairs remain unchanged and enter the human
review queue.

Post-repair quality control is split into three persisted, model-homogeneous
stages to avoid repeatedly unloading and reloading large models:

1. `review_repaired` runs deterministic preflight followed by the complete
   Gemma verification pass and records rejected segments without changing text.
2. `repair_review` keeps Qwen loaded while repairing the collected failure
   batch and publishes separate review-repaired candidates.
3. `validate_repaired` audits every final draft deterministically and keeps
   Gemma loaded for one independent pass over only the problem segments changed
   by `repair_review`. It publishes compile-gate output without another repair.

The final verifier's concrete failure becomes a review finding rather than an
immediate interleaved repair call.
The feedback prompt includes compact, direction-aware examples for duplicate
marker words, missing predicates, and unnatural literal clauses. The examples
teach repair operations rather than book-specific wording. If Qwen's bounded
correction is still rejected, the last accepted text is restored and the
segment enters human review; final validation does not rewrite it again.
Exact text duplicated immediately beside an inline marker is repaired
deterministically when the verifier explicitly diagnoses duplication; this
avoids repeated model calls for a mechanical, structurally provable edit.
Quoted glossary instructions of the form “change all instances of X to Y” are
also applied deterministically and passed through the normal segment contract.
Medium-or-lower findings that explicitly concede the translation is correct or
grammatically acceptable and state only a stylistic preference are recorded as
`accepted` rather than blocking compilation after fruitless rewrite attempts.
The semantic-audit prompt forbids these preference-only findings, and scope
normalization filters them defensively if a model emits one anyway.
The deterministic untranslated-text check is source-aware for quoted
third-language text: a quoted Latin-script phrase containing a diacritic may be
retained when the complete phrase occurs verbatim in the source. This permits
intentional dialogue such as a French lesson while ordinary copied English
dialogue remains a blocking finding.
Validation publishes corrected documents separately from repair-stage drafts,
preserving the earlier version as a safety artifact. Compilation reads only
these revalidated documents, and final EPUB validation compares extracted text
against those same artifacts rather than the superseded repair-stage drafts.
A structurally invalid or semantically rejected
feedback repair remains in review and compilation stays blocked.

The decompile manifest includes a source-format discriminator. EPUB uses its
preserved package tree for lossless resource and markup reconstruction. RTF is
decoded by a bounded, non-executing parser into the same chapter/segment
contract used by every middle stage. Its compiler emits an EPUB 3 package with
one XHTML document per normalized chapter, and its final validator reopens that
EPUB and compares visible text to the accepted repaired segments. Rich RTF
styling and embedded objects are deliberately outside this adapter's fidelity
contract.

Partial files are written to temporary paths and atomically promoted only after
validation. An interrupted request never becomes a completed artifact.

## 10. Persistence and reproducibility

Each book gets an isolated run directory containing the source, resolved
configuration, SQLite state, prompts, glossary candidates, translations, logs,
reports, and the compiled output document. State records source hashes, artifact hashes, model
tags, prompt hashes, attempt counts, durations, and validation results.

Changing the source, prompt, model, style, or glossary invalidates only the
dependent stages. Existing unrelated user files are never overwritten by
default.

## 11. Testing strategy

Tests are organized into:

1. **Unit tests:** every public function and validation rule.
2. **Contract tests:** mocked Ollama responses, including malformed JSON,
   truncated streams, thinking output, error chunks, and timeouts.
3. **Stage integration tests:** temporary directories and small text fixtures.
4. **Format smoke tests:** tiny legal EPUB and obfuscated synthetic RTF fixtures
   through decompile, compile, and parse-back validation.
5. **Resume tests:** interruption at every stage followed by safe resumption.
6. **Regression tests:** anonymized examples of previously observed failures.

Tests must not require a running Ollama server unless explicitly marked as local
integration tests. Network access is disabled in the default suite.

Automated package smoke coverage uses an invented EPUB fixture. Retained
public-domain EPUBs under `sample/` are for manual inspection and licensing
verification only; no automated test may assert against their prose, characters,
chapter counts, or package-specific layout.

## 12. Future extensions

- Optional web-backed terminology verification
- Embedding-assisted relevant-glossary selection
- Local browser review interface
- Multiple local worker processes when hardware permits
- MCP server adapter
- LangGraph adapter if distributed orchestration becomes necessary

## 13. Command-line operations

`book-agent` is the only operational entry point. `run` creates an isolated
workspace and executes the thirteen typed stages; `resume` reopens the captured
configuration and advances only incomplete work. The orchestrator contains no
translation logic and delegates every mutation to the existing stage APIs.

`status` and `report` read SQLite state without changing it. `retry` explicitly
invalidates a selected stage and all transitive dependents. `approve` publishes
a reviewed glossary or records approval for the exact current repaired-draft
validation hash. Glossary and optional final-draft review gates return a paused
outcome instead of presenting an incomplete job as a failure.

Dry-run validates the source EPUB/RTF suffix and resolved configuration, then
prints the planned stages without creating a workspace or contacting Ollama.
Before a real model-backed stage begins, both configured model roles are
checked for the requested context capacity. Terminal results use stable exit
codes: `0` complete, `1` failed, `2` paused, and `130` cancelled.

The common Ollama boundary emits content-free generation events. The CLI shows
request start, a heartbeat for each crossed 2,000-character visible-output
interval, and exact prompt tokens, output tokens, decode rate, and elapsed time
at completion. It never emits prompts, generated prose, or thinking content.
The file summary aggregates backend total, model-load, prompt-evaluation, and
decode durations for each stage and for the complete invocation.
Every bounded model work loop supplies a stable work-unit ID, current/total
position, and retry position. Translation and extraction use chunk counters;
semantic audit uses global batch plus document counters; repair and repaired-
draft verification use target counters; one-shot glossary resolution and review
report `item=1/1`.
