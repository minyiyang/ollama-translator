import pytest

from book_agent.languages import TranslationDirection
from book_agent.preprocessing import (
    GlossaryReplacementConflict,
    PreprocessedDocument,
    annotate_segment,
    build_replacement_index,
    apply_replacement_index,
    preprocess_segment,
    render_preprocessed_document,
    select_relevant_glossary_entries,
)
from book_agent.schemas import GlossaryCategory, GlossaryEntry


def entry(
    english: str,
    chinese: str,
    *,
    aliases: list[str] | None = None,
) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        category=GlossaryCategory.OTHER,
        aliases=aliases or [],
    )


class ReplacementIndexTests:
    def test_english_to_chinese_uses_longest_match_case_and_aliases(self) -> None:
        entries = [
            entry("Blade", "刀锋"),
            entry("Blade Street", "刀街"),
            entry("Aster", "阿斯特", aliases=["Captain Aster"]),
        ]
        index = build_replacement_index(entries, TranslationDirection.EN_TO_ZH)
        processed, occurrences = apply_replacement_index(
            "BLADE STREET met Captain Aster. Blade shone.", index
        )
        assert processed == "刀街 met 阿斯特. 刀锋 shone."
        assert sum(item.count for item in occurrences) == 3

    def test_english_boundaries_avoid_substrings_but_allow_possessive(self) -> None:
        index = build_replacement_index(
            [entry("Aster", "阿斯特")], TranslationDirection.EN_TO_ZH
        )
        processed, _ = apply_replacement_index(
            "Plaster is not Aster or Aster's idea.", index
        )
        assert processed == "Plaster is not 阿斯特 or 阿斯特's idea."

    def test_chinese_to_english_uses_longest_unicode_match(self) -> None:
        index = build_replacement_index(
            [entry("Blade", "刀"), entry("Blade Street", "刀街")],
            TranslationDirection.ZH_TO_EN,
        )
        processed, occurrences = apply_replacement_index("她走过刀街，拔出了刀。", index)
        assert processed == "她走过Blade Street，拔出了Blade。"
        assert sum(item.count for item in occurrences) == 2

    def test_protected_markers_are_not_modified(self) -> None:
        index = build_replacement_index(
            [entry("Aster", "阿斯特")], TranslationDirection.EN_TO_ZH
        )
        processed, _ = apply_replacement_index(
            '<Aster id="Aster"><D0001-S000001>Aster</D0001-S000001></Aster>',
            index,
        )
        assert processed == '<Aster id="Aster"><D0001-S000001>阿斯特</D0001-S000001></Aster>'

    def test_ambiguous_alternatives_are_skipped_or_rejected(self) -> None:
        entries = [entry("Aster", "阿斯特"), entry("Aster", "阿斯塔")]
        index = build_replacement_index(
            entries, TranslationDirection.EN_TO_ZH, conflict_policy="skip"
        )
        assert index.rules == ()
        assert set(index.conflicts["Aster"]) == {"阿斯特", "阿斯塔"}
        assert apply_replacement_index("Aster", index)[0] == "Aster"
        with pytest.raises(GlossaryReplacementConflict):
            build_replacement_index(
                entries, TranslationDirection.EN_TO_ZH, conflict_policy="error"
            )
        with pytest.raises(ValueError, match="conflict_policy"):
            build_replacement_index(
                entries, TranslationDirection.EN_TO_ZH, conflict_policy="guess"
            )

    def test_duplicate_alias_with_same_target_is_not_a_conflict(self) -> None:
        index = build_replacement_index(
            [entry("Aster", "阿斯特", aliases=["Aster"])],
            TranslationDirection.EN_TO_ZH,
        )
        assert len(index.rules) == 1
        assert index.conflicts == {}


class RelevanceAndDocumentTests:
    def test_annotate_segment_preserves_source_without_occurrences(self) -> None:
        segment = annotate_segment("D0001-S000001", "Order the Order to leave.")
        assert segment.original_text == "Order the Order to leave."
        assert segment.processed_text == segment.original_text
        assert segment.occurrences == []

    def test_relevance_selection_is_direction_aware_and_alias_aware(self) -> None:
        entries = [
            entry("Aster", "阿斯特", aliases=["Captain Aster"]),
            entry("Heron", "石鹭"),
        ]
        en = select_relevant_glossary_entries(
            "Captain Aster arrived.", entries, TranslationDirection.EN_TO_ZH
        )
        zh = select_relevant_glossary_entries(
            "石鹭到了。", entries, TranslationDirection.ZH_TO_EN
        )
        assert [item.english for item in en] == ["Aster"]
        assert [item.english for item in zh] == ["Heron"]

    def test_relevance_selection_accepts_typographic_apostrophe_and_hyphen_variants(self) -> None:
        entries = [
            entry("The Warden's Kin", "监长之族"),
            entry("Qel-madness", "凯尔疯症"),
        ]
        selected = select_relevant_glossary_entries(
            "The Warden’s Kin survived the Qel‑madness.",
            entries,
            TranslationDirection.EN_TO_ZH,
        )
        assert [item.english for item in selected] == ["Qel-madness", "The Warden's Kin"]

    def test_relevance_selection_respects_case_bearing_terms_obfuscated(self) -> None:
        entries = [
            entry("VRX", "\u7ef4\u5c14\u514b\u65af"),
            entry("Home", "\u5bb6\u56ed"),
            entry("qel drive", "\u51ef\u5c14\u9a71\u52a8"),
        ]
        selected = select_relevant_glossary_entries(
            "His vrx arm returned home beside the Qel drive.",
            entries,
            TranslationDirection.EN_TO_ZH,
        )
        assert [item.english for item in selected] == ["qel drive"]

    def test_relevance_prefers_longest_incompatible_nested_terms_obfuscated(self) -> None:
        entries = [
            entry("Qel", "\u51ef\u5c14"),
            entry("Qel Core", "\u51ef\u5c14\u6838\u5fc3"),
            entry("Nul", "\u52aa\u5c14"),
            entry("Nul Core", "\u6838\u5fc3\u4f53"),
        ]
        selected = select_relevant_glossary_entries(
            "Qel Core met Nul Core.", entries, TranslationDirection.EN_TO_ZH
        )
        assert [item.english for item in selected] == ["Nul Core", "Qel Core"]

    def test_relevance_suppresses_generic_suffix_inside_compound(self) -> None:
        entries = [
            entry("clade", "\u79cd\u65cf"),
            entry("Spider-clade", "\u8718\u86db\u65cf"),
        ]
        selected = select_relevant_glossary_entries(
            "The Spider-clade arrived.", entries, TranslationDirection.EN_TO_ZH
        )
        assert [item.english for item in selected] == ["Spider-clade"]

    def test_relevance_keeps_independent_shorter_occurrence_obfuscated(self) -> None:
        entries = [
            entry("Qel", "\u51ef\u5c14"),
            entry("Qel Core", "\u51ef\u5c14\u6838\u5fc3"),
        ]
        selected = select_relevant_glossary_entries(
            "Qel Core met Qel.", entries, TranslationDirection.EN_TO_ZH
        )
        assert [item.english for item in selected] == ["Qel", "Qel Core"]

    def test_preprocess_segment_and_render_preserve_id(self) -> None:
        index = build_replacement_index(
            [entry("Aster", "阿斯特")], TranslationDirection.EN_TO_ZH
        )
        segment = preprocess_segment("D0001-S000001", "Aster arrived.", index)
        document = PreprocessedDocument(
            order=1,
            manifest_id="chapter",
            archive_path="OEBPS/chapter.xhtml",
            source_sha256="a" * 64,
            segments=[segment],
        )
        assert segment.original_text == "Aster arrived."
        assert segment.processed_text == "阿斯特 arrived."
        assert render_preprocessed_document(document) == "<D0001-S000001>阿斯特 arrived.</D0001-S000001>\n"

