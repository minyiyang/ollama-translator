import contextlib
import io
import json
import tempfile
from pathlib import Path

from book_agent.cli import main
from book_agent.config import AppConfig
from book_agent.consistency_report import (
    ReportSegment,
    _is_mixed,
    build_report,
    character_usage,
    conventions,
    format_report,
    glossary_compliance,
    repeated_lines,
    repeated_sentences,
    visible_text,
)
from book_agent.schemas import GlossaryCategory, GlossaryEntry


def _segment(segment_id: str, source: str, target: str, *, edited: bool = False) -> ReportSegment:
    return ReportSegment(
        document_id=segment_id.split("-")[0],
        order=int(segment_id[1:5]),
        segment_id=segment_id,
        source=source,
        target=target,
        edited=edited,
    )


def _entry(english: str, chinese: str, category=GlossaryCategory.PERSON, aliases=()) -> GlossaryEntry:
    return GlossaryEntry(english=english, chinese=chinese, category=category, aliases=list(aliases))


def test_visible_text_drops_markers_and_collapses_whitespace():
    assert visible_text("<I000>Hello</I000>   world\n") == "Hello world"


class RepeatedLinesTests:
    def test_groups_repeats_and_counts_distinct_renderings(self):
        segments = [
            _segment("D0001-S000001", "Off with her head!", "砍掉她的头！"),
            _segment("D0002-S000001", "<I000>Off with her head!</I000>", "砍掉 她的头！"),
            _segment("D0003-S000001", "Off with her head!", "砍了她的脑袋！", edited=True),
            _segment("D0003-S000002", "Something else entirely.", "完全不同。"),
        ]
        report = repeated_lines(segments, min_chars=12)
        assert report["repeated_count"] == 1
        assert report["inconsistent_count"] == 1
        group = report["groups"][0]
        # Markers and spaces are ignored, so the first two renderings are the same.
        assert group["occurrences"] == 3 and group["variant_count"] == 2
        assert [r["edited"] for r in group["renderings"]] == [False, False, True]

    def test_ignores_short_lines_and_lists_consistent_repeats_last(self):
        segments = [
            _segment("D0001-S000001", "Yes.", "是。"),
            _segment("D0001-S000002", "Yes.", "对。"),
            _segment("D0001-S000003", "Consider your verdict", "考虑你们的裁决"),
            _segment("D0002-S000001", "Consider your verdict", "考虑你们的裁决"),
            _segment("D0002-S000002", "Give your evidence now", "提供证据"),
            _segment("D0002-S000003", "Give your evidence now", "拿出你的证据"),
        ]
        report = repeated_lines(segments, min_chars=12)
        assert report["repeated_count"] == 2
        assert [g["source"] for g in report["groups"]] == ["Give your evidence now", "Consider your verdict"]


def test_repeated_sentences_skip_speech_tags_and_symbol_rows():
    segments = [
        _segment("D0001-S000001", "“Off with her head!” the Queen shouted.", "x"),
        _segment("D0002-S000001", "“Off with her head!” said the Queen.", "x"),
        _segment("D0002-S000002", "* * * * * * * * *", "x"),
        _segment("D0003-S000001", "* * * * * * * * *", "x"),
        _segment("D0003-S000002", "“Off with her head! Off with her head!”", "x"),
    ]
    report = repeated_sentences(segments, min_chars=12)
    assert [item["sentence"] for item in report["sentences"]] == ["Off with her head!"]
    # Counted once per segment, even when a segment repeats it.
    assert report["sentences"][0]["segments"] == 3


class GlossaryComplianceTests:
    def test_reports_occurrences_without_the_approved_rendering(self):
        entries = [_entry("Hatter", "帽匠")]
        segments = [
            _segment("D0001-S000001", "The Hatter spoke.", "帽匠说话了。"),
            _segment("D0001-S000002", "The Hatter sighed.", "制帽人叹了口气。"),
        ]
        report = glossary_compliance(segments, entries)
        assert report["entries_seen"] == 1 and report["miss_count"] == 1
        assert report["entries"][0]["examples"][0]["segment_id"] == "D0001-S000002"

    def test_a_term_inside_a_longer_matched_term_is_not_checked_on_its_own(self):
        entries = [_entry("Hatter", "帽匠"), _entry("Mad Hatter", "疯帽子")]
        segments = [_segment("D0001-S000001", "The Mad Hatter spoke.", "疯帽子说话了。")]
        assert glossary_compliance(segments, entries)["miss_count"] == 0

    def test_a_chinese_alias_counts_as_the_rendering(self):
        entries = [_entry("Knave of Hearts", "红心侍从", aliases=["红桃侍从"])]
        segments = [_segment("D0001-S000001", "The Knave of Hearts bowed.", "红桃侍从鞠了一躬。")]
        assert glossary_compliance(segments, entries)["miss_count"] == 0

    def test_sentence_initial_common_words_are_not_the_named_term(self):
        entries = [_entry("Two", "二号")]
        segments = [
            _segment("D0001-S000001", "Two days wrong! It took two hours.", "错了两天！花了两个小时。"),
            _segment("D0001-S000002", "Then Two began, in a low voice.", "接着二号低声开口。"),
            _segment("D0001-S000003", "said Two, looking up.", "两个人抬头说。"),
        ]
        report = glossary_compliance(segments, entries)
        assert report["entries"][0]["occurrences"] == 2
        assert [e["segment_id"] for e in report["entries"][0]["examples"]] == ["D0001-S000003"]

    def test_chinese_to_english_checks_the_english_rendering(self):
        entries = [_entry("Hatter", "帽匠")]
        segments = [
            _segment("D0001-S000001", "帽匠说话了。", "The hatter spoke."),
            _segment("D0001-S000002", "帽匠叹气。", "The capmaker sighed."),
        ]
        report = glossary_compliance(segments, entries, source_language="zh", target_language="en")
        assert report["miss_count"] == 1


class CharacterUsageTests:
    def test_counts_pronouns_only_where_one_character_is_named(self):
        entries = [_entry("Duchess", "公爵夫人"), _entry("Alice", "爱丽丝")]
        segments = [
            _segment("D0001-S000001", "The Duchess sneezed; she was cross.", "公爵夫人打了个喷嚏，她很生气。"),
            _segment("D0001-S000002", "The Duchess and Alice: he said so.", "公爵夫人和爱丽丝：他这么说。"),
        ]
        rows = {row["character"]: row for row in character_usage(segments, entries)["characters"]}
        assert rows["Duchess"]["pronouns"] == {"she": 1}
        assert rows["Duchess"]["source_pronouns"] == {"she": 1}
        assert rows["Duchess"]["segments"] == 2

    def test_mixed_means_he_and_she_not_it(self):
        entries = [_entry("Hatter", "帽匠"), _entry("Queen", "王后")]
        segments = [
            *[_segment(f"D0001-S00000{i}", "x", "帽匠看着它。他笑了。") for i in range(1, 6)],
            *[_segment(f"D0002-S00000{i}", "x", "王后说她要走。") for i in range(1, 4)],
            *[_segment(f"D0002-S00000{i}", "x", "王后说他要走。") for i in range(4, 6)],
        ]
        report = character_usage(segments, entries)
        rows = {row["character"]: row for row in report["characters"]}
        assert rows["Hatter"]["pronouns"] == {"it": 5, "he": 5} and not rows["Hatter"]["pronouns_mixed"]
        assert rows["Queen"]["pronouns_mixed"]
        assert report["pronouns_mixed"] == ["Queen"]

    def test_entries_sharing_a_rendering_are_one_character(self):
        entries = [_entry("Queen", "王后"), _entry("The Queen", "王后"), _entry("Queen of Hearts", "红心王后")]
        segments = [
            _segment("D0001-S000001", "x", "王后来了。"),
            _segment("D0001-S000002", "x", "红心王后来了。"),
        ]
        rows = {row["character"]: row["segments"] for row in character_usage(segments, entries)["characters"]}
        # 王后 inside 红心王后 is the longer name, not a second mention.
        assert rows == {"Queen / The Queen": 1, "Queen of Hearts": 1}

    def test_lists_every_quoted_formal_you(self):
        entries = [_entry("Alice", "爱丽丝")]
        segments = [
            _segment("D0001-S000001", "x", "“您喜欢狗吗？”爱丽丝问。"),
            _segment("D0001-S000002", "x", "“你喜欢猫吗？”爱丽丝问。"),
            _segment("D0001-S000003", "x", "爱丽丝想起您这个字。"),  # not quoted speech
        ]
        report = character_usage(segments, entries)
        assert [item["segment_id"] for item in report["quoted_formal_segments"]] == ["D0001-S000001"]
        assert report["quoted_informal_segments"] == 1
        assert report["characters"][0]["address"] == {"您": 1, "你": 1}
        assert report["address_mixed"] == ["Alice"]


def test_conventions_count_styles_and_treat_nested_quotes_as_normal():
    segments = [
        _segment("D0001-S000001", "x", "“他说：‘好。’”——然后……"),
        _segment("D0001-S000002", "x", "他唱道：—"),
        _segment("D0001-S000003", "x", '他说"好"，共3次。'),
        _segment("D0001-S000004", "x", "English only, no CJK — ignored."),
    ]
    report = conventions(segments)
    assert report["quotation marks"]["segments_by_style"] == {"“ ” curly double": 1, '" straight ASCII': 1}
    assert report["quotation marks"]["mixed"]
    assert report["nested quotation marks"] == {
        "segments_by_style": {"‘ ’ curly single": 1},
        "mixed": False,
        "examples": {"‘ ’ curly single": ["D0001-S000001"]},
    }
    assert report["dash"]["segments_by_style"] == {"—— paired": 1, "— single": 1}
    assert report["ellipsis"]["segments_by_style"] == {"…… paired": 1}
    assert report["numerals"]["segments_by_style"] == {"Arabic digits": 1}


def test_is_mixed_needs_enough_uses_and_a_real_minority():
    assert not _is_mixed({"he": 3, "she": 1})  # too few
    assert not _is_mixed({"he": 19, "she": 1})  # 5%
    assert _is_mixed({"he": 8, "she": 2})  # 20%


def test_chinese_to_english_skips_the_chinese_only_sections():
    report = build_report([], [], source_language="zh", target_language="en")
    assert report["characters"] is None and report["conventions"] is None


# -- the command, on a real (fixture) job ---------------------------------------------------


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


class ConsistencyReportCommandTests:
    def _workspace(self, directory):
        from tests.test_compile_stages import CompileStageTests

        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        return CompileStageTests().prepare_workspace(Path(directory), config)

    def test_reads_the_edited_text_and_leaves_the_job_folder_untouched(self):
        from book_agent.hashing import sha256_text
        from book_agent.stages.validate_repaired import load_validated_repaired_documents
        from book_agent.text_edits import apply_edit

        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            segment = next(
                s for s in load_validated_repaired_documents(workspace)[0].document.segments
                if s.source_text == "Chapter One"
            )
            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="第一章",
                reason="Testing the consistency report.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            before = _snapshot(workspace.root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["consistency-report", str(workspace.root), "--json"])
            assert result == 0
            assert _snapshot(workspace.root) == before
            report = json.loads(output.getvalue())
            assert report["job_id"] == "fixture"
            assert report["text_basis"] == "validated draft with 1 active manual edit(s) applied"
            assert report["edited_segment_count"] == 1
            assert report["direction"] == "en-zh"

    def test_markdown_to_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self._workspace(directory)
            target = Path(directory) / "report.md"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["consistency-report", str(workspace.root), "--output", str(target)])
            assert result == 0
            assert f"Wrote {target}" in output.getvalue()
            text = target.read_text(encoding="utf-8")
            assert text.startswith("# Consistency report: fixture")
            for heading in (
                "## Repeated source lines",
                "## Repeated sentences inside segments (source only)",
                "## Glossary compliance, book-wide",
                "## Recurring names without a glossary entry",
                "## Pronouns and forms of address per character",
                "## Punctuation and numeral conventions",
            ):
                assert heading in text


def test_format_report_limits_rows_and_says_how_many_more():
    segments = [
        _segment(f"D{i:04d}-S000001", "The very same repeated line.", f"译文{i}") for i in range(1, 4)
    ] + [
        _segment(f"D{i:04d}-S000002", f"Another repeated line number {i % 2}.", f"另一{i}") for i in range(1, 5)
    ]
    report = build_report(segments, [], source_language="en", target_language="zh")
    report.update(job_id="demo", text_basis="test")
    text = format_report(report, limit=1)
    # Three repeated lines ("…number 0." and "…number 1." twice each), all rendered two ways.
    assert "**3** are rendered in more than one way" in text
    assert "…and 2 more (use --json for all)." in text
