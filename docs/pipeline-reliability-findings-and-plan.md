# Translation Pipeline Reliability: Findings and Implementation Plan

> **Implementation status (2026-09-09):** The shared content-policy,
> monotonic-promotion, context-bucket scheduling, generic quantity parsing,
> final-review, and EPUB compilation work described here has been implemented
> and exercised in production workspaces. This file remains the rationale and
> regression checklist; [Production Operations](OPERATIONS.md) describes the
> current configuration and operator procedure. Book-specific observations in
> this historical record are not runtime rules.

## Purpose

This document is a handoff for improving the generic EPUB translation pipeline after a representative production regression run. It records the evidence, root causes, design constraints, and implementation plan so work can resume in a new session without reconstructing the investigation.

The target workflow is English-to-Chinese literary prose intended for e-reader display and TTS, but every production rule described here must remain language-aware and reusable. The implementation must not contain book titles, segment IDs, character names, quoted phrases from a particular book, or one-off replacement text.

Observed book excerpts and segment IDs below are regression evidence only. They may appear in tests or fixtures, but must never control runtime behavior.

## Non-negotiable constraints

1. Treat failure classes generically. Do not hard-code explicit fixes for individual books or passages.
2. Keep EPUB structure, markers, identifiers, and other mechanically verifiable content under deterministic control.
3. Do not let an uncertain repair replace a better existing translation.
4. Do not repeatedly call an LLM when the same validation result is no longer converging.
5. Preserve output quality by allowing the pipeline to abstain and request manual review.
6. Keep the solution small: shared policies and conservative state transitions are preferred over a growing collection of guardrails.
7. Preserve the existing compile option that allows a bounded number of unresolved review segments.

## Current relevant configuration

The generic configuration in force during this investigation matched the
shipped `config.example.yaml` with the `styles/ereader-tts.txt` custom style.

- Translation model: `qwen3.8:latest`
- Glossary and semantic audit model: `gemma4:26b`
- Global adaptive context floor: 16,384 tokens
- Translation context cap: 65,536 tokens
- Translation prompt cap: 20,000 tokens
- Translation context multiplier: 3.0
- Repair context floor: 16,384 tokens
- Repair context multiplier: 1.0
- Audit semantic batching target: 8,000 source tokens
- Compile unresolved-review limit: 4
- Translation direction: `en-zh`
- Style: generic e-reader/TTS Chinese prose with de-AI enabled

The broader pipeline currently contains:

```text
decompile
  -> extract_glossary
  -> resolve_glossary
  -> approve_glossary
  -> preprocess
  -> translate
  -> audit_translation
  -> repair_translation
  -> review_repaired
  -> repair_review
  -> validate_repaired
  -> compile
  -> validate_epub
```

## Evidence from the failing run

The compile stage reported 17 unresolved review segments. The whole-file summary showed:

- 575 LLM calls
- 1,718,579 total tokens
- 274 calls in `repair_translation`
- 98 calls in `repair_review`
- about 1.2 hours total processing time

The initial audit produced 50 `translation is identical to source` findings. Investigation found that 48 were numeric or similarly language-neutral structural fragments; the other two were intentionally preserved identifiers.

Those false findings alone led to approximately:

- 235 unnecessary LLM calls
- 390,950 prompt tokens
- 7,205 output tokens
- 398,155 total tokens
- about 10.8 minutes of reported LLM time

### Breakdown of the 17 unresolved segments

Fourteen were deterministic false positives:

- twelve numeric headings, years, or structural numbers;
- one glossary-approved preserved uppercase identifier;
- one technical/editorial identifier containing letters, digits, punctuation, and a date.

Three were semantic or contextual cases:

1. A quotation-related finding where the target already contained the claimed punctuation and the source used multi-paragraph dialogue conventions.
2. A context-dependent fragment whose initial translation was reasonable, but an incorrect audit diagnosis anchored later repairs and made the final text worse.
3. An idiomatic/metaphorical fragment whose meaning depended on an earlier passage; repeated local repairs alternated between literal and paraphrased readings without converging.

If the deterministic false positives are removed and punctuation ambiguity is made nonblocking, the run should fall under the configured compile limit even if one or two truly ambiguous semantic segments remain for manual review.

## Root causes

### 1. The audit applies prose rules to non-prose content

`book_agent/audit.py` currently performs source-equals-target and length-ratio checks without first determining whether the segment is translatable prose.

Relevant areas:

- `audit_translated_document()`
- `_audit_language()`
- `_audit_length()`

This makes correct preservation of numbers, dates, codes, acronyms, and structural text look like untranslated prose. It also makes a short heading translated into Chinese appear to have an extreme token-length ratio.

### 2. Preservation logic is not shared by all stages

`book_agent/translation.py` already recognizes several valid preserved forms during translation validation, including separators, URIs, technical identifiers, and glossary-approved literals. The audit and repair paths do not consistently reuse that policy.

As a result, translation accepts content that audit later rejects, and repair then repeatedly tries to alter content that should remain unchanged.

### 3. Unchanged repair output is rejected unconditionally

`book_agent/repair.py::validate_repair_output()` rejects a repair that equals the current translation even when the current translation is the correct representation of language-neutral or protected content.

This guarantees nonconvergence for correctly preserved segments.

### 4. A bad diagnosis becomes self-reinforcing

Repair and verification prompts carry earlier issue descriptions forward. When the initial semantic diagnosis is wrong, later calls tend to solve the diagnosis rather than independently compare source and target.

The pipeline can therefore turn a reasonable translation into a worse one while accumulating apparently consistent explanations.

### 5. Failed candidates become part of the repair trajectory

The repair workflow does not enforce a sufficiently strong last-known-good boundary. A failed or unverified candidate can influence later retries, producing repair drift.

### 6. Verification lacks enough context

Repair prompts receive immediate neighboring material, but verification is primarily segment-local. Elliptical sentences, cross-paragraph dialogue, pronouns, and recurring metaphors cannot always be judged correctly in isolation.

### 7. Repeated calls do not add independent evidence

Multiple retries often use the same model, prompt structure, and diagnosis. Identical or near-identical failures are counted as new attempts even though they do not represent new information.

### 8. Structured marker repair can accidentally become a rewrite task

A later production regression showed a structurally parseable translation with two omitted inline emphasis markers. The existing structured recovery asked the model to reproduce the complete immutable target passage as `before`, `emphasized`, and `after`. The model slightly rewrote that target text while selecting the emphasis span, so the deterministic immutable-text check correctly rejected the repair. Subsequent ordinary translation retries omitted the same markers again.

The generic correction is to ask for less model output. For the common case of one non-empty inline marker pair, the recovery schema now requests only:

- an exact target substring copied verbatim from the immutable translation;
- its one-based occurrence when the same substring appears repeatedly.

Python locates that exact occurrence and inserts the canonical marker without allowing any other target text to change. Multi-marker and empty-marker cases retain the existing validated split-based fallback. Because successful translated chunks remain fully compatible, this recovery-only change preserves their existing checkpoints and is applied when a failed chunk is retried.

## External design references

These repositories were reviewed for general patterns, not for code copying.

### BookForge

Repository: <https://github.com/JunjoSick/bookforge>

Useful pattern:

- EPUB structure and markers remain program-owned.
- Models receive prose payloads rather than raw document structure.
- Structural validation and reconstruction are deterministic.
- Source-copy validation includes scoped exceptions instead of treating every unchanged string as prose failure.

### FoundryL10n

Repository: <https://github.com/AcTePuKc/FoundryL10n>

Useful pattern:

- Fragile placeholders, XML, bracketed actions, and numeric tokens are masked or protected before model processing.
- Integrity checks and terminology consistency are separate from free-form semantic judgment.

### book-translator

Repository: <https://github.com/KazKozDev/book-translator>

Useful patterns:

- Refinement is `estimate -> patch -> verify`, not unrestricted whole-chunk rewriting.
- A model proposes located edits; Python applies only exact, non-overlapping spans.
- Candidate verification compares before and after against the source.
- A/B candidate order is reversed to expose position bias.
- Style-only or subjective findings are not automatically allowed to rewrite text.
- A different verifier model is recommended when available.

### i18n-validate

Repository: <https://github.com/i18n-agent/i18n-validate>

Useful pattern:

- Source-equals-target is a warning heuristic, not universally a hard failure.
- Placeholder and structural mismatches are objective errors handled separately.

### COMET

Repository: <https://github.com/Unbabel/COMET>

Useful pattern:

- Reference-free translation quality estimation is separate from translation generation.
- Document-aware evaluation uses context for discourse phenomena.

COMET is not currently proposed as a required dependency because of its model and GPU cost. Its document-context design supports the direction of the planned verifier changes.

## Target design

### A. One shared segment policy

Create a single reusable content-policy layer used by translation, audit, repair, review, and final validation.

Suggested concepts:

```python
class SegmentKind(Enum):
    PROSE = "prose"
    LANGUAGE_NEUTRAL = "language_neutral"
    PROTECTED_IDENTIFIER = "protected_identifier"
    STRUCTURAL = "structural"
```

The exact API can differ, but the policy must answer at least:

```python
classify_segment(source_text, target_text, direction, glossary, metadata)
is_intentionally_preserved(...)
should_run_language_check(...)
should_run_length_check(...)
should_run_semantic_audit(...)
```

Classification must rely on generic evidence:

- Unicode script composition;
- presence or absence of source-language prose characters;
- numeric and punctuation structure;
- URI syntax;
- existing technical-identifier recognition;
- explicit glossary approval;
- document metadata when available;
- length and structural characteristics.

Do not classify every uppercase word as protected. An alphabetic literal should require either glossary approval or conservative technical-identifier evidence.

Language-neutral source content should still receive applicable integrity checks. For example, numeric meaning must not silently change merely because the target-language check is skipped.

### B. Stage-specific policy

#### Translation validation

- Keep current marker, empty-output, source-copy, and target-language checks.
- Replace private preservation decisions with the shared policy.
- Preserve identifiers only when the shared policy allows it.

#### Deterministic audit

- Skip target-language and unchanged-source checks for intentionally preserved content.
- Skip prose length-ratio checks for language-neutral and structural content.
- Keep empty output, marker integrity, mojibake, repetition, and number-integrity checks where applicable.
- Only send actual prose candidates to semantic audit.

#### Repair validation

- Use the shared preservation policy.
- Do not create `repair_unchanged` when unchanged output is valid by policy.
- Do not call the repair model for findings invalidated by the current deterministic audit.

#### Final validation and compile

- Recompute deterministic findings from the current text rather than carrying stale findings forward as truth.
- Preserve the unresolved-review report.
- Only current, reproducible findings should block compilation.

### C. Monotonic candidate promotion

Maintain an immutable last-known-good translation for each segment.

```text
last accepted text
    -> proposed candidate
    -> deterministic validation
    -> independent semantic comparison, when needed
    -> promote only on success
```

Rules:

1. Every retry starts from the last accepted text, not the most recent failed candidate.
2. A failed candidate is retained only as diagnostic history.
3. If verification is unavailable, contradictory, tied, or position-biased, keep the existing text.
4. Manual review is preferable to promoting an uncertain candidate.

### D. Located edits

For ordinary repair, request a bounded edit rather than a rewritten segment:

```json
{
  "old_span": "exact text present in the current translation",
  "new_span": "replacement text",
  "reason": "short source-grounded explanation"
}
```

Apply edits deterministically only when:

- `old_span` occurs exactly once;
- the span does not overlap another accepted edit;
- the resulting segment passes marker, identifier, number, language, and glossary validation;
- all text outside the applied span remains byte-for-byte unchanged.

If the model cannot locate the span, discard the edit. Do not guess its intended location.

Whole-segment independent retranslation remains a bounded fallback for genuine omission or globally broken translation, but it must still use monotonic candidate promotion.

The same principle applies to inline-marker recovery: select an exact existing target span and let Python insert the marker. Do not ask the model to reproduce immutable surrounding prose when a smaller exact-span response is sufficient.

### E. Independent semantic verification

Verification should judge the source and candidate, not the previous auditor's prose diagnosis.

Inputs:

- preceding source segment;
- current source segment;
- following source segment;
- current translation;
- candidate translation;
- relevant glossary entries;
- a small number of related source passages when retrieval finds strong evidence.

Do not include accumulated repair-history messages in the independent comparison prompt.

For a changed semantic segment:

1. Ask one independent model to compare the last accepted translation and the
   candidate against the source and bounded context.
2. Accept the candidate only when the judge explicitly confirms that it fixes
   the defect without introducing a new one.
3. If the candidate fails but the current translation is acceptable, retain it.
4. Otherwise retain the current translation and request review.

Because this is only used after deterministic filtering, the verification call
applies only to newly repaired segments that previously failed semantic review.

With the current two available model families, a conservative initial role split is:

- candidate repair: `qwen3.8:latest`;
- independent review and final comparison: `gemma4:26b`.

This can be configurable. Do not assume the verifier is correct merely because it is a different model.

### F. Generic context retrieval

Immediate neighboring segments should always be available to repair and verification.

For context-dependent fragments, optionally retrieve up to two additional passages from the same document using generic lexical evidence:

- tokenize source words;
- down-weight common stopwords;
- prioritize rare repeated content words and named terms;
- rank passages by overlap or a lightweight similarity score;
- exclude the current and already included neighboring segments;
- enforce a small token budget.

This should be deterministic and local. Do not add an embedding model unless simple lexical retrieval proves inadequate.

### G. Punctuation and structural ambiguity

Punctuation should not become a blocking semantic issue solely from an LLM claim.

- Validate marker structure deterministically.
- Evaluate quote and bracket structure across neighboring segments or the complete document where appropriate.
- Treat ambiguous punctuation-only findings as warnings unless a deterministic check confirms corruption.
- Do not automatically insert or remove punctuation based only on a semantic verifier's explanation.

This is particularly important because segment boundaries do not always align with dialogue or paragraph-level punctuation conventions.

### H. Convergence limits

Replace four near-identical attempts with evidence-changing attempts:

1. one normal repair candidate;
2. one independent/context-enhanced fallback candidate;
3. then stop and retain the last accepted text.

Stop earlier when normalized model output or validation findings repeat. Record a convergence reason such as:

- `repeated_candidate`;
- `repeated_validation`;
- `verifier_disagreement`;
- `insufficient_context`;
- `protected_content`.

## Implementation phases

### Phase 1: Low-risk correctness and cost reduction

1. Add the shared segment-content policy.
2. Refactor translation validation to use it without changing existing accepted behavior.
3. Use it in deterministic audit before language and length checks.
4. Use it in repair validation so valid unchanged content is accepted.
5. Re-audit the current text before selecting repair targets; discard stale findings.
6. Ensure failed repair candidates never replace the last accepted text.
7. Add convergence detection and reduce repeated equivalent attempts.
8. Make unconfirmed punctuation-only semantic findings nonblocking.

Expected outcome: remove most false positives and unnecessary calls with minimal changes to semantic generation.

### Phase 2: Safer semantic repair

1. Add located-edit repair output and deterministic application.
2. Add immediate context to repair verification.
3. Add generic related-passage retrieval.
4. Add configurable verifier model.
5. Add a single independent candidate comparison after deterministic filtering.
6. Keep whole-segment retranslation only as a bounded fallback.

### Phase 3: Pipeline simplification

After real-book verification, review whether the current sequence can be reduced to:

```text
translate
  -> deterministic audit
  -> semantic audit of filtered candidates
  -> monotonic located repair
  -> final deterministic validation
  -> compile/manual review
```

Do not remove stages until artifact compatibility, resume behavior, and quality reports have been verified.

#### Implemented convergence update (August 29, 2026)

The persisted stages remain for artifact and resume compatibility, but their
LLM work is now forward-only:

```text
deterministic + semantic audit
  -> targeted repair with deterministic candidate validation
  -> deterministic preflight + independent review
  -> one homogeneous repair pass over rejected segments
  -> deterministic validation of every document
  -> one independent semantic judgment only for newly repaired problem segments
  -> compile or manual review
```

Final validation no longer re-verifies repairs that already passed review, no
longer performs position-swapped calls, and no longer launches semantic repair
and fallback cycles. A failed final candidate restores the last accepted text
and enters the review queue. Structured judge output is explicitly bounded to
prevent verbose generations from consuming the context window.

#### Book 10 convergence correction (August 29, 2026)

The first forward-only run still reported fourteen unresolved segments. Artifact
inspection showed that this was not fourteen surviving translation defects:

- the numeric hard gate compared surface forms instead of values across languages
  (for example, lexical magnitudes, Chinese magnitude units, fractions, dates,
  numbered identifiers, and numbers separated by inline markers);
- the comparison prompt used moving A/B labels plus two booleans. One recorded
  response stated that the candidate was correct while returning `passed=false`;
- `REVIEW` repair history remained blocking even when the retained current text
  passed the current deterministic policy.

The implemented correction keeps deterministic checks cheap but limits blocking
to credible facts. Number normalization now handles marker boundaries, dates,
percentages, fractions, English/Chinese magnitude forms, and numbered identifiers.
Low-confidence lexical counts block only when both sides expose one mechanically
alignable fact; complex prose remains a semantic-audit concern. Repair validation
still forbids a candidate from introducing a new digit-bearing fact.

Independent review now names `CURRENT TRANSLATION` and `REPAIRED CANDIDATE`
directly. It validates absolute source fidelity rather than requiring the candidate
to win a stylistic A/B ranking. If current text is independently acceptable, it is
retained without another repair call. Deterministic-only `REVIEW` history is also
promoted to accepted when the current draft passes the recomputed gate.

#### Segment outcomes and context-homogeneous scheduling (August 29, 2026)

The audit, repair, repaired-review, review-repair, and final-validation stages now
separate planning from execution:

1. run deterministic checks and resume-cache checks for every relevant segment;
2. publish a content-free stage plan with pre-screened, skipped, and true LLM-task
   counts plus the exact adaptive context-bucket distribution;
3. execute remaining independent work in stable 8K context-window buckets so a
   model does not repeatedly alternate between 16K, 24K, and larger runners;
4. map results back into original spine and segment order before writing artifacts;
5. publish a terminal per-segment result such as `passed`, `failed`, `repaired`,
   `pending`, or `skipped`, together with the deterministic/semantic mode.

Mechanical, preference-only, cached, and exact-rule repairs are completed during
pre-screen and never enter an LLM bucket. Context selection uses the same prompt,
structured schema, multiplier, and 8K stepping function as the Ollama request
boundary; repair calls are pinned to their scheduled bucket so retries and marker
placement cannot silently trigger a smaller runner between neighboring tasks.
Document assembly remains deterministic and in spine order, so execution ordering
does not alter EPUB output ordering or resume identity.

## Candidate code locations

Likely files to inspect or change:

- `book_agent/translation.py`
  - existing preservation and translation-output validation helpers
- `book_agent/audit.py`
  - `audit_translated_document()`
  - `_audit_language()`
  - `_audit_length()`
- `book_agent/repair.py`
  - `RepairVerification`
  - `validate_repair_output()`
  - repair and verification prompt builders
- `book_agent/stages/audit.py`
  - deterministic/semantic candidate selection
- `book_agent/stages/repair.py`
  - candidate generation and promotion
- `book_agent/stages/review_repaired.py`
  - verifier calls
- `book_agent/stages/validate_repaired.py`
  - sparse final verification, deterministic preflight, and stale-finding reconciliation
- `book_agent/config.py` or the equivalent schema module
  - verifier model and convergence settings
- `tests/test_translation.py`
- `tests/test_audit.py`
- `tests/test_repair.py`
- stage-level integration tests

Prefer placing the shared content policy in a small neutral module if importing it from `translation.py` would create awkward coupling. Avoid duplicated regexes or slightly different definitions in each stage.

## Required regression tests

Examples below describe behavior, not runtime exceptions tied to particular books.

### Segment classification

- numeric-only source preserved unchanged is not untranslated prose;
- numeric heading rendered in Chinese is not rejected by prose length ratio;
- a date or technical code can remain unchanged;
- a glossary-approved identifier can remain unchanged;
- an arbitrary alphabetic sentence cannot remain unchanged;
- an unapproved ordinary uppercase word is not automatically protected;
- a changed number is still detected;
- inline-marker boundaries cannot concatenate prose into a false identifier;
- dates, percentages, fractions, magnitudes, and numbered identifiers compare by
  normalized value rather than surface spelling;
- mixed prose plus numbers remains prose and receives normal QA.

### Audit

- intentionally preserved segments do not become semantic candidates;
- normal unchanged English prose still receives a high-severity untranslated finding;
- short translated headings do not fail solely because token ratio is large;
- empty, malformed-marker, and mojibake findings remain active.

### Repair

- valid unchanged protected content is accepted;
- unchanged erroneous prose is still rejected;
- failed candidates do not mutate the accepted document;
- retry two starts from the last accepted text;
- repeated equivalent candidates stop early;
- a located edit changes only its exact span;
- missing or ambiguous edit spans are discarded;
- overlapping edits cannot corrupt text.

### Inline-marker recovery

- one missing non-empty marker can be restored by selecting an exact target substring;
- a repeated substring uses an explicit one-based occurrence;
- a nonexistent substring is rejected;
- marker selection cannot rewrite any target prose;
- unknown, duplicated, or reordered reference IDs are rejected;
- multi-marker and empty-marker passages continue through the validated fallback path.

### Verification

- repairs that already passed independent review skip final LLM validation;
- only newly repaired semantic failures receive final comparison;
- a rejected candidate restores the last accepted translation and stops;
- final validation does not launch another repair or fallback call;
- prior diagnosis text is absent from the independent comparison prompt;
- prompts name current and candidate roles directly and use no moving A/B labels;
- a verified acceptable current translation skips the repair pass;
- preceding and following source context are present;
- related-context retrieval is generic, bounded, and deterministic.

### Final validation and compile

- stale historical findings do not block a segment that currently passes validation;
- ambiguous punctuation-only LLM findings do not block without deterministic confirmation;
- genuine unresolved semantic segments remain in the manual-review report;
- the configured compile unresolved limit is still enforced.

## Validation strategy

Run tests in increasing scope:

1. Focused unit tests for classification, audit, repair, and verification.
2. Stage-level tests with fake/model-stub responses.
3. Full test suite.
4. Retry the existing regression workspace from `audit_translation` so translation and glossary work are reused.
5. Compare before/after stage summaries and unresolved reports.
6. Run at least one unrelated EPUB to ensure the policy generalizes.

The existing job can be retried after implementation with:

```powershell
python -m book_agent retry ".\runs\<job-id>" `
    --stage audit_translation `
    --resume `
    --plain
```

Do not use `--failed-only` for the first validation of the shared audit policy. The audit artifacts need to be regenerated so old false findings cannot survive downstream.

## Acceptance criteria

The work is complete when:

1. No production rule references a specific book, segment, name, phrase, or replacement.
2. Shared classification is used consistently by all relevant stages.
3. Language-neutral and approved preserved content no longer triggers prose repair loops.
4. Genuine unchanged source prose is still rejected.
5. Failed semantic candidates cannot degrade the accepted translation.
6. Repeated nonconvergent attempts stop early.
7. Context-dependent verification receives bounded contextual evidence.
8. Semantic changes require conservative promotion; uncertainty retains the original.
9. The full test suite passes.
10. The retried regression workspace produces no more than the configured four unresolved blocking segments without manual or book-specific patches.
11. Total LLM calls and repair tokens decrease substantially relative to the recorded 575-call run.

## Out of scope for the first implementation

- Adding COMET or another large quality-estimation dependency.
- Training or fine-tuning a model.
- Book-specific glossaries embedded in production code.
- Automatic resolution of every literary ambiguity.
- Replacing human review for genuinely uncertain passages.
- Broad pipeline removal before resume/artifact compatibility is proven.

## Final implementation principle

The pipeline should distinguish facts from judgments:

- structure, markers, numbers, approved identifiers, and exact edit application are deterministic facts;
- literary meaning and fluency are model judgments;
- model judgments may propose candidates, but uncertain judgments must not overwrite verified text.

This separation is the main reliability improvement and the main defense against introducing new issues.
