import json
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from book_agent.config import AppConfig
from book_agent.hashing import sha256_text
from book_agent.languages import TranslationDirection
from book_agent.stages.validate_repaired import load_validated_repaired_documents
from book_agent.state import connect_state, get_job_metadata
from book_agent.text_edits import StaleEditError, apply_edit, history_for_segment, load_events, segment_status
from book_agent.xliff_export import CORE_NAMESPACE, LEGACY_CORE_NAMESPACE, METADATA_NAMESPACE, export_xliff
from book_agent.xliff_import import (
    ImportOptions,
    XliffImportError,
    apply_import,
    cancel_import,
    classify,
    dismiss_import,
    import_report_csv,
    import_state,
    parse_xliff,
    start_import,
    will_import,
)
from tests.test_compile_stages import _retarget_pipeline_text, prepare_workspace

EN_ZH = TranslationDirection.EN_TO_ZH
BOOK = "b" * 64
ET.register_namespace("", CORE_NAMESPACE)
ET.register_namespace("mda", METADATA_NAMESPACE)
NS = {"x": CORE_NAMESPACE, "mda": METADATA_NAMESPACE}

# Fixture book segments (tests/epub_fixture.py): S1 "Chapter One",
# S2 "Hello <I000>small</I000> world.", S3 "Nested paragraph.", S4 "Plain item."
S1, S2, S3, S4 = "D0000-S000001", "D0000-S000002", "D0000-S000003", "D0000-S000004"


def _document(body: str, *, src="en", trg="zh", namespace=CORE_NAMESPACE, book=BOOK) -> str:
    metadata = (
        f'<mda:metadata><mda:metaGroup category="book-agent">'
        f'<mda:meta type="source_sha256">{book}</mda:meta></mda:metaGroup></mda:metadata>'
        if book
        else ""
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<xliff xmlns="{namespace}" xmlns:mda="{METADATA_NAMESPACE}" version="2.1" '
        f'srcLang="{src}" trgLang="{trg}"><file id="f">{metadata}{body}</file></xliff>'
    )


def _unit(unit_id: str, source: str, target: str | None = "译文") -> str:
    target_xml = "" if target is None else f"<target>{target}</target>"
    return f'<unit id="{unit_id}"><segment><source>{source}</source>{target_xml}</segment></unit>'


def _workspace(directory):
    config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
    return prepare_workspace(Path(directory), config)


def _book_hash(workspace) -> str:
    connection = connect_state(workspace.state_file)
    try:
        return get_job_metadata(connection, "source_sha256")
    finally:
        connection.close()


def _pipeline_text(workspace, segment_id: str) -> str:
    return next(
        segment.translated_text
        for repaired in load_validated_repaired_documents(workspace)
        for segment in repaired.document.segments
        if segment.segment_id == segment_id
    )


def _find(root, unit_id):
    return next(u for u in root.iterfind(".//x:unit", NS) if u.get("id") == unit_id)


def _set_inline(element, inner: str) -> None:
    fragment = ET.fromstring(f'<t xmlns="{CORE_NAMESPACE}">{inner}</t>')
    element.clear()
    element.text = fragment.text
    element.extend(list(fragment))


def _edit_export(xml: str, targets: dict[str, str] | None = None, sources: dict[str, str] | None = None) -> str:
    """Change some units' targets or sources the way a CAT tool would."""
    root = ET.fromstring(xml)
    for unit_id, inner in (targets or {}).items():
        _set_inline(_find(root, unit_id).find("x:segment/x:target", NS), inner)
    for unit_id, inner in (sources or {}).items():
        _set_inline(_find(root, unit_id).find("x:segment/x:source", NS), inner)
    return ET.tostring(root, encoding="unicode")


def _by_segment(items):
    return {item["unit_id"]: item for item in items}


# -- reading the file ------------------------------------------------------------


class ParseXliffTests:
    def test_reads_units_with_markers_groups_split_segments_and_annotations(self):
        body = (
            '<group id="g">'
            '<unit id="U1"><segment><source>Hello <pc id="000">small</pc> world.</source>'
            '<target>你好<pc id="000">小</pc>世界。</target></segment></unit>'
            "</group>"
            '<unit id="U2">'
            "<segment><source>One.</source><target>一。</target></segment>"
            "<ignorable><source> </source></ignorable>"
            "<segment><source>Two.</source><target>二<mrk id=\"m\" type=\"comment\">。</mrk></target></segment>"
            "</unit>"
            '<unit id="U3"><segment><source>Tab</source><target>制<cp hex="0009"/>表</target></segment></unit>'
        )
        units, warnings = parse_xliff(_document(body), direction=EN_ZH, source_sha256=BOOK)
        assert warnings == []
        by_id = {unit.unit_id: unit for unit in units}
        assert by_id["U1"].source == "Hello <I000>small</I000> world."
        assert by_id["U1"].target == "你好<I000>小</I000>世界。"
        assert by_id["U2"].source == "One. Two."
        assert by_id["U2"].target == "一。 二。"
        assert by_id["U3"].target == "制\t表"
        assert by_id["U1"].base_target_sha256 is None  # no unit metadata

    def test_reads_unit_export_metadata(self):
        body = (
            '<unit id="U1"><mda:metadata><mda:metaGroup category="book-agent">'
            '<mda:meta type="base_target_sha256">abc</mda:meta>'
            '<mda:meta type="edit_revision">E000007</mda:meta>'
            "</mda:metaGroup></mda:metadata>"
            "<segment><source>A</source><target>甲</target></segment></unit>"
            '<unit id="U2"><mda:metadata><mda:metaGroup category="book-agent">'
            '<mda:meta type="base_target_sha256">def</mda:meta>'
            "</mda:metaGroup></mda:metadata>"
            "<segment><source>B</source><target>乙</target></segment></unit>"
        )
        units, _ = parse_xliff(_document(body), direction=EN_ZH, source_sha256=BOOK)
        assert (units[0].base_target_sha256, units[0].edit_revision) == ("abc", "E000007")
        assert (units[1].base_target_sha256, units[1].edit_revision) == ("def", "")

    def test_flags_unsupported_markup_and_missing_targets_per_unit(self):
        body = (
            _unit("U1", "A", '甲<ph id="1"/>')
            + _unit("U2", "B", '<pc id="x9">乙</pc>')
            + _unit("U3", "C", None)
        )
        units, _ = parse_xliff(_document(body), direction=EN_ZH, source_sha256=BOOK)
        by_id = {unit.unit_id: unit for unit in units}
        assert by_id["U1"].markup_error == "<ph>"
        assert by_id["U2"].markup_error == '<pc id="x9">'
        assert by_id["U3"].target is None and by_id["U3"].markup_error == ""

    def test_accepts_region_subtags_and_the_pre_fix_namespace(self):
        document = _document(_unit("U1", "A"), src="en-US", trg="zh-CN", namespace=LEGACY_CORE_NAMESPACE)
        units, _ = parse_xliff(document, direction=EN_ZH, source_sha256=BOOK)
        assert [unit.unit_id for unit in units] == ["U1"]

    def test_warns_when_the_file_has_no_export_metadata(self):
        _, warnings = parse_xliff(_document(_unit("U1", "A"), book=""), direction=EN_ZH, source_sha256=BOOK)
        assert warnings and "stale" in warnings[0]

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', "DOCTYPE"),
            ("<xliff", "not well-formed"),
            ('<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2"/>', "XLIFF 1.2"),
            ("<html/>", "not an XLIFF 2 document"),
            (_document(_unit("U1", "A"), src="fr"), "this job translates en → zh"),
            (_document(_unit("U1", "A"), book="c" * 64), "different book"),
            (_document(_unit("U1", "A") + _unit("U1", "B")), "appears more than once"),
            (_document(""), "no translation units"),
        ],
    )
    def test_refuses_files_that_cannot_be_imported(self, document, message):
        with pytest.raises(XliffImportError, match=message):
            parse_xliff(document, direction=EN_ZH, source_sha256=BOOK)

    def test_a_file_from_another_book_names_the_job_it_came_from(self):
        document = _document(_unit("U1", "A"), book="c" * 64).replace(
            "</mda:metaGroup>", '<mda:meta type="job_id">robinson-crusoe</mda:meta></mda:metaGroup>'
        )
        with pytest.raises(XliffImportError, match=r"different book \(job robinson-crusoe\); import it into that job"):
            parse_xliff(document, direction=EN_ZH, source_sha256=BOOK)


# -- classifying against the book ------------------------------------------------------


class ClassifyTests:
    def classify_file(self, workspace, xml):
        units, _ = parse_xliff(xml, direction=EN_ZH, source_sha256=_book_hash(workspace))
        return _by_segment(classify(workspace, units))

    def test_an_untouched_export_is_entirely_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            items = self.classify_file(workspace, export_xliff(workspace))
            assert {item["category"] for item in items.values()} == {"unchanged"}

    def test_decides_each_category(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(
                export_xliff(workspace),
                targets={S1: "第一章", S3: "Nested paragraph, untranslated."},
                sources={S4: "A different source item."},
            )
            root = ET.fromstring(xml)
            file = root.find("x:file", NS)
            file.append(ET.fromstring(
                f'<unit xmlns="{CORE_NAMESPACE}" id="D0009-S000001"><segment><source>X</source>'
                f"<target>某</target></segment></unit>"
            ))
            _find(root, S2).find("x:segment", NS).remove(_find(root, S2).find("x:segment/x:target", NS))
            items = self.classify_file(workspace, ET.tostring(root, encoding="unicode"))

            assert items[S1]["category"] == "import"
            assert items[S1]["imported_text"] == "第一章"
            assert items[S2]["category"] == "no_target"
            assert items[S3]["category"] == "fails_checks"
            assert "untranslated" in {finding["category"] for finding in items[S3]["hard"]}
            assert items[S4]["category"] == "source_differs"
            assert items[S4]["imported_source"] == "A different source item."
            assert items["D0009-S000001"]["category"] == "unknown_id"
            assert items["D0009-S000001"]["segment_id"] is None

    def test_whitespace_and_marker_only_differences_do_not_count_as_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            current = _pipeline_text(workspace, S1)
            xml = _edit_export(
                export_xliff(workspace),
                targets={S1: f"  {current}\n"},
                sources={S3: " Nested\n  paragraph. "},
            )
            items = self.classify_file(workspace, xml)
            assert items[S1]["category"] == "unchanged"
            assert items[S3]["category"] == "unchanged"

    def test_a_segment_retranslated_by_a_rerun_since_export_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章"})
            _retarget_pipeline_text(workspace, S1, "重新翻译的章节。")
            item = self.classify_file(workspace, xml)[S1]
            assert item["category"] == "stale"
            assert item["stale"] and not item["edited_since_export"]
            assert "changed after this file was exported" in item["message"]

    def test_a_segment_edited_in_the_text_tab_since_export_is_flagged(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章"})
            apply_edit(
                workspace,
                segment_id=S1,
                text="章节一",
                reason="Edited after export.",
                base_target_sha256=sha256_text(_pipeline_text(workspace, S1)),
            )
            item = self.classify_file(workspace, xml)[S1]
            assert item["category"] == "edited_since_export"
            assert item["current_text"] == "章节一"

    def test_edited_since_export_takes_precedence_over_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章"})
            apply_edit(
                workspace,
                segment_id=S1,
                text="章节一",
                reason="Edited after export.",
                base_target_sha256=sha256_text(_pipeline_text(workspace, S1)),
            )
            _retarget_pipeline_text(workspace, S1, "重新翻译的章节。")
            item = self.classify_file(workspace, xml)[S1]
            assert item["category"] == "edited_since_export"
            assert item["stale"] and item["edited_since_export"]

    def test_a_file_without_export_metadata_counts_changed_units_as_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            body = _unit(S1, "Chapter One", "第一章")
            units, warnings = parse_xliff(_document(body, book=""), direction=EN_ZH, source_sha256=_book_hash(workspace))
            item = classify(workspace, units)[0]
            assert warnings
            assert item["category"] == "stale"
            assert "No export metadata" in item["message"]

    def test_overridable_findings_need_an_override(self, monkeypatch):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            monkeypatch.setattr(
                "book_agent.xliff_import.check_edits",
                lambda ws, proposed: {
                    sid: {"hard": [], "overridable": [{"category": "naturalness", "severity": "medium", "message": "reads stiffly"}]}
                    for sid in proposed
                },
            )
            item = self.classify_file(workspace, _edit_export(export_xliff(workspace), targets={S1: "第一章"}))[S1]
            assert item["category"] == "needs_override"
            assert item["message"] == "reads stiffly"


class WillImportTests:
    def item(self, **overrides):
        return {"category": "import", "stale": False, "edited_since_export": False, "hard": [], "overridable": [], **overrides}

    def test_only_eligible_categories_import(self):
        assert will_import(self.item(), ImportOptions())
        for category in ("unchanged", "unknown_id", "source_differs", "no_target", "unsupported_markup", "fails_checks"):
            assert not will_import(self.item(category=category), ImportOptions(True, True, True))

    def test_each_opt_in_unlocks_only_its_own_case(self):
        stale = self.item(category="stale", stale=True)
        edited = self.item(category="edited_since_export", edited_since_export=True)
        both = self.item(category="edited_since_export", stale=True, edited_since_export=True)
        override = self.item(category="needs_override", overridable=[{"message": "m"}])
        assert not will_import(stale, ImportOptions()) and will_import(stale, ImportOptions(include_stale=True))
        assert not will_import(edited, ImportOptions()) and will_import(edited, ImportOptions(include_edited=True))
        assert not will_import(both, ImportOptions(include_edited=True))
        assert will_import(both, ImportOptions(include_stale=True, include_edited=True))
        assert not will_import(override, ImportOptions()) and will_import(override, ImportOptions(include_overridable=True))

    def test_an_opted_in_unit_still_needs_to_pass_the_checks(self):
        stale_and_failing = self.item(category="stale", stale=True, hard=[{"message": "empty"}])
        assert not will_import(stale_and_failing, ImportOptions(True, True, True))


# -- storing and applying ------------------------------------------------------------


class ImportWorkflowTests:
    def test_start_stores_a_pending_preview_that_a_reload_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(workspace, "C:/fakepath/book.en-zh.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            assert preview["file_name"] == "book.en-zh.xlf"
            assert preview["counts"] == {"import": 1, "unchanged": 3}
            state = import_state(workspace)
            assert state["pending"]["import_id"] == preview["import_id"]
            assert state["pending"]["counts"] == preview["counts"]
            assert state["last"] is None
            assert load_events(workspace) == []  # nothing written yet

    def test_the_preview_is_recomputed_against_the_current_book(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            apply_edit(
                workspace,
                segment_id=S1,
                text="章节一",
                reason="Edited while the preview was open.",
                base_target_sha256=sha256_text(_pipeline_text(workspace, S1)),
            )
            assert import_state(workspace)["pending"]["counts"]["edited_since_export"] == 1

    def test_uploading_another_file_replaces_the_pending_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            first = start_import(workspace, "one.xlf", export_xliff(workspace))
            second = start_import(workspace, "two.xlf", export_xliff(workspace))
            assert import_state(workspace)["pending"]["import_id"] == second["import_id"]
            with pytest.raises(ValueError, match="no longer pending"):
                apply_import(workspace, first["import_id"], "Old file.", ImportOptions())

    def test_apply_writes_tracked_edits_with_the_reason_and_file_name(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(
                workspace,
                "book.xlf",
                _edit_export(export_xliff(workspace), targets={S1: "第一章", S2: "你好<pc id=\"000\">小小的</pc>世界。"}),
            )
            result = apply_import(workspace, preview["import_id"], "Translator pass.", ImportOptions(), author="translator")

            assert sorted(item["segment_id"] for item in result["applied"]) == [S1, S2]
            assert result["skipped"] == [] and result["unchanged_count"] == 2
            event = history_for_segment(workspace, S2)[-1]
            assert event.text == "你好<I000>小小的</I000>世界。"  # markers mapped back
            assert event.reason == "Translator pass. (imported from book.xlf)"
            assert event.author == "translator"
            state = import_state(workspace)
            assert state["pending"] is None
            assert state["last"]["import_id"] == preview["import_id"]

    def test_stale_units_are_skipped_unless_opted_in_and_then_rebased(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章", S3: "嵌套段落。"})
            _retarget_pipeline_text(workspace, S1, "重新翻译的章节。")

            skip = start_import(workspace, "book.xlf", xml)
            result = apply_import(workspace, skip["import_id"], "Default choices.", ImportOptions())
            assert [item["segment_id"] for item in result["applied"]] == [S3]
            assert [(item["segment_id"], item["category"]) for item in result["skipped"]] == [(S1, "stale")]

            dismiss_import(workspace, skip["import_id"])
            again = start_import(workspace, "book.xlf", xml)
            result = apply_import(workspace, again["import_id"], "Including stale.", ImportOptions(include_stale=True))
            assert [item["segment_id"] for item in result["applied"]] == [S1]
            status = segment_status(
                history_for_segment(workspace, S1), pipeline_text="重新翻译的章节。", source_text="Chapter One"
            )
            assert status.state.value == "edited"  # re-based on the current pipeline text, not a conflict

    def test_opting_in_overwrites_a_newer_text_tab_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章"})
            apply_edit(
                workspace,
                segment_id=S1,
                text="章节一",
                reason="Edited after export.",
                base_target_sha256=sha256_text(_pipeline_text(workspace, S1)),
            )
            preview = start_import(workspace, "book.xlf", xml)
            with pytest.raises(ValueError, match="nothing to import"):
                apply_import(workspace, preview["import_id"], "Default choices.", ImportOptions())
            result = apply_import(workspace, preview["import_id"], "Translator wins.", ImportOptions(include_edited=True))
            assert [item["segment_id"] for item in result["applied"]] == [S1]
            assert history_for_segment(workspace, S1)[-1].text == "第一章"

    def test_an_override_records_the_import_reason(self, monkeypatch):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)

            def stiff(ws, proposed):
                return {
                    sid: {"hard": [], "overridable": [{"category": "naturalness", "severity": "medium", "message": "reads stiffly"}]}
                    for sid in proposed
                }

            monkeypatch.setattr("book_agent.xliff_import.check_edits", stiff)
            monkeypatch.setattr("book_agent.text_edits.check_edits", stiff)
            preview = start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            with pytest.raises(ValueError, match="nothing to import"):
                apply_import(workspace, preview["import_id"], "Keep the tone.", ImportOptions())
            apply_import(workspace, preview["import_id"], "Keep the tone.", ImportOptions(include_overridable=True))
            event = history_for_segment(workspace, S1)[-1]
            assert event.overrides == ["reads stiffly"]

    def test_a_unit_that_only_passes_next_to_a_skipped_one_is_dropped_at_apply(self):
        # S4's imported text duplicates S3's current pipeline text (both paragraphs; the
        # audit's duplicate check skips headings). In the preview S3 is also being
        # replaced, so S4 passes; S3 is stale and skipped by default, so at apply S4
        # sits next to S3's pipeline text and must be dropped, while S1 still imports.
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            _retarget_pipeline_text(workspace, S3, "这是一段嵌套段落的第一个长版本。")
            repeated = "这是一段嵌套段落的第二个更长版本。"
            xml = _edit_export(
                export_xliff(workspace),
                targets={S1: "第一章", S3: "另一段完全不同的译文。", S4: repeated},
            )
            _retarget_pipeline_text(workspace, S3, repeated)

            preview = start_import(workspace, "book.xlf", xml)
            items = _by_segment(preview["items"])
            assert items[S3]["category"] == "stale"
            # No hard finding in the preview (a length note on the short source is overridable).
            assert items[S4]["category"] in {"import", "needs_override"} and items[S4]["hard"] == []

            result = apply_import(
                workspace, preview["import_id"], "Translator pass.", ImportOptions(include_overridable=True)
            )
            assert [item["segment_id"] for item in result["applied"]] == [S1]
            assert result["dropped_at_apply"] == 1
            skipped = {item["segment_id"]: item for item in result["skipped"]}
            assert skipped[S4]["category"] == "fails_checks"
            assert "duplicates" in skipped[S4]["message"]
            assert skipped[S3]["category"] == "stale"

    def test_a_change_during_apply_writes_nothing_and_keeps_the_preview(self, monkeypatch):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))

            def raced(ws, requests):
                raise StaleEditError("D0000-S000001 was edited after this page was loaded")

            monkeypatch.setattr("book_agent.xliff_import.apply_edit_batch", raced)
            with pytest.raises(ValueError, match="the book changed while importing"):
                apply_import(workspace, preview["import_id"], "Translator pass.", ImportOptions())
            assert load_events(workspace) == []
            assert import_state(workspace)["pending"]["import_id"] == preview["import_id"]

    def test_apply_needs_a_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            with pytest.raises(ValueError, match="reason"):
                apply_import(workspace, preview["import_id"], "ok", ImportOptions())

    def test_cancel_and_dismiss(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(workspace, "book.xlf", export_xliff(workspace))
            with pytest.raises(ValueError, match="only an applied import"):
                dismiss_import(workspace, preview["import_id"])
            cancel_import(workspace, preview["import_id"])
            assert import_state(workspace) == {"pending": None, "last": None}

            applied = start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            apply_import(workspace, applied["import_id"], "Translator pass.", ImportOptions())
            assert import_state(workspace)["last"]["import_id"] == applied["import_id"]
            dismiss_import(workspace, applied["import_id"])
            assert import_state(workspace)["last"] is None

    def test_refuses_before_the_validated_draft_and_unknown_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            with pytest.raises(ValueError, match="no such import"):
                cancel_import(workspace, "../../state")
            with pytest.raises(ValueError, match="no such import"):
                cancel_import(workspace, "I20260101T000000-abcdef")
            connection = connect_state(workspace.state_file)
            try:
                from book_agent.state import StageStatus, set_stage_status

                set_stage_status(connection, "validate_repaired", StageStatus.PENDING)
            finally:
                connection.close()
            with pytest.raises(ValueError, match="needs the validated draft"):
                start_import(workspace, "book.xlf", "<xliff/>")

    def test_report_lists_every_unit_before_and_after_applying(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            xml = _edit_export(export_xliff(workspace), targets={S1: "第一章"}, sources={S4: "Other source."})
            preview = start_import(workspace, "book.en-zh.xlf", xml)

            data, name = import_report_csv(workspace, preview["import_id"])
            assert name == "book.en-zh.import-report.csv"
            text = data.decode("utf-8-sig")
            assert text.splitlines()[0] == "unit_id,segment_id,chapter,outcome,category,reason"
            assert f"{S1},{S1},chapter,will import,import," in text
            assert f"{S4},{S4},chapter,skipped,source_differs," in text

            apply_import(workspace, preview["import_id"], "Translator pass.", ImportOptions())
            text = import_report_csv(workspace, preview["import_id"])[0].decode("utf-8-sig")
            assert f"{S1},{S1},chapter,imported,import," in text
            assert f"{S4},{S4},chapter,skipped,source_differs," in text

    def test_the_stored_record_holds_units_status_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            preview = start_import(workspace, "book.xlf", _edit_export(export_xliff(workspace), targets={S1: "第一章"}))
            path = workspace.root / "edits" / "imports" / f"{preview['import_id']}.json"
            record = json.loads(path.read_text(encoding="utf-8"))
            assert record["status"] == "preview" and len(record["units"]) == 4
            apply_import(workspace, preview["import_id"], "Translator pass.", ImportOptions())
            record = json.loads(path.read_text(encoding="utf-8"))
            assert record["status"] == "applied"
            assert record["result"]["applied"][0]["segment_id"] == S1
