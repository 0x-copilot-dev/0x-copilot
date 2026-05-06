"""Runtime event producer helpers shared by API producers and workers.

The producer attaches a fully-formed *preliminary* presentation synchronously
(title + status + body, via the projector chain in
``PresentationGenerator``), persists the event so SSE subscribers see it
within milliseconds, then optionally spawns a background LLM polish task.

When the LLM polish succeeds, it is emitted as a separate
``PRESENTATION_UPDATED`` event whose presentation merges *body fields*
(``summary``, ``result_preview``, ``action_label``, ``primary_entity``)
onto the preliminary. Title / status_label / kind are owned by the event
lifecycle and not patched by the LLM — that way a polish race or timeout
can never regress the card's lifecycle state. When the LLM times out, no
patch event fires at all; the preliminary already had a usable body.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence

from agent_runtime.api.async_ports import AsyncEventStorePort, AsyncPersistencePort
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.execution.contracts import JsonObject, StreamEvent, StreamEventSource
from runtime_adapters.async_wrappers import (
    adapt_event_store_to_async,
    adapt_persistence_to_async,
)
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)

INTENT_BUFFER_MAX = 4
INTENT_HINT_MAX_CHARS = 300
_INTENT_EVENT_TYPES = frozenset(
    {RuntimeApiEventType.MODEL_DELTA, RuntimeApiEventType.FINAL_RESPONSE}
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
        persistence: PersistencePort | AsyncPersistencePort,
        event_store: EventStorePort | AsyncEventStorePort,
        presentation_generator: PresentationGenerator | None = None,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence: AsyncPersistencePort = adapt_persistence_to_async(persistence)
        self.event_store: AsyncEventStorePort = adapt_event_store_to_async(event_store)
        self.presentation_generator = presentation_generator or PresentationGenerator()
        self._on_event_appended = on_event_appended
        # Per-(run_id, group_key) in-flight enrichment task. A newer event for
        # the same group_key cancels the older pending enrichment so we never
        # patch the card with a stale (e.g. STARTED) presentation after a
        # newer (e.g. RESULT) presentation has already landed.
        self._pending_enrichment: dict[tuple[str, str], asyncio.Task[None]] = {}
        # Per-run rolling window of recent assistant text (`MODEL_DELTA` /
        # `FINAL_RESPONSE`). Forwarded into the LLM prompt as `agent_intent_hint`
        # so the presentation summary reflects *why* the agent called the tool.
        self._intent_buffer: dict[str, deque[str]] = defaultdict(
            lambda: deque(maxlen=INTENT_BUFFER_MAX)
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

        self._track_intent(run.run_id, event_type, safe_payload)
        metadata_with_intent = self._inject_intent_hint(run.run_id, safe_metadata)

        preliminary = self.presentation_generator.preliminary_presentation_for_event(
            event_type=event_type,
            payload=safe_payload,
            metadata=metadata_with_intent,
            timeline_fields=timeline_fields,
        )
        draft_metadata = (
            {**metadata_with_intent, "presentation": preliminary}
            if preliminary is not None
            else metadata_with_intent
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

        # Eligibility uses the *pre-preliminary* metadata so the just-attached
        # preliminary doesn't suppress its own enrichment.
        if self.presentation_generator.event_eligible_for_enrichment(
            event_type, safe_payload, metadata_with_intent
        ):
            self._spawn_enrichment(
                run=run,
                event_type=event_type,
                source=source,
                payload=safe_payload,
                metadata=metadata_with_intent,
                timeline_fields=timeline_fields,
                preliminary=preliminary,
            )
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
        self._track_intent(run.run_id, draft.event_type, draft.payload)
        injected_metadata = self._inject_intent_hint(run.run_id, draft.metadata)

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
            metadata=injected_metadata,
            timeline_fields=timeline_fields,
        )
        if preliminary is not None:
            draft = draft.model_copy(
                update={
                    "presentation": preliminary,
                    "metadata": {
                        **injected_metadata,
                        "presentation": preliminary,
                    },
                }
            )
        elif injected_metadata is not draft.metadata:
            draft = draft.model_copy(update={"metadata": injected_metadata})
        envelope = await self.event_store.append_event(draft)
        await self.persistence.set_run_latest_sequence(
            run_id=run.run_id,
            latest_sequence_no=envelope.sequence_no,
        )
        if self._on_event_appended is not None:
            self._on_event_appended(run.run_id)

        if self.presentation_generator.event_eligible_for_enrichment(
            draft.event_type, draft.payload, draft.metadata
        ):
            self._spawn_enrichment(
                run=run,
                event_type=draft.event_type,
                source=draft.source,
                payload=draft.payload,
                metadata=draft.metadata,
                timeline_fields=timeline_fields,
                preliminary=preliminary,
            )
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

    async def flush_pending_enrichment(self, run_id: str | None = None) -> None:
        """Await all in-flight enrichment tasks. Test/cleanup hook only."""

        if run_id is None:
            tasks = list(self._pending_enrichment.values())
        else:
            tasks = [
                task
                for (rid, _), task in self._pending_enrichment.items()
                if rid == run_id
            ]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn_enrichment(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
        preliminary: JsonObject | None,
    ) -> None:
        group_key = self.presentation_generator._group_key(payload, timeline_fields)
        logging.getLogger(__name__).debug(
            "presentation.enrichment.spawn run=%s event=%s group_key=%s",
            run.run_id,
            event_type.value,
            group_key,
        )
        if group_key is not None:
            cancel_key = (run.run_id, group_key)
            existing = self._pending_enrichment.pop(cancel_key, None)
            if existing is not None and not existing.done():
                existing.cancel()
        task = asyncio.create_task(
            self._enrich_and_patch(
                run=run,
                event_type=event_type,
                source=source,
                payload=payload,
                metadata=metadata,
                timeline_fields=dict(timeline_fields),
                preliminary=preliminary,
                group_key=group_key,
            )
        )
        if group_key is not None:
            cancel_key = (run.run_id, group_key)
            self._pending_enrichment[cancel_key] = task
            task.add_done_callback(
                lambda done, key=cancel_key: self._cleanup_task(key, done)
            )

    def _cleanup_task(
        self,
        key: tuple[str, str],
        task: asyncio.Task[None],
    ) -> None:
        current = self._pending_enrichment.get(key)
        if current is task:
            self._pending_enrichment.pop(key, None)

    async def _enrich_and_patch(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        payload: JsonObject,
        metadata: JsonObject,
        timeline_fields: Mapping[str, object],
        preliminary: JsonObject | None,
        group_key: str | None,
    ) -> None:
        try:
            enriched = await self.presentation_generator.enrich_presentation_for_event(
                run=run,
                event_type=event_type,
                source=source,
                payload=payload,
                metadata=metadata,
                timeline_fields=timeline_fields,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).debug(
                "Presentation enrichment failed for run %s", run.run_id, exc_info=True
            )
            return
        if enriched is None:
            logging.getLogger(__name__).debug(
                "presentation.enrichment.empty run=%s event=%s",
                run.run_id,
                event_type.value,
            )
            return
        if preliminary is None:
            # Eligibility should always pair LLM-eligible events with a
            # preliminary envelope. If that invariant breaks, skip the patch
            # rather than emit a malformed body-only patch with no title.
            logging.getLogger(__name__).warning(
                "presentation.enrichment.missing_preliminary run=%s event=%s",
                run.run_id,
                event_type.value,
            )
            return
        merged, patches = self._merge_polish(preliminary, enriched)
        if not patches:
            logging.getLogger(__name__).debug(
                "presentation.enrichment.unchanged run=%s event=%s",
                run.run_id,
                event_type.value,
            )
            return
        logging.getLogger(__name__).debug(
            "presentation.enrichment.patch run=%s event=%s patches=%s",
            run.run_id,
            event_type.value,
            ",".join(patches),
        )

        patch_payload: JsonObject = {"patches": list(patches)}
        for key in ("call_id", "approval_id", "source_tool_call_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                patch_payload[key] = value
        if group_key is not None and "call_id" not in patch_payload:
            patch_payload.setdefault("group_key", group_key)

        try:
            patch_draft = RuntimeEventDraft(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.PRESENTATION_UPDATED,
                trace_id=run.trace_id,
                payload=patch_payload,
                metadata={"presentation": merged},
                presentation=merged,
            )
            envelope = await self.event_store.append_event(patch_draft)
            await self.persistence.set_run_latest_sequence(
                run_id=run.run_id,
                latest_sequence_no=envelope.sequence_no,
            )
            if self._on_event_appended is not None:
                self._on_event_appended(run.run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).debug(
                "Presentation patch event append failed for run %s",
                run.run_id,
                exc_info=True,
            )

    # Body fields the LLM polish layer is allowed to refine. Title /
    # status_label / kind / group_key / debug_label belong to the event's
    # lifecycle and stay frozen at whatever the synchronous chain produced,
    # so a polish race or timeout can never regress lifecycle state.
    _POLISH_BODY_FIELDS: tuple[str, ...] = (
        "summary",
        "result_preview",
        "action_label",
        "primary_entity",
    )

    @classmethod
    def _merge_polish(
        cls,
        preliminary: JsonObject | None,
        enriched: JsonObject,
    ) -> tuple[JsonObject, tuple[str, ...]]:
        """Overlay body fields from ``enriched`` onto ``preliminary``.

        Returns the merged envelope and the tuple of field names that
        actually changed. Empty tuple means no patch event needs to fire.
        """

        merged: JsonObject = dict(preliminary or {})
        patches: list[str] = []
        for field in cls._POLISH_BODY_FIELDS:
            new_value = enriched.get(field)
            if new_value in (None, "", [], ()):
                continue
            current = merged.get(field)
            if current == new_value:
                continue
            merged[field] = new_value
            patches.append(field)
        return merged, tuple(patches)

    def _track_intent(
        self,
        run_id: str,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> None:
        if event_type not in _INTENT_EVENT_TYPES:
            return
        text = self._first_text(payload, ("delta", "message", "summary", "text"))
        if text is None:
            return
        self._intent_buffer[run_id].append(text[:INTENT_HINT_MAX_CHARS])

    def _inject_intent_hint(self, run_id: str, metadata: JsonObject) -> JsonObject:
        if "agent_intent_hint" in metadata:
            return metadata
        buffer = self._intent_buffer.get(run_id)
        if not buffer:
            return metadata
        hint = " ".join(buffer)[-INTENT_HINT_MAX_CHARS:].strip()
        if not hint:
            return metadata
        return {**metadata, "agent_intent_hint": hint}

    @staticmethod
    def _first_text(payload: JsonObject, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
