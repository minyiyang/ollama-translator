"""Shared helpers for the benchmark scripts. Run them from the repository root."""

import glob
import json
import os
from collections import defaultdict

from book_agent.workspace import open_job_workspace


def ws(job):
    """The workspace of a job under runs/."""
    return open_job_workspace(f"runs/{job}")


def cost(job):
    """Per stage: [LLM calls, seconds, prompt tokens, output tokens], over every session of the job."""
    totals = defaultdict(lambda: [0, 0.0, 0, 0])
    for path in glob.glob(os.path.join("runs", job, "reports", "session-performance-*.json")):
        data = json.load(open(path, encoding="utf-8"))
        for stage, values in data["stages"].items():
            total = totals[stage]
            total[0] += values.get("llm_calls") or 0
            total[1] += values.get("elapsed_seconds") or 0
            total[2] += values.get("prompt_tokens") or 0
            total[3] += values.get("output_tokens") or 0
    return totals
