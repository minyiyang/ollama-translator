"""The same passages from several runs of one book, side by side (read-only, no model calls).

    python lang_benchmark/compare.py bench-alice-3ch-baseline bench-r1-qwen-en-zh
    python lang_benchmark/compare.py --every 10 bench-r1-qwen-zh-ja bench-r1-tg-zh-ja

Prints the summary line of each run, the segments whose final text differs between the first run and
the others (counted), and every Nth segment in full (default 25). Run from the repository root with
PYTHONPATH set to ".;lang_benchmark".
"""

import argparse
import re

from book_agent.stages.validate_repaired import load_repaired_validation_report, load_validated_repaired_documents
from benchlib import ws
from summarize import summarize

MARKER = re.compile(r"</?I\d{3}>")


def final_text(job):
    workspace = ws(job)
    segments = {s.segment_id: s for d in load_validated_repaired_documents(workspace) for s in d.document.segments}
    return segments, set(load_repaired_validation_report(workspace).review_segment_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", nargs="+")
    parser.add_argument("--every", type=int, default=25, help="print every Nth segment (default 25)")
    parser.add_argument("--width", type=int, default=400, help="characters of each passage shown")
    args = parser.parse_args()
    runs = {}
    for job in args.jobs:
        print(summarize(job))
        runs[job] = final_text(job)
    base_job = args.jobs[0]
    base, _ = runs[base_job]
    for job in args.jobs[1:]:
        other, _ = runs[job]
        shared = [key for key in base if key in other]
        differing = sum(1 for key in shared if base[key].translated_text != other[key].translated_text)
        print(f"\n{job} against {base_job}: {differing} of {len(shared)} segments differ"
              + ("" if len(shared) == len(base) == len(other) else f" ({len(base)} and {len(other)} segments)"))
    for index, key in enumerate(base):
        if index % args.every:
            continue
        print(f"\n-- {key}")
        print("  SOURCE:", MARKER.sub("", base[key].source_text)[: args.width])
        for job in args.jobs:
            segments, queue = runs[job]
            if key in segments:
                flag = " [review queue]" if key in queue else ""
                print(f"  {job}{flag}: {MARKER.sub('', segments[key].translated_text)[: args.width]}")


if __name__ == "__main__":
    main()
