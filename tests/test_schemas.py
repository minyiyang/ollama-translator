import pytest
from pydantic import ValidationError

from book_agent.schemas import (
    GlossaryCategory,
    GlossaryEntry,
    GlossaryResult,
    build_glossary_extraction_schema,
    deduplicate_entries,
    normalize_term,
    render_legacy_glossary,
)


def entry(
    english: str = "Cordelia",
    chinese: str = "科迪莉娅",
    category: GlossaryCategory = GlossaryCategory.PERSON,
    note: str = "主要人物",
) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        note=note,
        category=category,
    )


class GlossarySchemaTests:
    def test_valid_entry_and_result(self) -> None:
        result = GlossaryResult(entries=[entry()])
        assert result.entries[0].category == GlossaryCategory.PERSON

    def test_extraction_result_bounds_entries_and_evidence(self) -> None:
        extraction_schema = build_glossary_extraction_schema(80, 3)
        payload = entry().model_dump()
        payload["evidence"] = ["S1", "S2", "S3"]
        result = extraction_schema.model_validate({"entries": [payload]})
        assert len(result.entries[0].evidence) == 3

        payload["evidence"] = ["S1", "S2", "S3", "S4"]
        with pytest.raises(ValidationError):
            extraction_schema.model_validate({"entries": [payload]})

        payload["evidence"] = ["S1"]
        with pytest.raises(ValidationError):
            extraction_schema.model_validate({"entries": [payload] * 81})

    def test_extraction_schema_restricts_evidence_to_chunk_ids(self) -> None:
        extraction_schema = build_glossary_extraction_schema(
            80,
            3,
            ["D0001-S000001", "D0001-S000002"],
        )
        payload = entry().model_dump()
        payload["evidence"] = ["D0001-S000001"]
        extraction_schema.model_validate({"entries": [payload]})
        payload["evidence"] = ["D0001-S000003"]
        with pytest.raises(ValidationError):
            extraction_schema.model_validate({"entries": [payload]})

    def test_extraction_schema_accepts_provisional_untranslated_candidate(self) -> None:
        extraction_schema = build_glossary_extraction_schema(80, 3, ["D0001-S000001"])
        payload = entry().model_dump()
        payload.update(
            english="Riverstone",
            chinese="Riverstone",
            evidence=["D0001-S000001"],
        )
        result = extraction_schema.model_validate({"entries": [payload]})
        assert result.entries[0].chinese == "Riverstone"
        with pytest.raises(ValidationError, match="CJK"):
            GlossaryEntry.model_validate(result.entries[0].model_dump())

    def test_entry_rejects_english_without_latin_letter(self) -> None:
        with pytest.raises(ValidationError, match="Latin"):
            entry(english="科迪莉娅")

    def test_entry_rejects_chinese_without_cjk(self) -> None:
        with pytest.raises(ValidationError, match="CJK"):
            entry(chinese="Cordelia")

    def test_entry_rejects_parentheses_colons_and_linebreaks_in_terms(self, subtests) -> None:
        for invalid in ("Cordelia (witch)", "Cordelia:witch", "Cordelia\nwitch"):
            with subtests.test(invalid=invalid):
                with pytest.raises(ValidationError):
                    entry(english=invalid)

    def test_entry_rejects_multiline_note(self) -> None:
        with pytest.raises(ValidationError, match="line breaks"):
            entry(note="第一行\n第二行")

    def test_confidence_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            GlossaryEntry(
                english="Cordelia",
                chinese="科迪莉娅",
                category=GlossaryCategory.PERSON,
                confidence=1.1,
            )

    def test_normalize_term_folds_case_and_whitespace(self) -> None:
        assert normalize_term("  Blade   Street ") == "blade street"

    def test_deduplicate_removes_exact_pair_case_insensitively(self) -> None:
        entries = [entry(), entry(english="cordelia")]
        assert deduplicate_entries(entries) == [entries[0]]

    def test_deduplicate_retains_alternative_translations(self) -> None:
        entries = [entry(), entry(chinese="柯迪莉娅")]
        assert deduplicate_entries(entries) == entries

    def test_renderer_categorizes_sorts_and_ends_with_newline(self) -> None:
        output = render_legacy_glossary(
            [
                entry("Zephyr", "泽菲尔"),
                entry("Blade Street", "刀街", GlossaryCategory.PLACE, "街道"),
                entry("Aster", "阿斯特"),
            ]
        )
        assert output == ("--人名--\n"
            "Aster:阿斯特:主要人物\n"
            "Zephyr:泽菲尔:主要人物\n\n"
            "--地名--\n"
            "Blade Street:刀街:街道\n")

    def test_renderer_returns_empty_string_for_no_entries(self) -> None:
        assert render_legacy_glossary([]) == ""

