import tempfile
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile

from scripts.create_epub_spine_subset import OPF_NS, create_spine_subset

CONTAINER = b"""<?xml version='1.0'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
  <rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles>
</container>"""

OPF = b"""<?xml version='1.0'?>
<package xmlns='http://www.idpf.org/2007/opf' version='2.0'>
  <manifest>
    <item id='one' href='one.xhtml' media-type='application/xhtml+xml'/>
    <item id='two' href='two.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine><itemref idref='one'/><itemref idref='two'/></spine>
</package>"""


def test_create_spine_subset_preserves_package_and_selected_order() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory, "source.epub")
        output = Path(directory, "pilot.epub")
        with ZipFile(source, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
            archive.writestr("META-INF/container.xml", CONTAINER)
            archive.writestr("OEBPS/content.opf", OPF)
            archive.writestr("OEBPS/one.xhtml", "one")
            archive.writestr("OEBPS/two.xhtml", "two")

        create_spine_subset(source, output, ["two"])

        with ZipFile(output) as archive:
            assert archive.infolist()[0].filename == "mimetype"
            assert archive.infolist()[0].compress_type == ZIP_STORED
            assert archive.read("OEBPS/one.xhtml") == b"one"
            package = ElementTree.fromstring(archive.read("OEBPS/content.opf"))

    spine = package.find(f"{{{OPF_NS}}}spine")
    assert spine is not None
    assert [item.get("idref") for item in spine] == ["two"]
