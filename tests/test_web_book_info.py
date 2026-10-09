import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from book_agent.web import book_info as info_api
from book_agent.web.book_info import allowed_book, book_cover, book_info

CONTAINER = (
    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
    "</container>"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def package(manifest: str, metadata: str = "") -> str:
    return (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title> The Qel Road </dc:title><dc:creator>A. Vrax</dc:creator><dc:creator>B. Wright</dc:creator>"
        f"<dc:language>en</dc:language>{metadata}</metadata>"
        f"<manifest>{manifest}</manifest></package>"
    )


def epub(directory: str, opf: str, files: dict[str, bytes] | None = None, container: str = CONTAINER) -> Path:
    path = Path(directory) / "book.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for name, data in (files or {}).items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def folder():
    with tempfile.TemporaryDirectory() as directory:
        yield directory


class AllowedBookTests:
    def test_a_book_under_a_known_folder_is_allowed(self, folder):
        book = Path(folder) / "samples" / "book.epub"
        book.parent.mkdir()
        book.write_bytes(b"x")
        assert allowed_book(str(book), [Path(folder) / "samples"]) == book.resolve()

    def test_other_files_are_refused(self, folder):
        roots = [Path(folder) / "samples"]
        roots[0].mkdir()
        outside = Path(folder) / "book.epub"
        outside.write_bytes(b"x")
        other_kind = roots[0] / "notes.exe"
        other_kind.write_bytes(b"x")
        for path in (outside, other_kind, roots[0] / "missing.epub"):
            with pytest.raises(ValueError, match="unknown book file"):
                allowed_book(str(path), roots)


class EpubInfoTests:
    def test_title_authors_language_and_the_marked_cover(self, folder):
        path = epub(
            folder,
            package('<item id="art" href="images/front.png" media-type="image/png" properties="cover-image"/>'),
            {"OEBPS/images/front.png": PNG},
        )
        info = book_info(path)
        assert (info["title"], info["authors"], info["language"]) == ("The Qel Road", ["A. Vrax", "B. Wright"], "en")
        assert info["format"] == "epub" and info["size"] == path.stat().st_size and info["has_cover"]
        assert book_cover(path) == (PNG, "image/png")

    def test_cover_named_by_an_epub2_meta_and_reached_through_parent_folders(self, folder):
        path = epub(
            folder,
            package(
                '<item id="art" href="../shared/./front.jpg#top" media-type="application/octet-stream"/>',
                '<meta name="cover" content="art"/>',
            ),
            {"shared/front.jpg": PNG},
        )
        assert book_info(path)["has_cover"]
        # The file's own type decides when the package gives none that is an image.
        assert book_cover(path) == (PNG, "image/jpeg")

    def test_cover_guessed_from_an_image_named_cover(self, folder):
        path = epub(
            folder,
            package(
                '<item id="text" href="cover.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="img1" href="Cover.gif" media-type="image/gif"/>'
            ),
            {"OEBPS/Cover.gif": PNG},
        )
        assert book_info(path)["has_cover"]
        assert book_cover(path)[1] == "image/gif"

    def test_a_book_without_a_cover(self, folder):
        path = epub(folder, package('<item id="text" href="one.xhtml" media-type="application/xhtml+xml"/>'))
        assert not book_info(path)["has_cover"]
        with pytest.raises(ValueError, match="no cover image"):
            book_cover(path)

    def test_a_cover_the_archive_lacks_is_not_offered(self, folder):
        path = epub(folder, package('<item id="art" href="front.png" media-type="image/png" properties="cover-image"/>'))
        assert not book_info(path)["has_cover"]
        with pytest.raises(KeyError):
            book_cover(path)

    def test_a_vector_cover_is_never_served(self, folder):
        path = epub(
            folder,
            package('<item id="art" href="front.svg" media-type="image/svg+xml" properties="cover-image"/>'),
            {"OEBPS/front.svg": b"<svg/>"},
        )
        assert not book_info(path)["has_cover"]
        with pytest.raises(ValueError, match="not a raster image"):
            book_cover(path)

    def test_an_oversized_cover_is_refused(self, folder):
        path = epub(
            folder,
            package('<item id="art" href="front.png" media-type="image/png" properties="cover-image"/>'),
            {"OEBPS/front.png": PNG},
        )
        with patch.object(info_api, "_MAX_COVER_BYTES", 8), pytest.raises(ValueError, match="too large"):
            book_cover(path)

    def test_unreadable_archives_carry_a_warning(self, folder):
        nameless = epub(folder, package(""), container="<container><rootfiles/></container>")
        info = book_info(nameless)
        assert "names no package document" in info["warning"] and info["title"] == ""

        broken = Path(folder) / "broken.epub"
        broken.write_bytes(b"not a zip archive")
        assert "could not read EPUB metadata" in book_info(broken)["warning"]

        empty = Path(folder) / "empty.epub"
        with zipfile.ZipFile(empty, "w"):
            pass
        assert "could not read EPUB metadata" in book_info(empty)["warning"]


class OtherFormatTests:
    def test_rtf_title_and_author_come_from_its_header(self, folder):
        path = Path(folder) / "book.rtf"
        path.write_bytes(rb"{\rtf1{\info{\title The Qel Road}{\author A. Vrax}}\pard Text\par}")
        info = book_info(path)
        assert (info["format"], info["title"], info["authors"]) == ("rtf", "The Qel Road", ["A. Vrax"])

        bare = Path(folder) / "bare.rtf"
        bare.write_bytes(rb"{\rtf1\pard Text\par}")
        assert (book_info(bare)["title"], book_info(bare)["authors"]) == ("", [])

    def test_subtitles_report_their_cues_and_length(self, folder):
        path = Path(folder) / "film.srt"
        path.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHello.\n\n2\n00:01:05,000 --> 00:01:07,500\nGoodbye.\n",
            encoding="utf-8",
        )
        info = book_info(path)
        assert (info["title"], info["cues"]) == ("film", 2)
        assert info["duration"].startswith("00:01:07") or info["duration"].startswith("0:01:07")

        with patch.object(info_api, "read_subtitles", side_effect=ValueError("no cues")):
            assert book_info(path)["warning"] == "could not read the subtitle file: no cues"

    def test_pdf_title_and_author_as_the_file_records_them(self, folder):
        path = Path(folder) / "book.pdf"
        path.write_bytes(b"%PDF-1.4")
        with patch.object(info_api, "pdf_title_and_author", return_value=("The Qel Road", "A. Vrax")):
            info = book_info(path)
        assert (info["title"], info["authors"]) == ("The Qel Road", ["A. Vrax"])
        with patch.object(info_api, "pdf_title_and_author", return_value=("The Qel Road", "")):
            assert book_info(path)["authors"] == []
        with patch.object(info_api, "pdf_title_and_author", side_effect=ValueError("encrypted")):
            assert book_info(path)["warning"] == "could not read the PDF: encrypted"

    def test_converted_books_are_read_for_their_metadata(self, folder):
        path = Path(folder) / "book.txt"
        path.write_text("The Qel Road\n\nIt began in the rain.\n", encoding="utf-8")
        book = SimpleNamespace(title="The Qel Road", author="A. Vrax", language="en")
        with patch.object(info_api, "read_book", return_value=book):
            info = book_info(path)
        assert (info["title"], info["authors"], info["language"]) == ("The Qel Road", ["A. Vrax"], "en")
        with patch.object(info_api, "read_book", return_value=SimpleNamespace(title="T", author="", language="")):
            assert book_info(path)["authors"] == []
        with patch.object(info_api, "read_book", side_effect=ValueError("empty file")):
            assert book_info(path)["warning"] == "could not read the book: empty file"
