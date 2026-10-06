"""Details behind a run's findings (read-only): python lang_benchmark/inspect_runs.py <job> ...

Writes lang_benchmark/results/review-<job>.txt for each job."""

import json
import re
import sys
from pathlib import Path

from book_agent.stages.audit import load_document_audits
from book_agent.stages.audit_consistency import load_consistency_audit_report
from book_agent.stages.translate import load_translated_documents
from book_agent.stages.validate_repaired import load_repaired_document_validations, load_repaired_validation_report, load_validated_repaired_documents
from book_agent.stages.glossary import load_approved_glossary
from benchlib import cost, ws

MARKER = re.compile(r"</?I\d{3}>")
JOBS = sys.argv[1:] or ["bench-lang-ja-zh", "bench-lang-en-fr", "bench-lang-en-ja", "bench-lang-fr-en"]
out = None


def p(*args):
    print(*args, file=out)


for job in JOBS:
    out = open(f"lang_benchmark/results/review-{job}.txt", "w", encoding="utf-8")
    w = ws(job)
    p(f"\n======== {job}")
    final = {s.segment_id: (MARKER.sub("", s.source_text), MARKER.sub("", s.translated_text))
             for d in load_validated_repaired_documents(w) for s in d.document.segments}
    longest = max(final.items(), key=lambda item: len(item[1][0]))
    p(f"segments {len(final)}; longest {longest[0]}: source {len(longest[1][0])} chars, translation {len(longest[1][1])} chars")
    p("  longest translation head:", longest[1][1][:200])
    p("  longest translation tail:", longest[1][1][-200:])

    p("-- rule findings (all)")
    for audit in load_document_audits(w):
        for issue in audit.issues:
            if issue.source != "semantic":
                source, target = final.get(issue.segment_id, ("?", "?"))
                p(f"  [{issue.category.value}/{issue.severity.value}] {issue.segment_id}: {issue.message[:140]}")
                p(f"      SRC: {source[:150]}")
                p(f"      TGT: {target[:150]}")
    p("-- semantic findings (first 8)")
    count = 0
    for audit in load_document_audits(w):
        for issue in audit.issues:
            if issue.source == "semantic" and count < 40:
                count += 1
                p(f"  [{issue.category.value}/{issue.severity.value}] {issue.segment_id}: {issue.message[:200]}")
    p("-- consistency report")
    report = load_consistency_audit_report(w)
    p("  " + json.dumps(json.loads(report.model_dump_json()), ensure_ascii=False)[:6000])
    for path in sorted(Path("runs", job, "consistency").glob("*/0*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in (data.get("issues", []) if isinstance(data, dict) else data):
            p("  FILE", path.name, json.dumps(item, ensure_ascii=False)[:500])
    p("-- review queue and validations")
    validation = load_repaired_validation_report(w)
    p("  queue:", validation.review_segment_ids)
    for doc in load_repaired_document_validations(w):
        for item in getattr(doc, "segments", []) or []:
            if item.segment_id in validation.review_segment_ids:
                p("  ", json.dumps(json.loads(item.model_dump_json()), ensure_ascii=False)[:800])
    for sid in validation.review_segment_ids:
        source, target = final.get(sid, ("?", "?"))
        p(f"  {sid} SRC: {source[:300]}")
        p(f"  {sid} TGT: {target[:300]}")
    p("-- cost by stage (calls / min)")
    for stage, v in sorted(cost(job).items(), key=lambda item: -item[1][1]):
        if v[0] or v[1] > 1:
            p(f"  {stage}: {v[0]} calls, {v[1]/60:.1f} min, prompt {v[2]}, output {v[3]}")
    out.close()
