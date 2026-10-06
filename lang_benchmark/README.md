# Language benchmarks

The material behind sections 6 and 7 of `docs/GENERIC_LANGUAGES.md`: one excerpt per language pair,
translated unattended, then read and counted. It is for checking a change to the pipeline or trying a
model, not a test suite: every run calls the local models and takes from a few minutes to over an hour.

## Running

From the repository root, in your own terminal:

```
powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 en-zh
powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round r2 zh-ja en-ko
powershell -ExecutionPolicy Bypass -File .\lang_benchmark\run-benchmarks.ps1 -Round r2 -Translators "qwen,tg" zh-es
```

- A pair is `source-target`. Without pairs, every pair runs.
- Each run is the job `runs\bench-<round>-<translator>-<pair>`. A job that exists is resumed, so use a
  new `-Round` for a fresh run.
- Without `-Translators`, each pair uses the translator section 7 recommends for it.
- `-Book <file>` runs the given pairs on another book in `books\`.
- Logs go to `runs\bench-logs\`; results to `results\<round>-results.md` and `<round>-summary.txt`,
  with one `results\review-<job>.txt` of findings per run.

The models named in the configs must be installed in Ollama: `qwen3.8`, `translategemma:27b`,
`gemma4:31b`, `gemma4:26b`. The books must be built first (below).

## What is here

| Path | What |
|---|---|
| `configs/qwen-<pair>.yaml` | qwen3.8 translates. All 17 pairs. |
| `configs/tg-<pair>.yaml` | translategemma:27b translates in 8,000-token chunks; qwen3.8 keeps the glossary and is the fallback; gemma4:31b repairs. Written from the qwen configs by `make_tg_configs.py`. Pairs without Chinese as the target. |
| `BOOKS.md`, `make_books.py` | Where each book comes from, and the script that cuts the excerpts into `books/`. |
| `results/` | The results files the design document cites, from the runs of October 2026. |
| `run-benchmarks.ps1` | Runs pairs, then the three scripts below. |
| `evaluate.py` | One section per run: status, cost, glossary, findings, conventions, sample passages. |
| `summarize.py` | One line per run, with what the placement and untranslated-text rules find in the first draft and the final text. |
| `inspect_runs.py` | Every rule finding and the review queue of a run, with the passages. |
| `compare.py` | The same passages from several runs, side by side. |
| `smoke_translation.py`, `smoke_structured.py` | One real prompt sent straight to a model: whether it keeps the marker contract, and whether it can resolve a glossary. Try these before a full run with a new model. |
| `fetch_wikisource.py` | A public-domain text from Chinese Wikisource as Simplified Chinese. |

The Python scripts read finished jobs and call no model. Outside `run-benchmarks.ps1`, run them from the
repository root with `PYTHONPATH` set to `.;lang_benchmark` and `PYTHONIOENCODING=utf-8`.

## Books

The excerpts are not in the repository. `BOOKS.md` lists each one with a link to its source, and
`make_books.py` builds them into `books/` (ignored by git) from the downloads in `sample/`:

```
python lang_benchmark/make_books.py
```

All are public-domain texts: Carroll, Doyle, Grahame, Kafka, Verne, Pérez Galdós, Akutagawa, Hyun
Jin-geon, Lu Xun.

## Reading a result

`summarize.py` prints, per run: minutes, the review queue, how many segments repair changed, the audit
model's findings, the rules' findings, and two pairs of counts that should end at zero:

- **out of place first/final**: segments carrying a repeated, near-identical, or shifted translation.
- **untranslated first/final**: segments still in the source language, without the target's script,
  or with a run copied from the source.

A count in the first draft that is zero in the final text means repair handled it. A count left in the
final text should also be in the review queue; `inspect_runs.py` shows which segments are.

No number here says a translation is good. Section 7.1 of the design document says what the ratings
rest on: reading the passages.
