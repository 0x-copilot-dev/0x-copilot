"""HTTP streaming adapters for runtime API events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.api.conversation_query_service import ConversationQueryService
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)
from runtime_api.sse.event_bus import RuntimeEventBus


class RuntimeSseAdapter:
    """Adapt replayable runtime event envelopes to Server-Sent Events."""

    MEDIA_TYPE = "text/event-stream"
    TERMINAL_RUN_STATUSES = ConversationQueryService.TERMINAL_RUN_STATUSES
    FALLBACK_POLL_SECONDS = 2.0

    @classmethod
    async def stream(
        cls,
        *,
        service: ConversationQueryService,
        org_id: str,
        user_id: str,
        run_id: str,
        after_sequence: int,
        follow: bool = False,
        event_bus: RuntimeEventBus | None = None,
    ) -> AsyncIterator[str]:
        """Yield replayed events, waking on push notifications from the event bus."""

        latest_sequence = after_sequence
        while True:
            replay = await service.replay_events(
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                after_sequence=latest_sequence,
            )
            for event in replay.events:
                latest_sequence = max(latest_sequence, event.sequence_no)
                yield cls.format_event(event)
            if replay.run_status in cls.TERMINAL_RUN_STATUSES:
                if event_bus is not None:
                    event_bus.unsubscribe(run_id)
                return
            if not follow:
                if not replay.events:
                    yield await cls.heartbeat_event(
                        service=service,
                        org_id=org_id,
                        user_id=user_id,
                        run_id=run_id,
                        sequence_no=max(1, replay.latest_sequence_no + 1),
                    )
                if event_bus is not None:
                    event_bus.unsubscribe(run_id)
                return
            if event_bus is not None:
                await event_bus.wait(run_id, timeout=cls.FALLBACK_POLL_SECONDS)
            else:
                await asyncio.sleep(cls.FALLBACK_POLL_SECONDS)

    @classmethod
    async def heartbeat_event(
        cls,
        *,
        service: ConversationQueryService,
        org_id: str,
        user_id: str,
        run_id: str,
        sequence_no: int,
    ) -> str:
        """Build and return a synthetic heartbeat SSE frame for the given run."""
        run = await service.get_run(org_id=org_id, user_id=user_id, run_id=run_id)
        payload = {Keys.Payload.MESSAGE: Messages.Event.HEARTBEAT}
        metadata = {"transient": True}
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=RuntimeApiEventType.HEARTBEAT,
            source=StreamEventSource.SYSTEM,
            parent_task_id=None,
            payload=payload,
            metadata=metadata,
        )
        return cls.format_event(
            RuntimeEventEnvelope(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                sequence_no=sequence_no,
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
