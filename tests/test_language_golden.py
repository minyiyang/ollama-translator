"""Golden prompts and stage hashes for today's language pairs (docs/GENERIC_LANGUAGES.md, 6).

The generic-languages refactor must not change anything the model sees or any
checkpoint for `en-zh` and `zh-en`: prompt text and schemas feed unit hashes,
and stage hashes decide whether existing jobs stay current. This test records
both for a fixture job per pair and fails on any difference.

Re-record only for a deliberate change:  UPDATE_LANGUAGE_GOLDEN=1 python -m pytest tests/test_language_golden.py
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from book_agent.audit import SemanticAuditResult
from book_agent.config import AppConfig
from book_agent.glossary import GlossaryChunk, GlossaryChunkPiece, build_glossary_approval_cases
from book_agent.glossary_prompts import (
    build_approval_review_prompt,
    build_conflict_resolution_prompt,
    build_extraction_prompt,
    build_resolution_prompt,
)
from book_agent.languages import TranslationDirection, build_direction_instruction
from book_agent.pipeline_state import WorkflowStage
from book_agent.prose_rewrite import build_prose_rewrite_prompt
from book_agent.schemas import (
    GlossaryCategory,
    GlossaryEntry,
    build_glossary_approval_schema,
    build_glossary_extraction_schema,
    build_glossary_resolution_schema,
)
from book_agent.state import connect_state, list_stage_statuses
from book_agent.story_context import build_summary_prompt
from book_agent.style_sheet import (
    StyleCharacter,
    StyleExpression,
    StyleSheet,
    build_style_review_prompt,
    extraction_instructions,
    format_relevant_style,
)
from book_agent.workspace import create_job_workspace
from tests.epub_fixture import make_epub
from tests.test_audit_stage import FakeAuditClient
from tests.test_preprocess_stage import publish_approved_glossary
from tests.test_repair_stage import FakeRepairClient
from tests.test_translate_stage import FakeTranslationClient
from tests.test_validate_repaired_stage import FakeVerificationClient

GOLDEN = Path(__file__).parent / "golden" / "languages"
UPDATE = os.environ.get("UPDATE_LANGUAGE_GOLDEN") == "1"

EN_CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>Chapter One</h1><p>Aster opened the <em>small</em> door at 3 o'clock.</p>
<p>&#8220;Off with her head!&#8221; the Queen shouted at Aster.</p>
<ul><li><p>Nested paragraph about twelve gardeners.</p></li><li>Plain item.</li></ul></body></html>"""

ZH_CHAPTER = """<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>第一章</h1><p>阿斯特在三点钟打开了那扇<em>小</em>门。</p>
<p>“砍掉她的头！”王后对阿斯特喊道。</p>
<ul><li><p>关于十二个园丁的嵌套段落。</p></li><li>普通条目。</li></ul></body></html>""".encode("utf-8")

GLOSSARY = [
    GlossaryEntry(english="Aster", chinese="阿斯特", category=GlossaryCategory.PERSON, note="主角"),
    GlossaryEntry(english="Queen", chinese="王后", category=GlossaryCategory.PERSON),
]

PAIRS = {
    "en-zh": (EN_CHAPTER, "译文。"),
    "zh-en": (ZH_CHAPTER, "Translation."),
}


def _reproducible_epub(path: Path, chapter: bytes) -> Path:
    """The fixture EPUB with fixed entry timestamps, so its bytes (and every stage hash) repeat."""
    from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

    draft = make_epub(path.with_suffix(".draft.epub"), chapter=chapter)
    with ZipFile(draft) as source, ZipFile(path, "w") as target:
        for item in source.infolist():
            info = ZipInfo(item.filename, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED if item.filename == "mimetype" else ZIP_DEFLATED
            target.writestr(info, source.read(item.filename))
    return path


def _pipeline(direction: str, chapter: bytes, translated: str) -> dict:
    """Run the fixture pipeline with semantic audit on; record prompts and stage hashes."""
    config = AppConfig.model_validate({
        "translation": {"direction": direction},
        "audit": {"semantic_enabled": True},
    })
    from book_agent.stages.audit import run_translation_audit_stage
    from book_agent.stages.decompile import run_decompile_stage
    from book_agent.stages.preprocess import run_preprocessing_stage
    from book_agent.stages.repair import run_translation_repair_stage
    from book_agent.stages.repair_review import run_review_repair_stage
    from book_agent.stages.review_repaired import run_repaired_review_stage
    from book_agent.stages.translate import run_translation_stage
    from book_agent.stages.validate_repaired import run_repaired_validation_stage

    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        workspace = create_job_workspace(
            _reproducible_epub(base / "golden.epub", chapter), base / "runs", config, job_id="golden"
        )
        run_decompile_stage(workspace)
        publish_approved_glossary(workspace, GLOSSARY)
        run_preprocessing_stage(workspace, config)
        translator = FakeTranslationClient(translated_text=translated)
        auditor = FakeAuditClient()
        repairer = FakeRepairClient()
        verifier = FakeVerificationClient()
        run_translation_stage(workspace, config, translator)
        run_translation_audit_stage(workspace, config, auditor)
        run_translation_repair_stage(workspace, config, repairer)
        run_repaired_review_stage(workspace, config, verifier)
        run_review_repair_stage(workspace, config, verifier)
        run_repaired_validation_stage(workspace, config, verifier)
        connection = connect_state(workspace.state_file)
        try:
            hashes = {
                row["name"]: [row["input_hash"], row["output_hash"]]
                for row in list_stage_statuses(connection)
                if row["input_hash"]
            }
        finally:
            connection.close()
        from book_agent.stages.preprocess import load_preprocessed_documents
        from book_agent.stages.translate import load_translated_documents

        source = load_preprocessed_documents(workspace)[0]
        translated_document = load_translated_documents(workspace)[0]
        prose = build_prose_rewrite_prompt(
            source, translated_document, [s.segment_id for s in source.segments[:2]], config
        )
    return {
        "prompts.translate": translator.prompts,
        "prompts.audit": auditor.prompts,
        "prompts.repair": repairer.prompts,
        "prompts.verification": verifier.prompts,
        "prompts.prose_rewrite": prose,
        "stage_hashes": hashes,
    }


def _direct(direction: TranslationDirection) -> dict:
    chunk = GlossaryChunk(
        chunk_id="glossary-00001",
        pieces=[
            GlossaryChunkPiece(reference_id="D0000-S000001", document_path="text/chapter.xhtml", text="Aster opened the door."),
            GlossaryChunkPiece(reference_id="D0000-S000002", document_path="text/chapter.xhtml", text="阿斯特打开了门。"),
        ],
        estimated_tokens=20,
    )
    candidates = [*GLOSSARY, GlossaryEntry(english="Aster", chinese="阿斯塔", category=GlossaryCategory.PERSON)]
    sheet = StyleSheet(
        characters=[StyleCharacter(name="Mouse", pronoun="它", addressed_as="您", voice="formal", evidence=["D0000-S000001"])],
        expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！", evidence=["D0000-S000001"])],
    )
    ids = ["D0000-S000001", "D0000-S000002"]
    return {
        "prompts.direction_instruction": build_direction_instruction(direction),
        "prompts.glossary_extraction": build_extraction_prompt(chunk, direction),
        "prompts.glossary_resolution": build_resolution_prompt(candidates, direction),
        "prompts.glossary_conflict": build_conflict_resolution_prompt(candidates, direction),
        "prompts.glossary_approval": build_approval_review_prompt(build_glossary_approval_cases(GLOSSARY), direction),
        "prompts.style_extraction": extraction_instructions(direction, max_characters=20, max_expressions=20),
        "prompts.style_review": build_style_review_prompt(sheet, direction, {"D0000-S000001": "The Mouse spoke."}),
        "prompts.style_block": format_relevant_style(sheet),
        "prompts.story_summary": build_summary_prompt("Chapter text.", max_words=120, truncated=False),
        "schemas.glossary_extraction": build_glossary_extraction_schema(120, 3, ids).model_json_schema(),
        "schemas.glossary_resolution": build_glossary_resolution_schema(["T00001", "T00002"], 2).model_json_schema(),
        "schemas.glossary_approval": build_glossary_approval_schema(["A00001"]).model_json_schema(),
        "schemas.semantic_audit": SemanticAuditResult.model_json_schema(),
    }


@pytest.mark.parametrize("pair", sorted(PAIRS))
def test_prompts_schemas_and_stage_hashes_are_unchanged(pair: str) -> None:
    chapter, translated = PAIRS[pair]
    recorded = {**_direct(TranslationDirection(pair)), **_pipeline(pair, chapter, translated)}
    path = GOLDEN / f"{pair}.json"
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(recorded, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"recorded a new golden file {path.name}; re-run to compare against it")
        return
    golden = json.loads(path.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(recorded, ensure_ascii=False))
    for key in sorted(golden.keys() | current.keys()):
        assert current.get(key) == golden.get(key), (
            f"{pair}: {key} changed. Prompts, schemas, and stage hashes for today's pairs must stay "
            "byte-for-byte the same (docs/GENERIC_LANGUAGES.md, 6); re-record only for a deliberate change."
        )
