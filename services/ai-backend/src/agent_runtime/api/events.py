"""Runtime event producer: append redacted, ordered, UI-ready event envelopes.

The producer is fully synchronous — ``append_*_event`` builds the deterministic
presentation, persists the event, and notifies SSE subscribers in one step.
There is no background polish task or follow-up envelope; deterministic
presentation generation covers every case. The ``PRESENTATION_UPDATED`` enum
value is preserved on ``RuntimeApiEventType`` for replay compatibility with
old persisted runs. New runs never emit it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.execution.contracts import JsonObject, StreamEvent, StreamEventSource
from agent_runtime.observability.lifecycle_ledger import (
    LifecycleEventInspector,
    LifecycleLedger,
)
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class RuntimeEventProducer:
    """Append redacted, ordered, UI-ready event envelopes.

    The producer's hot path is uniformly async; ports are async-native
    so every persistence call awaits directly without bridging.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        presentation_generator: PresentationGenerator | None = None,
        on_event_appended: Callable[[str], None] | None = None,
        lifecycle_ledger: LifecycleLedger | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.presentation_generator = presentation_generator or PresentationGenerator()
        self._on_event_appended = on_event_appended
        # Single source of truth for "what is currently in flight on this run."
        # The producer owns the ledger; the run handler reads it via
        # :attr:`lifecycle_ledger` to drive RunTerminationCoordinator
        # reconciliation. Default-constructed so callers that don't care
        # (tests of the producer in isolation) get the invariant for free.
        self.lifecycle_ledger: LifecycleLedger = (
            lifecycle_ledger if lifecycle_ledger is not None else LifecycleLedger()
        )

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
        event_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RuntimeEventEnvelope:
        """Append an API-authored event and update the run sequence cursor.

        ``event_id`` and ``created_at`` are reserved for durable domain-outbox
        publication. Existing stream producers leave them unset and preserve
        the historical adapter-assigned identity and append timestamp.
        """

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
            org_id=run.org_id,
            event_id=event_id,
            created_at=created_at,
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
        await self._track_lifecycle(
            event_type_value=event_type.value,
            payload=safe_payload,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)
        return envelope

    async def _track_lifecycle(
        self,
        *,
        event_type_value: str,
        payload: JsonObject,
        parent_task_id: str | None,
        subagent_id: str | None,
    ) -> None:
        """Update the lifecycle ledger if the event opens or closes a pair.

        Centralized so individual emission sites can never forget to keep
        the ledger consistent — the producer's single ``append_api_event``
        chokepoint inspects the event type and routes to the ledger.
        """

        open_entry = LifecycleEventInspector.open_op(
            event_type_value=event_type_value,
            payload=payload,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )
        if open_entry is not None:
            await self.lifecycle_ledger.open(open_entry)
            return
        close_op = LifecycleEventInspector.close_op(
            event_type_value=event_type_value,
            payload=payload,
        )
        if close_op is not None:
            kind, entity_id = close_op
            await self.lifecycle_ledger.close(kind=kind, entity_id=entity_id)

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
            org_id=run.org_id,
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
        await self._track_lifecycle(
            event_type_value=draft.event_type.value,
            payload=draft.payload,
            parent_task_id=draft.parent_task_id,
            subagent_id=getattr(draft, "subagent_id", None),
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

    async def append_api_events_batch(
        self,
        *,
        run: RunRecord,
        source: StreamEventSource,
        event_type: RuntimeApiEventType,
        entries: Sequence[Mapping[str, object]],
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append N API events of one ``event_type`` under one transaction.

        Used by the worker's ``DeltaCoalescer`` (P4 Stage 2) to flush a
        batch of buffered ``MODEL_DELTA`` chunks. Each entry is a mapping
        with optional ``payload``, ``metadata``, ``parent_task_id``,
        ``subagent_id``, ``summary``, and ``status`` keys — the same
        argument shape as :meth:`append_api_event`. The producer projects
        each entry through the standard ``RuntimeEventPresentationProjector``
        + ``PresentationGenerator`` pipeline before handing the drafts to
        the event store's batched-append path.

        Behavior:
          * empty ``entries`` returns ``()`` without touching the store;
          * one ``on_event_appended(run_id)`` notification fires per batch
            (not per entry) — SSE clients still see N envelopes via the
            store, but the notify-then-pull pattern replays them in one
            tick;
          * cursor advancement matches ``append_api_event``: the adapter
            advances ``latest_sequence_no`` when consolidated, otherwise
            this method calls ``set_run_latest_sequence`` once with the
            highest assigned ``sequence_no``.
        """

        if not entries:
            return ()
        drafts: list[RuntimeEventDraft] = []
        for entry in entries:
            payload = entry.get("payload") or {}
            metadata = entry.get("metadata") or {}
            parent_task_id = entry.get("parent_task_id")
            subagent_id = entry.get("subagent_id")
            summary = entry.get("summary")
            status = entry.get("status")
            if not isinstance(payload, dict) or not isinstance(metadata, dict):
                raise TypeError(
                    "append_api_events_batch entry payload + metadata must be dicts"
                )
            safe_payload = RuntimeEventPresentationProjector.payload_for_event(
                event_type=event_type,
                payload=payload,
            )
            safe_metadata = metadata
            timeline_fields = RuntimeEventPresentationProjector.presentation_fields(
                event_type=event_type,
                source=source,
                parent_task_id=(
                    str(parent_task_id) if parent_task_id is not None else None
                ),
                payload=safe_payload,
                metadata=safe_metadata,
                subagent_id=(str(subagent_id) if subagent_id is not None else None),
            )
            if isinstance(summary, str) and (safe_summary := summary.strip()):
                timeline_fields["summary"] = safe_summary
            if isinstance(status, str) and (safe_status := status.strip()):
                timeline_fields["status"] = safe_status
            preliminary = (
                self.presentation_generator.preliminary_presentation_for_event(
                    event_type=event_type,
                    payload=safe_payload,
                    metadata=safe_metadata,
                    timeline_fields=timeline_fields,
                )
            )
            draft_metadata = (
                {**safe_metadata, "presentation": preliminary}
                if preliminary is not None
                else safe_metadata
            )
            drafts.append(
                RuntimeEventDraft(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    org_id=run.org_id,
                    source=source,
                    event_type=event_type,
                    trace_id=run.trace_id,
                    parent_task_id=(
                        str(parent_task_id) if parent_task_id is not None else None
                    ),
                    payload=safe_payload,
                    metadata=draft_metadata,
                    presentation=preliminary,
                    **timeline_fields,
                )
            )
        envelopes = await self.event_store.append_events_batch(drafts)
        for draft in drafts:
            await self._track_lifecycle(
                event_type_value=draft.event_type.value,
                payload=draft.payload,
                parent_task_id=draft.parent_task_id,
                subagent_id=getattr(draft, "subagent_id", None),
            )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)
        return envelopes
