"""Build the jobs the browser tests use and serve the dashboard on them.

    python e2e/serve.py --port 8798

No model runs: each job goes through the workflow as `book-agent run` takes it,
with the test suite's stand-ins for the models, and stops where a person would
take over, at the final review. The jobs live in a temporary folder that is
made anew each time.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from book_agent.audit import AuditSeverity  # noqa: E402
from book_agent.cli import (  # noqa: E402
    CliProgressContext,
    _print_processing_summary,
    _start_session_log,
    make_generation_progress_printer,
    make_progress_printer,
)
from book_agent.config import AppConfig  # noqa: E402
from book_agent.languages import glossary_pair  # noqa: E402
from book_agent.ollama_client import GenerationMetrics, GenerationResult, OllamaClient, StructuredOutputError  # noqa: E402
from book_agent.pipeline_state import WorkflowStage  # noqa: E402
from book_agent.stages.audit import run_translation_audit_stage  # noqa: E402
from book_agent.stages.repair import run_translation_repair_stage  # noqa: E402
from book_agent.stages.repair_review import run_review_repair_stage  # noqa: E402
from book_agent.stages.review_repaired import run_repaired_review_stage  # noqa: E402
from book_agent.stages.translate import run_translation_stage  # noqa: E402
from book_agent.stages.title import run_title_stage  # noqa: E402
from book_agent.stages.validate_repaired import run_repaired_validation_stage  # noqa: E402
from book_agent.subtitles import cue_passages, parse_subtitles  # noqa: E402
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


# The same scene as a film's subtitles: (start, end, the cue's text, its German passage by passage).
FILM = [
    ("00:00:01,000", "00:00:04,000", "Alice opened the little door\nat 3 o'clock.", ["Alice öffnete die kleine Tür um 5 Uhr."]),
    ("00:00:05,000", "00:00:08,000", "- Who are you?\n- I hardly know, sir.", ["Wer bist du?", "Ich weiß es kaum, mein Herr."]),
    ("00:00:09,000", "00:00:11,500", "<i>The White Rabbit came trotting back.</i>", ["Das Weiße Kaninchen kam zurückgetrabt."]),
    ("00:00:12,000", "00:00:13,000", "\u266a \u266a", []),
    ("00:00:14,000", "00:00:17,000", "Alice began to cry again.", ["Alice fing wieder an zu weinen."]),
]
SUBTITLES = "".join(f"{number}\n{start} --> {end}\n{text}\n\n" for number, (start, end, text, _) in enumerate(FILM, start=1))


class BookTranslator:
    """Answers the translate stage with the book's own translation of each passage."""

    def __init__(self, column: int, wrong_hour: bool):
        self.translations = {passage[0]: passage[column] for passage in PASSAGES}
        for cue, (_, _, _, german) in zip(parse_subtitles(SUBTITLES, "srt").cues, FILM):
            self.translations.update(zip(cue_passages(cue), german))
        if not wrong_hour:
            self.translations = {source: text.replace("5", "3") for source, text in self.translations.items()}

    def report_progress(self, event):
        pass

    def generate_text(self, prompt, **_):
        source = prompt.split("Source:\n", 1)[1]
        pieces = re.findall(r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", source, flags=re.DOTALL)
        content = "\n".join(f"<{item}>{self._translated(text)}</{item}>" for item, text in pieces)
        return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20))

    def _translated(self, text: str) -> str:
        """A passage's translation; a reference to a note at its end stays at the end, as a model keeps it."""
        reference = re.search(r"(<I\d{3}>.*</I\d{3}>)\s*$", text)
        plain = text[: reference.start()] if reference else text
        return self.translations[plain.strip()] + (reference[1] if reference else "")


class TitleTranslator:
    """Answers the title stage: the book's title is no passage of the chapter. Of the
    annotated book it gives the footnote, the picture's description, and the contents in
    German, and the endnote without its link back, as a careless model might: the stage
    leaves that one in English, for a person to translate."""

    NOTES = {
        "<I000>1</I000> Kid gloves are made of the skin of a young goat.": "<I000>1</I000> Glac\u00e9handschuhe werden aus dem Leder junger Ziegen gemacht.",
        "Endnotes": "Anmerkungen",
        "The pool was made of the tears Alice had wept. <I000>Back</I000>": "Der Teich war aus Alices Tr\u00e4nen. Zur\u00fcck",
    }
    # As the last run of the stage worded it; the proofreader's edit, made before that run, says it otherwise.
    DESCRIPTIONS = {"The White Rabbit with his gloves": "Das wei\u00dfe Kaninchen mit den Handschuhen"}
    CONTENTS = {"Endnotes": "Anmerkungen", "Contents": "Inhalt"}

    def __init__(self, german: bool):
        self.german = german
        self.title = "Alice im Wunderland" if german else "\u7231\u4e3d\u4e1d\u68a6\u6e38\u4ed9\u5883"

    def generate_text(self, prompt, **_):
        content = self.title
        for heading, known in (("Notes:\n", self.NOTES), ("Descriptions:\n", self.DESCRIPTIONS), ("Entries:\n", self.CONTENTS)):
            if heading in prompt:
                asked = [line.split(". ", 1) for line in prompt.split(heading, 1)[1].splitlines()]
                content = "\n".join(f"{number}. {known.get(text, '') if self.german else ''}" for number, text in asked)
        return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=20, eval_count=5))


def make_annotated_epub(path: Path) -> Path:
    """The same chapter as a publisher might set it: a footnote beside the text, an endnote
    in a document of its own, and a picture of the White Rabbit."""
    passages = [passage[0] for passage in PASSAGES]
    with zipfile.ZipFile(REPO / "tests" / "data" / "word-pictures-and-notes.docx") as archive:
        picture = archive.read("word/media/image1.png")
    chapter = (
        "<?xml version='1.0' encoding='UTF-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        f"<head><title>{passages[0]}</title></head><body><h1>{passages[0]}</h1><p>{passages[1]}</p>"
        "<p><img src='images/rabbit.png' alt='The White Rabbit with his gloves'/></p>"
        f"<p>{passages[2]}<a href='#fn-1' id='ref-1' epub:type='noteref'>1</a></p>"
        f"<p>{passages[3]}<a href='endnotes.xhtml#en-2' id='ref-2' epub:type='noteref'>2</a></p>"
        + "".join(f"<p>{text}</p>" for text in passages[4:])
        + "<aside id='fn-1' epub:type='footnote'><p><a href='#ref-1'>1</a> Kid gloves are made of the skin of a young goat.</p></aside>"
        "</body></html>"
    )
    endnotes = (
        "<?xml version='1.0' encoding='UTF-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        "<head><title>Endnotes</title></head><body><section epub:type='endnotes'><h2>Endnotes</h2><ol>"
        "<li id='en-2' epub:type='endnote'><p>The pool was made of the tears Alice had wept. "
        "<a href='chapter.xhtml#ref-2' epub:type='backlink'>Back</a></p></li></ol></section></body></html>"
    )
    navigation = (
        "<?xml version='1.0' encoding='UTF-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        f"<head><title>Contents</title></head><body><nav epub:type='toc'><ol><li><a href='chapter.xhtml'>{passages[0]}</a></li>"
        "<li><a href='endnotes.xhtml'>Endnotes</a></li></ol></nav></body></html>"
    )
    package = (
        "<?xml version='1.0' encoding='UTF-8'?>\n<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='id'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='id'>alice-annotated</dc:identifier>"
        "<dc:title>Alice's Adventures in Wonderland</dc:title><dc:creator>Lewis Carroll</dc:creator><dc:language>en</dc:language></metadata>"
        "<manifest><item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
        "<item id='chapter' href='chapter.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='endnotes' href='endnotes.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='rabbit' href='images/rabbit.png' media-type='image/png'/></manifest>"
        "<spine><itemref idref='chapter'/><itemref idref='endnotes'/></spine></package>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>",
        )
        for name, text in (("content.opf", package), ("nav.xhtml", navigation), ("chapter.xhtml", chapter), ("endnotes.xhtml", endnotes)):
            archive.writestr(f"OEBPS/{name}", text)
        archive.writestr("OEBPS/images/rabbit.png", picture)
    return path


class MockOllama:
    """Ollama's chat API as the title stage meets it, without a model: the book's
    title and its notes in German, with the token counts and times Ollama reports.
    The run's own client talks to it, so the run is logged as a real one is."""

    NOTES = {"Kid gloves are made of the skin of a young goat.": "Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht."}

    def chat(self, **request):
        prompt = request["messages"][-1]["content"]
        if "footnotes of a book" in prompt:
            asked = prompt.split("Notes:\n", 1)[1].splitlines()
            answer = "\n".join(f"{number}. {self.NOTES[line.split('. ', 1)[1]]}" for number, line in enumerate(asked, start=1))
        elif "descriptions of a book's pictures" in prompt:
            answer = "1. Eine Karte des Gartens"
        else:
            answer = "Alice im Wunderland"
        done = {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": len(prompt) // 4,
            "eval_count": len(answer) // 3 + 4,
            "total_duration": 1_500_000_000,
            "eval_duration": 900_000_000,
        }
        return iter([{"message": {"content": answer}}, done])


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


def build_job(
    base: Path, job_id: str, pair: str, wrong_hour: bool = True, book: str = "alice-in-wonderland.epub", logged: bool = False
) -> None:
    """One job, run as `book-agent run` runs it, with stand-ins for the models.
    With `wrong_hour` the translation has a mistake the repair model cannot fix,
    so the run stops for the final review; without, it completes. A `logged`
    run is logged as the command logs it, and its title stage is asked through
    the run's own client, of a stand-in for Ollama's API."""
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
    workspace = create_job_workspace(base / "books" / book, base / "runs", config, job_id=job_id)
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
        WorkflowStage.TRANSLATE_TITLE: lambda w, c, _: run_title_stage(w, c, TitleTranslator(german)),
    })
    client: object = object()
    options: dict = {}
    if logged:
        runners[WorkflowStage.TRANSLATE_TITLE] = default_stage_runners()[WorkflowStage.TRANSLATE_TITLE]
        context = CliProgressContext()
        _start_session_log(context, workspace, "run")
        client = OllamaClient(config.ollama, backend=MockOllama(), progress=make_generation_progress_printer(plain=True, context=context))
        options = {"progress": make_progress_printer(plain=True, context=context)}
    result = None
    # The log is what is wanted of a logged run; what it echoes to the terminal is not.
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        for _ in range(4):
            result = run_workflow(workspace, config, client=client, stage_runners=runners, **options)
            if result.result != "paused" or result.stage != WorkflowStage.APPROVE_GLOSSARY.value:
                break
            approve_glossary(workspace, config=config)
        if logged:
            _print_processing_summary(context, workspace)
    print(f"{job_id}: {result.result} at {result.stage}", flush=True)
    return workspace, config


def keep_an_edit_from_before_the_last_run(workspace, config) -> None:
    """The picture's description as the job's proofreader worded it, against what an earlier
    run of the title stage gave. The last run worded it otherwise, so the edit is a conflict for
    them to resolve; the book was compiled with it, as the compile uses an edit in conflict."""
    from book_agent.book_edits import book_items
    from book_agent.hashing import sha256_text
    from book_agent.text_edits import EditAction, SegmentEditEvent, _append_action_event

    item = next(item for item in book_items(workspace) if item.kind == "description")
    earlier = "Das Weiße Kaninchen mit Handschuhen"

    def build(count, events):
        return SegmentEditEvent(
            event_id=f"E{count:06d}",
            at="2026-10-01T10:00:00+02:00",
            author="proofreader",
            segment_id=item.item_id,
            document_id=item.document_id,
            action=EditAction.EDIT,
            text="Das Weiße Kaninchen mit seinen Handschuhen",
            previous_text=earlier,
            base_source_sha256=sha256_text(item.source),
            base_target_sha256=sha256_text(earlier),
            base_target_text=earlier,
            reason="Says whose gloves they are",
        )

    _append_action_event(workspace, segment_id=item.item_id, expected_event_id=None, build=build)
    runners = default_stage_runners()
    for stage in (WorkflowStage.COMPILE, WorkflowStage.VALIDATE_EPUB):
        runners[stage](workspace, config, None)


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
        # The finished German job is the chapter as a publisher set it, with a footnote, an endnote, and a picture.
        make_annotated_epub(base / "books" / "alice-annotated.epub")
        # A book that is not on the dashboard yet, for the test that adds one.
        UPLOAD.parent.mkdir(exist_ok=True)
        make_epub(UPLOAD, opf=HOLMES, chapter=HOLMES_CHAPTER)
        for job_id, (pair, wrong_hour) in JOBS.items():
            built = build_job(base, job_id, pair, wrong_hour, *(["alice-annotated.epub"] if job_id == "alice-german-finished" else []))
            if job_id == "alice-german-finished":
                keep_an_edit_from_before_the_last_run(*built)
        # Two subtitle jobs: one waiting for its reviewer, one finished.
        (base / "books" / "alice-film.srt").write_text(SUBTITLES, encoding="utf-8")
        build_job(base, "alice-film", "en>de", True, "alice-film.srt")
        build_job(base, "alice-film-finished", "en>de", False, "alice-film.srt")
        # The same chapter as its translator might keep it: a Markdown manuscript, with a plate
        # beside it and a note on the gloves. Its run is logged as the command logs one.
        (base / "books" / "plates").mkdir()
        with zipfile.ZipFile(REPO / "tests" / "data" / "word-pictures-and-notes.docx") as archive:
            (base / "books" / "plates" / "garden.png").write_bytes(archive.read("word/media/image1.png"))
        manuscript = "\n\n".join(
            [
                "---\ntitle: Alice's Adventures in Wonderland\n---",
                "# " + PASSAGES[0][0],
                PASSAGES[1][0],
                "![A map of the garden](plates/garden.png)",
                PASSAGES[2][0] + "[^gloves]",
                *(passage[0] for passage in PASSAGES[3:]),
                "[^gloves]: " + next(iter(MockOllama.NOTES)),
            ]
        ) + "\n"
        (base / "books" / "alice-manuscript.md").write_text(manuscript, encoding="utf-8")
        build_job(base, "alice-manuscript-finished", "en>de", False, "alice-manuscript.md", logged=True)
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
