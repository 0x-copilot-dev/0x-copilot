"""Shared streaming loop used by both run and approval handlers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agent_runtime.api.async_ports import AsyncEventStorePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.run_metrics import AssistantRunMetrics
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
            metrics.record_usage_from(chunk)
            latest_before = await event_store.get_latest_sequence(run_id=run.run_id)
            candidate = stream_event_mapper.stream_result_candidate(chunk)
            if candidate is not None and not active_subagent_tasks:
                result.final_result = candidate
                metrics.record_usage_from(candidate)
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
