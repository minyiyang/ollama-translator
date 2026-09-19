"""Title, author, and cover of a source book, read straight from the archive."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..epub import EpubError, local_name, parse_xml, validate_archive_path

# Raster only: an SVG from an untrusted book could run script on the dashboard's origin.
_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
_MAX_COVER_BYTES = 20 * 1024 * 1024


def allowed_book(path: str, roots: list[Path]) -> Path:
    """Only EPUB/RTF books under the dashboard's runs or sample folders may be inspected."""
    candidate = Path(path).resolve()
    if (
        not candidate.is_file()
        or candidate.suffix.lower() not in {".epub", ".rtf"}
        or not any(root.resolve() in candidate.parents for root in roots)
    ):
        raise ValueError("unknown book file")
    return candidate


def _opf(archive: zipfile.ZipFile):
    container = parse_xml(archive.read("META-INF/container.xml"), "container.xml")
    rootfile = next((e for e in container.iter() if local_name(e.tag) == "rootfile"), None)
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubError("container.xml names no package document")
    opf_path = str(validate_archive_path(rootfile.get("full-path", "")))
    return opf_path, parse_xml(archive.read(opf_path), opf_path)


def _cover_href(package) -> tuple[str, str] | None:
    items = [e for e in package.iter() if local_name(e.tag) == "item"]
    by_id = {item.get("id"): item for item in items}
    chosen = next((i for i in items if "cover-image" in (i.get("properties") or "").split()), None)
    if chosen is None:  # EPUB 2: <meta name="cover" content="item-id"/>
        meta = next((e for e in package.iter() if local_name(e.tag) == "meta" and e.get("name") == "cover"), None)
        chosen = by_id.get(meta.get("content")) if meta is not None else None
    if chosen is None:
        chosen = next(
            (i for i in items if (i.get("media-type") or "").startswith("image/")
             and "cover" in f"{i.get('id', '')} {i.get('href', '')}".lower()),
            None,
        )
    if chosen is None or not chosen.get("href"):
        return None
    return chosen.get("href", ""), chosen.get("media-type") or ""


def _resolve(opf_path: str, href: str) -> str:
    joined = PurePosixPath(opf_path).parent / href.split("#", 1)[0]
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return str(validate_archive_path("/".join(parts)))


def book_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "format": path.suffix.lower().lstrip("."),
        "title": "",
        "authors": [],
        "language": "",
        "has_cover": False,
    }
    if info["format"] == "rtf":
        head = path.read_bytes()[:65536].decode("latin-1", errors="replace")
        for key, field in (("title", "title"), ("author", "authors")):
            match = re.search(r"\{\\" + key + r"\s+([^}]*)\}", head)
            if match:
                value = match[1].strip()
                info[field] = [value] if field == "authors" else value
        return info
    try:
        with zipfile.ZipFile(path) as archive:
            opf_path, package = _opf(archive)
            texts = lambda name: [e.text.strip() for e in package.iter() if local_name(e.tag) == name and e.text and e.text.strip()]  # noqa: E731
            info["title"] = next(iter(texts("title")), "")
            info["authors"] = texts("creator")
            info["language"] = next(iter(texts("language")), "")
            cover = _cover_href(package)
            if cover:
                member = _resolve(opf_path, cover[0])
                info["has_cover"] = member in archive.namelist() and (
                    cover[1] in _IMAGE_TYPES.values() or Path(member).suffix.lower() in _IMAGE_TYPES
                )
    except (zipfile.BadZipFile, KeyError, EpubError) as error:
        info["warning"] = f"could not read EPUB metadata: {error}"
    return info


def book_cover(path: Path) -> tuple[bytes, str]:
    with zipfile.ZipFile(path) as archive:
        opf_path, package = _opf(archive)
        cover = _cover_href(package)
        if cover is None:
            raise ValueError("this book has no cover image")
        member = _resolve(opf_path, cover[0])
        if archive.getinfo(member).file_size > _MAX_COVER_BYTES:
            raise ValueError("cover image is too large")
        media_type = _IMAGE_TYPES.get(Path(member).suffix.lower(), "")
        if cover[1] in _IMAGE_TYPES.values():
            media_type = cover[1]
        if not media_type:
            raise ValueError("cover is not a raster image")
        return archive.read(member), media_type
