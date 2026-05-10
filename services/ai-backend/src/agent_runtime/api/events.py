"""Runtime event producer helpers shared by API producers and workers.

Polish-removal Phase 4 (docs/refactor/01-presentation-polish-removal.md):
the producer is now fully synchronous. ``append_*_event`` builds the
deterministic preliminary presentation, persists the event, and notifies
SSE subscribers. There is no background polish task, no
``PRESENTATION_UPDATED`` follow-up envelope, no ``agent_intent_hint``
buffer — the deterministic chain in :class:`PresentationGenerator` plus
the agent-supplied ``_display_*`` fields (Phase 3) cover every case the
polish LLM used to handle.

The ``PRESENTATION_UPDATED`` enum value is preserved on
``RuntimeApiEventType`` for replay compatibility with old persisted runs
(see PRD §6.5). New runs never emit it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.execution.contracts import JsonObject, StreamEvent, StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class RuntimeEventProducer:
    """Append redacted, ordered, UI-ready event envelopes through async ports.

    The producer's hot path is uniformly async. The constructor normalizes
    incoming ports: an async store goes through directly, a sync store is
    wrapped via ``adapt_*_to_async`` which bridges each call through
    ``asyncio.to_thread``. Callers therefore don't have to know which kind
    of port they're holding.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        presentation_generator: PresentationGenerator | None = None,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
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
        subagent_id: str | None = None,
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
            subagent_id=subagent_id,
        )
        if summary is not None and (safe_summary := summary.strip()):
            timeline_fields["summary"] = safe_summary
        if status is not None and (safe_status := status.strip()):
            timeline_fields["status"] = safe_status

        preliminary = self.presentation_generator.preliminary_presentation_for_event(
            event_type=event_type,
            payload=safe_payload,
            metadata=safe_metadata,
            timeline_fields=timeline_fields,
        )
        draft_metadata = (
            {**safe_metadata, "presentation": preliminary}
            if preliminary is not None
            else safe_metadata
        )
        draft = RuntimeEventDraft(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            source=source,
            event_type=event_type,
            trace_id=run.trace_id,
            parent_task_id=parent_task_id,
            payload=safe_payload,
            metadata=draft_metadata,
            presentation=preliminary,
            **timeline_fields,
        )
        envelope = await self.event_store.append_event(draft)
        await self.persistence.set_run_latest_sequence(
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
        timeline_fields = draft.model_dump(
            mode="python",
            include={
                "display_title",
                "summary",
                "status",
                "activity_kind",
                "span_id",
            },
        )
        preliminary = self.presentation_generator.preliminary_presentation_for_event(
            event_type=draft.event_type,
            payload=draft.payload,
            metadata=draft.metadata,
            timeline_fields=timeline_fields,
        )
        if preliminary is not None:
            draft = draft.model_copy(
                update={
                    "presentation": preliminary,
                    "metadata": {
                        **draft.metadata,
                        "presentation": preliminary,
                    },
                }
            )
        envelope = await self.event_store.append_event(draft)
        await self.persistence.set_run_latest_sequence(
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)
        return envelope

    async def append_compression_note(
        self,
        *,
        run: RunRecord,
        before_tokens: int,
        after_tokens: int,
        strategy: str,
        summary: str | None = None,
        payload_refs: Mapping[str, object] | None = None,
        metadata: JsonObject | None = None,
    ) -> RuntimeEventEnvelope:
        """Emit a ``COMPRESSION_NOTE`` envelope for the chat NoteCard.

        Called from the memory-compression hook when the context window
        manager redacts older messages mid-run. ``strategy`` mirrors
        ``ContextCompressionStrategy`` values (``summarize``, ``offload``,
        etc.) so the FE can branch its NoteCard copy if it needs to. The
        ``payload_refs`` slot lets callers attach offload/file refs without
        leaking content into the prompt path.
        """

        if before_tokens < 0 or after_tokens < 0:
            raise ValueError("before_tokens and after_tokens must be non-negative")
        if after_tokens > before_tokens:
            raise ValueError("after_tokens must not exceed before_tokens")
        clean_strategy = strategy.strip()
        if not clean_strategy:
            raise ValueError("strategy must be a non-empty string")
        payload: JsonObject = {
            "before_tokens": int(before_tokens),
            "after_tokens": int(after_tokens),
            "strategy": clean_strategy,
        }
        if summary is not None and summary.strip():
            payload["summary"] = summary.strip()
        if payload_refs:
            payload["payload_refs"] = dict(payload_refs)
        return await self.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.COMPRESSION_NOTE,
            payload=payload,
            metadata=metadata or {},
        )

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
