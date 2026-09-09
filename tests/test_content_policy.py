from collections import Counter

from book_agent.content_policy import (
    SegmentKind,
    classify_segment,
    glossary_target_matches,
    number_tokens,
    numeric_content_matches,
    repair_preserves_numbers,
    repair_preserves_glossary,
)
from book_agent.languages import TranslationDirection
from book_agent.schemas import GlossaryCategory, GlossaryEntry


class ObfuscatedSegmentPolicyTests:
    def classify(self, source, target, glossary=()):
        return classify_segment(
            source, target, TranslationDirection.EN_TO_ZH, glossary
        )

    def test_numeric_and_structural_content_are_not_prose(self):
        assert self.classify("2047", "2047") == SegmentKind.LANGUAGE_NEUTRAL
        assert self.classify("* * *", "* * *") == SegmentKind.STRUCTURAL

    def test_written_percentages_normalize_across_languages(self):
        assert (numeric_content_matches(
                "About half a per cent of people react.",
                "大约百分之零点五的人会有反应。",
            ))
        assert (numeric_content_matches(
                "It is one hundred per cent secure and zero per cent useful.",
                "它百分之百安全，用处恰好为零。",
            ))
        assert (numeric_content_matches(
                "We are one hundred and ten per cent ourselves.",
                "我们是百分之一百一十的自己。",
            ))

    def test_article_ordinals_are_not_misread_as_fractions(self):
        assert (numeric_content_matches(
                "A third species makes up forty per cent of the mass.",
                "第三种物种占总质量的百分之四十。",
            ))
        assert (numeric_content_matches(
                "A fifth columnist disclosed the names.",
                "一名第五纵队分子供出了那些名字。",
            ))

    def test_qualified_code_and_approved_literal_are_protected(self):
        assert self.classify("QX-2047/A", "QX-2047/A") == SegmentKind.PROTECTED_IDENTIFIER
        glossary = [
            GlossaryEntry(
                english="ZORVAX",
                chinese="ZORVAX",
                category=GlossaryCategory.TECHNOLOGY,
            )
        ]
        assert self.classify("ZORVAX", "ZORVAX", glossary) == SegmentKind.PROTECTED_IDENTIFIER

    def test_obfuscated_publication_identifiers_are_protected(self, subtests):
        for value in (
            "ISBN 978-1-4028-9462-6",
            "QZ4821.R7V93 2046",
            "621'.43\u2014xy17",
            "qPub r7.3",
        ):
            with subtests.test(value=value):
                assert self.classify(value, value) == SegmentKind.PROTECTED_IDENTIFIER

    def test_publication_boilerplate_is_not_treated_as_literary_prose(self, subtests):
        for source in (
            "Copyright © 2026 Example Author",
            "All rights reserved. No part of this publication may be reproduced.",
            "First published in 2026 by Example Press",
        ):
            with subtests.test(source=source):
                assert self.classify(source, source) == SegmentKind.PROTECTED_IDENTIFIER

    def test_obfuscated_abbreviated_metadata_field_is_protected(self):
        assert (self.classify(
                "<I000> q. vm.</I000>",
                "<I000> \u9875. \u7ef4\u59c6.</I000>",
            )) == SegmentKind.PROTECTED_IDENTIFIER

    def test_unapproved_uppercase_word_and_mixed_prose_remain_prose(self):
        assert self.classify("VELNORA", "VELNORA") == SegmentKind.PROSE
        assert self.classify("Velnor counted 2047 qirks.", "Velnor counted 2047 qirks.") == SegmentKind.PROSE

    def test_number_facts_normalize_words_magnitudes_and_identifiers(self):
        source = "Sector Four, code QX4, held six pods at 900,000 velms."
        target = "第4区，代码QX4，装有六个舱，距离为90万维姆。"
        assert numeric_content_matches(source, target)

    def test_classifier_inserted_for_indefinite_noun_is_not_a_number_change(self):
        assert (numeric_content_matches(
                "A zorvak entered another chamber.",
                "一只佐瓦克进入了另一个舱室。",
            ))

    def test_obfuscated_number_word_change_is_detected(self):
        assert not numeric_content_matches("Five zorvaks arrived.", "六只佐瓦克抵达。")

    def test_obfuscated_word_number_in_technical_compound_is_not_a_fact(self):
        assert (numeric_content_matches(
                "The qel zero-flux grip opened.",
                "\u5947\u5c14\u96f6\u901a\u91cf\u63e1\u628a\u6253\u5f00\u4e86\u3002",
            ))

    def test_obfuscated_countdown_numbers_are_hard_facts(self):
        source = "The qel countdown began: Ten qirks. Eight, seven..."
        assert (numeric_content_matches(
                source,
                "\u5947\u5c14\u5012\u8ba1\u65f6\u5f00\u59cb\uff1a\u5341\u4e2a\u5355\u4f4d\u3002\u516b\uff0c\u4e03\u2026\u2026",
            ))
        assert not (numeric_content_matches(
                source,
                "\u5947\u5c14\u5012\u8ba1\u65f6\u5f00\u59cb\uff1a\u5341\u4e2a\u5355\u4f4d\u3002\u516b\u2026\u2026",
            ))

    def test_obfuscated_repeated_approximate_magnitude_is_one_fact(self):
        source = "Hundreds upon hundreds of qel vessels arrived."
        assert numeric_content_matches(source, "\u6570\u767e\u8258\u5947\u5c14\u98de\u8239\u62b5\u8fbe\u3002")
        assert not numeric_content_matches(source, "\u6570\u5343\u8258\u5947\u5c14\u98de\u8239\u62b5\u8fbe\u3002")

    def test_production_literary_magnitudes_and_ratio_increase_are_equivalent(self):
        assert (numeric_content_matches(
                "A hundred skirmishes followed across a thousand years.",
                "\u4e0a\u767e\u573a\u6df7\u6218\u5ef6\u7eed\u4e86\u5343\u5e74\u3002",
            ))
        assert (numeric_content_matches(
                "Half a world away after a thousand years.",
                "\u534a\u4e2a\u4e16\u754c\u4e4b\u5916\uff0c\u5343\u8f7d\u4e4b\u540e\u3002",
            ))
        assert (numeric_content_matches(
                "The improved bow had half again the range.",
                "\u6539\u826f\u540e\u7684\u5f13\u5c04\u7a0b\u589e\u52a0\u4e86\u767e\u5206\u4e4b\u4e94\u5341\u3002",
            ))
        assert (numeric_content_matches(
                "The city was half again Velnor's size.",
                "\u8fd9\u5ea7\u57ce\u6bd4\u97e6\u5c14\u8bfa\u57ce\u5927\u51fa\u4e00\u534a\u3002",
            ))

    def test_production_score_count_is_objective(self):
        assert (numeric_content_matches(
                "The prison held eight-score cells.",
                "\u76d1\u72f1\u91cc\u6709\u4e00\u767e\u516d\u5341\u95f4\u7262\u623f\u3002",
            ))
        assert not (numeric_content_matches(
                "The prison held eight-score cells.",
                "\u76d1\u72f1\u91cc\u6709\u516b\u767e\u95f4\u7262\u623f\u3002",
            ))

    def test_quantified_literary_durations_are_objective(self):
        assert (numeric_content_matches(
                "The foundry town began four centuries ago.",
                "\u8fd9\u5ea7\u94f8\u9020\u57ce\u9547\u59cb\u4e8e\u56db\u767e\u5e74\u524d\u3002",
            ))
        assert not (numeric_content_matches(
                "The foundry town began four centuries ago.",
                "\u8fd9\u5ea7\u94f8\u9020\u57ce\u9547\u59cb\u4e8e\u4e00\u767e\u5e74\u524d\u3002",
            ))
        assert (numeric_content_matches(
                "The archive covers three millennia and two decades.",
                "\u6863\u6848\u6db5\u76d6\u4e09\u5343\u5e74\u53c8\u4e8c\u5341\u5e74\u3002",
            ))

    def test_production_percent_polarity_complements_are_equivalent(self):
        assert (numeric_content_matches(
                "Zero per cent of the labour was useful.",
                "\u8fd9\u4e9b\u52b3\u529b\u767e\u5206\u4e4b\u767e\u6beb\u65e0\u7528\u5904\u3002",
            ))
        assert (numeric_content_matches(
                "One hundred per cent of the treatment was ineffective.",
                "\u8fd9\u79cd\u6cbb\u7597\u767e\u5206\u4e4b\u96f6\u6709\u6548\u3002",
            ))
        assert not (numeric_content_matches(
                "Zero per cent of the labour was useful.",
                "\u8fd9\u4e9b\u52b3\u529b\u767e\u5206\u4e4b\u767e\u5f88\u6709\u7528\u3002",
            ))

    def test_book_two_literary_number_forms_are_equivalent(self, subtests):
        equivalents = (
            ("The door stopped half open.", "\u95e8\u5f00\u4e86\u4e00\u534a\u4fbf\u505c\u4f4f\u4e86\u3002"),
            ("The wounded were only half loaded.", "\u4f24\u5458\u624d\u88c5\u8f7d\u4e86\u4e00\u534a\u3002"),
            (
                "Qelmar is half again as big as most zorvak states.",
                "\u51ef\u5c14\u739b\u6bd4\u5927\u591a\u6570\u4f50\u74e6\u514b\u57ce\u90a6\u5927\u4e0a\u4e00\u534a\u3002",
            ),
            (
                "These two halves are not two halves; each shares with the other.",
                "\u8fd9\u4e24\u534a\u6839\u672c\u4e0d\u662f\u4e24\u534a\uff1b\u6bcf\u4e00\u534a\u90fd\u4e0e\u53e6\u4e00\u534a\u5206\u4eab\u3002",
            ),
            ("Six and a half hundred soldiers waited.", "\u516d\u767e\u4e94\u5341\u540d\u58eb\u5175\u7b49\u5f85\u7740\u3002"),
            (
                "One unit of a hundred men was abruptly half its number down.",
                "\u4e00\u4e2a\u767e\u4eba\u5c0f\u961f\u7a81\u7136\u6298\u635f\u4e86\u4e00\u534a\u3002",
            ),
            ("Thousands, really.", "\u786e\u5b9e\u6210\u5343\u4e0a\u4e07\u3002"),
            ("Stones laid centuries before rose four feet.", "\u6570\u767e\u5e74\u524d\u780c\u4e0b\u7684\u77f3\u5757\u9ad8\u56db\u82f1\u5c3a\u3002"),
            (
                "We will slay hundreds of them, and tens of hundreds.",
                "\u6211\u4eec\u4f1a\u6740\u4ed6\u4eec\u51e0\u767e\uff0c\u751a\u81f3\u51e0\u5343\u3002",
            ),
            ("The box cracked in half.", "\u7bb1\u5b50\u88c2\u6210\u4e24\u534a\u3002"),
            ("She cut the table in two.", "\u5979\u5c06\u684c\u5b50\u5288\u6210\u4e24\u534a\u3002"),
        )
        for source, target in equivalents:
            with subtests.test(source=source):
                assert numeric_content_matches(source, target)

    def test_book_three_literary_number_forms_are_equivalent(self):
        assert (numeric_content_matches(
                "Story-sequences of a thousand images covered the wall.",
                "墙上刻着由千幅图像组成的叙事序列。",
            ))
        assert (numeric_content_matches(
                "That would mean about half as much in Velnor Centrals.",
                "换算成韦尔诺中央币，数额大约只有一半。",
            ))
        assert not (numeric_content_matches(
                "That would mean about half as much in Velnor Centrals.",
                "换算成韦尔诺中央币，数额大约是两倍。",
            ))
        assert (numeric_content_matches(
                "The thing had scissored her in half.",
                "那东西已经把她拦腰剪成两截。",
            ))

    def test_obfuscated_demonym_glossary_allows_contextual_stem(self):
        assert (glossary_target_matches(
                "\u91c7\u7528\u6cfd\u5c14\u5c3c\u6807\u51c6\u3002",
                "\u6cfd\u5c14\u5c3c\u4eba",
            ))
        assert not (glossary_target_matches(
                "\u91c7\u7528\u5176\u4ed6\u6807\u51c6\u3002",
                "\u6cfd\u5c14\u5c3c\u4eba",
            ))

    def test_number_facts_keep_identifier_digits_scoped(self):
        assert number_tokens("Code QX-2047/A.") != number_tokens("代码QX-2048/A。")

    def test_obfuscated_identifier_forms_and_attached_unit_are_equivalent(self):
        assert numeric_content_matches("pre-QX4 records", "QX4之前的记录")
        assert numeric_content_matches("GP #7 at 0.002zor", "GP7以0.002zor运行")
        assert numeric_content_matches("Z-17 isotope", "锆17同位素")

    def test_obfuscated_marker_boundaries_do_not_create_identifiers(self):
        source = "Zorvak Works<I000></I000>175 Ninth Avenue<I001></I001>QX 40210"
        target = "佐瓦克工坊<I000></I000>第九大道175号<I001></I001>QX 40210"
        assert numeric_content_matches(source, target)

    def test_obfuscated_dates_and_scaled_magnitudes_are_equivalent(self):
        assert numeric_content_matches("First release: September 2048", "第一版：2048年9月")
        assert (numeric_content_matches(
                "The array tracked 23 billion motes from 174 billion velms.",
                "阵列从1740亿维姆外追踪了230亿个微粒。",
            ))

    def test_obfuscated_fractions_and_word_identifiers_are_equivalent(self):
        assert (numeric_content_matches(
                "Repeat: seven-tenths cee; beacon QZ Four remains active.",
                "重复：0.7c；信标QZ4仍在运行。",
            ))
        assert (numeric_content_matches(
                "The gate stood a quarter kilometer away.",
                "那道门位于四分之一公里外。",
            ))
        assert (numeric_content_matches(
                "The engine reached eighty percent output.",
                "引擎达到了百分之八十的输出功率。",
            ))

    def test_obfuscated_british_word_percent_is_objective(self):
        assert (numeric_content_matches(
                "The qir reserve fell to four per cent.",
                "\u5947\u5c14\u50a8\u5907\u964d\u81f3\u767e\u5206\u4e4b\u56db\u3002",
            ))
        assert not (numeric_content_matches(
                "The qir reserve fell to four per cent.",
                "\u5947\u5c14\u50a8\u5907\u964d\u81f3\u767e\u5206\u4e4b\u4e94\u3002",
            ))
        assert (numeric_content_matches(
                "The qir reserve was 2.1 per cent.",
                "\u5947\u5c14\u50a8\u5907\u4e3a2.1%\u3002",
            ))

    def test_chinese_decimal_percent_and_contextual_zero_are_objective(self):
        assert (numeric_content_matches(
                "The work was 99.9 per cent complete.",
                "工作完成了百分之九十九点九。",
            ))
        assert (numeric_content_matches(
                "Exactly zero per cent survived.",
                "存活率恰好为零。",
            ))
        assert (numeric_content_matches(
                "The work was 99.9-per-cent-recurring toil.",
                "其中百分之九十九点九都是循环往复的苦役。",
            ))

    def test_cross_language_percent_range_preserves_endpoint_order(self):
        source = "The atmosphere contained seventeen to nineteen per cent oxygen."
        assert numeric_content_matches(source, "大气含氧量为百分之十七到十九。")
        assert not numeric_content_matches(source, "大气含氧量为百分之十九到十七。")

    def test_compound_english_magnitude_multiplies_scales(self):
        assert (numeric_content_matches(
                "A million billion generations passed.",
                "一千万亿代过去了。",
            ))

    def test_chinese_wanyi_idiom_is_not_a_number_fact(self):
        assert number_tokens("以防万一。") == Counter()

    def test_obfuscated_leading_decimal_and_contextual_half_again(self):
        assert (numeric_content_matches(
                "The seal closed within .25 of a second.",
                "\u5bc6\u5c01\u4f1a\u57280.25\u79d2\u5185\u95ed\u5408\u3002",
            ))
        assert (numeric_content_matches(
                "It shortened by a third, then by half again.",
                "\u5b83\u5148\u7f29\u77ed\u4e09\u5206\u4e4b\u4e00\uff0c\u7136\u540e\u53c8\u7f29\u77ed\u4e00\u534a\u3002",
            ))
        assert (numeric_content_matches(
                "Half the qir crew remained.",
                "\u534a\u6570\u5947\u5c14\u8239\u5458\u7559\u4e86\u4e0b\u6765\u3002",
            ))

    def test_obfuscated_half_magnitude_and_bare_chinese_magnitude_are_equivalent(self):
        assert (numeric_content_matches(
                "Half-trillion qirks occupied the ring.",
                "五千亿只奇尔克占据了环带。",
            ))
        assert (numeric_content_matches(
                "A trillion zorvaks departed.",
                "万亿只佐瓦克离开了。",
            ))

    def test_obfuscated_half_suffix_and_half_a_magnitude_are_equivalent(self):
        assert (numeric_content_matches(
                "The qir panel was a foot and a half wide.",
                "\u5947\u5c14\u9762\u677f\u5bbd\u4e00\u82f1\u5c3a\u534a\u3002",
            ))
        assert (numeric_content_matches(
                "The qir gate was half a million velms away.",
                "\u5947\u5c14\u95e8\u8ddd\u79bb\u4e94\u5341\u4e07\u7ef4\u59c6\u3002",
            ))

    def test_obfuscated_mixed_half_and_written_magnitudes_are_objective(self):
        assert (numeric_content_matches(
                "One and a half trillion qirks crossed the veil.",
                "\u4e00\u4e07\u4e94\u5343\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u5e37\u5e55\u3002",
            ))
        assert not (numeric_content_matches(
                "Twelve hundred billion qirks crossed the veil.",
                "\u4e00\u5343\u4e8c\u767e\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u5e37\u5e55\u3002",
            ))
        assert (numeric_content_matches(
                "Twelve hundred billion qirks crossed the veil.",
                "\u4e00\u4e07\u4e8c\u5343\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u5e37\u5e55\u3002",
            ))

    def test_obfuscated_approximate_magnitude_matches_target_scale(self):
        assert (numeric_content_matches(
                "The qir plume stretched tens of millions of velms.",
                "\u5947\u5c14\u55b7\u6d41\u5ef6\u4f38\u4e86\u6570\u5343\u4e07\u7ef4\u59c6\u3002",
            ))
        assert not (numeric_content_matches(
                "The qir plume stretched tens of millions of velms.",
                "\u5947\u5c14\u55b7\u6d41\u5ef6\u4f38\u4e86\u6570\u767e\u4e07\u7ef4\u59c6\u3002",
            ))
        assert (numeric_content_matches(
                "Tens of thousands of qirks crossed the field.",
                "\u6570\u4ee5\u4e07\u8ba1\u7684\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u7530\u91ce\u3002",
            ))

    def test_obfuscated_bare_plural_magnitude_matches_target_scale(self):
        assert (numeric_content_matches(
                "The qir journey took millions of years.",
                "\u5947\u5c14\u65c5\u7a0b\u8017\u65f6\u6570\u767e\u4e07\u5e74\u3002",
            ))
        assert (numeric_content_matches(
                "Thousands of qirks crossed the field.",
                "\u6570\u5343\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u7530\u91ce\u3002",
            ))
        assert not (numeric_content_matches(
                "Thousands of qirks crossed the field.",
                "\u6570\u767e\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u7530\u91ce\u3002",
            ))

    def test_obfuscated_hundreds_include_trailing_values_and_unknown_units(self):
        assert (numeric_content_matches(
                "The qir cycle lasted one hundred sixty-five drels.",
                "\u5947\u5c14\u5468\u671f\u6301\u7eed\u4e86\u4e00\u767e\u516d\u5341\u4e94\u5fb7\u96f7\u3002",
            ))
        assert (numeric_content_matches(
                "The qir event happened thirty-five hundred falans ago.",
                "\u5947\u5c14\u4e8b\u4ef6\u53d1\u751f\u5728\u4e09\u5343\u4e94\u767e\u6cd5\u5170\u524d\u3002",
            ))
        assert (numeric_content_matches(
                "The four hundred and first qir gate opened.",
                "\u7b2c\u56db\u767e\u96f6\u4e00\u9053\u5947\u5c14\u95e8\u5f00\u542f\u4e86\u3002",
            ))

    def test_obfuscated_possessive_half_is_an_objective_fraction(self):
        assert (numeric_content_matches(
                "The qir grass reached half their height.",
                "\u5947\u5c14\u8349\u957f\u5230\u4e86\u5b83\u4eec\u8eab\u9ad8\u7684\u4e00\u534a\u3002",
            ))
        assert not (numeric_content_matches(
                "The qir signal arrived in six and a half minutes.",
                "\u5947\u5c14\u4fe1\u53f7\u5728\u516d\u5206\u949f\u540e\u5230\u8fbe\u3002",
            ))
        assert (numeric_content_matches(
                "The qir signal arrived in six and a half minutes.",
                "\u5947\u5c14\u4fe1\u53f7\u5728\u516d\u5206\u534a\u949f\u540e\u5230\u8fbe\u3002",
            ))

    def test_obfuscated_half_forms_avoid_lexical_false_positives(self):
        assert (numeric_content_matches(
                "A half-buried qir gate opened.",
                "\u4e00\u5ea7\u90e8\u5206\u63a9\u57cb\u7684\u5947\u5c14\u95e8\u5f00\u4e86\u3002",
            ))
        assert (numeric_content_matches(
                "A qir road half covered by dust remained.",
                "\u4e00\u6761\u88ab\u5c18\u571f\u90e8\u5206\u8986\u76d6\u7684\u5947\u5c14\u8def\u8fd8\u5728\u3002",
            ))
        assert (numeric_content_matches(
                "Half the qir core failed after four and a half years.",
                "\u534a\u622a\u5947\u5c14\u6838\u5fc3\u5728\u56db\u5e74\u534a\u540e\u5931\u6548\u3002",
            ))
        assert (numeric_content_matches(
                "The qir trip took half an hour each way.",
                "\u5947\u5c14\u65c5\u7a0b\u6765\u56de\u5404\u534a\u5c0f\u65f6\u3002",
            ))
        assert (numeric_content_matches(
                "The qir walker completed half a circuit.",
                "\u5947\u5c14\u884c\u8005\u5b8c\u6210\u4e86\u534a\u5708\u3002",
            ))

    def test_obfuscated_name_magnitude_character_is_not_a_number(self):
        assert (numeric_content_matches(
                "Qelmar went first.",
                "\u4e07\u5fb7\u5c14\u5148\u8d70\u4e86\u3002",
            ))

    def test_obfuscated_word_clock_time_matches_chinese_period_time(self):
        assert (numeric_content_matches(
                "The qir shuttle left at sixteen twenty this afternoon.",
                "\u5947\u5c14\u98de\u8239\u4eca\u5929\u4e0b\u5348\u56db\u70b9\u4e8c\u5341\u79bb\u5f00\u3002",
            ))

    def test_obfuscated_literary_number_equivalences_are_canonical(self, subtests):
        equivalents = (
            (
                "Nearly half a millennium earlier, the qir gate opened.",
                "\u8fd1\u4e94\u767e\u5e74\u524d\uff0c\u5947\u5c14\u95e8\u5f00\u542f\u4e86\u3002",
            ),
            (
                "The qir stream reached four-fifths light speed.",
                "\u5947\u5c14\u6d41\u8fbe\u5230\u4e86\u4e94\u5206\u4e4b\u56db\u5149\u901f\u3002",
            ),
            (
                "The qir stream reached two-fifths light speed.",
                "\u5947\u5c14\u6d41\u8fbe\u5230\u4e86\u4e94\u5206\u4e4b\u4e8c\u5149\u901f\u3002",
            ),
            (
                "The qirium-3 reserve was stable.",
                "\u5947\u5c14\u5143\u7d20-3\u50a8\u91cf\u7a33\u5b9a\u3002",
            ),
            (
                "Gravity was half again the usual level.",
                "\u91cd\u529b\u662f\u901a\u5e38\u6c34\u5e73\u76841.5\u500d\u3002",
            ),
            (
                "The first disc fell hundreds of velms; so did the second, then a third.",
                "\u7b2c\u4e00\u4e2a\u5706\u76d8\u6389\u4e0b\u4e86\u51e0\u767e\u7ef4\u59c6\uff1b\u7b2c\u4e8c\u4e2a\u4e5f\u662f\uff0c\u7136\u540e\u662f\u7b2c\u4e09\u4e2a\u3002",
            ),
            (
                "A Zorvak Fabrication #4 shell and a ZF #4 shell spanned a thousand velms.",
                "\u4e00\u4e2a\u4f50\u74e6\u514b\u5236\u90204\u53f7\u58f3\u4f53\u548c\u4e00\u4e2aZF 4\u53f7\u58f3\u4f53\u8de8\u8d8a\u4e86\u4e00\u5343\u7ef4\u59c6\u3002",
            ),
            (
                "Vessel Twenty-three waited a thousand miles away.",
                "23\u53f7\u8230\u5728\u5343\u91cc\u4e4b\u5916\u7b49\u5f85\u3002",
            ),
            (
                "A couple hundred qir engines remained.",
                "\u8fd8\u5269\u4e0b\u51e0\u767e\u53f0\u5947\u5c14\u5f15\u64ce\u3002",
            ),
            (
                "The qir tanks were two-thirds full.",
                "\u5947\u5c14\u50a8\u7f50\u6709\u4e09\u5206\u4e4b\u4e8c\u6ee1\u3002",
            ),
        )
        for source, target in equivalents:
            with subtests.test(source=source):
                assert numeric_content_matches(source, target)

    def test_obfuscated_fraction_of_magnitude_keeps_true_mismatch_blocking(self):
        assert (numeric_content_matches(
                "The qir probe moved a third of a million velms.",
                "\u5947\u5c14\u63a2\u6d4b\u5668\u79fb\u52a8\u4e86\u4e00\u767e\u4e07\u7ef4\u59c6\u7684\u4e09\u5206\u4e4b\u4e00\u3002",
            ))
        assert not (numeric_content_matches(
                "The qir probe moved a third of a million velms.",
                "\u5947\u5c14\u63a2\u6d4b\u5668\u79fb\u52a8\u4e86\u4e09\u5341\u4e07\u7ef4\u59c6\u3002",
            ))

    def test_obfuscated_compound_tenth_is_not_plain_half_unit(self):
        source = "The qir cell lasts half a cycle-tenth."
        assert (numeric_content_matches(
                source,
                "\u5947\u5c14\u7535\u6c60\u80fd\u7ef4\u6301\u4e8c\u5341\u5206\u4e4b\u4e00\u4e2a\u5468\u671f\u3002",
            ))
        assert not (numeric_content_matches(
                source,
                "\u5947\u5c14\u7535\u6c60\u80fd\u7ef4\u6301\u534a\u4e2a\u5468\u671f\u3002",
            ))

    def test_correcting_duration_scale_is_not_blocked_by_old_bad_number(self):
        source = "More than half a millennium passed after 2147."
        accepted = "2147\u5e74\u4e4b\u540e\u8fc7\u4e86\u534a\u4e2a\u591a\u4e16\u7eaa\u3002"
        corrected = "2147\u5e74\u4e4b\u540e\u8fc7\u4e86\u4e94\u767e\u591a\u5e74\u3002"
        assert repair_preserves_numbers(source, accepted, corrected)

    def test_implied_target_one_does_not_hide_changed_source_fact(self):
        assert (numeric_content_matches(
                "Two qirks carried 70 zorvaks.",
                "两只奇尔克携带70个佐瓦克，并保持1G重力。",
            ))
        assert not numeric_content_matches("Twelve qirks arrived.", "13只奇尔克抵达。")

    def test_existing_equivalent_number_form_does_not_block_local_repair(self):
        source = "Five orbs circled at 900,000 velms."
        accepted = "五颗球体在90万维姆处环绕。"
        candidate = "五颗天体在90万维姆处运行。"
        assert repair_preserves_numbers(source, accepted, candidate)
        assert not repair_preserves_numbers(source, accepted, "五颗天体在91万维姆处运行。")

    def test_obfuscated_classifier_change_does_not_block_local_repair(self):
        source = "One qir pilot raised one hand beside a zorvak."
        accepted = "\u4e00\u540d\u5947\u5c14\u98de\u884c\u5458\u5728\u4f50\u74e6\u514b\u65c1\u4e3e\u8d77\u4e00\u53ea\u624b\u3002"
        candidate = "\u4e00\u4f4d\u5947\u5c14\u98de\u884c\u5458\u5728\u4e00\u53ea\u4f50\u74e6\u514b\u65c1\u4e3e\u8d77\u4e86\u624b\u3002"
        assert repair_preserves_numbers(source, accepted, candidate)

    def test_obfuscated_word_magnitude_can_correct_preexisting_numeric_error(self):
        source = "Ten billion qirks crossed the gate."
        accepted = "\u5341\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u95e8\u3002"
        corrected = "\u4e00\u767e\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u95e8\u3002"
        unrelated = "\u4e8c\u767e\u4ebf\u53ea\u5947\u5c14\u514b\u7a7f\u8fc7\u4e86\u95e8\u3002"
        assert repair_preserves_numbers(source, accepted, corrected)
        assert not repair_preserves_numbers(source, accepted, unrelated)

    def test_obfuscated_approved_term_is_locked_during_repair(self):
        glossary = [
            GlossaryEntry(
                english="qelmin gate",
                chinese="凯尔门",
                category=GlossaryCategory.TECHNOLOGY,
            )
        ]
        assert (repair_preserves_glossary(
                "The qelmin gate opened.",
                "凯尔门开启了。",
                "凯尔门缓缓开启。",
                TranslationDirection.EN_TO_ZH,
                glossary,
            ))
        assert not (repair_preserves_glossary(
                "The qelmin gate opened.",
                "凯尔门开启了。",
                "传送门缓缓开启。",
                TranslationDirection.EN_TO_ZH,
                glossary,
            ))

