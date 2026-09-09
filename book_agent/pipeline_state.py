"""Workflow dependency, currency, and selective invalidation rules."""

import sqlite3
from enum import Enum
from pathlib import Path

from .hashing import hash_named_values, sha256_file
from .state import (
    StageStatus,
    get_stage_status,
    list_artifacts,
    set_stage_status,
)


class WorkflowStage(str, Enum):
    DECOMPILE = "decompile"
    EXTRACT_GLOSSARY = "extract_glossary"
    RESOLVE_GLOSSARY = "resolve_glossary"
    APPROVE_GLOSSARY = "approve_glossary"
    PREPROCESS = "preprocess"
    TRANSLATE = "translate"
    AUDIT_TRANSLATION = "audit_translation"
    REPAIR_TRANSLATION = "repair_translation"
    REPROSE_TRANSLATION = "reprose_translation"
    REVIEW_REPAIRED = "review_repaired"
    REPAIR_REVIEW = "repair_review"
    VALIDATE_REPAIRED = "validate_repaired"
    COMPILE = "compile"
    VALIDATE_EPUB = "validate_epub"


STAGE_DEPENDENCIES: dict[WorkflowStage, tuple[WorkflowStage, ...]] = {
    WorkflowStage.DECOMPILE: (),
    WorkflowStage.EXTRACT_GLOSSARY: (WorkflowStage.DECOMPILE,),
    WorkflowStage.RESOLVE_GLOSSARY: (WorkflowStage.EXTRACT_GLOSSARY,),
    WorkflowStage.APPROVE_GLOSSARY: (WorkflowStage.RESOLVE_GLOSSARY,),
    WorkflowStage.PREPROCESS: (WorkflowStage.APPROVE_GLOSSARY,),
    WorkflowStage.TRANSLATE: (WorkflowStage.PREPROCESS,),
    WorkflowStage.AUDIT_TRANSLATION: (WorkflowStage.TRANSLATE,),
    WorkflowStage.REPAIR_TRANSLATION: (WorkflowStage.AUDIT_TRANSLATION,),
    WorkflowStage.REPROSE_TRANSLATION: (WorkflowStage.REPAIR_TRANSLATION,),
    WorkflowStage.REVIEW_REPAIRED: (WorkflowStage.REPROSE_TRANSLATION,),
    WorkflowStage.REPAIR_REVIEW: (WorkflowStage.REVIEW_REPAIRED,),
    WorkflowStage.VALIDATE_REPAIRED: (WorkflowStage.REPAIR_REVIEW,),
    WorkflowStage.COMPILE: (WorkflowStage.VALIDATE_REPAIRED,),
    WorkflowStage.VALIDATE_EPUB: (WorkflowStage.COMPILE,),
}


def initialize_pipeline_stages(connection: sqlite3.Connection) -> None:
    """Create missing pipeline-stage rows without overwriting resume state."""
    for stage in WorkflowStage:
        if get_stage_status(connection, stage.value) is None:
            set_stage_status(connection, stage.value, StageStatus.PENDING)


def downstream_stages(stage: WorkflowStage) -> list[WorkflowStage]:
    """Return transitive dependents in workflow declaration order."""
    affected: set[WorkflowStage] = set()
    changed = True
    while changed:
        changed = False
        for candidate, dependencies in STAGE_DEPENDENCIES.items():
            if candidate not in affected and (
                stage in dependencies or any(item in affected for item in dependencies)
            ):
                affected.add(candidate)
                changed = True
    return [candidate for candidate in WorkflowStage if candidate in affected]


def build_stage_input_hash(values: dict[str, str]) -> str:
    """Build a canonical hash from named stage inputs."""
    if not values:
        raise ValueError("stage inputs cannot be empty")
    return hash_named_values(values)


def build_stage_output_hash(connection: sqlite3.Connection, stage: WorkflowStage) -> str:
    """Build a canonical hash from all recorded artifacts produced by a stage."""
    artifacts = list_artifacts(connection, stage=stage.value)
    if not artifacts:
        raise ValueError(f"stage has no recorded artifacts: {stage.value}")
    return hash_named_values(
        {str(artifact["path"]): str(artifact["sha256"]) for artifact in artifacts}
    )


def stage_is_current(
    connection: sqlite3.Connection,
    stage: WorkflowStage,
    input_hash: str,
    *,
    artifact_root: str | Path | None = None,
) -> bool:
    """Check completion, input identity, and optionally on-disk artifact hashes."""
    record = get_stage_status(connection, stage.value)
    if record is None:
        return False
    if record["status"] != StageStatus.COMPLETED.value:
        return False
    if record["input_hash"] != input_hash or not record["output_hash"]:
        return False
    if artifact_root is None:
        return True
    artifacts = list_artifacts(connection, stage=stage.value)
    if not artifacts:
        return False
    root = Path(artifact_root)
    resolved_root = root.resolve()
    for artifact in artifacts:
        relative_path = Path(str(artifact["path"]))
        path = (root / relative_path).resolve()
        if path != resolved_root and resolved_root not in path.parents:
            return False
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            return False
    return build_stage_output_hash(connection, stage) == record["output_hash"]


def invalidate_stage_and_dependents(
    connection: sqlite3.Connection,
    stage: WorkflowStage,
    *,
    include_stage: bool = True,
) -> list[WorkflowStage]:
    """Reset a changed stage and its transitive dependents, preserving history."""
    affected = downstream_stages(stage)
    if include_stage:
        affected.insert(0, stage)
    for item in affected:
        connection.execute(
            """
            UPDATE stages SET status = ?, attempts = 0, message = '',
                input_hash = '', output_hash = '', updated_at = ?
            WHERE name = ?
            """,
            (StageStatus.PENDING.value, _database_now(connection), item.value),
        )
        connection.execute("DELETE FROM artifacts WHERE stage = ?", (item.value,))
        connection.execute(
            """
            UPDATE work_units SET status = ?, attempts = 0, output_hash = '',
                validation_json = '{}', message = '', updated_at = ?
            WHERE stage = ?
            """,
            (StageStatus.PENDING.value, _database_now(connection), item.value),
        )
    connection.commit()
    return affected


def invalidate_failed_stage_units_and_dependents(
    connection: sqlite3.Connection,
    stage: WorkflowStage,
) -> list[WorkflowStage]:
    """Reset only failed units in one stage while fully resetting dependents."""
    affected = [stage, *downstream_stages(stage)]
    connection.execute(
        """
        UPDATE stages SET status = ?, attempts = 0, message = '',
            input_hash = '', output_hash = '', updated_at = ?
        WHERE name = ?
        """,
        (StageStatus.PENDING.value, _database_now(connection), stage.value),
    )
    connection.execute("DELETE FROM artifacts WHERE stage = ?", (stage.value,))
    connection.execute(
        """
        UPDATE work_units SET status = ?, attempts = 0, output_hash = '',
            validation_json = '{}', message = '', updated_at = ?
        WHERE stage = ? AND status = ?
        """,
        (
            StageStatus.PENDING.value,
            _database_now(connection),
            stage.value,
            StageStatus.FAILED.value,
        ),
    )
    for item in downstream_stages(stage):
        connection.execute(
            """
            UPDATE stages SET status = ?, attempts = 0, message = '',
                input_hash = '', output_hash = '', updated_at = ?
            WHERE name = ?
            """,
            (StageStatus.PENDING.value, _database_now(connection), item.value),
        )
        connection.execute("DELETE FROM artifacts WHERE stage = ?", (item.value,))
        connection.execute(
            """
            UPDATE work_units SET status = ?, attempts = 0, output_hash = '',
                validation_json = '{}', message = '', updated_at = ?
            WHERE stage = ?
            """,
            (StageStatus.PENDING.value, _database_now(connection), item.value),
        )
    connection.commit()
    return affected


def _database_now(connection: sqlite3.Connection) -> str:
    return str(connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0])
