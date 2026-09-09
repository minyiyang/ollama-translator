"""Single, testable boundary for local Ollama operations."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

import ollama
from pydantic import BaseModel, ValidationError

from .config import OllamaConfig


class OllamaClientError(RuntimeError):
    """Base error for the project Ollama boundary."""


class OllamaUnavailableError(OllamaClientError):
    """The local Ollama service could not complete a request."""


class ModelNotFoundError(OllamaClientError):
    """The configured model is not installed."""


class ContextWindowError(OllamaClientError):
    """The model context is smaller than the configured requirement."""


class GenerationError(OllamaClientError):
    """Text generation failed after bounded retries."""


class StructuredOutputError(OllamaClientError):
    """Structured generation did not validate after bounded retries."""


class GenerationCancelled(OllamaClientError):
    """Generation was cancelled by the caller."""


@dataclass(frozen=True)
class ModelInfo:
    name: str
    context_length: int
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationMetrics:
    done_reason: str = ""
    total_duration_ns: int = 0
    load_duration_ns: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration_ns: int = 0
    eval_count: int = 0
    eval_duration_ns: int = 0
    time_to_first_output_ns: int = 0
    thinking_duration_ns: int = 0
    content_duration_ns: int = 0
    thinking_chars: int = 0


@dataclass(frozen=True)
class GenerationResult:
    content: str
    thinking: str
    metrics: GenerationMetrics


@dataclass(frozen=True)
class GenerationProgressEvent:
    """Safe request progress that never contains prompts, output text, or thinking."""

    kind: Literal[
        "started",
        "heartbeat",
        "completed",
        "validation_failed",
        "stage_plan",
        "segment_result",
    ]
    model: str
    generated_chars: int = 0
    metrics: GenerationMetrics | None = None
    thinking_chars: int = 0
    label: str = ""
    message: str = ""
    context_size: int = 0
    prompt_tokens_estimate: int = 0
    prescreened: int = 0
    llm_tasks: int = 0
    skipped: int = 0
    result: str = ""
    mode: str = ""
    issue_count: int = 0
    result_index: int = 0
    result_total: int = 0
    role: str = ""
    generated_tokens_estimate: int = 0


SchemaT = TypeVar("SchemaT", bound=BaseModel)
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class StructuredGenerationResult(Generic[SchemaT]):
    value: SchemaT
    generation: GenerationResult


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise GenerationCancelled when cancellation was requested."""
        if self.is_cancelled:
            raise GenerationCancelled("generation cancelled")


_THINK_RE = re.compile(r"<think>(.*?)</think>", flags=re.DOTALL | re.IGNORECASE)
_STRUCTURED_TRAILING_SENTINEL = re.compile(
    r"(?:<\|eot\|>|<\|end_of_text\|>|<\|end\|>)\s*$",
    flags=re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _estimate_request_tokens(text: str) -> int:
    """Conservatively estimate mixed-language request tokens without a tokenizer."""
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(_CJK_RE.sub("", text).encode("utf-8"))
    return cjk + math.ceil(non_cjk / 4)


def estimate_request_tokens(
    prompt: str,
    response_format: dict[str, Any] | None = None,
) -> int:
    """Estimate tokens sent before generation, including a structured-output schema."""
    schema_text = json.dumps(response_format, ensure_ascii=False) if response_format else ""
    return _estimate_request_tokens(prompt) + _estimate_request_tokens(schema_text)


def select_request_context(
    prompt: str,
    *,
    maximum: int,
    minimum: int,
    response_format: dict[str, Any] | None = None,
    multiplier: float = 2.0,
) -> int:
    """Choose an 8K-stepped context with prompt and completion headroom."""
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    request_tokens = estimate_request_tokens(prompt, response_format)
    required = math.ceil(request_tokens * multiplier) + 4_096
    step = 8_192
    selected = ((required + step - 1) // step) * step
    return min(maximum, max(minimum, selected))


def split_legacy_thinking(content: str) -> tuple[str, str]:
    """Separate legacy think tags, retaining all visible response content."""
    thoughts = [match.strip() for match in _THINK_RE.findall(content) if match.strip()]
    visible = _THINK_RE.sub("", content).strip()
    return visible, "\n".join(thoughts)


def strip_structured_output_sentinel(content: str) -> str:
    """Remove a recognized terminal model token after complete JSON only."""
    candidate = content.strip()
    match = _STRUCTURED_TRAILING_SENTINEL.search(candidate)
    if match is None:
        return candidate
    prefix = candidate[: match.start()].rstrip()
    return prefix if prefix.endswith(("}", "]")) else candidate


def _value(obj: object, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _message_value(response: object, name: str, default: Any = "") -> Any:
    message = _value(response, "message", {})
    return _value(message, name, default)


class OllamaClient:
    """Local Ollama client with validation, retries, metrics, and cancellation."""

    def __init__(
        self,
        config: OllamaConfig,
        *,
        backend: object | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        progress: Callable[[GenerationProgressEvent], None] | None = None,
        progress_interval_chars: int | None = None,
        progress_interval_tokens: int | None = None,
        progress_interval_seconds: float | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        resolved_chars = (
            config.progress_interval_chars
            if progress_interval_chars is None
            else progress_interval_chars
        )
        resolved_seconds = (
            config.progress_interval_seconds
            if progress_interval_seconds is None
            else progress_interval_seconds
        )
        resolved_tokens = (
            config.progress_interval_tokens
            if progress_interval_tokens is None
            else progress_interval_tokens
        )
        if resolved_chars < 0:
            raise ValueError("progress_interval_chars cannot be negative")
        if resolved_seconds < 0:
            raise ValueError("progress_interval_seconds cannot be negative")
        if resolved_tokens < 0:
            raise ValueError("progress_interval_tokens cannot be negative")
        self.config = config
        self._backend = backend or ollama.Client(
            host=config.host,
            timeout=config.timeout_seconds,
        )
        self._sleep = sleeper
        self._progress = progress
        self._progress_interval_chars = resolved_chars
        self._progress_interval_tokens = resolved_tokens
        self._progress_interval_ns = int(resolved_seconds * 1_000_000_000)
        self._clock_ns = clock_ns

    def health_check(self) -> bool:
        """Return whether the local service responds to a model-list request."""
        try:
            self._backend.list()
            return True
        except (ollama.RequestError, ollama.ResponseError, OSError, TimeoutError):
            return False

    def list_model_names(self) -> list[str]:
        """Return installed model names in server order."""
        def operation() -> list[str]:
            response = self._backend.list()
            models = _value(response, "models", []) or []
            names = []
            for model in models:
                name = _value(model, "model", _value(model, "name", ""))
                if name:
                    names.append(str(name))
            return names

        return self._with_retries(operation, OllamaUnavailableError)

    def inspect_model(self, model: str | None = None) -> ModelInfo:
        """Return normalized metadata for an installed model."""
        model_name = model or self.config.model

        def operation() -> ModelInfo:
            try:
                response = self._backend.show(model_name)
            except ollama.ResponseError as error:
                if error.status_code == 404:
                    raise ModelNotFoundError(f"model is not installed: {model_name}") from error
                raise
            model_info = _value(response, "model_info", None)
            if model_info is None:
                model_info = _value(response, "modelinfo", {}) or {}
            context_values = [
                int(value)
                for key, value in model_info.items()
                if str(key).endswith("context_length")
            ]
            context_length = max(context_values, default=0)
            details = _value(response, "details", {}) or {}
            capabilities = _value(response, "capabilities", ()) or ()
            return ModelInfo(
                name=model_name,
                context_length=context_length,
                family=str(_value(details, "family", "") or ""),
                parameter_size=str(_value(details, "parameter_size", "") or ""),
                quantization=str(_value(details, "quantization_level", "") or ""),
                capabilities=tuple(str(item) for item in capabilities),
            )

        return self._with_retries(operation, OllamaUnavailableError)

    def validate_model_context(
        self,
        required_context: int,
        model: str | None = None,
    ) -> ModelInfo:
        """Inspect a model and require at least the requested context length."""
        if required_context <= 0:
            raise ValueError("required_context must be positive")
        info = self.inspect_model(model)
        if info.context_length < required_context:
            raise ContextWindowError(
                f"model {info.name} context {info.context_length} is below {required_context}"
            )
        return info

    def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        think: bool | None = None,
        stream: bool = True,
        on_chunk: Callable[[str], None] | None = None,
        cancellation: CancellationToken | None = None,
        progress_label: str = "",
        usage_role: str = "",
        context_minimum: int | None = None,
        context_maximum: int | None = None,
        context_multiplier: float = 2.0,
        allow_model_context_cap_override: bool = False,
    ) -> GenerationResult:
        """Generate text with optional streaming progress and bounded retries."""
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        token = cancellation or CancellationToken()
        return self._with_retries(
            lambda: self._generate_once(
                prompt,
                system=system,
                model=model,
                think=think,
                stream=stream,
                response_format=None,
                on_chunk=on_chunk,
                cancellation=token,
                progress_label=progress_label,
                usage_role=usage_role,
                context_minimum=context_minimum,
                context_maximum=context_maximum,
                context_multiplier=context_multiplier,
                allow_model_context_cap_override=allow_model_context_cap_override,
            ),
            GenerationError,
            cancellation=token,
        )

    def generate_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        *,
        system: str | None = None,
        model: str | None = None,
        think: bool | None = None,
        cancellation: CancellationToken | None = None,
        progress_label: str = "",
        usage_role: str = "",
        context_minimum: int | None = None,
        context_maximum: int | None = None,
        context_multiplier: float = 2.0,
        allow_model_context_cap_override: bool = False,
        max_output_tokens: int | None = None,
        max_attempts: int | None = None,
    ) -> StructuredGenerationResult[SchemaT]:
        """Generate non-streamed JSON and validate it against a Pydantic schema."""
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        if max_attempts is not None and max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        token = cancellation or CancellationToken()

        def operation() -> StructuredGenerationResult[SchemaT]:
            generation = self._generate_once(
                prompt,
                system=system,
                model=model,
                think=think,
                stream=True,
                response_format=schema.model_json_schema(),
                on_chunk=None,
                cancellation=token,
                progress_label=progress_label,
                usage_role=usage_role,
                context_minimum=context_minimum,
                context_maximum=context_maximum,
                context_multiplier=context_multiplier,
                allow_model_context_cap_override=allow_model_context_cap_override,
                max_output_tokens=max_output_tokens,
            )
            try:
                value = schema.model_validate_json(
                    strip_structured_output_sentinel(generation.content)
                )
            except ValidationError as error:
                self._emit_progress(
                    GenerationProgressEvent(
                        "validation_failed",
                        model or self.config.model,
                        label=progress_label,
                        message="structured_output",
                        role=usage_role,
                    )
                )
                raise StructuredOutputError(str(error)) from error
            return StructuredGenerationResult(value=value, generation=generation)

        return self._with_retries(
            operation,
            StructuredOutputError,
            cancellation=token,
            retryable_extra=(StructuredOutputError,),
            max_attempts=max_attempts,
        )

    def _generate_once(
        self,
        prompt: str,
        *,
        system: str | None,
        model: str | None,
        think: bool | None,
        stream: bool,
        response_format: dict[str, Any] | None,
        on_chunk: Callable[[str], None] | None,
        cancellation: CancellationToken,
        progress_label: str = "",
        usage_role: str = "",
        context_minimum: int | None = None,
        context_maximum: int | None = None,
        context_multiplier: float = 2.0,
        allow_model_context_cap_override: bool = False,
        max_output_tokens: int | None = None,
    ) -> GenerationResult:
        cancellation.raise_if_cancelled()
        if context_minimum is not None and context_minimum <= 0:
            raise ValueError("context_minimum must be positive")
        if context_maximum is not None and context_maximum <= 0:
            raise ValueError("context_maximum must be positive")
        if context_multiplier <= 0:
            raise ValueError("context_multiplier must be positive")
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        model_name = model or self.config.model
        model_context_cap = (
            None
            if allow_model_context_cap_override
            else self.config.model_num_ctx_caps.get(model_name)
        )
        request_maximum = min(
            context_maximum or self.config.num_ctx,
            self.config.num_ctx,
            model_context_cap or self.config.num_ctx,
        )
        request_minimum = (
            context_minimum if context_minimum is not None else self.config.min_num_ctx
        )
        if model_context_cap is not None:
            request_minimum = min(request_minimum, request_maximum)
        if request_minimum > request_maximum:
            raise ValueError("context_minimum cannot exceed context_maximum")
        prompt_tokens_estimate = estimate_request_tokens(prompt, response_format)
        request_num_ctx = (
            select_request_context(
                prompt,
                maximum=request_maximum,
                minimum=request_minimum,
                response_format=response_format,
                multiplier=context_multiplier,
            )
            if self.config.adaptive_num_ctx
            else request_maximum
        )
        self._emit_progress(
            GenerationProgressEvent(
                "started",
                model_name,
                label=progress_label,
                context_size=request_num_ctx,
                prompt_tokens_estimate=prompt_tokens_estimate,
                role=usage_role,
            )
        )
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        request: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "format": response_format,
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": request_num_ctx,
            },
            "keep_alive": self.config.keep_alive,
        }
        if max_output_tokens is not None:
            request["options"]["num_predict"] = max_output_tokens
        if think is not None:
            request["think"] = think
        request_started_ns = self._clock_ns()
        try:
            response = self._backend.chat(**request)
        except TypeError as error:
            if (
                "think" in request
                and "unexpected keyword argument 'think'" in str(error)
            ):
                raise OllamaUnavailableError(
                    "the installed Ollama Python SDK does not support thinking "
                    "control; reinstall this project to upgrade its dependencies: "
                    "python -m pip install -e ."
                ) from error
            raise
        chunks: Iterator[object] = iter(response) if stream else iter((response,))
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        final_response: object = {}
        generated_chars = 0
        thinking_chars = 0
        generated_tokens_estimate = 0
        last_progress_chars = 0
        last_progress_tokens = 0
        last_progress_ns = request_started_ns
        progress_emitted = False
        first_output_ns: int | None = None
        first_thinking_ns: int | None = None
        first_content_ns: int | None = None
        for chunk in chunks:
            cancellation.raise_if_cancelled()
            error = _value(chunk, "error", None)
            if error:
                raise GenerationError(str(error))
            final_response = chunk
            content = str(_message_value(chunk, "content", "") or "")
            thinking = str(
                _message_value(chunk, "thinking", _value(chunk, "thinking", "")) or ""
            )
            if content or thinking:
                observed_ns = self._clock_ns()
                if first_output_ns is None:
                    first_output_ns = observed_ns
                if thinking and first_thinking_ns is None:
                    first_thinking_ns = observed_ns
                if content and first_content_ns is None:
                    first_content_ns = observed_ns
            if content:
                content_parts.append(content)
                generated_chars += len(content)
                generated_tokens_estimate += _estimate_request_tokens(content)
                if on_chunk:
                    on_chunk(content)
            if thinking:
                thinking_parts.append(thinking)
                thinking_chars += len(thinking)
                generated_tokens_estimate += _estimate_request_tokens(thinking)
            if stream and (content or thinking):
                observed_chars = generated_chars + thinking_chars
                character_due = (
                    self._progress_interval_tokens == 0
                    and self._progress_interval_chars > 0
                    and observed_chars
                    >= last_progress_chars + self._progress_interval_chars
                )
                token_due = (
                    self._progress_interval_tokens > 0
                    and generated_tokens_estimate
                    >= last_progress_tokens + self._progress_interval_tokens
                )
                time_due = (
                    self._progress_interval_ns > 0
                    and observed_ns - last_progress_ns >= self._progress_interval_ns
                )
                if not progress_emitted or token_due or character_due or time_due:
                    self._emit_progress(
                        GenerationProgressEvent(
                            "heartbeat",
                            model_name,
                            generated_chars=generated_chars,
                            thinking_chars=thinking_chars,
                            label=progress_label,
                            role=usage_role,
                            generated_tokens_estimate=generated_tokens_estimate,
                        )
                    )
                    progress_emitted = True
                    last_progress_chars = observed_chars
                    last_progress_tokens = generated_tokens_estimate
                    last_progress_ns = observed_ns

        completed_ns = self._clock_ns()
        visible, legacy_thinking = split_legacy_thinking("".join(content_parts))
        thinking_parts.append(legacy_thinking)
        thinking_chars += len(legacy_thinking)
        thinking_end_ns = first_content_ns or completed_ns
        metrics = GenerationMetrics(
            done_reason=str(_value(final_response, "done_reason", "") or ""),
            total_duration_ns=int(_value(final_response, "total_duration", 0) or 0),
            load_duration_ns=int(_value(final_response, "load_duration", 0) or 0),
            prompt_eval_count=int(_value(final_response, "prompt_eval_count", 0) or 0),
            prompt_eval_duration_ns=int(
                _value(final_response, "prompt_eval_duration", 0) or 0
            ),
            eval_count=int(_value(final_response, "eval_count", 0) or 0),
            eval_duration_ns=int(_value(final_response, "eval_duration", 0) or 0),
            time_to_first_output_ns=(
                max(0, first_output_ns - request_started_ns)
                if first_output_ns is not None
                else 0
            ),
            thinking_duration_ns=(
                max(0, thinking_end_ns - first_thinking_ns)
                if first_thinking_ns is not None
                else 0
            ),
            content_duration_ns=(
                max(0, completed_ns - first_content_ns)
                if first_content_ns is not None
                else 0
            ),
            thinking_chars=thinking_chars,
        )
        self._emit_progress(
            GenerationProgressEvent(
                "completed",
                model_name,
                generated_chars=generated_chars,
                metrics=metrics,
                label=progress_label,
                context_size=request_num_ctx,
                role=usage_role,
                generated_tokens_estimate=generated_tokens_estimate,
            )
        )
        return GenerationResult(
            content=visible,
            thinking="".join(thinking_parts).strip(),
            metrics=metrics,
        )

    def _emit_progress(self, event: GenerationProgressEvent) -> None:
        if self._progress is not None:
            self._progress(event)

    def report_progress(self, event: GenerationProgressEvent) -> None:
        """Publish a safe higher-level generation status through the CLI channel."""
        self._emit_progress(event)

    def _with_retries(
        self,
        operation: Callable[[], ResultT],
        error_type: type[OllamaClientError],
        *,
        cancellation: CancellationToken | None = None,
        retryable_extra: tuple[type[Exception], ...] = (),
        max_attempts: int | None = None,
    ) -> ResultT:
        retryable = (
            ollama.RequestError,
            ollama.ResponseError,
            OSError,
            TimeoutError,
            GenerationError,
        ) + retryable_extra
        attempts = max_attempts or self.config.max_retries + 1
        delay = self.config.retry_initial_seconds
        for attempt in range(attempts):
            if cancellation:
                cancellation.raise_if_cancelled()
            try:
                return operation()
            except (GenerationCancelled, ModelNotFoundError):
                raise
            except retryable as error:
                if attempt == attempts - 1:
                    if isinstance(error, error_type):
                        raise
                    raise error_type(str(error)) from error
                if delay:
                    self._sleep(delay)
                delay = min(delay * 2, self.config.retry_max_seconds)
        raise AssertionError("retry loop exited unexpectedly")
