# Production Operations Guide

Last updated: 2026-09-09

This is the current operator-facing guide for the resumable local-Ollama
translation workflow. It supersedes historical runtime recommendations in
`PLAN.md` and `pipeline-reliability-findings-and-plan.md`; those documents keep
their implementation history and rationale.

## What the pipeline does

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> preprocess -> translate
  -> audit_translation -> repair_translation -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> compile -> validate_epub
```

Every stage is checkpointed under `runs/<job-id>/`. Re-running `resume` skips
valid completed work. Do not edit a workspace artifact in place: use a review
resolution or retry the earliest affected stage.

## Recommended production roles

The active configuration is the source of truth. A conservative local split is:

| Work | Default role |
|---|---|
| Candidate glossary extraction | `qwen3.8:27b` |
| Glossary resolution and approval review | `qwen3.8:latest` |
| Initial translation and targeted repair | `qwen3.8:latest` |
| Semantic audit | `gemma4:31b` |
| Quantity shadow/adjudication | `gemma4:26b`, escalating to `gemma4:31b` only when configured |
| Repair comparison / bounded verification | `gemma4:26b` |
| Prose rewrite proposals | `qwen3.8:latest` |
| Reprose verification | `gemma4:31b` |

Keep `thinking: false` for constrained JSON audit and verification work unless
there is a demonstrated quality benefit. Semantic-audit calls are deliberately
small and scheduled by context bucket to reduce model swapping; do not replace
the semantic gate wholesale with a faster model without a controlled benchmark.

## Start, inspect, and resume

Validate the effective settings before a costly run:

```powershell
python -m book_agent.cli config --file .\configs\my-book.yaml
python -m book_agent.cli run "D:\books\source.epub" --config .\configs\my-book.yaml --dry-run
```

Start a stable workspace:

```powershell
python -m book_agent.cli run "D:\books\source.epub" `
  --config .\configs\my-book.yaml `
  --job-id "my-book-en-zh"
```

Check a job or continue it:

```powershell
python -m book_agent.cli status .\runs\my-book-en-zh
python -m book_agent.cli resume .\runs\my-book-en-zh --plain
```

`--plain` is useful for a terminal log. It does not change pipeline behavior.
Streaming progress is intentionally content-free and emits a heartbeat at the
configured time/token threshold; stage summaries also break LLM use down by
role and context allocation.

## Glossary-first series workflow

For a series, first run every volume through glossary resolution. Configure a
shared placeholder path in every volume and pause at the glossary gate. Then:

```powershell
python -m book_agent.cli build-series-glossary `
  --runs .\runs `
  --job-pattern "my-series-*-en-zh" `
  --source-stage resolved `
  --output .\glossaries\my-series.review.json `
  --book-output-dir .\glossaries\my-series-book-reviews
```

Review the generated series file and per-book overlays. Approve each overlay,
then rebuild the authoritative shared glossary from approved book glossaries:

```powershell
python -m book_agent.cli build-series-glossary `
  --runs .\runs `
  --job-pattern "my-series-*-en-zh" `
  --output .\glossaries\my-series.json
```

The normal aggregation is deterministic and promotes only supported consensus.
Use `--llm-conflicts` only for unresolved alternatives after that pass. Do not
rebuild a shared glossary while volumes are translating; if it changes after
preprocessing, retry from `preprocess` for every affected workspace.

For already reviewed inputs, skip extraction rather than recreating them:

```yaml
glossary:
  extraction_enabled: false
  series_glossaries:
    - ../glossaries/my-series.json
  book_glossaries:
    - ../glossaries/my-book.reviewed.json
workflow:
  require_glossary_review: false
```

## Final human review

The final validation stage writes a readable report and a machine-editable
worksheet under `reports/`:

- `final-human-review.md` — reviewer handoff;
- `final-human-review.decisions.json` — current queued decisions;
- `unresolved-review-segments.json` — current blocking defects only.

Resolve the complete queue in one submission. This is important: an atomic
worksheet records explicit acceptance of parser-only findings alongside real
corrections, preventing accepted timeline/marker findings from returning after
an unrelated edit.

```powershell
python -m book_agent.cli resolve-review `
  .\runs\my-book-en-zh `
  .\runs\my-book-en-zh\reports\final-human-review.resolutions.json `
  --approve-final
```

Add `--resume` only when you want compilation to start immediately. Otherwise,
approve first, inspect the final reports, and invoke `resume` yourself.

## Retrying a pipeline stage

Use `retry` only when inputs/configuration or an implementation fix requires
new artifacts. Retrying a stage invalidates its downstream stages.

```powershell
python -m book_agent.cli retry .\runs\my-book-en-zh `
  --stage audit_translation --resume --plain
```

Use `--failed-only` only for a targeted recovery where completed stage
checkpoints remain valid. For a changed shared audit policy, retry the full
audit stage so stale findings cannot survive.

## Context and GPU behavior

Adaptive `num_ctx` is a request ceiling, not a promise that each task needs a
large runner. Requests are prescanned, bucketed, and processed in a stable
context order. This reduces model reloads and GPU-memory churn while preserving
the configured read-only neighbor context.

If a segment exceeds its request context, increase the configured context
budget or split the input at a structural boundary. Do not remove neighbors as
a blanket workaround: they are read-only continuity evidence. If an Ollama
generation becomes pathologically slow after a failed/truncated structured
call, stop the job, restart the local model service to release the runner, and
resume only after verifying the relevant context ceiling is viable.

## EPUB compilation options

The compiler preserves original package resources and validates local manifest,
XML/CSS, spine, and translated-text references. Two optional cleanup features
are available:

```yaml
epub:
  strip_print_page_markers: true
  insert_missing_chapter_headings: true
  # Optional fallback only. Linked EPUB TOC labels are preferred automatically.
  chapter_heading_labels:
    - 第一章
    - 第二章
```

When heading insertion is enabled, the compiler first extracts linked TOC
labels from the source EPUB. It uses `chapter_heading_labels` only as a fallback
and never inserts a duplicate where a visible heading already exists.

## Reports worth keeping

- `reports/stage-summary.*` / CLI final table: call, token, load, prompt, and
  decode accounting by stage and LLM role.
- `reports/glossary.*`: extraction, resolution, approval, and quality evidence.
- `reports/audit-*`, `reports/repair-*`, and `reports/reprose-*`: diagnostic
  history and accepted/rejected candidates.
- `reports/compiled-*` and `reports/validate-epub.*`: output package proof.

Treat a successful compilation plus `validate_epub` completion as the release
boundary. Keep the job workspace with the EPUB: it is the reproducibility
record for that release.
