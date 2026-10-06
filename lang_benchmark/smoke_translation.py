"""Smoke test: does a model follow the pipeline's real translation prompt? (one chunk, no job)"""
import glob, json, sys, time, urllib.request
from book_agent.preprocessing import PreprocessedDocument, select_relevant_glossary_entries
from book_agent.translation import build_translation_chunks, build_translation_prompt, validate_translation_output
from book_agent.workflow import load_workspace_config
from benchlib import ws

job, doc_index, first, count = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
models = sys.argv[5:]
w = ws(job)
config = load_workspace_config(w)
path = sorted(glob.glob(f"runs/{job}/preprocessed/*/0*.json"))[doc_index]
document = PreprocessedDocument.model_validate_json(open(path, encoding="utf-8").read())
document = document.model_copy(update={"segments": document.segments[first:first + count]})
chunk = build_translation_chunks(document, 6000)[0]
source = " ".join(p.source_text for p in chunk.pieces)
glossary = select_relevant_glossary_entries(source, document.relevant_glossary, config.translation.direction)
prompt = build_translation_prompt(chunk, glossary, config)
out = open(f"lang_benchmark/results/smoke-{job}.txt", "w", encoding="utf-8")
print(f"prompt {len(prompt)} chars, {len(chunk.pieces)} segments, {len(glossary)} glossary entries", file=out)
for model in models:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": 0.0, "num_ctx": 16384}, "keep_alive": "1m"}).encode()
    start = time.time()
    request = urllib.request.Request("http://localhost:11434/api/generate", body, {"Content-Type": "application/json"})
    try:
        answer = json.load(urllib.request.urlopen(request, timeout=1500))
    except Exception as error:  # noqa: BLE001
        print(f"\n===== {model}: request failed: {error}", file=out)
        continue
    text = answer.get("response", "")
    translations, validation = validate_translation_output(text, chunk, config.translation.direction, relevant_glossary=glossary)
    print(f"\n===== {model}: {time.time() - start:.0f}s, {answer.get('eval_count')} tokens; contract passed={validation.passed}", file=out)
    for issue in validation.issues:
        print(f"  ISSUE {issue.code} {issue.reference_id}: {issue.message}", file=out)
    for piece in chunk.pieces:
        print(f"  SRC {piece.reference_id}: {piece.source_text}", file=out)
        print(f"  TGT {translations.get(piece.segment_id, '<missing>')}", file=out)
    if not translations:
        print("  RAW:", text[:1500], file=out)
    print(f"{model}: passed={validation.passed}, {time.time() - start:.0f}s")
out.close()
