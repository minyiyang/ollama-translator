import pytest

from book_agent.prose_rewrite import (
    ProseRewriteBatch,
    ProseRewriteDecision,
    prose_rewrite_candidate_reasons,
    protected_rewrite_changes,
    validate_prose_rewrite_batch,
)


def reasons(target: str, target_characters: int = 90) -> list[str]:
    return prose_rewrite_candidate_reasons("", target, target_characters=target_characters)


def decision(segment_id: str, action: str, text: str = "") -> ProseRewriteDecision:
    return ProseRewriteDecision(
        segment_id=segment_id, action=action, rewritten_text=text, reason="A concrete reason."
    )


class CandidateReasonTests:
    def test_plain_short_prose_gives_no_reason_to_rewrite(self):
        assert reasons("他走了。") == []

    def test_each_risk_is_named(self):
        assert reasons("他慢慢地走过了那条很长很长的街道。", target_characters=10) == ["long-prose"]
        assert reasons("他来了——她走了——天黑了——灯亮了。") == ["clause-density"]
        assert reasons("事实上他走了。") == ["translationese"]
        assert reasons("走吧走吧走吧。") == ["repetition"]
        assert reasons('他说"走"。') == ["typography"]
        # Straight quotes beside curly ones are left alone: the passage already uses the right marks.
        assert reasons('他说“走”，又说"来"。') == []

    def test_a_passage_can_have_several(self):
        found = reasons('事实上，他说"走"——走吧走吧走吧——她来了——天黑了，灯亮了，门开了。', target_characters=20)
        assert found == ["long-prose", "clause-density", "translationese", "repetition", "typography"]


class ProtectedChangeTests:
    def test_a_rewrite_that_keeps_every_signature_is_clean(self):
        assert protected_rewrite_changes("", "他不比Aster更高。", "他并不比Aster更高。") == []
        assert protected_rewrite_changes("", "他比Aster高。", "他比Aster高一些。") == []

    def test_each_kind_of_change_is_named(self):
        assert protected_rewrite_changes("", "他走了。", "他没走。") == ["negation/polarity"]
        assert protected_rewrite_changes("", "他走了。", "他必须走了。") == ["modality"]
        assert protected_rewrite_changes("", "他走了。", "他走得更快了。") == ["comparison"]
        assert protected_rewrite_changes("", "Aster走了。", "阿斯特走了。") == ["Latin name/term"]
        assert protected_rewrite_changes("", "aster走了。", "Aster走了。") == []  # case is not a change of name
        assert protected_rewrite_changes("", "他说：“走。”", "他说走。") == ["quotation boundary"]
        assert protected_rewrite_changes("", "他说：“走。”", "他说：“走。") == ["quotation boundary"]


class BatchValidationTests:
    def test_a_complete_ordered_batch_passes(self):
        batch = ProseRewriteBatch(decisions=[decision("S1", "keep"), decision("S2", "rewrite", "他走了。")])
        assert validate_prose_rewrite_batch(batch, ["S1", "S2"]) is batch

    def test_missing_extra_or_reordered_ids_are_refused(self):
        batch = ProseRewriteBatch(decisions=[decision("S2", "keep"), decision("S1", "keep")])
        for expected in (["S1", "S2"], ["S2"], ["S2", "S1", "S3"]):
            with pytest.raises(ValueError, match="must exactly match"):
                validate_prose_rewrite_batch(batch, expected)

    def test_a_decision_must_agree_with_its_text(self):
        with pytest.raises(ValueError, match="keep decision returned rewritten text: S1"):
            validate_prose_rewrite_batch(ProseRewriteBatch(decisions=[decision("S1", "keep", "他走了。")]), ["S1"])
        with pytest.raises(ValueError, match="rewrite decision is empty: S1"):
            validate_prose_rewrite_batch(ProseRewriteBatch(decisions=[decision("S1", "rewrite", "  ")]), ["S1"])
