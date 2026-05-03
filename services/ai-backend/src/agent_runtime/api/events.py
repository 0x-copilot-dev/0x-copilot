"""Runtime event producer helpers shared by API producers and workers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from agent_runtime.execution.contracts import JsonObject, StreamEvent, StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.presentation import PresentationGenerator


class RuntimeEventProducer:
    """Append redacted, ordered, UI-ready event envelopes through typed ports."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        presentation_generator: PresentationGenerator | None = None,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.presentation_generator = presentation_generator or PresentationGenerator()
        self._on_event_appended = on_event_appended

    async def append_api_event(
        self,
        *,
        run: RunRecord,
        source: StreamEventSource,
        event_type: RuntimeApiEventType,
        payload: JsonObject | None = None,
        metadata: JsonObject | None = None,
        parent_task_id: str | None = None,
        summary: str | None = None,
        status: str | None = None,
    ) -> RuntimeEventEnvelope:
        """Append an API-authored event and update the run sequence cursor."""

        safe_payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=payload or {},
        )
        safe_metadata = metadata or {}
        timeline_fields = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=source,
            parent_task_id=parent_task_id,
            payload=safe_payload,
            metadata=safe_metadata,
        )
        if summary is not None and (safe_summary := summary.strip()):
            timeline_fields["summary"] = safe_summary
        if status is not None and (safe_status := status.strip()):
            timeline_fields["status"] = safe_status
        card_presentation = await self.presentation_generator.presentation_for_event(
            run=run,
            event_type=event_type,
            source=source,
            payload=safe_payload,
            metadata=safe_metadata,
            timeline_fields=timeline_fields,
        )
        if card_presentation is not None:
            safe_metadata = {**safe_metadata, "presentation": card_presentation}
        draft = RuntimeEventDraft(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            source=source,
            event_type=event_type,
            trace_id=run.trace_id,
            parent_task_id=parent_task_id,
            payload=safe_payload,
            metadata=safe_metadata,
            presentation=card_presentation,
            **timeline_fields,
        )
        envelope = await asyncio.to_thread(self.event_store.append_event, draft)
        await asyncio.to_thread(
            self.persistence.set_run_latest_sequence,
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)
        return envelope

    async def append_stream_event(
        self,
        *,
        run: RunRecord,
        stream_event: StreamEvent,
    ) -> RuntimeEventEnvelope:
        """Append a normalized runtime event after projecting UI timeline fields."""

        draft = RuntimeEventDraft.from_stream_event(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            stream_event=stream_event,
        )
        card_presentation = await self.presentation_generator.presentation_for_event(
            run=run,
            event_type=draft.event_type,
            source=draft.source,
            payload=draft.payload,
            metadata=draft.metadata,
            timeline_fields=draft.model_dump(
                mode="python",
                include={
                    "display_title",
                    "summary",
                    "status",
                    "activity_kind",
                    "span_id",
                },
            ),
        )
        if card_presentation is not None:
            draft = draft.model_copy(
                update={
                    "presentation": card_presentation,
                    "metadata": {**draft.metadata, "presentation": card_presentation},
                }
            )
        envelope = await asyncio.to_thread(self.event_store.append_event, draft)
        await asyncio.to_thread(
            self.persistence.set_run_latest_sequence,
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)
        return envelope

    async def append_stream_events(
        self,
        *,
        run: RunRecord,
        stream_events: Sequence[StreamEvent],
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append normalized runtime events in order for a worker batch."""

        envelopes: list[RuntimeEventEnvelope] = []
        for event in stream_events:
            envelopes.append(
                await self.append_stream_event(run=run, stream_event=event)
            )
        return tuple(envelopes)
