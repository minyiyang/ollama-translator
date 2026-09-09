# Ollama Translator Working Plan

> **Historical execution record.** Last reconciled: 2026-09-09. The phase
> checklists below preserve implementation history, including old test counts,
> fixture names, and experimental model assignments. Current operational
> behavior is documented in [Production Operations](OPERATIONS.md); the source
> configuration and automated tests are authoritative for release decisions.
>
> **Current suite:** 629 pytest cases plus 55 subtests, offline. The per-phase
> verification notes below record what passed when each phase closed; the
> public-domain EPUB assertions they describe were later replaced by synthetic
> fixtures, so those figures do not describe the suite as it stands.

This is the living implementation plan. Update status and acceptance notes as
work progresses. `DESIGN.md` is authoritative for architecture; this file is
authoritative for execution order.

Status values: `TODO`, `IN PROGRESS`, `DONE`, `BLOCKED`.

## Definition of done for every task

- Public behavior is documented.
- Every new public function has success, boundary, and expected-failure tests.
- Tests do not depend on the network by default.
- Paths are supplied or derived inside an isolated run directory.
- Failures are explicit; no stage silently reports success.
- Relevant tests and the complete fast suite pass.

## Phase 0: Project foundation

Status: **DONE**

- [x] Record architectural design.
- [x] Record implementation and testing plan.
- [x] Create installable Python package and CLI entry point.
- [x] Add explicit `en-zh` and `zh-en` language-direction configuration.
- [x] Add validated application configuration.
- [x] Add translation-style profiles and custom profile loading.
- [x] Add context-token budget calculation.
- [x] Add canonical glossary schemas and legacy renderer.
- [x] Add SQLite state primitives.
- [x] Add tests for every implemented foundation function.
- [x] Configure optional pytest coverage reporting in `pyproject.toml`.

Verification at close: 59 standard-library unit tests passed. Pytest and coverage
are declared as optional development dependencies but are not installed in the
current environment yet.

Acceptance: the foundation suite runs without Ollama and covers all public
foundation functions.

## Phase 1: Common Ollama client

Status: **DONE**

- [x] Health and model-existence checks.
- [x] Read model metadata and verify requested context capacity.
- [x] Text generation with streaming.
- [x] Structured generation with Pydantic JSON schema.
- [x] Separate thinking from final content, including legacy think tags.
- [x] Detect HTTP errors and mid-stream error objects.
- [x] Timeout, bounded retry, backoff, and cancellation.
- [x] Capture token counts, stop reason, and durations.
- [x] Mocked contract tests for every response and failure mode.

Verification at close: 86 unit/contract tests passed without Ollama generation.
The local smoke check confirms `qwen3.8:latest` is installed and reports a
262,144-token context, satisfying the 100,000-token working requirement.

Acceptance: no other module makes direct HTTP or Ollama SDK calls.

## Phase 2: Job state and workspace

Status: **DONE**

- [x] Create isolated job workspace.
- [x] Hash source, configuration, prompts, glossary, and artifacts through
      canonical file/text/named-input hashing primitives.
- [x] Track job, stage, chapter, chunk, attempt, and validation state.
- [x] Implement atomic artifact promotion.
- [x] Implement resume and selective dependency-based invalidation.
- [x] Test interruption and resumption at every stage state.

Verification at close: 122 unit/contract tests passed. A disposable workspace made
from the read-only Alice sample captured the complete 189,231-byte EPUB,
initialized all 13 stages, matched its SHA-256, reopened successfully, and left
the original sample unchanged.

Acceptance: killing the process cannot make an incomplete artifact appear
complete, and a resumed run does not repeat valid work.

## Phase 3: Decompile and document inventory

Status: **DONE**

- [x] Replace the text-only behavior of `epub_decompiler_v7.2.py` with a typed,
      resumable decompile stage.
- [x] Preserve the complete original package, metadata document, and resource inventory.
- [x] Discover OPF manifest, spine chapter order, EPUB 3 navigation, NCX fallback,
      and stable document/segment identifiers.
- [x] Normalize source text without losing protected structural markers.
- [x] Add tiny EPUB fixtures and malformed-input tests covering unsafe paths,
      duplicate members, size limits, XML safety, missing files, and bad spine IDs.
- [x] Use the read-only `sample/Alice's Adventures in Wonderland by Lewis
      Carroll.epub` as a real-book smoke fixture via temporary copies.

Verification at close: 144 tests passed. The public-domain EPUB
fixture produced 21 manifest items, 15 ordered spine documents, 16 navigation
entries, 24 preserved package resources, and 804 stable text segments, and its
source hash was unchanged.

Acceptance: an input EPUB produces a deterministic chapter and resource
manifest suitable for reconstruction.

## Phase 4: Glossary pipeline

Status: **DONE**

- [x] Split all chapters into extraction chunks; do not sample only largest files.
- [x] Extract structured candidates independently per chunk with evidence IDs.
- [x] Route glossary candidate extraction to configurable `gemma4:26b` while
      retaining `qwen3.8:latest` for candidate resolution.
- [x] Batch five complete chapters with a 30,000-token ceiling, disable
      extraction thinking, and stream structured output for live
      2,000-character heartbeats.
- [x] Merge candidates deterministically while retaining valid alternatives.
- [x] Resolve translation, category, notes, aliases, false positives, and conflicts using the LLM.
- [x] Validate and render categorized legacy glossary output.
- [x] Support book, series, seed, and extracted sources with explicit
      `book > series > seed > extracted` precedence.
- [x] Add optional human approval checkpoint with validated JSON or legacy input.
- [x] Add optional independent Qwen glossary review with strict term scope,
      checkpointed metrics, non-thinking structured output, and a CLI approval flag.
- [x] Test duplicates, alternatives, colons, parentheses, Chinese-only terms,
      malformed model responses, and category errors.

Verification at close: 169 offline tests passed. The public-domain EPUB
fixture confirmed all 804 nonempty source segments were covered exactly once
across multiple bounded chunks at the default five-chapter/30,000-token
extraction budget. The extraction model is independently configurable and
defaults to `gemma4:26b`; the earlier live `qwen3.8:latest` smoke call produced
schema-valid bilingual entries and evidence through Ollama structured output.

Acceptance: every final glossary is schema-valid and round-trips through its
legacy renderer/parser without data loss allowed by that format.

## Phase 5: Preprocessing and relevant glossary selection

- [x] Preserve original source text by default and use preprocessing for
      document-level glossary relevance selection rather than literal replacement.
- [x] Add explicit legacy `replace` mode and context-sensitive glossary prompt
      rules for polysemous terms.

Status: **DONE**

- [x] Replace unambiguous glossary terms safely with longest-match precedence.
- [x] Preserve segment IDs and protected markup.
- [x] Select relevant entries per document by exact/alias match.
- [x] Report and skip ambiguous alternatives by default, with optional hard-error policy.
- [x] Leave local embedding retrieval as an optional later enhancement.
- [x] Test overlapping terms, capitalization, punctuation, aliases, ambiguity,
      bidirectional replacement, and Unicode.

Verification at close: 185 tests passed, covering
English-to-Chinese and Chinese-to-English preprocessing, per-document
resumption, conflict policy, and protected markers. The public-domain EPUB
fixture selected two recurring names and performed more than 100 deterministic
replacements without changing the source.

Acceptance: preprocessing is deterministic and never modifies protected
structure.

## Phase 6: First-pass translation

Status: **DONE**

- [x] Structural chunking with adaptive token budget.
- [x] Assemble task, glossary, and selected style prompts.
- [x] Apply the configured naturalness/de-AI overlay during first-pass translation.
- [x] Stream translations through the shared client.
- [x] Preserve stable segment IDs and accept inline markup inside protected markers.
- [x] Checkpoint each validated chunk.
- [x] Add bounded retry prompts using validator diagnostics.
- [x] Test every built-in style and custom style loading.
- [x] Make built-in style profiles language-neutral for both `en-zh` and `zh-en`.

Verification at close: 204 offline tests passed. Translation tests cover adaptive
budgeting, oversized-segment splitting, exact marker order, inline markup,
direction-aware glossary prompts, target-language checks, validated chunk
checkpointing, bounded retries, failure persistence, resumption, and both
translation directions. The public-domain EPUB fixture confirmed all 804 source
segments were covered by translation chunks within the calculated source budget
without changing the source EPUB. A live `qwen3.8:latest` smoke run translated Alice
Chapter I as one 27-segment chunk: all markers passed, no Latin prose remained,
and only the two intentional separator rows matched the source. The request used
3,634 prompt tokens and 6,985 evaluated output tokens and took about 8m24s on the
current 30% CPU / 70% GPU allocation.

Acceptance: interrupted book translation resumes at the first incomplete chunk,
and style never overrides hard translation constraints.

## Phase 7: Translation audit and targeted repair

Status: **DONE**

- [x] Deterministic completeness, structure, target-language, length, untranslated-text,
      duplicate-content, and glossary checks.
- [x] Audit formulaic model-like prose conservatively and distinguish it from
      intentional source style or repetition.
- [x] LLM semantic audit for omissions and mistranslations only where needed.
- [x] Route semantic audit and repair verification to `gemma4:31b`, separately
      from the `qwen3.8:latest` translation and targeted-repair model.
- [x] Prefer flagged segments plus local context over full-chapter LLM review;
      the Alice full-chapter structured audit took about 13m04s.
- [x] Remove subjective numeric audit scores and require scoped issues with
      nonempty, actionable fixes; the smoke auditor had interpreted 0-100 fields
      as 1-10 and returned incomplete replacements despite schema-valid JSON.
- [x] Repair only flagged segments; do not restyle or rewrite passing text.
- [x] Revalidate repaired segments and the assembled translation.
- [x] Feed failed semantic verification into one bounded segment-only repair cycle.
- [x] Preserve repair drafts and compile only separately published revalidated documents.
- [x] Create per-chapter and whole-book reports.
- [x] Route repeated failures to a human review queue.

Verification at close: 240 offline tests passed. Tests cover both translation
directions; structure, language, untranslated phrase, length, glossary,
mojibake, duplication, and conservative AI-style checks; bounded semantic
selection and batching; exact model-result scope; diagnostic retries and
resumption; one-segment repair; unchanged/invalid repair rejection; repaired
draft assembly; semantic repair verification; and final human-review routing.
The saved single-chapter draft produced zero false deterministic failures and
selected 9 of 27 high-risk segments, including the observed miles/unit defect;
immediate neighbors already present in each prompt are also eligible for
concrete findings without increasing model input.

Acceptance: every repaired document has a stored passing validation result or
an explicit segment-level human-review entry. Compilation must later refuse
unresolved entries unless a reasoned override is recorded.

## Phase 8: EPUB compilation and validation

Status: **DONE**

- [x] Replace `epub_compiler.py` with typed compilation and validation interfaces.
- [x] Restore metadata, spine order, navigation, styles, images, and resources.
- [x] Preserve inline XHTML elements and attributes through protected translation markers.
- [x] Validate ZIP structure, mimetype placement, manifest resources, local XML/CSS
      references, spine/document/segment sets, and compiled translated text.
- [x] Produce the final EPUB and compilation/validation reports atomically.
- [x] Refuse compilation while unresolved human-review segments remain.
- [x] Add end-to-end tiny-book and real Alice round-trip tests.

Verification at close: 251 offline tests passed. Tiny EPUB tests cover nested inline
marker extraction, stable-path lookup, inline-tree reinsertion, attribute
preservation, unbalanced/changed marker rejection, deterministic byte-identical
builds, mimetype ordering/compression, resource/text equivalence, stage resume,
unresolved-review refusal, and corrupt-output failure reporting. The complete
public-domain EPUB fixture round-tripped all 15 spine documents, 804 text
segments, and 24 resources through compilation and validation without modifying
the source EPUB.

Acceptance: the output opens as a valid EPUB and contains every expected
translated chapter and original non-text resource.

## Phase 9: CLI and operations

Status: **DONE**

- [x] `run`, `resume`, `status`, `approve`, `retry`, and `report` commands.
- [x] Dry-run and configuration inspection.
- [x] Clean exit codes for complete, failed, paused, and cancelled jobs.
- [x] Rich progress display with plain-text fallback.
- [x] Current/total work-unit and retry counters for every model-backed stage.
- [x] Stage-specific adaptive 8K context floor for targeted repair and verification.
- [x] User documentation and example commands.

Verification at close: 385 test cases plus 19 subtests passed
offline. Phase 9 tests cover every command
parser, path resolution, write-free and model-free dry-run, complete/resumed,
failed, paused, and cancelled orchestration, Ollama model/context preflight,
glossary and hash-bound final-draft approval, dependent-stage retry,
machine-readable status/report output, and plain progress fallback. A real
dry-run against the read-only public-domain sample resolved both configured
model roles and every stage without creating a job workspace.

Long streamed generations also emit a content-free heartbeat at each crossed
2,000-character interval and finish with exact Ollama prompt/output tokens,
decode speed, and elapsed time.
Every progress line carries the active stage and an invocation-scoped session
hash. Translation retains raw attempt drafts and performs bounded primary-to-
fallback model failover after repeated validation failures.
Rejected semantic repairs now have direction-aware few-shot feedback examples,
structured marker recovery, independent-model fallback, and regression coverage.
Post-repair work is split into Gemma-only `review_repaired`, Qwen-only
`repair_review`, and Gemma-only `validate_repaired` stages. Model-order and
per-stage resume tests prevent regression to interleaved model switching.
The deterministic language audit also has paired regressions proving that an
intentional source-matched foreign quotation is accepted while copied English
dialogue is still rejected.

Acceptance: the complete workflow is operable without editing Python or prompt
files. The CLI returns `0` for completion, `1` for failure, `2` for a review
pause, and `130` for cancellation.

## Optional later work

Status: **TODO**

- [x] Define a reproducible, explicitly unrun Ollama/TabbyAPI/SGLang framework
      and speculative-decoding benchmark protocol.
- [ ] Execute the frozen one/two-chapter framework benchmark and publish raw results.
- [ ] Local review web UI.
- [ ] Web terminology verification provider.
- [ ] MCP adapter for external agents.
- [ ] LangGraph adapter only if distributed execution is justified.
- [ ] Hardware-aware concurrent scheduling.
