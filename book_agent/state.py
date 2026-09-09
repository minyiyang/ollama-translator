"""SQLite primitives for resumable workflow stage state."""

import sqlite3
import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


def connect_state(path: str | Path) -> sqlite3.Connection:
    """Open a state database and create its parent directory when necessary."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_state(connection: sqlite3.Connection) -> None:
    """Create the resumable workflow state schema idempotently."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stages (
            name TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            input_hash TEXT NOT NULL DEFAULT '',
            output_hash TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            path TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS work_units (
            unit_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            kind TEXT NOT NULL,
            parent_id TEXT,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            input_hash TEXT NOT NULL DEFAULT '',
            output_hash TEXT NOT NULL DEFAULT '',
            validation_json TEXT NOT NULL DEFAULT '{}',
            message TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(unit_id, stage)
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS validations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL,
            validator TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """
    )
    _ensure_column(connection, "stages", "input_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "stages", "output_hash", "TEXT NOT NULL DEFAULT ''")
    connection.commit()


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def set_stage_status(
    connection: sqlite3.Connection,
    name: str,
    status: StageStatus,
    *,
    attempts: int = 0,
    message: str = "",
    input_hash: str = "",
    output_hash: str = "",
) -> None:
    """Insert or update one workflow stage status."""
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValueError("stage name cannot be empty")
    if attempts < 0:
        raise ValueError("attempts cannot be negative")
    updated_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO stages(name, status, attempts, message, input_hash, output_hash, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            status = excluded.status,
            attempts = excluded.attempts,
            message = excluded.message,
            input_hash = excluded.input_hash,
            output_hash = excluded.output_hash,
            updated_at = excluded.updated_at
        """,
        (cleaned_name, status.value, attempts, message, input_hash, output_hash, updated_at),
    )
    connection.commit()


def get_stage_status(connection: sqlite3.Connection, name: str) -> dict[str, object] | None:
    """Return one stage as a dictionary, or None when it is unknown."""
    row = connection.execute(
        "SELECT name, status, attempts, message, input_hash, output_hash, updated_at "
        "FROM stages WHERE name = ?",
        (name,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_stage_statuses(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """Return all stages ordered by name."""
    rows = connection.execute(
        "SELECT name, status, attempts, message, input_hash, output_hash, updated_at "
        "FROM stages ORDER BY name"
    ).fetchall()
    return [dict(row) for row in rows]


def set_job_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    """Insert or replace one job metadata value."""
    cleaned_key = key.strip()
    if not cleaned_key:
        raise ValueError("metadata key cannot be empty")
    connection.execute(
        "INSERT INTO job_metadata(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (cleaned_key, value),
    )
    connection.commit()


def get_job_metadata(connection: sqlite3.Connection, key: str) -> str | None:
    """Return one job metadata value."""
    row = connection.execute(
        "SELECT value FROM job_metadata WHERE key = ?", (key,)
    ).fetchone()
    return str(row["value"]) if row is not None else None


def list_job_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """Return all job metadata ordered by key."""
    rows = connection.execute(
        "SELECT key, value FROM job_metadata ORDER BY key"
    ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def record_artifact(
    connection: sqlite3.Connection,
    path: str,
    stage: str,
    kind: str,
    sha256: str,
    size: int,
) -> None:
    """Insert or update a validated artifact record."""
    if not path.strip() or not stage.strip() or not kind.strip():
        raise ValueError("artifact path, stage, and kind cannot be empty")
    artifact_path = Path(path)
    if artifact_path.is_absolute() or ".." in artifact_path.parts:
        raise ValueError("artifact path must stay relative to the job workspace")
    if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise ValueError("artifact sha256 must be a lowercase 64-character hex digest")
    if size < 0:
        raise ValueError("artifact size cannot be negative")
    connection.execute(
        """
        INSERT INTO artifacts(path, stage, kind, sha256, size, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            stage = excluded.stage,
            kind = excluded.kind,
            sha256 = excluded.sha256,
            size = excluded.size,
            updated_at = excluded.updated_at
        """,
        (artifact_path.as_posix(), stage, kind, sha256, size, _utc_now()),
    )
    connection.commit()


def get_artifact(connection: sqlite3.Connection, path: str) -> dict[str, object] | None:
    """Return an artifact record by stored relative path."""
    row = connection.execute(
        "SELECT path, stage, kind, sha256, size, updated_at FROM artifacts WHERE path = ?",
        (path,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_artifacts(
    connection: sqlite3.Connection,
    *,
    stage: str | None = None,
) -> list[dict[str, object]]:
    """Return artifact records, optionally filtered by producing stage."""
    if stage is None:
        rows = connection.execute(
            "SELECT path, stage, kind, sha256, size, updated_at FROM artifacts ORDER BY path"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT path, stage, kind, sha256, size, updated_at FROM artifacts "
            "WHERE stage = ? ORDER BY path",
            (stage,),
        ).fetchall()
    return [dict(row) for row in rows]


def retire_stage_artifacts(connection: sqlite3.Connection, stage: str) -> int:
    """Remove published records for an incomplete generation, preserving files and work units."""
    cleaned_stage = stage.strip()
    if not cleaned_stage:
        raise ValueError("stage cannot be empty")
    cursor = connection.execute("DELETE FROM artifacts WHERE stage = ?", (cleaned_stage,))
    connection.commit()
    return int(cursor.rowcount)


def set_work_unit_status(
    connection: sqlite3.Connection,
    unit_id: str,
    stage: str,
    kind: str,
    status: StageStatus,
    *,
    parent_id: str | None = None,
    attempts: int = 0,
    input_hash: str = "",
    output_hash: str = "",
    validation: dict[str, object] | None = None,
    message: str = "",
) -> None:
    """Insert or update chapter/chunk state."""
    if not unit_id.strip() or not stage.strip() or not kind.strip():
        raise ValueError("unit_id, stage, and kind cannot be empty")
    if attempts < 0:
        raise ValueError("attempts cannot be negative")
    validation_json = json.dumps(validation or {}, ensure_ascii=False, sort_keys=True)
    connection.execute(
        """
        INSERT INTO work_units(
            unit_id, stage, kind, parent_id, status, attempts, input_hash,
            output_hash, validation_json, message, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(unit_id, stage) DO UPDATE SET
            kind = excluded.kind,
            parent_id = excluded.parent_id,
            status = excluded.status,
            attempts = excluded.attempts,
            input_hash = excluded.input_hash,
            output_hash = excluded.output_hash,
            validation_json = excluded.validation_json,
            message = excluded.message,
            updated_at = excluded.updated_at
        """,
        (
            unit_id, stage, kind, parent_id, status.value, attempts, input_hash,
            output_hash, validation_json, message, _utc_now(),
        ),
    )
    connection.commit()


def get_work_unit(
    connection: sqlite3.Connection,
    unit_id: str,
    stage: str,
) -> dict[str, object] | None:
    """Return one stage-specific chapter/chunk record with decoded validation data."""
    row = connection.execute(
        "SELECT * FROM work_units WHERE unit_id = ? AND stage = ?", (unit_id, stage)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["validation"] = json.loads(str(result.pop("validation_json")))
    return result


def list_work_units(
    connection: sqlite3.Connection,
    *,
    stage: str | None = None,
) -> list[dict[str, object]]:
    """Return decoded chapter/chunk records ordered by unit identifier."""
    if stage is None:
        rows = connection.execute("SELECT * FROM work_units ORDER BY unit_id").fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM work_units WHERE stage = ? ORDER BY unit_id", (stage,)
        ).fetchall()
    results = []
    for row in rows:
        result = dict(row)
        result["validation"] = json.loads(str(result.pop("validation_json")))
        results.append(result)
    return results


def record_attempt(
    connection: sqlite3.Connection,
    scope_id: str,
    stage: str,
    attempt: int,
    status: StageStatus,
    *,
    message: str = "",
    metrics: dict[str, object] | None = None,
) -> int:
    """Append an immutable stage/chunk attempt and return its database id."""
    if not scope_id.strip() or not stage.strip():
        raise ValueError("scope_id and stage cannot be empty")
    if attempt <= 0:
        raise ValueError("attempt must be positive")
    cursor = connection.execute(
        """
        INSERT INTO attempts(scope_id, stage, attempt, status, message, metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scope_id, stage, attempt, status.value, message,
            json.dumps(metrics or {}, ensure_ascii=False, sort_keys=True), _utc_now(),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def list_attempts(connection: sqlite3.Connection, scope_id: str) -> list[dict[str, object]]:
    """Return attempt history for a job, chapter, or chunk."""
    rows = connection.execute(
        "SELECT * FROM attempts WHERE scope_id = ? ORDER BY attempt, id", (scope_id,)
    ).fetchall()
    results = []
    for row in rows:
        result = dict(row)
        result["metrics"] = json.loads(str(result.pop("metrics_json")))
        results.append(result)
    return results


def record_validation(
    connection: sqlite3.Connection,
    scope_id: str,
    validator: str,
    passed: bool,
    *,
    details: dict[str, object] | None = None,
) -> int:
    """Append an immutable validation result and return its database id."""
    if not scope_id.strip() or not validator.strip():
        raise ValueError("scope_id and validator cannot be empty")
    cursor = connection.execute(
        """
        INSERT INTO validations(scope_id, validator, passed, details_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            scope_id, validator, int(passed),
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True), _utc_now(),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def list_validations(
    connection: sqlite3.Connection,
    scope_id: str,
) -> list[dict[str, object]]:
    """Return decoded validation history for a scope."""
    rows = connection.execute(
        "SELECT * FROM validations WHERE scope_id = ? ORDER BY id", (scope_id,)
    ).fetchall()
    results = []
    for row in rows:
        result = dict(row)
        result["passed"] = bool(result["passed"])
        result["details"] = json.loads(str(result.pop("details_json")))
        results.append(result)
    return results


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
