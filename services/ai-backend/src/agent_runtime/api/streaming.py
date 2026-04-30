"""HTTP streaming adapters for runtime API events."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.agent.contracts import StreamEventSource
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.api.contracts import (
    AgentRunStatus,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)
from agent_runtime.api.service import RuntimeApiService


class RuntimeSseAdapter:
    """Adapt replayable runtime event envelopes to Server-Sent Events."""

    MEDIA_TYPE = "text/event-stream"
    TERMINAL_RUN_STATUSES = RuntimeApiService.TERMINAL_RUN_STATUSES

    @classmethod
    async def stream(
        cls,
        *,
        service: RuntimeApiService,
        org_id: str,
        user_id: str,
        run_id: str,
        after_sequence: int,
    ) -> AsyncIterator[str]:
        """Yield replayed events and an idle heartbeat for non-terminal runs."""

        replay = service.replay_events(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )
        yielded = False
        for event in replay.events:
            yielded = True
            yield cls.format_event(event)
        if not yielded and replay.run_status not in cls.TERMINAL_RUN_STATUSES:
            run = service.get_run(org_id=org_id, user_id=user_id, run_id=run_id)
            payload = {Keys.Payload.MESSAGE: Messages.Event.HEARTBEAT}
            metadata = {"transient": True}
            presentation = RuntimeEventPresentationProjector.presentation_fields(
                event_type=RuntimeApiEventType.HEARTBEAT,
                source=StreamEventSource.SYSTEM,
                parent_task_id=None,
                payload=payload,
                metadata=metadata,
            )
            yield cls.format_event(
                RuntimeEventEnvelope(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    sequence_no=max(1, replay.latest_sequence_no + 1),
                    source=StreamEventSource.SYSTEM,
                    event_type=RuntimeApiEventType.HEARTBEAT,
                    trace_id=run.trace_id,
                    payload=payload,
                    metadata=metadata,
                    **presentation,
                )
            )

    @classmethod
    def format_event(cls, event: RuntimeEventEnvelope) -> str:
        """Return one SSE-framed runtime event."""

        return (
            f"event: {Values.SSE_EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.model_dump_json()}\n\n"
        )
