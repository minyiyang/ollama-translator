"""Glossaries for any language pair (docs/GENERIC_LANGUAGES.md, phase 3).

Entries have a source and a target side and a glossary records its pair. An
en/zh book's glossary is en-zh either way, keyed by English, and the model
still sees `english`/`chinese` for it; any other pair says `source`/`target`.
Files written before phase 3 (english/chinese, no pair) still load.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from book_agent.glossary import (
    GlossaryFormatError,
    GlossarySource,
    GlossarySourceKind,
    build_glossary_approval_cases,
    build_glossary_resolution_cases,
    orient_glossary,
)
from book_agent.glossary_prompts import build_extraction_prompt, build_resolution_prompt
from book_agent.glossary import GlossaryChunk, GlossaryChunkPiece
from book_agent.languages import LanguagePair, TranslationDirection, glossary_sides, source_aliases
from book_agent.preprocessing import ReplacementOccurrence
from book_agent.schemas import (
    GlossaryApprovalRecord,
    GlossaryEntry,
    GlossaryResult,
    build_glossary_approval_schema,
    build_glossary_extraction_schema,
    build_glossary_resolution_schema,
)
from book_agent.series import WorkbenchTerm
from book_agent.series_glossary import (
    _build_series_conflict_choice_schema,
    _build_series_conflict_prompt,
    build_series_glossary,
)
from book_agent.style_sheet import StyleCharacter, StyleSheet, build_style_candidate_schema, check_style_choices

EN_JA = LanguagePair("en>ja")
EN_ZH = TranslationDirection.EN_TO_ZH
ZH_EN = TranslationDirection.ZH_TO_EN


def _ja(source="Aster", target="アスター", **extra):
    return GlossaryEntry.for_pair({"source": source, "target": target, "category": "人名", **extra}, EN_JA)


# -- stored files ------------------------------------------------------------------------


def test_a_glossary_written_before_phase_3_loads_as_en_zh_and_is_rewritten_with_the_new_names():
    old = '{"entries": [{"english": "Aster", "chinese": "阿斯特", "category": "人名", "aliases": ["Ast"]}]}'
    glossary = GlossaryResult.model_validate_json(old)
    assert glossary.pair == EN_ZH
    assert (glossary.entries[0].source, glossary.entries[0].target, glossary.entries[0].aliases) == ("Aster", "阿斯特", ["Ast"])
    written = json.loads(glossary.model_dump_json())
    assert written["pair"] == "en-zh" and set(written["entries"][0]) >= {"source", "target"}
    assert "english" not in written["entries"][0]


def test_any_pair_round_trips_with_its_pair():
    glossary = GlossaryResult(pair=EN_JA, entries=[_ja()])
    again = GlossaryResult.model_validate_json(glossary.model_dump_json())
    assert again == glossary and again.pair == EN_JA


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"source": "Aster", "target": "Aster2"}, "Japanese character"),  # the target must be Japanese
        ({"source": "アスター", "target": "アスター"}, "Latin letter"),  # the source must be English
        ({"english": "Aster", "chinese": "阿斯特"}, "en-zh glossary"),  # old names belong to en-zh
    ],
)
def test_terms_are_checked_against_the_pairs_scripts(data, message):
    with pytest.raises(ValidationError, match=message):
        GlossaryResult.model_validate({"pair": "en>ja", "entries": [{**data, "category": "人名"}]})


def test_a_preserved_code_may_stay_unchanged_in_any_target():
    assert _ja("ZX-81", "ZX-81").target == "ZX-81"


def test_old_report_and_document_fields_still_load():
    record = GlossaryApprovalRecord.model_validate(
        {"term_id": "A00001", "english": "Aster", "chinese": "阿斯特", "result": "approved", "mode": "llm"}
    )
    assert (record.source, record.target) == ("Aster", "阿斯特")
    occurrence = ReplacementOccurrence.model_validate(
        {"source": "Aster", "target": "阿斯特", "canonical_english": "Aster", "count": 2}
    )
    assert occurrence.canonical_term == "Aster"
    term = WorkbenchTerm.model_validate({
        "term_id": "T00001", "english": "Aster", "chinese": "阿斯特", "category": "人名",
        "origin": "consensus", "decision": "keep", "decided_by": "rule",
        "suggestion": {"kind": "resolve", "chinese": "阿斯特", "rationale": "r", "model": "m"},
    })
    assert (term.source, term.target, term.suggestion.target) == ("Aster", "阿斯特", "阿斯特")


# -- orientation ---------------------------------------------------------------------------


def test_a_zh_en_job_uses_its_en_zh_glossary_the_other_way():
    entry = GlossaryEntry(source="Aster", target="阿斯特", category="人名", aliases=["Ast"])
    assert glossary_sides(entry, EN_ZH) == ("Aster", "阿斯特")
    assert glossary_sides(entry, ZH_EN) == ("阿斯特", "Aster")
    assert source_aliases(entry, EN_ZH) == ["Ast"] and source_aliases(entry, ZH_EN) == []


def test_a_glossary_of_the_reverse_pair_is_swapped_explicitly_and_an_unrelated_one_refused():
    ja_en = GlossaryResult(
        pair=LanguagePair("ja>en"),
        entries=[GlossaryEntry.for_pair({"source": "アスター", "target": "Aster", "category": "人名", "aliases": ["アスタ"]}, LanguagePair("ja>en"))],
    )
    swapped = orient_glossary(ja_en, EN_JA)
    assert swapped.pair == EN_JA
    # Aliases were spellings of the old source term, so a swap drops them.
    assert [(e.source, e.target, e.aliases) for e in swapped.entries] == [("Aster", "アスター", [])]
    assert orient_glossary(swapped, EN_JA) is swapped
    with pytest.raises(GlossaryFormatError, match="en>ja"):
        orient_glossary(GlossaryResult(entries=[]), EN_JA)


# -- what the model sees ----------------------------------------------------------------------


def test_the_model_sees_english_and_chinese_for_an_en_zh_glossary_and_source_target_otherwise():
    zh = GlossaryEntry(source="Aster", target="阿斯特", category="人名")
    assert set(build_glossary_resolution_cases([zh])[0]) == {"term_id", "english", "candidates"}
    assert "chinese" in build_glossary_resolution_cases([zh])[0]["candidates"][0]
    assert set(build_glossary_resolution_cases([_ja()], EN_JA)[0]) == {"term_id", "source", "candidates"}
    assert set(build_glossary_approval_cases([_ja()], EN_JA)[0]["entry"]) >= {"source", "target"}

    def fields(schema: type) -> set[str]:
        return {name for definition in schema.model_json_schema()["$defs"].values() for name in definition.get("properties", {})}

    assert {"english", "chinese"} <= fields(build_glossary_extraction_schema(10, 2))
    assert {"source", "target"} <= fields(build_glossary_extraction_schema(10, 2, pair=EN_JA))
    assert "chinese" in fields(build_glossary_resolution_schema(["T00001"], 1))
    assert "target" in fields(build_glossary_resolution_schema(["T00001"], 1, pair=EN_JA))
    assert "target" in fields(build_glossary_approval_schema(["A00001"], EN_JA))


def test_prompts_for_another_pair_name_its_languages_and_never_chinese():
    chunk = GlossaryChunk(
        chunk_id="glossary-00001",
        pieces=[GlossaryChunkPiece(reference_id="D0000-S000001", document_path="t.xhtml", text="Aster opened the door.")],
        estimated_tokens=10,
    )
    extraction = build_extraction_prompt(chunk, EN_JA)
    resolution = build_resolution_prompt([_ja()], EN_JA)
    for prompt in (extraction, resolution):
        assert "Japanese" in prompt and "Chinese" not in prompt and "english" not in prompt
    assert "`source`" in extraction and '"source":"Aster"' in resolution


def test_series_conflicts_keep_the_en_zh_wording_and_use_source_target_otherwise():
    def build(pair, entries_a, entries_b):
        sources = [
            GlossarySource(name="a", kind=GlossarySourceKind.SERIES, entries=tuple(entries_a)),
            GlossarySource(name="b", kind=GlossarySourceKind.SERIES, entries=tuple(entries_b)),
        ]
        return build_series_glossary(sources, pair=pair)

    zh = build(
        EN_ZH,
        [GlossaryEntry(source="Aster", target="阿斯特", category="人名")],
        [GlossaryEntry(source="Aster", target="阿斯塔", category="人名")],
    )
    ja = build(EN_JA, [_ja()], [_ja(target="アスタ")])
    assert zh.glossary.pair == EN_ZH and ja.glossary.pair == EN_JA
    ids = {"aster": "C00001"}
    zh_prompt = _build_series_conflict_prompt(zh.report.conflicts, {}, ids, EN_ZH)
    ja_prompt = _build_series_conflict_prompt(ja.report.conflicts, {}, ids, EN_JA)
    assert '"english":"Aster"' in zh_prompt.replace(" ", "") and "selected_chinese" in zh_prompt
    assert '"source":"Aster"' in ja_prompt.replace(" ", "") and "selected_target" in ja_prompt
    assert "Chinese" not in ja_prompt
    assert "selected_target" in json.dumps(_build_series_conflict_choice_schema(["C00001"], EN_JA).model_json_schema())


# -- style sheet ---------------------------------------------------------------------------------


def test_style_choices_follow_the_target_language():
    sheet = StyleSheet(characters=[StyleCharacter(name="Mouse", pronoun="he")])
    check_style_choices(sheet, ZH_EN)  # the tuned pairs offer both languages' pronouns
    with pytest.raises(ValueError, match="none for this language"):
        check_style_choices(sheet, LanguagePair("en>de"))
    schema = json.dumps(
        build_style_candidate_schema(["D0000-S000001"], max_characters=2, max_expressions=2, direction=LanguagePair("en>de")).model_json_schema()
    )
    assert '"他"' not in schema and '"he"' not in schema
