"""Deterministic RTF extraction, reconstruction, and validation.

The adapter intentionally normalizes rich formatting to chapter and paragraph
structure and emits EPUB. It does not claim lossless RTF formatting
preservation; the native EPUB adapter remains lossless for package resources
and markup.
"""

from __future__ import annotations

import os
import re
import tempfile
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from striprtf.striprtf import rtf_to_text as strip_rtf_to_text

from .epub import (
    ChapterDocument,
    EpubMetadata,
    EpubPackageManifest,
    TextSegment,
    inspect_epub_package,
    normalize_text,
    safe_extract_epub,
)
from .epub_compile import (
    EpubCompilationError,
    EpubCompilationReport,
    EpubValidationReport,
)
from .hashing import sha256_bytes, sha256_file
from .repair import RepairedDocument


class RtfError(ValueError):
    """The RTF input is invalid or exceeds the bounded parser contract."""


_MAX_RTF_BYTES = 64 * 1024 * 1024
_CHAPTER_LINE = re.compile(
    r"^(?:(?:(?:chapter|глава)\s+(?:\d+|[ivxlcdm]+)|"
    r"(?:part|часть)\s+(?:\d+|[ivxlcdm]+))"
    r"(?:\s*[:.\-\u2013\u2014]?\s*.*)?|"
    r"\d{1,4}[.)]\s+\S.*|(?-i:[А-ЯЁ][А-ЯЁ\s\-\u2013\u2014:]{3,}))$",
    flags=re.IGNORECASE,
)


def is_chapter_heading(text: str) -> bool:
    """Return whether a line matches a conservative reference-script heading rule."""
    return _CHAPTER_LINE.fullmatch(text.strip()) is not None


def rtf_to_text(data: bytes) -> str:
    """Extract visible text with striprtf under a bounded input contract."""
    if len(data) > _MAX_RTF_BYTES:
        raise RtfError(f"RTF exceeds {_MAX_RTF_BYTES} byte safety limit")
    source = data.decode("latin-1")
    if not re.match(r"^\s*\{\\rtf\d", source):
        raise RtfError("source is not an RTF document")
    try:
        text = strip_rtf_to_text(source, errors="strict")
    except (LookupError, UnicodeError, ValueError) as error:
        raise RtfError(f"RTF text decoding failed: {error}") from error
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le", errors="surrogatepass"
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def inspect_rtf_document(path: str | Path, source_sha256: str) -> EpubPackageManifest:
    """Map an RTF file into the pipeline's format-neutral document contract."""
    source = Path(path)
    text = rtf_to_text(source.read_bytes())
    documents = _build_documents(text, source.stem)
    if not documents:
        raise RtfError("RTF contains no translatable text")
    return EpubPackageManifest(
        source_format="rtf",
        source_sha256=source_sha256,
        opf_path="",
        package_version="1",
        unique_identifier=source_sha256,
        metadata=EpubMetadata(values={"format": ["rtf"], "title": [source.stem]}),
        manifest_items=[],
        spine=[],
        navigation=[],
        documents=documents,
        resources=[],
    )


def compile_rtf_document(
    manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
    output_path: str | Path,
) -> EpubCompilationReport:
    """Package translated RTF chapters as a deterministic EPUB 3 document."""
    if manifest.source_format != "rtf":
        raise EpubCompilationError("RTF compiler received a non-RTF manifest")
    if not manifest.documents:
        raise EpubCompilationError("RTF manifest contains no documents")
    expected = [item.manifest_id for item in manifest.documents]
    actual = [item.document.manifest_id for item in repaired_documents]
    if actual != expected:
        raise EpubCompilationError(
            f"repaired document order or set differs: expected {expected}, received {actual}"
        )
    output = Path(output_path)
    if output.suffix.casefold() != ".epub":
        raise EpubCompilationError("RTF-derived output must use the .epub suffix")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="rtf-epub-build-", dir=output.parent
    ) as directory:
        root = Path(directory) / "package"
        _write_rtf_epub_package(root, manifest, repaired_documents)
        temporary = Path(directory) / "compiled.epub"
        _write_epub_zip(root, temporary)
        os.replace(temporary, output)
    return EpubCompilationReport(
        output_path=str(output.resolve()),
        document_count=len(repaired_documents),
        segment_count=sum(len(item.document.segments) for item in repaired_documents),
        resource_count=len(repaired_documents) + 4,
        output_sha256=sha256_file(output),
        output_size=output.stat().st_size,
    )


def validate_compiled_rtf(
    rtf_path: str | Path,
    source_manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
) -> EpubValidationReport:
    """Validate an RTF-derived EPUB and compare its visible translated text."""
    errors: list[str] = []
    output = Path(rtf_path)
    try:
        with ZipFile(output) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos if not item.is_dir()]
            if not infos or infos[0].filename != "mimetype":
                errors.append("mimetype is not the first archive entry")
            elif infos[0].compress_type != ZIP_STORED:
                errors.append("mimetype entry is compressed")
            if len(names) != len(set(names)):
                errors.append("archive contains duplicate file entries")
    except (BadZipFile, OSError) as error:
        return EpubValidationReport(
            passed=False,
            errors=[f"invalid RTF-derived EPUB ZIP: {error}"],
            document_count=0,
            segment_count=0,
            resource_count=0,
        )
    if source_manifest.source_format != "rtf":
        errors.append("source manifest format is not RTF")
    expected_ids = [item.manifest_id for item in source_manifest.documents]
    actual_ids = [item.document.manifest_id for item in repaired_documents]
    if actual_ids != expected_ids:
        errors.append("repaired document order or set differs")
    inspected = None
    with tempfile.TemporaryDirectory(prefix="rtf-epub-validate-") as directory:
        root = Path(directory)
        try:
            safe_extract_epub(output, root)
            inspected = inspect_epub_package(root, sha256_file(output))
        except (OSError, ValueError) as error:
            errors.append(f"compiled EPUB inspection failed: {error}")
        if inspected is not None:
            if len(inspected.documents) != len(repaired_documents):
                errors.append("compiled EPUB chapter count differs")
            for position, repaired in enumerate(repaired_documents):
                if position >= len(inspected.documents):
                    break
                actual_segments = inspected.documents[position].segments
                expected_segments = repaired.document.segments
                if len(actual_segments) != len(expected_segments):
                    errors.append(
                        f"compiled EPUB segment count differs for {repaired.document.manifest_id}"
                    )
                    continue
                for actual, expected in zip(
                    actual_segments, expected_segments, strict=True
                ):
                    if normalize_text(actual.text) != normalize_text(expected.translated_text):
                        errors.append(
                            f"compiled EPUB text differs for {expected.segment_id}"
                        )
    return EpubValidationReport(
        passed=not errors,
        errors=errors,
        document_count=len(inspected.documents) if inspected is not None else 0,
        segment_count=(
            sum(len(item.segments) for item in inspected.documents)
            if inspected is not None
            else 0
        ),
        resource_count=len(inspected.resources) if inspected is not None else 0,
    )


def _build_documents(text: str, source_title: str) -> list[ChapterDocument]:
    paragraphs = [item.strip() for item in re.split(r"\n+", text) if item.strip()]
    if not paragraphs:
        return []
    starts = [index for index, item in enumerate(paragraphs) if is_chapter_heading(item)]
    ranges: list[tuple[int, int]] = []
    if not starts:
        ranges.append((0, len(paragraphs)))
    else:
        if starts[0] > 0:
            ranges.append((0, starts[0]))
        ranges.extend(
            (start, starts[position + 1] if position + 1 < len(starts) else len(paragraphs))
            for position, start in enumerate(starts)
        )
    documents: list[ChapterDocument] = []
    for order, (start, end) in enumerate(ranges):
        values = paragraphs[start:end]
        manifest_id = f"rtf-{order:04d}"
        segments = [
            TextSegment(
                segment_id=f"D{order:04d}-S{position:06d}",
                element_path=f"paragraph[{position}]",
                tag="p",
                text=value,
                protected_text=value,
            )
            for position, value in enumerate(values, start=1)
        ]
        documents.append(
            ChapterDocument(
                order=order,
                manifest_id=manifest_id,
                archive_path=f"rtf/{manifest_id}.txt",
                media_type="text/rtf",
                linear=True,
                title=values[0] if is_chapter_heading(values[0]) else source_title,
                source_sha256=sha256_bytes("\n".join(values).encode("utf-8")),
                segments=segments,
            )
        )
    return documents


def _write_rtf_epub_package(
    root: Path,
    manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
) -> None:
    (root / "META-INF").mkdir(parents=True)
    (root / "OEBPS" / "text").mkdir(parents=True)
    (root / "mimetype").write_bytes(b"application/epub+zip")
    (root / "META-INF" / "container.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
        "<rootfiles><rootfile full-path='OEBPS/content.opf' "
        "media-type='application/oebps-package+xml'/></rootfiles></container>\n",
        encoding="utf-8",
    )
    title = manifest.metadata.values.get("title", ["Translated document"])[0]
    language = repaired_documents[0].document.direction.target_language.value
    manifest_items = [
        "<item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>",
    ]
    spine_items = []
    nav_items = []
    for repaired in repaired_documents:
        document = repaired.document
        href = f"text/{document.manifest_id}.xhtml"
        manifest_items.append(
            f"<item id='{escape(document.manifest_id, quote=True)}' "
            f"href='{escape(href, quote=True)}' media-type='application/xhtml+xml'/>"
        )
        spine_items.append(
            f"<itemref idref='{escape(document.manifest_id, quote=True)}'/>"
        )
        source_document = manifest.documents[document.order]
        translated_title = (
            document.segments[0].translated_text
            if document.segments
            and source_document.segments
            and source_document.title == source_document.segments[0].text
            else source_document.title or document.manifest_id
        )
        nav_items.append(
            f"<li><a href='{escape(href, quote=True)}'>"
            f"{escape(translated_title)}</a></li>"
        )
        paragraphs = "\n".join(
            f"<p>{escape(segment.translated_text)}</p>" for segment in document.segments
        )
        (root / "OEBPS" / "text" / f"{document.manifest_id}.xhtml").write_text(
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<!DOCTYPE html>\n"
            f"<html xmlns='http://www.w3.org/1999/xhtml' lang='{language}' "
            f"xml:lang='{language}'><head>"
            f"<title>{escape(translated_title)}</title></head>"
            f"<body>{paragraphs}</body></html>\n",
            encoding="utf-8",
        )
    (root / "OEBPS" / "nav.xhtml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<!DOCTYPE html>\n"
        "<html xmlns='http://www.w3.org/1999/xhtml' "
        f"xmlns:epub='http://www.idpf.org/2007/ops' lang='{language}' "
        f"xml:lang='{language}'><head>"
        f"<title>{escape(title)}</title></head><body><nav epub:type='toc'>"
        f"<ol>{''.join(nav_items)}</ol></nav></body></html>\n",
        encoding="utf-8",
    )
    (root / "OEBPS" / "content.opf").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<package xmlns='http://www.idpf.org/2007/opf' version='3.0' "
        "unique-identifier='book-id'><metadata "
        "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        f"<dc:identifier id='book-id'>{escape(manifest.unique_identifier)}</dc:identifier>"
        f"<dc:title>{escape(title)}</dc:title><dc:language>{language}</dc:language>"
        "<meta property='dcterms:modified'>1980-01-01T00:00:00Z</meta>"
        "</metadata><manifest>"
        f"{''.join(manifest_items)}</manifest><spine>{''.join(spine_items)}</spine>"
        "</package>\n",
        encoding="utf-8",
    )


def _write_epub_zip(package_root: Path, output: Path) -> None:
    files = sorted(path for path in package_root.rglob("*") if path.is_file())
    relative = {path.relative_to(package_root).as_posix(): path for path in files}
    ordered = ["mimetype", *[item for item in sorted(relative) if item != "mimetype"]]
    with ZipFile(output, "w") as archive:
        for name in ordered:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            archive.writestr(
                info,
                relative[name].read_bytes(),
                compress_type=info.compress_type,
                compresslevel=None if name == "mimetype" else 9,
            )
