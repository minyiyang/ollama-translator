import sqlite3
import tempfile
from pathlib import Path

import pytest

from book_agent.state import (
    StageStatus,
    connect_state,
    get_stage_status,
    initialize_state,
    get_artifact,
    get_job_metadata,
    get_work_unit,
    list_artifacts,
    list_attempts,
    list_job_metadata,
    list_stage_statuses,
    list_validations,
    list_work_units,
    record_artifact,
    record_attempt,
    record_validation,
    retire_stage_artifacts,
    set_job_metadata,
    set_stage_status,
    set_work_unit_status,
)


class StateTests:
    def setup_method(self) -> None:
        self.connection = connect_state(":memory:")
        initialize_state(self.connection)

    def teardown_method(self) -> None:
        self.connection.close()

    def test_connect_state_creates_parent_and_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "nested", "state.sqlite3")
            connection = connect_state(path)
            connection.close()
            assert path.exists()

    def test_initialize_state_is_idempotent(self) -> None:
        initialize_state(self.connection)
        initialize_state(self.connection)
        table = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stages'"
        ).fetchone()
        assert table is not None

    def test_set_and_get_stage_status(self) -> None:
        set_stage_status(
            self.connection,
            "translate",
            StageStatus.RUNNING,
            attempts=2,
            message="chapter 3",
        )
        stage = get_stage_status(self.connection, "translate")
        assert stage is not None
        assert stage is not None
        assert stage["status"] == "running"
        assert stage["attempts"] == 2
        assert stage["message"] == "chapter 3"
        assert "+00:00" in str(stage["updated_at"])

    def test_set_stage_status_updates_existing_stage(self) -> None:
        set_stage_status(self.connection, "translate", StageStatus.RUNNING)
        set_stage_status(self.connection, "translate", StageStatus.COMPLETED, attempts=1)
        assert len(list_stage_statuses(self.connection)) == 1
        assert get_stage_status(self.connection, "translate")["status"] == "completed"

    def test_set_stage_status_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            set_stage_status(self.connection, "  ", StageStatus.PENDING)

    def test_set_stage_status_rejects_negative_attempts(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            set_stage_status(
                self.connection, "translate", StageStatus.FAILED, attempts=-1
            )

    def test_get_unknown_stage_returns_none(self) -> None:
        assert get_stage_status(self.connection, "unknown") is None

    def test_list_stage_statuses_orders_by_name(self) -> None:
        set_stage_status(self.connection, "translate", StageStatus.PENDING)
        set_stage_status(self.connection, "decompile", StageStatus.COMPLETED)
        assert [row["name"] for row in list_stage_statuses(self.connection)] == ["decompile", "translate"]

    def test_stage_hashes_are_stored(self) -> None:
        set_stage_status(
            self.connection,
            "translate",
            StageStatus.COMPLETED,
            input_hash="input",
            output_hash="output",
        )
        stage = get_stage_status(self.connection, "translate")
        assert stage["input_hash"] == "input"
        assert stage["output_hash"] == "output"

    def test_job_metadata_set_get_list_and_validation(self) -> None:
        set_job_metadata(self.connection, "job_id", "aster-1")
        set_job_metadata(self.connection, "job_id", "aster-2")
        set_job_metadata(self.connection, "source", "book.epub")
        assert get_job_metadata(self.connection, "job_id") == "aster-2"
        assert get_job_metadata(self.connection, "missing") is None
        assert list_job_metadata(self.connection) == {"job_id": "aster-2", "source": "book.epub"}
        with pytest.raises(ValueError, match="key"):
            set_job_metadata(self.connection, " ", "value")

    def test_artifact_records_update_filter_and_validate(self) -> None:
        record_artifact(self.connection, "one.txt", "translate", "chapter", "a" * 64, 3)
        record_artifact(self.connection, "two.txt", "compile", "epub", "b" * 64, 4)
        record_artifact(self.connection, "one.txt", "translate", "chapter", "c" * 64, 5)
        assert get_artifact(self.connection, "one.txt")["sha256"] == "c" * 64
        assert get_artifact(self.connection, "missing") is None
        assert len(list_artifacts(self.connection)) == 2
        assert len(list_artifacts(self.connection, stage="translate")) == 1
        with pytest.raises(ValueError, match="cannot be empty"):
            record_artifact(self.connection, "", "translate", "chapter", "a" * 64, 1)
        with pytest.raises(ValueError, match="negative"):
            record_artifact(self.connection, "x", "translate", "chapter", "a" * 64, -1)
        with pytest.raises(ValueError, match="relative"):
            record_artifact(
                self.connection, "../outside", "translate", "chapter", "a" * 64, 1
            )
        with pytest.raises(ValueError, match="sha256"):
            record_artifact(self.connection, "x", "translate", "chapter", "invalid", 1)

        assert retire_stage_artifacts(self.connection, "translate") == 1
        assert list_artifacts(self.connection, stage="translate") == []
        assert len(list_artifacts(self.connection, stage="compile")) == 1
        with pytest.raises(ValueError, match="cannot be empty"):
            retire_stage_artifacts(self.connection, " ")

    def test_work_unit_records_roundtrip_and_filter(self) -> None:
        set_work_unit_status(
            self.connection,
            "chapter-01/chunk-01",
            "translate",
            "chunk",
            StageStatus.COMPLETED,
            parent_id="chapter-01",
            attempts=2,
            input_hash="in",
            output_hash="out",
            validation={"passed": True},
        )
        set_work_unit_status(
            self.connection,
            "chapter-02",
            "preprocess",
            "chapter",
            StageStatus.PENDING,
        )
        unit = get_work_unit(self.connection, "chapter-01/chunk-01", "translate")
        assert unit["validation"] == {"passed": True}
        assert unit["attempts"] == 2
        assert get_work_unit(self.connection, "missing", "translate") is None
        assert len(list_work_units(self.connection)) == 2
        assert len(list_work_units(self.connection, stage="translate")) == 1
        with pytest.raises(ValueError, match="cannot be empty"):
            set_work_unit_status(
                self.connection, "", "translate", "chunk", StageStatus.PENDING
            )

    def test_same_unit_can_have_independent_stage_records(self) -> None:
        set_work_unit_status(
            self.connection,
            "chunk-01",
            "translate",
            "chunk",
            StageStatus.COMPLETED,
            output_hash="translated",
        )
        set_work_unit_status(
            self.connection,
            "chunk-01",
            "repair_translation",
            "chunk",
            StageStatus.PENDING,
        )
        assert get_work_unit(self.connection, "chunk-01", "translate")["output_hash"] == "translated"
        assert get_work_unit(self.connection, "chunk-01", "repair_translation")["status"] == "pending"
        with pytest.raises(ValueError, match="negative"):
            set_work_unit_status(
                self.connection,
                "chunk",
                "translate",
                "chunk",
                StageStatus.FAILED,
                attempts=-1,
            )

    def test_attempt_history_is_immutable_and_decoded(self) -> None:
        first = record_attempt(
            self.connection,
            "chunk-01",
            "translate",
            1,
            StageStatus.FAILED,
            metrics={"eval_count": 10},
        )
        second = record_attempt(
            self.connection, "chunk-01", "translate", 2, StageStatus.COMPLETED
        )
        assert first < second
        attempts = list_attempts(self.connection, "chunk-01")
        assert [item["attempt"] for item in attempts] == [1, 2]
        assert attempts[0]["metrics"] == {"eval_count": 10}
        with pytest.raises(ValueError, match="positive"):
            record_attempt(
                self.connection, "chunk", "translate", 0, StageStatus.FAILED
            )
        with pytest.raises(ValueError, match="cannot be empty"):
            record_attempt(self.connection, "", "translate", 1, StageStatus.FAILED)

    def test_validation_history_is_immutable_and_decoded(self) -> None:
        record_validation(
            self.connection,
            "chunk-01",
            "paragraph_ids",
            False,
            details={"missing": ["P2"]},
        )
        record_validation(self.connection, "chunk-01", "paragraph_ids", True)
        validations = list_validations(self.connection, "chunk-01")
        assert [item["passed"] for item in validations] == [False, True]
        assert validations[0]["details"] == {"missing": ["P2"]}
        with pytest.raises(ValueError, match="cannot be empty"):
            record_validation(self.connection, "", "validator", False)

