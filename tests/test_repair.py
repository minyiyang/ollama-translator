import pytest

from book_agent.audit import AuditCategory, AuditIssue, AuditSeverity, DocumentAudit
from book_agent.config import AppConfig
from book_agent.languages import TranslationDirection
from book_agent.repair import (
    PairwiseRepairVerification,
    PairwiseRepairVerificationResult,
    RepairedDocument,
    RepairVerification,
    RepairVerificationResult,
    RepairDisposition,
    LocatedEdit,
    LocatedRepairResult,
    SegmentRepair,
    apply_deterministic_repairs,
    apply_segment_repairs,
    apply_located_edits,
    build_repair_prompt,
    build_located_repair_prompt,
    build_repair_verification_prompt,
    build_repair_verification_batches,
    map_pairwise_repair_verification,
    repair_verification_output_token_limit,
    requires_full_segment_translation,
    select_repair_targets,
    retrieve_related_source_context,
    validate_repair_output,
    validate_repair_verification_scope,
    validate_pairwise_repair_verification_scope,
)
from book_agent.schemas import GlossaryCategory, GlossaryEntry
from tests.test_audit import source_document, translated_document


def audit_issue(segment_id="D0001-S000001", severity=AuditSeverity.MEDIUM):
    return AuditIssue(
        segment_id=segment_id,
        category=AuditCategory.MISTRANSLATION,
        severity=severity,
        message="The unit is translated incorrectly.",
        suggested_fix="Use the correct target-language unit.",
        source="semantic",
    )


def document_audit(issues):
    return DocumentAudit(
        document_id="chapter",
        archive_path="OEBPS/chapter.xhtml",
        direction=TranslationDirection.EN_TO_ZH,
        segment_count=2,
        passed=False,
        issues=issues,
    )


class RepairCoreTests:
    def test_deterministic_repair_applies_exact_glossary_instruction(self):
        issue = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.GLOSSARY,
            severity=AuditSeverity.MEDIUM,
            message="The approved glossary rendering is inconsistent.",
            suggested_fix="Replace all instances of '鲁滨逊' with '鲁滨孙'.",
            source="semantic",
        )
        repaired, rules = apply_deterministic_repairs(
            "鲁滨逊看见鲁滨逊的足迹。", [issue]
        )
        assert repaired == "鲁滨孙看见鲁滨孙的足迹。"
        assert rules == ["exact_glossary_replacement"]

    def test_deterministic_repair_removes_only_diagnosed_marker_duplicate(self):
        duplicate = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.DUPLICATION,
            severity=AuditSeverity.HIGH,
            message="The word is duplicated adjacent to its inline marker.",
            source="deterministic",
        )
        repaired, rules = apply_deterministic_repairs(
            "当我<I000>我</I000>发现", [duplicate]
        )
        assert repaired == "当<I000>我</I000>发现"
        assert rules == ["adjacent_marker_deduplication"]

        unchanged, no_rules = apply_deterministic_repairs(
            "当我<I000>我</I000>发现", [audit_issue()]
        )
        assert unchanged == "当我<I000>我</I000>发现"
        assert no_rules == []

    def test_target_selection_groups_by_document_segment_and_threshold(self):
        audits = [
            document_audit(
                [audit_issue(severity=AuditSeverity.LOW), audit_issue("D0001-S000002", AuditSeverity.HIGH)]
            )
        ]
        targets = select_repair_targets(audits, "medium")
        assert list(targets["chapter"]) == ["D0001-S000002"]
        with pytest.raises(ValueError):
            select_repair_targets(audits, "urgent")

    def test_repair_prompt_is_scoped_and_includes_style_issue_and_neighbors(self):
        source = source_document(["first", "second", "third"])
        translated = translated_document(["第一", "错误译文", "第三"])
        prompt = build_repair_prompt(
            source,
            translated,
            "D0001-S000002",
            [audit_issue("D0001-S000002")],
            AppConfig(),
        )
        assert "Repair only segment D0001-S000002" in prompt
        assert "[D0001-S000001] CONTEXT ONLY" in prompt
        assert "[D0001-S000002] REPAIR" in prompt
        assert "Naturalness overlay" in prompt
        assert "unit is translated incorrectly" in prompt
        assert "当<I000>我</I000>发现" in prompt
        assert "Output exactly <D0001-S000002>" in prompt
        with pytest.raises(ValueError, match="unknown"):
            build_repair_prompt(source, translated, "missing", [], AppConfig())

    def test_obfuscated_deferred_prompt_requires_complete_translation(self):
        source = source_document(["Vrax nel tor."])
        translated = translated_document(["Vrax nel tor."])
        deferred = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.UNTRANSLATED,
            severity=AuditSeverity.HIGH,
            message="Obfuscated first draft was retained for repair.",
            suggested_fix="Translate the complete source segment.",
            source="translation-deferred",
        )

        assert requires_full_segment_translation([deferred])
        prompt = build_repair_prompt(
            source,
            translated,
            "D0001-S000001",
            [deferred],
            AppConfig(),
        )

        assert "Translate the complete SOURCE" in prompt
        assert "must be replaced completely" in prompt
        assert "Do not copy source-language prose" in prompt
        assert "Do not broadly rewrite" not in prompt
        with pytest.raises(ValueError, match="complete segment translation"):
            build_located_repair_prompt(
                source,
                translated,
                "D0001-S000001",
                [deferred],
                AppConfig(),
            )

    def test_high_untranslated_safety_copy_requires_complete_translation(self):
        untranslated = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.UNTRANSLATED,
            severity=AuditSeverity.HIGH,
            message="translation is identical to source",
            source="deterministic",
        )

        assert requires_full_segment_translation([untranslated])

    def test_full_translation_recovery_is_not_blocked_by_old_quantity_baseline(self):
        deferred = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.UNTRANSLATED,
            severity=AuditSeverity.HIGH,
            message="Source safety copy awaits complete translation.",
            suggested_fix="Translate the complete source segment.",
            source="translation-deferred",
        )
        config = AppConfig.model_validate(
            {
                "audit": {
                    "quantity": {
                        "enabled": True,
                        "mode": "enforce",
                        "verify_repairs": True,
                    }
                }
            }
        )

        repaired, validation = validate_repair_output(
            "<D0001-S000001>所有人都到了。</D0001-S000001>",
            "All of them arrived.",
            "D0001-S000001",
            "All of them arrived.",
            config,
            trigger_issues=[deferred],
        )

        assert repaired == "所有人都到了。"
        assert "quantity_integrity" not in {item.code for item in validation.issues}
        assert validation.passed

    def test_repair_validation_accepts_change_and_rejects_contract_or_no_change(self):
        config = AppConfig()
        repaired, validation = validate_repair_output(
            "<D0001-S000001>正确译文。</D0001-S000001>",
            "Original source.",
            "D0001-S000001",
            "错误译文。",
            config,
        )
        assert validation.passed
        assert repaired == "正确译文。"
        _, invalid = validate_repair_output(
            "outside",
            "Original source.",
            "D0001-S000001",
            "错误译文。",
            config,
        )
        assert not invalid.passed
        _, unchanged = validate_repair_output(
            "<D0001-S000001>错误译文。</D0001-S000001>",
            "Original source.",
            "D0001-S000001",
            "错误译文。",
            config,
        )
        assert "repair_unchanged" in {item.code for item in unchanged.issues}

        protected, protected_validation = validate_repair_output(
            "<D0001-S000001>QX-2047/A</D0001-S000001>",
            "QX-2047/A",
            "D0001-S000001",
            "QX-2047/A",
            config,
        )
        assert protected == "QX-2047/A"
        assert protected_validation.passed

        _, changed_number = validate_repair_output(
            "<D0001-S000001>甲乙2048丙。</D0001-S000001>",
            "Velnor counted 2047 qirks.",
            "D0001-S000001",
            "甲乙2047丙。",
            config,
        )
        assert "number_integrity" in {item.code for item in changed_number.issues}

        corrected_magnitude, corrected_validation = validate_repair_output(
            (
                "<D0001-S000001>"
                "\u4e00\u767e\u4ebf\u53ea\u5947\u5c14\u514b\u901a\u8fc7\u3002"
                "</D0001-S000001>"
            ),
            "Ten billion qirks passed through.",
            "D0001-S000001",
            "\u5341\u4ebf\u53ea\u5947\u5c14\u514b\u901a\u8fc7\u3002",
            config,
        )
        assert corrected_magnitude == "\u4e00\u767e\u4ebf\u53ea\u5947\u5c14\u514b\u901a\u8fc7\u3002"
        assert corrected_validation.passed

        glossary = [
            GlossaryEntry(
                english="qelmin gate",
                chinese="凯尔门",
                category=GlossaryCategory.TECHNOLOGY,
            )
        ]
        _, changed_term = validate_repair_output(
            "<D0001-S000001>传送门已经开启。</D0001-S000001>",
            "The qelmin gate opened.",
            "D0001-S000001",
            "凯尔门已经开启。",
            config,
            glossary,
        )
        assert "glossary_integrity" in {item.code for item in changed_term.issues}

    def test_repair_must_remove_its_untranslated_trigger(self):
        trigger = AuditIssue(
            segment_id="D0001-S000001",
            category=AuditCategory.UNTRANSLATED,
            severity=AuditSeverity.MEDIUM,
            message="possible untranslated English word: clinging",
            source="deterministic",
        )

        _, validation = validate_repair_output(
            "<D0001-S000001>他仍然clinging在墙壁上。</D0001-S000001>",
            "He remained clinging to the wall.",
            "D0001-S000001",
            "他仍clinging在墙上。",
            AppConfig(),
            trigger_issues=[trigger],
        )

        assert "repair_trigger_unresolved" in {item.code for item in validation.issues}

    def test_apply_repairs_changes_only_successful_known_segments(self):
        document = translated_document(["原译一", "原译二"])
        repairs = [
            SegmentRepair(
                segment_id="D0001-S000001",
                disposition=RepairDisposition.REPAIRED,
                original_translation="原译一",
                repaired_translation="修订一",
                issues=[audit_issue()],
                attempts=1,
            ),
            SegmentRepair(
                segment_id="D0001-S000002",
                disposition=RepairDisposition.ACCEPTED,
                original_translation="原译二",
                repaired_translation="",
                issues=[audit_issue("D0001-S000002")],
                attempts=2,
            ),
        ]
        result = apply_segment_repairs(document, repairs)
        assert [item.translated_text for item in result.segments] == ["修订一", "原译二"]
        bad = repairs[0].model_copy(update={"segment_id": "unknown"})
        with pytest.raises(ValueError, match="unknown"):
            apply_segment_repairs(document, [bad])

    def test_located_edits_are_exact_unique_and_nonoverlapping(self):
        text = "甲VORP乙NEXA丙"
        changed = apply_located_edits(
            text,
            [
                LocatedEdit(old_span="VORP", new_span="QIRK", reason="obfuscated fix"),
                LocatedEdit(old_span="NEXA", new_span="TULM", reason="obfuscated fix"),
            ],
        )
        assert changed == "甲QIRK乙TULM丙"
        with pytest.raises(ValueError, match="exactly once"):
            apply_located_edits("VORP-VORP", [LocatedEdit(old_span="VORP", new_span="Q", reason="obfuscated fix")])
        with pytest.raises(ValueError, match="overlap"):
            apply_located_edits(
                "VORPNEXA",
                [
                    LocatedEdit(old_span="VORPNE", new_span="Q", reason="obfuscated fix"),
                    LocatedEdit(old_span="NEXA", new_span="T", reason="obfuscated fix"),
                ],
            )

    def test_located_repair_prompt_uses_immutable_obfuscated_spans(self):
        source = source_document(["A zorvak crossed the qelmin gate."])
        draft = translated_document(["一只佐瓦克穿过了错误的凯尔门。"])
        prompt = build_located_repair_prompt(
            source,
            draft,
            "D0001-S000001",
            [audit_issue()],
            AppConfig(),
        )
        assert "old_span must be copied" in prompt
        assert "do not rewrite the full segment" in prompt
        assert "Output exactly <" not in prompt
        proposal = LocatedRepairResult(
            edits=[
                LocatedEdit(
                    old_span="错误的凯尔门",
                    new_span="正确的凯尔门",
                    reason="Repairs the obfuscated gate term.",
                )
            ]
        )
        assert apply_located_edits(draft.segments[0].translated_text, proposal.edits) == "一只佐瓦克穿过了正确的凯尔门。"

    def test_related_context_is_bounded_deterministic_and_obfuscated(self):
        source = source_document(
            [
                "Zorvak qelmin alpha.",
                "Neutral filler passage.",
                "Zorvak qelmin pivots.",
                "Another neutral passage.",
                "Qelmin returns to Zorvak.",
            ]
        )
        first = retrieve_related_source_context(
            source, "D0001-S000003", max_passages=1, max_tokens=10
        )
        second = retrieve_related_source_context(
            source, "D0001-S000003", max_passages=1, max_tokens=10
        )
        assert first == second
        assert first == ["Zorvak qelmin alpha."]

    def test_repair_prompt_includes_related_source_only_obfuscated(self):
        source = source_document(
            [
                "Vrax uses qelmin as an oath.",
                "Neutral nelo passage.",
                "A qelmin phrase needs repair.",
                "Another neutral tor passage.",
                "Qelmin returns in Vrax speech.",
            ]
        )
        draft = translated_document(
            ["甲。", "乙。", "丙。", "丁。", "戊。"]
        )
        prompt = build_repair_prompt(
            source,
            draft,
            "D0001-S000003",
            [audit_issue("D0001-S000003")],
            AppConfig(),
        )
        assert "[RELATED SOURCE ONLY]" in prompt
        assert "Vrax uses qelmin as an oath." in prompt
        assert "grammatical subject and atomic subject-predicate facts" in prompt
        assert "keep the correction stative" in prompt
        assert "do not invent an opposite side" in prompt
        assert "without changing what they measure" in prompt
        assert "CURRENT: 甲。" not in prompt

    def test_repair_verification_prompt_and_exact_scope(self):
        source = source_document()
        draft = translated_document()
        repair = SegmentRepair(
            segment_id="D0001-S000001",
            disposition=RepairDisposition.REPAIRED,
            original_translation="错误译文。",
            repaired_translation="你好，世界。",
            issues=[audit_issue()],
            attempts=1,
        )
        repaired = RepairedDocument(document=draft, repairs=[repair])
        prompt = build_repair_verification_prompt(
            source, repaired, ["D0001-S000001"]
        )
        assert "TRANSLATION A: 错误译文。" in prompt
        assert "TRANSLATION B: 你好，世界。" in prompt
        assert "a_acceptable and b_acceptable" in prompt
        assert "absolute publication readiness" in prompt
        assert "better than the alternative" in prompt
        assert "winner=neither" in prompt
        assert "Recheck the complete preferred translation" in prompt
        assert "PRECEDING SOURCE:" in prompt
        assert "FOLLOWING SOURCE:" in prompt
        assert "Suggested fix:" not in prompt
        assert "Do not rely on any prior diagnosis" in prompt
        assert "Do not score or rewrite" in prompt
        assert "Compare atomic subject-predicate facts explicitly" in prompt
        assert "must not become motion" in prompt
        assert "merely a plausible clarification" in prompt
        assert "same relation as in SOURCE" in prompt
        assert "source-owned italicized fictional" in prompt
        assert "elliptical purpose" in prompt
        assert "Apply glossary entries by sense" in prompt
        swapped = build_repair_verification_prompt(
            source, repaired, ["D0001-S000001"], candidate_first=True
        )
        assert "TRANSLATION A: 你好，世界。" in swapped
        assert "TRANSLATION B: 错误译文。" in swapped
        assert "CURRENT TRANSLATION" not in swapped
        assert "REPAIRED CANDIDATE" not in swapped
        with pytest.raises(ValueError, match="unknown"):
            build_repair_verification_prompt(source, repaired, ["missing"])
        result = RepairVerificationResult(
            verifications=[
                RepairVerification(
                    segment_id="D0001-S000001",
                    passed=True,
                    message="The corrected translation restores the source unit.",
                )
            ]
        )
        assert validate_repair_verification_scope(result, ["D0001-S000001"]) == result
        with pytest.raises(ValueError, match="exactly match"):
            validate_repair_verification_scope(result, ["D0001-S000002"])

        pairwise = PairwiseRepairVerificationResult(
            verifications=[
                PairwiseRepairVerification(
                    segment_id="D0001-S000001",
                    a_acceptable=False,
                    b_acceptable=True,
                    winner="b",
                    message="B preserves the source fact while A changes it.",
                )
            ]
        )
        validate_pairwise_repair_verification_scope(
            pairwise, ["D0001-S000001"]
        )
        mapped = map_pairwise_repair_verification(
            pairwise, candidate_first=False
        )
        assert mapped.verifications[0].passed
        assert not mapped.verifications[0].current_acceptable

    def test_repair_verification_batches_fit_complete_request_context(self):
        source = source_document(
            ["one " * 1_200, "two " * 1_200, "three " * 1_200, "four " * 1_200]
        )
        draft = translated_document(
            ["一" * 1_200, "二" * 1_200, "三" * 1_200, "四" * 1_200]
        )
        ids = [draft.segments[0].segment_id, draft.segments[3].segment_id]
        repaired = RepairedDocument(
            document=draft,
            repairs=[
                SegmentRepair(
                    segment_id=segment_id,
                    disposition=RepairDisposition.REPAIRED,
                    original_translation="旧译",
                    repaired_translation=draft.segments[index].translated_text,
                    issues=[audit_issue(segment_id)],
                    attempts=1,
                )
                for index, segment_id in ((0, ids[0]), (3, ids[1]))
            ],
        )

        batches = build_repair_verification_batches(
            source, repaired, ids, max_request_context=16_384
        )

        assert batches == [[ids[0]], [ids[1]]]

    def test_repair_verification_output_budget_scales_and_caps(self):
        assert repair_verification_output_token_limit(1) == 1_024
        assert repair_verification_output_token_limit(5) == 3_072
        assert repair_verification_output_token_limit(20) == 4_096
        with pytest.raises(ValueError, match="must be positive"):
            repair_verification_output_token_limit(0)

