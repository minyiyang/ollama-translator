
from book_agent.audit import (
    AuditRiskTag,
    audit_translated_document,
    reapply_quantity_adjudications,
)
from book_agent.config import AuditConfig
from book_agent.quantities import (
    QuantityAuditDecision,
    QuantityAuditResult,
    QuantityKind,
    QuantityMismatchKind,
    build_quantity_audit_prompt,
    compare_quantity_texts,
    extract_quantity_facts,
    validate_quantity_audit_scope,
)
from tests.test_audit import source_document, translated_document


class QuantityTests:
    def test_cross_language_units_and_exact_conversion_match(self):
        direct = compare_quantity_texts("five miles", "五英里")
        converted = compare_quantity_texts("one mile", "1609.344米")
        assert direct.status == "match"
        assert converted.status == "match"
        assert direct.source_facts[0].kind == QuantityKind.DISTANCE
        assert direct.source_facts[0].unit == "meter"

    def test_value_unit_identifier_and_addition_defects_are_concrete(self):
        value = compare_quantity_texts("five miles", "五公里")
        identifier = compare_quantity_texts("Use BVS-1.", "使用BVS-2。")
        addition = compare_quantity_texts("He waited outside.", "他在外面等了五年。")
        assert value.status == "mismatch"
        assert value.mismatches[0].kind == QuantityMismatchKind.VALUE_CHANGE
        assert identifier.status == "mismatch"
        assert addition.status == "mismatch"
        assert addition.mismatches[0].kind == QuantityMismatchKind.ADDED

    def test_ranges_percentages_and_approximation_are_typed(self):
        range_result = compare_quantity_texts("10 to 20 meters", "10至20米")
        percent_result = compare_quantity_texts("about 50 percent", "大约百分之五十")
        approximation_loss = compare_quantity_texts("about five miles", "五英里")
        assert range_result.status == "match"
        assert range_result.source_facts[0].lower == 10
        assert percent_result.status == "match"
        assert approximation_loss.status == "mismatch"
        assert approximation_loss.mismatches[0].kind == QuantityMismatchKind.APPROXIMATION_CHANGE

    def test_literary_relation_is_uncertain_instead_of_phrase_rule(self):
        result = compare_quantity_texts("It was half again as large.", "它大出一半。")
        assert result.status == "uncertain"
        assert "contextual" in result.reason

    def test_polarity_multiple_attachments_and_complements_are_not_silently_lost(self):
        polarity = compare_quantity_texts("He did not wait five years.", "他等了五年。")
        multiple = compare_quantity_texts(
            "Five scouts carried ten lamps.", "五名斥候带着十盏灯。"
        )
        complement = compare_quantity_texts("It was 0 percent useful.", "它百分百无用。")
        assert polarity.status == "uncertain"
        assert polarity.mismatches[0].kind == QuantityMismatchKind.POLARITY_CHANGE
        assert multiple.status == "uncertain"
        assert "attachment" in multiple.reason
        assert complement.status == "uncertain"

    def test_quantity_prompt_and_scope_are_grounded(self):
        comparison = compare_quantity_texts(
            "It was half again as large.", "它大出一半。", segment_id="S1"
        )
        prompt = build_quantity_audit_prompt(
            "S1", "It was half again as large.", "它大出一半。", comparison
        )
        assert "independently identify" in prompt
        result = QuantityAuditResult(
            decisions=[
                QuantityAuditDecision(
                    segment_id="S1",
                    status="match",
                    message="Both express a fifty-percent relative increase.",
                    source_quote="half again",
                    translation_quote="大出一半",
                    confidence=0.94,
                )
            ]
        )
        assert (validate_quantity_audit_scope(
                result, "S1", "It was half again as large.", "它大出一半。"
            )) == result

    def test_audit_switch_bypasses_legacy_phrase_gate_and_records_typed_facts(self):
        config = AuditConfig.model_validate(
            {"quantity": {"enabled": True, "mode": "enforce"}}
        )
        report = audit_translated_document(
            source_document(["They walked five miles."]),
            translated_document(["他们走了五公里。"]),
            config,
        )
        assert not report.passed
        assert report.quantity_candidate_ids == ["D0001-S000001"]
        assert report.quantity_comparisons[0].status == "mismatch"
        quantity_issue = next(
            item for item in report.issues if item.source == "quantity-deterministic"
        )
        assert AuditRiskTag.QUANTITY in quantity_issue.risk_tags

    def test_final_audit_reuses_unchanged_contextual_quantity_match(self):
        config = AuditConfig.model_validate(
            {"quantity": {"enabled": True, "mode": "enforce"}}
        )
        current = audit_translated_document(
            source_document(["They walked five miles."]),
            translated_document(["他们走了五公里。"]),
            config,
        )
        comparison = current.quantity_comparisons[0].model_copy(
            update={
                "status": "match",
                "reason": "Contextual review accepted the rendering.",
                "adjudicated": True,
            }
        )
        historical = current.model_copy(
            update={"quantity_comparisons": [comparison]}
        )

        reapplied = reapply_quantity_adjudications(current, historical)

        assert reapplied.quantity_comparisons[0].status == "match"
        assert reapplied.quantity_comparisons[0].adjudicated
        assert not any(item.source == "quantity-deterministic" for item in reapplied.issues)

    def test_shadow_mode_records_without_blocking(self):
        config = AuditConfig.model_validate(
            {"quantity": {"enabled": True, "mode": "shadow"}}
        )
        report = audit_translated_document(
            source_document(["They walked five miles."]),
            translated_document(["他们走了五公里。"]),
            config,
        )
        assert report.passed
        assert report.quantity_comparisons[0].status == "mismatch"
        assert not any(item.source.startswith("quantity-") for item in report.issues)

    def test_extractor_does_not_treat_unrelated_chinese_one_as_a_quantity(self):
        assert extract_quantity_facts("一旦她回来，我们就走。") == []

    def test_extractor_does_not_parse_point_nod_as_zero_heads(self):
        assert extract_quantity_facts("他点头表示同意。") == []

    def test_chinese_singular_classifier_is_ignored_when_source_uses_article(self):
        result = compare_quantity_texts("He entered a room.", "他走进一个房间。")
        assert result.status == "match"
        assert result.target_facts == []

    def test_chinese_singular_classifier_still_matches_explicit_one(self):
        result = compare_quantity_texts("He saw one guard.", "他看见一名守卫。")
        assert result.status == "match"
        assert result.target_facts[0].value == 1

    def test_pronominal_english_one_is_not_a_quantity(self):
        assert extract_quantity_facts("They turned to one another.") == []
        assert extract_quantity_facts("This one was broken.") == []

    def test_bare_chinese_heading_number_matches_english_heading(self):
        result = compare_quantity_texts("Twenty-Two", "二十二")
        assert result.status == "match"

    def test_implicit_one_unit_and_age_suffix_match(self):
        assert compare_quantity_texts("an inch", "一英寸").status == "match"
        assert compare_quantity_texts("twenty years his junior", "比他年轻二十岁").status == "match"

    def test_approximation_does_not_leak_from_neighboring_phrase(self):
        result = compare_quantity_texts(
            "many years passed. For ten years he fought.",
            "多年过去。此后十年，他一直战斗。",
        )
        assert result.status == "match"

    def test_bare_year_matches_chinese_year_suffix(self):
        assert compare_quantity_texts("Published 2010", "2010年出版").status == "match"

    def test_hyphenated_year_and_chinese_hour_classifier_match(self):
        assert compare_quantity_texts("the Twelve-Year War", "十二年战争").status == "match"
        assert compare_quantity_texts("four hours", "四个小时").status == "match"

    def test_standalone_dozen_and_score_have_nonzero_values(self):
        dozen = extract_quantity_facts("a dozen guards")
        score = extract_quantity_facts("a score of guards")
        assert dozen[0].value == 12
        assert score[0].value == 20

    def test_chinese_dozen_measure_matches_english_dozen_measure(self):
        result = compare_quantity_texts(
            "He passed at a distance of barely a dozen feet.",
            "他从不足一打英尺外经过。",
        )
        assert result.status == "match"

    def test_body_part_and_proximity_idioms_are_not_measurements(self):
        assert compare_quantity_texts("It felt like a foot.", "那感觉像是一只脚。\n").status == "match"
        assert (compare_quantity_texts(
                "She was within an inch of striking out.",
                "她几乎就要出手攻击。",
            ).status) == "match"
        assert (compare_quantity_texts(
                "Have you found me a foot that needs a new owner?",
                "你找到一只需要新主人的脚了吗？",
            ).status) == "match"
        assert (compare_quantity_texts(
                "It was another foot in the mire of politics.",
                "这是政治泥潭中的另一只脚。",
            ).status) == "match"

    def test_comparator_must_be_adjacent_to_quantity(self):
        assert (compare_quantity_texts(
                "practising her letters over and over, a dozen years gone",
                "十二年前一遍又一遍地练习写字",
            ).status) == "match"
        assert compare_quantity_texts("over five years", "五年").status != "match"

    def test_next_day_matches_chinese_second_day_idiom(self):
        assert compare_quantity_texts("He explained the next day.", "第二天，他解释道。").status == "match"
        assert compare_quantity_texts("They waited two days.", "他们等到第二天。").status != "match"

    def test_day_after_and_another_hour_match_chinese_rendering(self):
        comparison = compare_quantity_texts(
            "He came the day after and walked for another hour or so.",
            "他第二天来了，又走了一个小时左右。",
        )
        assert comparison.status == "uncertain"
        assert comparison.mismatches == []

    def test_number_word_match_does_not_start_with_conjunction(self):
        assert extract_quantity_facts("quiet and half-clad") == []

    def test_uncertain_candidate_always_retains_comparison(self):
        config = AuditConfig.model_validate(
            {"quantity": {"enabled": True, "mode": "shadow"}}
        )
        report = audit_translated_document(
            source_document(["It lasted twice as long."]),
            translated_document(["它持续了两倍长。"]),
            config,
        )
        assert report.quantity_candidate_ids == ["D0001-S000001"]
        comparisons = {item.segment_id: item for item in report.quantity_comparisons}
        assert "D0001-S000001" in comparisons
        assert comparisons["D0001-S000001"].status == "uncertain"

    def test_literary_quantity_forms_do_not_become_false_deterministic_mismatches(self, subtests):
        equivalent_cases = [
            ("halfway there", "已经走了一半"),
            ("five feet and a half", "五英尺半"),
            ("five centuries ago", "五百年前"),
            ("two score crossbows", "四十把弩"),
            ("hundreds of soldiers", "数百名士兵"),
            ("a couple of hundred men", "约两百人"),
        ]
        for source, target in equivalent_cases:
            with subtests.test(source=source):
                assert compare_quantity_texts(source, target).status != "mismatch"

    def test_typed_checker_still_blocks_clear_quantity_omission(self):
        result = compare_quantity_texts(
            "Hundreds of soldiers crossed five bridges.",
            "士兵们过了五座桥。",
        )
        assert result.status in {"mismatch", "uncertain"}
        assert result.mismatches

