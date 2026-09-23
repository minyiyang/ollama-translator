import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from book_agent.config import AppConfig
from book_agent.hashing import sha256_text
from book_agent.text_edits import apply_edit
from book_agent.xliff_export import export_xliff
from tests.test_compile_stages import CompileStageTests

_NS = {"x": "urn:oasis:names:tc:xliff:document:2.1"}


class XliffExportTests:
    def test_exports_one_file_per_chapter_with_translated_state(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            xml = export_xliff(workspace)
            root = ET.fromstring(xml)
            assert root.tag == "{urn:oasis:names:tc:xliff:document:2.1}xliff"
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

    def test_marker_becomes_a_paired_code_element(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            xml = export_xliff(workspace)
            root = ET.fromstring(xml)
            unit = next(
                u for u in root.iter("{urn:oasis:names:tc:xliff:document:2.1}unit")
                if u.get("id") == "D0000-S000002"
            )
            source = unit.find(".//x:source", _NS)
            pc = source.find("x:pc", _NS)
            assert pc is not None
            assert pc.get("id") == "000"
            assert pc.text == "small"

    def test_an_active_edit_exports_as_reviewed(self):
        config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
        with tempfile.TemporaryDirectory() as directory:
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            from book_agent.stages.validate_repaired import load_validated_repaired_documents

            segment = next(
                s for s in load_validated_repaired_documents(workspace)[0].document.segments
                if s.source_text == "Chapter One"
            )
            apply_edit(
                workspace,
                segment_id=segment.segment_id,
                text="第一章",
                reason="Testing the XLIFF export.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            xml = export_xliff(workspace)
            root = ET.fromstring(xml)
            unit = next(
                u for u in root.iter("{urn:oasis:names:tc:xliff:document:2.1}unit")
                if u.get("id") == segment.segment_id
            )
            xliff_segment = unit.find("x:segment", _NS)
            assert xliff_segment.get("state") == "reviewed"
            assert xliff_segment.find("x:target", _NS).text == "第一章"
