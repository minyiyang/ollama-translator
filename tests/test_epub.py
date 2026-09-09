import tempfile
import warnings
from pathlib import Path
from zipfile import ZipFile

import pytest

from book_agent.epub import (
    EpubError,
    UnsafeArchiveError,
    discover_opf_path,
    extract_text_segments,
    inspect_epub_package,
    local_name,
    normalize_text,
    parse_content_document,
    parse_xml,
    resolve_package_href,
    safe_extract_epub,
    validate_archive_path,
)
from book_agent.hashing import sha256_file
from tests.epub_fixture import CHAPTER, CONTAINER, NAV, OPF, make_epub


class EpubUtilityTests:
    def test_local_name_and_normalize_text(self) -> None:
        assert local_name("{namespace}title") == "title"
        assert local_name("dc:title") == "title"
        assert normalize_text(" one\n two\u00a0three ") == "one two three"

    def test_parse_xml_rejects_invalid_and_entity_declarations(self) -> None:
        with pytest.raises(EpubError, match="invalid XML"):
            parse_xml(b"<broken>", "broken.xml")
        with pytest.raises(EpubError, match="unsafe"):
            parse_xml(b"<!DOCTYPE x><x/>", "unsafe.xml")
        assert local_name(parse_xml(b"<!DOCTYPE html><html/>", "cover.xhtml").tag) == "html"

    def test_validate_archive_path(self, subtests) -> None:
        assert validate_archive_path("OEBPS/text/a.xhtml").as_posix() == "OEBPS/text/a.xhtml"
        for invalid in ("../escape", "/absolute", "C:/drive", "a\\b"):
            with subtests.test(invalid=invalid):
                with pytest.raises(UnsafeArchiveError):
                    validate_archive_path(invalid)

    def test_resolve_package_href(self) -> None:
        assert resolve_package_href("OEBPS/content.opf", "text/Aster%20One.xhtml#part") == ("OEBPS/text/Aster One.xhtml", "part")
        with pytest.raises(EpubError, match="external"):
            resolve_package_href("OEBPS/content.opf", "https://example.com/a")
        with pytest.raises(EpubError, match="escapes"):
            resolve_package_href("content.opf", "../outside")
        assert resolve_package_href("OEBPS/text/chapter.xhtml", "#legacy-anchor") == ("OEBPS/text/chapter.xhtml", "legacy-anchor")
        assert resolve_package_href("OEBPS/content.opf", "/OPS/chapter.xhtml") == ("OPS/chapter.xhtml", "")
        with pytest.raises(EpubError, match="backslashes"):
            resolve_package_href("OEBPS/content.opf", "text\\chapter.xhtml")

    def test_content_parser_falls_back_for_malformed_html_without_dropping_short_text(self):
        malformed = (
            b"<html><body><div>Short <span>text</span><script>ignored()</script></div>"
            b"<p>Broken <b>markup</body></html>"
        )
        document = parse_content_document(malformed, "legacy.html")
        segments = extract_text_segments(document, 2)
        assert [item.text for item in segments] == ["Short text", "Broken markup"]
        assert "ignored" not in " ".join(item.protected_text for item in segments)

    def test_content_parser_accepts_legacy_public_doctype_without_entities(self):
        legacy = (
            b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
            b'"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
            b"<html><body><p>Legacy XHTML.</p></body></html>"
        )
        document = parse_content_document(legacy, "legacy.xhtml")
        assert extract_text_segments(document, 1)[0].text == "Legacy XHTML."

    def test_extract_text_segments_preserves_blocks_and_inline_text(self) -> None:
        document = parse_xml(CHAPTER, "chapter.xhtml")
        segments = extract_text_segments(document, 3)
        assert [segment.text for segment in segments] == ["Chapter One", "Hello small world.", "Nested paragraph.", "Plain item."]
        assert segments[0].segment_id == "D0003-S000001"
        assert "/body[1]/h1[1]" in segments[0].element_path


class EpubExtractionTests:
    def test_extract_and_inspect_minimal_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            extracted = base / "extracted"
            names = safe_extract_epub(epub, extracted)
            assert len(names) == 6
            assert discover_opf_path(extracted) == "OEBPS/content.opf"
            manifest = inspect_epub_package(extracted, sha256_file(epub))
            assert manifest.package_version == "3.0"
            assert manifest.metadata.values["title"] == ["Fixture Book"]
            assert len(manifest.manifest_items) == 3
            assert len(manifest.spine) == 1
            assert manifest.documents[0].title == "Chapter One"
            assert len(manifest.documents[0].segments) == 4
            assert [entry.level for entry in manifest.navigation] == [0, 1]
            assert manifest.navigation[0].archive_path == "OEBPS/text/chapter.xhtml"
            assert len(manifest.resources) == 6

    def test_inspection_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            extracted = base / "extracted"
            safe_extract_epub(epub, extracted)
            first = inspect_epub_package(extracted, sha256_file(epub)).model_dump_json()
            second = inspect_epub_package(extracted, sha256_file(epub)).model_dump_json()
            assert first == second

    def test_navigation_falls_back_to_ncx(self) -> None:
        opf = b"""<package xmlns='http://www.idpf.org/2007/opf' version='2.0'>
        <metadata/><manifest>
        <item id='chapter' href='text/chapter.xhtml' media-type='application/xhtml+xml'/>
        <item id='ncx' href='toc.ncx' media-type='application/x-dtbncx+xml'/>
        </manifest><spine toc='ncx'><itemref idref='chapter'/></spine></package>"""
        ncx = b"""<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/'>
        <navMap><navPoint><navLabel><text>Chapter One</text></navLabel>
        <content src='text/chapter.xhtml#start'/></navPoint></navMap></ncx>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = base / "ncx.epub"
            with ZipFile(epub, "w") as archive:
                archive.writestr("mimetype", b"application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr("OEBPS/text/chapter.xhtml", CHAPTER)
                archive.writestr("OEBPS/toc.ncx", ncx)
            extracted = base / "out"
            safe_extract_epub(epub, extracted)
            manifest = inspect_epub_package(extracted, sha256_file(epub))
            assert len(manifest.navigation) == 1
            assert manifest.navigation[0].label == "Chapter One"
            assert manifest.navigation[0].fragment == "start"

    def test_inspection_recovers_document_order_from_toc_when_spine_is_empty(self) -> None:
        opf = b"""<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
        <metadata/><manifest>
        <item id='chapter' href='text/chapter.xhtml'/>
        <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>
        </manifest><spine/></package>"""
        chapter = b"""<html><head><title>Internal title</title></head>
        <body><p>Short chapter text.</p></body></html>"""
        wrapped_nav = NAV.replace(
            b"<a href='text/chapter.xhtml#start'>Chapter One</a>",
            b"<div><a href='text/chapter.xhtml#start'>Chapter One</a></div>",
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = base / "fallback.epub"
            with ZipFile(epub, "w") as archive:
                archive.writestr("mimetype", b"application/epub+zip")
                archive.writestr("META-INF/container.xml", CONTAINER)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr("OEBPS/text/chapter.xhtml", chapter)
                archive.writestr("OEBPS/nav.xhtml", wrapped_nav)
            extracted = base / "out"
            safe_extract_epub(epub, extracted)
            manifest = inspect_epub_package(extracted, sha256_file(epub))
            assert [item.item_id for item in manifest.spine] == ["chapter"]
            assert len(manifest.documents) == 1
            assert manifest.documents[0].title == "Chapter One"
            assert [item.text for item in manifest.documents[0].segments] == ["Short chapter text."]

    def test_discover_opf_uses_existing_preferred_rootfile(self) -> None:
        container = b"""<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
        <rootfiles>
          <rootfile full-path='missing.opf'/>
          <rootfile full-path='OPS/book.opf' media-type='application/oebps-package+xml'/>
        </rootfiles></container>"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "META-INF").mkdir()
            (base / "META-INF" / "container.xml").write_bytes(container)
            (base / "OPS").mkdir()
            (base / "OPS" / "book.opf").write_text("<package/>", encoding="utf-8")
            assert discover_opf_path(base) == "OPS/book.opf"

    def test_safe_extract_rejects_nonempty_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            destination = base / "out"
            destination.mkdir()
            (destination / "existing").write_text("x")
            with pytest.raises(FileExistsError):
                safe_extract_epub(epub, destination)

    def test_safe_extract_rejects_bad_mimetype(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory, "bad.epub")
            with ZipFile(epub, "w") as archive:
                archive.writestr("mimetype", b"text/plain")
            with pytest.raises(EpubError, match="mimetype"):
                safe_extract_epub(epub, Path(directory, "out"))

    def test_safe_extract_canonicalizes_legacy_mimetype_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = base / "legacy.epub"
            with ZipFile(epub, "w") as archive:
                archive.writestr("MIMETYPE", b"\xef\xbb\xbfapplication/epub+zip\r\n")
                archive.writestr("META-INF/container.xml", CONTAINER)
                archive.writestr("OEBPS/content.opf", OPF)
                archive.writestr("OEBPS/text/chapter.xhtml", CHAPTER)
                archive.writestr("OEBPS/nav.xhtml", NAV)
                archive.writestr("OEBPS/styles.css", b"body {}")
            extracted = base / "out"
            names = safe_extract_epub(epub, extracted)
            assert "mimetype" in names
            assert "MIMETYPE" not in names
            assert (extracted / "mimetype").read_bytes() == b"application/epub+zip"

    def test_safe_extract_synthesizes_missing_mimetype_for_legacy_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = base / "legacy.epub"
            with ZipFile(epub, "w") as archive:
                archive.writestr("META-INF/container.xml", CONTAINER)
                archive.writestr("OEBPS/content.opf", OPF)
                archive.writestr("OEBPS/text/chapter.xhtml", CHAPTER)
                archive.writestr("OEBPS/nav.xhtml", NAV)
                archive.writestr("OEBPS/styles.css", b"body {}")
            extracted = base / "out"
            safe_extract_epub(epub, extracted)
            assert (extracted / "mimetype").read_bytes() == b"application/epub+zip"
            manifest = inspect_epub_package(extracted, sha256_file(epub))
            assert len(manifest.documents) == 1

    def test_safe_extract_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory, "bad.epub")
            with ZipFile(epub, "w") as archive:
                archive.writestr("mimetype", b"application/epub+zip")
                archive.writestr("../escape", b"bad")
            with pytest.raises(UnsafeArchiveError):
                safe_extract_epub(epub, Path(directory, "out"))
            assert not Path(directory, "escape").exists()

    def test_safe_extract_rejects_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory, "bad.epub")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with ZipFile(epub, "w") as archive:
                    archive.writestr("mimetype", b"application/epub+zip")
                    archive.writestr("mimetype", b"application/epub+zip")
            with pytest.raises(EpubError, match="duplicate"):
                safe_extract_epub(epub, Path(directory, "out"))

    def test_safe_extract_enforces_entry_and_total_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "fixture.epub")
            with pytest.raises(EpubError, match="too many"):
                safe_extract_epub(epub, base / "one", max_entries=1)
            with pytest.raises(EpubError, match="size limit"):
                safe_extract_epub(epub, base / "two", max_entry_size=2)
            with pytest.raises(EpubError, match="total"):
                safe_extract_epub(epub, base / "three", max_total_size=30)

    def test_discover_opf_requires_container_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with pytest.raises(EpubError, match="container"):
                discover_opf_path(base)
            (base / "META-INF").mkdir()
            (base / "META-INF" / "container.xml").write_bytes(CONTAINER)
            with pytest.raises(EpubError, match="package document"):
                discover_opf_path(base)

    def test_inspect_rejects_unknown_spine_reference(self) -> None:
        bad_opf = OPF.replace(b"idref='chapter'", b"idref='missing'")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "bad.epub", opf=bad_opf)
            extracted = base / "out"
            safe_extract_epub(epub, extracted)
            with pytest.raises(EpubError, match="unknown"):
                inspect_epub_package(extracted, sha256_file(epub))

    def test_inspect_rejects_missing_spine_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            epub = make_epub(base / "bad.epub")
            extracted = base / "out"
            safe_extract_epub(epub, extracted)
            (extracted / "OEBPS" / "text" / "chapter.xhtml").unlink()
            with pytest.raises(EpubError, match="missing"):
                inspect_epub_package(extracted, sha256_file(epub))

