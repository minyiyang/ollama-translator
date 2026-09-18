"""New-job setup: config files, validation, dry-run, and model availability."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..atomic_io import atomic_write_text
from ..cli import resolve_config_paths
from ..config import AppConfig
from ..pipeline_state import WorkflowStage
from ..workspace import build_job_id, slugify_job_name, validate_job_id

_CONFIG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")


def list_configs(config_dir: Path) -> list[dict[str, str]]:
    if not config_dir.is_dir():
        return []
    return [
        {"name": path.name, "path": str(path)}
        for path in sorted(config_dir.iterdir())
        if path.is_file() and _CONFIG_NAME_RE.match(path.name)
    ]


def config_path(config_dir: Path, name: str) -> Path:
    if not _CONFIG_NAME_RE.match(name):
        raise ValueError("config name must be a simple .yaml/.yml file name")
    return config_dir / name


def read_config(config_dir: Path, name: str) -> str:
    return config_path(config_dir, name).read_text(encoding="utf-8")


def save_config(config_dir: Path, name: str, text: str) -> str:
    parse_config(text, config_dir)  # never save an invalid configuration
    path = config_path(config_dir, name)
    config_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text if text.endswith("\n") else text + "\n")
    return str(path)


def parse_config(text: str, base: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"YAML syntax error: {error}") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    try:
        return resolve_config_paths(AppConfig.model_validate(raw), base)
    except ValidationError as error:
        raise ValueError(
            "\n".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
        ) from error


def model_roles(config: AppConfig) -> list[dict[str, str]]:
    """Every model the enabled stages will call, with the role that uses it."""
    roles = [("translation, glossary resolution/approval", config.ollama.model)]
    if config.glossary.extraction_enabled:
        roles.append(("glossary extraction", config.glossary.extraction_model))
    roles += [(f"fallback translation #{i + 1}", model) for i, model in enumerate(config.translation.fallback_models)]
    if config.audit.semantic_enabled:
        roles.append(("semantic audit", config.audit.model))
        if config.audit.verifier_model:
            roles.append(("repair verification", config.audit.verifier_model))
    roles.append(("targeted repair", config.audit.repair_model or config.ollama.model))
    if config.audit.quantity.enabled:
        roles.append(("quantity audit", config.audit.quantity.model))
        if config.audit.quantity.escalation_model:
            roles.append(("quantity escalation", config.audit.quantity.escalation_model))
    if config.reprose.enabled:
        roles.append(("prose rewrite", config.reprose.model))
        roles.append(("rewrite verification", config.reprose.verifier_model))
    return [{"role": role, "model": model} for role, model in roles if model]


def installed_models(host: str) -> list[str] | None:
    """Ask Ollama for its local model list; ``None`` when it is unreachable."""
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=3) as response:
            return [item["name"] for item in json.load(response).get("models", [])]
    except (OSError, ValueError):
        return None


def _is_installed(model: str, installed: list[str]) -> bool:
    return model in installed or (":" not in model and f"{model}:latest" in installed)


def validate_setup(
    text: str,
    config_dir: Path,
    runs: Path,
    source: str,
    job_id: str,
) -> dict[str, Any]:
    """Validate everything ``run`` needs without creating files or calling a model."""
    config = parse_config(text, config_dir)
    problems: list[str] = []
    source_path = Path(source).expanduser()
    if not source:
        problems.append("choose a source EPUB or RTF file")
    elif not source_path.is_file():
        problems.append(f"source file not found: {source_path}")
    elif source_path.suffix.casefold() not in {".epub", ".rtf"}:
        problems.append("source must be an EPUB or RTF file")
    readable = f"{slugify_job_name(source_path.stem)}-{config.translation.direction.value}" if source else ""
    job = job_id or (readable if readable and not (runs / readable).exists() else build_job_id(source_path) if source else "")
    try:
        validate_job_id(job)
        if (runs / job).exists():
            problems.append(f"a job named {job} already exists; pick another job id")
    except ValueError as error:
        problems.append(str(error))
    installed = installed_models(config.ollama.host)
    models = [
        {**item, "installed": None if installed is None else _is_installed(item["model"], installed)}
        for item in model_roles(config)
    ]
    if installed is None:
        problems.append(f"Ollama is not reachable at {config.ollama.host}")
    else:
        missing = sorted({m["model"] for m in models if not m["installed"]})
        if missing:
            problems.append("models not installed: " + ", ".join(missing))
    return {
        "ok": not problems,
        "problems": problems,
        "job_id": job,
        "models": models,
        "summary": {
            "direction": config.translation.direction.value,
            "style": config.translation.style.value,
            "glossary_review": (
                "LLM" if config.workflow.llm_glossary_review
                else "human" if config.workflow.require_glossary_review
                else "none"
            ),
            "semantic_audit": config.audit.semantic_enabled,
            "reprose": config.reprose.enabled,
            "final_review_required": config.workflow.require_final_review,
            "runs": str(runs),
        },
        "stages": [stage.value for stage in WorkflowStage],
    }


_SECTION_TITLES = {
    "ollama": "Ollama connection",
    "budget": "Token budget",
    "translation": "Translation",
    "glossary": "Glossary",
    "preprocessing": "Preprocessing",
    "epub": "EPUB output",
    "audit": "Audit and repair",
    "reprose": "Prose rewrite",
    "workflow": "Workflow gates",
    "paths": "Paths",
}


def config_schema() -> dict[str, Any]:
    """Flatten ``AppConfig`` into form-ready fields grouped by top-level section."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # Path defaults are not JSON-schema serializable
        schema = AppConfig.model_json_schema()
    definitions = schema["$defs"]
    defaults = AppConfig().model_dump(mode="json")

    def resolve(prop: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in prop:
            target = definitions[prop["$ref"].rsplit("/", 1)[-1]]
            return {**target, **{k: v for k, v in prop.items() if k != "$ref"}}
        return prop

    def lookup(path: list[str]) -> Any:
        value: Any = defaults
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def walk(model: dict[str, Any], path: list[str]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        for key, raw in model.get("properties", {}).items():
            prop = resolve(raw)
            nullable = False
            if "anyOf" in prop:
                options = [resolve(option) for option in prop["anyOf"]]
                nullable = any(option.get("type") == "null" for option in options)
                prop = {**next(o for o in options if o.get("type") != "null"), **{
                    k: v for k, v in prop.items() if k != "anyOf"
                }}
            here = [*path, key]
            if prop.get("type") == "object" and "properties" in prop:
                fields.extend(walk(prop, here))
                continue
            kind = "enum" if "enum" in prop else prop.get("type", "string")
            field = {
                "path": ".".join(here),
                "key": key,
                "type": kind,
                "default": lookup(here),
                "nullable": nullable,
            }
            if "enum" in prop:
                field["enum"] = prop["enum"]
            if kind == "array":
                field["item_type"] = resolve(prop.get("items", {})).get("type", "string")
            if kind == "object":
                field["value_type"] = resolve(prop.get("additionalProperties", {})).get("type", "string")
            for bound in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                if bound in prop:
                    field[bound] = prop[bound]
            fields.append(field)
        return fields

    return {
        "sections": [
            {
                "key": key,
                "title": _SECTION_TITLES.get(key, key),
                "fields": walk(resolve(prop), [key]),
            }
            for key, prop in schema["properties"].items()
        ]
    }


class YamlSyntaxError(ValueError):
    """YAML that does not parse, with the 1-based position PyYAML reported."""

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        context_line: int | None = None,
    ) -> None:
        super().__init__(f"YAML syntax error: {message}")
        self.problem = message
        self.line = line
        self.column = column
        self.context_line = context_line  # e.g. where an unclosed bracket was opened

    def as_dict(self) -> dict[str, Any]:
        return {"message": self.problem, "line": self.line, "column": self.column, "context_line": self.context_line}


def parse_values(text: str) -> dict[str, Any]:
    """YAML text to a plain mapping, without schema validation."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        context_mark = getattr(error, "context_mark", None)
        problem = getattr(error, "problem", None) or str(error)
        context = getattr(error, "context", None)
        message = f"{context}; {problem}" if context else str(problem)
        raise YamlSyntaxError(
            message,
            line=mark.line + 1 if mark is not None else None,
            column=mark.column + 1 if mark is not None else None,
            context_line=(
                context_mark.line + 1
                if context_mark is not None and (mark is None or context_mark.line != mark.line)
                else None
            ),
        ) from error
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise YamlSyntaxError("the top level must be a mapping of settings (key: value)", line=1, column=1)
    return raw


def parse_for_editor(text: str) -> dict[str, Any]:
    """Editor feedback: parsed values, or the syntax error and where it is. No schema checks."""
    try:
        return {"values": parse_values(text), "syntax_error": None}
    except YamlSyntaxError as error:
        return {"values": None, "syntax_error": error.as_dict()}


def dump_values(values: dict[str, Any]) -> str:
    """A mapping to YAML in the user's key order (comments are not preserved)."""
    return yaml.safe_dump(values, sort_keys=False, allow_unicode=True, default_flow_style=False)


def check_config(text: str, base: Path) -> list[dict[str, str]]:
    """Schema errors keyed by dotted field path; an empty list means valid."""
    try:
        raw = parse_values(text)
    except ValueError as error:
        return [{"path": "", "message": str(error)}]
    try:
        resolve_config_paths(AppConfig.model_validate(raw), base)
    except ValidationError as error:
        return [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
            }
            for item in error.errors()
        ]
    return []
