import re

import pytest

from book_agent.config import AppConfig
from book_agent.glossary import estimate_tokens
from book_agent.languages import TranslationDirection
from book_agent.preprocessing import PreprocessedDocument, PreprocessedSegment
from book_agent.schemas import GlossaryCategory, GlossaryEntry
from book_agent.translation import (
    InlineMarkerPlacement,
    InlineMarkerPlacementResult,
    InlineMarkerSelection,
    InlineMarkerSelectionResult,
    SingleInlineMarkerPlacement,
    SingleInlineMarkerPlacementResult,
    SingleInlineMarkerSelection,
    SingleInlineMarkerSelectionResult,
    TranslatedDocument,
    TranslationChunk,
    TranslationChunkPiece,
    TranslationOutputError,
    apply_inline_marker_placements,
    apply_inline_marker_selections,
    apply_single_inline_marker_selections,
    apply_single_inline_marker_placements,
    assemble_translated_segments,
    build_inline_marker_placement_prompt,
    build_inline_marker_selection_prompt,
    build_single_inline_marker_selection_prompt,
    build_translation_chunks,
    build_translation_prompt,
    calculate_translation_source_budget,
    constrain_translation_chunks_by_prompt,
    format_relevant_glossary,
    parse_marked_translation,
    render_translated_document,
    restore_deterministic_inline_markers,
    validate_translation_output,
)


def make_document(texts=None):
    texts = texts or ["Hello world.", "A second paragraph."]
    return PreprocessedDocument(
        order=2,
        manifest_id="chapter",
        archive_path="OEBPS/chapter.xhtml",
        source_sha256="a" * 64,
        segments=[
            PreprocessedSegment(
                segment_id=f"D0002-S{index:06d}",
                original_text=text,
                processed_text=text,
            )
            for index, text in enumerate(texts, start=1)
        ],
    )


def make_chunk():
    return TranslationChunk(
        chunk_id="translate-0002-00001",
        document_id="chapter",
        document_order=2,
        estimated_source_tokens=4,
        pieces=[
            TranslationChunkPiece(
                reference_id="D0002-S000001",
                segment_id="D0002-S000001",
                part_number=1,
                source_text="Hello world.",
            ),
            TranslationChunkPiece(
                reference_id="D0002-S000002",
                segment_id="D0002-S000002",
                part_number=1,
                source_text="Good night.",
            ),
        ],
    )


class TranslationCoreTests:
    def test_budget_uses_actual_prompt_and_glossary(self):
        config = AppConfig()
        short = calculate_translation_source_budget(config, "style", "")
        longer = calculate_translation_source_budget(config, "style", "汉" * 20_000)
        assert short == 20_000
        assert longer < short

    def test_budget_rejects_no_source_capacity(self):
        with pytest.raises(ValueError, match="no translation source capacity"):
            calculate_translation_source_budget(AppConfig(), "style", "汉" * 60_000)

    def test_chunks_cover_segments_and_split_oversized_text(self):
        document = make_document(["one two three four five", "short"])
        chunks = build_translation_chunks(document, 2)
        pieces = [piece for chunk in chunks for piece in chunk.pieces]
        assert len(pieces) > 2
        assert {piece.segment_id for piece in pieces} == ({
            "D0002-S000001", "D0002-S000002"
        })
        assert any(piece.reference_id.endswith("-P001") for piece in pieces)
        assert all(chunk.estimated_source_tokens <= 2 for chunk in chunks)

    def test_chunks_require_positive_budget(self):
        with pytest.raises(ValueError):
            build_translation_chunks(make_document(), 0)

    def test_chunks_are_split_by_fully_rendered_prompt_size(self):
        document = make_document(["word " * 40 for _ in range(8)])
        chunks = build_translation_chunks(document, 1_000)
        bounded = constrain_translation_chunks_by_prompt(
            chunks,
            180,
            lambda chunk: "fixed prompt " + chunk.render_source(),
        )
        assert len(bounded) > 1
        assert (all(
                estimate_tokens("fixed prompt " + chunk.render_source()) <= 180
                for chunk in bounded
            ))
        assert [chunk.chunk_id for chunk in bounded] == [f"translate-0002-{index:05d}" for index in range(1, len(bounded) + 1)]

    def test_glossary_is_direction_aware(self):
        entry = GlossaryEntry(
            english="Stone Heron",
            chinese="石鹭",
            note="character",
            category=GlossaryCategory.PERSON,
            aliases=["Heron"],
        )
        forward = format_relevant_glossary([entry], TranslationDirection.EN_TO_ZH)
        reverse = format_relevant_glossary([entry], TranslationDirection.ZH_TO_EN)
        assert "Stone Heron => 石鹭" in forward
        assert f"[{GlossaryCategory.PERSON.value}]" in forward
        assert "aliases: Heron" in forward
        assert "石鹭 => Stone Heron" in reverse
        assert "aliases" not in reverse

    def test_prompt_combines_style_naturalness_glossary_and_markers(self):
        entry = GlossaryEntry(
            english="Heron", chinese="兔子", category=GlossaryCategory.PERSON
        )
        config = AppConfig.model_validate(
            {"translation": {"style": "fantasy", "de_ai_strength": "moderate"}}
        )
        prompt = build_translation_prompt(make_chunk(), [entry], config)
        assert "Naturalness overlay" in prompt
        assert "model-like prose" in prompt
        assert "Heron => 兔子" in prompt
        assert "<D0002-S000001>Hello world.</D0002-S000001>" in prompt
        assert "Chinese" in prompt
        assert "different meaning, ignore that glossary entry" in prompt
        assert "source text is preserved" in prompt
        assert "Required inline-marker manifest" in prompt
        assert "D0002-S000001: (no inline markers)" in prompt
        assert "Example 1 valid translation" in prompt
        assert "Example 2 valid translation" in prompt

    def test_prompt_can_treat_glossary_as_contextual_preference(self):
        entry = GlossaryEntry(
            english="Order",
            chinese="教团",
            note="仅指故事中的组织",
            category=GlossaryCategory.ORGANIZATION,
        )
        config = AppConfig.model_validate(
            {"translation": {"use_glossary_strictly": False}}
        )
        prompt = build_translation_prompt(make_chunk(), [entry], config)
        assert "context-sensitive preferences" in prompt
        assert "category and note" in prompt

    def test_prompt_can_disable_naturalness_overlay(self):
        config = AppConfig.model_validate(
            {"translation": {"de_ai_enabled": False}}
        )
        assert "Naturalness overlay" not in build_translation_prompt(make_chunk(), [], config)

    def test_prompt_marker_examples_support_none_and_one_shot(self):
        none_config = AppConfig.model_validate(
            {"translation": {"marker_examples": "none"}}
        )
        none_prompt = build_translation_prompt(make_chunk(), [], none_config)
        assert "Marker examples" not in none_prompt
        one_config = AppConfig.model_validate(
            {"translation": {"marker_examples": "one-shot"}}
        )
        one_prompt = build_translation_prompt(make_chunk(), [], one_config)
        assert "Example 1 valid translation" in one_prompt
        assert "Example 2 valid translation" not in one_prompt

    def test_parse_valid_marked_translation(self):
        output = "<A>甲<em>强调</em></A>\n\n<B>乙</B>"
        assert parse_marked_translation(output, ["A", "B"]) == {"A": "甲<em>强调</em>", "B": "乙"}

    def test_parse_rejects_contract_violations(self, subtests):
        invalid = [
            "commentary<A>甲</A><B>乙</B>",
            "<B>乙</B><A>甲</A>",
            "<A>甲</B><B>乙</B>",
            "<A>甲</A><A>又甲</A>",
        ]
        for output in invalid:
            with subtests.test(output=output), pytest.raises(TranslationOutputError):
                parse_marked_translation(output, ["A", "B"])

    def test_validation_accepts_both_directions(self):
        forward = "<D0002-S000001>你好，世界。</D0002-S000001>" \
                  "<D0002-S000002>晚安。</D0002-S000002>"
        _, result = validate_translation_output(forward, make_chunk(), TranslationDirection.EN_TO_ZH)
        assert result.passed

    def test_validation_recovers_wrong_closing_ids_when_openings_are_exact(self):
        output = (
            "<D0002-S000001>\u4f60\u597d\uff0c\u4e16\u754c\u3002</D0002-S000002>"
            "<D0002-S000002>\u665a\u5b89\u3002</D0002-S000002>"
        )
        translations, result = validate_translation_output(
            output, make_chunk(), TranslationDirection.EN_TO_ZH
        )
        assert result.passed
        assert set(translations) == {"D0002-S000001", "D0002-S000002"}

    def test_validation_recovers_opening_implied_by_previous_closing(self):
        output = (
            "<D0002-S000001>\u4f60\u597d\uff0c\u4e16\u754c\u3002</D0002-S000002>"
            "\u665a\u5b89\u3002</D0002-S000002>"
        )
        translations, result = validate_translation_output(
            output, make_chunk(), TranslationDirection.EN_TO_ZH
        )
        assert result.passed
        assert set(translations) == {"D0002-S000001", "D0002-S000002"}

    def test_validation_does_not_recover_missing_opening_boundary(self):
        output = (
            "<D0002-S000001>\u4f60\u597d\uff0c\u4e16\u754c\u3002</D0002-S000001>"
            "\u665a\u5b89\u3002</D0002-S000002>"
        )
        _, result = validate_translation_output(
            output, make_chunk(), TranslationDirection.EN_TO_ZH
        )
        assert not result.passed
        assert result.issues[0].code == "marker_contract"
        reverse_chunk = make_chunk().model_copy(deep=True)
        reverse_chunk.pieces[0].source_text = "你好"
        reverse_chunk.pieces[1].source_text = "晚安"
        reverse = "<D0002-S000001>Hello.</D0002-S000001>" \
                  "<D0002-S000002>Good night.</D0002-S000002>"
        _, result = validate_translation_output(reverse, reverse_chunk, TranslationDirection.ZH_TO_EN)
        assert result.passed

    def test_validation_retains_exact_prefix_before_truncated_passage(self):
        output = (
            "<D0002-S000001>你好。</D0002-S000001>"
            "<D0002-S000002>未完"
        )
        translations, result = validate_translation_output(
            output, make_chunk(), TranslationDirection.EN_TO_ZH
        )
        assert not result.passed
        assert translations == {"D0002-S000001": "你好。"}
        assert result.issues[0].code == "marker_contract"
        assert result.issues[0].reference_id == "D0002-S000002"

    def test_validation_reports_marker_empty_language_and_exact_source(self):
        _, marker = validate_translation_output("bad", make_chunk(), TranslationDirection.EN_TO_ZH)
        assert marker.issues[0].code == "marker_contract"
        empty = "<D0002-S000001></D0002-S000001><D0002-S000002>晚安</D0002-S000002>"
        _, result = validate_translation_output(empty, make_chunk(), TranslationDirection.EN_TO_ZH)
        assert "empty_translation" in {issue.code for issue in result.issues}
        untranslated = "<D0002-S000001>Hello world.</D0002-S000001>" \
                       "<D0002-S000002>Good night.</D0002-S000002>"
        _, result = validate_translation_output(untranslated, make_chunk(), TranslationDirection.EN_TO_ZH)
        codes = {issue.code for issue in result.issues}
        assert "target_language_missing" in codes
        assert "untranslated_exact" in codes

    def test_validation_accepts_approved_preserved_identifier_with_punctuation(self):
        chunk = make_chunk()
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0001-S000001",
                segment_id="D0001-S000001",
                part_number=1,
                source_text='"Flup."',
            )
        ]
        glossary = [
            GlossaryEntry(
                english="FLUP",
                chinese="FLUP",
                category=GlossaryCategory.TECHNOLOGY,
                note="preserve this coined identifier",
            )
        ]
        output = "<D0001-S000001>\u201cFLUP\u3002\u201d</D0001-S000001>"

        _, approved = validate_translation_output(
            output,
            chunk,
            TranslationDirection.EN_TO_ZH,
            glossary,
        )
        _, unapproved = validate_translation_output(
            output,
            chunk,
            TranslationDirection.EN_TO_ZH,
        )

        assert approved.passed
        assert not unapproved.passed
        assert "target_language_missing" in {issue.code for issue in unapproved.issues}

    def test_validation_requires_inline_marker_sequence(self):
        chunk = make_chunk()
        chunk.pieces[0].source_text = "Hello <I000>small</I000> world."
        output = (
            "<D0002-S000001>你好，小世界。</D0002-S000001>"
            "<D0002-S000002>晚安。</D0002-S000002>"
        )
        _, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert "protected_marker_mismatch" in {item.code for item in result.issues}
        preserved = output.replace("你好，小世界。", "你好<I000>小</I000>世界。")
        _, result = validate_translation_output(
            preserved, chunk, TranslationDirection.EN_TO_ZH
        )
        assert result.passed

    def test_validation_requires_obfuscated_full_span_wrapper_scope(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Qir signal one. Qir signal two.</I000>"
        )
        misplaced = (
            "<D0002-S000001><I000>\u5947\u5c14\u4fe1\u53f7\u4e00\u3002</I000>"
            "\u5947\u5c14\u4fe1\u53f7\u4e8c\u3002</D0002-S000001>"
        )
        _, result = validate_translation_output(
            misplaced, chunk, TranslationDirection.EN_TO_ZH
        )
        assert not result.passed
        assert "protected_marker_mismatch" in {item.code for item in result.issues}

    def test_validation_strips_model_invented_inline_markers(self):
        chunk = make_chunk()
        chunk.pieces[0].source_text = "Hello <I000>small</I000> world."
        output = (
            "<D0002-S000001>你好<I000>小</I000><I003>世界</I003>。</D0002-S000001>"
            "<D0002-S000002><I000>晚安</I000>。</D0002-S000002>"
        )
        translations, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert result.passed
        assert translations["D0002-S000001"] == "你好<I000>小</I000>世界。"
        assert translations["D0002-S000002"] == "晚安。"

    def test_structured_marker_placement_preserves_translation_exactly(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "What <I000>can</I000> make <I001>some</I001> difference?"
        )
        translations = {"D0002-S000001": "究竟会带来某种变化？"}
        prompt = build_inline_marker_placement_prompt(chunk, translations)
        assert "Required parts count: 5" in prompt
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=["", "究竟", "会带来", "某种", "变化？"],
                )
            ]
        )
        repaired = apply_inline_marker_placements(chunk, translations, result)
        assert repaired["D0002-S000001"] == "<I000>究竟</I000>会带来<I001>某种</I001>变化？"

    def test_obfuscated_nested_marker_placement_preserves_source_nesting(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "A <I000>qir <I001>vel</I001></I000> signal."
        )
        translations = {"D0002-S000001": "zorvak pulse"}
        prompt = build_inline_marker_placement_prompt(chunk, translations)
        assert "Required marker sequence: <I000>, <I001>, </I001>, </I000>" in prompt
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=["", "zor", "vak", " pulse", ""],
                )
            ]
        )
        repaired = apply_inline_marker_placements(chunk, translations, result)
        assert repaired["D0002-S000001"] == "<I000>zor<I001>vak</I001> pulse</I000>"

    def test_obfuscated_full_span_wrapper_needs_no_model_split_points(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Qir <I001>vel</I001> signal.</I000>"
        )
        translations = {"D0002-S000001": "zorvak pulse"}
        prompt = build_inline_marker_placement_prompt(chunk, translations)
        assert "Automatically restored full-span wrapper IDs: 000" in prompt
        assert "Required marker sequence: <I001>, </I001>" in prompt
        assert "Required parts count: 3" in prompt
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=["zor", "vak", " pulse"],
                )
            ]
        )
        repaired = apply_inline_marker_placements(chunk, translations, result)
        assert repaired["D0002-S000001"] == "<I000>zor<I001>vak</I001> pulse</I000>"

    def test_obfuscated_empty_inner_marker_is_restored_deterministically(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Vrax <I001></I001> nel tor.</I000>"
        )
        translations = {"D0002-S000001": "zorvak pulse"}

        repaired = restore_deterministic_inline_markers(chunk, translations)

        assert repaired is not None
        rendered = repaired["D0002-S000001"]
        assert re.findall(r"</?I\d{3}>", rendered) == ["<I000>", "<I001>", "</I001>", "</I000>"]
        assert re.sub(r"</?I\d{3}>", "", rendered) == "zorvak pulse"

    def test_obfuscated_repeated_equivalent_span_uses_surviving_marker(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Tor <I001>Qel,</I001> vrax <I002>Qel</I002> zod.</I000>"
        )
        translations = {
            "D0002-S000001": (
                "<I000>zor <I001>vak</I001> mid vak end</I000>"
            )
        }

        repaired = restore_deterministic_inline_markers(chunk, translations)

        assert repaired["D0002-S000001"] == "<I000>zor <I001>vak</I001> mid <I002>vak</I002> end</I000>"

    def test_obfuscated_repeated_span_rejects_ambiguous_extra_occurrence(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Tor <I001>Qel,</I001> vrax <I002>Qel</I002> zod.</I000>"
        )
        translations = {
            "D0002-S000001": (
                "<I000>vak zor <I001>vak</I001> mid vak end</I000>"
            )
        }

        repaired = restore_deterministic_inline_markers(chunk, translations)

        assert repaired is None

    def test_obfuscated_nonempty_inner_marker_requires_model_placement(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Vrax <I001>nel</I001> tor.</I000>"
        )

        repaired = restore_deterministic_inline_markers(
            chunk,
            {"D0002-S000001": "zorvak pulse"},
        )

        assert repaired is None

    def test_structured_marker_placement_rejects_rewritten_translation(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = "What <I000>can</I000> change?"
        translations = {"D0002-S000001": "会有什么变化？"}
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=["", "究竟", "会有什么变化？"],
                )
            ]
        )
        with pytest.raises(TranslationOutputError, match="altered immutable"):
            apply_inline_marker_placements(chunk, translations, result)

    def test_obfuscated_flat_marker_placement_recovers_duplicated_context(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Vrax <I001>nel</I001> tor <I002>qir</I002>.</I000>"
        )
        translations = {
            "D0002-S000001": "zor alpha mid beta end beta"
        }
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=[
                        "zor ",
                        "alpha",
                        " mid beta",
                        "beta",
                        " end beta",
                    ],
                )
            ]
        )

        repaired = apply_inline_marker_placements(chunk, translations, result)

        assert repaired["D0002-S000001"] == ("<I000>zor <I001>alpha</I001> mid "
            "<I002>beta</I002> end beta</I000>")

    def test_obfuscated_nested_marker_placement_recovers_duplicated_context(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "<I000>Vrax <I001><I002>nel</I002></I001> tor "
            "<I003><I004>qir</I004></I003> <I005></I005>zod.</I000>"
        )
        translations = {
            "D0002-S000001": "zor alpha mid beta without gamma"
        }
        result = InlineMarkerPlacementResult(
            placements=[
                InlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    parts=[
                        "zor alpha mid ",
                        "",
                        "alpha",
                        "",
                        " mid ",
                        "",
                        "beta",
                        "",
                        " without",
                        "",
                        " gamma",
                    ],
                )
            ]
        )

        repaired = apply_inline_marker_placements(chunk, translations, result)

        assert repaired["D0002-S000001"] == ("<I000>zor <I001><I002>alpha</I002></I001> mid "
            "<I003><I004>beta</I004></I003> without"
            "<I005></I005> gamma</I000>")

    def test_single_marker_placement_expands_unique_exact_local_window(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = "Where <I000>can</I000> they be?"
        translations = {"D0002-S000001": "前文。我到底把它们放在哪儿了？后文。"}
        result = SingleInlineMarkerPlacementResult(
            placements=[
                SingleInlineMarkerPlacement(
                    reference_id="D0002-S000001",
                    before="我到底把它们放在",
                    emphasized="哪儿",
                    after="了？",
                )
            ]
        )
        repaired = apply_single_inline_marker_placements(chunk, translations, result)
        assert repaired["D0002-S000001"] == "前文。我到底把它们放在<I000>哪儿</I000>了？后文。"

    def test_single_marker_selection_inserts_exact_repeated_occurrence(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = "Did <I000>we</I000> leave after we spoke?"
        translations = {"D0002-S000001": "我们说完后，我们离开了吗？"}
        prompt = build_single_inline_marker_selection_prompt(chunk, translations)
        assert "copy into emphasized" in prompt
        result = SingleInlineMarkerSelectionResult(
            selections=[
                SingleInlineMarkerSelection(
                    reference_id="D0002-S000001",
                    emphasized="我们",
                    occurrence=1,
                )
            ]
        )
        repaired = apply_single_inline_marker_selections(chunk, translations, result)
        assert repaired["D0002-S000001"] == "<I000>我们</I000>说完后，我们离开了吗？"

    def test_single_marker_selection_rejects_nonexistent_target_span(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = "Did <I000>we</I000> leave?"
        translations = {"D0002-S000001": "我们离开了吗？"}
        result = SingleInlineMarkerSelectionResult(
            selections=[
                SingleInlineMarkerSelection(
                    reference_id="D0002-S000001",
                    emphasized="咱们",
                )
            ]
        )
        with pytest.raises(TranslationOutputError, match="exact target occurrence"):
            apply_single_inline_marker_selections(chunk, translations, result)

    def test_obfuscated_flat_multi_marker_selection_preserves_immutable_text(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "The <I000>qelm</I000> crossed the ridge with "
            "<I001>two vraks</I001>."
        )
        translations = {"D0002-S000001": "凯尔姆带着两只弗拉克越过山脊。"}
        prompt = build_inline_marker_selection_prompt(chunk, translations)
        assert "Marker ID: I000" in prompt
        assert "Marker ID: I001" in prompt
        result = InlineMarkerSelectionResult(
            selections=[
                InlineMarkerSelection(
                    reference_id="D0002-S000001",
                    marker_id="I000",
                    emphasized="凯尔姆",
                ),
                InlineMarkerSelection(
                    reference_id="D0002-S000001",
                    marker_id="I001",
                    emphasized="两只弗拉克",
                ),
            ]
        )
        repaired = apply_inline_marker_selections(chunk, translations, result)
        assert repaired["D0002-S000001"] == "<I000>凯尔姆</I000>带着<I001>两只弗拉克</I001>越过山脊。"

    def test_obfuscated_flat_multi_marker_selection_rejects_reordered_spans(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = "<I000>qelm</I000> then <I001>vrak</I001>"
        translations = {"D0002-S000001": "弗拉克随后遇见凯尔姆"}
        result = InlineMarkerSelectionResult(
            selections=[
                InlineMarkerSelection(
                    reference_id="D0002-S000001",
                    marker_id="I000",
                    emphasized="凯尔姆",
                ),
                InlineMarkerSelection(
                    reference_id="D0002-S000001",
                    marker_id="I001",
                    emphasized="弗拉克",
                ),
            ]
        )
        with pytest.raises(TranslationOutputError, match="source order"):
            apply_inline_marker_selections(chunk, translations, result)

    def test_obfuscated_marker_selection_restores_empty_and_nonempty_pairs(self):
        chunk = make_chunk()
        chunk.pieces = [chunk.pieces[0]]
        chunk.pieces[0].source_text = (
            "Qelm met <I000></I000>vraks called <I001>zor</I001>."
        )
        translations = {"D0002-S000001": "凯尔姆遇见了名为佐尔的弗拉克。"}
        result = InlineMarkerSelectionResult(
            selections=[
                InlineMarkerSelection(
                    reference_id="D0002-S000001",
                    marker_id="I001",
                    emphasized="佐尔",
                )
            ]
        )
        repaired = apply_inline_marker_selections(chunk, translations, result)
        rendered = repaired["D0002-S000001"]
        assert re.findall(r"</?I\d{3}>", rendered) == ["<I000>", "</I000>", "<I001>", "</I001>"]
        assert re.sub(r"</?I\d{3}>", "", rendered) == translations["D0002-S000001"]

    def test_validation_allows_unchanged_separator_with_inline_markers(self):
        separator = "* * *<I000></I000> * * *<I001></I001>"
        chunk = make_chunk().model_copy(deep=True)
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0002-S000001",
                segment_id="D0002-S000001",
                part_number=1,
                source_text=separator,
            )
        ]
        output = f"<D0002-S000001>{separator}</D0002-S000001>"
        _, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert result.passed

    def test_validation_allows_unchanged_uri_with_inline_markers(self):
        source = "<I000>https://docs.example.invalid/reference/42</I000>"
        chunk = make_chunk().model_copy(deep=True)
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0001-S000001",
                segment_id="D0001-S000001",
                part_number=1,
                source_text=source,
            )
        ]
        output = f"<D0001-S000001>{source}</D0001-S000001>"
        translations, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert result.passed
        assert translations["D0001-S000001"] == source

    def test_validation_still_rejects_unchanged_inline_link_text(self):
        source = "<I000>Read the documentation</I000>"
        chunk = make_chunk().model_copy(deep=True)
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0001-S000001",
                segment_id="D0001-S000001",
                part_number=1,
                source_text=source,
            )
        ]
        output = f"<D0001-S000001>{source}</D0001-S000001>"
        _, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert not result.passed
        assert {issue.code for issue in result.issues} == {"target_language_missing", "untranslated_exact"}

    def test_validation_allows_exact_qualified_technical_identifier(self):
        source = "ExampleToolkit.Components.ReferenceNode"
        chunk = make_chunk().model_copy(deep=True)
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0001-S000001",
                segment_id="D0001-S000001",
                part_number=1,
                source_text=source,
            )
        ]
        output = f"<D0001-S000001>{source}</D0001-S000001>"
        _, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert result.passed

    def test_validation_still_rejects_exact_ordinary_name(self):
        source = "Harbor Banner Emblem"
        chunk = make_chunk().model_copy(deep=True)
        chunk.pieces = [
            TranslationChunkPiece(
                reference_id="D0001-S000001",
                segment_id="D0001-S000001",
                part_number=1,
                source_text=source,
            )
        ]
        output = f"<D0001-S000001>{source}</D0001-S000001>"
        _, result = validate_translation_output(
            output, chunk, TranslationDirection.EN_TO_ZH
        )
        assert not result.passed

    def test_assemble_and_render_translation(self):
        document = make_document(["one two", "three"])
        parts = {
            "D0002-S000001-P001": "第一",
            "D0002-S000001-P002": "第二",
            "D0002-S000002": "第三",
        }
        segments = assemble_translated_segments(document, parts, TranslationDirection.EN_TO_ZH)
        assert segments[0].translated_text == "第一第二"
        translated = TranslatedDocument(
            order=2,
            manifest_id="chapter",
            archive_path="OEBPS/chapter.xhtml",
            direction=TranslationDirection.EN_TO_ZH,
            style="literary",
            segments=segments,
        )
        rendered = render_translated_document(translated)
        assert "<D0002-S000001>第一第二</D0002-S000001>" in rendered
        assert rendered.endswith("\n")

    def test_english_parts_join_with_spaces(self):
        document = make_document(["你好"])
        parts = {"D0002-S000001-P001": "Hello", "D0002-S000001-P002": "world"}
        segments = assemble_translated_segments(document, parts, TranslationDirection.ZH_TO_EN)
        assert segments[0].translated_text == "Hello world"

    def test_assembly_rejects_missing_or_noncontiguous_parts(self):
        document = make_document(["one"])
        with pytest.raises(TranslationOutputError, match="missing"):
            assemble_translated_segments(document, {}, TranslationDirection.EN_TO_ZH)
        with pytest.raises(TranslationOutputError, match="non-contiguous"):
            assemble_translated_segments(
                document,
                {"D0002-S000001-P002": "第二"},
                TranslationDirection.EN_TO_ZH,
            )

    apply_inline_marker_placements,
    build_inline_marker_placement_prompt,
