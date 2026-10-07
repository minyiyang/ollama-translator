"""Subtitle jobs: the file read as cues, translated cue by cue, and written back with its times untouched."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from book_agent.audit import AuditCategory, AuditIssue, AuditSeverity
from book_agent.config import AppConfig
from book_agent.languages import LanguagePair
from book_agent.ollama_client import GenerationMetrics, GenerationResult
from book_agent.stages.audit import load_document_audits, run_translation_audit_stage
from book_agent.stages.compile import load_compiled_epub_path, run_epub_compile_stage
from book_agent.stages.decompile import load_decompile_manifest, run_decompile_stage
from book_agent.stages.preprocess import load_preprocessed_documents, run_preprocessing_stage
from book_agent.stages.repair import run_translation_repair_stage
from book_agent.stages.repair_review import run_review_repair_stage
from book_agent.stages.review_repaired import run_repaired_review_stage
from book_agent.stages.translate import run_translation_stage
from book_agent.stages.validate_epub import run_epub_validation_stage
from book_agent.stages.validate_repaired import run_repaired_validation_stage
from book_agent.subtitles import (
    SubtitleError,
    cue_passages,
    job_type,
    parse_subtitles,
    read_subtitles,
    reading_limits,
    render_subtitles,
    wrap_cue,
)
from book_agent.workspace import create_job_workspace
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_validate_repaired_stage import FakeVerificationClient

# The opening of The Sign of the Four as a film might subtitle it: (start, end, text, German).
SCENE = [
    ("00:00:01,000", "00:00:03,500", "<i>Sherlock Holmes took his bottle\nfrom the corner of the mantelpiece.</i>",
     ["Holmes nahm seine Flasche vom Kaminsims."]),
    ("00:00:04,000", "00:00:06,000", "- Which is it today?\n- Cocaine.", ["Was ist es heute?", "Kokain."]),
    ("00:00:07,000", "00:00:08,000", "{\\an8}♪ ♪", None),
    ("00:00:09,000", "00:00:10,000", "Yes.", ["Ja."]),
    ("00:00:12,000", "00:00:13,000", "Yeah.", ["Ja."]),
    ("00:00:16,000", "00:00:18,000", "Watson!", ["Watson!"]),
    ("00:00:19,000", "00:00:22,000", "It is cocaine,\na seven-per-cent solution.", ["Es ist Kokain, eine siebenprozentige Lösung."]),
    ("00:00:27,000", "00:00:28,200", "My mind rebels at stagnation.",
     ["Mein Geist rebelliert gegen den Stillstand, gib mir Probleme, gib mir Arbeit, gib mir das abstruseste Kryptogramm."]),
    ("00:00:32,000", "00:00:34,000", "Miss Morstan entered the room at 3 o'clock.", ["Miss Morstan betrat das Zimmer um 3 Uhr."]),
    ("00:00:38,000", "00:00:40,000", "Thank you, Mr. Holmes.", ["Danke, Mr. Holmes."]),
]
SRT = "".join(f"{number}\n{start} --> {end}\n{text}\n\n" for number, (start, end, text, _) in enumerate(SCENE, start=1))

VTT = """WEBVTT - The Sign of the Four

NOTE Translated from the English.

intro
00:01.000 --> 00:03.500 line:90% align:center
<v Watson>Which is it today, <i>morphine</i> &amp; cocaine?

00:00:04.000 --> 00:00:06.000
It is cocaine.
"""

ASS = """[Script Info]
Title: The Sign of the Four

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,not shown
Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\an8}Which is it today,\\Nmorphine or cocaine?
Dialogue: 0,0:00:04.00,0:00:06.00,Default,Holmes,0,0,0,,It is cocaine, a seven-per-cent solution.
"""


def test_a_subrip_file_is_read_as_cues_with_their_times_and_markup_set_aside():
    cues = parse_subtitles(SRT, "srt").cues
    assert len(cues) == len(SCENE)
    first, second, music = cues[0], cues[1], cues[2]
    assert (first.start, first.end, first.duration) == (1000, 3500, 2.5)
    # The italics around the whole cue are kept to put back; its two lines are one passage.
    assert (first.prefix, first.suffix) == ("<i>", "</i>")
    assert cue_passages(first) == ["Sherlock Holmes took his bottle from the corner of the mantelpiece."]
    # Two speakers: a passage each, without the dash.
    assert second.speakers and cue_passages(second) == ["Which is it today?", "Cocaine."]
    # Music has nothing to translate, and keeps its position code.
    assert not music.translatable and cue_passages(music) == [] and music.prefix == "{\\an8}"


def test_a_file_with_nothing_changed_is_written_back_as_it_was():
    for text, kind in ((SRT, "srt"), (VTT, "vtt"), (ASS, "ass")):
        assert render_subtitles(parse_subtitles(text, kind), {}, reading_limits("de")) == text, kind
    # A file from Windows keeps its line endings.
    windows = SRT.replace("\n", "\r\n")
    assert render_subtitles(parse_subtitles(windows, "srt"), {}, reading_limits("de")) == windows
    with pytest.raises(SubtitleError, match="no subtitle cues"):
        parse_subtitles("Just some text.\n", "srt")


def test_translated_cues_keep_their_times_markup_and_speakers():
    german = {number: text for number, (_, _, _, text) in enumerate(SCENE, start=1) if text}
    written = render_subtitles(parse_subtitles(SRT, "srt"), german, reading_limits("de"))
    assert "1\n00:00:01,000 --> 00:00:03,500\n<i>Holmes nahm seine Flasche vom Kaminsims.</i>\n" in written
    assert "2\n00:00:04,000 --> 00:00:06,000\n- Was ist es heute?\n- Kokain.\n" in written
    assert "3\n00:00:07,000 --> 00:00:08,000\n{\\an8}♪ ♪\n" in written
    # Too long for one line: two of about the same length.
    assert "Es ist Kokain, eine\nsiebenprozentige Lösung.\n" in written
    assert re.findall(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", written) == re.findall(
        r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", SRT
    )


def test_webvtt_and_ass_keep_what_is_theirs():
    vtt = parse_subtitles(VTT, "vtt")
    assert [cue.start for cue in vtt.cues] == [1000, 4000]
    # The speaker's tag stays; italics inside the line are dropped; an entity is the character it stands for.
    assert vtt.cues[0].prefix == "<v Watson>" and vtt.cues[0].lines == ["Which is it today, morphine & cocaine?"]
    written = render_subtitles(vtt, {1: ["Was ist es heute, Morphium & Kokain?"], 2: ["Es ist Kokain."]}, reading_limits("de"))
    assert "intro\n00:01.000 --> 00:03.500 line:90% align:center\n<v Watson>Was ist es heute, Morphium &amp; Kokain?\n" in written
    assert written.startswith("WEBVTT - The Sign of the Four\n\nNOTE Translated from the English.\n")

    ass = parse_subtitles(ASS, "ass")
    assert [(cue.start, cue.end) for cue in ass.cues] == [(1000, 3500), (4000, 6000)]  # the comment line is not a cue
    assert ass.cues[0].prefix == "{\\an8}" and ass.cues[0].lines == ["Which is it today,", "morphine or cocaine?"]
    narrow = reading_limits("de", line_characters=24)
    written = render_subtitles(ass, {1: ["Was ist es heute, Morphium oder Kokain?"], 2: ["Es ist Kokain, eine siebenprozentige Lösung."]}, narrow)
    assert "Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\an8}Was ist es heute,\\NMorphium oder Kokain?\n" in written
    assert "Dialogue: 0,0:00:04.00,0:00:06.00,Default,Holmes,0,0,0,,Es ist Kokain, eine\\Nsiebenprozentige Lösung.\n" in written
    assert "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,not shown\n" in written


def test_lines_are_broken_for_the_language_they_are_in():
    german, chinese = reading_limits("de"), reading_limits("zh")
    assert (german.line_characters, german.lines, german.characters_per_second) == (42, 2, 20.0)
    assert (chinese.line_characters, chinese.characters_per_second) == (16, 9.0)  # a character says more
    assert reading_limits("ko").characters_per_second == 12.0
    assert reading_limits("de", line_characters=37, characters_per_second=17).line_characters == 37
    assert wrap_cue("Danke, Mr. Holmes.", german) == ["Danke, Mr. Holmes."]
    # At a space, and after the comma where that is near the middle.
    assert wrap_cue("Ich kann es nicht sagen, aber ich werde es herausfinden.", german) == [
        "Ich kann es nicht sagen,", "aber ich werde es herausfinden.",
    ]
    lines = wrap_cue("福尔摩斯从壁炉台的角落里拿起他的瓶子，又取出皮下注射器。", chinese)
    assert all(len(line) <= 16 for line in lines) and "".join(lines) == "福尔摩斯从壁炉台的角落里拿起他的瓶子，又取出皮下注射器。"
    assert not any(line[0] in "，。" for line in lines)  # no line opens with a comma or a full stop


def test_which_files_make_a_subtitle_job():
    assert [job_type(name) for name in ("film.srt", "film.VTT", "film.ass", "film.ssa")] == ["subtitles"] * 4
    assert [job_type(name) for name in ("book.epub", "book.docx", "notes.txt")] == ["book"] * 3


class _Subtitler:
    """Stands in for the translation model: answers each passage of the scene with its German."""

    def __init__(self, scene):
        self.prompts = []
        self.table = {}
        for cue, (_, _, _, german) in zip(parse_subtitles(SRT, "srt").cues, scene):
            self.table.update(zip(cue_passages(cue), german or []))

    def report_progress(self, event):
        pass

    def generate_text(self, prompt, **_):
        self.prompts.append(prompt)
        source = prompt.split("Source:\n", 1)[1]
        pieces = re.findall(r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", source, flags=re.DOTALL)
        content = "\n".join(f"<{item}>{self.table[text.strip()]}</{item}>" for item, text in pieces)
        return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20))


def _translated(base: Path, scene, config: AppConfig):
    """The scene's file taken through decompile, preprocess, translate, and audit."""
    source = base / "sign.srt"
    source.write_text(SRT, encoding="utf-8")
    workspace = create_job_workspace(source, base / "runs", config, job_id="sign")
    run_decompile_stage(workspace)
    publish_approved_glossary(workspace, [])
    run_preprocessing_stage(workspace, config)
    translator = _Subtitler(scene)
    run_translation_stage(workspace, config, translator)
    # The model is told it is writing subtitles, not the literary prose the config's default style asks for.
    assert all("These are the subtitles of a film" in prompt and "literary prose" not in prompt for prompt in translator.prompts)
    run_translation_audit_stage(workspace, config)
    return workspace


CONFIG = {"translation": {"direction": "en>de"}, "audit": {"semantic_enabled": False}}


def test_a_subtitle_file_becomes_a_part_of_the_film_with_a_passage_for_each_cue():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated(Path(directory), SCENE, config)
        assert len(read_subtitles(workspace.source_file).cues) == len(SCENE)
        manifest = load_decompile_manifest(workspace)
        assert manifest.source_format == "subtitle"
        assert [(document.title, len(document.segments)) for document in manifest.documents] == [("0:00:01 \u2013 0:00:40", 10)]
        assert [segment.element_path for segment in manifest.documents[0].segments][:3] == ["cue[1]", "cue[2]/line[1]", "cue[2]/line[2]"]
        # Each passage goes on with its cue's time on screen, and the job with its language's limits.
        document = load_preprocessed_documents(workspace)[0]
        assert document.reading_limits == (42, 2, 20.0)
        assert document.cues["D0000-S000001"] == (1, 2.5, False) and document.cues["D0000-S000002"] == (2, 2.0, True)


def test_the_audit_finds_a_cue_that_cannot_be_read_in_its_time():
    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated(Path(directory), SCENE, config)
        # One cue says far more than can be read in its second on screen. The short
        # answers the two languages share ("Ja." twice, "Watson!") are not findings.
        issues = [issue for audit in load_document_audits(workspace) for issue in audit.issues]
        # It is found twice over: for its time, and for the three lines it would need.
        assert [(issue.segment_id, issue.category, issue.severity, issue.code) for issue in issues] == [
            ("D0000-S000008", AuditCategory.READABILITY, AuditSeverity.MEDIUM, "unreadable_subtitle"),
            ("D0000-S000008", AuditCategory.READABILITY, AuditSeverity.MEDIUM, "unreadable_subtitle"),
        ]
        messages = sorted(issue.message for issue in issues)
        assert messages[0].startswith("subtitle does not fit the screen: 4 lines")
        assert messages[1].startswith("subtitle is too long to read in its time: 98 characters in 1.2 seconds, where about 24")
        # A job set to a slower reader finds the first cue too: 34 characters in two and a half seconds.
        slow = AppConfig.model_validate({**CONFIG, "subtitles": {"characters_per_second": 10}})
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated(Path(directory), SCENE, slow)
        found = {issue.segment_id for audit in load_document_audits(workspace) for issue in audit.issues}
        assert {"D0000-S000001", "D0000-S000008"} <= found


def test_a_speakers_line_too_long_for_the_screen_is_found():
    from book_agent.audit import _audit_reading
    from book_agent.preprocessing import PreprocessedDocument, PreprocessedSegment
    from book_agent.translation import TranslatedDocument, TranslatedSegment

    # A cue of two speakers on screen for 2.8 seconds, as the model translated it in the Alice run.
    passages = {"S1": "Ich wage zu sagen, dass es vielleicht einen gibt.", "S2": "Einen, in der Tat!"}
    source = PreprocessedDocument(
        order=0, manifest_id="part-0001", archive_path="subtitles/part-0001.txt", source_sha256="x",
        segments=[PreprocessedSegment(segment_id=key, original_text="x", processed_text="x") for key in passages],
        cues={key: (164, 2.8, True) for key in passages}, reading_limits=(42, 2, 20.0),
    )
    translated = TranslatedDocument(
        order=0, manifest_id="part-0001", archive_path="subtitles/part-0001.txt",
        direction=LanguagePair("en>de"), style="literary",
        segments=[TranslatedSegment(segment_id=key, source_text="x", translated_text=text) for key, text in passages.items()],
    )
    found = [(issue.segment_id, issue.severity, issue.message.split(":")[0]) for issue in _audit_reading(source, translated)]
    # "- Ich wage zu sagen, ..." is 51 characters: speakers' lines are not broken again, so it must be found.
    assert found == [("S1", AuditSeverity.MEDIUM, "subtitle does not fit the screen")]
    # Shown a little longer than it takes to read, the same cue fails on both counts.
    brief = source.model_copy(update={"cues": {key: (164, 2.0, True) for key in passages}})
    assert [issue.message.split(":")[0] for issue in _audit_reading(brief, translated)] == [
        "subtitle is too long to read in its time", "subtitle does not fit the screen",
    ]


def test_a_subtitle_file_goes_through_the_pipeline_and_comes_back_a_subtitle_file():
    config = AppConfig.model_validate(CONFIG)
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]
    with tempfile.TemporaryDirectory() as directory:
        workspace = _translated(Path(directory), scene, config)
        assert [issue for audit in load_document_audits(workspace) for issue in audit.issues] == []
        run_translation_repair_stage(workspace, config)
        verifier = FakeVerificationClient()
        run_repaired_review_stage(workspace, config, verifier)
        run_review_repair_stage(workspace, config, verifier)
        run_repaired_validation_stage(workspace, config, verifier)
        run_epub_compile_stage(workspace, config)
        assert run_epub_validation_stage(workspace).passed
        # What comes out is a subtitle file of the kind that went in, and nothing else.
        output = Path(load_compiled_epub_path(workspace))
        assert output.suffix == ".srt" and [path.suffix for path in output.parent.iterdir()] == [".srt"]
        written = parse_subtitles(output.read_text(encoding="utf-8"), "srt")
        original = parse_subtitles(SRT, "srt")
        assert [(cue.start, cue.end, cue.prefix, cue.suffix) for cue in written.cues] == [
            (cue.start, cue.end, cue.prefix, cue.suffix) for cue in original.cues
        ]
        assert written.cues[0].lines == ["Holmes nahm seine Flasche vom Kaminsims."]
        assert written.cues[1].lines == ["- Was ist es heute?", "- Kokain."]
        assert written.cues[2].lines == ["\u266a \u266a"]
        assert written.cues[7].lines == ["Mein Geist rebelliert."]


def test_a_books_config_and_documents_are_stored_as_before_there_were_subtitle_jobs():
    assert "subtitles" not in AppConfig().model_dump()
    assert AppConfig.model_validate({"subtitles": {"line_characters": 37}}).model_dump()["subtitles"]["line_characters"] == 37
    with pytest.raises(ValueError):
        AppConfig.model_validate({"subtitles": {"line_characters": 2}})


# The opening of "A Mad Tea-Party" (Alice's Adventures in Wonderland, chapter VII) cut into
# sixty cues by lang_benchmark/make_subtitles.py: speech, narration in italics, two-speaker cues.
TEA_PARTY = Path(__file__).parent / "data" / "alice-mad-tea-party.srt"


def test_a_scene_of_real_dialogue_is_read_and_written_back_unchanged():
    text = TEA_PARTY.read_text(encoding="utf-8")
    parsed = read_subtitles(TEA_PARTY)
    cues = parsed.cues
    assert len(cues) == 60 and [cue.number for cue in cues] == list(range(1, 61))
    assert render_subtitles(parsed, {}, reading_limits("en")) == text
    assert not cues[0].translatable and cues[0].prefix == "{\\an8}"  # the titles' music
    assert cues[1].prefix == "<i>" and cue_passages(cues[1]) == ["There was a table set out under a tree in front of the house,"]
    assert cue_passages(cues[15]) == ["I don’t see any wine,", "There isn’t any,"]  # Alice, then the March Hare
    assert sum(cue.speakers for cue in cues) >= 3 and sum(cue.prefix == "<i>" for cue in cues) >= 15
    assert all(cues[index].start >= cues[index - 1].end for index in range(1, 60))


def test_every_cue_of_the_scene_keeps_its_place_when_all_of_them_are_translated():
    parsed = read_subtitles(TEA_PARTY)
    # Stands in for a translation: every passage, in capitals, a little longer than it was.
    shouted = {cue.number: [passage.upper() + " OH!" for passage in cue_passages(cue)] for cue in parsed.cues if cue.translatable}
    limits = reading_limits("en")
    written = parse_subtitles(render_subtitles(parsed, shouted, limits), "srt")
    assert [(cue.start, cue.end, cue.prefix, cue.suffix) for cue in written.cues] == [
        (cue.start, cue.end, cue.prefix, cue.suffix) for cue in parsed.cues
    ]
    for before, after in zip(parsed.cues, written.cues):
        if not before.translatable:
            assert after.lines == before.lines
            continue
        assert after.speakers == before.speakers
        said = " ".join(line.lstrip("- ") for line in after.lines)
        assert said == " ".join(shouted[before.number]), before.number
        # Broken for the screen again: no line over the limit, and no more lines than hold the text.
        assert all(len(line) <= limits.line_characters for line in after.lines), after.lines
        assert before.speakers or len(after.lines) <= -(-len(said) // limits.line_characters) + 1


def test_the_scene_is_one_part_of_the_film_with_a_passage_for_each_thing_said():
    from book_agent.subtitles import inspect_subtitle_file

    manifest = inspect_subtitle_file(TEA_PARTY, "abc")
    parsed = read_subtitles(TEA_PARTY)
    assert len(manifest.documents) == 1 and manifest.documents[0].title.startswith("0:00:06 – 0:0")
    segments = manifest.documents[0].segments
    assert len(segments) == sum(len(cue_passages(cue)) for cue in parsed.cues)
    assert segments[0].text == "There was a table set out under a tree in front of the house,"
    assert any(segment.text == "Have some wine," for segment in segments)
    assert not any("<i>" in segment.text or "\n" in segment.text for segment in segments)


def test_the_audit_model_reads_a_scene_in_a_few_calls_and_is_told_it_is_reading_subtitles():
    from book_agent.ollama_client import StructuredOutputError
    from book_agent.repair import build_repair_prompt
    from book_agent.stages.translate import load_translated_documents
    from tests.test_audit_stage import FakeAuditClient

    class Auditor(FakeAuditClient):
        def generate_structured(self, prompt, schema, **options):
            if "Allowed IDs:" not in prompt:
                raise StructuredOutputError("no numeric ruling")
            return super().generate_structured(prompt, schema, **options)

    # The book default: one passage to a call. A scene of ten cues would be ten calls.
    config = AppConfig.model_validate({
        "translation": {"direction": "en>de"},
        "audit": {"semantic_enabled": True, "semantic_max_candidates_per_batch": 1},
    })
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / "sign.srt"
        source.write_text(SRT, encoding="utf-8")
        workspace = create_job_workspace(source, base / "runs", config, job_id="sign")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, _Subtitler(scene))
        auditor = Auditor(return_issue=False)
        run_translation_audit_stage(workspace, config, auditor)
        # Eight cues to a call: two calls for the ten, not ten.
        assert len(auditor.prompts) == 2
        assert sum(prompt.count("AUDIT HIGH RISK") for prompt in auditor.prompts) == 10
        prompt = auditor.prompts[0]
        assert "These segments are subtitle cues" in prompt and "Explain each issue in one sentence" in prompt
        assert "A subtitle is condensed on purpose" in prompt
        # Repair is told the same, and not to make a cue longer.
        document = load_preprocessed_documents(workspace)[0]
        translated = load_translated_documents(workspace)[0]
        finding = AuditIssue(
            segment_id="D0000-S000008", category=AuditCategory.MISTRANSLATION, severity=AuditSeverity.MEDIUM,
            message="The meaning differs.",
        )
        repair = build_repair_prompt(document, translated, "D0000-S000008", [finding], config)
        assert "These are the subtitles of a film" in repair and "must be no longer than the current one" in repair


def test_one_cue_the_audit_model_cannot_answer_for_does_not_cost_the_others_a_verdict():
    from book_agent.ollama_client import StructuredOutputError
    from tests.test_audit_stage import FakeAuditClient

    class Auditor(FakeAuditClient):
        """Goes round in circles whenever one particular cue is among those it is asked about."""

        def generate_structured(self, prompt, schema, **options):
            if "Allowed IDs:" not in prompt:
                raise StructuredOutputError("no numeric ruling")
            allowed = prompt.split("Allowed IDs: ", 1)[1].split("\n", 1)[0].split(", ")
            self.asked.append(allowed)
            if "D0000-S000008" in allowed:
                raise StructuredOutputError("the answer was cut off")
            return super().generate_structured(prompt, schema, **options)

    config = AppConfig.model_validate({
        "translation": {"direction": "en>de"},
        "audit": {"semantic_enabled": True},
        "workflow": {"max_retries": 1},
    })
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / "sign.srt"
        source.write_text(SRT, encoding="utf-8")
        workspace = create_job_workspace(source, base / "runs", config, job_id="sign")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, _Subtitler(scene))
        auditor = Auditor(return_issue=False)
        auditor.asked = []
        run_translation_audit_stage(workspace, config, auditor)
        issues = [issue for audit in load_document_audits(workspace) for issue in audit.issues]
        # Only the cue that fails on its own is left undecided; the other nine were audited.
        assert [issue.segment_id for issue in issues] == ["D0000-S000008"]
        assert "could not produce a complete structured decision" in issues[0].message
        assert ["D0000-S000008"] in auditor.asked
        assert sorted(cue for asked in auditor.asked if "D0000-S000008" not in asked for cue in asked) == [
            f"D0000-S{number:06d}" for number in (1, 2, 3, 4, 5, 6, 7, 9, 10)
        ]


def test_the_audit_of_subtitles_is_given_little_room_to_run_on_and_no_more_after_a_failure():
    from book_agent.ollama_client import StructuredOutputError
    from tests.test_audit_stage import FakeAuditClient

    class Auditor(FakeAuditClient):
        """Runs to the end of its allowance once, as a model repeating itself does, then answers."""

        def generate_structured(self, prompt, schema, **options):
            if "Allowed IDs:" not in prompt:
                raise StructuredOutputError("no numeric ruling")
            self.allowances.append(options.get("max_output_tokens"))
            if len(self.allowances) == 1:
                raise StructuredOutputError("the answer was cut off")
            return super().generate_structured(prompt, schema, **options)

    config = AppConfig.model_validate({
        "translation": {"direction": "en>de"},
        "audit": {"semantic_enabled": True, "semantic_max_output_tokens": 3072},
    })
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        source = base / "sign.srt"
        source.write_text(SRT, encoding="utf-8")
        workspace = create_job_workspace(source, base / "runs", config, job_id="sign")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        run_translation_stage(workspace, config, _Subtitler(scene))
        auditor = Auditor(return_issue=False)
        auditor.allowances = []
        run_translation_audit_stage(workspace, config, auditor)
        # The scene's ten cues in one call: 160 tokens a cue and a little over, not the 3,072 a
        # book's passage may take, and the same again after the failure, not twice as much.
        assert auditor.allowances == [1856, 1856]
        assert [issue for audit in load_document_audits(workspace) for issue in audit.issues] == []


def test_a_subtitle_job_is_refused_settings_written_for_a_books_prose():
    from book_agent.cli import build_dry_run_summary
    from book_agent.subtitles import book_only_problem, book_only_settings

    plain = AppConfig()
    story = AppConfig.model_validate({"consistency": {"story_context": {"enabled": True}, "style_sheet": {"enabled": True}}})
    assert book_only_settings("film.srt", plain) == [] and book_only_problem("film.srt", plain) == ""
    assert book_only_settings("book.epub", story) == []  # a book may have them
    assert book_only_settings("film.srt", story) == ["consistency.story_context.enabled", "consistency.style_sheet.enabled"]
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "film.srt"
        source.write_text(SRT, encoding="utf-8")
        assert build_dry_run_summary(source, plain, Path(directory) / "runs")["job_type"] == "subtitles"
        with pytest.raises(ValueError, match="are for a book's chapters and prose; set to false for a subtitle job"):
            build_dry_run_summary(source, story, Path(directory) / "runs")
