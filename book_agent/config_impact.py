"""What changing a started job's config does to its pipeline (docs/OUTPUT_AND_CONFIG_UX.md, 4).

Every setting is given the first stage that has to run again for a change of
it to be in the job's result. A finished stage is not looked at again when a
job resumes, so a change to a setting it read does nothing until that stage is
rerun; this module says which stage that is.

The table is written from what each stage reads and hashes. Two tests keep it
from drifting (tests/test_config_impact.py): every setting of `AppConfig` must
resolve here, and for a sample of settings a changed value must make the
mapped stage's recorded input hash stale and no earlier stage's.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .config import AppConfig
from .pipeline_state import WorkflowStage, downstream_stages

S = WorkflowStage

# Settings that make it another job: a job is not moved to another pair of
# languages or another place. Shown with this reason, and never saved.
LOCKED: dict[str, str] = {
    "translation.direction": "direction",
    "translation.source_language": "direction",
    "translation.target_language": "direction",
    "paths.": "paths",
}

# The first stage that reads each setting, by exact path or by section ("audit.").
# The longest matching entry wins. None: nothing already done depends on it; it
# applies to whatever runs next (a timeout, a retry count, a setting nothing reads).
_FIRST_STAGE: dict[str, WorkflowStage | None] = {
    # How Ollama is reached and retried, and how progress is reported.
    "ollama.": None,
    # The default model of the glossary's resolution and of every later role that names none.
    "ollama.model": S.RESOLVE_GLOSSARY,
    # The context a call is given decides how the book is cut into chunks.
    "ollama.num_ctx": S.TRANSLATE,
    "ollama.adaptive_num_ctx": S.TRANSLATE,
    "ollama.min_num_ctx": S.TRANSLATE,
    "ollama.model_num_ctx_caps": S.TRANSLATE,
    "ollama.temperature": S.TRANSLATE,
    "budget.": S.TRANSLATE,
    "translation.": S.TRANSLATE,
    "translation.preserve_paragraphs": None,
    "translation.preserve_punctuation": None,
    "translation.fallback_models": S.RESCUE_TRANSLATION,
    "translation.attempts_per_model": S.RESCUE_TRANSLATION,
    "translation.harmonize_fallback_with_primary": S.RESCUE_TRANSLATION,
    "translation.translated_title": S.TRANSLATE_TITLE,
    "glossary.": S.EXTRACT_GLOSSARY,
    "glossary.resolution_": S.RESOLVE_GLOSSARY,
    "glossary.seed_glossaries": S.RESOLVE_GLOSSARY,
    "glossary.series_glossaries": S.RESOLVE_GLOSSARY,
    "glossary.book_glossaries": S.RESOLVE_GLOSSARY,
    "glossary.approval_": S.APPROVE_GLOSSARY,
    "glossary.drop_generic_terms": S.APPROVE_GLOSSARY,
    "preprocessing.": S.PREPROCESS,
    "epub.": S.COMPILE,
    "audit.": S.AUDIT_TRANSLATION,
    # The severity repair starts at also decides which consistency findings go to it.
    "audit.repair_min_severity": S.AUDIT_CONSISTENCY,
    "audit.repair_": S.REPAIR_TRANSLATION,
    "audit.quantity.verify_repairs": S.REPAIR_TRANSLATION,
    "audit.verifier_": S.REVIEW_REPAIRED,
    "audit.semantic_verification_policy": S.VALIDATE_REPAIRED,
    "reprose.": S.REPROSE_TRANSLATION,
    "consistency.": S.AUDIT_CONSISTENCY,
    # The style sheet is extracted with the glossary and approved at its gate.
    "consistency.style_sheet.": S.EXTRACT_GLOSSARY,
    "consistency.style_sheet.review": S.APPROVE_GLOSSARY,
    "consistency.style_sheet.max_entries_per_chunk": S.TRANSLATE,
    "consistency.story_context.": S.BUILD_STORY_CONTEXT,
    "consistency.story_context.chapters_before": S.PREPROCESS,
    "workflow.": None,
    "workflow.require_glossary_review": S.APPROVE_GLOSSARY,
    "workflow.llm_glossary_review": S.APPROVE_GLOSSARY,
    "workflow.defer_failed_translation_segments": S.TRANSLATE,
    "workflow.require_final_review": S.COMPILE,
    "workflow.compile_max_unresolved_review_segments": S.COMPILE,
    # A subtitle job's reading limits are settings of its audits, and of the compile that breaks its lines.
    "subtitles.": S.AUDIT_TRANSLATION,
    "output.": S.COMPILE,
    # Locked once a job has started (LOCKED): where it lives is not one of its results.
    "paths.": None,
}

# A setting read by a second stage that is no dependent of its first: the
# pipeline has two branches between the source and the preprocessing, the
# glossary's and the story summaries', and a rerun of one does not redo the
# other. Each entry says when the second stage reads the setting.
_ALSO: dict[str, tuple[WorkflowStage, Any]] = {
    # The story summaries are written by the default model when they name none of their own.
    "ollama.model": (
        S.BUILD_STORY_CONTEXT,
        lambda config: config.consistency.story_context.enabled and not config.consistency.story_context.model,
    ),
}

_ORDER = {stage: index for index, stage in enumerate(WorkflowStage)}


def _match(table: dict[str, Any], path: str) -> str | None:
    """The entry of `table` for a setting: its own, or the longest prefix that covers it."""
    if path in table:
        return path
    prefixes = [key for key in table if key.endswith((".", "_")) and path.startswith(key)]
    return max(prefixes, key=len) if prefixes else None


def locked_reason(path: str) -> str:
    """Why a setting cannot be changed once a job has started: "direction", "paths", or "" when it can."""
    key = _match(LOCKED, path)
    return LOCKED[key] if key else ""


def first_stage(path: str) -> WorkflowStage | None:
    """The first stage that must run again for a change of this setting to
    reach the job's result; None when nothing already done depends on it.
    KeyError for a setting the table does not know."""
    key = _match(_FIRST_STAGE, path)
    if key is None:
        raise KeyError(f"no pipeline stage is recorded for the setting {path}")
    return _FIRST_STAGE[key]


def affected_stages(path: str, before: AppConfig, after: AppConfig) -> list[WorkflowStage]:
    """Every stage that must run again for a change of this setting between
    two configs, in pipeline order: its first stage, and any stage on another
    branch of the pipeline that reads it too. The dependents of these are not
    listed: a rerun of a stage redoes them."""
    first = first_stage(path)
    stages = [first] if first else []
    if path in _ALSO:
        other, applies = _ALSO[path]
        if other not in stages and (applies(before) or applies(after)):
            stages.append(other)
    return sorted(stages, key=_ORDER.__getitem__)


def rerun_roots(stages: list[WorkflowStage]) -> list[WorkflowStage]:
    """The fewest stages to rerun so that every one of `stages` is redone: those
    that are no dependent of another. Rerunning a stage redoes its dependents
    only, so two stages on different branches are both named."""
    wanted = sorted(set(stages), key=_ORDER.__getitem__)
    covered = {dependent for stage in wanted for dependent in downstream_stages(stage)}
    return [stage for stage in wanted if stage not in covered]


def setting_paths(model: type[BaseModel] = AppConfig, prefix: str = "") -> list[str]:
    """Every setting of the config, as its dotted path."""
    paths: list[str] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths.extend(setting_paths(annotation, f"{prefix}{name}."))
        else:
            paths.append(f"{prefix}{name}")
    return paths


def _value(data: Any, path: str) -> Any:
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def config_changes(before: AppConfig, after: AppConfig) -> list[dict[str, Any]]:
    """The settings that differ between two configs, in the config's own
    order, each with its two values, the first stage it affects ("" for
    none), every stage it affects that is no dependent of another
    (`stages`), and why it is locked ("" when it is not)."""
    # Every section is written out: a section left at its defaults is still compared.
    old = {name: getattr(before, name).model_dump(mode="json") for name in AppConfig.model_fields}
    new = {name: getattr(after, name).model_dump(mode="json") for name in AppConfig.model_fields}
    changes: list[dict[str, Any]] = []
    for path in setting_paths():
        was, now = _value(old, path), _value(new, path)
        if was == now:
            continue
        stages = affected_stages(path, before, after)
        changes.append(
            {
                "path": path,
                "before": was,
                "after": now,
                "stage": stages[0].value if stages else "",
                "stages": [stage.value for stage in stages],
                "locked": locked_reason(path),
            }
        )
    return changes


def config_impact(changes: list[dict[str, Any]], statuses: dict[str, str]) -> dict[str, Any]:
    """What a set of changes does to a job whose stages stand at `statuses`
    (stage name to status).

    `rerun_stages` are the stages the job must be rerun from for every
    change to be in its result: each finished stage that read a changed
    setting, less those a rerun of another already redoes. It is one stage
    as a rule, and two when the changes reach both the glossary's branch of
    the pipeline and the story summaries'. It is empty when no finished
    stage is affected, and the changes then simply apply to what runs next.
    `rerun_stage` is the first of them ("" for none). Each change says
    whether a stage that read it has finished (`finished`: the change is not
    in what that stage made)."""
    done = {name for name, status in statuses.items() if status == "completed"}
    described = [
        {**change, "finished": any(stage in done for stage in change.get("stages", [change["stage"]]))}
        for change in changes
    ]
    affected = [
        S(stage)
        for change in described
        if not change["locked"]
        for stage in change.get("stages", [change["stage"]])
        if stage in done
    ]
    roots = [stage.value for stage in rerun_roots(affected)]
    return {
        "changes": described,
        "locked": [change["path"] for change in described if change["locked"]],
        "rerun_stages": roots,
        "rerun_stage": roots[0] if roots else "",
    }
