"""Isolated per-book job workspace creation and discovery."""

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .atomic_io import atomic_copy_file, atomic_write_text
from .config import AppConfig
from .hashing import sha256_file, sha256_text
from .pipeline_state import initialize_pipeline_stages
from .state import connect_state, initialize_state, set_job_metadata


WORKSPACE_DIRECTORIES = (
    "logs",
    "source",
    "decompiled",
    "glossary/candidates",
    "preprocessed",
    "translated",
    "audited",
    "repaired",
    "reprosed",
    "output",
    "reports",
    "tmp",
)


@dataclass(frozen=True)
class JobWorkspace:
    root: Path
    source_file: Path
    config_file: Path
    state_file: Path

    def directory(self, relative: str) -> Path:
        """Return a workspace child directory and reject traversal."""
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("workspace path escapes the job root")
        return candidate


def slugify_job_name(value: str) -> str:
    """Create a portable lowercase ASCII job-name component."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "book"


def build_job_id(source: str | Path, *, now: datetime | None = None) -> str:
    """Build a readable job id from source stem and UTC time."""
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{slugify_job_name(Path(source).stem)}-{timestamp:%Y%m%dT%H%M%SZ}"


def validate_job_id(job_id: str) -> str:
    """Validate an explicit job id as one safe path component."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_id):
        raise ValueError("job_id must be a safe path component")
    return job_id


def create_job_workspace(
    source: str | Path,
    runs_directory: str | Path,
    config: AppConfig,
    *,
    job_id: str | None = None,
    now: datetime | None = None,
    config_source: str | Path | None = None,
) -> JobWorkspace:
    """Create an isolated job, atomically capture source/config, and initialize state."""
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    resolved_job_id = validate_job_id(job_id) if job_id else build_job_id(source_path, now=now)
    root = Path(runs_directory).resolve() / resolved_job_id
    root.mkdir(parents=True, exist_ok=False)
    try:
        for relative in WORKSPACE_DIRECTORIES:
            (root / relative).mkdir(parents=True, exist_ok=False)
        captured_source = root / "source" / source_path.name
        atomic_copy_file(source_path, captured_source)
        config_text = config.model_dump_json(indent=2)
        config_file = atomic_write_text(root / "config.resolved.json", config_text)
        state_file = root / "state.sqlite3"
        connection = connect_state(state_file)
        try:
            initialize_state(connection)
            initialize_pipeline_stages(connection)
            metadata = {
                "job_id": resolved_job_id,
                "created_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "source_original": str(source_path),
                "source_copy": str(captured_source.relative_to(root)),
                "source_sha256": sha256_file(captured_source),
                "config_sha256": sha256_text(config_text),
                "production_profile": config.workflow.production_profile,
                "production_profile_version": str(
                    config.workflow.production_profile_version
                ),
                "semantic_verification_policy": (
                    config.audit.semantic_verification_policy
                ),
            }
            if config_source is not None:
                config_source_path = Path(config_source).resolve()
                if not config_source_path.is_file():
                    raise FileNotFoundError(config_source_path)
                metadata["config_source"] = str(config_source_path)
                metadata["config_source_sha256"] = sha256_file(config_source_path)
            for key, value in metadata.items():
                set_job_metadata(connection, key, value)
        finally:
            connection.close()
        return JobWorkspace(
            root=root,
            source_file=captured_source,
            config_file=config_file,
            state_file=state_file,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def open_job_workspace(root: str | Path) -> JobWorkspace:
    """Open and validate an existing job workspace."""
    root_path = Path(root).resolve()
    config_file = root_path / "config.resolved.json"
    state_file = root_path / "state.sqlite3"
    source_files = list((root_path / "source").glob("*")) if (root_path / "source").is_dir() else []
    if not root_path.is_dir() or not config_file.is_file() or not state_file.is_file():
        raise ValueError("not a valid job workspace")
    source_files = [path for path in source_files if path.is_file()]
    if len(source_files) != 1:
        raise ValueError("job workspace must contain exactly one captured source file")
    return JobWorkspace(root_path, source_files[0], config_file, state_file)
