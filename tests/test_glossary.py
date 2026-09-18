import tempfile
from pathlib import Path

import pytest

from book_agent.epub import ChapterDocument, TextSegment
from book_agent.glossary import (
    GlossaryChunk,
    GlossaryChunkPiece,
    GlossaryFormatError,
    GlossarySource,
    GlossarySourceKind,
    analyze_glossary_quality,
    build_glossary_approval_batches,
    build_glossary_approval_cases,
    build_glossary_chunks,
    build_glossary_resolution_batches,
    canonicalize_candidate_evidence,
    estimate_tokens,
    find_unglossed_proper_nouns,
    harmonize_glossary,
    load_glossary_file,
    merge_candidate_entries,
    merge_prioritized_sources,
    parse_legacy_glossary,
    restore_glossary_evidence,
    screen_glossary_approval_cases,
    screen_glossary_candidates,
    screen_glossary_documents,
    sort_glossary_entries,
    split_text_to_budget,
    validate_candidate_evidence,
    validate_resolution_scope,
)
from book_agent.schemas import (
    GlossaryCategory,
    GlossaryEntry,
    GlossaryResult,
    render_legacy_glossary,
)


def glossary_entry(
    english: str,
    chinese: str,
    *,
    category: GlossaryCategory = GlossaryCategory.PERSON,
    note: str = "",
    evidence: list[str] | None = None,
    aliases: list[str] | None = None,
    confidence: float = 1.0,
) -> GlossaryEntry:
    return GlossaryEntry(
        english=english,
        chinese=chinese,
        category=category,
        note=note,
        evidence=evidence or [],
        aliases=aliases or [],
        confidence=confidence,
    )


def document(*texts: str) -> ChapterDocument:
    return ChapterDocument(
        order=1,
        manifest_id="chapter",
        archive_path="OEBPS/chapter.xhtml",
        media_type="application/xhtml+xml",
        linear=True,
        source_sha256="a" * 64,
        segments=[
            TextSegment(
                segment_id=f"D0001-S{index:06d}",
                element_path=f"/html/body/p[{index}]",
                tag="p",
                text=text,
            )
            for index, text in enumerate(texts, start=1)
        ],
    )


class GlossaryChunkTests:
    def test_obfuscated_document_screening_ignores_generic_filenames(self) -> None:
        story = document("Qelm crossed the Vrax causeway.").model_copy(
            update={"order": 7, "title": "One", "archive_path": "OPS/split_007.html"}
        )
        in_book_glossary = document("Glossary Qelm - a Nyr craftmaster").model_copy(
            update={
                "order": 56,
                "title": "Glossary",
                "archive_path": "OPS/split_056.html",
            }
        )
        praise = document("Praise for the Qelm Cycle Review quotation").model_copy(
            update={
                "order": 57,
                "title": "Qelm Cycle",
                "archive_path": "OPS/split_057.html",
            }
        )
        copyright_page = document(
            "First published 2091 by Nyr Press. ISBN 000-0-00-000000-0"
        ).model_copy(
            update={
                "order": 61,
                "title": "Qelm Cycle",
                "archive_path": "OPS/split_061.html",
            }
        )

        included, report = screen_glossary_documents(
            [story, praise, in_book_glossary, copyright_page]
        )

        assert [item.order for item in included] == [7, 56]
        assert report.excluded_document_count == 2
        assert [item.role for item in report.decisions] == ["content", "content", "publication", "publication"]

    def test_document_screening_excludes_marketing_previews_and_author_matter(self) -> None:
        story = document("Qelm crossed the Vrax causeway.").model_copy(
            update={"order": 7, "title": "One", "archive_path": "OPS/chapter001.xhtml"}
        )
        discovery = document(
            "Discover Your Next Great Read. Get sneak peeks and recommendations."
        ).model_copy(
            update={
                "order": 43,
                "title": "Discover More",
                "archive_path": "OPS/discover-page.xhtml",
            }
        )
        author = document(
            "Nyr Author was born in Qelm and has since worked as a legal executive."
        ).model_copy(
            update={
                "order": 45,
                "title": "meet the author",
                "archive_path": "OPS/personblurb.xhtml",
            }
        )
        preview = document(
            "If you enjoyed Qelm, look out for Vrax. This is an unrelated novel extract."
        ).model_copy(
            update={
                "order": 46,
                "title": 'A Preview of "Vrax"',
                "archive_path": "OPS/appendix003.xhtml",
            }
        )

        included, report = screen_glossary_documents(
            [story, discovery, author, preview]
        )

        assert [item.order for item in included] == [7]
        assert report.excluded_document_count == 3
        assert [item.role for item in report.decisions] == ["content", "publication", "publication", "publication"]

    def test_obfuscated_candidate_screening_drops_publication_only_evidence(self) -> None:
        pieces = [
            GlossaryChunkPiece(
                reference_id="D0099-S000001",
                document_path="OPS/qel-titlepage.xhtml",
                text="Qelm Vrax",
            ),
            GlossaryChunkPiece(
                reference_id="D0002-S000001",
                document_path="OPS/qel-chapter.xhtml",
                text="Nul Zor",
            ),
        ]
        result = GlossaryResult(
            entries=[
                glossary_entry(
                    "Qelm", chr(0x5947) + chr(0x5C14), evidence=["D0099-S000001"]
                ),
                glossary_entry(
                    "Nul", chr(0x7EBD) + chr(0x5C14), evidence=["D0002-S000001"]
                ),
            ]
        )

        screened, report = screen_glossary_candidates(result, pieces)

        assert [entry.english for entry in screened.entries] == ["Nul"]
        assert report.rejected_count == 1
        assert report.rejected_reason_counts == {"publication_document_only": 1}

    def test_obfuscated_quality_report_warns_without_deleting_ambiguous_term(self) -> None:
        result = GlossaryResult(
            entries=[
                glossary_entry(
                    "qelm",
                    chr(0x5947) + chr(0x5C14),
                    category=GlossaryCategory.CONCEPT,
                    evidence=["D0002-S000001"],
                )
            ]
        )

        quality = analyze_glossary_quality(result)

        assert quality.result == "warning"
        assert quality.suspicious_generic_terms == ["qelm"]
        assert result.entries[0].english == "qelm"

    def test_quality_report_warns_for_oversized_draft(self) -> None:
        result = GlossaryResult(
            entries=[
                glossary_entry(
                    f"Qelm {index}",
                    f"奇尔{index}",
                    evidence=["D0002-S000001"],
                )
                for index in range(401)
            ]
        )

        quality = analyze_glossary_quality(result)

        assert "oversized glossary draft (401 entries) requires pruning" in quality.warnings

    def test_series_quality_report_does_not_apply_volume_size_limit(self) -> None:
        result = GlossaryResult(
            entries=[
                glossary_entry(
                    f"Qelm {index}",
                    f"\u5947\u5c14{index}",
                    evidence=["series:book-01"],
                )
                for index in range(401)
            ]
        )

        quality = analyze_glossary_quality(result, scope="series")

        assert quality.scope == "series"
        assert not any("oversized" in warning for warning in quality.warnings)

    def test_resolution_batches_keep_obfuscated_source_alternatives_together(self) -> None:
        entries = [
            glossary_entry("Qelm", "奇尔"),
            glossary_entry("Qelm", "卡姆"),
            glossary_entry("Vrax", "弗拉克斯"),
        ]
        batches = build_glossary_resolution_batches(entries, max_tokens=1)
        qelm_batches = [
            batch
            for batch in batches
            if any(item.english == "Qelm" for item in batch)
        ]
        assert len(qelm_batches) == 1
        assert {(item.english, item.chinese) for item in qelm_batches[0]} == {("Qelm", "卡姆"), ("Qelm", "奇尔")}
        assert len(batches) > 1

    def test_glossary_allows_preserved_codes_but_not_untranslated_names(self) -> None:
        code = glossary_entry(
            "ZX-17",
            "ZX-17",
            category=GlossaryCategory.TECHNOLOGY,
        )
        acronym = glossary_entry(
            "RND",
            "RND",
            category=GlossaryCategory.ORGANIZATION,
        )
        assert code.chinese == "ZX-17"
        assert acronym.chinese == "RND"
        with pytest.raises(ValueError, match="CJK character"):
            glossary_entry("Riverstone", "Riverstone")
        with pytest.raises(ValueError, match="exact same"):
            glossary_entry(
                "ZX-17",
                "ZX-18",
                category=GlossaryCategory.TECHNOLOGY,
            )

    def test_estimate_tokens_handles_empty_english_and_chinese(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("中文") == 2
        assert estimate_tokens("abcd") == 1

    def test_split_text_to_budget_preserves_content_and_limits_parts(self) -> None:
        parts = split_text_to_budget("one two three four five six", 3)
        assert " ".join(parts) == "one two three four five six"
        assert all(estimate_tokens(part) <= 3 for part in parts)
        assert split_text_to_budget("   ", 3) == []
        with pytest.raises(ValueError, match="positive"):
            split_text_to_budget("text", 0)

    def test_split_text_handles_chinese_without_spaces(self) -> None:
        text = "阿斯特掉进兔子洞开始冒险"
        parts = split_text_to_budget(text, 4)
        assert "".join(parts) == text
        assert all(estimate_tokens(part) <= 4 for part in parts)

    def test_build_chunks_covers_every_segment_and_splits_oversized_segment(self) -> None:
        chunks = build_glossary_chunks(
            [document("Aster met Heron", "阿斯特掉进兔子洞开始冒险")],
            4,
        )
        references = [piece.reference_id for chunk in chunks for piece in chunk.pieces]
        assert "D0001-S000001" in references
        assert any(reference.startswith("D0001-S000002-P") for reference in references)
        assert all(chunk.estimated_tokens <= 4 for chunk in chunks)
        assert [chunk.chunk_id for chunk in chunks] == ([
            f"glossary-{index:05d}" for index in range(1, len(chunks) + 1)
        ])
        with pytest.raises(ValueError, match="positive"):
            build_glossary_chunks([], 0)
        with pytest.raises(ValueError, match="max_documents"):
            build_glossary_chunks([], 10, max_documents=0)

    def test_build_chunks_groups_five_complete_documents(self) -> None:
        documents = []
        for index in range(12):
            source = document(f"Chapter {index + 1} text")
            documents.append(
                source.model_copy(
                    update={
                        "order": index,
                        "manifest_id": f"chapter-{index + 1}",
                        "archive_path": f"OEBPS/chapter-{index + 1}.xhtml",
                        "segments": [
                            source.segments[0].model_copy(
                                update={"segment_id": f"D{index + 1:04d}-S000001"}
                            )
                        ],
                    }
                )
            )
        chunks = build_glossary_chunks(documents, 30_000, max_documents=5)
        document_counts = [len({piece.document_path for piece in chunk.pieces}) for chunk in chunks]
        assert document_counts == [5, 5, 2]
        assert all(chunk.estimated_tokens <= 30_000 for chunk in chunks)

    def test_chunk_render_uses_reference_markers(self) -> None:
        chunk = GlossaryChunk(
            chunk_id="glossary-00001",
            pieces=[
                GlossaryChunkPiece(
                    reference_id="D1-S1", document_path="chapter", text="Aster"
                )
            ],
            estimated_tokens=2,
        )
        assert chunk.render() == "<D1-S1>Aster</D1-S1>"

    def test_candidate_evidence_must_be_present_and_known(self) -> None:
        chunk = GlossaryChunk(
            chunk_id="one",
            pieces=[GlossaryChunkPiece(reference_id="S1", document_path="c", text="Aster")],
            estimated_tokens=2,
        )
        validate_candidate_evidence(
            GlossaryResult(entries=[glossary_entry("Aster", "阿斯特", evidence=["S1"])]),
            chunk,
        )
        with pytest.raises(GlossaryFormatError, match="no evidence"):
            validate_candidate_evidence(
                GlossaryResult(entries=[glossary_entry("Aster", "阿斯特")]), chunk
            )
        with pytest.raises(GlossaryFormatError, match="unknown"):
            validate_candidate_evidence(
                GlossaryResult(entries=[glossary_entry("Aster", "阿斯特", evidence=["S2"])]),
                chunk,
            )

    def test_candidate_evidence_canonicalizes_zero_padding_only(self) -> None:
        chunk = GlossaryChunk(
            chunk_id="one",
            pieces=[
                GlossaryChunkPiece(
                    reference_id="D0001-S000002",
                    document_path="c",
                    text="Mira",
                )
            ],
            estimated_tokens=2,
        )
        result = canonicalize_candidate_evidence(
            GlossaryResult(
                entries=[
                    glossary_entry(
                        "Mira",
                        "米拉",
                        evidence=["D0001-S00002", "D1-S2", "D0001-S000099"],
                    )
                ]
            ),
            chunk,
        )
        assert result.entries[0].evidence == ["D0001-S000002", "D0001-S000099"]
        with pytest.raises(GlossaryFormatError, match="S000099"):
            validate_candidate_evidence(result, chunk)

    def test_resolution_scope_allows_retranslation_but_rejects_invented_terms(self) -> None:
        candidates = [glossary_entry("Aster", "阿斯塔")]
        validate_resolution_scope(
            GlossaryResult(entries=[glossary_entry("Aster", "阿斯特")]), candidates
        )
        with pytest.raises(GlossaryFormatError, match="invented"):
            validate_resolution_scope(
                GlossaryResult(entries=[glossary_entry("Heron", "石鹭")]), candidates
            )


    def test_obfuscated_resolution_evidence_is_restored_deterministically(self) -> None:
        candidates = [
            glossary_entry("Qelm", "\u5947\u5c14", evidence=["D0007-S000003"]),
            glossary_entry(
                "Qelm",
                "\u5361\u59c6",
                evidence=["D0011-S000009", "D0015-S000004", "D0018-S000002"],
            ),
        ]
        resolved = GlossaryResult(
            entries=[
                glossary_entry(
                    "Qelm",
                    "\u7eea\u5c14",
                    evidence=["D9999-S999999"],
                )
            ]
        )

        restored = restore_glossary_evidence(
            resolved,
            candidates,
            max_evidence_per_entry=3,
        )

        assert restored.entries[0].evidence == ["D0007-S000003", "D0011-S000009", "D0015-S000004"]

    def test_obfuscated_approval_prescreen_routes_only_questionable_entries(self) -> None:
        entries = [
            glossary_entry(
                "Qelm Spindle",
                "奇尔姆纺锤",
                category=GlossaryCategory.TECHNOLOGY,
                evidence=["D0001-S000001"],
            ),
            glossary_entry("Vraxwright", "弗拉克斯赖特"),
            glossary_entry(
                "Quist Engine",
                "奎斯特引擎",
                category=GlossaryCategory.TECHNOLOGY,
                evidence=["D0001-S000002"],
                confidence=0.6,
            ),
            glossary_entry(
                "zorb",
                "佐布",
                category=GlossaryCategory.OTHER,
                evidence=["D0001-S000003"],
            ),
        ]

        cases = build_glossary_approval_cases(entries)
        deterministic, review, reasons = screen_glossary_approval_cases(cases)

        assert [case["term_id"] for case in cases] == ([
            "A00001", "A00002", "A00003", "A00004"
        ])
        assert [GlossaryEntry.model_validate(case["entry"]).english for case in deterministic] == ["Qelm Spindle"]
        assert len(review) == 3
        assert "missing_evidence" in reasons["A00001"]
        assert "low_confidence" in reasons["A00003"]
        assert "suspicious_generic" in reasons["A00004"]
        assert "other_category" in reasons["A00004"]
        assert sum(len(batch) for batch in build_glossary_approval_batches(review, 1)) == 3


class GlossaryMergeTests:
    def test_merge_candidates_combines_evidence_aliases_note_and_confidence(self) -> None:
        first = glossary_entry(
            "Aster", "阿斯特", evidence=["S1"], aliases=["Captain Aster"], confidence=0.6
        )
        second = glossary_entry(
            "aster",
            "阿斯特",
            evidence=["S2"],
            aliases=["Aster Venn"],
            note="故事的主要人物",
            confidence=0.9,
        )
        merged = merge_candidate_entries([first, second])
        assert len(merged) == 1
        assert merged[0].confidence == 0.9
        assert merged[0].evidence == ["S1", "S2"]
        assert merged[0].aliases == ["Aster Venn", "Captain Aster"]
        assert merged[0].note == "故事的主要人物"

    def test_merge_candidates_preserves_translation_alternatives(self) -> None:
        entries = [
            glossary_entry("Heron", "兔子"),
            glossary_entry("Heron", "石鹭"),
        ]
        assert len(merge_candidate_entries(entries)) == 2

    def test_sort_entries_uses_category_then_term(self) -> None:
        entries = [
            glossary_entry("Farshore", "远岸", category=GlossaryCategory.PLACE),
            glossary_entry("Heron", "兔子"),
            glossary_entry("Aster", "阿斯特"),
        ]
        assert [entry.english for entry in sort_glossary_entries(entries)] == ["Aster", "Heron", "Farshore"]

    def test_source_precedence_and_same_level_alternatives(self) -> None:
        sources = [
            GlossarySource("extract", GlossarySourceKind.EXTRACTED, (glossary_entry("Aster", "阿斯塔"),)),
            GlossarySource("seed", GlossarySourceKind.SEED, (glossary_entry("Aster", "阿斯特拉"),)),
            GlossarySource("series-a", GlossarySourceKind.SERIES, (glossary_entry("Aster", "阿斯特"),)),
            GlossarySource("series-b", GlossarySourceKind.SERIES, (glossary_entry("Aster", "阿斯提"),)),
            GlossarySource("book", GlossarySourceKind.BOOK, (glossary_entry("Heron", "石鹭"),)),
        ]
        merged = merge_prioritized_sources(sources)
        aster = [entry.chinese for entry in merged if entry.english.casefold() == "aster"]
        assert aster == ["阿斯提", "阿斯特"]
        assert "石鹭" in [entry.chinese for entry in merged]


class GlossaryFormatTests:
    def test_legacy_roundtrip_preserves_supported_fields(self) -> None:
        original = [
            glossary_entry("Aster", "阿斯特", note="主要人物"),
            glossary_entry("Farshore", "远岸", category=GlossaryCategory.PLACE, note="地点"),
        ]
        parsed = parse_legacy_glossary(render_legacy_glossary(original))
        assert [(entry.english, entry.chinese, entry.note, entry.category) for entry in parsed.entries] == [(entry.english, entry.chinese, entry.note, entry.category) for entry in original]

    def test_legacy_parser_rejects_unknown_missing_category_and_bad_entry(self, subtests) -> None:
        cases = (
            "--未知--\nAster:阿斯特:note",
            "Aster:阿斯特:note",
            "--人名--\nAster:阿斯特",
            "--人名--\nAster:English:note",
        )
        for value in cases:
            with subtests.test(value=value):
                with pytest.raises(GlossaryFormatError):
                    parse_legacy_glossary(value)

    def test_load_json_and_legacy_files(self) -> None:
        result = GlossaryResult(entries=[glossary_entry("Aster", "阿斯特")])
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory, "glossary.json")
            json_path.write_text(result.model_dump_json(), encoding="utf-8")
            text_path = Path(directory, "glossary.txt")
            text_path.write_text(render_legacy_glossary(result.entries), encoding="utf-8")
            assert load_glossary_file(json_path).entries == result.entries
            assert load_glossary_file(text_path).entries[0].english == "Aster"
            json_path.write_text("invalid", encoding="utf-8")
            with pytest.raises(GlossaryFormatError):
                load_glossary_file(json_path)



class GlossaryHarmonizationTests:
    """Variant spellings of one term must not carry competing mandatory renderings."""

    def rendering(self, entries: list[GlossaryEntry], english: str) -> list[str]:
        return sorted(item.chinese for item in entries if item.english == english)

    def test_leading_article_variants_share_the_better_supported_rendering(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Qelm", "奇尔", evidence=["S1", "S2", "S3"]),
                glossary_entry("The Qelm", "奇尔族", evidence=["S4"]),
            ]
        )
        assert self.rendering(entries, "The Qelm") == ["奇尔"]
        assert self.rendering(entries, "Qelm") == ["奇尔"]
        assert report.change_count == 1
        assert report.changes[0].kind == "variant"
        assert report.changes[0].previous_chinese == "奇尔族"

    def test_lowercase_plural_variants_share_one_rendering(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "piercer",
                    "穿刺者",
                    category=GlossaryCategory.ITEM,
                    evidence=["S1", "S2"],
                ),
                glossary_entry("piercers", "穿刺机", category=GlossaryCategory.ITEM),
            ]
        )
        assert self.rendering(entries, "piercers") == ["穿刺者"]
        assert report.change_count == 1

    def test_capitalised_plural_conflict_is_warned_not_rewritten(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Vraxwright", "弗拉克斯赖特", evidence=["S1", "S2"]),
                glossary_entry("Vraxwrights", "弗拉克斯匠人", evidence=["S3"]),
            ]
        )
        assert self.rendering(entries, "Vraxwrights") == ["弗拉克斯匠人"]
        assert report.change_count == 0
        assert any("Vraxwrights" in warning for warning in report.warnings)

    def test_capitalised_plural_group_form_is_consistent(self) -> None:
        # The plural names the family; its rendering legitimately extends the name.
        entries, report = harmonize_glossary(
            [
                glossary_entry("Vrax", "弗拉克斯"),
                glossary_entry("Vraxes", "弗拉克斯家族"),
            ]
        )
        assert self.rendering(entries, "Vraxes") == ["弗拉克斯家族"]
        assert report.change_count == 0
        assert report.warnings == []

    def test_capitalised_plural_never_spreads_a_better_supported_wrong_rendering(
        self,
    ) -> None:
        # A singular carrying the wrong taxon with more evidence must not
        # overwrite a correct plural.
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "Nul beetle",
                    "金龟",
                    category=GlossaryCategory.ITEM,
                    evidence=["S1", "S2", "S3"],
                ),
                glossary_entry(
                    "Nul beetles", "努尔甲虫", category=GlossaryCategory.ITEM
                ),
            ]
        )
        assert self.rendering(entries, "Nul beetles") == ["努尔甲虫"]
        assert report.change_count == 0
        assert any("Nul beetles" in warning for warning in report.warnings)

    def test_higher_priority_source_outranks_extraction_evidence(self) -> None:
        entries, _ = harmonize_glossary(
            [
                glossary_entry("Stranger", "陌生人", evidence=["S1", "S2", "S3"]),
                glossary_entry("The Stranger", "陌生客"),
            ],
            priority_by_term={
                "stranger": GlossarySourceKind.EXTRACTED.priority,
                "the stranger": GlossarySourceKind.BOOK.priority,
            },
        )
        assert self.rendering(entries, "Stranger") == ["陌生客"]

    def test_plain_form_wins_when_support_is_equal(self) -> None:
        entries, _ = harmonize_glossary(
            [
                glossary_entry("The Qelm", "奇尔族"),
                glossary_entry("Qelm", "奇尔"),
            ]
        )
        assert self.rendering(entries, "The Qelm") == ["奇尔"]

    def test_case_distinct_name_and_common_noun_are_not_merged(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "Hearth", "炉灵管家", category=GlossaryCategory.TECHNOLOGY
                ),
                glossary_entry("hearths", "炉膛", category=GlossaryCategory.ITEM),
            ]
        )
        assert self.rendering(entries, "Hearth") == ["炉灵管家"]
        assert self.rendering(entries, "hearths") == ["炉膛"]
        assert report.change_count == 0

    def test_short_bases_do_not_pair_by_plural_suffix(self) -> None:
        _, report = harmonize_glossary(
            [glossary_entry("Ar", "阿尔"), glossary_entry("Ares", "阿瑞斯")]
        )
        assert report.change_count == 0

    def test_deliberate_same_term_alternatives_are_left_for_review(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Heron", "兔子"),
                glossary_entry("Heron", "石鹭"),
                glossary_entry("Herons", "鹭群"),
            ]
        )
        assert self.rendering(entries, "Heron") == ["兔子", "石鹭"]
        assert self.rendering(entries, "Herons") == ["鹭群"]
        assert report.change_count == 0
        assert any("Heron" in warning for warning in report.warnings)

    def test_verbatim_acronym_variants_are_not_a_conflict(self) -> None:
        # A glossary can map an acronym to itself in both singular and plural;
        # copying one rendering onto the other produced an invalid entry.
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "ZQT", "ZQT", category=GlossaryCategory.TERM, evidence=["S1"]
                ),
                glossary_entry("ZQTs", "ZQTs", category=GlossaryCategory.TERM),
            ]
        )
        GlossaryResult(entries=entries)  # the published glossary must still validate
        assert self.rendering(entries, "ZQT") == ["ZQT"]
        assert self.rendering(entries, "ZQTs") == ["ZQTs"]
        assert report.change_count == 0
        assert report.warnings == []

    def test_plural_code_beside_a_translation_is_warned(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "ZQT",
                    "ZQT",
                    category=GlossaryCategory.TERM,
                    evidence=["S1", "S2", "S3"],
                ),
                glossary_entry("ZQTs", "中级资格证书", category=GlossaryCategory.TERM),
            ]
        )
        GlossaryResult(entries=entries)
        assert self.rendering(entries, "ZQTs") == ["中级资格证书"]
        assert report.change_count == 0
        assert any("ZQTs" in warning for warning in report.warnings)

    def test_rewrite_that_would_be_invalid_is_warned_not_applied(self) -> None:
        # Keeping a term in English is only legal when the term is itself a
        # preservable code; ``The ZQT`` cannot hold a verbatim rendering.
        entries, report = harmonize_glossary(
            [
                glossary_entry(
                    "ZQT",
                    "ZQT",
                    category=GlossaryCategory.TERM,
                    evidence=["S1", "S2", "S3"],
                ),
                glossary_entry(
                    "The ZQT", "中级资格证书", category=GlossaryCategory.TERM
                ),
            ]
        )
        GlossaryResult(entries=entries)
        assert self.rendering(entries, "The ZQT") == ["中级资格证书"]
        assert report.change_count == 0
        assert any("The ZQT" in warning for warning in report.warnings)

    def test_full_names_adopt_the_standalone_component_rendering(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Vrell", "韦雷尔", evidence=["S1"]),
                glossary_entry("Vrell Mason", "韦雷勒·梅森"),
                glossary_entry("Helena Vrell Lain", "海伦娜·韦雷勒·莱恩"),
            ]
        )
        assert self.rendering(entries, "Vrell Mason") == ["韦雷尔·梅森"]
        assert self.rendering(entries, "Helena Vrell Lain") == ["海伦娜·韦雷尔·莱恩"]
        assert sorted(change.kind for change in report.changes) == [
            "component",
            "component",
        ]

    def test_component_alignment_follows_the_matching_part_not_word_order(self) -> None:
        # The full name is rendered given-name first; positional alignment
        # duplicated the given name instead of matching the shared part.
        entries, report = harmonize_glossary(
            [
                glossary_entry("Ysera", "伊瑟拉", evidence=["S1"]),
                glossary_entry("Maker Ysera", "伊瑟莎·梅克"),
            ]
        )
        assert self.rendering(entries, "Maker Ysera") == ["伊瑟拉·梅克"]
        assert report.change_count == 1

    def test_component_alignment_never_drops_a_title_fused_into_the_part(self) -> None:
        # A rank fused into the rendered name must not be silently lost.
        entries, report = harmonize_glossary(
            [
                glossary_entry("Vantor", "万托尔", evidence=["S1"]),
                glossary_entry("Captain-Auxillian Vantor", "奥克西利安·万托雷上尉"),
            ]
        )
        assert self.rendering(entries, "Captain-Auxillian Vantor") == [
            "奥克西利安·万托雷上尉"
        ]
        assert report.change_count == 0
        assert any(
            "Captain-Auxillian Vantor" in warning for warning in report.warnings
        )

    def test_component_with_no_resembling_part_is_warned(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Birdbot", "鸟机器人"),
                glossary_entry("Inspector Birdbot", "伯德博特·督察"),
            ]
        )
        assert self.rendering(entries, "Inspector Birdbot") == ["伯德博特·督察"]
        assert report.change_count == 0
        assert any("Inspector Birdbot" in warning for warning in report.warnings)

    def test_variants_in_different_categories_are_not_merged(self) -> None:
        # A ship named "The Render" must not be rewritten from an unrelated
        # person entry that happens to share the base word.
        entries, report = harmonize_glossary(
            [
                glossary_entry("Render", "伦德尔", evidence=["S1", "S2"]),
                glossary_entry("The Render", "裂帆号", category=GlossaryCategory.ITEM),
            ]
        )
        assert self.rendering(entries, "The Render") == ["裂帆号"]
        assert report.change_count == 0
        assert any("The Render" in warning for warning in report.warnings)

    def test_unalignable_component_conflict_is_warned_not_rewritten(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Birdbot", "鸟机器人"),
                glossary_entry("Inspector Birdbot", "伯德博特督察"),
            ]
        )
        assert self.rendering(entries, "Inspector Birdbot") == ["伯德博特督察"]
        assert report.change_count == 0
        assert any("Inspector Birdbot" in warning for warning in report.warnings)

    def test_curated_full_name_is_not_rewritten_from_extracted_component(self) -> None:
        entries, report = harmonize_glossary(
            [
                glossary_entry("Ketch", "凯奇"),
                glossary_entry("Vrie Ketch", "弗莱·凯什"),
            ],
            priority_by_term={
                "ketch": GlossarySourceKind.EXTRACTED.priority,
                "vrie ketch": GlossarySourceKind.BOOK.priority,
            },
        )
        assert self.rendering(entries, "Vrie Ketch") == ["弗莱·凯什"]
        assert report.change_count == 0
        assert any("Vrie Ketch" in warning for warning in report.warnings)

    def test_non_person_compound_titles_are_not_warned(self) -> None:
        _, report = harmonize_glossary(
            [
                glossary_entry("Dominion", "多米尼恩", category=GlossaryCategory.PLACE),
                glossary_entry(
                    "Crown of Dominion", "主权王冠", category=GlossaryCategory.ITEM
                ),
            ]
        )
        assert report.warnings == []

    def test_common_noun_components_are_ignored(self) -> None:
        _, report = harmonize_glossary(
            [
                glossary_entry("engine", "引擎", category=GlossaryCategory.TECHNOLOGY),
                glossary_entry(
                    "Difference engine", "差分机", category=GlossaryCategory.TECHNOLOGY
                ),
            ]
        )
        assert report.change_count == 0
        assert report.warnings == []

    def test_unglossed_recurring_mid_sentence_names_are_reported(self) -> None:
        texts = ["Qelm asked the Hearth for the schedule."] * 12
        texts += ["Aster waited while the Hearth thought."] * 3
        # sentence-initial: not evidence of a name
        texts += ["Hearth was slow today."] * 20
        found = find_unglossed_proper_nouns(
            [document(*texts)], [glossary_entry("Aster", "阿斯特")], min_occurrences=10
        )
        assert found == {"Hearth": 15}

    def test_glossed_names_are_not_reported_as_unglossed(self) -> None:
        texts = ["Then the Stranger moved."] * 12
        found = find_unglossed_proper_nouns(
            [document(*texts)],
            [glossary_entry("The Stranger", "陌生客")],
            min_occurrences=10,
        )
        assert found == {}
