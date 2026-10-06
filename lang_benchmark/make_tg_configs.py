"""Benchmark configs with translategemma translating, qwen3.8 for the glossary, gemma4:31b for repair."""
import re

TRANSLATION = """translation:
  model: translategemma:27b        # translates; qwen3.8 keeps the glossary calls (gemma4:31b took 48 min on one)
  fallback_models: [qwen3.8:latest]
  harmonize_fallback_with_primary: false   # a rewrite task translategemma repeats itself on
"""

for pair in ("en-de", "en-es", "en-ko", "de-en", "es-en", "en-fr", "en-ja", "fr-en", "zh-fr", "zh-de", "zh-es", "zh-ja"):
    text = open(f"lang_benchmark/configs/qwen-{pair}.yaml", encoding="utf-8").read()

    def once(pattern, replacement):
        global text
        text, count = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.M)
        assert count == 1, (pair, pattern)

    once(r"^translation:\n", TRANSLATION)
    once(r"^  repair_model: qwen3\.8:latest$", "  repair_model: gemma4:31b")
    once(r"^  max_prompt_tokens: 20000$", "  max_prompt_tokens: 8000          # shorter chunks: translategemma repeats itself in long ones")
    text = f"# translategemma comparison of qwen-{pair}.yaml: same book and settings, other models.\n" + text
    open(f"lang_benchmark/configs/tg-{pair}.yaml", "w", encoding="utf-8", newline="\n").write(text)
    print("wrote", f"lang_benchmark/configs/tg-{pair}.yaml")
