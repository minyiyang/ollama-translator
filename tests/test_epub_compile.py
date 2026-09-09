import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import pytest

from book_agent.epub import (
    build_protected_inline_text,
    extract_text_segments,
    inspect_epub_package,
    safe_extract_epub,
)
from book_agent.epub_compile import (
    EpubCompilationError,
    apply_segment_translation,
    compile_epub_package,
    find_element_by_stable_path,
    parse_inline_translation,
    render_translated_xhtml,
    _extract_linked_toc_heading_labels,
    validate_compiled_epub,
)
from book_agent.hashing import sha256_file
from book_agent.languages import TranslationDirection
from book_agent.repair import RepairedDocument
from book_agent.translation import TranslatedDocument, TranslatedSegment
from tests.epub_fixture import make_epub


def translated_documents(manifest):
    results = []
    for document in manifest.documents:
        segments = []
        for index, segment in enumerate(document.segments, start=1):
            markers = re.findall(r"</?I\d{3}>", segment.protected_text)
            if markers:
                first_close = next(
                    i for i, marker in enumerate(markers) if marker.startswith("</")
                )
                text = "".join(markers[:first_close]) + "强调译文" + "".join(markers[first_close:])
            else:
                text = f"译文第{index}段。"
            segments.append(
                TranslatedSegment(
                    segment_id=segment.segment_id,
                    source_text=segment.protected_text or segment.text,
                    translated_text=text,
                )
            )
        results.append(
            RepairedDocument(
                document=TranslatedDocument(
                    order=document.order,
                    manifest_id=document.manifest_id,
                    archive_path=document.archive_path,
                    direction=TranslationDirection.EN_TO_ZH,
                    style="literary",
                    segments=segments,
                )
            )
        )
    return results


class EpubCompileTests:
    def test_extracts_translated_linked_toc_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "text").mkdir()
            (root / "text" / "contents.xhtml").write_text(
                "<html><body><a href='one.xhtml'>第一章</a>"
                "<a href='two.xhtml'>第二章</a>"
                "<a href='story.xhtml'>陨落英雄</a></body></html>",
                encoding="utf-8",
            )
            for name in ("one.xhtml", "two.xhtml", "story.xhtml"):
                (root / "text" / name).write_text(
                    "<html><body><p>text</p></body></html>", encoding="utf-8"
                )
            docs = [
                SimpleNamespace(manifest_id="contents", archive_path="text/contents.xhtml"),
                SimpleNamespace(manifest_id="one", archive_path="text/one.xhtml"),
                SimpleNamespace(manifest_id="two", archive_path="text/two.xhtml"),
                SimpleNamespace(manifest_id="story", archive_path="text/story.xhtml"),
            ]
            labels = _extract_linked_toc_heading_labels(
                root, SimpleNamespace(documents=docs)
            )
            assert labels == {"one": "第一章", "two": "第二章", "story": "陨落英雄"}

    def test_protected_inline_text_records_nested_paths(self):
        root = ET.fromstring("<p>Hello <em>small <a href='x'>link</a></em> world.</p>")
        text, markers = build_protected_inline_text(root, "/p[1]")
        assert text == "Hello <I000>small <I001>link</I001></I000> world."
        assert [item.tag for item in markers] == ["em", "a"]
        assert markers[1].element_path == "/p[1]/em[1]/a[1]"

    def test_find_path_and_apply_translation_preserve_inline_element(self):
        root = ET.fromstring("<html><body><p>Hello <em class='tone'>small</em> world.</p></body></html>")
        segment = extract_text_segments(root, 0)[0]
        paragraph = find_element_by_stable_path(root, segment.element_path)
        assert paragraph.tag == "p"
        apply_segment_translation(root, segment, "你好<I000>小小</I000>世界。")
        emphasis = list(paragraph)[0]
        assert paragraph.text == "你好"
        assert emphasis.text == "小小"
        assert emphasis.tail == "世界。"
        assert emphasis.attrib["class"] == "tone"
        with pytest.raises(EpubCompilationError, match="does not exist"):
            find_element_by_stable_path(root, "/html[1]/body[1]/p[9]")

    def test_inline_parser_rejects_unbalanced_markers(self):
        parsed = parse_inline_translation("前<I000>中</I000>后")
        assert parsed.children[0].text == "中"
        with pytest.raises(EpubCompilationError, match="unclosed"):
            parse_inline_translation("<I000>text")
        with pytest.raises(EpubCompilationError, match="unbalanced"):
            parse_inline_translation("</I000>")

    def test_render_rejects_changed_segment_or_inline_marker_contract(self):
        root = ET.fromstring("<html><body><p>Hello <em>small</em>.</p></body></html>")
        source_data = ET.tostring(root)
        source = type("Doc", (), {})
        # Use the real manifest schema through a tiny inspected EPUB below for public behavior.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            package = base / "package"
            safe_extract_epub(epub, package)
            manifest = inspect_epub_package(package, sha256_file(epub))
            document = manifest.documents[0]
            repaired = translated_documents(manifest)[0].document
            rendered = render_translated_xhtml(
                package.joinpath(*document.archive_path.split("/")).read_bytes(),
                document,
                repaired,
            )
            assert "<em>" in rendered.decode("utf-8")
            bad = repaired.model_copy(
                update={"segments": repaired.segments[:-1]}
            )
            with pytest.raises(EpubCompilationError, match="segment IDs"):
                render_translated_xhtml(source_data, document, bad)
            broken = repaired.model_copy(deep=True)
            inline = next(item for item in broken.segments if "<I000>" in item.translated_text)
            inline.translated_text = "丢失格式标记"
            with pytest.raises(EpubCompilationError, match="inline marker"):
                render_translated_xhtml(
                    package.joinpath(*document.archive_path.split("/")).read_bytes(),
                    document,
                    broken,
                )

    def test_compile_and_validate_tiny_epub_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            package = base / "package"
            safe_extract_epub(epub, package)
            manifest = inspect_epub_package(package, sha256_file(epub))
            repaired = translated_documents(manifest)
            output = base / "translated.epub"
            report = compile_epub_package(package, manifest, repaired, output)
            assert report.document_count == 1
            assert report.segment_count == 4
            assert report.output_sha256 == sha256_file(output)
            with ZipFile(output) as archive:
                assert archive.infolist()[0].filename == "mimetype"
                assert archive.infolist()[0].compress_type == ZIP_STORED
                chapter = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
                assert "<em>" in chapter
                assert "强调译文" in chapter
            validation = validate_compiled_epub(output, manifest, repaired)
            assert validation.passed, validation.errors
            assert validation.resource_count == len(manifest.resources)
            second = base / "translated-again.epub"
            second_report = compile_epub_package(package, manifest, repaired, second)
            assert second_report.output_sha256 == report.output_sha256

    def test_compile_can_strip_print_page_markers_without_removing_toc(self):
        chapter = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'
      xmlns:epub='http://www.idpf.org/2007/ops'>
<head><title>Fixture</title></head><body>
<h1>Chapter 12</h1>
<p><span epub:type='pagebreak' role='doc-pagebreak' id='page42'
         title='42'/><a id='page-43'/>Hello <em>small</em> world.</p>
</body></html>"""
        nav = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'
      xmlns:epub='http://www.idpf.org/2007/ops'>
<body>
<nav epub:type='toc'><ol><li><a href='text/chapter.xhtml'>Chapter 12</a></li></ol></nav>
<nav epub:type='page-list' hidden='hidden' role='doc-pagelist'>
  <ol><li><a href='text/chapter.xhtml#page42'>42</a></li></ol>
</nav>
</body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub", chapter=chapter)
            package = base / "package"
            safe_extract_epub(epub, package)
            (package / "OEBPS" / "nav.xhtml").write_bytes(nav)
            manifest = inspect_epub_package(package, sha256_file(epub))
            repaired = translated_documents(manifest)
            repaired[0].document.segments[0].translated_text = "第12章"
            output = base / "translated.epub"

            report = compile_epub_package(
                package,
                manifest,
                repaired,
                output,
                strip_print_page_markers=True,
            )

            assert report.stripped_page_marker_count == 3
            with ZipFile(output) as archive:
                rendered_chapter = archive.read(
                    "OEBPS/text/chapter.xhtml"
                ).decode("utf-8")
                rendered_nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
            assert "第12章" in rendered_chapter
            assert "pagebreak" not in rendered_chapter
            assert "doc-pagebreak" not in rendered_chapter
            assert 'id="page-43"' not in rendered_chapter
            assert "toc" in rendered_nav
            assert "page-list" not in rendered_nav
            validation = validate_compiled_epub(
                output,
                manifest,
                repaired,
                strip_print_page_markers=True,
                source_package_root=package,
            )
            assert validation.passed, validation.errors

    def test_malformed_html_fallback_still_compiles_and_validates(self):
        malformed = (
            b"<html><head><title>Legacy</title></head><body>"
            b"<div>Short <em>legacy</div><p>Second paragraph.</p></body></html>"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "legacy.epub", chapter=malformed)
            package = base / "package"
            safe_extract_epub(epub, package)
            manifest = inspect_epub_package(package, sha256_file(epub))
            repaired = translated_documents(manifest)
            output = base / "translated.epub"
            compile_epub_package(package, manifest, repaired, output)
            validation = validate_compiled_epub(output, manifest, repaired)
            assert validation.passed, validation.errors

    def test_compile_rejects_document_set_and_validation_rejects_bad_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            package = base / "package"
            safe_extract_epub(epub, package)
            manifest = inspect_epub_package(package, sha256_file(epub))
            with pytest.raises(EpubCompilationError, match="document order or set"):
                compile_epub_package(package, manifest, [], base / "bad.epub")
            invalid = base / "invalid.epub"
            invalid.write_bytes(b"not a zip")
            report = validate_compiled_epub(invalid, manifest, translated_documents(manifest))
            assert not report.passed
            assert "invalid EPUB ZIP" in report.errors[0]

