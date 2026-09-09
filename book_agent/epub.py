"""Safe EPUB extraction and deterministic package inspection."""

from __future__ import annotations

import mimetypes
import re
import stat
from typing import Literal
from html import escape
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from pydantic import BaseModel, ConfigDict, Field

from .atomic_io import atomic_write_bytes
from .hashing import sha256_file


class EpubError(ValueError):
    """Base error for invalid or unsafe EPUB input."""


class UnsafeArchiveError(EpubError):
    """An archive member could escape extraction or behave as a link."""


class EpubMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, list[str]] = Field(default_factory=dict)


class ManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    href: str
    archive_path: str
    media_type: str
    properties: list[str] = Field(default_factory=list)
    exists: bool


class SpineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    idref: str
    linear: bool
    item_id: str
    archive_path: str
    media_type: str


class NavigationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    href: str
    archive_path: str
    fragment: str = ""
    level: int = 0


class TextSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    element_path: str
    tag: str
    text: str
    protected_text: str = ""
    inline_markers: list["InlineMarker"] = Field(default_factory=list)


class InlineMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker_id: str
    element_path: str
    tag: str


class ChapterDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int
    manifest_id: str
    archive_path: str
    media_type: str
    linear: bool
    title: str = ""
    source_sha256: str
    segments: list[TextSegment] = Field(default_factory=list)


class ResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive_path: str
    manifest_id: str | None = None
    media_type: str
    properties: list[str] = Field(default_factory=list)
    size: int
    sha256: str
    in_spine: bool = False


class EpubPackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_format: Literal["epub", "rtf"] = "epub"
    source_sha256: str
    opf_path: str
    package_version: str
    unique_identifier: str
    metadata: EpubMetadata
    manifest_items: list[ManifestItem]
    spine: list[SpineItem]
    navigation: list[NavigationEntry]
    documents: list[ChapterDocument]
    resources: list[ResourceRecord]


_BLOCK_TAGS = {
    "body", "main", "article", "section", "div", "aside", "header", "footer",
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote",
    "dt", "dd", "figcaption", "caption", "td", "th", "address", "pre",
}
_IGNORED_TEXT_TAGS = {"script", "style", "noscript", "template", "nav"}
_DOCUMENT_MEDIA_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "application/xml",
    "text/xml",
}
_ENTITY_DECLARATION = re.compile(br"<!\s*ENTITY", flags=re.IGNORECASE)
_DOCTYPE_DECLARATION = re.compile(br"<!\s*DOCTYPE[^>]*>", flags=re.IGNORECASE)
_SAFE_HTML_DOCTYPE = re.compile(br"<!\s*DOCTYPE\s+html\s*>", flags=re.IGNORECASE)
_EPUB_MIMETYPE = b"application/epub+zip"


def local_name(tag: str) -> str:
    """Return an XML tag or attribute name without its namespace."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def parse_xml(data: bytes, source_name: str) -> ET.Element:
    """Parse XML after rejecting DTD/entity declarations."""
    if _ENTITY_DECLARATION.search(data):
        raise EpubError(f"entity declarations are not allowed: {source_name}")
    for declaration in _DOCTYPE_DECLARATION.findall(data):
        if _SAFE_HTML_DOCTYPE.fullmatch(declaration) is None:
            raise EpubError(f"unsafe document type declaration: {source_name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise EpubError(f"invalid XML in {source_name}: {error}") from error


def parse_content_document(data: bytes, source_name: str) -> ET.Element:
    """Parse XHTML strictly, then safely normalize malformed HTML as a fallback."""
    try:
        return parse_xml_compat(data, source_name)
    except EpubError as error:
        if not str(error).startswith("invalid XML"):
            raise
    soup = BeautifulSoup(data, "html.parser")
    root = soup.find("html") or soup.find(True)
    if root is None:
        raise EpubError(f"HTML document has no root element: {source_name}")
    return _beautifulsoup_to_element(root)


def parse_xml_compat(data: bytes, source_name: str) -> ET.Element:
    """Parse legacy package XML after safely removing non-semantic doctypes."""
    if _ENTITY_DECLARATION.search(data):
        raise EpubError(f"entity declarations are not allowed: {source_name}")
    sanitized = _DOCTYPE_DECLARATION.sub(b"", data)
    try:
        return ET.fromstring(sanitized)
    except ET.ParseError as error:
        raise EpubError(f"invalid XML in {source_name}: {error}") from error


def _beautifulsoup_to_element(tag: Tag) -> ET.Element:
    attributes = {
        str(key): " ".join(value) if isinstance(value, list) else str(value)
        for key, value in tag.attrs.items()
    }
    element = ET.Element(str(tag.name), attributes)
    previous = None
    for child in tag.children:
        if isinstance(child, Comment):
            converted = ET.Comment(str(child))
            element.append(converted)
            previous = converted
        elif isinstance(child, Tag):
            converted = _beautifulsoup_to_element(child)
            element.append(converted)
            previous = converted
        elif isinstance(child, NavigableString):
            value = str(child)
            if previous is None:
                element.text = (element.text or "") + value
            else:
                previous.tail = (previous.tail or "") + value
    return element


def validate_archive_path(name: str) -> PurePosixPath:
    """Validate and normalize one EPUB ZIP member path."""
    if "\\" in name:
        raise UnsafeArchiveError(f"archive member uses backslashes: {name}")
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise UnsafeArchiveError(f"unsafe archive member path: {name}")
    normalized = PurePosixPath(*(part for part in path.parts if part not in ("", ".")))
    if not normalized.parts:
        raise UnsafeArchiveError(f"empty archive member path: {name}")
    return normalized


def safe_extract_epub(
    epub_path: str | Path,
    destination: str | Path,
    *,
    max_entries: int = 20_000,
    max_entry_size: int = 256 * 1024 * 1024,
    max_total_size: int = 2 * 1024 * 1024 * 1024,
) -> list[str]:
    """Validate and extract an EPUB without traversal, links, or ZIP bombs."""
    source = Path(epub_path)
    target = Path(destination)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"extraction destination is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    try:
        archive = ZipFile(source)
    except (BadZipFile, OSError) as error:
        raise EpubError(f"invalid EPUB ZIP: {source}") from error
    extracted: list[str] = []
    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise EpubError("EPUB contains too many archive entries")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise EpubError("EPUB contains duplicate archive member names")
        mimetype_names = [
            name for name in names
            if len(PurePosixPath(name).parts) == 1 and name.casefold() == "mimetype"
        ]
        if len(mimetype_names) > 1:
            raise EpubError("EPUB contains multiple mimetype entries")
        mimetype_name = mimetype_names[0] if mimetype_names else ""
        if mimetype_name:
            declared = archive.read(mimetype_name)
            normalized = declared.removeprefix(b"\xef\xbb\xbf").strip()
            if normalized != _EPUB_MIMETYPE:
                raise EpubError("EPUB mimetype entry is invalid")
        total_size = 0
        for info in infos:
            member = validate_archive_path(info.filename)
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise UnsafeArchiveError(f"symbolic links are not allowed: {info.filename}")
            if info.file_size > max_entry_size:
                raise EpubError(f"EPUB member exceeds size limit: {info.filename}")
            total_size += info.file_size
            if total_size > max_total_size:
                raise EpubError("EPUB exceeds total uncompressed size limit")
            output_member = (
                PurePosixPath("mimetype")
                if mimetype_name and info.filename == mimetype_name
                else member
            )
            output = target.joinpath(*output_member.parts)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            payload = (
                _EPUB_MIMETYPE
                if mimetype_name and info.filename == mimetype_name
                else archive.read(info)
            )
            atomic_write_bytes(output, payload)
            extracted.append(output_member.as_posix())
        if not mimetype_name:
            atomic_write_bytes(target / "mimetype", _EPUB_MIMETYPE)
            extracted.insert(0, "mimetype")
    return extracted


def discover_opf_path(extracted_root: str | Path) -> str:
    """Read container.xml and return the first existing package rootfile."""
    root = Path(extracted_root)
    container_path = root / "META-INF" / "container.xml"
    if not container_path.is_file():
        raise EpubError("EPUB container.xml is missing")
    container = parse_xml(container_path.read_bytes(), "META-INF/container.xml")
    rootfiles = container.findall(".//{*}rootfile")
    if not rootfiles:
        raise EpubError("EPUB container has no rootfile")
    candidates = sorted(
        rootfiles,
        key=lambda item: item.attrib.get("media-type")
        != "application/oebps-package+xml",
    )
    missing = []
    for rootfile in candidates:
        full_path = rootfile.attrib.get("full-path", "").strip()
        if not full_path:
            continue
        opf = validate_archive_path(full_path).as_posix()
        if (root / Path(*PurePosixPath(opf).parts)).is_file():
            return opf
        missing.append(opf)
    if missing:
        raise EpubError(f"package document is missing: {', '.join(missing)}")
    raise EpubError("EPUB container has no usable rootfile")


def resolve_package_href(opf_path: str, href: str) -> tuple[str, str]:
    """Resolve a package-relative href to archive path and fragment."""
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise EpubError(f"external manifest href is not supported: {href}")
    decoded = unquote(parsed.path)
    if "\\" in decoded:
        raise EpubError(f"href uses backslashes: {href}")
    base = PurePosixPath(opf_path).parent
    if not decoded:
        combined = PurePosixPath(opf_path)
    elif decoded.startswith("/"):
        combined = PurePosixPath(decoded.lstrip("/"))
    else:
        combined = base.joinpath(decoded)
    parts: list[str] = []
    for part in combined.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise EpubError(f"href escapes EPUB root: {href}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise EpubError(f"empty resolved href: {href}")
    return PurePosixPath(*parts).as_posix(), unquote(parsed.fragment)


def normalize_text(value: str) -> str:
    """Normalize XML text whitespace without changing words or punctuation."""
    return " ".join(value.replace("\u00a0", " ").split())


def extract_text_segments(document: ET.Element, document_order: int) -> list[TextSegment]:
    """Extract leaf block text with stable structural identifiers and XML paths."""
    segments: list[TextSegment] = []

    def visit(element: ET.Element, path: str) -> None:
        tag = local_name(element.tag)
        if element.attrib.get("data-book-agent-generated") == "chapter-heading":
            return
        if tag in _IGNORED_TEXT_TAGS:
            return
        descendant_blocks = any(
            local_name(descendant.tag) in _BLOCK_TAGS
            for descendant in _visible_descendants(element)
            if descendant is not element
        )
        text = normalize_text("".join(_visible_itertext(element)))
        if tag in _BLOCK_TAGS and not descendant_blocks and text:
            segment_id = f"D{document_order:04d}-S{len(segments) + 1:06d}"
            protected_text, inline_markers = build_protected_inline_text(element, path)
            segments.append(
                TextSegment(
                    segment_id=segment_id,
                    element_path=path,
                    tag=tag,
                    text=text,
                    protected_text=protected_text,
                    inline_markers=inline_markers,
                )
            )
        counts: defaultdict[str, int] = defaultdict(int)
        for child in list(element):
            child_tag = local_name(child.tag)
            if not child_tag:
                continue
            counts[child_tag] += 1
            visit(child, f"{path}/{child_tag}[{counts[child_tag]}]")

    visit(document, f"/{local_name(document.tag)}[1]")
    return segments


def build_protected_inline_text(
    block: ET.Element,
    block_path: str,
) -> tuple[str, list[InlineMarker]]:
    """Render block text with stable markers around every original inline child."""
    markers: list[InlineMarker] = []

    def render(element: ET.Element, path: str) -> str:
        result = escape(element.text or "", quote=False)
        counts: defaultdict[str, int] = defaultdict(int)
        for child in list(element):
            child_tag = local_name(child.tag)
            if not child_tag or child_tag in _IGNORED_TEXT_TAGS:
                result += escape(child.tail or "", quote=False)
                continue
            counts[child_tag] += 1
            child_path = f"{path}/{child_tag}[{counts[child_tag]}]"
            marker_id = f"I{len(markers):03d}"
            markers.append(
                InlineMarker(
                    marker_id=marker_id,
                    element_path=child_path,
                    tag=child_tag,
                )
            )
            result += f"<{marker_id}>{render(child, child_path)}</{marker_id}>"
            result += escape(child.tail or "", quote=False)
        return result

    rendered = " ".join(render(block, block_path).replace("\u00a0", " ").split())
    return rendered, markers


def _visible_descendants(element: ET.Element):
    yield element
    for child in list(element):
        if local_name(child.tag) in _IGNORED_TEXT_TAGS:
            continue
        yield from _visible_descendants(child)


def _visible_itertext(element: ET.Element):
    if element.text:
        yield element.text
    for child in list(element):
        if local_name(child.tag) not in _IGNORED_TEXT_TAGS:
            yield from _visible_itertext(child)
        if child.tail:
            yield child.tail


def inspect_epub_package(
    extracted_root: str | Path,
    source_sha256: str,
) -> EpubPackageManifest:
    """Inspect an extracted EPUB and return a deterministic typed manifest."""
    root = Path(extracted_root)
    opf_path = discover_opf_path(root)
    opf_file = root.joinpath(*PurePosixPath(opf_path).parts)
    package = parse_xml_compat(opf_file.read_bytes(), opf_path)
    metadata_values: defaultdict[str, list[str]] = defaultdict(list)
    metadata_element = package.find("./{*}metadata")
    if metadata_element is not None:
        for element in list(metadata_element):
            text = normalize_text("".join(element.itertext()))
            if text:
                metadata_values[local_name(element.tag)].append(text)

    manifest_items: list[ManifestItem] = []
    items_by_id: dict[str, ManifestItem] = {}
    for element in package.findall("./{*}manifest/{*}item"):
        item_id = element.attrib.get("id", "").strip()
        href = element.attrib.get("href", "").strip()
        media_type = element.attrib.get("media-type", "").strip().lower()
        if not item_id or not href or item_id in items_by_id:
            raise EpubError("manifest items require unique id and href")
        if not media_type:
            suffix = PurePosixPath(urlsplit(href).path).suffix.lower()
            media_type = {
                ".xhtml": "application/xhtml+xml",
                ".html": "text/html",
                ".htm": "text/html",
                ".ncx": "application/x-dtbncx+xml",
            }.get(suffix, mimetypes.guess_type(href)[0] or "application/octet-stream")
        archive_path, _ = resolve_package_href(opf_path, href)
        item = ManifestItem(
            item_id=item_id,
            href=href,
            archive_path=archive_path,
            media_type=media_type,
            properties=element.attrib.get("properties", "").split(),
            exists=root.joinpath(*PurePosixPath(archive_path).parts).is_file(),
        )
        manifest_items.append(item)
        items_by_id[item_id] = item

    spine: list[SpineItem] = []
    for order, element in enumerate(package.findall("./{*}spine/{*}itemref")):
        idref = element.attrib.get("idref", "")
        if idref not in items_by_id:
            raise EpubError(f"spine references unknown manifest id: {idref}")
        item = items_by_id[idref]
        spine.append(
            SpineItem(
                order=order,
                idref=idref,
                linear=element.attrib.get("linear", "yes").lower() != "no",
                item_id=item.item_id,
                archive_path=item.archive_path,
                media_type=item.media_type,
            )
        )

    navigation = _extract_navigation(root, opf_path, manifest_items)
    document_spine = [item for item in spine if _is_document_item(item)]
    if not document_spine:
        document_spine = _fallback_document_spine(navigation, manifest_items)
        if not spine:
            spine = document_spine
    documents: list[ChapterDocument] = []
    for spine_item in document_spine:
        if not _is_document_item(spine_item):
            continue
        document_path = root.joinpath(*PurePosixPath(spine_item.archive_path).parts)
        if not document_path.is_file():
            raise EpubError(f"spine document is missing: {spine_item.archive_path}")
        document = parse_content_document(document_path.read_bytes(), spine_item.archive_path)
        segments = extract_text_segments(document, spine_item.order)
        title = _document_title(document, spine_item, navigation)
        documents.append(
            ChapterDocument(
                order=spine_item.order,
                manifest_id=spine_item.item_id,
                archive_path=spine_item.archive_path,
                media_type=spine_item.media_type,
                linear=spine_item.linear,
                title=title,
                source_sha256=sha256_file(document_path),
                segments=segments,
            )
        )

    manifest_by_path = {item.archive_path: item for item in manifest_items}
    spine_paths = {item.archive_path for item in spine}
    resources: list[ResourceRecord] = []
    for path in sorted(file for file in root.rglob("*") if file.is_file()):
        archive_path = path.relative_to(root).as_posix()
        item = manifest_by_path.get(archive_path)
        media_type = item.media_type if item else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        resources.append(
            ResourceRecord(
                archive_path=archive_path,
                manifest_id=item.item_id if item else None,
                media_type=media_type,
                properties=item.properties if item else [],
                size=path.stat().st_size,
                sha256=sha256_file(path),
                in_spine=archive_path in spine_paths,
            )
        )

    return EpubPackageManifest(
        source_sha256=source_sha256,
        opf_path=opf_path,
        package_version=package.attrib.get("version", ""),
        unique_identifier=package.attrib.get("unique-identifier", ""),
        metadata=EpubMetadata(values=dict(sorted(metadata_values.items()))),
        manifest_items=manifest_items,
        spine=spine,
        navigation=navigation,
        documents=documents,
        resources=resources,
    )


def _extract_navigation(
    root: Path,
    opf_path: str,
    items: list[ManifestItem],
) -> list[NavigationEntry]:
    nav_item = next((item for item in items if "nav" in item.properties), None)
    if nav_item and nav_item.exists:
        document = parse_content_document(
            root.joinpath(*PurePosixPath(nav_item.archive_path).parts).read_bytes(),
            nav_item.archive_path,
        )
        toc = next(
            (
                element for element in document.iter()
                if local_name(element.tag) == "nav"
                and any(
                    local_name(key) == "type" and "toc" in value.split()
                    for key, value in element.attrib.items()
                )
            ),
            None,
        )
        if toc is not None:
            entries: list[NavigationEntry] = []

            def walk_list(element: ET.Element, level: int) -> None:
                for child in list(element):
                    if local_name(child.tag) != "li":
                        continue
                    link = _find_navigation_link(child)
                    if link is not None and link.attrib.get("href"):
                        href = link.attrib["href"]
                        try:
                            archive_path, fragment = resolve_package_href(
                                nav_item.archive_path, href
                            )
                        except EpubError:
                            continue
                        entries.append(
                            NavigationEntry(
                                label=normalize_text("".join(link.itertext())),
                                href=href,
                                archive_path=archive_path,
                                fragment=fragment,
                                level=level,
                            )
                        )
                    for nested in list(child):
                        if local_name(nested.tag) in {"ol", "ul"}:
                            walk_list(nested, level + 1)

            first_list = next(
                (element for element in list(toc) if local_name(element.tag) in {"ol", "ul"}),
                None,
            )
            if first_list is not None:
                walk_list(first_list, 0)
                return entries

    ncx_item = next(
        (item for item in items if item.media_type == "application/x-dtbncx+xml"),
        None,
    )
    if ncx_item and ncx_item.exists:
        document = parse_xml_compat(
            root.joinpath(*PurePosixPath(ncx_item.archive_path).parts).read_bytes(),
            ncx_item.archive_path,
        )
        entries: list[NavigationEntry] = []

        def walk_points(element: ET.Element, level: int) -> None:
            for point in list(element):
                if local_name(point.tag) != "navPoint":
                    continue
                label_element = point.find("./{*}navLabel/{*}text")
                content = point.find("./{*}content")
                if content is not None and content.attrib.get("src"):
                    href = content.attrib["src"]
                    try:
                        archive_path, fragment = resolve_package_href(
                            ncx_item.archive_path, href
                        )
                    except EpubError:
                        continue
                    entries.append(
                        NavigationEntry(
                            label=(
                                normalize_text("".join(label_element.itertext()))
                                if label_element is not None
                                else ""
                            ),
                            href=href,
                            archive_path=archive_path,
                            fragment=fragment,
                            level=level,
                        )
                    )
                walk_points(point, level + 1)

        nav_map = document.find(".//{*}navMap")
        if nav_map is not None:
            walk_points(nav_map, 0)
        return entries
    return []


def _find_navigation_link(item: ET.Element) -> ET.Element | None:
    """Find a link wrapped by presentation elements without entering nested lists."""
    pending = list(item)
    while pending:
        candidate = pending.pop(0)
        name = local_name(candidate.tag)
        if name in {"ol", "ul"}:
            continue
        if name == "a" and candidate.attrib.get("href"):
            return candidate
        pending[0:0] = list(candidate)
    return None


def _is_document_item(item) -> bool:
    media_type = item.media_type.lower().split(";", 1)[0].strip()
    suffix = PurePosixPath(item.archive_path).suffix.lower()
    return media_type in _DOCUMENT_MEDIA_TYPES or suffix in {".xhtml", ".html", ".htm"}


def _fallback_document_spine(
    navigation: list[NavigationEntry],
    manifest_items: list[ManifestItem],
) -> list[SpineItem]:
    """Recover readable document order from TOC paths, then manifest order."""
    by_path = {
        item.archive_path: item
        for item in manifest_items
        if item.exists and _is_document_item(item) and "nav" not in item.properties
    }
    ordered_paths = []
    for entry in navigation:
        if entry.archive_path in by_path and entry.archive_path not in ordered_paths:
            ordered_paths.append(entry.archive_path)
    for item in manifest_items:
        if item.archive_path in by_path and item.archive_path not in ordered_paths:
            ordered_paths.append(item.archive_path)
    return [
        SpineItem(
            order=order,
            idref=by_path[path].item_id,
            linear=True,
            item_id=by_path[path].item_id,
            archive_path=path,
            media_type=by_path[path].media_type,
        )
        for order, path in enumerate(ordered_paths)
    ]


def _document_title(
    document: ET.Element,
    spine_item: SpineItem,
    navigation: list[NavigationEntry],
) -> str:
    for element in document.iter():
        if local_name(element.tag) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            title = normalize_text("".join(element.itertext()))
            if title:
                return title
    for entry in navigation:
        if entry.archive_path == spine_item.archive_path and entry.label:
            return entry.label
    for element in document.iter():
        if local_name(element.tag) == "title":
            title = normalize_text("".join(element.itertext()))
            if title:
                return title
    return PurePosixPath(spine_item.archive_path).stem
