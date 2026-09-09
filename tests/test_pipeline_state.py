import tempfile
from pathlib import Path

import pytest

from book_agent.hashing import sha256_file
from book_agent.pipeline_state import (
    WorkflowStage,
    build_stage_input_hash,
    build_stage_output_hash,
    downstream_stages,
    initialize_pipeline_stages,
    invalidate_failed_stage_units_and_dependents,
    invalidate_stage_and_dependents,
    stage_is_current,
)
from book_agent.state import (
    StageStatus,
    connect_state,
    get_artifact,
    get_stage_status,
    get_work_unit,
    initialize_state,
    list_stage_statuses,
    record_artifact,
    set_stage_status,
    set_work_unit_status,
)


class PipelineStateTests:
    def setup_method(self) -> None:
        self.connection = connect_state(":memory:")
        initialize_state(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_initialize_pipeline_creates_every_stage_without_overwriting(self) -> None:
        set_stage_status(
            self.connection,
            WorkflowStage.DECOMPILE.value,
            StageStatus.COMPLETED,
            input_hash="in",
            output_hash="out",
        )
        initialize_pipeline_stages(self.connection)
        assert len(list_stage_statuses(self.connection)) == len(WorkflowStage)
        assert get_stage_status(self.connection, WorkflowStage.DECOMPILE.value)["status"] == "completed"

    def test_downstream_stages_are_transitive_and_ordered(self) -> None:
        downstream = downstream_stages(WorkflowStage.RESOLVE_GLOSSARY)
        assert downstream[0] == WorkflowStage.APPROVE_GLOSSARY
        assert downstream[-1] == WorkflowStage.VALIDATE_EPUB
        assert WorkflowStage.EXTRACT_GLOSSARY not in downstream
        assert downstream_stages(WorkflowStage.VALIDATE_EPUB) == []

    def test_stage_input_hash_is_order_independent_and_nonempty(self) -> None:
        assert build_stage_input_hash({"source": "a", "prompt": "b"}) == build_stage_input_hash({"prompt": "b", "source": "a"})
        with pytest.raises(ValueError, match="cannot be empty"):
            build_stage_input_hash({})

    def test_stage_output_hash_uses_recorded_artifacts(self) -> None:
        record_artifact(self.connection, "b", "translate", "chunk", "2" * 64, 1)
        record_artifact(self.connection, "a", "translate", "chunk", "1" * 64, 1)
        first = build_stage_output_hash(self.connection, WorkflowStage.TRANSLATE)
        self.connection.execute("DELETE FROM artifacts")
        record_artifact(self.connection, "a", "translate", "chunk", "1" * 64, 1)
        record_artifact(self.connection, "b", "translate", "chunk", "2" * 64, 1)
        assert first == build_stage_output_hash(self.connection, WorkflowStage.TRANSLATE)
        with pytest.raises(ValueError, match="no recorded"):
            build_stage_output_hash(self.connection, WorkflowStage.COMPILE)

    def test_stage_current_checks_status_input_output_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "translated" / "chapter.txt"
            artifact.parent.mkdir()
            artifact.write_text("translated", encoding="utf-8")
            record_artifact(
                self.connection,
                "translated/chapter.txt",
                "translate",
                "chapter",
                sha256_file(artifact),
                artifact.stat().st_size,
            )
            output_hash = build_stage_output_hash(self.connection, WorkflowStage.TRANSLATE)
            set_stage_status(
                self.connection,
                "translate",
                StageStatus.COMPLETED,
                input_hash="expected",
                output_hash=output_hash,
            )
            assert (stage_is_current(
                    self.connection,
                    WorkflowStage.TRANSLATE,
                    "expected",
                    artifact_root=root,
                ))
            assert not stage_is_current(self.connection, WorkflowStage.TRANSLATE, "changed")
            artifact.write_text("tampered", encoding="utf-8")
            assert not (stage_is_current(
                    self.connection,
                    WorkflowStage.TRANSLATE,
                    "expected",
                    artifact_root=root,
                ))

    def test_stage_current_rejects_missing_record_artifacts_and_output(self) -> None:
        assert not stage_is_current(self.connection, WorkflowStage.TRANSLATE, "input")
        set_stage_status(
            self.connection,
            "translate",
            StageStatus.COMPLETED,
            input_hash="input",
            output_hash="",
        )
        assert not stage_is_current(self.connection, WorkflowStage.TRANSLATE, "input")
        set_stage_status(
            self.connection,
            "translate",
            StageStatus.COMPLETED,
            input_hash="input",
            output_hash="output",
        )
        with tempfile.TemporaryDirectory() as directory:
            assert not (stage_is_current(
                    self.connection,
                    WorkflowStage.TRANSLATE,
                    "input",
                    artifact_root=directory,
                ))

    def test_invalidation_resets_only_stage_and_dependents(self) -> None:
        initialize_pipeline_stages(self.connection)
        for stage in WorkflowStage:
            set_stage_status(
                self.connection,
                stage.value,
                StageStatus.COMPLETED,
                attempts=2,
                input_hash="in",
                output_hash="out",
            )
        record_artifact(self.connection, "glossary.json", "resolve_glossary", "glossary", "a" * 64, 1)
        record_artifact(self.connection, "chapter.txt", "translate", "chapter", "b" * 64, 1)
        set_work_unit_status(
            self.connection,
            "chunk-01",
            "translate",
            "chunk",
            StageStatus.COMPLETED,
            attempts=2,
            output_hash="out",
            validation={"passed": True},
        )
        affected = invalidate_stage_and_dependents(
            self.connection, WorkflowStage.RESOLVE_GLOSSARY
        )
        assert affected[0] == WorkflowStage.RESOLVE_GLOSSARY
        assert get_stage_status(self.connection, "extract_glossary")["status"] == "completed"
        assert get_stage_status(self.connection, "resolve_glossary")["status"] == "pending"
        assert get_stage_status(self.connection, "translate")["attempts"] == 0
        assert get_artifact(self.connection, "glossary.json") is None
        unit = get_work_unit(self.connection, "chunk-01", "translate")
        assert unit["status"] == "pending"
        assert unit["validation"] == {}

    def test_invalidation_can_exclude_changed_stage(self) -> None:
        initialize_pipeline_stages(self.connection)
        set_stage_status(
            self.connection, "translate", StageStatus.COMPLETED, output_hash="out"
        )
        affected = invalidate_stage_and_dependents(
            self.connection, WorkflowStage.TRANSLATE, include_stage=False
        )
        assert WorkflowStage.TRANSLATE not in affected
        assert get_stage_status(self.connection, "translate")["status"] == "completed"
        assert WorkflowStage.AUDIT_TRANSLATION in affected

    def test_failed_only_invalidation_preserves_completed_stage_units(self) -> None:
        initialize_pipeline_stages(self.connection)
        set_stage_status(
            self.connection, "translate", StageStatus.FAILED, input_hash="in"
        )
        for unit_id, status in [
            ("chunk-ok", StageStatus.COMPLETED),
            ("chunk-bad", StageStatus.FAILED),
        ]:
            set_work_unit_status(
                self.connection,
                unit_id,
                "translate",
                "chunk",
                status,
                attempts=2,
                input_hash="in",
                output_hash="out" if status is StageStatus.COMPLETED else "",
            )
        invalidate_failed_stage_units_and_dependents(
            self.connection, WorkflowStage.TRANSLATE
        )
        completed = get_work_unit(self.connection, "chunk-ok", "translate")
        failed = get_work_unit(self.connection, "chunk-bad", "translate")
        assert completed["status"] == "completed"
        assert completed["output_hash"] == "out"
        assert failed["status"] == "pending"
        assert failed["attempts"] == 0

    def test_stage_current_rejects_artifact_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "root")
            root.mkdir()
            outside = Path(directory, "outside.txt")
            outside.write_text("outside", encoding="utf-8")
            self.connection.execute(
                """
                INSERT INTO artifacts(path, stage, kind, sha256, size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "../outside.txt",
                    "translate",
                    "chapter",
                    sha256_file(outside),
                    outside.stat().st_size,
                    "2026-08-27T00:00:00Z",
                ),
            )
            output_hash = build_stage_output_hash(self.connection, WorkflowStage.TRANSLATE)
            set_stage_status(
                self.connection,
                "translate",
                StageStatus.COMPLETED,
                input_hash="input",
                output_hash=output_hash,
            )
            assert not (stage_is_current(
                    self.connection,
                    WorkflowStage.TRANSLATE,
                    "input",
                    artifact_root=root,
                ))

    def test_interrupted_running_stage_is_resumable_after_database_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory, "state.sqlite3")
            first = connect_state(database)
            initialize_state(first)
            initialize_pipeline_stages(first)
            for stage in WorkflowStage:
                set_stage_status(
                    first,
                    stage.value,
                    StageStatus.RUNNING,
                    attempts=1,
                    input_hash=f"{stage.value}-input",
                )
            first.close()

            resumed = connect_state(database)
            try:
                initialize_state(resumed)
                initialize_pipeline_stages(resumed)
                for stage in WorkflowStage:
                    record = get_stage_status(resumed, stage.value)
                    assert record["status"] == StageStatus.RUNNING.value
                    assert not stage_is_current(resumed, stage, f"{stage.value}-input")
            finally:
                resumed.close()

