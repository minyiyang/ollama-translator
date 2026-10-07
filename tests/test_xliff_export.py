import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from book_agent.config import AppConfig
from book_agent.hashing import sha256_text
from book_agent.state import connect_state, get_job_metadata
from book_agent.text_edits import apply_edit
from book_agent.xliff_export import CORE_NAMESPACE, METADATA_NAMESPACE, export_xliff
from tests.test_compile_stages import prepare_workspace

_NS = {"x": CORE_NAMESPACE, "mda": METADATA_NAMESPACE}


def _workspace(directory):
    config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
    return prepare_workspace(Path(directory), config)


def _unit(root, unit_id):
    return next(u for u in root.iterfind(".//x:unit", _NS) if u.get("id") == unit_id)


def _metas(element) -> dict[str, str]:
    return {
        meta.get("type"): meta.text or ""
        for meta in element.iterfind("mda:metadata/mda:metaGroup[@category='book-agent']/mda:meta", _NS)
    }


class XliffExportTests:
    def test_uses_the_xliff_2_core_namespace_with_version_2_1(self):
        # XLIFF 2.1 did not change the core namespace; CAT tools reject a "2.1" one.
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_xliff(_workspace(directory)))
            assert CORE_NAMESPACE == "urn:oasis:names:tc:xliff:document:2.0"
            assert root.tag == f"{{{CORE_NAMESPACE}}}xliff"
            assert root.get("version") == "2.1"

    def test_exports_one_file_per_chapter_with_translated_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_xliff(_workspace(directory)))
            assert root.get("srcLang") == "en"
            assert root.get("trgLang") == "zh"
            files = root.findall("x:file", _NS)
            assert len(files) == 1
            units = files[0].findall("x:unit", _NS)
            assert len(units) == 4
            for unit in units:
                segment = unit.find("x:segment", _NS)
                assert segment.get("state") == "translated"
                assert "".join(segment.find("x:source", _NS).itertext())
                assert "".join(segment.find("x:target", _NS).itertext())

    def test_records_what_each_unit_was_exported_against(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            root = ET.fromstring(export_xliff(workspace))
            connection = connect_state(workspace.state_file)
            try:
                source_sha256 = get_job_metadata(connection, "source_sha256")
            finally:
                connection.close()

            book = _metas(root.find("x:file", _NS))
            assert book == {"source_sha256": source_sha256, "job_id": workspace.root.name}

            from book_agent.stages.validate_repaired import load_validated_repaired_documents

            segment = load_validated_repaired_documents(workspace)[0].document.segments[0]
            unit = _metas(_unit(root, segment.segment_id))
            # No edit yet, so no revision is recorded.
            assert unit == {"base_target_sha256": sha256_text(segment.translated_text)}

    def test_marker_becomes_a_paired_code_element(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_xliff(_workspace(directory)))
            pc = _unit(root, "D0000-S000002").find(".//x:source/x:pc", _NS)
            assert pc is not None
            assert pc.get("id") == "000"
            assert pc.text == "small"

    def test_an_active_edit_exports_as_reviewed_with_its_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = _workspace(directory)
            from book_agent.stages.validate_repaired import load_validated_repaired_documents

            segment = next(
                s for s in load_validated_repaired_documents(workspace)[0].document.segments
                if s.source_text == "Chapter One"
            )
            event = apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="第一章",
                reason="Testing the XLIFF export.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            unit = _unit(ET.fromstring(export_xliff(workspace)), segment.segment_id)
            xliff_segment = unit.find("x:segment", _NS)
            assert xliff_segment.get("state") == "reviewed"
            assert xliff_segment.find("x:target", _NS).text == "第一章"
            # The base stays the pipeline text the edit was made against.
            assert _metas(unit) == {
                "base_target_sha256": sha256_text(segment.translated_text),
                "edit_revision": event.event_id,
            }
