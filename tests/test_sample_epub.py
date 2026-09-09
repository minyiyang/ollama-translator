"""Synthetic end-to-end EPUB smoke coverage.

Public-domain books in ``sample/`` are retained for manual package inspection,
not as a behavioral test corpus.  This test intentionally uses invented text.
"""

import tempfile
from pathlib import Path

from book_agent.config import AppConfig
from book_agent.epub_compile import compile_epub_package, validate_compiled_epub
from book_agent.glossary import build_glossary_chunks
from book_agent.languages import TranslationDirection
from book_agent.preprocessing import apply_replacement_index, build_replacement_index
from book_agent.repair import RepairedDocument
from book_agent.schemas import GlossaryCategory, GlossaryEntry
from book_agent.stages.decompile import run_decompile_stage
from book_agent.translation import TranslatedDocument, TranslatedSegment
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub


CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Arrival</title></head>
<body><h1>Arrival at Northbridge</h1>
<p>Aster met <em>the guide</em> beneath the signal tower.</p>
<p>Northbridge answered with one clear bell.</p></body></html>"""


class SyntheticEpubSmokeTests:
    def test_synthetic_epub_decompiles_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = create_job_workspace(
                make_epub(base / "synthetic.epub", chapter=CHAPTER),
                base / "runs",
                AppConfig(),
                job_id="synthetic-smoke",
            )
            manifest = run_decompile_stage(workspace)
            document = manifest.documents[0]
            assert len(manifest.documents) == 1
            assert len(document.segments) == 3
            chunks = build_glossary_chunks(
                manifest.documents,
                AppConfig().glossary.extraction_chunk_tokens,
                max_documents=AppConfig().glossary.extraction_chapters_per_chunk,
            )
            assert [piece.reference_id for chunk in chunks for piece in chunk.pieces] == [segment.segment_id for segment in document.segments]

            entries = [
                GlossaryEntry(english="Aster", chinese="阿斯特", category=GlossaryCategory.PERSON),
                GlossaryEntry(english="Northbridge", chinese="北桥", category=GlossaryCategory.PLACE),
            ]
            source_text = "\n".join(segment.text for segment in document.segments)
            processed, occurrences = apply_replacement_index(
                source_text,
                build_replacement_index(entries, TranslationDirection.EN_TO_ZH),
            )
            assert "阿斯特" in processed
            assert "北桥" in processed
            assert sum(item.count for item in occurrences) == 3

            repaired = [
                RepairedDocument(
                    document=TranslatedDocument(
                        order=document.order,
                        manifest_id=document.manifest_id,
                        archive_path=document.archive_path,
                        direction=TranslationDirection.EN_TO_ZH,
                        style="faithful",
                        segments=[
                            TranslatedSegment(
                                segment_id=segment.segment_id,
                                source_text=segment.protected_text or segment.text,
                                translated_text=segment.protected_text or segment.text,
                            )
                            for segment in document.segments
                        ],
                    )
                )
            ]
            package_root = next((workspace.root / "decompiled").glob("*/package"))
            output = base / "synthetic-roundtrip.epub"
            compilation = compile_epub_package(package_root, manifest, repaired, output)
            assert compilation.segment_count == len(document.segments)
            assert validate_compiled_epub(output, manifest, repaired).passed

