"""One line per benchmark run, for comparing rounds and translators (read-only, no model calls).

    python lang_benchmark/summarize.py bench-r1-tg-en-de bench-r1-qwen-en-de
    python lang_benchmark/summarize.py            # every job under runs/ named bench-*

Each line: pair, status, minutes, review queue, the audit model's findings, the rules' findings,
and what the placement and untranslated-text rules still find in the first draft and the final text.
Run from the repository root with PYTHONPATH set to ".;lang_benchmark" (run-benchmarks.ps1 does).
"""

import glob
import os
import re
import sys
from collections import Counter

from book_agent.audit import _foreign_script_text
from book_agent.consistency import BookSegment, ConsistencySettings, convention_issues
from book_agent.languages import copied_source_run, lacks_target_script, profile, reads_as_source
from book_agent.stages.audit import load_document_audits
from book_agent.stages.glossary import load_approved_glossary
from book_agent.stages.translate import load_translated_documents
from book_agent.stages.validate_repaired import load_repaired_validation_report, load_validated_repaired_documents
from book_agent.translation import misplaced_passages, shifted_passages
from book_agent.workflow import load_workspace_config, workflow_status
from benchlib import cost, ws

MARKER = re.compile(r"</?I\d{3}>")


def plain(text):
    return MARKER.sub("", text)


def placement(documents):
    """Segments the repeated, near-match and shifted-passage rules name."""
    count = 0
    for document in documents:
        items = [(s.segment_id, s.source_text, s.translated_text) for s in document.segments]
        count += len(misplaced_passages(items)) + len(shifted_passages(items))
    return count


def untranslated(segments, direction):
    return sum(
        bool(
            reads_as_source(plain(s.translated_text), direction)
            or lacks_target_script(plain(s.translated_text), direction)
            or copied_source_run(plain(s.source_text), plain(s.translated_text), direction)
        )
        for s in segments
    )


def summarize(job):
    workspace = ws(job)
    direction = load_workspace_config(workspace).translation.direction
    source, target = profile(direction.source_language), profile(direction.target_language)
    totals = cost(job)
    audits = load_document_audits(workspace)
    semantic = Counter(i.severity.value for a in audits for i in a.issues if i.source == "semantic")
    rules = Counter(i.category.value for a in audits for i in a.issues if i.source != "semantic")
    first_documents = load_translated_documents(workspace)
    final_documents = [d.document for d in load_validated_repaired_documents(workspace)]
    first = [s for d in first_documents for s in d.segments]
    final = [s for d in final_documents for s in d.segments]
    queue = load_repaired_validation_report(workspace).review_segment_ids
    segments = [BookSegment("d", i, s.segment_id, s.source_text, s.translated_text) for i, s in enumerate(final)]
    conventions = len(convention_issues(segments, ConsistencySettings(target_language=direction.target_language)))
    third = sum(bool(_foreign_script_text(s.source, s.target, source, target)) for s in segments)
    changed = sum(1 for a, b in zip(first, final) if a.translated_text != b.translated_text)
    slowest = ", ".join(f"{k} {v[1] / 60:.1f}" for k, v in sorted(totals.items(), key=lambda item: -item[1][1])[:3])
    return (
        f"{job:28} {direction.value:6} {workflow_status(workspace)['overall']:9} "
        f"{sum(v[1] for v in totals.values()) / 60:6.1f} min ({slowest})  "
        f"segments {len(final):3}  review queue {len(queue):2}  repaired {changed:3}  "
        f"audit model {sum(semantic.values()):2} (high {semantic.get('high', 0)})  rules {dict(rules.most_common())}  "
        f"out of place first/final {placement(first_documents)}/{placement(final_documents)}  "
        f"untranslated first/final {untranslated(first, direction)}/{untranslated(final, direction)}  "
        f"conventions final {conventions}  third script final {third}  "
        f"glossary {len(load_approved_glossary(workspace).entries)}"
    )


if __name__ == "__main__":
    jobs = sys.argv[1:] or sorted(
        os.path.basename(path) for path in glob.glob("runs/bench-*") if os.path.exists(os.path.join(path, "state.sqlite3"))
    )
    for job in jobs:
        try:
            print(summarize(job))
        except Exception as error:  # noqa: BLE001 - one unfinished job must not hide the others
            print(f"{job:28} not evaluated: {str(error)[:120]}")
