"""Smoke test: can a model do the glossary resolution call (structured JSON) for a non-zh pair?"""
import json, sys, time, urllib.request
from book_agent.glossary import build_glossary_resolution_cases
from book_agent.glossary_prompts import build_resolution_prompt
from book_agent.languages import LanguagePair
from book_agent.schemas import GlossaryEntry, build_glossary_resolution_schema

pair = LanguagePair("en>de")
raw = [("White Rabbit", "Weißes Kaninchen", "人名"), ("White Rabbit", "Weißer Hase", "人名"), ("Dinah", "Dinah", "人名"),
       ("well", "Brunnen", "地名"), ("Caucus-race", "Caucus-Rennen", "概念"), ("Mock Turtle", "Falsche Schildkröte", "人名"),
       ("Mock Turtle", "Suppenschildkröte", "人名"), ("Duchess", "Herzogin", "人名")]
entries = [GlossaryEntry.for_pair({"source": s, "target": t, "category": c, "evidence": ["D0000-S000001"]}, pair) for s, t, c in raw]
prompt = build_resolution_prompt(entries, pair)
cases = build_glossary_resolution_cases(entries, pair)
schema = build_glossary_resolution_schema([str(c["term_id"]) for c in cases], max_decisions=len(entries), pair=pair)
out = open("lang_benchmark/results/smoke-structured.txt", "w", encoding="utf-8")
for model in sys.argv[1:]:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False, "format": schema.model_json_schema(),
                       "options": {"temperature": 0.0, "num_ctx": 16384}, "keep_alive": "1m"}).encode()
    start = time.time()
    answer = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/generate", body, {"Content-Type": "application/json"}), timeout=900))
    text = answer.get("response", "")
    try:
        schema.model_validate_json(text); ok = True
    except Exception as error:  # noqa: BLE001
        ok = str(error)[:300]
    print(f"===== {model}: {time.time() - start:.0f}s; schema valid: {ok}\n{text[:2500]}\n", file=out)
    print(model, ok)
out.close()
