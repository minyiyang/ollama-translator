"""Phase 2 of docs/BOOK_CONSISTENCY.md: the book style sheet."""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from book_agent.config import AppConfig
from book_agent.consistency import BookSegment, ConsistencySettings, book_consistency_issues, expression_issues
from book_agent.glossary_prompts import build_extraction_prompt
from book_agent.languages import TranslationDirection
from book_agent.ollama_client import GenerationMetrics, GenerationResult, StructuredGenerationResult
from book_agent.style_sheet import (
    StyleCharacter,
    StyleCharacterDecision,
    StyleExpression,
    StyleExpressionDecision,
    StyleSheet,
    apply_style_review,
    build_style_review_schema,
    format_relevant_style,
    load_style_sheet,
    merge_style_candidates,
    select_relevant_style,
    style_entry_ids,
)
from tests.epub_fixture import make_epub

EN_ZH = TranslationDirection.EN_TO_ZH


def _sheet(*, characters=(), expressions=()) -> StyleSheet:
    return StyleSheet(characters=list(characters), expressions=list(expressions))


# -- pure functions ------------------------------------------------------------------


class MergeTests:
    def test_the_most_common_value_wins_and_the_rest_are_kept_for_review(self):
        merged = merge_style_candidates(
            [
                _sheet(characters=[StyleCharacter(name="Mouse", pronoun="它", addressed_as="您", evidence=["A"])]),
                _sheet(characters=[StyleCharacter(name="mouse", pronoun="他", voice="formal, easily offended", evidence=["B"])]),
                _sheet(characters=[StyleCharacter(name="Mouse", pronoun="它", evidence=["C"])]),
            ],
            EN_ZH,
        )
        (mouse,) = merged.characters
        assert (mouse.name, mouse.pronoun, mouse.addressed_as) == ("Mouse", "它", "您")
        assert mouse.alternatives == ["他"]
        assert mouse.voice == "formal, easily offended"
        assert mouse.evidence == ["A", "B", "C"]

    def test_expressions_merge_by_source_text(self):
        merged = merge_style_candidates(
            [
                _sheet(expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！")]),
                _sheet(expressions=[StyleExpression(source="off with her head!", rendering="砍掉她的头！")]),
                _sheet(expressions=[StyleExpression(source="Off with her head!", rendering="砍了她的脑袋！")]),
            ],
            EN_ZH,
        )
        (expression,) = merged.expressions
        assert expression.rendering == "砍掉她的头！" and expression.alternatives == ["砍了她的脑袋！"]
        assert merged.conventions.dash == "——"


class ReviewTests:
    def test_each_entry_is_approved_revised_or_rejected(self):
        sheet = _sheet(
            characters=[
                StyleCharacter(name="Mouse", pronoun="它", alternatives=["他"]),
                StyleCharacter(name="Nobody", pronoun="他"),
            ],
            expressions=[StyleExpression(source="Off with her head!", rendering="砍她的头！")],
        )
        assert style_entry_ids(sheet) == ["C0001", "C0002", "X0001"]
        reviewed = apply_style_review(sheet, [
            StyleCharacterDecision(entry_id="C0001", action="revise", pronoun="他", addressed_as="", reason="speaks and acts as a person"),
            StyleCharacterDecision(entry_id="C0002", action="reject", pronoun="他", addressed_as="", reason="not a character"),
            StyleExpressionDecision(entry_id="X0001", action="revise", rendering="砍掉她的头！", reason="set phrase"),
        ])
        assert [(c.name, c.pronoun, c.alternatives) for c in reviewed.characters] == [("Mouse", "他", [])]
        assert [e.rendering for e in reviewed.expressions] == ["砍掉她的头！"]

    def test_the_review_schema_requires_one_decision_per_entry_with_final_values(self):
        sheet = _sheet(
            characters=[StyleCharacter(name="Gryphon", pronoun="它")],
            expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！")],
        )
        schema = build_style_review_schema(sheet)
        character = {"entry_id": "C0001", "action": "revise", "pronoun": "他", "addressed_as": "你", "reason": "a person"}
        expression = {"entry_id": "X0001", "action": "approve", "rendering": "砍掉她的头！", "reason": "correct as drafted"}
        assert schema.model_validate({"characters": [character], "expressions": [expression]})
        with pytest.raises(ValueError):  # a missing decision
            schema.model_validate({"characters": [], "expressions": [expression]})
        with pytest.raises(ValueError):  # an unknown entry
            schema.model_validate({"characters": [{**character, "entry_id": "C0009"}], "expressions": [expression]})
        # Regression: a "revise" that states its correction only in the reason used to
        # leave the Gryphon as 它. The corrected values are now required.
        without_values = {"entry_id": "C0001", "action": "revise", "reason": "should be 他, a person"}
        with pytest.raises(ValueError):
            schema.model_validate({"characters": [without_values], "expressions": [expression]})

    def test_a_sheet_without_characters_still_gets_a_schema(self):
        schema = build_style_review_schema(_sheet(expressions=[StyleExpression(source="Come on, then!", rendering="来吧！")]))
        assert schema.model_validate({
            "characters": [],
            "expressions": [{"entry_id": "X0001", "action": "approve", "rendering": "来吧！", "reason": "correct as drafted"}],
        })


class UseTests:
    def test_only_entries_present_in_the_text_are_selected_within_the_budget(self):
        sheet = _sheet(
            characters=[StyleCharacter(name="Mouse", pronoun="它"), StyleCharacter(name="Hatter", pronoun="他")],
            expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！")],
        )
        text = "“Off with her <I000>head</I000>!” The Mouse shrank back. Mousetrap."
        selected = select_relevant_style(sheet, text.replace("<I000>head</I000>", "head"), max_entries=5)
        assert [c.name for c in selected.characters] == ["Mouse"]
        assert [e.source for e in selected.expressions] == ["Off with her head!"]
        assert select_relevant_style(sheet, text, max_entries=1).characters == []

    def test_the_prompt_block_is_empty_without_entries(self):
        assert format_relevant_style(StyleSheet()) == ""
        block = format_relevant_style(_sheet(characters=[StyleCharacter(name="Mouse", pronoun="它", addressed_as="您")]))
        assert "Mouse: usually 它; often addressed as 您" in block and "dash ——" in block
        # Source wording and the scene come first; the notes never read as rules.
        assert "context only" in block and "source wording and the scene decide" in block

    def test_expression_renderings_are_checked_on_unedited_segments(self):
        sheet = _sheet(expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！")])
        assert [i.segment_id for i in expression_issues([
            ("S1", "“Off with her head!” she said.", "“砍掉她的头！”她说。"),
            ("S2", "“Off with her head!” she roared.", "“砍了她！”她吼道。"),
        ], sheet)] == ["S2"]
        segments = [
            BookSegment("D1", 1, "S2", "“Off with her head!” she roared.", "“砍了她！”她吼道。", edited=True),
        ]
        assert book_consistency_issues(segments, ConsistencySettings(), sheet) == []


def test_the_extraction_prompt_is_unchanged_when_the_style_sheet_is_off():
    from book_agent.glossary import GlossaryChunk, GlossaryChunkPiece
    from book_agent.stages.glossary import _chunk_extraction_schema, _style_extraction_instructions

    chunk = GlossaryChunk(
        chunk_id="glossary-00001",
        pieces=[GlossaryChunkPiece(reference_id="D0000-S000001", document_path="a.xhtml", text="The Mouse spoke.")],
        estimated_tokens=5,
    )
    off, on = AppConfig(), AppConfig.model_validate({"consistency": {"style_sheet": {"enabled": True}}})
    assert _style_extraction_instructions(off) == ""
    assert "`style` object" in build_extraction_prompt(chunk, EN_ZH) + _style_extraction_instructions(on)
    assert "style" not in _chunk_extraction_schema(off, chunk).model_fields
    assert "style" in _chunk_extraction_schema(on, chunk).model_fields


# -- the glossary stages with the style sheet on -------------------------------------------


CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>Chapter One</h1><p>The Mouse looked at Alice with great suspicion.</p>
<p>Off with her head! the Queen shouted once more.</p></body></html>"""


class SchemaFakeClient:
    """Validates plain dict payloads against the schema each call actually sends."""

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def report_progress(self, event) -> None:
        pass

    def generate_structured(self, prompt: str, schema: type, **kwargs: Any) -> StructuredGenerationResult:
        self.prompts.append(prompt)
        value = schema.model_validate(self.payloads.pop(0))
        return StructuredGenerationResult(
            value=value,
            generation=GenerationResult(
                content=value.model_dump_json(),
                thinking="",
                metrics=GenerationMetrics(done_reason="stop", prompt_eval_count=100, eval_count=20),
            ),
        )


def _config(style_review: str = "glossary", **workflow) -> AppConfig:
    return AppConfig.model_validate({
        "consistency": {"style_sheet": {"enabled": True, "review": style_review}},
        "workflow": {"require_glossary_review": False, **workflow},
    })


def _extracted(directory, config):
    from book_agent.stages.decompile import load_decompile_manifest, run_decompile_stage
    from book_agent.stages.glossary import run_glossary_extraction_stage, run_glossary_resolution_stage
    from book_agent.workspace import create_job_workspace

    base = Path(directory)
    workspace = create_job_workspace(make_epub(base / "style.epub", chapter=CHAPTER), base / "runs", config, job_id="style")
    run_decompile_stage(workspace)
    ids = {
        segment.text.split()[1] if segment.text.startswith("The Mouse") else segment.text.split()[0]: segment.segment_id
        for document in load_decompile_manifest(workspace).documents
        for segment in document.segments
    }
    client = SchemaFakeClient([{
        "entries": [],
        "style": {
            "characters": [{"name": "Mouse", "pronoun": "它", "addressed_as": "您", "voice": "formal", "evidence": [ids["Mouse"]]}],
            "expressions": [{"source": "Off with her head!", "rendering": "砍掉她的头！", "evidence": [ids["Off"]]}],
        },
    }])
    run_glossary_extraction_stage(workspace, config, client)
    assert "`style` object" in client.prompts[0]
    run_glossary_resolution_stage(workspace, config, None)
    return workspace, ids


class GlossaryGateTests:
    def test_extraction_and_resolution_produce_a_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _ = _extracted(directory, _config())
            draft = load_style_sheet(workspace, "style_draft")
            assert [(c.name, c.pronoun, c.addressed_as) for c in draft.characters] == [("Mouse", "它", "您")]
            assert [(e.source, e.rendering) for e in draft.expressions] == [("Off with her head!", "砍掉她的头！")]

    def test_without_review_the_draft_is_approved_as_is(self):
        from book_agent.stages.glossary import run_glossary_approval_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace, _ = _extracted(directory, config)
            run_glossary_approval_stage(workspace, config)
            approved = load_style_sheet(workspace)
            assert approved.characters[0].pronoun == "它" and approved.expressions[0].rendering == "砍掉她的头！"

    def test_llm_review_revises_the_draft_in_one_call(self):
        from book_agent.stages.glossary import run_glossary_approval_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config(llm_glossary_review=True)
            workspace, _ = _extracted(directory, config)
            client = SchemaFakeClient([{
                "characters": [
                    {"entry_id": "C0001", "action": "revise", "pronoun": "他", "addressed_as": "您", "reason": "acts as a person"},
                ],
                "expressions": [
                    {"entry_id": "X0001", "action": "approve", "rendering": "砍掉她的头！", "reason": "the Queen's refrain"},
                ],
            }])
            run_glossary_approval_stage(workspace, config, llm_review=True, client=client)
            assert len(client.prompts) == 1 and "C0001 character 'Mouse'" in client.prompts[0]
            assert "The Mouse looked at Alice" in client.prompts[0]  # evidence is quoted
            assert load_style_sheet(workspace).characters[0].pronoun == "他"

    def test_a_human_reviewed_file_is_approved(self):
        from book_agent.stages.glossary import run_glossary_approval_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config(style_review="human")
            workspace, _ = _extracted(directory, config)
            reviewed = Path(directory) / "style.reviewed.json"
            reviewed.write_text(
                _sheet(characters=[StyleCharacter(name="Mouse", pronoun="他", addressed_as="您")]).model_dump_json(),
                encoding="utf-8",
            )
            run_glossary_approval_stage(workspace, config, reviewed_style_file=reviewed)
            approved = load_style_sheet(workspace)
            assert approved.characters[0].pronoun == "他" and approved.expressions == []

    def test_by_default_the_gate_waits_for_a_person_even_with_llm_glossary_review(self):
        from book_agent.pipeline_state import WorkflowStage
        from book_agent.stages.glossary import GlossaryApprovalRequired, run_glossary_approval_stage
        from book_agent.state import connect_state, get_stage_status

        with tempfile.TemporaryDirectory() as directory:
            config = _config(style_review="human", llm_glossary_review=True)
            assert AppConfig().consistency.style_sheet.review == "human"
            workspace, _ = _extracted(directory, config)
            client = SchemaFakeClient([])  # no model call may happen
            with pytest.raises(GlossaryApprovalRequired, match="style sheet needs a human review"):
                run_glossary_approval_stage(workspace, config, llm_review=True, client=client)
            connection = connect_state(workspace.state_file)
            try:
                record = get_stage_status(connection, WorkflowStage.APPROVE_GLOSSARY.value)
            finally:
                connection.close()
            assert (record["status"], record["message"]) == ("paused", "style sheet review required")
            assert client.prompts == [] and load_style_sheet(workspace) is None

            # The reviewed sheet (from the Glossary tab or --style) lets the gate continue,
            # with the glossary still LLM-reviewed and no LLM style review.
            reviewed = Path(directory) / "style.reviewed.json"
            reviewed.write_text(load_style_sheet(workspace, "style_draft").model_dump_json(), encoding="utf-8")
            run_glossary_approval_stage(
                workspace, config, llm_review=True, client=client, reviewed_style_file=reviewed
            )
            assert client.prompts == []
            assert load_style_sheet(workspace).characters[0].name == "Mouse"

    def test_approved_entries_reach_preprocessing_and_the_translation_prompt(self):
        from book_agent.stages.glossary import run_glossary_approval_stage
        from book_agent.stages.preprocess import load_preprocessed_documents, run_preprocessing_stage
        from book_agent.stages.translate import _build_chunk_prompt, build_translation_chunks

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace, _ = _extracted(directory, config)
            run_glossary_approval_stage(workspace, config)
            run_preprocessing_stage(workspace, config)
            (document,) = load_preprocessed_documents(workspace)
            assert [c.name for c in document.relevant_style.characters] == ["Mouse"]
            chunks = build_translation_chunks(document, 4000)
            prompt = _build_chunk_prompt(document, chunks[0], [], config)
            assert "Book style sheet" in prompt and "Mouse: usually 它; often addressed as 您" in prompt
            assert "'Off with her head!' => '砍掉她的头！'" in prompt

            # Switched off, the same document gives the prompt without a style block.
            off = AppConfig.model_validate({"workflow": {"require_glossary_review": False}})
            assert "Book style sheet" not in _build_chunk_prompt(document, chunks[0], [], off)


def test_prose_rewrite_keeps_approved_expressions():
    from types import SimpleNamespace
    from unittest.mock import patch

    from book_agent.prose_rewrite import apply_validated_prose_decision

    source = SimpleNamespace(
        segments=[SimpleNamespace(segment_id="S1", processed_text="“Off with her head!” she roared.")],
        relevant_glossary=[],
        relevant_style=_sheet(expressions=[StyleExpression(source="Off with her head!", rendering="砍掉她的头！")]),
    )
    repaired = SimpleNamespace(document=SimpleNamespace(
        segments=[SimpleNamespace(segment_id="S1", translated_text="“砍掉她的头！”她吼道。")]
    ))
    decision = SimpleNamespace(action="rewrite", segment_id="S1", rewritten_text="“把她的头砍了！”她怒吼。", reason="livelier")
    config = AppConfig.model_validate({"consistency": {"style_sheet": {"enabled": True}}})
    passed = SimpleNamespace(passed=True, issues=[])
    with patch("book_agent.prose_rewrite.validate_repair_output", return_value=("“把她的头砍了！”她怒吼。", passed)), \
            patch("book_agent.prose_rewrite.protected_rewrite_changes", return_value=[]):
        _, applied, message = apply_validated_prose_decision(source, repaired, decision, config)
    assert not applied and "砍掉她的头！" in message


def test_a_preprocessed_document_without_a_style_sheet_serializes_as_before():
    from book_agent.preprocessing import PreprocessedDocument

    document = PreprocessedDocument(order=1, manifest_id="m", archive_path="a", source_sha256="x", segments=[])
    assert "relevant_style" not in json.loads(document.model_dump_json())
    styled = document.model_copy(update={"relevant_style": StyleSheet()})
    assert PreprocessedDocument.model_validate_json(styled.model_dump_json()).relevant_style == StyleSheet()
