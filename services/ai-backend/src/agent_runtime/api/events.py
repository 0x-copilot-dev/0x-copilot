"""Runtime event producer helpers shared by API producers and workers."""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.execution.contracts import JsonObject, StreamEvent, StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort


class RuntimeEventProducer:
    """Append redacted, ordered, UI-ready event envelopes through typed ports."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store

    def append_api_event(
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
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=source,
            parent_task_id=parent_task_id,
            payload=safe_payload,
            metadata=safe_metadata,
        )
        if summary is not None and (safe_summary := summary.strip()):
            presentation["summary"] = safe_summary
        if status is not None and (safe_status := status.strip()):
            presentation["status"] = safe_status
        envelope = self.event_store.append_event(
            RuntimeEventDraft(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                source=source,
                event_type=event_type,
                trace_id=run.trace_id,
                parent_task_id=parent_task_id,
                payload=safe_payload,
                metadata=safe_metadata,
                **presentation,
            )
        )
        self.persistence.set_run_latest_sequence(
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        return envelope

    def append_stream_event(
        self,
        *,
        run: RunRecord,
        stream_event: StreamEvent,
    ) -> RuntimeEventEnvelope:
        """Append a normalized runtime event after projecting UI timeline fields."""

        envelope = self.event_store.append_event(
            RuntimeEventDraft.from_stream_event(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                stream_event=stream_event,
            )
        )
        self.persistence.set_run_latest_sequence(
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        return envelope

    def append_stream_events(
        self,
        *,
        run: RunRecord,
        stream_events: Sequence[StreamEvent],
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append normalized runtime events in order for a worker batch."""

        return tuple(
            self.append_stream_event(run=run, stream_event=event)
            for event in stream_events
        )
