"""Evaluate the language-profile benchmarks (docs/GENERIC_LANGUAGES.md, phase 5).

Read-only, no model calls. Prints Markdown:

    python lang_benchmark/evaluate.py bench-lang-en-fr bench-lang-en-ja bench-lang-fr-en bench-lang-ja-zh
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter

from book_agent.stages.audit import load_document_audits
from book_agent.stages.glossary import load_approved_glossary
from book_agent.stages.validate_repaired import load_repaired_validation_report, load_validated_repaired_documents
from book_agent.style_sheet import load_style_sheet
from book_agent.workflow import load_workspace_config, workflow_status
from benchlib import cost, ws

MARKER = re.compile(r"</?I\d{3}>")
NBSP = "  "

# What each target language's conventions look like in the translation.
STYLES = {
    "fr": {
        "« » quotes": re.compile(r"[«»]"),
        '" straight quotes': re.compile(r'"'),
        "“ ” quotes": re.compile(r"[“”]"),
        "no-break space before ; : ! ?": re.compile(rf"[{NBSP}][;:!?]"),
        "no such space before ; : ! ?": re.compile(rf"(?<![{NBSP}\d;:!?])[;:!?](?![\d/])"),
    },
    "ja": {
        "「」 quotes": re.compile(r"[「」]"),
        '" or “ ” quotes': re.compile(r'["“”]'),
        "…… ellipsis": re.compile(r"……"),
        "… or ... ellipsis": re.compile(r"(?<!…)…(?!…)|\.\.\."),
    },
    "zh": {
        "“ ” quotes": re.compile(r"[“”]"),
        "「」 quotes": re.compile(r"[「」]"),
        '" straight quotes': re.compile(r'"'),
        "—— paired dash": re.compile(r"——"),
        "— single dash": re.compile(r"(?<!—)—(?!—)"),
    },
    "en": {
        "“ ” quotes": re.compile(r"[“”]"),
        '" straight quotes': re.compile(r'"'),
    },
    "es": {
        "« » quotes": re.compile(r"[«»]"),
        "“ ” quotes": re.compile(r"[“”]"),
        '" straight quotes': re.compile(r'"'),
        "¿ opening": re.compile(r"¿"),
        "? without ¿ in the segment": re.compile(r"^(?!.*¿).*\?", re.S),
        "— raya dialogue": re.compile(r"(?:^|\s)—"),
    },
    "de": {
        "„ “ quotes": re.compile(r"„"),
        "» « quotes": re.compile(r"[»«]"),
        "” English closing quote": re.compile(r"”"),
        '" straight quotes': re.compile(r'"'),
        "– Gedankenstrich": re.compile(r" – "),
    },
    "ko": {
        "“ ” quotes": re.compile(r"[“”]"),
        '" straight quotes': re.compile(r'"'),
        "「」 quotes": re.compile(r"[「」]"),
        "…… ellipsis": re.compile(r"……"),
    },
}


def evaluate(job):
    w = ws(job)
    config = load_workspace_config(w)
    pair = config.translation.direction
    status = workflow_status(w)
    languages = status["languages"]
    print(f"## {job}: {languages['source']['name']} → {languages['target']['name']} ({pair.value})\n")
    print(f"- **Status**: {status['overall']}; tiers {languages['source']['tier']} → {languages['target']['tier']}")
    print(f"- **Skipped checks**: {', '.join(item['check'] for item in languages['skipped']) or 'none'}")

    totals = cost(job)
    calls = sum(v[0] for v in totals.values())
    minutes = sum(v[1] for v in totals.values()) / 60
    slowest = sorted(totals.items(), key=lambda item: -item[1][1])[:4]
    print(f"- **Cost**: {calls} LLM calls, {minutes:.1f} min; slowest: "
          + ", ".join(f"{stage} {v[1]/60:.1f} min" for stage, v in slowest))

    try:
        glossary = load_approved_glossary(w)
        print(f"- **Glossary**: {len(glossary.entries)} approved entries, pair {glossary.pair.value}; e.g. "
              + "; ".join(f"{e.source} → {e.target}" for e in glossary.entries[:8]))
    except Exception as error:  # noqa: BLE001
        print(f"- **Glossary**: not available ({error})")

    sheet = load_style_sheet(w)
    if sheet is not None and sheet.characters:
        print("- **Style sheet characters**: " + "; ".join(
            f"{c.name} ({c.pronoun or '—'}, {c.addressed_as or '—'})" for c in sheet.characters[:8]
        ))

    audits = load_document_audits(w)
    deterministic = Counter(i.category.value for a in audits for i in a.issues if i.source != "semantic")
    semantic = Counter(i.category.value for a in audits for i in a.issues if i.source == "semantic")
    print(f"- **Audit findings** (rules): {dict(deterministic.most_common())}")
    print(f"- **Audit findings** (semantic model): {dict(semantic.most_common())}")
    untranslated = [i.message for a in audits for i in a.issues if i.category.value == "untranslated"][:5]
    if untranslated:
        print("  - untranslated, e.g.: " + " | ".join(m[:90] for m in untranslated))
    numeric = [i.message for a in audits for i in a.issues if "numeric" in i.message][:3]
    if numeric:
        print("  - numbers, e.g.: " + " | ".join(m[:90] for m in numeric))

    findings = []
    for path in sorted(Path("runs", job, "consistency").glob("*/0*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        findings += data.get("issues", []) if isinstance(data, dict) else data
    if findings:
        kinds = Counter(f"[{item.get('severity')}] {item.get('message', '')[:60]}" for item in findings)
        print(f"- **Book consistency**: {len(findings)} findings; " + "; ".join(f"{k} ×{n}" for k, n in kinds.most_common(5)))
    else:
        print("- **Book consistency**: no findings")

    report = load_repaired_validation_report(w)
    print(f"- **Review queue**: {len(report.review_segment_ids)} segments")

    target = pair.target_language.value
    segments = [
        (MARKER.sub("", s.source_text), MARKER.sub("", s.translated_text))
        for d in load_validated_repaired_documents(w)
        for s in d.document.segments
    ]
    styles = STYLES.get(target.split("-")[0])
    if styles:
        counts = {name: sum(1 for _, text in segments if pattern.search(text)) for name, pattern in styles.items()}
        print(f"- **Target punctuation** (segments using each form, of {len(segments)}): {counts}")
    print("\n| source | translation |\n|---|---|")
    for source, text in [item for item in segments if len(item[0]) > 60][:4]:
        print(f"| {source[:160]} | {text[:160]} |")
    print()


if __name__ == "__main__":
    print("# Language-profile benchmarks\n")
    for job in sys.argv[1:]:
        try:
            evaluate(job)
        except Exception as error:  # noqa: BLE001 - one unfinished job must not hide the others
            print(f"## {job}\n\nNot evaluated: {error}\n")
