"""Safe stage planning and per-segment outcome reporting helpers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from .ollama_client import (
    GenerationProgressEvent,
    OllamaClient,
    select_request_context,
)


TaskT = TypeVar("TaskT")


def llm_role_kwargs(client: object, role: str) -> dict[str, str]:
    """Attach role metadata to the real client without constraining test doubles."""
    return {"usage_role": role} if isinstance(client, OllamaClient) else {}


def request_context_bucket(
    prompt: str,
    *,
    minimum: int,
    maximum: int,
    schema: type[BaseModel] | None = None,
    multiplier: float = 2.0,
) -> int:
    """Predict the exact adaptive context bucket used by ``OllamaClient``."""
    response_format = schema.model_json_schema() if schema is not None else None
    return select_request_context(
        prompt,
        minimum=minimum,
        maximum=maximum,
        response_format=response_format,
        multiplier=multiplier,
    )


def group_by_context_bucket(
    tasks: Sequence[TaskT],
    bucket: Callable[[TaskT], int],
) -> list[TaskT]:
    """Return a stable context-homogeneous execution order."""
    return sorted(tasks, key=bucket)


def group_by_model_then_context(
    tasks: Sequence[TaskT],
    model: Callable[[TaskT], str],
    bucket: Callable[[TaskT], int],
) -> list[TaskT]:
    """Return a stable model-major order, then group context sizes per model.

    Model order follows first appearance so callers can keep a warm model loaded
    across a whole phase instead of alternating models at document boundaries.
    """
    model_order: dict[str, int] = {}
    for task in tasks:
        name = model(task)
        if name not in model_order:
            model_order[name] = len(model_order)
    return sorted(tasks, key=lambda task: (model_order[model(task)], bucket(task)))


def report_stage_plan(
    client: OllamaClient | None,
    *,
    model: str,
    stage: str,
    prescreened: int,
    llm_tasks: int,
    skipped: int,
    context_buckets: Iterable[int] = (),
    role: str = "",
) -> None:
    """Print a content-free pre-screen and context scheduling summary."""
    if client is None or not callable(getattr(client, "report_progress", None)):
        return
    counts = Counter(context_buckets)
    buckets = ",".join(f"{size}:{counts[size]}" for size in sorted(counts)) or "none"
    client.report_progress(
        GenerationProgressEvent(
            "stage_plan",
            model,
            label=f"stage-plan={stage}",
            message=(
                f"result=pending; prescreened={prescreened}; llm_tasks={llm_tasks}; "
                f"skipped={skipped}; context_buckets={buckets}"
            ),
            prescreened=prescreened,
            llm_tasks=llm_tasks,
            skipped=skipped,
            role=role,
        )
    )


def report_segment_result(
    client: OllamaClient | None,
    *,
    model: str,
    stage: str,
    segment_id: str,
    result: str,
    mode: str,
    issue_count: int = 0,
    context_bucket: int = 0,
    message: str = "",
    result_index: int = 0,
    result_total: int = 0,
    role: str = "",
) -> None:
    """Print one terminal, content-free outcome for a stage/segment pair."""
    if client is None or not callable(getattr(client, "report_progress", None)):
        return
    details = (
        f"result={result}; mode={mode}; issues={issue_count}"
        + (f"; context={context_bucket}" if context_bucket else "")
        + (f"; reason={message}" if message else "")
    )
    progress = (
        f"segment={result_index}/{result_total} id={segment_id}"
        if result_index and result_total
        else f"segment={segment_id}"
    )
    client.report_progress(
        GenerationProgressEvent(
            "segment_result",
            model,
            label=f"{progress} stage={stage}",
            message=details,
            context_size=context_bucket,
            result=result,
            mode=mode,
            issue_count=issue_count,
            result_index=result_index,
            result_total=result_total,
            role=role,
        )
    )
