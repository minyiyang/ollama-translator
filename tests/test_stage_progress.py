
from book_agent.stage_progress import (
    group_by_context_bucket,
    group_by_model_then_context,
    report_segment_result,
    report_stage_plan,
    request_context_bucket,
)


class _ProgressSink:
    def __init__(self) -> None:
        self.events = []

    def report_progress(self, event) -> None:
        self.events.append(event)


class StageProgressTests:
    def test_obfuscated_tasks_are_grouped_stably_by_exact_context_bucket(self) -> None:
        short = "zx_" * 8
        long = "qy_" * 10_000
        small_bucket = request_context_bucket(
            short, minimum=16_384, maximum=65_536
        )
        large_bucket = request_context_bucket(
            long, minimum=16_384, maximum=65_536
        )
        tasks = [
            {"id": "S_z1", "bucket": large_bucket},
            {"id": "S_z2", "bucket": small_bucket},
            {"id": "S_z3", "bucket": small_bucket},
        ]

        ordered = group_by_context_bucket(tasks, lambda item: item["bucket"])

        assert small_bucket < large_bucket
        assert [item["id"] for item in ordered] == ["S_z2", "S_z3", "S_z1"]

    def test_obfuscated_context_selection_respects_role_ceiling(self) -> None:
        capped = request_context_bucket(
            "qz_" * 20_000,
            minimum=16_384,
            maximum=65_536,
            multiplier=8.0,
        )

        assert capped == 65_536

    def test_mixed_model_tasks_are_model_major_then_context_grouped(self) -> None:
        tasks = [
            {"id": "g-large", "model": "gemma", "bucket": 65_536},
            {"id": "d-small", "model": "deepseek", "bucket": 16_384},
            {"id": "g-small", "model": "gemma", "bucket": 16_384},
            {"id": "d-large", "model": "deepseek", "bucket": 65_536},
        ]

        ordered = group_by_model_then_context(
            tasks,
            lambda item: item["model"],
            lambda item: item["bucket"],
        )

        assert [item["id"] for item in ordered] == ["g-small", "g-large", "d-small", "d-large"]

    def test_obfuscated_plan_and_segment_events_have_explicit_results(self) -> None:
        sink = _ProgressSink()
        report_stage_plan(
            sink,
            model="mdl_z",
            stage="audit_z",
            prescreened=7,
            llm_tasks=2,
            skipped=5,
            context_buckets=[16_384, 24_576],
        )
        report_segment_result(
            sink,
            model="mdl_z",
            stage="audit_z",
            segment_id="D_z-S_z",
            result="passed",
            mode="deterministic-prescreen",
            result_index=2,
            result_total=7,
        )

        assert [event.kind for event in sink.events] == ["stage_plan", "segment_result"]
        assert "result=pending" in sink.events[0].message
        assert "context_buckets=16384:1,24576:1" in sink.events[0].message
        assert "result=passed" in sink.events[1].message
        assert sink.events[1].label == "segment=2/7 id=D_z-S_z stage=audit_z"
