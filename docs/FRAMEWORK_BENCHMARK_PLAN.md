# Translation Inference Framework Benchmark Plan

Status: **UNRUN / NO PERFORMANCE RESULTS**

Last updated: 2026-09-09

This document defines a future benchmark of inference backends for the
translation stage. It does not claim that TabbyAPI, SGLang, vLLM, or
speculative decoding is faster or better on the project hardware. Every number
must remain `TBD` until produced by the protocol below.

## 1. Question

Can another local inference backend materially reduce long-form translation
latency while preserving the current Qwen draft quality, marker contract,
glossary behavior, and resumability?

The current Ollama integration remains the production baseline until a variant
passes both the structural/quality gates and the performance threshold.

## 2. Current baseline

- Backend: Ollama SDK chat API.
- Target: the locally installed `qwen3.8:latest`, reported by Ollama as a
  27.3B Q4_K_M Qwen architecture model.
- Translation thinking: disabled.
- Sampling temperature: 0.
- Context: adaptive, prompt-derived context buckets; production semantic work
  is normally scheduled in stable 16K buckets and translation can grow only
  when its rendered prompt requires it.
- Concurrency: one local generation at a time.
- Prompt: current literary style, de-AI overlay, contextual glossary, and
  protected passage/inline marker contract.
- Earlier fixed-131K sample throughput is diagnostic history only and is not a
  controlled benchmark result. Use synthetic replay prompts rather than prose
  from repository sample EPUBs for all new measurements.

## 3. Candidate variants

| ID | Backend | Target | Draft/speculation | Result |
|---|---|---|---|---|
| A | Ollama | Current installed Qwen | None | TBD |
| B | Ollama | Same model | Fixed context controls: 16K/32K/64K | TBD |
| C | TabbyAPI / ExLlamaV3 | Same base checkpoint converted to EXL3 | Disabled | TBD |
| D | TabbyAPI / ExLlamaV3 | Same target | 1.5B same-family draft | TBD |
| E | TabbyAPI / ExLlamaV3 | Same target | 3B same-family draft | TBD |
| F | TabbyAPI / ExLlamaV3 | Same target | N-gram drafting | TBD |
| G | SGLang | Same base checkpoint | No speculation | TBD |
| H | SGLang | Same target | Standalone 1.5B draft | TBD |
| I | SGLang | Same target | Standalone 3B draft | TBD |
| J | SGLang | Same target | N-gram speculation | TBD |
| K | SGLang | Same target | Compatible EAGLE-3/MTP checkpoint, if one exists | TBD |
| L | vLLM, optional | Same base checkpoint | Target-only and compatible draft model | TBD |

TabbyAPI currently documents ExLlamaV3 model, MTP, N-gram, and separate-model
drafting modes. SGLang documents EAGLE-2/3, MTP, DFlash, standalone draft-model,
and N-gram modes. A proposed Qwen 1.5B or 3B draft must not be assumed compatible:
the exact tokenizer, vocabulary, architecture, target revision, and framework
support must be verified before a run.

Primary references:

- [TabbyAPI server and draft-model options](https://github.com/theroyallab/tabbyAPI/wiki/02.-Server-options)
- [TabbyAPI OpenAI API and model loading](https://github.com/theroyallab/tabbyAPI/wiki/03.-Usage)
- [SGLang speculative decoding](https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/speculative_decoding.mdx)
- [vLLM speculative decoding](https://github.com/vllm-project/vllm/blob/main/docs/features/speculative_decoding/README.md)

## 4. Fairness rules

### 4.1 Same translation workload

Export and freeze a replay corpus from one or two public-domain sample chapters:

- exact system/user prompts;
- exact glossary entries;
- exact source passage markers;
- direction and style settings;
- maximum output tokens and stop conditions;
- expected segment and marker contracts.

Do not benchmark by running independently chunked versions of the book. Every
backend must receive the same logical requests in the same order.

### 4.2 Model equivalence

GGUF Q4_K_M, EXL3, GPTQ, and unquantized/Hugging Face weights are not bit-identical.
Record:

- source checkpoint and immutable revision;
- conversion tool/version;
- quantization type and calibration dataset;
- tokenizer files and chat template;
- KV-cache precision;
- target and draft VRAM allocation.

Compare exact weights when possible. When conversion formats differ, classify
the run as `same base checkpoint / different quantization` and require the full
quality gate rather than claiming an engine-only comparison.

### 4.3 Runtime controls

- Temperature 0 and thinking disabled.
- Same effective context capacity for the request.
- One request at a time for the primary latency benchmark.
- One cold run after unloading the model.
- At least three warm repetitions per request.
- No concurrent audit, glossary, repair, browser, or unrelated GPU workload.
- Capture driver, CUDA, framework, Python, model-server, and OS/WSL versions.
- Reboot or otherwise restore a documented thermal/power state before final runs.

## 5. Metrics

### 5.1 Performance

Record per request and aggregate by median, p90, and total:

- model load time;
- time to first visible token;
- prompt tokens and prompt tokens/s;
- output tokens and decode tokens/s;
- total wall time;
- peak GPU VRAM, GPU utilization, CPU utilization, and system RAM;
- output characters/s;
- retry and validation-failure count;
- accepted tokens, acceptance rate, and accepted length for speculative variants;
- energy use when reliable hardware telemetry is available.

The primary speed outcome is total wall time for validated first drafts, not raw
server tokens/s. A backend that generates faster but causes more full retries is
not faster for this project.

### 5.2 Structural gates

Every measured draft must satisfy:

- 100% expected outer passage IDs in recoverable order;
- 100% source-owned inline marker coverage after bounded deterministic repair;
- no missing, duplicated, or reordered segments;
- no text outside the protected output contract;
- no empty required translation;
- EPUB reconstruction tests remain green.

Report both first-response contract success and success after focused marker
repair. Never silently discard failed outputs from the denominator.

### 5.3 Translation-quality gates

Performance results are ineligible unless quality remains acceptable:

- target-language presence and untranslated-text checks;
- number/unit/name preservation;
- glossary sense and approved-rendering compliance;
- duplication and omission audit;
- independent Gemma semantic audit using one frozen auditor configuration;
- blind human ratings for adequacy, fluency, literary voice, dialogue, and
  cross-chapter consistency;
- pairwise preference with backend identity hidden.

Speculative decoding is intended to preserve the target distribution, but
framework numerical behavior, quantization, batching, and sampling can still
produce different text. Validate outputs instead of assuming equality. vLLM's
documentation describes its theoretical and algorithmic losslessness while
also noting possible numerical variation.

## 6. Result template

| Variant | Cold load | TTFT p50 | Decode tok/s p50 | Validated wall time | First-pass contract | Audit issues | Human preference | Peak VRAM |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A Ollama adaptive | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| C Tabby target-only | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| D Tabby + 1.5B | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| E Tabby + 3B | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| G SGLang target-only | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| H SGLang + 1.5B | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| I SGLang + 3B | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Attach raw machine-readable results, framework logs, resolved configurations,
and output hashes. Do not edit numbers manually into this summary without an
associated result artifact.

## 7. Decision rule

Adopt another backend only when all of the following hold:

1. Structural gates pass at least as often as the Ollama baseline.
2. Blind quality is non-inferior; no material glossary or style regression.
3. Median validated translation wall time improves by at least 20% on the actual
   book workload, or a smaller gain solves a demonstrated memory/capacity problem.
4. Installation and operational complexity are acceptable on the target Windows
   machine.
5. Resume, cancellation, structured output, metrics, and raw-attempt capture can
   be implemented without weakening current guarantees.

If no candidate clears the threshold, retain Ollama and focus on prompt-contract,
chunking, and retry-efficiency improvements.

## 8. Required project work before execution

- Define a provider-neutral generation interface that preserves `GenerationResult`
  metrics, streaming, cancellation, thinking control, and structured validation.
- Add an OpenAI-compatible adapter for TabbyAPI/SGLang without changing stage logic.
- Add a replay command that reads frozen prompts and writes immutable JSONL results.
- Add GPU/CPU telemetry capture.
- Add contract and human-review scoring tools.
- Keep this benchmark outside the critical path until the current Ollama workflow
  completes end-to-end sample EPUB validation.
