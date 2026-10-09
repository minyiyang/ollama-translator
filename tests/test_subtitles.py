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
    CONVERSION_NOTES,
    SUBTITLE_FORMATS,
    SubtitleError,
    conversion_notes,
    convert_subtitle_file,
    convert_subtitles,
    cue_passages,
    job_type,
    parse_subtitles,
    read_subtitles,
    reading_limits,
    render_subtitles,
    subtitle_kind,
    validate_converted_subtitles,
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


def test_a_job_set_to_other_reading_limits_is_audited_and_written_again_but_not_translated_again():
    from book_agent.stages.preprocess import limit_fields

    config = AppConfig.model_validate(CONFIG)
    narrow = AppConfig.model_validate({**CONFIG, "subtitles": {"line_characters": 12, "characters_per_second": 5}})
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]

    def first_cue(workspace) -> list[str]:
        return parse_subtitles(Path(load_compiled_epub_path(workspace)).read_text(encoding="utf-8"), "srt").cues[0].lines

    with tempfile.TemporaryDirectory() as directory:
        # A job that goes by the limits it was preprocessed with, whether its config set them or its
        # language gave them, is hashed as it was before the limits were hashed: nothing is done again.
        (Path(directory) / "narrow").mkdir()
        set_from_the_start = _translated(Path(directory) / "narrow", scene, narrow)
        assert limit_fields(set_from_the_start, narrow) == {}
        workspace = _translated(Path(directory), scene, config)
        assert limit_fields(workspace, config) == {} and limit_fields(workspace, narrow) == {"reading_limits": "12:2:5.0"}
        assert [issue for audit in load_document_audits(workspace) for issue in audit.issues] == []
        run_translation_repair_stage(workspace, config)
        verifier = FakeVerificationClient()
        run_repaired_review_stage(workspace, config, verifier)
        run_review_repair_stage(workspace, config, verifier)
        run_repaired_validation_stage(workspace, config, verifier)
        run_epub_compile_stage(workspace, config)
        assert first_cue(workspace) == ["Holmes nahm seine Flasche vom Kaminsims."]
        # Compiled for a narrower screen, the same translation is written again in shorter lines.
        run_epub_compile_stage(workspace, narrow)
        assert len(first_cue(workspace)) > 1 and all(len(line) <= 12 for line in first_cue(workspace))
        # The limits are the audit's and the compile's: nothing before them is done again.
        run_preprocessing_stage(workspace, narrow)
        translator = _Subtitler(scene)
        run_translation_stage(workspace, narrow, translator)
        assert translator.prompts == []
        # The audit goes by the new limits: the first cue's 34 characters are too many for its two and a half seconds.
        assert load_preprocessed_documents(workspace, narrow)[0].reading_limits == (12, 2, 5.0)
        run_translation_audit_stage(workspace, narrow)
        assert "D0000-S000001" in {issue.segment_id for audit in load_document_audits(workspace) for issue in audit.issues}


def test_a_books_config_and_documents_are_stored_as_before_there_were_subtitle_jobs():
    assert "subtitles" not in AppConfig().model_dump()
    assert AppConfig.model_validate({"subtitles": {"line_characters": 37}}).model_dump()["subtitles"]["line_characters"] == 37
    with pytest.raises(ValueError):
        AppConfig.model_validate({"subtitles": {"line_characters": 2}})


# The opening of "A Mad Tea-Party" (Alice's Adventures in Wonderland, chapter VII) cut into
# sixty cues by lang_benchmark/make_subtitles.py: speech, narration in italics, two-speaker cues.
TEA_PARTY = Path(__file__).parent / "data" / "alice-mad-tea-party.srt"


def test_a_scene_of_real_dialogue_is_read_and_written_back_unchanged():
    # As the file is on disk, line endings and all: a checkout on Windows gives it CRLF.
    text = TEA_PARTY.read_bytes().decode("utf-8")
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


def test_a_film_is_translated_a_few_lines_to_a_call_so_that_each_stays_under_its_own_cue():
    from book_agent.stages.translate import SUBTITLE_PASSAGES_PER_CALL

    class Echo:
        """Stands in for the translation model: answers every line it is asked for in German, and counts them."""

        def __init__(self):
            self.asked: list[int] = []
            self.lines: set[str] = set()

        def report_progress(self, event):
            pass

        def generate_text(self, prompt, **_):
            pieces = re.findall(r"<(D[A-Za-z0-9_-]+)>(.*?)</\1>", prompt.split("Source:\n", 1)[1], flags=re.DOTALL)
            self.asked.append(len(pieces))
            self.lines.update(item for item, _ in pieces)
            content = "\n".join(f"<{item}>Übersetzt.</{item}>" for item, _ in pieces)
            return GenerationResult(content=content, thinking="", metrics=GenerationMetrics(prompt_eval_count=100, eval_count=20))

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        workspace = create_job_workspace(TEA_PARTY, base / "runs", config, job_id="tea")
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, [])
        run_preprocessing_stage(workspace, config)
        model = Echo()
        run_translation_stage(workspace, config, model)
        passages = {segment.segment_id for document in load_preprocessed_documents(workspace) for segment in document.segments}
    # Sixty lines to a call, a model put one cue's translation under the next for lines at a time.
    assert max(model.asked) == SUBTITLE_PASSAGES_PER_CALL == 12
    assert model.lines == passages  # every line of the film, a dozen at a time at most


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


def test_a_subtitle_job_runs_with_a_books_config_the_prose_rewrite_switched_off():
    from book_agent.cli import build_dry_run_summary
    from book_agent.subtitles import subtitle_config

    # The benchmark configs are written for books: the style sheet on, and for English into Chinese the prose rewrite.
    book_config = AppConfig.model_validate({
        "reprose": {"enabled": True},
        "consistency": {"story_context": {"enabled": True}, "style_sheet": {"enabled": True}},
    })
    config, note = subtitle_config("film.vtt", book_config)
    assert not config.reprose.enabled and "reprose.enabled is switched off for this job" in note
    assert config.consistency.style_sheet.enabled and config.consistency.story_context.enabled  # left as they are
    assert book_config.reprose.enabled  # the config given is not changed
    assert subtitle_config("book.epub", book_config) == (book_config, "")
    assert subtitle_config("film.vtt", AppConfig()) == (AppConfig(), "")
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "film.srt"
        source.write_text(SRT, encoding="utf-8")
        summary = build_dry_run_summary(source, book_config, Path(directory) / "runs")
        assert summary["job_type"] == "subtitles" and summary["reprose_enabled"] is False and "switched off" in summary["note"]


# -- from one subtitle format to another (docs/OUTPUT_AND_CONFIG_UX.md, 3) --------------------------

# A scene with what each format says in its own way: italics, two speakers, and a cue placed on the screen.
PLACED_SRT = (
    "1\n00:00:01,000 --> 00:00:03,500\n<i>Which is it today,\nmorphine & cocaine?</i>\n\n"
    "2\n00:00:04,000 --> 00:00:06,000\n- Which is it?\n- Cocaine.\n\n"
    "3\n00:00:07,005 --> 00:00:08,000\n{\\an8}<b>BAKER STREET</b>\n\n"
    "4\n00:00:09,000 --> 00:00:10,000\n<font color=\"#ffff00\">Watson!</font>\n"
)
PLACED_VTT = (
    "WEBVTT - The Sign of the Four\n\nNOTE Translated from the English.\n\nSTYLE\n::cue { color: yellow }\n\n"
    "intro\n00:01.000 --> 00:03.500 line:90% align:center\n<i>Which is it today,\nmorphine &amp; cocaine?</i>\n\n"
    "00:00:04.000 --> 00:00:06.000\n- Which is it?\n- Cocaine.\n\n"
    "00:00:07.005 --> 00:00:08.000 position:10%\n<b>BAKER STREET</b>\n\n"
    "00:00:09.000 --> 00:00:10.000\n<v Holmes>Watson!\n"
)
PLACED_ASS = (
    "[Script Info]\nTitle: The Sign of the Four\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Sign,Georgia\n\n[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,not shown\n"
    "Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\i1}Which is it today,\\Nmorphine & cocaine?{\\i0}\n"
    "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,- Which is it?\\N- Cocaine.\n"
    "Dialogue: 1,0:00:04.00,0:00:08.00,Sign,,0,0,0,,{\\p1}m 0 0 l 100 0 100 40 0 40{\\p0}\n"
    "Dialogue: 1,0:00:07.00,0:00:08.00,Sign,,0,0,0,,{\\an8\\b1\\pos(192,30)\\c&H00FFFF&}BAKER STREET\n"
    "Dialogue: 0,0:00:09.00,0:00:10.00,Default,Holmes,0,0,0,,{\\k20}Wat{\\k30}son!\n"
)
PLACED = {"srt": PLACED_SRT, "vtt": PLACED_VTT, "ass": PLACED_ASS}
PLACED_LINES = [["Which is it today,", "morphine & cocaine?"], ["- Which is it?", "- Cocaine."], ["BAKER STREET"], ["Watson!"]]
# What each conversion says it did not carry over, or added: the table of the design.
NOTED = {
    ("srt", "vtt"): ["position", "markup"],
    ("srt", "ass"): ["times", "markup", "default_style"],
    ("vtt", "srt"): ["cue_settings", "blocks", "markup"],
    ("vtt", "ass"): ["cue_settings", "blocks", "times", "markup", "default_style"],
    ("ass", "srt"): ["styles", "drawings"],
    ("ass", "vtt"): ["styles", "drawings"],
}


@pytest.mark.parametrize("source, target", sorted(NOTED))
def test_a_subtitle_file_is_written_in_another_format_with_its_cues_times_and_lines(source, target):
    parsed = parse_subtitles(PLACED[source], source)
    text, notes = convert_subtitles(parsed, target)
    assert notes == NOTED[(source, target)] and set(notes) <= set(CONVERSION_NOTES)
    written = parse_subtitles(text, target)
    kept = [cue for cue in parsed.cues if "\\p1" not in cue.prefix]  # a drawing is not a line anyone says
    assert [cue.lines for cue in written.cues] == PLACED_LINES == [cue.lines for cue in kept]
    # ASS holds hundredths of a second: 7.005 is written as 7.01.
    slack = 5 if target == "ass" else 0
    for before, after in zip(kept, written.cues, strict=True):
        assert abs(before.start - after.start) <= slack and abs(before.end - after.end) <= slack
    # Italics and bold are said in the format's own way, around the whole cue.
    italic, bold = {"srt": ("<i>", "<b>"), "vtt": ("<i>", "<b>"), "ass": ("{\\i1}", "\\b1")}[target]
    assert written.cues[0].prefix == italic and bold in written.cues[2].prefix
    assert written.cues[1].speakers and (written.cues[1].prefix, written.cues[1].suffix) == ("", "")
    # Nothing of the other format's markup is left in the text.
    assert "font" not in text and "<v " not in text and "\\k" not in text and "\\pos" not in text


def test_each_format_is_written_as_players_read_it():
    parsed = parse_subtitles(PLACED_SRT, "srt")
    srt = convert_subtitles(parse_subtitles(PLACED_VTT, "vtt"), "srt")[0]
    assert srt.startswith("1\n00:00:01,000 --> 00:00:03,500\n<i>Which is it today,\nmorphine & cocaine?</i>\n\n2\n")
    assert "WEBVTT" not in srt and "NOTE" not in srt and "line:90%" not in srt
    vtt = convert_subtitles(parsed, "vtt")[0]
    assert vtt.startswith("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n<i>Which is it today,\nmorphine &amp; cocaine?</i>\n\n")
    ass = convert_subtitles(parsed, "ass")[0]
    assert "[Script Info]" in ass and "Style: Default," in ass and "\n[Events]\nFormat: Layer, Start, End, Style" in ass
    assert "Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\i1}Which is it today,\\Nmorphine & cocaine?{\\i0}\n" in ass
    # A position code SubRip borrowed from ASS is ASS's own again.
    assert "Dialogue: 0,0:00:07.01,0:00:08.00,Default,,0,0,0,,{\\b1\\an8}BAKER STREET{\\b0}\n" in ass
    with pytest.raises(SubtitleError, match="mobi is not a subtitle format"):
        convert_subtitles(parsed, "mobi")


def test_a_file_taken_to_another_format_and_back_has_its_cues_and_text():
    first = parse_subtitles(PLACED_SRT, "srt")
    for other in ("vtt", "ass"):
        there = convert_subtitles(first, other)[0]
        back = parse_subtitles(convert_subtitles(parse_subtitles(there, other), "srt")[0], "srt")
        assert [cue.lines for cue in back.cues] == [cue.lines for cue in first.cues]
        assert [cue.number for cue in back.cues] == [1, 2, 3, 4]
        if other == "vtt":
            assert [(cue.start, cue.end) for cue in back.cues] == [(cue.start, cue.end) for cue in first.cues]
    # A plain file loses nothing and says so.
    plain = parse_subtitles("1\n00:00:01,000 --> 00:00:02,000\nYes.\n", "srt")
    assert convert_subtitles(plain, "vtt")[1] == [] and convert_subtitles(plain, "ass")[1] == ["default_style"]


def test_a_converted_file_is_read_back_against_the_file_it_was_made_from():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        for source in SUBTITLE_FORMATS:
            compiled = folder / f"scene.{source}"
            compiled.write_text(PLACED[source], encoding="utf-8")
            assert conversion_notes(compiled, source) == []  # its own format: nothing to say
            for target in SUBTITLE_FORMATS:
                if target == source:
                    continue
                converted = folder / f"from-{source}.{target}"
                assert convert_subtitle_file(compiled, converted, target) == NOTED[(source, target)] == conversion_notes(compiled, target)
                assert subtitle_kind(converted) == target and validate_converted_subtitles(converted, compiled) == []
        compiled, converted = folder / "scene.srt", folder / "from-srt.vtt"
        text = converted.read_text(encoding="utf-8")
        converted.write_text(text.replace("BAKER STREET", "BAKER ROAD").replace("00:00:04.000", "00:00:04.200"), encoding="utf-8")
        assert validate_converted_subtitles(converted, compiled) == [
            "cue 2: its time changed in the vtt file", "cue 3: its text changed in the vtt file",
        ]
        converted.write_text(text.split("\n\n00:00:09")[0] + "\n", encoding="utf-8")
        assert validate_converted_subtitles(converted, compiled) == ["the vtt file has 3 cues; the file it was made from has 4"]
        converted.write_text("WEBVTT\n", encoding="utf-8")
        assert "could not be read back" in validate_converted_subtitles(converted, compiled)[0]
    assert subtitle_kind("film.SSA") == "ass" and subtitle_kind("book.epub") == ""


def _compiled(base: Path, config: AppConfig):
    """The scene's job taken to the end of the pipeline."""
    scene = [cue if number != 8 else (*cue[:3], ["Mein Geist rebelliert."]) for number, cue in enumerate(SCENE, start=1)]
    workspace = _translated(base, scene, config)
    run_translation_repair_stage(workspace, config)
    verifier = FakeVerificationClient()
    run_repaired_review_stage(workspace, config, verifier)
    run_review_repair_stage(workspace, config, verifier)
    run_repaired_validation_stage(workspace, config, verifier)
    run_epub_compile_stage(workspace, config)
    return workspace


def test_a_subtitle_job_gives_back_the_format_its_config_names_checked_like_the_other():
    from book_agent.stages.compile import load_output_path
    from book_agent.state import connect_state, get_stage_status

    def hash_of(workspace, stage):
        connection = connect_state(workspace.state_file)
        try:
            return get_stage_status(connection, stage)["input_hash"]
        finally:
            connection.close()

    plain = AppConfig.model_validate(CONFIG)
    as_vtt = AppConfig.model_validate({**CONFIG, "output": {"format": "vtt"}})
    with tempfile.TemporaryDirectory() as directory:
        workspace = _compiled(Path(directory), as_vtt)
        compiled, given = Path(load_compiled_epub_path(workspace)), Path(load_output_path(workspace))
        # The file of the kind that came in is still written, and is what the other is made from.
        assert (compiled.suffix, given.suffix) == (".srt", ".vtt") and given.stem == compiled.stem
        assert sorted(path.suffix for path in given.parent.iterdir()) == [".srt", ".vtt"]
        assert run_epub_validation_stage(workspace).passed
        written = parse_subtitles(given.read_text(encoding="utf-8"), "vtt")
        assert written.cues[0].lines == ["Holmes nahm seine Flasche vom Kaminsims."] and written.cues[0].prefix == "<i>"
        assert [(cue.start, cue.end) for cue in written.cues] == [(cue.start, cue.end) for cue in parse_subtitles(SRT, "srt").cues]

        # A converted file that lost a cue fails the check, not only the file it was made from.
        vtt_hash, translated = hash_of(workspace, "compile"), hash_of(workspace, "translate")
        given.write_text(given.read_text(encoding="utf-8").split("\n\n00:00:38")[0] + "\n", encoding="utf-8")
        from book_agent.pipeline_state import invalidate_stage_and_dependents
        from book_agent.workflow import WorkflowStage

        connection = connect_state(workspace.state_file)
        try:
            invalidate_stage_and_dependents(connection, WorkflowStage.VALIDATE_EPUB)
        finally:
            connection.close()
        with pytest.raises(Exception, match="the vtt file has 9 cues; the file it was made from has 10"):
            run_epub_validation_stage(workspace)

        # Naming the format it came in, or none, is the job it always was: compiled again, not translated again.
        run_epub_compile_stage(workspace, plain)
        assert Path(load_output_path(workspace)).suffix == ".srt" and run_epub_validation_stage(workspace).passed
        assert hash_of(workspace, "compile") != vtt_hash and hash_of(workspace, "translate") == translated
        srt_hash = hash_of(workspace, "compile")
        run_epub_compile_stage(workspace, AppConfig.model_validate({**CONFIG, "output": {"format": "srt"}}))
        assert hash_of(workspace, "compile") == srt_hash


def test_the_dashboard_offers_a_subtitle_job_the_subtitle_formats_and_says_what_each_loses():
    from book_agent.state import StageStatus, connect_state, set_stage_status
    from book_agent.web.messages import UserError
    from book_agent.web.server import UiApp
    from book_agent.workflow import workflow_status

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _compiled(Path(directory), config)
        app = UiApp(workspace.root.parent, Path(directory), [])
        info = app.job_info("sign")
        assert (info["job_type"], info["output_format"], info["output_formats"]) == ("subtitles", "srt", ["srt", "vtt", "ass"])
        # Not finished: the Jobs list has no formats to offer yet.
        assert "output_formats" not in app.setup()["jobs"][0]
        assert info["output_notes"] == {"vtt": ["position"], "ass": ["default_style"]}
        with pytest.raises(UserError, match="has not completed"):
            app.job_output("sign", "vtt")
        run_epub_validation_stage(workspace)
        connection = connect_state(workspace.state_file)
        try:
            for stage in workflow_status(workspace)["stages"]:
                if stage["status"] != StageStatus.COMPLETED.value:
                    set_stage_status(connection, stage["name"], StageStatus.COMPLETED)
        finally:
            connection.close()

        # The Jobs list offers a finished job what its own page does.
        [row] = app.setup()["jobs"]
        assert row["downloadable"] and (row["output_format"], row["output_formats"]) == ("srt", ["srt", "vtt", "ass"])
        assert row["output_notes"] == {"vtt": ["position"], "ass": ["default_style"]}

        own, name = app.job_output("sign")
        assert name.endswith(".srt") and app.job_output("sign", "srt") == (own, name)
        data, converted = app.job_output("sign", "vtt")
        assert converted == name.removesuffix(".srt") + ".vtt" and data.decode("utf-8").startswith("WEBVTT\n\n00:00:01.000 --> ")
        assert "Holmes nahm seine Flasche vom Kaminsims." in data.decode("utf-8")
        data, converted = app.job_output("sign", "ass")
        assert converted.endswith(".ass") and b"Dialogue: 0,0:00:01.00,0:00:03.50,Default" in data
        # No book format, the EPUB among them.
        for kind in ("epub", "pdf", "docx", "txt", "mobi"):
            with pytest.raises(UserError, match=f"a subtitle job is written as SRT, WebVTT, or ASS, not as {kind}"):
                app.job_output("sign", kind)


def test_a_subtitle_file_in_the_older_ssa_form_keeps_its_name_and_is_offered_the_other_two():
    from book_agent.output import hashed_output_fields, output_format
    from book_agent.web.server import UiApp

    config = AppConfig.model_validate(CONFIG)
    assert output_format("film.ssa", config) == ("ssa", "")
    assert output_format("film.ssa", AppConfig.model_validate({**CONFIG, "output": {"format": "ass"}})) == ("ssa", "")
    assert output_format("film.ssa", AppConfig.model_validate({**CONFIG, "output": {"format": "srt"}})) == ("srt", "")
    assert hashed_output_fields("film.ssa", AppConfig.model_validate({**CONFIG, "output": {"format": "ass"}})) == {}
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "sign.ssa"
        source.write_text(ASS, encoding="utf-8")
        workspace = create_job_workspace(source, Path(directory) / "runs", config, job_id="sign")
        info = UiApp(workspace.root.parent, Path(directory), [])._output_formats(workspace)
        assert info == {"output_format": "ssa", "output_formats": ["ssa", "srt", "vtt"], "output_notes": {"srt": ["styles"], "vtt": ["styles"]}}


def test_a_subtitle_jobs_pages_leave_out_the_stage_that_only_a_book_has_work_for():
    from book_agent.pipeline_state import WorkflowStage
    from book_agent.web.estimate import estimate
    from book_agent.web.jobs import list_jobs, shown_stages
    from book_agent.web.rerun import rerun_preview
    from book_agent.web.server import UiApp
    from book_agent.web.setup import validate_setup
    from book_agent.workflow import workflow_status

    names = [stage.value for stage in WorkflowStage]
    assert shown_stages("book.epub", names) == names
    assert shown_stages("film.srt", names) == [name for name in names if name != "translate_title"]
    assert shown_stages("film.vtt", [{"name": "translate_title"}, {"name": "compile"}]) == [{"name": "compile"}]

    config = AppConfig.model_validate(CONFIG)
    with tempfile.TemporaryDirectory() as directory:
        workspace = _compiled(Path(directory), config)
        # The stage is still one of the job's own: it runs, and passes at once.
        assert "translate_title" in [stage["name"] for stage in workflow_status(workspace)["stages"]]
        app = UiApp(workspace.root.parent, Path(directory), [])
        shown = [stage["name"] for stage in app.job_info("sign")["stages"]]
        assert "translate_title" not in shown and shown[-2:] == ["compile", "validate_epub"]
        [row] = list_jobs(app.runs)
        assert row["total"] == len(names) - 1 == len(shown)
        from book_agent.web.jobs import ProgressReader

        assert [stage["name"] for stage in ProgressReader().snapshot(workspace)["status"]["stages"]] == shown
        assert "translate_title" not in [stage["name"] for stage in rerun_preview(workspace, "repair_translation")["stages"]]
        assert "translate_title" not in estimate(workspace, app.runs, config)["unknown_stages"]
        check = validate_setup("translation:\n  direction: en>de\n", Path(directory), Path(directory) / "elsewhere", str(workspace.source_file), "")
        assert "translate_title" not in check["stages"] and "compile" in check["stages"]


# Text a format would read as markup: reviewed on PR #10.
LITERAL_SRT = "1\n00:00:01,000 --> 00:00:02,000\n<i>Look in C:\\new\\home\\notes</i>\n"
LITERAL_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>&lt;door closes&gt; R&amp;D</i>\n\n00:00:03.000 --> 00:00:04.000\nYes.\n"


@pytest.mark.parametrize(
    "text, kind, lines",
    [
        pytest.param(LITERAL_SRT, "srt", ["Look in C:\\new\\home\\notes"], id="backslashes"),
        pytest.param(LITERAL_VTT, "vtt", ["<door closes> R&D"], id="angle-brackets"),
    ],
)
def test_text_that_a_format_would_read_as_markup_stays_text_in_every_format(text, kind, lines):
    parsed = parse_subtitles(text, kind)
    assert parsed.cues[0].lines == lines
    with tempfile.TemporaryDirectory() as directory:
        compiled = Path(directory) / f"scene.{kind}"
        compiled.write_text(text, encoding="utf-8")
        for target in SUBTITLE_FORMATS:
            written, _ = convert_subtitles(parsed, target)
            back = parse_subtitles(written, target).cues[0]
            # One line still, with every character of it, and the italics around it.
            assert back.lines == lines, target
            assert back.prefix in {"<i>", "{\\i1}"}
            if target != kind:
                converted = Path(directory) / f"converted.{target}"
                convert_subtitle_file(compiled, converted, target)
                assert validate_converted_subtitles(converted, compiled) == []


def test_what_stands_between_a_marked_character_and_the_text_is_not_seen_and_is_taken_out_again():
    ass = convert_subtitles(parse_subtitles(LITERAL_SRT, "srt"), "ass")[0]
    # A word joiner after the backslash: a player shows \n and \h as they are written, not as a line break and a space.
    assert "{\\i1}Look in C:\\\u2060new\\\u2060home\\\u2060notes{\\i0}" in ass
    srt = convert_subtitles(parse_subtitles(LITERAL_VTT, "vtt"), "srt")[0]
    assert "<i><\u2060door closes> R&D</i>" in srt
    # WebVTT has its own way to say it, and uses that.
    vtt = convert_subtitles(parse_subtitles(srt, "srt"), "vtt")[0]
    assert "<i>&lt;door closes&gt; R&amp;D</i>" in vtt and "\u2060" not in vtt
    # A backslash that begins no code needs nothing.
    plain = parse_subtitles("1\n00:00:01,000 --> 00:00:02,000\nD:\\films\\sign\n", "srt")
    assert "\u2060" not in convert_subtitles(plain, "ass")[0]


def test_a_translation_with_such_text_is_written_into_the_file_of_the_kind_that_came_in_as_text():
    limits = reading_limits("en")
    for kind, source, text, written_as in (
        ("srt", "1\n00:00:01,000 --> 00:00:02,000\n<i>Sieh nach</i>\n", "<door closes> {aside}", "<i><\u2060door closes> {\u2060aside}</i>"),
        ("ass", ASS, "C:\\new", "C:\\\u2060new"),
    ):
        parsed = parse_subtitles(source, kind)
        written = render_subtitles(parsed, {1: [text]}, limits)
        assert written_as in written
        assert parse_subtitles(written, kind).cues[0].lines == [text]
