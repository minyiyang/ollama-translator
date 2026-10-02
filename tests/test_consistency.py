"""Phase 1 of docs/BOOK_CONSISTENCY.md: detectors, the audit_consistency stage, and its plumbing."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from book_agent.audit import AuditCategory, AuditIssue, AuditSeverity, SemanticAuditResult
from book_agent.config import AppConfig
from book_agent.consistency import (
    CONSISTENCY_SOURCE,
    BookSegment,
    ConsistencySettings,
    book_consistency_issues,
    convention_issues,
    repeated_line_issues,
)
from book_agent.pipeline_state import (
    ADDED_AFTER_REPAIR_MESSAGE,
    WorkflowStage,
    initialize_pipeline_stages,
)
from book_agent.state import StageStatus, connect_state, get_stage_status, set_stage_status

SETTINGS = ConsistencySettings()


def _seg(segment_id: str, source: str, target: str, **flags) -> BookSegment:
    return BookSegment(
        document_id=segment_id.split("-")[0],
        order=int(segment_id[1:5]),
        segment_id=segment_id,
        source=source,
        target=target,
        **flags,
    )


def _by_segment(issues):
    return {issue.segment_id: issue for issue in issues}


# -- detectors ------------------------------------------------------------------------


class RepeatedLineTests:
    def test_majority_rendering_is_the_reference(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "Off with her head, now!", "砍掉她的头，现在！"),
                _seg("D0002-S000001", "Off with her head, now!", "砍掉它的头，现在！"),
                _seg("D0003-S000001", "Off with her head, now!", "砍掉她的头，现在！"),
            ],
            SETTINGS,
        )
        assert list(_by_segment(issues)) == ["D0002-S000001"]
        issue = issues[0]
        assert issue.category is AuditCategory.CONSISTENCY
        assert issue.source == CONSISTENCY_SOURCE
        assert issue.severity is AuditSeverity.MEDIUM
        assert "D0001-S000001, D0003-S000001" in issue.message
        assert issue.suggested_fix.endswith("砍掉她的头，现在！")

    def test_first_in_reading_order_breaks_a_tie(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "Oh, I beg your pardon!", "哦，请您原谅！"),
                _seg("D0002-S000001", "Oh, I beg your pardon!", "哦，请原谅！"),
            ],
            SETTINGS,
        )
        assert list(_by_segment(issues)) == ["D0002-S000001"]

    def test_a_human_edit_beats_the_majority(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "Off with her head, now!", "砍掉她的头，现在！"),
                _seg("D0002-S000001", "Off with her head, now!", "砍掉她的头，现在！"),
                _seg("D0003-S000001", "Off with her head, now!", "砍了她的脑袋，现在！", edited=True),
            ],
            SETTINGS,
        )
        assert sorted(_by_segment(issues)) == ["D0001-S000001", "D0002-S000001"]
        assert all("D0003-S000001" in issue.suggested_fix for issue in issues)

    def test_a_verified_repair_breaks_a_tie_but_loses_to_a_human_edit(self):
        lines = [
            _seg("D0001-S000001", "Off with her head, now!", "砍掉她的头，现在！"),
            _seg("D0002-S000001", "Off with her head, now!", "砍了她的头，现在！", repaired=True),
        ]
        assert list(_by_segment(repeated_line_issues(lines, SETTINGS))) == ["D0001-S000001"]
        lines[0] = _seg("D0001-S000001", "Off with her head, now!", "砍掉她的头，现在！", edited=True)
        assert list(_by_segment(repeated_line_issues(lines, SETTINGS))) == ["D0002-S000001"]

    def test_one_repaired_occurrence_does_not_outvote_the_majority(self):
        """A repair made for another finding must not flag every occurrence that agrees."""
        lines = [
            _seg(f"D{index:04d}-S000001", "Off with her head, now!", "砍掉她的头，现在！")
            for index in range(1, 10)
        ]
        lines.append(
            _seg("D0010-S000001", "Off with her head, now!", "砍了她的头，现在！", repaired=True)
        )
        issues = repeated_line_issues(lines, SETTINGS)
        assert list(_by_segment(issues)) == ["D0010-S000001"]
        assert issues[0].suggested_fix.endswith("砍掉她的头，现在！")

    def test_disagreeing_human_edits_are_both_flagged_with_no_fix(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "Off with her head, now!", "砍掉她的头！", edited=True),
                _seg("D0002-S000001", "Off with her head, now!", "砍了她的脑袋！", edited=True),
                _seg("D0003-S000001", "Off with her head, now!", "砍掉她的头！"),
            ],
            SETTINGS,
        )
        assert sorted(_by_segment(issues)) == ["D0001-S000001", "D0002-S000001"]
        assert all(issue.suggested_fix == "" and "disagree" in issue.message for issue in issues)

    def test_clearly_different_wording_is_low_and_punctuation_is_ignored(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "Come on, then, all of you!", "那就来吧，你们大家！"),
                _seg("D0002-S000001", "Come on, then, all of you!", "走吧！"),
                _seg("D0003-S000001", "Come on, then, all of you!", "那就来吧, 你们大家!"),
            ],
            SETTINGS,
        )
        assert [(i.segment_id, i.severity) for i in issues] == [("D0002-S000001", AuditSeverity.LOW)]

    def test_you_and_polite_you_are_not_drift(self):
        """你/您 follows who speaks to whom (decision 7.6); English "you" does not decide it."""
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "“How do you know that, Watson?”", "“你怎么知道的，华生？”"),
                _seg("D0002-S000001", "“How do you know that, Watson?”", "“您怎么知道的，华生？”"),
            ],
            SETTINGS,
        )
        assert issues == []

    def test_short_replies_are_listed_never_queued(self):
        """The Return of Sherlock Holmes: "Certainly not." answers different questions."""
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "“Certainly not, sir.”", "“当然不会，先生。”"),
                _seg("D0002-S000001", "“Certainly not, sir.”", "“当然不会，先生。”"),
                _seg("D0003-S000001", "“Certainly not, sir.”", "“当然不知道，先生。”"),
            ],
            SETTINGS,
        )
        assert [(i.segment_id, i.severity) for i in issues] == [("D0003-S000001", AuditSeverity.LOW)]
        # Four words is a refrain, not a reply: still drift.
        refrain = repeated_line_issues(
            [
                _seg("D0001-S000001", "“Off with his head!” she said.", "“砍掉他的头！”她说。"),
                _seg("D0002-S000001", "“Off with his head!” she cried.", "“砍掉他的头！”她喊道。"),
                _seg("D0003-S000001", "“Off with his head!” she roared.", "“砍掉它的头！”她吼道。"),
            ],
            SETTINGS,
        )
        assert [(i.segment_id, i.severity) for i in refrain] == [("D0003-S000001", AuditSeverity.MEDIUM)]

    def test_quoted_speech_is_paired_in_order_inside_longer_segments(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "“Off with his head!” the Queen shouted.", "“砍掉他的头！”王后喊道。"),
                _seg("D0002-S000001", "She roared: “Off with his head!”", "她吼道：“砍掉它的头！”"),
                _seg("D0003-S000001", "“Off with his head!” she said again.", "“砍掉他的头！”她又说。"),
            ],
            SETTINGS,
        )
        assert list(_by_segment(issues)) == ["D0002-S000001"]
        assert issues[0].message.startswith('Repeated quoted line "Off with his head"')

    def test_quotes_are_not_paired_when_the_counts_differ(self):
        issues = repeated_line_issues(
            [
                _seg("D0001-S000001", "“Off with his head!” she said.", "“砍掉他的头！”她说。"),
                _seg("D0002-S000001", "“Off with his head!” “Now!”", "“砍掉它的头！现在！”"),
            ],
            SETTINGS,
        )
        assert issues == []

    def test_short_lines_and_quoted_speech_can_be_left_out(self):
        short = [_seg("D0001-S000001", "Yes, sir.", "是的。"), _seg("D0002-S000001", "Yes, sir.", "对。")]
        assert repeated_line_issues(short, SETTINGS) == []
        quoted = [
            _seg("D0001-S000001", "“Off with his head!” she said.", "“砍掉他的头！”她说。"),
            _seg("D0002-S000001", "He cried “Off with his head!”", "他喊“砍掉它的头！”"),
        ]
        assert repeated_line_issues(quoted, ConsistencySettings(quoted_speech=False)) == []


class ConventionTests:
    def test_single_dash_and_straight_quotes_against_the_book_majority(self):
        segments = [
            _seg("D0001-S000001", "x", "他停下——然后走了。"),
            _seg("D0001-S000002", "x", "她说：“好。”"),
            _seg("D0001-S000003", "x", "他唱道：—"),
            _seg("D0001-S000004", "x", '他说"好"。'),
        ]
        issues = _by_segment(convention_issues(segments, SETTINGS))
        assert sorted(issues) == ["D0001-S000003", "D0001-S000004"]
        assert "——" in issues["D0001-S000003"].suggested_fix

    def test_no_finding_when_the_book_mostly_uses_the_other_style(self):
        segments = [
            _seg("D0001-S000001", "x", "他唱道：—"),
            _seg("D0001-S000002", "x", "她说—好。"),
            _seg("D0001-S000003", "x", "他停下——然后走了。"),
        ]
        assert convention_issues(segments, SETTINGS) == []

    def test_english_targets_are_not_checked(self):
        segments = [_seg("D0001-S000001", "x", 'He said "fine" -- then left.')]
        assert convention_issues(segments, ConsistencySettings(target_language="en")) == []

    def test_book_consistency_issues_can_skip_conventions(self):
        segments = [_seg("D0001-S000001", "x", "他停下——然后。"), _seg("D0001-S000002", "x", "他唱道：—")]
        assert book_consistency_issues(segments, SETTINGS)
        assert book_consistency_issues(segments, ConsistencySettings(conventions=False)) == []

    def test_book_consistency_issues_leave_human_edits_to_the_edit_check(self):
        """Repair works on the pipeline text, so a finding on a human edit has nothing to fix."""
        segments = [
            _seg("D0001-S000001", "x", "他停下——然后。"),
            _seg("D0001-S000002", "x", "他唱道：—", edited=True),
            _seg("D0001-S000003", "x", '他说"好"，她说：“好。”', edited=True),
            _seg("D0001-S000004", "x", "她唱道：—"),
            _seg("D0001-S000005", "x", "她停下——然后。"),
        ]
        assert sorted(_by_segment(convention_issues(segments, SETTINGS))) == [
            "D0001-S000002", "D0001-S000003", "D0001-S000004",
        ]
        assert list(_by_segment(book_consistency_issues(segments, SETTINGS))) == ["D0001-S000004"]


def test_the_semantic_auditor_cannot_emit_consistency_findings():
    schema = SemanticAuditResult.model_json_schema()
    categories = schema["$defs"]["AuditCategory"]["enum"]
    assert "consistency" not in categories and "mistranslation" in categories


# -- existing jobs ---------------------------------------------------------------------


class InitializePipelineStagesTests:
    def _connection(self, directory):
        return connect_state(Path(directory) / "state.sqlite3")

    def test_a_job_repaired_before_the_stage_existed_stays_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._connection(directory)
            try:
                from book_agent.state import initialize_state

                initialize_state(connection)
                set_stage_status(connection, WorkflowStage.REPAIR_TRANSLATION.value, StageStatus.COMPLETED)
                initialize_pipeline_stages(connection)
                record = get_stage_status(connection, WorkflowStage.AUDIT_CONSISTENCY.value)
                assert record["status"] == StageStatus.COMPLETED.value
                assert record["message"] == ADDED_AFTER_REPAIR_MESSAGE
            finally:
                connection.close()

    def test_a_new_job_gets_a_pending_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._connection(directory)
            try:
                from book_agent.state import initialize_state

                initialize_state(connection)
                initialize_pipeline_stages(connection)
                record = get_stage_status(connection, WorkflowStage.AUDIT_CONSISTENCY.value)
                assert record["status"] == StageStatus.PENDING.value
            finally:
                connection.close()


# -- the stage and its plumbing, on the fixture pipeline ------------------------------------


REPEATED_CHAPTER = b"""<?xml version='1.0'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Fixture</title></head>
<body><h1>Chapter One</h1><p>The Queen shouted at everyone in the garden.</p>
<p>Alice walked on quietly.</p><p>The Queen shouted at everyone in the garden.</p></body></html>"""


def _config(**consistency) -> AppConfig:
    return AppConfig.model_validate(
        {"audit": {"semantic_enabled": False}, "consistency": consistency}
    )


def _prepared(directory, config):
    """A job through validate_repaired on a chapter with one repeated paragraph."""
    from tests.epub_fixture import make_epub
    from tests.test_compile_stages import CompileStageTests

    base = Path(directory)
    return CompileStageTests().prepare_workspace(
        base, config, source=make_epub(base / "repeated.epub", chapter=REPEATED_CHAPTER)
    )


def _repeated_ids(workspace):
    from book_agent.stages.validate_repaired import load_validated_repaired_documents

    return [
        segment.segment_id
        for document in load_validated_repaired_documents(workspace)
        for segment in document.document.segments
        if "Queen shouted" in segment.source_text
    ]


def _fake_issue(segment_id: str, severity=AuditSeverity.MEDIUM) -> AuditIssue:
    return AuditIssue(
        segment_id=segment_id,
        category=AuditCategory.CONSISTENCY,
        severity=severity,
        message="Repeated line rendered differently (test).",
        suggested_fix="Use the rendering from elsewhere.",
        source=CONSISTENCY_SOURCE,
    )


class AuditConsistencyStageTests:
    def test_runs_in_the_pipeline_and_always_publishes_a_report(self):
        from book_agent.stages.audit_consistency import (
            load_consistency_audit_report,
            load_consistency_issues,
            run_consistency_audit_stage,
        )
        from tests.test_compile_stages import CompileStageTests

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = CompileStageTests().prepare_workspace(Path(directory), config)
            report = run_consistency_audit_stage(workspace, config)
            assert report.enabled and report.issue_count == 0 and report.segment_count > 0
            assert load_consistency_audit_report(workspace) == report
            assert load_consistency_issues(workspace) == {}
            connection = connect_state(workspace.state_file)
            try:
                record = get_stage_status(connection, WorkflowStage.AUDIT_CONSISTENCY.value)
            finally:
                connection.close()
            assert record["status"] == StageStatus.COMPLETED.value
            assert record["message"] == "no consistency drift found"

    def test_findings_are_stored_per_document_and_reach_repair(self):
        from book_agent.stages.audit_consistency import load_consistency_issues, run_consistency_audit_stage
        from book_agent.stages.repair import run_translation_repair_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _prepared(directory, config)
            first, _ = _repeated_ids(workspace)
            captured = {}

            def capture(audits, minimum):
                captured["issues"] = [i for a in audits for i in a.issues]
                return {}

            with patch(
                "book_agent.stages.audit_consistency.book_consistency_issues",
                return_value=[_fake_issue(first), _fake_issue(first, AuditSeverity.LOW)],
            ):
                report = run_consistency_audit_stage(workspace, config)
            assert (report.issue_count, report.repair_count) == (2, 1)
            stored = load_consistency_issues(workspace)
            assert [i.segment_id for issues in stored.values() for i in issues] == [first, first]
            with patch("book_agent.stages.repair.select_repair_targets", side_effect=capture):
                run_translation_repair_stage(workspace, config)
            assert any(
                i.segment_id == first and i.source == CONSISTENCY_SOURCE for i in captured["issues"]
            )

    def test_rerunning_it_invalidates_repair_and_later_stages(self):
        from book_agent.stages.audit_consistency import run_consistency_audit_stage

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            run_consistency_audit_stage(workspace, _config())
            run_consistency_audit_stage(workspace, _config(min_repeat_characters=20))
            connection = connect_state(workspace.state_file)
            try:
                repair = get_stage_status(connection, WorkflowStage.REPAIR_TRANSLATION.value)
                validate = get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)
            finally:
                connection.close()
            assert repair["status"] == StageStatus.PENDING.value
            assert validate["status"] == StageStatus.PENDING.value

    def test_disabled_publishes_an_empty_report(self):
        from book_agent.stages.audit_consistency import run_consistency_audit_stage

        with tempfile.TemporaryDirectory() as directory:
            config = _config(enabled=False)
            workspace = _prepared(directory, config)
            with patch(
                "book_agent.stages.audit_consistency.book_consistency_issues",
                side_effect=AssertionError("must not run"),
            ):
                report = run_consistency_audit_stage(workspace, config)
            assert not report.enabled and report.issue_count == 0


class ValidateRepairedRecheckTests:
    def test_medium_drift_in_the_final_drafts_joins_the_defect_queue(self):
        from book_agent.stages.validate_repaired import (
            load_repaired_document_validations,
            load_repaired_validation_report,
            run_repaired_validation_stage,
        )
        from tests.test_validate_repaired_stage import FakeVerificationClient

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _prepared(directory, config)
            first, second = _repeated_ids(workspace)
            with patch(
                "book_agent.stages.validate_repaired.book_consistency_issues",
                return_value=[_fake_issue(second), _fake_issue(first, AuditSeverity.LOW)],
            ):
                run_repaired_validation_stage(
                    workspace, _config(close_variant_similarity=0.5), FakeVerificationClient()
                )
            report = load_repaired_validation_report(workspace)
            assert second in report.defect_segment_ids and second in report.review_segment_ids
            assert first not in report.review_segment_ids  # low drift is listed, not queued
            validation = next(
                item for item in load_repaired_document_validations(workspace)
                if second in item.review_segment_ids
            )
            assert any(
                issue.category is AuditCategory.CONSISTENCY
                for issue in validation.deterministic_audit.issues
            )


class CompileGateTests:
    def test_an_edit_makes_the_unedited_occurrence_block_compile(self):
        """Decision 7.4 end to end: the edited rendering wins; the other copy is queued."""
        from book_agent.hashing import sha256_text
        from book_agent.stages.compile import write_unresolved_review_report
        from book_agent.stages.validate_repaired import (
            load_repaired_validation_report,
            load_validated_repaired_documents,
        )
        from book_agent.text_edits import apply_edit, check_edits, unresolved_review_gate

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, second = _repeated_ids(workspace)
            report = load_repaired_validation_report(workspace)
            assert unresolved_review_gate(workspace, report).unresolved_review_ids == []

            segment = next(
                s for d in load_validated_repaired_documents(workspace)
                for s in d.document.segments if s.segment_id == first
            )
            # A human edit is the reference, so the proposal itself is not flagged.
            assert check_edits(workspace, {first: "新译文。"})[first] == {
                "hard": [], "overridable": []
            }
            apply_edit(
                workspace,
                segment_id=first,
                text="新译文。",
                reason="Clearer rendering of the Queen's line.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            gate = unresolved_review_gate(workspace, report)
            assert gate.unresolved_review_ids == [second]
            assert gate.defect_count == 1

            # The Text tab shows the drift on the unedited copy (Consistency filter).
            from book_agent.web.text_view import text_chapter

            document_id = next(
                d.document.manifest_id for d in load_validated_repaired_documents(workspace)
                if any(s.segment_id == second for s in d.document.segments)
            )
            rows = {row["segment_id"]: row for row in text_chapter(workspace, document_id)["segments"]}
            assert [f["category"] for f in rows[second]["findings"]] == ["consistency"]
            assert rows[second]["in_review_queue"]

            write_unresolved_review_report(workspace)
            worksheet = json.loads(
                (workspace.root / "reports" / "unresolved-review-segments.json").read_text(encoding="utf-8")
            )
            entry = next(item for item in worksheet["segments"] if item["segment_id"] == second)
            assert any(f["origin"] == "book_consistency" for f in entry["findings"])

    def test_a_second_edit_that_disagrees_needs_an_override(self):
        from book_agent.hashing import sha256_text
        from book_agent.stages.validate_repaired import load_validated_repaired_documents
        from book_agent.text_edits import apply_edit, check_edits

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, second = _repeated_ids(workspace)
            segment = next(
                s for d in load_validated_repaired_documents(workspace)
                for s in d.document.segments if s.segment_id == first
            )
            apply_edit(
                workspace,
                segment_id=first,
                text="新译文。",
                reason="Clearer rendering of the Queen's line.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            result = check_edits(workspace, {second: "旧译文。"})[second]
            assert result["hard"] == []
            assert [f["category"] for f in result["overridable"]] == ["consistency"]
            # Matching the first edit is fine.
            assert check_edits(workspace, {second: "新译文。"})[second]["overridable"] == []

    def test_a_substantial_rewrite_lists_the_other_copies_without_queueing_them(self):
        """Clearly different wording counts as intentional (low), even from a human edit."""
        from book_agent.hashing import sha256_text
        from book_agent.stages.validate_repaired import (
            load_repaired_validation_report,
            load_validated_repaired_documents,
        )
        from book_agent.text_edits import apply_edit, unresolved_review_gate

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, _ = _repeated_ids(workspace)
            segment = next(
                s for d in load_validated_repaired_documents(workspace)
                for s in d.document.segments if s.segment_id == first
            )
            apply_edit(
                workspace,
                segment_id=first,
                text="王后对花园里的每个人大喊。",
                reason="A rewrite for this scene only.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            gate = unresolved_review_gate(workspace, load_repaired_validation_report(workspace))
            assert gate.unresolved_review_ids == []

    def test_disabled_checks_leave_the_gate_alone(self):
        from book_agent.hashing import sha256_text
        from book_agent.stages.validate_repaired import (
            load_repaired_validation_report,
            load_validated_repaired_documents,
        )
        from book_agent.text_edits import apply_edit, unresolved_review_gate

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config(enabled=False))
            first, _ = _repeated_ids(workspace)
            segment = next(
                s for d in load_validated_repaired_documents(workspace)
                for s in d.document.segments if s.segment_id == first
            )
            apply_edit(
                workspace,
                segment_id=first,
                text="新译文。",
                reason="Clearer rendering of the Queen's line.",
                base_target_sha256=sha256_text(segment.translated_text),
            )
            gate = unresolved_review_gate(workspace, load_repaired_validation_report(workspace))
            assert gate.unresolved_review_ids == []


def _segment(workspace, text: str):
    """The validated segment whose source contains ``text``."""
    from book_agent.stages.validate_repaired import load_validated_repaired_documents

    return next(
        segment
        for document in load_validated_repaired_documents(workspace)
        for segment in document.document.segments
        if text in segment.source_text
    )


def _edit(workspace, segment, text: str) -> None:
    from book_agent.hashing import sha256_text
    from book_agent.text_edits import apply_edit

    apply_edit(
        workspace,
        segment_id=segment.segment_id,
        text=text,
        reason="Reviewer's wording.",
        base_target_sha256=sha256_text(segment.translated_text),
    )


class DraftDriftTests:
    """Drift already in the validated draft is validate_repaired's to queue, not compile's."""

    def test_a_job_validated_before_the_checks_is_not_blocked_by_an_unrelated_edit(self):
        from book_agent.stages.compile import run_epub_compile_stage
        from book_agent.stages.validate_repaired import load_repaired_validation_report
        from book_agent.text_edits import compiled_consistency_issues, unresolved_review_gate

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _prepared(directory, config)
            first, second = _repeated_ids(workspace)
            report = load_repaired_validation_report(workspace)
            assert report.review_segment_ids == []
            other = _segment(workspace, "Alice walked")
            # The draft drifts, but its validation report predates the checks.
            with patch(
                "book_agent.text_edits.book_consistency_issues",
                return_value=[_fake_issue(first), _fake_issue(second)],
            ) as detector:
                assert unresolved_review_gate(workspace, report).total_count == 0
                assert detector.call_count == 0  # without an edit there is nothing to compare
                _edit(workspace, other, other.translated_text + "（改）")
                gate = unresolved_review_gate(workspace, report)
                assert (gate.unresolved_review_ids, gate.defect_count) == ([], 0)
                # Still listed for the reader (Text tab, compile worksheet).
                assert {i.segment_id for i in compiled_consistency_issues(workspace)} == {first, second}
                assert run_epub_compile_stage(workspace, config).segment_count > 0

    def test_only_the_drift_an_edit_adds_joins_the_gate(self):
        from book_agent.stages.validate_repaired import load_repaired_validation_report
        from book_agent.text_edits import unresolved_review_gate

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, second = _repeated_ids(workspace)
            other = _segment(workspace, "Alice walked")
            _edit(workspace, other, other.translated_text + "（改）")

            def detector(segments, settings, style=None):
                found = [_fake_issue(first)]
                if any(segment.edited for segment in segments):
                    found.append(_fake_issue(second))
                return found

            with patch("book_agent.text_edits.book_consistency_issues", side_effect=detector):
                gate = unresolved_review_gate(workspace, load_repaired_validation_report(workspace))
            assert (gate.unresolved_review_ids, gate.defect_count) == ([second], 1)


class TextTabFindingsTests:
    def _rows(self, workspace, document_id):
        from book_agent.web.text_view import text_chapter

        return {row["segment_id"]: row for row in text_chapter(workspace, document_id)["segments"]}

    def _validate_status(self, workspace) -> str:
        connection = connect_state(workspace.state_file)
        try:
            return get_stage_status(connection, WorkflowStage.VALIDATE_REPAIRED.value)["status"]
        finally:
            connection.close()

    def test_stage_findings_show_until_validation_and_not_after_the_drift_is_gone(self):
        """An empty compile-time check means no drift, not a missing draft."""
        from book_agent.stages.audit_consistency import load_consistency_issues, run_consistency_audit_stage
        from book_agent.stages.validate_repaired import load_validated_repaired_documents

        with tempfile.TemporaryDirectory() as directory:
            config = _config()
            workspace = _prepared(directory, config)
            first, _ = _repeated_ids(workspace)
            document_id = next(
                d.document.manifest_id for d in load_validated_repaired_documents(workspace)
                if any(s.segment_id == first for s in d.document.segments)
            )
            found = [_fake_issue(first), _fake_issue(first, AuditSeverity.LOW)]
            with patch(
                "book_agent.stages.audit_consistency.book_consistency_issues", return_value=found
            ):
                run_consistency_audit_stage(workspace, config)
            assert load_consistency_issues(workspace)  # what the stage found before repair

            # The validated draft has no drift left: the stage findings are history.
            assert self._validate_status(workspace) == StageStatus.COMPLETED.value
            row = self._rows(workspace, document_id)[first]
            assert row["findings"] == [] and not row["flagged"]

            # Before the draft is validated, the stage findings are all there is.
            with patch(
                "book_agent.stages.audit_consistency.book_consistency_issues", return_value=found
            ):
                run_consistency_audit_stage(workspace, _config(min_repeat_characters=20))
            assert self._validate_status(workspace) == StageStatus.PENDING.value
            row = self._rows(workspace, document_id)[first]
            assert [(f["category"], f["severity"]) for f in row["findings"]] == [
                ("consistency", "medium")
            ]


class EditCheckConventionTests:
    def test_a_proposed_edit_is_still_checked_against_the_books_punctuation(self):
        from book_agent.text_edits import check_edits

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, _ = _repeated_ids(workspace)
            _edit(workspace, _segment(workspace, "Alice walked"), "爱丽丝——安静地走着。")
            flagged = check_edits(workspace, {first: "王后—对花园里的每个人大喊。"})[first]
            assert any(
                f["category"] == "consistency" and "single dash" in f["message"]
                for f in flagged["overridable"]
            )
            paired = check_edits(workspace, {first: "王后——对花园里的每个人大喊。"})[first]
            assert not any("single dash" in f["message"] for f in paired["overridable"])


class ConsistencyCacheTests:
    def test_the_whole_book_check_runs_once_until_the_edits_change(self):
        """The Text tab and the gate ask several times per request (docs/BOOK_CONSISTENCY.md, 8.2)."""
        from book_agent import consistency
        from book_agent.stages.validate_repaired import (
            load_repaired_validation_report,
            load_validated_repaired_documents,
        )
        from book_agent.text_edits import compiled_consistency_issues, unresolved_review_gate
        from book_agent.web.text_view import text_chapter, text_outline

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, second = _repeated_ids(workspace)
            report = load_repaired_validation_report(workspace)
            document_id = next(
                d.document.manifest_id for d in load_validated_repaired_documents(workspace)
                if any(s.segment_id == second for s in d.document.segments)
            )
            with patch(
                "book_agent.text_edits.book_consistency_issues",
                wraps=consistency.book_consistency_issues,
            ) as detector:
                _edit(workspace, _segment(workspace, "Alice walked"), "爱丽丝安静地走着（改）。")
                detector.reset_mock()  # the edit check has its own pass
                assert unresolved_review_gate(workspace, report).unresolved_review_ids == []
                assert detector.call_count == 1  # the text as compiled: no drift, nothing to compare
                compiled_consistency_issues(workspace)
                unresolved_review_gate(workspace, report)
                text_outline(workspace)
                assert detector.call_count == 1

                # A new edit changes the text as compiled, so it is checked again,
                # and compared with the draft itself.
                _edit(workspace, _segment(workspace, "Queen shouted"), "新译文。")
                detector.reset_mock()
                assert unresolved_review_gate(workspace, report).unresolved_review_ids == [second]
                assert detector.call_count == 2
                rows = text_chapter(workspace, document_id)["segments"]
                assert [row["segment_id"] for row in rows if row["in_review_queue"]] == [second]
                text_outline(workspace)
                assert detector.call_count == 2

    def test_a_cached_result_cannot_be_changed_by_its_reader(self):
        from book_agent.text_edits import compiled_consistency_issues

        with tempfile.TemporaryDirectory() as directory:
            workspace = _prepared(directory, _config())
            first, second = _repeated_ids(workspace)
            _edit(workspace, _segment(workspace, "Queen shouted"), "新译文。")
            issues = compiled_consistency_issues(workspace)
            assert [i.segment_id for i in issues] == [second]
            issues.clear()
            assert [i.segment_id for i in compiled_consistency_issues(workspace)] == [second]
