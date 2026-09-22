"""XLIFF 2.1 export of the current translation (docs/FULL_TEXT_REVIEW.md, phase 5).

A read-only snapshot for CAT-tool round trips: export only for now, import is
future work. One ``.xlf`` document for the whole book, with one ``<file>`` per
chapter and one ``<unit>`` per segment. A segment with an active edit (state
``edited`` or ``conflict``) exports its edited text with
``state="reviewed"``; everything else exports the pipeline text with
``state="translated"``, since nothing has confirmed it beyond the automated
pipeline. Our protected ``<I000>`` inline markers map onto XLIFF's paired
``<pc>`` code elements.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape, quoteattr

from .config import AppConfig
from .stages.decompile import load_decompile_manifest
from .stages.preprocess import load_preprocessed_documents
from .stages.validate_repaired import load_validated_repaired_documents
from .text_edits import SegmentEditState, edited_segment_statuses
from .workspace import JobWorkspace

_MARKER_TOKEN = re.compile(r"(<I\d{3}>|</I\d{3}>)")
_OPEN_MARKER = re.compile(r"^<I(\d{3})>$")
_CLOSE_MARKER = re.compile(r"^</I(\d{3})>$")

_ACTIVE_STATES = (SegmentEditState.EDITED, SegmentEditState.CONFLICT)


def _inline_xml(text: str) -> str:
    """Render segment text as XLIFF inline content: ``<I000>`` markers become ``<pc>``."""
    out: list[str] = []
    open_ids: list[str] = []
    for token in _MARKER_TOKEN.split(text):
        if not token:
            continue
        if match := _OPEN_MARKER.match(token):
            out.append(f'<pc id="{match.group(1)}">')
            open_ids.append(match.group(1))
        elif match := _CLOSE_MARKER.match(token):
            out.append("</pc>")
            if open_ids and open_ids[-1] == match.group(1):
                open_ids.pop()
        else:
            out.append(escape(token))
    return "".join(out)


def export_xliff(workspace: JobWorkspace) -> str:
    """Build one XLIFF 2.1 document for the whole book."""
    config = AppConfig.model_validate_json(workspace.config_file.read_text(encoding="utf-8"))
    direction = config.translation.direction
    manifest = load_decompile_manifest(workspace)
    titles = {item.manifest_id: item.title for item in manifest.documents}
    sources = {item.manifest_id: item for item in load_preprocessed_documents(workspace)}
    pipeline = {
        item.document.manifest_id: item.document
        for item in load_validated_repaired_documents(workspace)
    }
    statuses = edited_segment_statuses(workspace)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.1" version="2.1" '
        f'srcLang="{direction.source_language.value}" '
        f'trgLang="{direction.target_language.value}">',
    ]
    for manifest_id, source in sorted(sources.items(), key=lambda item: item[1].order):
        translation = pipeline.get(manifest_id)
        if translation is None:
            continue
        target_by_id = {
            segment.segment_id: segment.translated_text for segment in translation.segments
        }
        title = titles.get(manifest_id, manifest_id)
        lines.append(f"  <file id={quoteattr(manifest_id)} original={quoteattr(title)}>")
        for segment in source.segments:
            pipeline_text = target_by_id.get(segment.segment_id, "")
            status = statuses.get(segment.segment_id)
            active = status is not None and status.state in _ACTIVE_STATES
            target_text = status.text if active else pipeline_text
            state = "reviewed" if active else "translated"
            lines.append(f"    <unit id={quoteattr(segment.segment_id)}>")
            lines.append(f'      <segment state="{state}">')
            lines.append(f"        <source>{_inline_xml(segment.original_text)}</source>")
            lines.append(f"        <target>{_inline_xml(target_text)}</target>")
            lines.append("      </segment>")
            lines.append("    </unit>")
        lines.append("  </file>")
    lines.append("</xliff>")
    return "\n".join(lines) + "\n"
