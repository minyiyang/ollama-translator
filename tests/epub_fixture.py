from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


CONTAINER = b"""<?xml version='1.0'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
  <rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles>
</container>"""

OPF = b"""<?xml version='1.0'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='book-id'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:identifier id='book-id'>fixture-id</dc:identifier>
    <dc:title>Fixture Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id='chapter' href='text/chapter.xhtml' media-type='application/xhtml+xml'/>
    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>
    <item id='css' href='styles.css' media-type='text/css'/>
  </manifest>
  <spine><itemref idref='chapter'/></spine>
</package>"""

CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>Chapter One</h1><p>Hello <em>small</em> world.</p>
<ul><li><p>Nested paragraph.</p></li><li>Plain item.</li></ul></body></html>"""

NAV = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>
<body><nav epub:type='toc'><ol><li><a href='text/chapter.xhtml#start'>Chapter One</a>
<ol><li><a href='text/chapter.xhtml#part'>Part</a></li></ol></li></ol></nav></body></html>"""


def make_epub(path: Path, *, opf: bytes = OPF, chapter: bytes = CHAPTER) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/text/chapter.xhtml", chapter, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", NAV, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/styles.css", b"body {}", compress_type=ZIP_DEFLATED)
    return path

