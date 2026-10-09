import io
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

reportlab = pytest.importorskip("reportlab")

from book_agent import pdf_export  # noqa: E402
from book_agent.book_formats import Block, Book, BookFormatError, export_book, read_book  # noqa: E402
from book_agent.pdf_export import check_pdf_output, pdf_output_problem, write_pdf  # noqa: E402
from tests.test_book_formats import _sample_epub  # noqa: E402

# The font that comes with reportlab: the same letters on every machine the tests run on.
FONTS = Path(reportlab.__file__).parent / "fonts"
VERA = FONTS / "Vera.ttf"


def text_of(pdf: Path) -> str:
    from pdfminer.high_level import extract_text

    return extract_text(str(pdf))


def pages_of(pdf: Path) -> int:
    from pdfminer.pdfpage import PDFPage

    with pdf.open("rb") as stream:
        return sum(1 for _ in PDFPage.get_pages(stream))


def png(width: int = 320, height: int = 120) -> bytes:
    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (width, height), (40, 110, 180)).save(data, "PNG")
    return data.getvalue()


def pictures_of(pdf: Path) -> list[tuple[float, float]]:
    """The width and height, in points, of each picture as it is set on its page."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTFigure, LTImage

    def found(item):
        if isinstance(item, (LTImage, LTFigure)) and not hasattr(item, "get_text"):
            if isinstance(item, LTImage) or not list(item):
                yield (item.width, item.height)
                return
        for child in getattr(item, "_objs", []):
            yield from found(child)

    return [size for page in extract_pages(str(pdf)) for size in found(page)]


@pytest.fixture
def folder():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


class WrittenBookTests:
    def test_a_compiled_book_is_written_as_a_pdf_that_reads_back(self, folder):
        epub = _sample_epub(folder)
        pdf = export_book(epub, folder / "zeichen.pdf", "pdf", pdf_font=VERA)
        assert pdf.read_bytes().startswith(b"%PDF-")
        text = " ".join(text_of(pdf).split())
        for words in ("Kapitel I. Die Wissenschaft der Deduktion", "Flasche", "siebenprozentige Lösung", "* * *"):
            assert words in text, words
        # A list's mark is one the font has.
        assert "seine Flasche 1. das Handgelenk 2. der Unterarm" in text and "(cid:" not in text
        # Each chapter starts a page, and every page is numbered.
        assert pages_of(pdf) == 2
        back = read_book(pdf)
        assert (back.title, back.author) == ("Das Zeichen der Vier", "Arthur Conan Doyle")
        assert [block.html for block in back.blocks if block.kind == "h1"][0].startswith("Kapitel I.")
        # The same book gives the same file.
        assert pdf.read_bytes() == export_book(epub, folder / "again.pdf", "pdf", pdf_font=VERA).read_bytes()

    def test_lists_notes_links_code_and_pictures_are_carried(self, folder):
        picture = png()
        book = Book(
            title="The Sign of the Four", author="Arthur Conan Doyle", language="en",
            blocks=[
                Block("p", 'He took his bottle<sup><a href="#note-1">1</a></sup> from the <em>corner</em> of the <strong>mantelpiece</strong>.'),
                Block("img", "A plan of the house", "plan.png"),
                Block("img", "A torn page", "torn.png"),
                Block("img", "", "nothing.png"),
                Block("li", "morphine"), Block("li", 'cocaine, see <a href="https://example.org/times">the Times</a>'),
                Block("oli", "the wrist"), Block("oli", "the forearm"),
                Block("pre", "seven\n  per cent"),
                Block("p", "Which is it <code>to-day</code>?<br/>I asked."),
                Block("note", "A seven-per-cent solution.", "note-1"),
                Block("note", "Its second paragraph.", "note-1"),
            ],
            images={"plan.png": picture, "torn.png": picture[:60]},
        )
        write_pdf(book, folder / "sign.pdf", VERA)
        text = text_of(folder / "sign.pdf")
        # A book with no heading of its own is headed by its title.
        assert text.lstrip().startswith("The Sign of the Four")
        assert "1. the wrist" in " ".join(text.split()) and "2. the forearm" in " ".join(text.split())
        assert "1. A seven-per-cent solution." in " ".join(text.split())
        assert "1. Its second paragraph." not in " ".join(text.split())
        # A picture that cannot be shown is stood in for by its description; one without a description is left out.
        assert "[A torn page]" in text and "[A plan of the house]" not in text
        data = (folder / "sign.pdf").read_bytes()
        assert b"/Subtype /Image" in data and b"https://example.org/times" in data

    @pytest.mark.parametrize("pixels", [(1000, 2000), (2000, 1000), (600, 627), (3000, 3000), (40, 4000), (4000, 40)])
    def test_a_picture_larger_than_the_page_is_made_to_fit_it(self, folder, pixels):
        # A tall plate filled the page to its margins, which is more than the page's frame holds: the book was refused.
        book = Book(
            title="The Sign of the Four", language="en",
            blocks=[Block("h1", "Chapter I"), Block("p", "A plan of the house."), Block("img", "The plan", "plan.png"),
                    Block("p", "He studied it.")],
            images={"plan.png": png(*pixels)},
        )
        write_pdf(book, folder / "plate.pdf", VERA)
        assert "He studied it." in text_of(folder / "plate.pdf")
        (wide, high), = pictures_of(folder / "plate.pdf")
        # A5 is 419.5 by 595.3 points; the text and its pictures stand inside 18 mm margins and the frame's padding.
        assert wide <= 419.53 - 2 * 51.03 - 12 and high <= 595.28 - 51.03 - 62.37 - 12
        assert wide / high == pytest.approx(pixels[0] / pixels[1], rel=0.01)

    def test_a_small_picture_keeps_its_size(self, folder):
        book = Book(language="en", blocks=[Block("img", "A mark", "mark.png")], images={"mark.png": png(160, 80)})
        write_pdf(book, folder / "mark.pdf", VERA)
        assert pictures_of(folder / "mark.pdf") == [pytest.approx((120, 60))]  # 96 pixels to the inch

    def test_a_book_with_nothing_in_it_is_still_a_file(self, folder):
        write_pdf(Book(language="en"), folder / "empty.pdf", VERA)
        assert pages_of(folder / "empty.pdf") == 1


class FontTests:
    def test_a_named_font_must_be_there_be_embeddable_and_have_the_letters(self, folder):
        with pytest.raises(BookFormatError, match="names a file that is not there"):
            check_pdf_output("de", folder / "missing.ttf")
        broken = folder / "broken.ttf"
        broken.write_bytes(b"not a font at all")
        with pytest.raises(BookFormatError, match=r"broken\.ttf cannot be put into a PDF.*output\.pdf_font"):
            check_pdf_output("de", broken)
        with pytest.raises(BookFormatError, match=r"Vera\.ttf lacks \d+ of the book's letters"):
            check_pdf_output("zh", VERA)
        check_pdf_output("de", VERA)
        assert pdf_output_problem("de", VERA) == ""
        assert "lacks" in pdf_output_problem("ja", VERA)

    def test_a_system_font_is_found_by_name_with_its_bold_and_italic(self):
        files = {path.name.lower(): path for path in FONTS.glob("Vera*.ttf")}
        with patch.object(pdf_export, "_system_fonts", return_value=files):
            fonts = pdf_export._choose_fonts("Größe, déjà vu", "de", None)
        assert len({fonts.regular, fonts.bold, fonts.italic, fonts.bold_italic}) == 4
        # A family that has only its regular file stands in for the others with it.
        with patch.object(pdf_export, "_system_fonts", return_value={"vera.ttf": VERA}):
            fonts = pdf_export._choose_fonts("Größe", "de", None)
        assert {fonts.bold, fonts.italic, fonts.bold_italic} == {fonts.regular}

    def test_a_system_without_a_font_for_the_language_says_what_to_set(self):
        with patch.object(pdf_export, "_system_fonts", return_value={}):
            assert "set output.pdf_font to a .ttf or .ttc file" in pdf_output_problem("de")
        # Fonts there are, but none with Chinese letters.
        with patch.object(pdf_export, "_system_fonts", return_value={"vera.ttf": VERA}):
            problem = pdf_output_problem("zh")
        assert "has the letters of" in problem and "Chinese" in problem

    def test_a_font_that_has_most_of_the_letters_is_used(self):
        # Three letters in a thousand that the font lacks do not refuse the book.
        text = "abcdefghijklmnopqrstuvwxyz" * 4 + "的一"
        with patch.object(pdf_export, "_system_fonts", return_value={"vera.ttf": VERA}):
            assert pdf_export._choose_fonts(text, "en", None).regular

    def test_the_folders_searched_include_the_font_reportlab_brings(self):
        assert FONTS in pdf_export._font_folders()
        pdf_export._system_fonts.cache_clear()
        try:
            assert pdf_export._system_fonts()["vera.ttf"].is_file()
        finally:
            pdf_export._system_fonts.cache_clear()


class RefusalTests:
    @pytest.mark.parametrize("language, script", [("ar", "Arabic"), ("he", "Hebrew"), ("hi", "Devanagari"), ("th", "Thai")])
    def test_scripts_that_join_or_run_right_to_left_are_refused_with_the_reason(self, language, script):
        problem = pdf_output_problem(language, VERA)
        assert f"cannot be written in {script} script" in problem and "choose another output.format" in problem

    def test_without_reportlab_the_message_says_what_to_install(self, folder):
        with patch.dict(sys.modules, {"reportlab": None}):
            assert pdf_output_problem("de", VERA) == pdf_export.NEEDS_REPORTLAB
            with pytest.raises(BookFormatError, match="pip install reportlab"):
                write_pdf(Book(language="de"), folder / "book.pdf", VERA)
        assert "pip install reportlab" in pdf_export.NEEDS_REPORTLAB

    def test_a_language_the_profiles_do_not_know_is_tried_with_the_latin_fonts(self):
        assert pdf_output_problem("", VERA) == ""
