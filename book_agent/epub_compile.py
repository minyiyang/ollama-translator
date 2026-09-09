"""Lossless EPUB package reconstruction and deterministic validation."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from html import unescape
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, Field

from .epub import (
    ChapterDocument,
    EpubPackageManifest,
    TextSegment,
    inspect_epub_package,
    local_name,
    normalize_text,
    parse_content_document,
    parse_xml,
    resolve_package_href,
    safe_extract_epub,
    validate_archive_path,
)
from .hashing import sha256_file
from .repair import RepairedDocument
from .translation import TranslatedDocument


class EpubCompilationError(ValueError):
    """The translated package cannot be reconstructed safely."""


class EpubCompilationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_path: str
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    stripped_page_marker_count: int = Field(default=0, ge=0)
    output_sha256: str
    output_size: int = Field(ge=0)


class EpubValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    errors: list[str] = Field(default_factory=list)
    document_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)


@dataclass
class _InlineNode:
    marker_id: str = ""
    text: str = ""
    children: list["_InlineNode"] = field(default_factory=list)
    tail: str = ""


_PATH_PART = re.compile(r"(?P<tag>[^/\[]+)\[(?P<index>\d+)\]")
_INLINE_TOKEN = re.compile(r"<(?P<close>/)?(?P<id>I\d{3})>")


def find_element_by_stable_path(root: ET.Element, path: str) -> ET.Element:
    """Resolve a namespace-insensitive stable element path."""
    parts = [item for item in path.split("/") if item]
    if not parts:
        raise EpubCompilationError("element path is empty")
    first = _PATH_PART.fullmatch(parts[0])
    if first is None or first.group("index") != "1" or local_name(root.tag) != first.group("tag"):
        raise EpubCompilationError(f"element path root does not match document: {path}")
    current = root
    for raw_part in parts[1:]:
        match = _PATH_PART.fullmatch(raw_part)
        if match is None:
            raise EpubCompilationError(f"invalid element path component: {raw_part}")
        candidates = [
            child for child in list(current) if local_name(child.tag) == match.group("tag")
        ]
        index = int(match.group("index"))
        if index <= 0 or index > len(candidates):
            raise EpubCompilationError(f"element path does not exist: {path}")
        current = candidates[index - 1]
    return current


def parse_inline_translation(text: str) -> _InlineNode:
    """Parse protected inline markers while treating all other characters as literal text."""
    root = _InlineNode()
    stack = [root]
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text):
        _append_inline_text(stack[-1], unescape(text[cursor:match.start()]))
        marker_id = match.group("id")
        if match.group("close"):
            if len(stack) == 1 or stack[-1].marker_id != marker_id:
                raise EpubCompilationError(f"unbalanced inline marker: {marker_id}")
            stack.pop()
        else:
            node = _InlineNode(marker_id=marker_id)
            stack[-1].children.append(node)
            stack.append(node)
        cursor = match.end()
    _append_inline_text(stack[-1], unescape(text[cursor:]))
    if len(stack) != 1:
        raise EpubCompilationError(f"unclosed inline marker: {stack[-1].marker_id}")
    return root


def apply_segment_translation(
    document_root: ET.Element,
    segment: TextSegment,
    translated_text: str,
) -> None:
    """Reinsert one translated segment into its original block and inline tree."""
    element = find_element_by_stable_path(document_root, segment.element_path)
    if local_name(element.tag) != segment.tag:
        raise EpubCompilationError(
            f"segment tag differs at {segment.element_path}: {segment.tag}"
        )
    expected = re.findall(r"</?I\d{3}>", segment.protected_text or segment.text)
    actual = re.findall(r"</?I\d{3}>", translated_text)
    if actual != expected:
        raise EpubCompilationError(
            f"inline marker sequence differs for {segment.segment_id}"
        )
    tree = parse_inline_translation(translated_text)
    _apply_inline_node(element, tree, segment.segment_id)


def render_translated_xhtml(
    source_data: bytes,
    source_document: ChapterDocument,
    translated_document: TranslatedDocument,
    *,
    chapter_heading: str = "",
) -> bytes:
    """Render one translated XHTML document while preserving its element structure."""
    if source_document.manifest_id != translated_document.manifest_id:
        raise EpubCompilationError("source and translated document IDs differ")
    expected_ids = [item.segment_id for item in source_document.segments]
    actual_ids = [item.segment_id for item in translated_document.segments]
    if expected_ids != actual_ids:
        raise EpubCompilationError("source and translated segment IDs or order differ")
    _register_namespaces(source_data)
    root = parse_content_document(source_data, source_document.archive_path)
    translations = {
        item.segment_id: item.translated_text for item in translated_document.segments
    }
    for segment in source_document.segments:
        apply_segment_translation(root, segment, translations[segment.segment_id])
    _strip_orphaned_page_template_links(root)
    if chapter_heading:
        _insert_missing_chapter_heading(root, chapter_heading)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _insert_missing_chapter_heading(document_root: ET.Element, heading: str) -> None:
    """Add a stable visible heading only when a document has none already."""
    body = next((item for item in document_root.iter() if local_name(item.tag) == "body"), None)
    if body is None:
        return
    if any(local_name(item.tag) in {"h1", "h2", "h3", "h4", "h5", "h6"} and normalize_text("".join(item.itertext())) for item in body.iter()):
        return
    namespace = body.tag[: body.tag.rfind("}") + 1] if body.tag.startswith("{") else ""
    node = ET.Element(namespace + "h1", {"class": "book-agent-chapter-heading", "data-book-agent-generated": "chapter-heading"})
    node.text = heading
    body.insert(0, node)


def _chapter_heading_labels(
    manifest: EpubPackageManifest,
    explicit_labels: list[str] | None = None,
) -> dict[str, str]:
    """Map supplied TOC labels onto the reading documents in spine order."""
    start_paths = {
        item.archive_path for item in manifest.navigation
        if item.label.strip().lower() in {"chapters", "begin reading", "start reading"}
    }
    if not start_paths:
        return {}
    start_order = min((item.order for item in manifest.documents if item.archive_path in start_paths), default=None)
    if start_order is None:
        return {}
    documents = [item for item in manifest.documents if item.order >= start_order]
    labels = [item.strip() for item in (explicit_labels or []) if item.strip()]
    return {
        item.manifest_id: label
        for item, label in zip(documents, labels, strict=False)
    }


def _extract_linked_toc_heading_labels(
    package_root: Path,
    manifest: EpubPackageManifest,
) -> dict[str, str]:
    """Extract translated TOC labels from link-dense contents documents."""
    document_by_path = {item.archive_path: item for item in manifest.documents}
    labels: dict[str, str] = {}
    for document in manifest.documents:
        path = package_root.joinpath(*PurePosixPath(document.archive_path).parts)
        if not path.is_file():
            continue
        try:
            root = parse_content_document(path.read_bytes(), document.archive_path)
        except Exception:
            continue
        links: list[tuple[str, str]] = []
        for element in root.iter():
            if local_name(element.tag) != "a":
                continue
            href = next(
                (value for key, value in element.attrib.items() if local_name(key) == "href"),
                "",
            ).strip()
            label = normalize_text("".join(element.itertext()))
            if not href or not label:
                continue
            try:
                target, _ = resolve_package_href(document.archive_path, href)
            except ValueError:
                continue
            if target in document_by_path and target != document.archive_path:
                links.append((target, label))
        if len({target for target, _ in links}) < 3:
            continue
        for target, label in links:
            labels.setdefault(document_by_path[target].manifest_id, label)
    return labels


def _strip_orphaned_page_template_links(document_root: ET.Element) -> None:
    """Drop obsolete Adobe page-template links when rebuilding XHTML."""
    for parent in document_root.iter():
        for child in list(parent):
            if local_name(child.tag) != "link":
                continue
            href = next(
                (value for key, value in child.attrib.items() if local_name(key) == "href"),
                "",
            ).strip().lower()
            media_type = next(
                (value for key, value in child.attrib.items() if local_name(key) == "type"),
                "",
            ).strip().lower()
            if href.endswith(".xpgt") and media_type == "application/vnd.adobe-page-template+xml":
                parent.remove(child)


def compile_epub_package(
    package_root: str | Path,
    manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
    output_path: str | Path,
    *,
    strip_print_page_markers: bool = False,
    insert_missing_chapter_headings: bool = False,
    chapter_heading_labels: list[str] | None = None,
) -> EpubCompilationReport:
    """Rebuild a translated EPUB atomically from the preserved source package."""
    source_root = Path(package_root)
    output = Path(output_path)
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    _validate_document_set(manifest, repaired_documents)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="epub-build-", dir=output.parent) as directory:
        temporary = Path(directory)
        staging = temporary / "package"
        shutil.copytree(source_root, staging)
        source_by_id = {item.manifest_id: item for item in manifest.documents}
        for repaired in repaired_documents:
            document = repaired.document
            source_document = source_by_id[document.manifest_id]
            path = staging.joinpath(*PurePosixPath(source_document.archive_path).parts)
            rendered = render_translated_xhtml(
                path.read_bytes(), source_document, document,
            )
            path.write_bytes(rendered)
        if insert_missing_chapter_headings:
            chapter_labels = _extract_linked_toc_heading_labels(staging, manifest)
            if not chapter_labels:
                chapter_labels = _chapter_heading_labels(manifest, chapter_heading_labels)
            for document in manifest.documents:
                heading = chapter_labels.get(document.manifest_id, "")
                if not heading:
                    continue
                path = staging.joinpath(*PurePosixPath(document.archive_path).parts)
                root = parse_content_document(path.read_bytes(), document.archive_path)
                _insert_missing_chapter_heading(root, heading)
                path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        stripped_page_marker_count = 0
        if strip_print_page_markers:
            for resource in manifest.resources:
                if resource.media_type not in {
                    "application/xhtml+xml",
                    "text/html",
                }:
                    continue
                path = staging.joinpath(
                    *PurePosixPath(resource.archive_path).parts
                )
                if not path.is_file():
                    continue
                rendered, removed = strip_print_page_markers_from_xhtml(
                    path.read_bytes(), resource.archive_path
                )
                if removed:
                    path.write_bytes(rendered)
                    stripped_page_marker_count += removed
        temporary_epub = temporary / "compiled.epub"
        _write_epub_zip(staging, temporary_epub)
        os.replace(temporary_epub, output)
    return EpubCompilationReport(
        output_path=str(output.resolve()),
        document_count=len(repaired_documents),
        segment_count=sum(len(item.document.segments) for item in repaired_documents),
        resource_count=len(manifest.resources),
        stripped_page_marker_count=stripped_page_marker_count,
        output_sha256=sha256_file(output),
        output_size=output.stat().st_size,
    )


def validate_compiled_epub(
    epub_path: str | Path,
    source_manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
    *,
    strip_print_page_markers: bool = False,
    source_package_root: str | Path | None = None,
) -> EpubValidationReport:
    """Validate archive invariants, package structure, resources, and translated text."""
    epub = Path(epub_path)
    errors: list[str] = []
    try:
        with ZipFile(epub) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos if not item.is_dir()]
            if not infos or infos[0].filename != "mimetype":
                errors.append("mimetype is not the first archive entry")
            elif infos[0].compress_type != ZIP_STORED:
                errors.append("mimetype entry is compressed")
            if len(names) != len(set(names)):
                errors.append("archive contains duplicate file entries")
            for name in names:
                try:
                    validate_archive_path(name)
                except ValueError as error:
                    errors.append(str(error))
    except (BadZipFile, OSError) as error:
        return EpubValidationReport(
            passed=False,
            errors=[f"invalid EPUB ZIP: {error}"],
            document_count=0,
            segment_count=0,
            resource_count=0,
        )

    inspected = None
    with tempfile.TemporaryDirectory(prefix="epub-validate-") as directory:
        root = Path(directory)
        try:
            safe_extract_epub(epub, root)
            inspected = inspect_epub_package(root, sha256_file(epub))
        except Exception as error:
            errors.append(str(error))
        if inspected is not None:
            _compare_package_manifests(source_manifest, inspected, errors)
            _compare_translated_text(inspected, repaired_documents, errors)
            _validate_internal_references(root, inspected, errors)
            translated_paths = {
                item.document.archive_path for item in repaired_documents
            }
            source_resources = {item.archive_path: item for item in source_manifest.resources}
            output_resources = {item.archive_path: item for item in inspected.resources}
            source_root = Path(source_package_root) if source_package_root else None
            for path, resource in source_resources.items():
                if path in translated_paths:
                    continue
                output_resource = output_resources.get(path)
                if output_resource is not None and output_resource.sha256 != resource.sha256:
                    if not (
                        strip_print_page_markers
                        and source_root is not None
                        and _matches_page_marker_stripped_source(
                            source_root, root, resource
                        )
                    ):
                        errors.append(f"non-text resource changed: {path}")
            if strip_print_page_markers:
                for resource in inspected.resources:
                    if resource.media_type not in {
                        "application/xhtml+xml",
                        "text/html",
                    }:
                        continue
                    path = root.joinpath(
                        *PurePosixPath(resource.archive_path).parts
                    )
                    if not path.is_file():
                        continue
                    _, remaining = strip_print_page_markers_from_xhtml(
                        path.read_bytes(), resource.archive_path
                    )
                    if remaining:
                        errors.append(
                            f"print page marker remains after compilation: "
                            f"{resource.archive_path}"
                        )
    return EpubValidationReport(
        passed=not errors,
        errors=errors,
        document_count=len(inspected.documents) if inspected else 0,
        segment_count=(
            sum(len(item.segments) for item in inspected.documents) if inspected else 0
        ),
        resource_count=len(inspected.resources) if inspected else 0,
    )


def strip_print_page_markers_from_xhtml(
    source_data: bytes,
    archive_path: str,
) -> tuple[bytes, int]:
    """Remove print-page navigation semantics while preserving ordinary TOC links."""
    if not re.search(
        rb"page(?:break|-list)|doc-pagebreak|\bid\s*=\s*['\"]page-(?:\d+|[ivxlcdm]+)['\"]",
        source_data,
        flags=re.IGNORECASE,
    ):
        return source_data, 0
    _register_namespaces(source_data)
    root = parse_content_document(source_data, archive_path)
    parent_by_child = {
        child: parent for parent in root.iter() for child in list(parent)
    }
    changed = 0
    for element in reversed(list(root.iter())):
        if _is_legacy_print_page_anchor(element):
            parent = parent_by_child.get(element)
            if parent is not None:
                _remove_element_preserving_tail(parent, element)
                changed += 1
            continue
        type_key = _attribute_key(element, "type")
        role_key = _attribute_key(element, "role")
        type_tokens = _attribute_tokens(element, type_key)
        role_tokens = _attribute_tokens(element, role_key)
        if local_name(element.tag) == "nav" and "page-list" in type_tokens:
            parent = parent_by_child.get(element)
            if parent is not None:
                _remove_element_preserving_tail(parent, element)
                changed += 1
            continue
        if "pagebreak" not in type_tokens and "doc-pagebreak" not in role_tokens:
            continue
        _remove_attribute_token(element, type_key, "pagebreak")
        _remove_attribute_token(element, role_key, "doc-pagebreak")
        element.attrib.pop("title", None)
        element.attrib.pop("aria-label", None)
        changed += 1
    if not changed:
        return source_data, 0
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), changed


def _is_legacy_print_page_anchor(element: ET.Element) -> bool:
    """Recognize empty Adobe/KF8-style anchors used solely as print page marks."""
    if local_name(element.tag) != "a":
        return False
    identifier = element.attrib.get("id", "")
    if not re.fullmatch(r"page-(?:\d+|[ivxlcdm]+)", identifier, flags=re.IGNORECASE):
        return False
    if _attribute_key(element, "href") is not None:
        return False
    return not list(element) and not (element.text or "").strip()


def _attribute_key(element: ET.Element, name: str) -> str | None:
    return next(
        (key for key in element.attrib if local_name(key) == name),
        None,
    )


def _attribute_tokens(element: ET.Element, key: str | None) -> set[str]:
    if key is None:
        return set()
    return {token.casefold() for token in element.attrib[key].split()}


def _remove_attribute_token(
    element: ET.Element,
    key: str | None,
    token: str,
) -> None:
    if key is None:
        return
    remaining = [
        item for item in element.attrib[key].split()
        if item.casefold() != token
    ]
    if remaining:
        element.attrib[key] = " ".join(remaining)
    else:
        element.attrib.pop(key, None)


def _remove_element_preserving_tail(
    parent: ET.Element,
    element: ET.Element,
) -> None:
    children = list(parent)
    index = children.index(element)
    tail = element.tail or ""
    if index:
        previous = children[index - 1]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(element)


def _matches_page_marker_stripped_source(
    source_root: Path,
    output_root: Path,
    resource,
) -> bool:
    if resource.media_type not in {"application/xhtml+xml", "text/html"}:
        return False
    source_path = source_root.joinpath(
        *PurePosixPath(resource.archive_path).parts
    )
    output_path = output_root.joinpath(
        *PurePosixPath(resource.archive_path).parts
    )
    if not source_path.is_file() or not output_path.is_file():
        return False
    expected, changed = strip_print_page_markers_from_xhtml(
        source_path.read_bytes(), resource.archive_path
    )
    return bool(changed) and expected == output_path.read_bytes()


def _append_inline_text(node: _InlineNode, text: str) -> None:
    if node.children:
        node.children[-1].tail += text
    else:
        node.text += text


def _apply_inline_node(element: ET.Element, node: _InlineNode, segment_id: str) -> None:
    children = list(element)
    if len(children) != len(node.children):
        raise EpubCompilationError(
            f"inline child count differs for {segment_id}: expected {len(children)}, received {len(node.children)}"
        )
    element.text = node.text or None
    for child, child_node in zip(children, node.children):
        _apply_inline_node(child, child_node, segment_id)
        child.tail = child_node.tail or None


def _register_namespaces(data: bytes) -> None:
    try:
        for _, pair in ET.iterparse(BytesIO(data), events=("start-ns",)):
            prefix, uri = pair
            if prefix != "xml":
                ET.register_namespace(prefix or "", uri)
    except (ET.ParseError, ValueError):
        return


def _validate_document_set(
    manifest: EpubPackageManifest,
    repaired_documents: list[RepairedDocument],
) -> None:
    expected = [item.manifest_id for item in manifest.documents]
    actual = [item.document.manifest_id for item in repaired_documents]
    if actual != expected:
        raise EpubCompilationError(
            f"repaired document order or set differs: expected {expected}, received {actual}"
        )


def _write_epub_zip(package_root: Path, output: Path) -> None:
    files = sorted(path for path in package_root.rglob("*") if path.is_file())
    relative = {path.relative_to(package_root).as_posix(): path for path in files}
    if "mimetype" not in relative:
        raise EpubCompilationError("preserved package has no mimetype file")
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


def _compare_package_manifests(source, output, errors) -> None:
    if output.opf_path != source.opf_path:
        errors.append("package document path changed")
    if output.package_version != source.package_version:
        errors.append("package version changed")
    source_items = [
        (item.item_id, item.archive_path, item.media_type, item.properties)
        for item in source.manifest_items
    ]
    output_items = [
        (item.item_id, item.archive_path, item.media_type, item.properties)
        for item in output.manifest_items
    ]
    if output_items != source_items:
        errors.append("manifest item set or order changed")
    source_spine = [(item.idref, item.linear, item.archive_path) for item in source.spine]
    output_spine = [(item.idref, item.linear, item.archive_path) for item in output.spine]
    if output_spine != source_spine:
        errors.append("spine order or properties changed")
    source_paths = {item.archive_path for item in source.resources}
    output_paths = {item.archive_path for item in output.resources}
    if output_paths != source_paths:
        errors.append(
            f"resource set changed; missing={sorted(source_paths-output_paths)}, added={sorted(output_paths-source_paths)}"
        )


def _compare_translated_text(inspected, repaired_documents, errors) -> None:
    output_by_id = {item.manifest_id: item for item in inspected.documents}
    for repaired in repaired_documents:
        expected_document = repaired.document
        actual_document = output_by_id.get(expected_document.manifest_id)
        if actual_document is None:
            errors.append(f"translated document missing: {expected_document.manifest_id}")
            continue
        expected_ids = [item.segment_id for item in expected_document.segments]
        actual_ids = [item.segment_id for item in actual_document.segments]
        if expected_ids != actual_ids:
            errors.append(f"translated segment IDs differ: {expected_document.manifest_id}")
            continue
        actual_text = {item.segment_id: item.text for item in actual_document.segments}
        for segment in expected_document.segments:
            expected = normalize_text(
                unescape(re.sub(r"</?I\d{3}>", "", segment.translated_text))
            )
            if actual_text[segment.segment_id] != expected:
                errors.append(f"translated text differs after compilation: {segment.segment_id}")


def _validate_internal_references(
    root: Path,
    manifest: EpubPackageManifest,
    errors: list[str],
) -> None:
    for item in manifest.manifest_items:
        if not item.exists:
            errors.append(f"manifest resource is missing: {item.archive_path}")
    references: list[tuple[str, str]] = []
    for resource in manifest.resources:
        path = root.joinpath(*PurePosixPath(resource.archive_path).parts)
        if resource.media_type in {"application/xhtml+xml", "text/html", "image/svg+xml"}:
            try:
                document = parse_content_document(path.read_bytes(), resource.archive_path)
            except Exception:
                continue
            for element in document.iter():
                for key, value in element.attrib.items():
                    if local_name(key) in {"href", "src"} and value.strip():
                        references.append((resource.archive_path, value.strip()))
        elif resource.media_type == "text/css":
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"url\((?P<value>[^)]+)\)", text, flags=re.IGNORECASE):
                references.append(
                    (resource.archive_path, match.group("value").strip().strip("\"'"))
                )
    seen: set[tuple[str, str]] = set()
    for base, reference in references:
        if (base, reference) in seen:
            continue
        seen.add((base, reference))
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or reference.startswith("data:") or not parsed.path:
            continue
        try:
            target, _ = resolve_package_href(base, reference)
        except ValueError as error:
            errors.append(str(error))
            continue
        if not root.joinpath(*PurePosixPath(target).parts).is_file():
            errors.append(f"local resource reference is missing: {base} -> {reference}")
