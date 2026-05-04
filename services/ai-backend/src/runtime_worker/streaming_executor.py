"""Shared streaming loop used by both run and approval handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_runtime.api.async_ports import AsyncEventStorePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.run_metrics import AssistantRunMetrics, TokenUsageExtractor
from runtime_worker.stream_events import StreamOrchestrator


@dataclass
class StreamingResult:
    """Accumulated result from a streaming execution loop."""

    final_result: object | None = None
    last_chunk: object | None = None
    response_deltas: list[str] = field(default_factory=list)
    subagent_summaries: list[str] = field(default_factory=list)
    saw_task_subagent: bool = False
    action_interrupted: bool = False


class _Fields:
    ACTION_REQUIRED = "action_required"
    CONTENT = "content"
    DELTA = "delta"
    MESSAGE = "message"
    MESSAGE_ID = "message_id"
    PERFORMANCE_METRICS = "performance_metrics"
    USAGE = "usage"
    USAGE_METADATA = "usage_metadata"
    ID = "id"


class _MessageIdExtractor:
    """Pull a stable message id from a stream chunk for B2 dedup.

    Different LangChain providers expose the message id in different
    places: ``chunk.message.id`` (event-stream wrappers), ``chunk.id``
    (AIMessage chunks), or nested under ``data``/``message`` mappings
    when the chunk is a dict. We try each in order and return the first
    non-empty string. Returns ``None`` when no id is available — the
    caller skips per-call recording in that case.
    """

    @classmethod
    def extract(cls, value: object) -> str | None:
        # Plain AIMessage / chunk objects.
        msg_id = getattr(value, _Fields.ID, None)
        if isinstance(msg_id, str) and msg_id:
            return msg_id
        nested = getattr(value, _Fields.MESSAGE, None)
        if nested is not None:
            inner = getattr(nested, _Fields.ID, None)
            if isinstance(inner, str) and inner:
                return inner
        # Mapping-shaped chunks (event-stream envelopes).
        if isinstance(value, Mapping):
            mid = value.get(_Fields.ID)
            if isinstance(mid, str) and mid:
                return mid
            inner_msg = value.get(_Fields.MESSAGE)
            if isinstance(inner_msg, Mapping):
                inner_id = inner_msg.get(_Fields.ID)
                if isinstance(inner_id, str) and inner_id:
                    return inner_id
            data = value.get("data")
            if isinstance(data, Mapping):
                return cls.extract(data)
            output = value.get("output")
            if isinstance(output, Mapping):
                return cls.extract(output)
        return None


class StreamingExecutor:
    """Execute a streaming runtime loop, collecting events and metrics.

    Encapsulates the common streaming pattern shared by run and approval handlers.
    """

    action_interrupt_events = frozenset(
        {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
        }
    )

    @classmethod
    async def run(
        cls,
        *,
        stream: AsyncIterator[object],
        run: RunRecord,
        metrics: AssistantRunMetrics,
        event_store: AsyncEventStorePort,
        event_producer: RuntimeEventProducer,
        stream_event_mapper: StreamOrchestrator,
        track_subagents: bool = False,
    ) -> StreamingResult:
        result = StreamingResult()
        active_subagent_tasks: set[str] = set()
        completed_subagent_tasks: set[str] = set()

        async for chunk in stream:
            result.last_chunk = chunk
            current_task_id = (
                next(iter(active_subagent_tasks)) if active_subagent_tasks else None
            )
            chunk_message_id = _MessageIdExtractor.extract(chunk)
            metrics.record_usage_from(
                chunk, message_id=chunk_message_id, task_id=current_task_id
            )
            await cls._maybe_emit_model_call_completed(
                run=run,
                metrics=metrics,
                event_producer=event_producer,
                message_id=chunk_message_id,
                source=chunk,
            )
            latest_before = await event_store.get_latest_sequence(run_id=run.run_id)
            candidate = stream_event_mapper.stream_result_candidate(chunk)
            if candidate is not None and not active_subagent_tasks:
                result.final_result = candidate
                candidate_id = _MessageIdExtractor.extract(candidate)
                metrics.record_usage_from(
                    candidate, message_id=candidate_id, task_id=current_task_id
                )
                await cls._maybe_emit_model_call_completed(
                    run=run,
                    metrics=metrics,
                    event_producer=event_producer,
                    message_id=candidate_id,
                    source=candidate,
                )
            delta = stream_event_mapper.stream_delta(chunk)
            await stream_event_mapper.append_activity_events(
                run=run,
                chunk=chunk,
                delta=delta,
            )
            new_events = await event_store.list_events_after(
                org_id=run.org_id,
                run_id=run.run_id,
                after_sequence=latest_before,
            )
            for event in new_events:
                if event.event_type in cls.action_interrupt_events:
                    result.action_interrupted = True
                    return result
                if track_subagents:
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_STARTED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.add(event.task_id)
                        result.saw_task_subagent = True
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_COMPLETED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.discard(event.task_id)
                        if event.task_id not in completed_subagent_tasks:
                            completed_subagent_tasks.add(event.task_id)
                            if event.summary:
                                result.subagent_summaries.append(event.summary)
            if delta is None:
                continue
            if not active_subagent_tasks:
                result.response_deltas.append(delta)
            metrics.record_model_delta(delta)
            await event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                payload={_Fields.DELTA: delta, _Fields.MESSAGE: delta},
                summary=delta,
            )
        return result

    @classmethod
    async def _maybe_emit_model_call_completed(
        cls,
        *,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        event_producer: RuntimeEventProducer,
        message_id: str | None,
        source: object,
    ) -> None:
        """Emit ``MODEL_CALL_COMPLETED`` once per AIMessage with usage (B2).

        Idempotent on ``message_id``: subsequent chunks for the same call
        are ignored. The payload carries the slot's accumulated counts —
        which match what the per-call row will store — wrapped in the
        existing ``AssistantPerformanceMetrics`` shape so SSE consumers
        share the same schema as ``RUN_COMPLETED``.
        """

        if message_id is None:
            return
        # Only emit when this chunk actually carries usage — otherwise we'd
        # close the call before the provider has reported tokens.
        if not TokenUsageExtractor.extract(source):
            return
        slot = metrics.per_call.slot(message_id)
        if slot is None:
            return
        completed_at = datetime.now(timezone.utc)
        if not metrics.per_call.mark_completed(message_id, completed_at=completed_at):
            return
        started_at = slot.started_at or completed_at
        duration_ms = AssistantRunMetrics._duration_ms(started_at, completed_at)
        usage_payload = {
            "input": slot.input_tokens,
            "output": slot.output_tokens,
            "cached_input": slot.cached_input_tokens,
            "total": slot.total_tokens,
        }
        await event_producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_CALL_COMPLETED,
            payload={
                _Fields.MESSAGE_ID: message_id,
                _Fields.PERFORMANCE_METRICS: {
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": duration_ms,
                    _Fields.USAGE: usage_payload,
                },
            },
        )

    @classmethod
    def compose_final(cls, result: StreamingResult) -> object:
        if result.action_interrupted:
            return {_Fields.ACTION_REQUIRED: True}
        if result.final_result is not None:
            return result.final_result
        if result.response_deltas:
            return {_Fields.CONTENT: "".join(result.response_deltas)}
        if result.saw_task_subagent and result.subagent_summaries:
            return {_Fields.CONTENT: "\n\n".join(result.subagent_summaries)}
        return result.last_chunk
