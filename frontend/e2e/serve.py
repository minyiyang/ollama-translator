"""Build the jobs the browser tests use and serve the dashboard on them.

    python e2e/serve.py --port 8798

No model runs: each job goes through the workflow as `book-agent run` takes it,
with the test suite's stand-ins for the models, and stops where a person would
take over, at the final review. The jobs live in a temporary folder that is
made anew each time.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from book_agent.audit import AuditSeverity  # noqa: E402
from book_agent.config import AppConfig  # noqa: E402
from book_agent.languages import glossary_pair  # noqa: E402
from book_agent.ollama_client import GenerationMetrics, GenerationResult, StructuredOutputError  # noqa: E402
from book_agent.pipeline_state import WorkflowStage  # noqa: E402
from book_agent.stages.audit import run_translation_audit_stage  # noqa: E402
from book_agent.stages.repair import run_translation_repair_stage  # noqa: E402
from book_agent.stages.repair_review import run_review_repair_stage  # noqa: E402
from book_agent.stages.review_repaired import run_repaired_review_stage  # noqa: E402
from book_agent.stages.translate import run_translation_stage  # noqa: E402
from book_agent.stages.validate_repaired import run_repaired_validation_stage  # noqa: E402
from book_agent.web import serve_ui  # noqa: E402
from book_agent.workflow import approve_glossary, default_stage_runners, run_workflow  # noqa: E402
from book_agent.workspace import create_job_workspace  # noqa: E402
from tests.epub_fixture import OPF, make_epub  # noqa: E402
from tests.test_audit_stage import FakeAuditClient  # noqa: E402
from tests.test_repair_stage import FakeRepairClient  # noqa: E402
from tests.test_validate_repaired_stage import FakeUnchangedFeedbackClient, FakeVerificationClient  # noqa: E402

# The opening of Alice's Adventures in Wonderland, chapter II, lightly cut:
# English, German, Chinese.
PASSAGES = [
    ("The Pool of Tears", "Der Tränenteich", "眼泪池"),
    (
        "Alice opened the little door at 3 o'clock and looked along the passage into the loveliest garden you ever saw.",
        # The translation says five o'clock: the passage a reviewer has to put right.
        "Alice öffnete die kleine Tür um 5 Uhr und blickte durch den Gang in den schönsten Garten, den man je gesehen hat.",
        "爱丽丝在5点钟打开了那扇小门，顺着过道望进一座你从未见过的最可爱的花园。",
    ),
    (
        "The White Rabbit came trotting back, splendidly dressed, with a pair of white kid gloves in one hand.",
        "Das Weiße Kaninchen kam zurückgetrabt, prächtig gekleidet, mit einem Paar weißer Glacéhandschuhe in der einen Hand.",
        "白兔小跑着回来了，穿得十分讲究，一只手里拿着一双白色的羊皮手套。",
    ),
    (
        "Alice began to cry again, for she felt very lonely and low-spirited.",
        "Alice fing wieder an zu weinen, denn sie fühlte sich sehr einsam und niedergeschlagen.",
        "爱丽丝又哭了起来，因为她觉得非常孤单，心情低落。",
    ),
    (
        "Soon she was swimming in the pool of tears which she had wept when she was nine feet high.",
        "Bald schwamm sie in dem Tränenteich, den sie geweint hatte, als sie neun Fuß groß war.",
        "不久，她就在自己九英尺高时哭出的眼泪池里游起泳来。",
    ),
]

CHAPTER = (
    "<?xml version='1.0'?>\n<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Alice</title></head>\n<body>"
    f"<h1>{PASSAGES[0][0]}</h1>" + "".join(f"<p>{passage[0]}</p>" for passage in PASSAGES[1:]) + "</body></html>"
).encode("utf-8")
BOOK = OPF.replace(b"Fixture Book", b"Alice's Adventures in Wonderland").replace(b"Test Author", b"Lewis Carroll")


UPLOAD = Path(__file__).resolve().parent / ".books" / "the-sign-of-the-four.epub"
HOLMES = OPF.replace(b"Fixture Book", b"The Sign of the Four").replace(b"Test Author", b"Arthur Conan Doyle")
HOLMES_CHAPTER = (
    "<?xml version='1.0'?>\n<html xmlns='http://www.w3.org/1999/xhtml'><head><title>The Sign of the Four</title></head>\n"
    "<body><h1>The Science of Deduction</h1><p>Sherlock Holmes took his bottle from the corner of the mantelpiece "
    "and his hypodermic syringe from its neat morocco case.</p></body></html>"
).encode("utf-8")


class BookTranslator:
    """Answers the translate stage with the book's own translation of each passage."""

    def __init__(self, column: int, wrong_hour: bool):
        self.translations = {passage[0]: passage[column] for passage in PASSAGES}
        if not wrong_hour:
            self.translations = {source: text.replace("5", "3") for source, text in self.translations.items()}

    def report_progress(self, event):
        pass

    def generate_text(self, prompt, **_):
        source = prompt.split("Source:\n", 1)[1]
        pieces = re.findall(r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", source, flags=re.DOTALL)
        content = "\n".join(f"<{item}>{self.translations[text.strip()]}</{item}>" for item, text in pieces)
        return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20))


class BookAuditor(FakeAuditClient):
    """Answers the audit as a careful reader would: it says so when a passage has the wrong hour."""

    def __init__(self, wrong_hour: bool):
        super().__init__(
            issue_severity=AuditSeverity.HIGH,
            issue_message="The source says 3 o'clock; the translation says 5.",
        )
        self.wrong_hour = wrong_hour

    def generate_structured(self, prompt, schema, **options):
        if "Allowed IDs:" not in prompt:
            raise StructuredOutputError("no ruling")  # the numeric ruling: leave the rule's finding as it is
        self.return_issue = self.wrong_hour and "3 o'clock" in prompt
        return super().generate_structured(prompt, schema, **options)


def build_job(base: Path, job_id: str, pair: str, wrong_hour: bool = True) -> None:
    """One job, run as `book-agent run` runs it, with stand-ins for the models.
    With `wrong_hour` the translation has a mistake the repair model cannot fix,
    so the run stops for the final review; without, it completes."""
    german = pair.endswith("de")
    seed = base / f"{job_id}-glossary.json"
    terms = [
        {"source": "Alice", "target": "Alice" if german else "爱丽丝", "category": "人名"},
        {"source": "White Rabbit", "target": "Weißes Kaninchen" if german else "白兔", "category": "人名"},
    ]
    config = AppConfig.model_validate({
        "translation": {"direction": pair},
        "audit": {"semantic_enabled": True},
        "glossary": {"extraction_enabled": False, "seed_glossaries": [str(seed)]},
        "workflow": {"require_glossary_review": False},
    })
    seed.write_text(
        json.dumps({"pair": glossary_pair(config.translation.direction).value, "entries": terms}, ensure_ascii=False),
        encoding="utf-8",
    )
    workspace = create_job_workspace(base / "books" / "alice-in-wonderland.epub", base / "runs", config, job_id=job_id)
    translator, auditor = BookTranslator(1 if german else 2, wrong_hour), BookAuditor(wrong_hour)
    # The repair model fails on the wrong hour, so it is left for the reviewer.
    repairer = FakeRepairClient(invalid_calls=10 if wrong_hour else 0)
    verifier = FakeUnchangedFeedbackClient(passed=True) if wrong_hour else FakeVerificationClient()
    runners = default_stage_runners()
    runners.update({
        WorkflowStage.TRANSLATE: lambda w, c, _: run_translation_stage(w, c, translator),
        WorkflowStage.AUDIT_TRANSLATION: lambda w, c, _: run_translation_audit_stage(w, c, auditor),
        WorkflowStage.REPAIR_TRANSLATION: lambda w, c, _: run_translation_repair_stage(w, c, repairer),
        WorkflowStage.REVIEW_REPAIRED: lambda w, c, _: run_repaired_review_stage(w, c, verifier),
        WorkflowStage.REPAIR_REVIEW: lambda w, c, _: run_review_repair_stage(w, c, verifier),
        WorkflowStage.VALIDATE_REPAIRED: lambda w, c, _: run_repaired_validation_stage(w, c, verifier),
    })
    result = None
    for _ in range(4):
        result = run_workflow(workspace, config, client=object(), stage_runners=runners)
        if result.result != "paused" or result.stage != WorkflowStage.APPROVE_GLOSSARY.value:
            break
        approve_glossary(workspace, config=config)
    print(f"{job_id}: {result.result} at {result.stage}", flush=True)


# Each test that changes a job has one of its own.
JOBS = {
    "alice-german": ("en>de", True),
    "alice-german-accept": ("en>de", True),
    "alice-german-rewrite": ("en>de", True),
    "alice-german-text": ("en>de", True),
    "alice-german-finished": ("en>de", False),
    "alice-chinese": ("en-zh", True),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8798)
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="book-agent-e2e-"))
    try:
        (base / "books").mkdir()
        (base / "configs").mkdir()
        make_epub(base / "books" / "alice-in-wonderland.epub", opf=BOOK, chapter=CHAPTER)
        # A book that is not on the dashboard yet, for the test that adds one.
        UPLOAD.parent.mkdir(exist_ok=True)
        make_epub(UPLOAD, opf=HOLMES, chapter=HOLMES_CHAPTER)
        for job_id, (pair, wrong_hour) in JOBS.items():
            build_job(base, job_id, pair, wrong_hour)
        serve_ui(
            base / "runs",
            base / "configs",
            sample_dirs=[base / "books"],
            template=REPO / "config.example.yaml",
            port=args.port,
            open_browser=False,
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
