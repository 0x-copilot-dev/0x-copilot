"""PRD-A2 D5a — mid-run ``usage.recorded`` emission on the streamed path.

Drives the once-per-``message.id`` emit hook the streaming executor calls after
``MODEL_CALL_COMPLETED``. Asserts: flag-on appends ``usage.recorded`` right after
the completion event; flag-off is byte-identical (only the completion event);
subagent slots map to ``purpose=subagent``; the ``mark_completed`` dedupe means a
duplicate usage chunk emits neither event twice. Fakes only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.observability.attribution import Purpose, UsageAttributionContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.streaming_executor import StreamingExecutor


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        org_id="org_a",
        user_id="user_1",
        conversation_id="conv-1",
        user_message_id="msg-user-1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        trace_id="trace-1",
        runtime_context=AgentRuntimeContext(
            org_id="org_a",
            user_id="user_1",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run-1",
            trace_id="trace-1",
        ),
        started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
    )


def _subagent_context() -> UsageAttributionContext:
    return UsageAttributionContext(
        org_id="org_a",
        user_id="user_1",
        run_id="run-1",
        conversation_id="conv-1",
        trace_id="trace-1",
        purpose=Purpose.SUBAGENT_WORK,
        task_id="task-x",
        subagent_slug="researcher",
    )


class _FakeEventProducer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append_api_event(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class UsageRecordedEmitMixin:
    @staticmethod
    def _metrics_with_usage(
        *,
        message_id: str,
        input_tokens: int,
        output_tokens: int,
        context: UsageAttributionContext | None = None,
    ) -> tuple[AssistantRunMetrics, dict[str, Any]]:
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        chunk = {
            "id": message_id,
            "usage_metadata": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }
        metrics.record_usage_from(chunk, message_id=message_id, context=context)
        return metrics, chunk

    @staticmethod
    def _event_types(producer: _FakeEventProducer) -> list[RuntimeApiEventType]:
        return [call["event_type"] for call in producer.calls]


class TestUsageRecordedEmit(UsageRecordedEmitMixin):
    async def test_flag_on_appends_usage_recorded_after_model_call_completed(
        self,
    ) -> None:
        producer = _FakeEventProducer()
        metrics, chunk = self._metrics_with_usage(
            message_id="msg-a", input_tokens=123, output_tokens=45
        )

        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source=chunk,
            surfaces_v2_enabled=True,
        )

        # Ordering: MODEL_CALL_COMPLETED first, then usage.recorded — never
        # ahead of the completion event.
        assert self._event_types(producer) == [
            RuntimeApiEventType.MODEL_CALL_COMPLETED,
            RuntimeApiEventType.USAGE_RECORDED,
        ]
        usage_call = producer.calls[1]
        assert usage_call["source"] is StreamEventSource.MODEL
        assert usage_call["payload"] == {
            "v": 1,
            "purpose": "run",
            "model": "openai:gpt-5.4-mini",
            "tokens_in": 123,
            "tokens_out": 45,
        }

    async def test_flag_off_event_stream_byte_identical(self) -> None:
        # Snapshot the appended kwargs with the flag off: exactly today's
        # stream — only MODEL_CALL_COMPLETED, no usage.recorded.
        producer = _FakeEventProducer()
        metrics, chunk = self._metrics_with_usage(
            message_id="msg-a", input_tokens=10, output_tokens=20
        )

        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source=chunk,
            surfaces_v2_enabled=False,
        )

        assert self._event_types(producer) == [RuntimeApiEventType.MODEL_CALL_COMPLETED]

    async def test_flag_defaults_off(self) -> None:
        # The kwarg default is False, so an un-updated caller stays byte-identical.
        producer = _FakeEventProducer()
        metrics, chunk = self._metrics_with_usage(
            message_id="msg-a", input_tokens=10, output_tokens=20
        )

        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source=chunk,
        )

        assert self._event_types(producer) == [RuntimeApiEventType.MODEL_CALL_COMPLETED]

    async def test_subagent_chunk_maps_to_purpose_subagent(self) -> None:
        producer = _FakeEventProducer()
        metrics, chunk = self._metrics_with_usage(
            message_id="msg-sub",
            input_tokens=7,
            output_tokens=3,
            context=_subagent_context(),
        )

        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-sub",
            source=chunk,
            surfaces_v2_enabled=True,
        )

        usage_call = producer.calls[1]
        assert usage_call["event_type"] is RuntimeApiEventType.USAGE_RECORDED
        assert usage_call["payload"]["purpose"] == "subagent"

    async def test_duplicate_usage_chunk_emits_once(self) -> None:
        producer = _FakeEventProducer()
        metrics, chunk = self._metrics_with_usage(
            message_id="msg-a", input_tokens=10, output_tokens=20
        )

        for _ in range(2):
            await StreamingExecutor._maybe_emit_model_call_completed(
                run=_run_record(),
                metrics=metrics,
                event_producer=producer,
                message_id="msg-a",
                source=chunk,
                surfaces_v2_enabled=True,
            )

        # mark_completed dedupes: exactly one completion + one usage event.
        assert self._event_types(producer) == [
            RuntimeApiEventType.MODEL_CALL_COMPLETED,
            RuntimeApiEventType.USAGE_RECORDED,
        ]
