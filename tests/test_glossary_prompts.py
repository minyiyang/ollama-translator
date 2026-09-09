import json

from book_agent.glossary import GlossaryChunk, GlossaryChunkPiece
from book_agent.glossary_prompts import (
    build_extraction_prompt,
    build_resolution_prompt,
    build_review_prompt,
)
from book_agent.languages import TranslationDirection
from book_agent.schemas import (
    GlossaryCategory,
    GlossaryEntry,
    build_glossary_approval_schema,
    build_glossary_resolution_schema,
)


class GlossaryPromptTests:
    def setup_method(self) -> None:
        self.chunk = GlossaryChunk(
            chunk_id="one",
            pieces=[GlossaryChunkPiece(reference_id="S1", document_path="c", text="Aster")],
            estimated_tokens=2,
        )

    def test_extraction_prompt_is_direction_aware_and_requires_evidence(self) -> None:
        en_zh = build_extraction_prompt(self.chunk, TranslationDirection.EN_TO_ZH)
        zh_en = build_extraction_prompt(self.chunk, TranslationDirection.ZH_TO_EN)
        assert "English book passages" in en_zh
        assert "Simplified Chinese book passages" in zh_en
        assert "evidence" in en_zh
        assert "one to 3 exact" in en_zh
        assert "at most 80 entries" in en_zh
        assert "all leading zeroes" in en_zh
        assert "<S1>Aster</S1>" in en_zh

    def test_resolution_prompt_contains_candidates_and_no_web_claim(self) -> None:
        entry = GlossaryEntry(
            english="Qelwright", chinese="奎尔赖特", category=GlossaryCategory.PERSON
        )
        prompt = build_resolution_prompt([entry], TranslationDirection.EN_TO_ZH)
        assert '"term_id":"T00001"' in prompt
        assert '"english":"Qelwright"' in prompt
        assert "do not claim web verification" in prompt
        assert "Simplified Chinese" in prompt
        assert "Format-only example" in prompt
        assert "output has no English field" in prompt

    def test_obfuscated_resolution_schema_accepts_only_ids_and_omits_english(self) -> None:
        schema = build_glossary_resolution_schema(
            ["T00001", "T00002"], max_decisions=2
        )
        serialized = json.dumps(schema.model_json_schema())

        assert '"english"' not in serialized
        assert "T00001" in serialized
        assert "T00002" in serialized

    def test_obfuscated_approval_schema_is_exact_size_and_omits_english(self) -> None:
        schema = build_glossary_approval_schema(["A00004", "A00009"])
        serialized = json.dumps(schema.model_json_schema())

        assert '"english"' not in serialized
        assert "A00004" in serialized
        assert "A00009" in serialized
        assert '"minItems": 2' in serialized
        assert '"maxItems": 2' in serialized

    def test_obfuscated_review_prompt_is_delta_only_and_id_safe(self) -> None:
        entry = GlossaryEntry(
            english="Vraxwright",
            chinese="弗拉克斯赖特",
            note="\u4eba\u7269",
            category=GlossaryCategory.PERSON,
        )
        prompt = build_review_prompt([entry], TranslationDirection.EN_TO_ZH)
        assert "exactly one decision" in prompt
        assert "Never return, copy, correct, or rewrite English" in prompt
        assert '"term_id":"A00001"' in prompt
        assert '"english":"Vraxwright"' in prompt
        assert "Format-only example" in prompt
        assert "deltas only and has no English field" in prompt

