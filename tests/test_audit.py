import math

import pytest

from book_agent.audit import (
    AuditCategory,
    AuditIssue,
    AuditRiskTag,
    AuditSeverity,
    DocumentAudit,
    SemanticAuditResult,
    audit_translated_document,
    build_semantic_audit_batches,
    build_semantic_audit_prompt,
    deduplicate_audit_issues,
    expand_semantic_audit_scope,
    merge_audit_issues,
    reconcile_document_audit,
    select_semantic_audit_candidates,
    validate_semantic_audit_scope,
)
from book_agent.config import AuditConfig
from book_agent.languages import TranslationDirection
from book_agent.ollama_client import estimate_request_tokens
from book_agent.preprocessing import PreprocessedDocument, PreprocessedSegment
from book_agent.schemas import GlossaryCategory, GlossaryEntry
from book_agent.translation import TranslatedDocument, TranslatedSegment


def source_document(texts=None, *, glossary=None):
    texts = texts or ["Hello world.", "Another source sentence."]
    return PreprocessedDocument(
        order=1,
        manifest_id="chapter",
        archive_path="OEBPS/chapter.xhtml",
        source_sha256="a" * 64,
        segments=[
            PreprocessedSegment(
                segment_id=f"D0001-S{index:06d}",
                original_text=text,
                processed_text=text,
            )
            for index, text in enumerate(texts, start=1)
        ],
        relevant_glossary=glossary or [],
    )


def translated_document(texts=None, *, direction=TranslationDirection.EN_TO_ZH, ids=None):
    texts = texts or ["你好，世界。", "另一个源句子。"]
    ids = ids or [f"D0001-S{index:06d}" for index in range(1, len(texts) + 1)]
    return TranslatedDocument(
        order=1,
        manifest_id="chapter",
        archive_path="OEBPS/chapter.xhtml",
        direction=direction,
        style="literary",
        segments=[
            TranslatedSegment(segment_id=item, source_text="source", translated_text=text)
            for item, text in zip(ids, texts)
        ],
    )


def issue(segment="D0001-S000001", category=AuditCategory.MISTRANSLATION, severity=AuditSeverity.MEDIUM, message="specific problem", *, source="deterministic", fix=""):
    return AuditIssue(
        segment_id=segment,
        category=category,
        severity=severity,
        message=message,
        source=source,
        suggested_fix=fix,
    )


class AuditCoreTests:
    def test_severity_rank_is_ordered(self):
        assert AuditSeverity.LOW.rank < AuditSeverity.MEDIUM.rank
        assert AuditSeverity.MEDIUM.rank < AuditSeverity.HIGH.rank

    def test_clean_document_passes(self):
        report = audit_translated_document(
            source_document(), translated_document(), AuditConfig()
        )
        assert report.passed
        assert report.issues == []

    def test_structure_empty_mojibake_and_untranslated_are_reported(self):
        source = source_document()
        translated = translated_document(
            ["", "This remains fully untranslated"],
            ids=["D0001-S000001", "unexpected"],
        )
        report = audit_translated_document(source, translated, AuditConfig())
        categories = {item.category for item in report.issues}
        assert AuditCategory.STRUCTURE in categories
        assert AuditCategory.EMPTY in categories
        separate = audit_translated_document(
            source_document(["Hello there.", "Other text."]),
            translated_document(["This remains fully untranslated", "坏字符â€œ"]),
            AuditConfig(),
        )
        categories = {item.category for item in separate.issues}
        assert AuditCategory.UNTRANSLATED in categories
        assert AuditCategory.PUNCTUATION in categories

    def test_obfuscated_full_span_wrapper_scope_is_a_structure_error(self):
        source = source_document(["<I000>Qelmin crossed the qir field.</I000>"])
        translated = translated_document(
            ["<I000>凯尔敏穿过了</I000>奇尔场。"]
        )

        report = audit_translated_document(source, translated, AuditConfig())

        assert AuditCategory.STRUCTURE in {item.category for item in report.issues}

    def test_obfuscated_inflected_stray_word_is_reported(self):
        source = source_document(["The qelminator renewed the core."])
        translated = translated_document(["核心完成了 qelmination。"])

        report = audit_translated_document(source, translated, AuditConfig())

        assert AuditCategory.UNTRANSLATED in {item.category for item in report.issues}

    def test_approved_exact_preserved_single_word_is_not_a_stray_word(self):
        glossary = [
            GlossaryEntry(
                english="QELMINATOR",
                chinese="QELMINATOR",
                category=GlossaryCategory.TERM,
            )
        ]
        source = source_document(
            ["The qelminator renewed the core."], glossary=glossary
        )
        translated = translated_document(["qelminator 更新了核心。"])

        report = audit_translated_document(source, translated, AuditConfig())

        messages = [
            item.message
            for item in report.issues
            if item.category is AuditCategory.UNTRANSLATED
        ]
        assert not any("word:" in message for message in messages)

    def test_exact_copied_ordinary_source_words_are_reported(self):
        source = source_document(
            ["The occupant somehow reached the sealed chamber."]
        )
        translated = translated_document(["这个occupant somehow进入了密封舱室。"])

        report = audit_translated_document(source, translated, AuditConfig())

        messages = [
            item.message
            for item in report.issues
            if item.category is AuditCategory.UNTRANSLATED
        ]
        assert any("occupant" in message for message in messages)

    def test_bare_domain_is_not_reported_as_untranslated_prose(self):
        source = source_document(
            ["Details are available at hachettespeakersbureau.com today."]
        )
        translated = translated_document(
            ["详情见hachettespeakersbureau.com。"]
        )

        report = audit_translated_document(source, translated, AuditConfig())

        messages = [
            item.message
            for item in report.issues
            if item.category is AuditCategory.UNTRANSLATED
        ]
        assert not any("hachettespeakersbureau" in item for item in messages)

    def test_length_glossary_internal_and_cross_segment_duplicates(self):
        glossary = [
            GlossaryEntry(
                english="Qelmar",
                chinese="\u51ef\u5c14\u739b",
                category=GlossaryCategory.PERSON,
            )
        ]
        source = source_document(
            ["Qelmar walked through a very long and winding corridor.", "Different source."],
            glossary=glossary,
        )
        translated = translated_document(
            ["重复的中文句子。重复的中文句子。", "重复的中文句子。重复的中文句子。"]
        )
        config = AuditConfig(min_length_ratio=0.8, max_length_ratio=1.1)
        report = audit_translated_document(source, translated, config)
        categories = {item.category for item in report.issues}
        assert AuditCategory.GLOSSARY in categories
        assert AuditCategory.DUPLICATION in categories
        assert AuditCategory.OMISSION in categories or AuditCategory.ADDITION in categories

    def test_glossary_audit_uses_direction_and_exact_latin_boundaries(self):
        glossary = [
            GlossaryEntry(
                english="QRM",
                chinese="QRM",
                category=GlossaryCategory.TECHNOLOGY,
            )
        ]
        substring_only = audit_translated_document(
            source_document(["A QRMOR-plated biped vanished."], glossary=glossary),
            translated_document(["\u4e00\u4e2a\u88c5\u7532\u53cc\u8db3\u4f53\u6d88\u5931\u4e86\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY not in {item.category for item in substring_only.issues}

        exact_term = audit_translated_document(
            source_document(["The QRM module vanished."], glossary=glossary),
            translated_document(["\u90a3\u4e2a\u6a21\u5757\u6d88\u5931\u4e86\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY in {item.category for item in exact_term.issues}

    def test_glossary_audit_accepts_any_obfuscated_approved_variant(self):
        glossary = [
            GlossaryEntry(
                english="qelmin pod",
                chinese="\u51ef\u5c14\u8231",
                category=GlossaryCategory.TECHNOLOGY,
            ),
            GlossaryEntry(
                english="qelmin pod",
                chinese="\u51ef\u5c14\u7ef4\u751f\u8231",
                category=GlossaryCategory.ITEM,
            ),
        ]
        report = audit_translated_document(
            source_document(["The qelmin pod opened."], glossary=glossary),
            translated_document(["\u51ef\u5c14\u7ef4\u751f\u8231\u5f00\u542f\u4e86\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY not in {item.category for item in report.issues}

    def test_glossary_audit_does_not_apply_case_bearing_term_to_common_word(self):
        glossary = [
            GlossaryEntry(
                english="VRX",
                chinese="\u7ef4\u5c14\u514b\u65af",
                category=GlossaryCategory.TECHNOLOGY,
            ),
            GlossaryEntry(
                english="Home",
                chinese="\u5bb6\u56ed",
                category=GlossaryCategory.PLACE,
            ),
        ]
        report = audit_translated_document(
            source_document(["His vrx arm returned home."], glossary=glossary),
            translated_document(["\u4ed6\u7684\u624b\u81c2\u56de\u5bb6\u4e86\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY not in {item.category for item in report.issues}

    def test_glossary_audit_enforces_longest_incompatible_nested_term(self):
        glossary = [
            GlossaryEntry(
                english="Nul",
                chinese="\u52aa\u5c14",
                category=GlossaryCategory.OTHER,
            ),
            GlossaryEntry(
                english="Nul Core",
                chinese="\u6838\u5fc3\u4f53",
                category=GlossaryCategory.OTHER,
            ),
        ]
        report = audit_translated_document(
            source_document(["Nul Core stabilized."], glossary=glossary),
            translated_document(["\u7ed3\u6784\u7a33\u5b9a\u4e86\u3002"]),
            AuditConfig(),
        )
        glossary_issues = [
            item for item in report.issues
            if item.category is AuditCategory.GLOSSARY
        ]
        assert len(glossary_issues) == 1
        assert "\u6838\u5fc3\u4f53" in glossary_issues[0].message

    def test_glossary_audit_treats_generic_single_word_as_advisory(self):
        glossary = [
            GlossaryEntry(
                english="clade",
                chinese="\u79cd\u65cf",
                category=GlossaryCategory.TERM,
            )
        ]
        report = audit_translated_document(
            source_document(["The clade gathered."], glossary=glossary),
            translated_document(["\u4eba\u4eec\u805a\u96c6\u8d77\u6765\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY not in {item.category for item in report.issues}

    def test_glossary_audit_enforces_only_compatible_longest_term(self):
        glossary = [
            GlossaryEntry(
                english="Qel",
                chinese="\u51ef\u5c14",
                category=GlossaryCategory.OTHER,
            ),
            GlossaryEntry(
                english="Qel Core",
                chinese="\u51ef\u5c14\u6838\u5fc3",
                category=GlossaryCategory.OTHER,
            ),
        ]
        report = audit_translated_document(
            source_document(["Qel Core stabilized."], glossary=glossary),
            translated_document(["\u7ed3\u6784\u7a33\u5b9a\u4e86\u3002"]),
            AuditConfig(),
        )
        glossary_issues = [
            item for item in report.issues
            if item.category is AuditCategory.GLOSSARY
        ]
        assert len(glossary_issues) == 1
        assert "\u51ef\u5c14\u6838\u5fc3" in glossary_issues[0].message

    def test_glossary_audit_accepts_obfuscated_contextual_demonym_stem(self):
        glossary = [
            GlossaryEntry(
                english="Qel Terran",
                chinese="\u6cfd\u5c14\u5c3c\u4eba",
                category=GlossaryCategory.PERSON,
            )
        ]
        report = audit_translated_document(
            source_document(["Use Qel Terran gravity."], glossary=glossary),
            translated_document(["\u91c7\u7528\u6cfd\u5c14\u5c3c\u91cd\u529b\u6807\u51c6\u3002"]),
            AuditConfig(),
        )
        assert AuditCategory.GLOSSARY not in {item.category for item in report.issues}

    def test_intentional_adjacent_repetition_is_source_grounded_obfuscated(self):
        source = source_document(
            ["Vraxen qelmin rotates. Vraxen qelmin rotates. Zorvak observes."]
        )
        faithful = translated_document(
            ["甲乙丙丁戊己庚辛。甲乙丙丁戊己庚辛。壬癸观察。"]
        )
        report = audit_translated_document(source, faithful, AuditConfig())
        assert AuditCategory.DUPLICATION not in {item.category for item in report.issues}

        excessive = translated_document(
            ["甲乙丙丁戊己庚辛。甲乙丙丁戊己庚辛。甲乙丙丁戊己庚辛。"]
        )
        report = audit_translated_document(source, excessive, AuditConfig())
        assert AuditCategory.DUPLICATION in {item.category for item in report.issues}

    def test_ai_style_is_low_severity_and_does_not_fail_alone(self):
        report = audit_translated_document(
            source_document(["This is a sufficiently long source sentence."]),
            translated_document(["值得注意的是，这是一段自然中文。"]),
            AuditConfig(),
        )
        assert report.issues[0].category == AuditCategory.AI_STYLE
        assert report.issues[0].severity == AuditSeverity.LOW
        assert report.passed

    def test_chinese_to_english_untranslated_run_is_reported(self):
        report = audit_translated_document(
            source_document(["阿斯特走进花园。"]),
            translated_document(
                ["Aster entered 花园里面。"], direction=TranslationDirection.ZH_TO_EN
            ),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED in {item.category for item in report.issues}

    def test_parenthetical_english_gloss_does_not_count_as_untranslated_prose(self):
        report = audit_translated_document(
            source_document(["Things were much of a muchness in the story."]),
            translated_document(["这些事情大同小异（much of a muchness）。"]),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED not in {item.category for item in report.issues}

    def test_intentional_foreign_quotation_does_not_count_as_untranslated_english(self):
        report = audit_translated_document(
            source_document(
                ['She opened her French lesson-book and asked, “Où est ma chatte?”']
            ),
            translated_document(['她打开法语课本，问道：“Où est ma chatte?”']),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED not in {item.category for item in report.issues}

    def test_foreign_quotation_allows_fullwidth_target_punctuation(self):
        report = audit_translated_document(
            source_document(
                ['She asked in French, “Où est ma chatte?”']
            ),
            translated_document(['她用法语问：“Où est ma chatte？”']),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED not in {item.category for item in report.issues}

    def test_copied_english_quotation_still_counts_as_untranslated(self):
        report = audit_translated_document(
            source_document(['She asked, “Where is my cat?”']),
            translated_document(['她问道：“Where is my cat?”']),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED in {item.category for item in report.issues}

    def test_single_copied_source_word_is_reported_as_untranslated(self, subtests):
        for source, target, residue in (
            (
                "He remained clinging to the wall.",
                "\u4ed6\u4ecdclinging\u5728\u5899\u4e0a\u3002",
                "clinging",
            ),
            (
                "A sprawling city lay below.",
                "\u4e00\u5ea7sprawling\u7684\u57ce\u5e02\u4f4d\u4e8e\u4e0b\u65b9\u3002",
                "sprawling",
            ),
        ):
            with subtests.test(residue=residue):
                report = audit_translated_document(
                    source_document([source]),
                    translated_document([target]),
                    AuditConfig(),
                )
                messages = [
                    item.message
                    for item in report.issues
                    if item.category is AuditCategory.UNTRANSLATED
                ]
                assert any(residue in message for message in messages)

    def test_exact_inline_foreign_phrase_is_intentionally_preserved(self):
        report = audit_translated_document(
            source_document(
                ["The sea was called <I000>den wild zee</I000> by the Dutch."]
            ),
            translated_document(
                ["荷兰人把这片海称为<I000>den wild zee</I000>。"]
            ),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED not in {item.category for item in report.issues}

    def test_obfuscated_titlecase_codename_is_not_a_deterministic_failure(self):
        report = audit_translated_document(
            source_document(["The operative used the codename Alter Node Seven."]),
            translated_document(
                ["\u8fd9\u540d\u7279\u5de5\u4f7f\u7528\u4ee3\u53f7Alter Node Seven\u3002"]
            ),
            AuditConfig(),
        )
        assert AuditCategory.UNTRANSLATED not in {item.category for item in report.issues}

    def test_separator_rows_are_allowed_unchanged(self):
        report = audit_translated_document(
            source_document(["* * *<I000></I000> * *<I001></I001> *"]),
            translated_document(["* * *<I000></I000> * *<I001></I001> *"]),
            AuditConfig(),
        )
        assert report.passed
        assert report.issues == []
        assert report.semantic_candidate_ids == []

    def test_identifier_only_publication_row_is_allowed_unchanged(self):
        text = (
            "<I000>ISBN 978-0-123-12345-9 PDF<I001></I001> "
            "ISBN 978-0-123-12345-4 EPUB</I000>"
        )
        report = audit_translated_document(
            source_document([text]),
            translated_document([text]),
            AuditConfig(),
        )

        assert report.passed
        assert report.issues == []
        assert report.semantic_candidate_ids == []

    def test_obfuscated_neutral_content_is_filtered_but_numbers_stay_intact(self):
        source = source_document(["2047", "QX-2047/A", "Velnor holds 12 qirks."])
        translated = translated_document(["2047", "QX-2047/A", "甲乙持有13个丙丁。"])
        report = audit_translated_document(source, translated, AuditConfig())
        assert "D0001-S000001" not in report.semantic_candidate_ids
        assert "D0001-S000002" not in report.semantic_candidate_ids
        numeric_issues = [
            item
            for item in report.issues
            if item.message.startswith("numeric content differs from source")
        ]
        assert [item.segment_id for item in numeric_issues] == ["D0001-S000003"]
        assert "source facts:" in numeric_issues[0].message
        assert "translation facts:" in numeric_issues[0].message

    def test_obfuscated_short_heading_skips_prose_ratio(self):
        report = audit_translated_document(
            source_document(["Qelvar"]),
            translated_document(["甲乙丙丁戊己庚辛"]),
            AuditConfig(max_length_ratio=1.1),
        )
        assert AuditCategory.ADDITION not in {item.category for item in report.issues}

    def test_stale_obfuscated_findings_do_not_survive_current_policy(self):
        source = source_document(["QX-2047/A"])
        translated = translated_document(["QX-2047/A"])
        historical = DocumentAudit(
            document_id="chapter",
            archive_path="OEBPS/chapter.xhtml",
            direction=TranslationDirection.EN_TO_ZH,
            segment_count=1,
            passed=False,
            issues=[issue(source="semantic", message="Obfuscated stale diagnosis")],
        )
        refreshed = reconcile_document_audit(source, translated, historical, AuditConfig())
        assert refreshed.passed
        assert refreshed.issues == []

    def test_obfuscated_deferred_translation_survives_reconciliation(self):
        source = source_document(["Vrax nel tor."])
        translated = translated_document(["Vrax nel tor."])
        deferred = issue(
            category=AuditCategory.UNTRANSLATED,
            severity=AuditSeverity.HIGH,
            source="translation-deferred",
            message="Obfuscated source safety copy awaits complete translation.",
            fix="Translate the complete source segment.",
        )
        historical = DocumentAudit(
            document_id="chapter",
            archive_path="OEBPS/chapter.xhtml",
            direction=TranslationDirection.EN_TO_ZH,
            segment_count=1,
            passed=False,
            issues=[deferred],
        )

        refreshed = reconcile_document_audit(
            source, translated, historical, AuditConfig()
        )

        assert not refreshed.passed
        assert any(item.source == "translation-deferred" for item in refreshed.issues)

    def test_semantic_punctuation_claim_is_a_nonblocking_warning(self):
        result = validate_semantic_audit_scope(
            SemanticAuditResult(
                issues=[
                    issue(
                        category=AuditCategory.PUNCTUATION,
                        severity=AuditSeverity.HIGH,
                        source="semantic",
                        fix="Adjust the obfuscated punctuation.",
                    )
                ]
            ),
            ["D0001-S000001"],
        )
        assert result.issues[0].severity == AuditSeverity.LOW

    def test_semantic_candidate_selection_uses_issues_risk_sampling_and_cap(self):
        source = source_document(
            ["short", "It measured 12 miles.", "word " * 800, "last"]
        )
        config = AuditConfig(
            semantic_min_source_tokens=100,
            semantic_sample_every=4,
            max_semantic_candidates_per_document=3,
        )
        selected = select_semantic_audit_candidates(
            source, [issue("D0001-S000001")], config
        )
        assert selected == ["D0001-S000001", "D0001-S000002", "D0001-S000003"]

    def test_inline_marker_ids_do_not_trigger_numeric_semantic_review(self):
        source = source_document(["A short <I000>emphasized</I000> phrase."])
        selected = select_semantic_audit_candidates(source, [], AuditConfig())
        assert selected == []

    def test_semantic_batches_obey_budget_and_validate_ids(self):
        source = source_document(["one two three", "four five six", "seven eight"])
        target = translated_document(["一二三", "四五六", "七八"])
        ids = [item.segment_id for item in source.segments]
        batches = build_semantic_audit_batches(source, target, ids, 5)
        assert len(batches) > 1
        assert [item for batch in batches for item in batch] == ids
        with pytest.raises(ValueError):
            build_semantic_audit_batches(source, target, ids, 0)
        with pytest.raises(ValueError, match="unknown"):
            build_semantic_audit_batches(source, target, ["missing"], 10)
        with pytest.raises(ValueError, match="exceeds"):
            build_semantic_audit_batches(
                source_document(["word " * 100]),
                translated_document(["译文"]),
                ["D0001-S000001"],
                2,
            )

    def test_semantic_batches_can_isolate_each_high_risk_candidate(self):
        source = source_document(["one", "two", "three"])
        target = translated_document(["\u4e00", "\u4e8c", "\u4e09"])
        ids = [item.segment_id for item in source.segments]

        batches = build_semantic_audit_batches(
            source,
            target,
            ids,
            100,
            max_candidates_per_batch=1,
        )

        assert batches == [[item] for item in ids]
        with pytest.raises(ValueError, match="max_candidates_per_batch"):
            build_semantic_audit_batches(
                source,
                target,
                ids,
                100,
                max_candidates_per_batch=0,
            )

    def test_semantic_batches_split_to_fit_complete_request_context(self):
        source = source_document(
            ["one " * 800, "two " * 800, "three " * 800, "four " * 800]
        )
        target = translated_document(
            ["一" * 800, "二" * 800, "三" * 800, "四" * 800]
        )
        all_ids = [item.segment_id for item in source.segments]
        ids = [all_ids[0], all_ids[3]]

        batches = build_semantic_audit_batches(
            source,
            target,
            ids,
            8_000,
            max_request_context=16_384,
        )

        assert batches == [[ids[0]], [ids[1]]]

    def test_semantic_prompt_marks_candidates_and_neighbors(self):
        source = source_document(
            ["first", "Orbit-Relay second", "third", "fourth"],
            glossary=[
                GlossaryEntry(
                    english="Orbit-Relay",
                    chinese="轨道中继",
                    category=GlossaryCategory.TERM,
                )
            ],
        )
        target = translated_document(["一", "二", "三", "四"])
        prompt = build_semantic_audit_prompt(source, target, ["D0001-S000002"])
        assert "[D0001-S000002] AUDIT HIGH RISK" in prompt
        assert "[D0001-S000001] CONTEXT ONLY" in prompt
        assert "[D0001-S000003] CONTEXT ONLY" in prompt
        assert "Never report a finding against a CONTEXT ONLY segment" in prompt
        assert "[D0001-S000004]" not in prompt
        assert "Do not score" in prompt
        assert "grammatically acceptable" in prompt
        assert "matter of stylistic taste" in prompt
        assert "Orbit-Relay => 轨道中继" in prompt
        assert "intentionally cut off by a dash" in prompt
        assert "source-owned italicized fictional" in prompt
        assert "elliptical purpose" in prompt
        assert "verify whether its target-language equivalent" in prompt
        assert "source_quote and/or translation_quote" in prompt
        assert "action versus state" in prompt
        assert "distance, and spatial relationship" in prompt
        assert "turn a state, position, separation, or measurement into motion" in prompt
        assert "report every independent concrete defect" in prompt
        with pytest.raises(ValueError, match="unknown"):
            build_semantic_audit_prompt(source, target, ["missing"])

    def test_semantic_prompt_sheds_only_optional_neighbors_to_fit_context(self):
        source = source_document(
            ["previous " * 900, "target fact", "following " * 900]
        )
        target = translated_document(
            [
                "previous translation " * 900,
                "target translation",
                "following translation " * 900,
            ]
        )
        candidate = "D0001-S000002"

        full_prompt = build_semantic_audit_prompt(source, target, [candidate])
        bounded_prompt = build_semantic_audit_prompt(
            source,
            target,
            [candidate],
            max_request_context=8_192,
        )

        assert f"[{candidate}] AUDIT HIGH RISK" in bounded_prompt
        assert "SOURCE: target fact" in bounded_prompt
        assert "TRANSLATION: target translation" in bounded_prompt
        assert bounded_prompt != full_prompt
        assert "previous translation" not in bounded_prompt
        assert "following translation" not in bounded_prompt
        required = math.ceil(
            estimate_request_tokens(
                bounded_prompt,
                SemanticAuditResult.model_json_schema(),
            )
            * 2.0
        ) + 4_096
        assert required <= 8_192
        assert (build_semantic_audit_batches(
                source,
                target,
                [candidate],
                100,
                max_request_context=8_192,
            )) == [[candidate]]

    def test_semantic_prompt_is_unchanged_when_full_scope_fits_context(self):
        source = source_document(["first", "target", "third"])
        target = translated_document(["one", "two", "three"])
        candidate = "D0001-S000002"

        unbounded = build_semantic_audit_prompt(source, target, [candidate])
        bounded = build_semantic_audit_prompt(
            source,
            target,
            [candidate],
            max_request_context=16_384,
        )

        assert bounded == unbounded

    def test_semantic_scope_expands_to_immediate_neighbors(self):
        source = source_document(["first", "second", "third", "fourth"])
        assert expand_semantic_audit_scope(source, ["D0001-S000002"]) == ["D0001-S000001", "D0001-S000002", "D0001-S000003"]
        with pytest.raises(ValueError, match="unknown"):
            expand_semantic_audit_scope(source, ["missing"])

    def test_long_unit_bearing_prose_is_selected_for_semantic_review(self):
        source = source_document(
            ["The survey capsule should now be nearing the lower boundary, 4000 units below."]
        )
        selected = select_semantic_audit_candidates(source, [], AuditConfig())
        assert selected == ["D0001-S000001"]

    def test_semantic_scope_requires_allowed_ids_and_normalizes_owned_fields(self):
        valid = SemanticAuditResult(
            issues=[issue(source="semantic", fix="Use the correct unit.")]
        )
        validated_valid = validate_semantic_audit_scope(
            valid, ["D0001-S000001"]
        )
        assert validated_valid.issues[0].risk_tags == {AuditRiskTag.QUANTITY}
        with pytest.raises(ValueError, match="out-of-scope"):
            validate_semantic_audit_scope(valid, ["other"])
        normalized_source = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[issue(fix="Complete fix")]),
            ["D0001-S000001"],
        )
        assert normalized_source.issues[0].source == "semantic"
        normalized = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[issue(source="semantic", fix="no")]),
            ["D0001-S000001"],
        )
        assert "faithfully resolves" in normalized.issues[0].suggested_fix
        preference = issue(
            message=(
                "The translation is grammatically acceptable but slightly formulaic; "
                "another word order may sound more natural."
            ),
            fix="Prefer another word order.",
        )
        filtered = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[preference]), ["D0001-S000001"]
        )
        assert filtered.issues == []

    def test_semantic_scope_normalizes_unique_zero_padding_variant(self):
        finding = issue(
            segment="D0042-S00007",
            source="semantic",
            fix="Replace the mismatched obfuscated unit.",
        )
        validated = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[finding]),
            ["D0042-S000007"],
        )
        assert validated.issues[0].segment_id == "D0042-S000007"

        unrelated = finding.model_copy(update={"segment_id": "D0042-S00008"})
        with pytest.raises(ValueError, match="out-of-scope"):
            validate_semantic_audit_scope(
                SemanticAuditResult(issues=[unrelated]),
                ["D0042-S000007"],
            )

    def test_semantic_scope_reassigns_evidence_to_unique_matching_segment(self):
        result = SemanticAuditResult(
            issues=[
                issue(source="semantic", fix="Correct it.").model_copy(
                    update={
                        "source_quote": "Another source sentence.",
                        "translation_quote": "另一个源句子。",
                    }
                )
            ]
        )
        source_by_id = {
            "D0001-S000001": "Hello world.",
            "D0001-S000002": "Another source sentence.",
        }
        translation_by_id = {
            "D0001-S000001": "你好，世界。",
            "D0001-S000002": "另一个源句子。",
        }
        validated = validate_semantic_audit_scope(
            result,
            list(source_by_id),
            source_by_id,
            translation_by_id,
        )
        assert len(validated.issues) == 1
        assert validated.issues[0].segment_id == "D0001-S000002"

    def test_semantic_scope_discards_evidence_that_matches_no_segment(self):
        ungrounded = SemanticAuditResult(
            issues=[
                issue(source="semantic", fix="Correct it.").model_copy(
                    update={"source_quote": "Text absent from this audit batch."}
                )
            ]
        )
        validated = validate_semantic_audit_scope(
            ungrounded,
            ["D0001-S000001"],
            {"D0001-S000001": "Hello world."},
            {"D0001-S000001": "你好，世界。"},
        )
        assert validated.issues == []

    def test_semantic_scope_accepts_grounded_evidence(self):
        grounded = SemanticAuditResult(
            issues=[
                issue(source="semantic", fix="Correct it.").model_copy(
                    update={
                        "source_quote": "Hello world.",
                        "translation_quote": "你好，世界。",
                    }
                )
            ]
        )
        validated = validate_semantic_audit_scope(
            grounded,
            ["D0001-S000001"],
            {"D0001-S000001": "Hello world."},
            {"D0001-S000001": "你好，世界。"},
        )
        assert validated == grounded

    def test_semantic_scope_infers_structured_high_risk_tags_from_diagnosis(self):
        source_text = "The pilot moved into the chamber, not out of it."
        translation_text = "飞行员离开了舱室。"
        finding = issue(
            source="semantic",
            message=(
                "The spatial direction is reversed, and the translation drops the "
                "source negation."
            ),
            fix="Restore the into relation and negative contrast.",
        ).model_copy(
            update={
                "source_quote": source_text,
                "translation_quote": translation_text,
            }
        )

        validated = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[finding]),
            ["D0001-S000001"],
            {"D0001-S000001": source_text},
            {"D0001-S000001": translation_text},
        )

        assert validated.issues[0].risk_tags == {AuditRiskTag.SPATIAL, AuditRiskTag.POLARITY, AuditRiskTag.RELATION}

    def test_semantic_scope_discards_obfuscated_false_countdown_omission(self):
        source_text = "The qel countdown began: Ten qirks. Eight, seven..."
        translation_text = (
            "\u5947\u5c14\u5012\u8ba1\u65f6\u5f00\u59cb\uff1a\u5341\u4e2a\u5355\u4f4d\u3002\u516b\uff0c\u4e03\u2026\u2026"
        )
        finding = issue(
            category=AuditCategory.OMISSION,
            source="semantic",
            message="The translation omits the number seven from the qel countdown.",
            fix="Restore the omitted countdown number.",
        ).model_copy(
            update={
                "source_quote": source_text,
                "translation_quote": translation_text,
            }
        )
        validated = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[finding]),
            ["D0001-S000001"],
            {"D0001-S000001": source_text},
            {"D0001-S000001": translation_text},
        )
        assert validated.issues == []

    def test_semantic_scope_discards_preserved_designation_preference(self):
        finding = issue(
            source="semantic",
            message=(
                "The translation fails to translate the abbreviation QZ in the "
                "numbered designation."
            ),
            fix="Expand the abbreviation.",
        ).model_copy(
            update={
                "source_quote": "a QZ #7 shell",
                "translation_quote": "\u4e00\u679aQZ #7\u5916\u58f3",
            }
        )
        validated_identifier = validate_semantic_audit_scope(
            SemanticAuditResult(issues=[finding]),
            ["D0001-S000001"],
            {"D0001-S000001": "The probe used a QZ #7 shell."},
            {"D0001-S000001": "\u63a2\u6d4b\u5668\u4f7f\u7528\u4e00\u679aQZ #7\u5916\u58f3\u3002"},
        )
        assert validated_identifier.issues == []

    def test_semantic_scope_discards_explicit_noop_suggested_fix(self):
        noop = SemanticAuditResult(
            issues=[
                issue(
                    source="semantic",
                    fix="Change '接管制糖厂' to '接管制糖厂'.",
                )
            ]
        )
        validated = validate_semantic_audit_scope(noop, ["D0001-S000001"])
        assert validated.issues == []

    def test_issue_merge_and_deduplication_are_stable(self):
        first = issue(message="Same problem")
        duplicate = issue(message=" same   problem ")
        other = issue(category=AuditCategory.ADDITION, message="Other problem")
        assert len(deduplicate_audit_issues([first, duplicate])) == 1
        merged = merge_audit_issues([other], [first, duplicate])
        assert len(merged) == 2
        assert merged == deduplicate_audit_issues(merged)

