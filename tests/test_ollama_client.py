from typing import Any

import ollama
import pytest
from pydantic import BaseModel

from book_agent.config import OllamaConfig
from book_agent.ollama_client import (
    CancellationToken,
    ContextWindowError,
    GenerationCancelled,
    GenerationError,
    GenerationProgressEvent,
    ModelNotFoundError,
    OllamaClient,
    OllamaUnavailableError,
    StructuredOutputError,
    split_legacy_thinking,
    strip_structured_output_sentinel,
)


class ExampleSchema(BaseModel):
    name: str
    count: int


class FakeBackend:
    def __init__(self) -> None:
        self.list_responses: list[Any] = [{"models": []}]
        self.show_responses: list[Any] = []
        self.chat_responses: list[Any] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.list_calls = 0
        self.show_calls: list[str] = []

    @staticmethod
    def _next(responses: list[Any]) -> Any:
        if not responses:
            raise AssertionError("fake response queue is empty")
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def list(self) -> Any:
        self.list_calls += 1
        return self._next(self.list_responses)

    def show(self, model: str) -> Any:
        self.show_calls.append(model)
        return self._next(self.show_responses)

    def chat(self, **kwargs: Any) -> Any:
        self.chat_calls.append(kwargs)
        return self._next(self.chat_responses)


class LegacyThinkBackend(FakeBackend):
    """Models the ollama 0.4.x chat signature, which predates ``think``."""

    def chat(  # type: ignore[override]
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        format: dict[str, Any] | None,
        options: dict[str, Any],
        keep_alive: str,
    ) -> Any:
        return super().chat(
            model=model,
            messages=messages,
            stream=stream,
            format=format,
            options=options,
            keep_alive=keep_alive,
        )


def config(**overrides: Any) -> OllamaConfig:
    values = {
        "max_retries": 0,
        "retry_initial_seconds": 0,
        "retry_max_seconds": 0,
    }
    values.update(overrides)
    return OllamaConfig.model_validate(values)


class ThinkingTests:
    def test_split_legacy_thinking_extracts_multiple_blocks(self) -> None:
        visible, thinking = split_legacy_thinking(
            "<think>first</think>Hello <think>second</think>world"
        )
        assert visible == "Hello world"
        assert thinking == "first\nsecond"

    def test_split_legacy_thinking_leaves_plain_content(self) -> None:
        assert split_legacy_thinking("answer") == ("answer", "")

    def test_structured_sentinel_is_removed_only_after_complete_json(self) -> None:
        assert strip_structured_output_sentinel('{"count":1}<|eot|>') == '{"count":1}'
        assert strip_structured_output_sentinel('unfinished<|eot|>') == 'unfinished<|eot|>'


class CancellationTokenTests:
    def test_token_starts_active_and_can_be_cancelled(self) -> None:
        token = CancellationToken()
        assert not token.is_cancelled
        token.raise_if_cancelled()
        token.cancel()
        assert token.is_cancelled
        with pytest.raises(GenerationCancelled):
            token.raise_if_cancelled()


class OllamaClientTests:
    def setup_method(self) -> None:
        self.backend = FakeBackend()
        self.client = OllamaClient(config(), backend=self.backend)

    def test_health_check_true(self) -> None:
        assert self.client.health_check()

    def test_legacy_sdk_think_error_has_upgrade_instructions(self) -> None:
        client = OllamaClient(config(), backend=LegacyThinkBackend())
        with pytest.raises(OllamaUnavailableError, match=r"python -m pip install -e \."):
            client.generate_text("hello", think=False)

    def test_health_check_false_on_request_error(self) -> None:
        self.backend.list_responses = [ollama.RequestError("offline")]
        assert not self.client.health_check()

    def test_list_model_names_supports_model_and_name_fields(self) -> None:
        self.backend.list_responses = [
            {"models": [{"model": "qwen3.8:latest"}, {"name": "other:latest"}]}
        ]
        assert self.client.list_model_names() == ["qwen3.8:latest", "other:latest"]

    def test_list_model_names_retries_with_exponential_delays(self) -> None:
        backend = FakeBackend()
        backend.list_responses = [
            ollama.RequestError("temporary"),
            ollama.RequestError("temporary"),
            {"models": []},
        ]
        sleeps: list[float] = []
        client = OllamaClient(
            config(max_retries=2, retry_initial_seconds=1, retry_max_seconds=2),
            backend=backend,
            sleeper=sleeps.append,
        )
        assert client.list_model_names() == []
        assert sleeps == [1, 2]

    def test_list_model_names_wraps_exhausted_error(self) -> None:
        self.backend.list_responses = [ollama.RequestError("offline")]
        with pytest.raises(OllamaUnavailableError, match="offline"):
            self.client.list_model_names()

    def test_inspect_model_normalizes_legacy_response(self) -> None:
        self.backend.show_responses = [
            {
                "modelinfo": {"qwen35.context_length": 262_144},
                "details": {
                    "family": "qwen35",
                    "parameter_size": "27.3B",
                    "quantization_level": "Q4_K_M",
                },
            }
        ]
        info = self.client.inspect_model()
        assert info.name == "qwen3.8:latest"
        assert info.context_length == 262_144
        assert info.family == "qwen35"
        assert info.parameter_size == "27.3B"
        assert info.quantization == "Q4_K_M"

    def test_inspect_model_normalizes_modern_response(self) -> None:
        self.backend.show_responses = [
            {
                "model_info": {"family.context_length": 131_072},
                "capabilities": ["completion", "thinking", "tools"],
            }
        ]
        info = self.client.inspect_model("modern:latest")
        assert info.context_length == 131_072
        assert info.capabilities == ("completion", "thinking", "tools")
        assert self.backend.show_calls == ["modern:latest"]

    def test_inspect_model_preserves_not_found_error(self) -> None:
        self.backend.show_responses = [ollama.ResponseError("missing", 404)]
        with pytest.raises(ModelNotFoundError, match="not installed"):
            self.client.inspect_model()

    def test_validate_model_context_accepts_sufficient_model(self) -> None:
        self.backend.show_responses = [
            {"modelinfo": {"qwen35.context_length": 262_144}}
        ]
        assert self.client.validate_model_context(131_072).context_length == 262_144

    def test_validate_model_context_rejects_small_model(self) -> None:
        self.backend.show_responses = [
            {"modelinfo": {"qwen35.context_length": 32_768}}
        ]
        with pytest.raises(ContextWindowError, match="below"):
            self.client.validate_model_context(100_000)

    def test_validate_model_context_requires_positive_value(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            self.client.validate_model_context(0)

    def test_generate_nonstreamed_text_and_metrics(self) -> None:
        self.backend.chat_responses = [
            {
                "message": {"content": "answer", "thinking": "reason"},
                "done_reason": "stop",
                "total_duration": 10,
                "load_duration": 2,
                "prompt_eval_count": 30,
                "eval_count": 12,
            }
        ]
        result = self.client.generate_text(
            "prompt", system="system", stream=False
        )
        assert result.content == "answer"
        assert result.thinking == "reason"
        assert result.metrics.done_reason == "stop"
        assert result.metrics.eval_count == 12
        call = self.backend.chat_calls[0]
        assert call["messages"][0] == {"role": "system", "content": "system"}
        assert call["options"]["num_ctx"] == 16_384
        assert not call["stream"]

    def test_adaptive_context_grows_for_large_prompts(self) -> None:
        self.backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        self.client.generate_text("x" * 100_000, stream=False)
        assert self.backend.chat_calls[0]["options"]["num_ctx"] == 57_344

    def test_request_can_use_smaller_context_minimum(self) -> None:
        self.backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        self.client.generate_text("short repair", stream=False, context_minimum=8_192)
        assert self.backend.chat_calls[0]["options"]["num_ctx"] == 8_192

    def test_request_context_maximum_caps_adaptive_context(self) -> None:
        self.backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        self.client.generate_text(
            "x" * 400_000,
            stream=False,
            context_maximum=65_536,
        )
        assert self.backend.chat_calls[0]["options"]["num_ctx"] == 65_536

    def test_model_context_cap_overrides_global_minimum(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        client = OllamaClient(
            config(model_num_ctx_caps={"gemma4:31b": 8_192}),
            backend=backend,
        )
        client.generate_text("short audit", model="gemma4:31b", stream=False)
        assert backend.chat_calls[0]["options"]["num_ctx"] == 8_192

    def test_model_context_cap_does_not_affect_other_models(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        client = OllamaClient(
            config(model_num_ctx_caps={"gemma4:31b": 8_192}),
            backend=backend,
        )
        client.generate_text("short translation", model="qwen3.8:latest", stream=False)
        assert backend.chat_calls[0]["options"]["num_ctx"] == 16_384

    def test_explicit_request_can_override_model_context_cap(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        client = OllamaClient(
            config(model_num_ctx_caps={"gemma4:31b": 8_192}),
            backend=backend,
        )
        client.generate_text(
            "short overflow audit",
            model="gemma4:31b",
            stream=False,
            context_maximum=16_384,
            allow_model_context_cap_override=True,
        )
        assert backend.chat_calls[0]["options"]["num_ctx"] == 16_384

    def test_request_context_multiplier_increases_completion_headroom(self) -> None:
        self.backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        self.client.generate_text(
            "x" * 100_000,
            stream=False,
            context_multiplier=3.5,
        )
        assert self.backend.chat_calls[0]["options"]["num_ctx"] == 98_304

    def test_request_context_minimum_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="context_minimum"):
            self.client.generate_text("repair", context_minimum=0)

    def test_request_context_multiplier_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="context_multiplier"):
            self.client.generate_text("repair", context_multiplier=0)

    def test_adaptive_context_can_be_disabled(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            {"message": {"content": "answer"}, "done_reason": "stop"}
        ]
        client = OllamaClient(
            config(adaptive_num_ctx=False), backend=backend, sleeper=lambda _: None
        )
        client.generate_text("prompt", stream=False)
        assert backend.chat_calls[0]["options"]["num_ctx"] == 131_072

    def test_generate_streamed_text_callbacks_and_legacy_thinking(self) -> None:
        self.backend.chat_responses = [
            iter(
                [
                    {"message": {"content": "<think>reason</think>Hel"}},
                    {
                        "message": {"content": "lo"},
                        "done_reason": "stop",
                        "eval_count": 2,
                    },
                ]
            )
        ]
        chunks: list[str] = []
        result = self.client.generate_text("prompt", on_chunk=chunks.append)
        assert result.content == "Hello"
        assert result.thinking == "reason"
        assert chunks == ["<think>reason</think>Hel", "lo"]
        assert result.metrics.eval_count == 2

    def test_generation_progress_reports_immediate_heartbeat_and_exact_metrics(self) -> None:
        events: list[GenerationProgressEvent] = []
        backend = FakeBackend()
        backend.chat_responses = [
            iter(
                [
                    {"message": {"content": "a" * 1_500}},
                    {
                        "message": {"content": "b" * 600},
                        "done_reason": "stop",
                        "total_duration": 5_000_000_000,
                        "prompt_eval_count": 120,
                        "prompt_eval_duration": 1_000_000_000,
                        "eval_count": 50,
                        "eval_duration": 4_000_000_000,
                    },
                ]
            )
        ]
        client = OllamaClient(config(), backend=backend, progress=events.append)
        result = client.generate_text(
            "prompt", model="translator", progress_label="attempt=1/4"
        )
        assert [event.kind for event in events] == ["started", "heartbeat", "completed"]
        assert events[0].prompt_tokens_estimate == 2
        assert events[0].context_size == 16_384
        assert events[1].generated_chars == 1_500
        assert events[2].metrics.eval_duration_ns == 4_000_000_000
        assert all(event.label == "attempt=1/4" for event in events)
        assert result.metrics.prompt_eval_duration_ns == 1_000_000_000

    def test_structured_generation_propagates_progress_label(self) -> None:
        events: list[GenerationProgressEvent] = []
        backend = FakeBackend()
        backend.chat_responses = [
            iter([{"message": {"content": '{"name":"valid","count":1}'}}])
        ]
        client = OllamaClient(config(), backend=backend, progress=events.append)
        client.generate_structured(
            "prompt", ExampleSchema, progress_label="chunk=2/5 id=glossary-00002"
        )
        assert all(event.label == "chunk=2/5 id=glossary-00002" for event in events)

    def test_stream_records_observed_thinking_and_content_phase_times(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            iter(
                [
                    {"message": {"thinking": "reason"}},
                    {"message": {"content": "answer"}, "done_reason": "stop"},
                ]
            )
        ]
        times = iter([0, 2_000_000_000, 5_000_000_000, 9_000_000_000])
        client = OllamaClient(config(), backend=backend, clock_ns=lambda: next(times))
        result = client.generate_text("prompt", think=True)
        assert result.metrics.time_to_first_output_ns == 2_000_000_000
        assert result.metrics.thinking_duration_ns == 3_000_000_000
        assert result.metrics.content_duration_ns == 4_000_000_000
        assert result.metrics.thinking_chars == 6

    def test_generation_progress_interval_rejects_negative_value(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            OllamaClient(config(), backend=FakeBackend(), progress_interval_chars=-1)
        with pytest.raises(ValueError, match="cannot be negative"):
            OllamaClient(
                config(), backend=FakeBackend(), progress_interval_seconds=-1
            )
        with pytest.raises(ValueError, match="cannot be negative"):
            OllamaClient(
                config(), backend=FakeBackend(), progress_interval_tokens=-1
            )

    def test_generation_progress_propagates_usage_role(self) -> None:
        events: list[GenerationProgressEvent] = []
        backend = FakeBackend()
        backend.chat_responses = [
            iter([{"message": {"content": "done"}, "done_reason": "stop"}])
        ]
        client = OllamaClient(config(), backend=backend, progress=events.append)
        client.generate_text("prompt", usage_role="audit.semantic")
        assert all(event.role == "audit.semantic" for event in events)

    def test_generation_heartbeat_uses_estimated_output_token_interval(self) -> None:
        events: list[GenerationProgressEvent] = []
        backend = FakeBackend()
        backend.chat_responses = [
            iter(
                [
                    {"message": {"content": "abcd"}},
                    {"message": {"content": "efgh"}},
                    {"message": {"content": "ijkl"}, "done_reason": "stop"},
                ]
            )
        ]
        client = OllamaClient(
            config(),
            backend=backend,
            progress=events.append,
            progress_interval_seconds=0,
            progress_interval_tokens=2,
        )
        client.generate_text("prompt")
        heartbeats = [event for event in events if event.kind == "heartbeat"]
        assert len(heartbeats) == 2
        assert heartbeats[0].generated_tokens_estimate == 1
        assert heartbeats[1].generated_tokens_estimate == 3

    def test_generation_progress_heartbeats_are_time_bounded_and_include_thinking(self) -> None:
        events: list[GenerationProgressEvent] = []
        backend = FakeBackend()
        backend.chat_responses = [
            iter(
                [
                    {"message": {"thinking": "first"}},
                    {"message": {"thinking": "second"}},
                    {"message": {"content": "answer"}, "done_reason": "stop"},
                ]
            )
        ]
        times = iter(
            [
                0,
                100_000_000,
                1_000_000_000,
                2_200_000_000,
                2_300_000_000,
            ]
        )
        client = OllamaClient(
            config(progress_interval_seconds=2.0, progress_interval_chars=10_000),
            backend=backend,
            progress=events.append,
            clock_ns=lambda: next(times),
        )
        client.generate_text("prompt", think=True)
        heartbeats = [event for event in events if event.kind == "heartbeat"]
        assert len(heartbeats) == 2
        assert heartbeats[0].thinking_chars == 5
        assert heartbeats[0].generated_chars == 0
        assert heartbeats[1].thinking_chars == 11
        assert heartbeats[1].generated_chars == 6

    def test_generate_detects_midstream_error_object(self) -> None:
        self.backend.chat_responses = [
            iter([{"message": {"content": "partial"}}, {"error": "GPU failure"}])
        ]
        with pytest.raises(GenerationError, match="GPU failure"):
            self.client.generate_text("prompt")

    def test_generate_retries_request_error(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            ollama.RequestError("temporary"),
            {"message": {"content": "recovered"}},
        ]
        client = OllamaClient(
            config(max_retries=1), backend=backend, sleeper=lambda _: None
        )
        assert client.generate_text("prompt", stream=False).content == "recovered"
        assert len(backend.chat_calls) == 2

    def test_generate_wraps_timeout(self) -> None:
        self.backend.chat_responses = [TimeoutError("too slow")]
        with pytest.raises(GenerationError, match="too slow"):
            self.client.generate_text("prompt", stream=False)

    def test_generate_rejects_empty_prompt(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            self.client.generate_text("  ")

    def test_generate_honors_precancelled_token_without_backend_call(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(GenerationCancelled):
            self.client.generate_text("prompt", cancellation=token)
        assert self.backend.chat_calls == []

    def test_generate_honors_cancellation_during_stream(self) -> None:
        token = CancellationToken()
        self.backend.chat_responses = [
            iter([{"message": {"content": "first"}}, {"message": {"content": "second"}}])
        ]

        def cancel_after_first(_: str) -> None:
            token.cancel()

        with pytest.raises(GenerationCancelled):
            self.client.generate_text(
                "prompt", cancellation=token, on_chunk=cancel_after_first
            )

    def test_generate_structured_validates_schema_and_uses_format(self) -> None:
        self.backend.chat_responses = [
            iter([{"message": {"content": '{"name":"Aster","count":2}'}}])
        ]
        result = self.client.generate_structured("extract", ExampleSchema, think=False)
        assert result.value == ExampleSchema(name="Aster", count=2)
        call = self.backend.chat_calls[0]
        assert call["stream"]
        assert not call["think"]
        assert call["format"]["type"] == "object"

    def test_generate_structured_accepts_terminal_model_sentinel(self) -> None:
        self.backend.chat_responses = [
            iter(
                [
                    {
                        "message": {
                            "content": '{"name":"Aster","count":2}<|eot|>'
                        }
                    }
                ]
            )
        ]
        result = self.client.generate_structured("extract", ExampleSchema)
        assert result.value.count == 2

    def test_generate_structured_caps_obfuscated_output_tokens(self) -> None:
        self.backend.chat_responses = [
            iter([{"message": {"content": '{"name":"Vrax","count":3}'}}])
        ]
        result = self.client.generate_structured(
            "obfuscated extraction", ExampleSchema, max_output_tokens=321
        )
        assert result.value.name == "Vrax"
        assert self.backend.chat_calls[0]["options"]["num_predict"] == 321
        with pytest.raises(ValueError, match="max_output_tokens"):
            self.client.generate_structured(
                "obfuscated extraction", ExampleSchema, max_output_tokens=0
            )

    def test_generate_structured_honors_single_obfuscated_attempt(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            iter([{"message": {"content": '{"name":"Vrax"'}}]),
            iter([{"message": {"content": '{"name":"Zorb","count":2}'}}]),
        ]
        events = []
        client = OllamaClient(
            config(max_retries=3),
            backend=backend,
            sleeper=lambda _: None,
            progress=events.append,
        )
        with pytest.raises(StructuredOutputError):
            client.generate_structured(
                "obfuscated extraction", ExampleSchema, max_attempts=1
            )
        assert len(backend.chat_calls) == 1
        assert (any(
                event.kind == "validation_failed"
                and event.message == "structured_output"
                for event in events
            ))
        with pytest.raises(ValueError, match="max_attempts"):
            client.generate_structured(
                "obfuscated extraction", ExampleSchema, max_attempts=0
            )

    def test_generate_structured_retries_invalid_output(self) -> None:
        backend = FakeBackend()
        backend.chat_responses = [
            iter([{"message": {"content": "invalid"}}]),
            iter([{"message": {"content": '{"name":"Aster","count":1}'}}]),
        ]
        client = OllamaClient(
            config(max_retries=1), backend=backend, sleeper=lambda _: None
        )
        assert client.generate_structured("extract", ExampleSchema).value.count == 1

    def test_generate_structured_raises_after_invalid_output(self) -> None:
        self.backend.chat_responses = [iter([{"message": {"content": "invalid"}}])]
        with pytest.raises(StructuredOutputError):
            self.client.generate_structured("extract", ExampleSchema)

    def test_generate_structured_rejects_empty_prompt(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            self.client.generate_structured(" ", ExampleSchema)

