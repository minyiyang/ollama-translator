import tempfile
import threading
from pathlib import Path

import pytest

from book_agent.config import AppConfig
from book_agent.hashing import sha256_text
from book_agent.repair import RepairedValidationReport
from book_agent.stages.validate_repaired import load_validated_repaired_documents
from book_agent.text_edits import (
    BlockingCheckError,
    EditAction,
    SegmentEditEvent,
    SegmentEditRequest,
    SegmentEditState,
    StaleEditError,
    apply_edit,
    apply_edit_batch,
    apply_keep,
    apply_revert,
    apply_take_pipeline,
    conflicted_segment_ids,
    events_by_segment,
    load_events,
    segment_status,
    unresolved_review_gate,
)
from tests.test_compile_stages import CompileStageTests, _retarget_pipeline_text


def _clean_workspace(directory: Path):
    config = AppConfig.model_validate({"audit": {"semantic_enabled": False}})
    workspace = CompileStageTests().prepare_workspace(directory, config)
    from book_agent.stages.validate_repaired import load_validated_repaired_documents

    repaired = load_validated_repaired_documents(workspace)[0]
    segment_id = repaired.document.segments[0].segment_id
    pipeline_text = repaired.document.segments[0].translated_text
    return workspace, segment_id, pipeline_text


class TextEditsTests:
    def test_no_log_file_means_no_events(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, _, _ = _clean_workspace(Path(directory))
            assert load_events(workspace) == []

    def test_apply_edit_appends_and_reaches_edited_state(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            event = apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Restored the missing clause.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            assert event.event_id == "E000001"
            assert event.action is EditAction.EDIT
            assert event.text == "第一章"
            assert event.previous_text == pipeline_text
            assert event.base_target_sha256 == sha256_text(pipeline_text)
            assert event.base_target_text == pipeline_text
            assert event.reason == "Restored the missing clause."
            assert event.author

            events = load_events(workspace)
            assert len(events) == 1
            status = segment_status(events, pipeline_text=pipeline_text, source_text="Chapter One")
            assert status.state is SegmentEditState.EDITED
            assert status.text == "第一章"
            grouped = events_by_segment(workspace)
            assert segment_id in grouped and len(grouped[segment_id]) == 1

    def test_apply_edit_rejects_a_short_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            with pytest.raises(ValueError, match="reason"):
                apply_edit(
                    workspace,
                    segment_id=segment_id,
                    text="x",
                    reason="ok",
                    base_target_sha256=sha256_text(pipeline_text),
                )

    def test_apply_edit_rejects_a_stale_base_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, _ = _clean_workspace(Path(directory))
            with pytest.raises(StaleEditError):
                apply_edit(
                    workspace,
                    segment_id=segment_id,
                    text="第一章",
                    reason="Restored the missing clause.",
                    base_target_sha256="0" * 64,
                )

    def test_apply_edit_to_empty_text_is_never_overridable(self):
        """Empty text is a hard-block category (matching Final review); no reason waives it."""
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            with pytest.raises(BlockingCheckError) as excinfo:
                apply_edit(
                    workspace,
                    segment_id=segment_id,
                    text="",
                    reason="Clearing it out for a test.",
                    base_target_sha256=sha256_text(pipeline_text),
                )
            assert any(f["category"] == "empty" for f in excinfo.value.findings)

            with pytest.raises(BlockingCheckError):
                apply_edit(
                    workspace,
                    segment_id=segment_id,
                    text="",
                    reason="Clearing it out for a test.",
                    base_target_sha256=sha256_text(pipeline_text),
                    override_reason="Confirmed intentional for this test.",
                )

    def test_apply_edit_with_an_overridable_finding_needs_and_records_a_reason(self, monkeypatch):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            monkeypatch.setattr(
                "book_agent.text_edits.check_edit",
                lambda ws, sid, text: [
                    {"category": "naturalness", "severity": "high", "message": "reads stiffly"}
                ],
            )
            with pytest.raises(BlockingCheckError) as excinfo:
                apply_edit(
                    workspace,
                    segment_id=segment_id,
                    text="第一章",
                    reason="Smoothing the phrasing.",
                    base_target_sha256=sha256_text(pipeline_text),
                )
            assert excinfo.value.findings[0]["category"] == "naturalness"

            event = apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Smoothing the phrasing.",
                base_target_sha256=sha256_text(pipeline_text),
                override_reason="Reviewed by hand; the stiffness is intentional.",
            )
            assert event.overrides == ["reads stiffly"]

    def test_apply_revert_returns_to_pipeline_and_refuses_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Restored the missing clause.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            revert = apply_revert(workspace, segment_id=segment_id, reason="Changed my mind.")
            assert revert.action is EditAction.REVERT
            assert revert.previous_text == "第一章"

            events = load_events(workspace)
            status = segment_status(events, pipeline_text=pipeline_text, source_text="anything")
            assert status.state is SegmentEditState.PIPELINE

            with pytest.raises(ValueError, match="no active edit"):
                apply_revert(workspace, segment_id=segment_id, reason="Nothing to undo here.")

    def test_apply_keep_rebases_a_conflict_to_edited(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Testing conflict unblock via keep.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            _retarget_pipeline_text(workspace, segment_id, "全新翻译。")
            events = load_events(workspace)
            conflict_status = segment_status(events, pipeline_text="全新翻译。", source_text="Chapter One")
            assert conflict_status.state is SegmentEditState.CONFLICT
            assert segment_id in conflicted_segment_ids(workspace)

            kept = apply_keep(workspace, segment_id=segment_id, reason="Keeping my wording over the rerun.")
            assert kept.action is EditAction.KEEP
            assert kept.text == "第一章"
            assert kept.base_target_sha256 == sha256_text("全新翻译。")

            events = load_events(workspace)
            status = segment_status(events, pipeline_text="全新翻译。", source_text="Chapter One")
            assert status.state is SegmentEditState.EDITED
            assert status.text == "第一章"
            assert segment_id not in conflicted_segment_ids(workspace)

    def test_apply_take_pipeline_returns_a_conflict_to_the_new_pipeline_text(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Testing conflict unblock via take_pipeline.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            _retarget_pipeline_text(workspace, segment_id, "全新翻译。")

            taken = apply_take_pipeline(
                workspace, segment_id=segment_id, reason="Accepting the rerun's wording."
            )
            assert taken.action is EditAction.TAKE_PIPELINE
            assert taken.text == ""
            assert taken.previous_text == "第一章"

            events = load_events(workspace)
            status = segment_status(events, pipeline_text="全新翻译。", source_text="Chapter One")
            assert status.state is SegmentEditState.PIPELINE
            assert status.text == "全新翻译。"
            assert segment_id not in conflicted_segment_ids(workspace)

    def test_keep_and_take_pipeline_refuse_a_non_conflict_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            with pytest.raises(ValueError, match="not a conflict"):
                apply_keep(workspace, segment_id=segment_id, reason="Nothing to keep here.")
            with pytest.raises(ValueError, match="not a conflict"):
                apply_take_pipeline(workspace, segment_id=segment_id, reason="Nothing to take here.")

            apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="A plain edit, not a conflict.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            with pytest.raises(ValueError, match="not a conflict"):
                apply_keep(workspace, segment_id=segment_id, reason="Still not a conflict.")

    def test_corrupt_trailing_line_is_reported_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            apply_edit(
                workspace,
                segment_id=segment_id,
                text="第一章",
                reason="Restored the missing clause.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            log_path = workspace.root / "edits" / "segment-edits.jsonl"
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("{not json\n")
            with pytest.raises(ValueError, match="corrupt at line 2"):
                load_events(workspace)

    def test_concurrent_appends_get_unique_sequential_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, pipeline_text = _clean_workspace(Path(directory))
            stale: list[StaleEditError] = []

            def save(n: int) -> None:
                try:
                    apply_edit(
                        workspace,
                        segment_id=segment_id,
                        text="第一章",
                        reason="concurrency test",
                        base_target_sha256=sha256_text(pipeline_text),
                        expected_event_id="",
                    )
                except StaleEditError as error:
                    stale.append(error)

            threads = [
                threading.Thread(target=save, args=(n,))
                for n in range(20)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            events = load_events(workspace)
            assert [event.event_id for event in events] == ["E000001"]
            assert len(stale) == 19

    def test_batch_rejects_a_stale_member_without_appending_any_event(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, first_id, first_text = _clean_workspace(Path(directory))
            documents = load_validated_repaired_documents(workspace)
            second = next(
                segment
                for repaired in documents
                for segment in repaired.document.segments
                if segment.segment_id != first_id
            )
            with pytest.raises(StaleEditError):
                apply_edit_batch(
                    workspace,
                    [
                        SegmentEditRequest(
                            segment_id=first_id,
                            text="第一章",
                            reason="First member of an atomic batch.",
                            base_target_sha256=sha256_text(first_text),
                            expected_event_id="",
                        ),
                        SegmentEditRequest(
                            segment_id=second.segment_id,
                            text=second.translated_text,
                            reason="Second member has a stale revision.",
                            base_target_sha256=sha256_text(second.translated_text),
                            expected_event_id="E999999",
                        ),
                    ],
                )
            assert load_events(workspace) == []


class SegmentStatusTests:
    def _event(self, **overrides):
        base = dict(
            event_id="E000001",
            at="2026-09-21T00:00:00+00:00",
            author="tester",
            segment_id="D0000-S1",
            document_id="chapter",
            action=EditAction.EDIT,
            text="edited text",
            previous_text="pipeline text",
            base_source_sha256=sha256_text("source"),
            base_target_sha256=sha256_text("pipeline text"),
            reason="a good reason",
        )
        base.update(overrides)
        return SegmentEditEvent(**base)

    def test_no_events_is_pipeline(self):
        status = segment_status([], pipeline_text="pipeline text", source_text="source")
        assert status.state is SegmentEditState.PIPELINE
        assert status.text == "pipeline text"

    def test_edit_against_unchanged_pipeline_is_edited(self):
        status = segment_status(
            [self._event()], pipeline_text="pipeline text", source_text="source"
        )
        assert status.state is SegmentEditState.EDITED
        assert status.text == "edited text"

    def test_edit_after_pipeline_text_changes_is_conflict(self):
        status = segment_status(
            [self._event()], pipeline_text="a new pipeline text", source_text="source"
        )
        assert status.state is SegmentEditState.CONFLICT

    def test_edit_after_source_changes_is_orphaned(self):
        status = segment_status(
            [self._event()], pipeline_text="pipeline text", source_text="a different source"
        )
        assert status.state is SegmentEditState.ORPHANED

    def test_missing_segment_is_orphaned(self):
        status = segment_status([self._event()], pipeline_text=None, source_text=None)
        assert status.state is SegmentEditState.ORPHANED

    def test_keep_rebases_to_edited_against_new_pipeline_text(self):
        keep = self._event(
            action=EditAction.KEEP,
            base_target_sha256=sha256_text("a new pipeline text"),
        )
        status = segment_status([self._event(), keep], pipeline_text="a new pipeline text", source_text="source")
        assert status.state is SegmentEditState.EDITED
        assert status.text == "edited text"

    def test_take_pipeline_returns_to_pipeline(self):
        take = self._event(action=EditAction.TAKE_PIPELINE, text="")
        status = segment_status(
            [self._event(), take], pipeline_text="a new pipeline text", source_text="source"
        )
        assert status.state is SegmentEditState.PIPELINE
        assert status.text == "a new pipeline text"


class UnresolvedReviewGateTests:
    """Final review and the Text tab share this gate (docs/FULL_TEXT_REVIEW.md, phase 5)."""

    def _report(self, **overrides):
        base = dict(
            document_count=1,
            segment_count=4,
            passed=False,
            remaining_issue_count=2,
            review_segment_ids=[],
            remaining_defect_count=0,
            approval_required_count=0,
            defect_segment_ids=[],
            approval_segment_ids=[],
        )
        base.update(overrides)
        return RepairedValidationReport(**base)

    def test_classifies_defects_and_approvals_and_reports_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, defect_id, _ = _clean_workspace(Path(directory))
            other = load_validated_repaired_documents(workspace)[0].document.segments[1]
            approval_id = other.segment_id
            report = self._report(
                review_segment_ids=sorted([defect_id, approval_id]),
                defect_segment_ids=[defect_id],
                approval_segment_ids=[approval_id],
            )

            gate = unresolved_review_gate(workspace, report)

            assert gate.unresolved_review_ids == sorted([defect_id, approval_id])
            assert gate.conflict_ids == []
            assert gate.defect_count == 1
            assert gate.approval_count == 1
            assert gate.total_count == 2
            assert gate.passes(2)
            assert not gate.passes(1)

    def test_uncategorized_review_ids_fall_back_to_all_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, segment_id, _ = _clean_workspace(Path(directory))
            other = load_validated_repaired_documents(workspace)[0].document.segments[1]
            report = self._report(
                review_segment_ids=sorted([segment_id, other.segment_id]),
                defect_segment_ids=[],
                approval_segment_ids=[],
            )

            gate = unresolved_review_gate(workspace, report)

            assert gate.defect_count == 2
            assert gate.approval_count == 0

    def test_an_active_edit_resolves_queue_membership_without_rewriting_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, defect_id, pipeline_text = _clean_workspace(Path(directory))
            other = load_validated_repaired_documents(workspace)[0].document.segments[1]
            approval_id = other.segment_id
            report = self._report(
                review_segment_ids=sorted([defect_id, approval_id]),
                defect_segment_ids=[defect_id],
                approval_segment_ids=[approval_id],
            )

            apply_edit(
                workspace,
                segment_id=defect_id,
                text="第一章",
                reason="Resolving the defect via an edit.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            gate = unresolved_review_gate(workspace, report)

            assert gate.unresolved_review_ids == [approval_id]
            assert gate.defect_count == 0
            assert gate.approval_count == 1
            # The original report object passed in is never mutated.
            assert report.review_segment_ids == sorted([defect_id, approval_id])

    def test_a_conflict_is_excluded_from_the_queue_but_tracked_and_counted_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace, defect_id, pipeline_text = _clean_workspace(Path(directory))
            other = load_validated_repaired_documents(workspace)[0].document.segments[1]
            approval_id = other.segment_id
            report = self._report(
                review_segment_ids=sorted([defect_id, approval_id]),
                defect_segment_ids=[defect_id],
                approval_segment_ids=[approval_id],
            )

            apply_edit(
                workspace,
                segment_id=defect_id,
                text="第一章",
                reason="Setting up a conflict for the gate test.",
                base_target_sha256=sha256_text(pipeline_text),
            )
            _retarget_pipeline_text(workspace, defect_id, "全新翻译。")

            gate = unresolved_review_gate(workspace, report)

            assert defect_id not in gate.unresolved_review_ids
            assert gate.conflict_ids == [defect_id]
            assert gate.unresolved_review_ids == [approval_id]
            assert gate.defect_count == 0
            assert gate.approval_count == 1
            assert gate.total_count == 2  # 1 unresolved queue segment + 1 conflict
