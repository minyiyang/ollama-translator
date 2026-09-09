import tempfile
import warnings
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from book_agent.epub_compile import EpubCompilationError
from book_agent.hashing import sha256_file
from book_agent.languages import TranslationDirection
from book_agent.repair import RepairedDocument
from book_agent.rtf import (
    RtfError,
    compile_rtf_document,
    inspect_rtf_document,
    is_chapter_heading,
    rtf_to_text,
    validate_compiled_rtf,
)
from book_agent.translation import TranslatedDocument, TranslatedSegment
from tests.rtf_fixture import RTF, make_rtf


class RtfAdapterTests:
    def test_reference_style_heading_patterns_use_obfuscated_titles(self):
        assert is_chapter_heading("7. Zorvak Qelm")
        assert is_chapter_heading("ГЛАВА 3: ЖОРВАК")

    def test_extracts_obfuscated_text_and_skips_metadata(self):
        text = rtf_to_text(RTF)
        assert "Chapter 1: Velnor Gate" in text
        assert "The token é remains visible." in text
        assert "Unicode: 译文." in text
        assert "Qelm Archive" not in text

    def test_inspection_builds_stable_chapter_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            source = make_rtf(Path(directory) / "qelm.rtf")
            manifest = inspect_rtf_document(source, sha256_file(source))
            assert manifest.source_format == "rtf"
            assert len(manifest.documents) == 2
            assert manifest.documents[0].manifest_id == "rtf-0000"
            assert manifest.documents[1].segments[0].segment_id == "D0001-S000001"

    def test_compile_epub_and_parse_back_matches_accepted_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_rtf(base / "qelm.rtf")
            manifest = inspect_rtf_document(source, sha256_file(source))
            repaired = []
            for document in manifest.documents:
                translated = TranslatedDocument(
                    order=document.order,
                    manifest_id=document.manifest_id,
                    archive_path=document.archive_path,
                    direction=TranslationDirection.EN_TO_ZH,
                    style="neutral",
                    segments=[
                        TranslatedSegment(
                            segment_id=segment.segment_id,
                            source_text=segment.text,
                            translated_text=f"译文-{document.order}-{index}",
                        )
                        for index, segment in enumerate(document.segments, start=1)
                    ],
                )
                repaired.append(RepairedDocument(document=translated))
            output = base / "qelm.translated.epub"
            report = compile_rtf_document(manifest, repaired, output)
            assert report.output_size > 0
            assert validate_compiled_rtf(output, manifest, repaired).passed
            with ZipFile(output) as archive:
                assert archive.infolist()[0].filename == "mimetype"
                assert archive.infolist()[0].compress_type == ZIP_STORED
                chapter = archive.read("OEBPS/text/rtf-0001.xhtml").decode("utf-8")
                assert "译文-1-1" in chapter
                navigation = archive.read("OEBPS/nav.xhtml").decode("utf-8")
                assert "译文-1-1" in navigation
            output.write_bytes(b"not an epub")
            assert not validate_compiled_rtf(output, manifest, repaired).passed

    def test_rejects_non_rtf_input(self):
        with pytest.raises(RtfError):
            rtf_to_text(b"Zorvak qelmin")

    def test_enforces_size_and_decoder_boundaries(self):
        with patch("book_agent.rtf._MAX_RTF_BYTES", 3):
            with pytest.raises(RtfError, match="safety limit"):
                rtf_to_text(RTF)
        with patch(
            "book_agent.rtf.strip_rtf_to_text",
            side_effect=UnicodeError("qelm decoder"),
        ):
            with pytest.raises(RtfError, match="decoding failed"):
                rtf_to_text(rb"{\rtf1 Zorvak}")

    def test_rejects_empty_rtf_and_preserves_obfuscated_preface(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            empty = make_rtf(base / "empty.rtf", content=rb"{\rtf1\ansi }")
            with pytest.raises(RtfError, match="no translatable text"):
                inspect_rtf_document(empty, sha256_file(empty))
            preface = make_rtf(
                base / "preface.rtf",
                content=(
                    rb"{\rtf1\ansi Zorvak preface.\par "
                    rb"Chapter 1: Qelm Gate\par Nerith varkel.\par}"
                ),
            )
            manifest = inspect_rtf_document(preface, sha256_file(preface))
            assert len(manifest.documents) == 2
            assert manifest.documents[0].title == "preface"
            plain = make_rtf(
                base / "plain.rtf",
                content=rb"{\rtf1\ansi Zorvak qelm.\par Nerith varkel.\par}",
            )
            plain_manifest = inspect_rtf_document(plain, sha256_file(plain))
            assert len(plain_manifest.documents) == 1
            assert plain_manifest.documents[0].title == "plain"

    def test_compile_contract_rejects_wrong_format_set_suffix_and_empty_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_rtf(base / "qelm.rtf")
            manifest = inspect_rtf_document(source, sha256_file(source))
            with pytest.raises(EpubCompilationError, match="non-RTF"):
                compile_rtf_document(
                    manifest.model_copy(update={"source_format": "epub"}),
                    [],
                    base / "wrong.epub",
                )
            with pytest.raises(EpubCompilationError, match="order or set"):
                compile_rtf_document(manifest, [], base / "missing.epub")
            with pytest.raises(EpubCompilationError, match="no documents"):
                compile_rtf_document(
                    manifest.model_copy(update={"documents": []}),
                    [],
                    base / "empty.epub",
                )
            repaired = _translated_documents(manifest)
            with pytest.raises(EpubCompilationError, match=r"\.epub suffix"):
                compile_rtf_document(manifest, repaired, base / "wrong.rtf")

    def test_validation_reports_manifest_set_and_text_mismatches(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_rtf(base / "qelm.rtf")
            manifest = inspect_rtf_document(source, sha256_file(source))
            repaired = _translated_documents(manifest)
            output = base / "qelm.translated.epub"
            compile_rtf_document(manifest, repaired, output)
            wrong_source = manifest.model_copy(update={"source_format": "epub"})
            assert not validate_compiled_rtf(output, wrong_source, repaired).passed
            assert not validate_compiled_rtf(output, manifest, repaired[:-1]).passed
            repaired[0].document.segments[0].translated_text = "Qelm mismatch"
            result = validate_compiled_rtf(output, manifest, repaired)
            assert not result.passed
            assert any("text differs" in item for item in result.errors)

    def test_validation_reports_epub_container_and_shape_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = make_rtf(base / "qelm.rtf")
            manifest = inspect_rtf_document(source, sha256_file(source))
            repaired = _translated_documents(manifest)
            good = base / "good.epub"
            compile_rtf_document(manifest, repaired, good)
            with ZipFile(good) as archive:
                entries = [(item.filename, archive.read(item)) for item in archive.infolist()]
            malformed = base / "malformed.epub"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with ZipFile(malformed, "w", compression=ZIP_DEFLATED) as archive:
                    archive.writestr("qelm.bin", b"qelm")
                    for name, data in entries:
                        archive.writestr(name, data)
                    archive.writestr("qelm.bin", b"duplicate")
            malformed_report = validate_compiled_rtf(malformed, manifest, repaired)
            assert not malformed_report.passed
            assert any("first archive" in item for item in malformed_report.errors)
            assert any("duplicate" in item for item in malformed_report.errors)
            assert any("inspection failed" in item for item in malformed_report.errors)

            compressed = base / "compressed.epub"
            with ZipFile(compressed, "w", compression=ZIP_DEFLATED) as archive:
                for name, data in entries:
                    archive.writestr(name, data)
            compressed_report = validate_compiled_rtf(compressed, manifest, repaired)
            assert not compressed_report.passed
            assert any("compressed" in item for item in compressed_report.errors)

            short_manifest = manifest.model_copy(update={"documents": manifest.documents[:1]})
            short_output = base / "short.epub"
            compile_rtf_document(short_manifest, repaired[:1], short_output)
            short_report = validate_compiled_rtf(short_output, manifest, repaired)
            assert not short_report.passed
            assert any("chapter count" in item for item in short_report.errors)

            repaired_with_missing_segment = _translated_documents(manifest)
            repaired_with_missing_segment[0].document.segments.pop()
            segment_report = validate_compiled_rtf(
                good, manifest, repaired_with_missing_segment
            )
            assert not segment_report.passed
            assert any("segment count" in item for item in segment_report.errors)


def _translated_documents(manifest):
    return [
        RepairedDocument(
            document=TranslatedDocument(
                order=document.order,
                manifest_id=document.manifest_id,
                archive_path=document.archive_path,
                direction=TranslationDirection.EN_TO_ZH,
                style="neutral",
                segments=[
                    TranslatedSegment(
                        segment_id=segment.segment_id,
                        source_text=segment.text,
                        translated_text=f"译文-{document.order}-{index}",
                    )
                    for index, segment in enumerate(document.segments, start=1)
                ],
            )
        )
        for document in manifest.documents
    ]

