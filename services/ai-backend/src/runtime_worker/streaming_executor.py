"""Shared streaming loop used by both run and approval handlers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_runtime.api.ports import EventStorePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.providers.citation_pipeline import CitationStreamPipeline
from agent_runtime.observability.attribution import (
    Purpose,
    UsageAttributionContext,
)
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.delta_coalescer import DeltaCoalescer
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser

_LOGGER = logging.getLogger(__name__)


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
    CONNECTOR_SLUG = "connector_slug"
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


class _AttributionBuilder:
    """Build :class:`UsageAttributionContext` from per-chunk signals.

    Sub-PRD 01b: this replaces the time-based DB heuristic that used
    to live in ``UsageAttributionResolver``. Every dimension is read
    from data already present on the chunk + the streaming orchestrator
    state:

    - ``task_id`` / ``subagent_slug`` from
      :class:`StreamUpdateProcessor` keyed on the chunk's namespace
      ``subagent_task_id``. Deterministic per-chunk; safe under
      parallel subagents (each subagent's chunks carry their own
      subgraph UUID).
    - ``originating_tool_*`` from :class:`ToolCallLedger` pop. The
      ledger's pending-attribution queue is scope-aware (subagent_id
      filters cross-attribution).
    - ``purpose`` via :meth:`Purpose.derive` from input/output signals.

    The builder owns no state of its own — every method takes the run
    + orchestrator + ledger as arguments. Stateless instances make
    testing trivial.
    """

    def __init__(self, *, run: RunRecord, orchestrator: StreamOrchestrator) -> None:
        self._run = run
        self._orchestrator = orchestrator

    def build_for_chunk(self, chunk: object) -> UsageAttributionContext:
        """Build a context for an LLM call emit landing on this chunk.

        Reads subagent identity from the chunk's namespace + the
        orchestrator's subagent linkage; reads tool attribution from
        the per-run ledger. Returns a constructed
        :class:`UsageAttributionContext` whose ``purpose`` was derived
        from the same signals.
        """

        part = chunk if isinstance(chunk, Mapping) else None
        namespace = (
            StreamPartParser.namespace_for(part)
            if part is not None
            else StreamNamespace(())
        )
        # task_id resolves via the supervisor_task_call_id metadata
        # ``atlas_task_tool`` injects, falling back to the orchestrator's
        # subgraph-to-call_id mapping when the metadata isn't on this
        # specific chunk (e.g. updates-mode chunks).
        task_id: str | None = None
        subagent_slug: str | None = None
        if namespace.is_subagent:
            task_id = (
                StreamPartParser.supervisor_task_call_id_for(part)
                if part is not None
                else None
            )
            if task_id is None:
                task_id = (
                    self._orchestrator.update_processor.subagent_call_id_for_subgraph(
                        run_id=self._run.run_id,
                        subgraph_task_id=namespace.subagent_task_id,
                    )
                )
            subagent_slug = (
                self._orchestrator.update_processor.subagent_id_for_subgraph(
                    run_id=self._run.run_id,
                    subgraph_task_id=namespace.subagent_task_id,
                )
            )

        # Originating-tool attribution: pop the most-recent settled
        # tool for this scope. Scope key is the subagent slug so a
        # parallel subagent's TOOL_RESULT doesn't stamp a sibling's
        # LLM call.
        ledger = self._orchestrator.message_processor.ledger_for_run(self._run.run_id)
        scope_key = subagent_slug
        pending = ledger.pop_pending_attribution(scope_key)
        originating_tool_call_id: str | None = None
        originating_tool_name: str | None = None
        connector_slug: str | None = None
        if pending is not None:
            originating_tool_call_id = pending.call_id
            originating_tool_name = pending.tool_name
            connector_slug = pending.connector_slug

        is_subagent = subagent_slug is not None and task_id is not None
        input_has_tool_message = pending is not None
        output_has_tool_calls = self._chunk_has_tool_calls(chunk)
        purpose = Purpose.derive(
            input_has_tool_message=input_has_tool_message,
            output_has_tool_calls=output_has_tool_calls,
            is_subagent=is_subagent,
            is_compression=False,  # wired in 01c when summarization joins
        )

        # Pydantic invariant: TOOL_INTERPRETATION requires
        # originating_tool_call_id. If we computed it from
        # ``pending is not None``, the invariant holds. Defensive: if
        # purpose ended up as TOOL_INTERPRETATION without an
        # originating tool (shouldn't happen given derive precedence),
        # downgrade to MAIN so construction never raises.
        if purpose == Purpose.TOOL_INTERPRETATION and originating_tool_call_id is None:
            purpose = Purpose.MAIN
        # Defensive: SUBAGENT_WORK requires subagent_slug; if the
        # orchestrator didn't link the subgraph yet (early chunk),
        # downgrade.
        if purpose == Purpose.SUBAGENT_WORK and subagent_slug is None:
            purpose = Purpose.MAIN
            task_id = None

        return UsageAttributionContext(
            org_id=self._run.org_id,
            user_id=self._run.user_id,
            run_id=self._run.run_id,
            conversation_id=self._run.conversation_id,
            trace_id=self._run.trace_id,
            purpose=purpose,
            task_id=task_id if subagent_slug is not None else None,
            subagent_slug=subagent_slug,
            originating_tool_call_id=originating_tool_call_id,
            originating_tool_name=originating_tool_name,
            connector_slug=connector_slug,
        )

    @staticmethod
    def _chunk_has_tool_calls(chunk: object) -> bool:
        """Inspect the AIMessage on the chunk for non-empty tool_calls.

        LangChain AIMessage carries ``.tool_calls`` (list[dict]) when
        the model output included tool selections. The chunk may wrap
        the message in a ``data: (AIMessageChunk, metadata)`` tuple,
        or expose the message at the chunk root.
        """

        candidates: list[object] = [chunk]
        if isinstance(chunk, Mapping):
            data = chunk.get("data")
            if isinstance(data, tuple) and data:
                candidates.append(data[0])
            message = chunk.get("message")
            if message is not None:
                candidates.append(message)
        for candidate in candidates:
            tool_calls = getattr(candidate, "tool_calls", None)
            if isinstance(tool_calls, list) and tool_calls:
                return True
        return False


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
        event_store: EventStorePort,
        event_producer: RuntimeEventProducer,
        stream_event_mapper: StreamOrchestrator,
        track_subagents: bool = False,
        citation_pipeline: CitationStreamPipeline | None = None,
        citation_resolver: CitationResolver | None = None,
        delta_coalesce_window_ms: int = 0,
        delta_coalesce_max_chunks: int = 64,
    ) -> StreamingResult:
        # PR 1.1-rev2 — fall back to the active ContextVar-bound resolver
        # when the caller didn't pass one. This lets the approval-resume
        # path (``RuntimeApprovalHandler._stream_resume``) and any future
        # caller pick up the resolver automatically as long as
        # ``CitationResolver.bind_for_run`` is active in the same async
        # context. Without this fallback, the resume path streams
        # ``[[N]]`` markers from the model that get silently dropped.
        if citation_resolver is None:
            citation_resolver = CitationResolver.active()
        result = StreamingResult()
        # ``active_subagent_tasks`` survives as a boolean signal: "is
        # any subagent currently the active speaker?" That gates the
        # final-result / response-delta / citation-skip branches below.
        # It is NOT used for attribution any more — attribution comes
        # from the deterministic ``_AttributionBuilder`` keyed on
        # chunk namespace.
        active_subagent_tasks: set[str] = set()
        completed_subagent_tasks: set[str] = set()
        attribution_builder = _AttributionBuilder(
            run=run, orchestrator=stream_event_mapper
        )

        # P4 Stage 2 — coalesce ``MODEL_DELTA`` chunk writes within a
        # configurable window. Default ``window_ms=0`` means passthrough
        # (one append per chunk, matching pre-Stage-2). The ``async with``
        # block guarantees a final flush on normal exit, exception, or
        # cancellation so buffered chunks are never silently dropped.
        delta_coalescer = DeltaCoalescer(
            producer=event_producer,
            run=run,
            window_ms=delta_coalesce_window_ms,
            max_chunks=delta_coalesce_max_chunks,
        )

        async with delta_coalescer:
            async for chunk in stream:
                result.last_chunk = chunk
                chunk_message_id = _MessageIdExtractor.extract(chunk)
                # Build the attribution context *only* when the chunk
                # closes an AIMessage (otherwise nothing to attribute);
                # ``record_usage_from`` is the boundary that stamps it
                # onto the per-call slot.
                chunk_context: UsageAttributionContext | None = None
                if chunk_message_id is not None and metrics.chunk_has_usage(chunk):
                    chunk_context = attribution_builder.build_for_chunk(chunk)
                metrics.record_usage_from(
                    chunk, message_id=chunk_message_id, context=chunk_context
                )
                # P4 Stage 2 — flush any buffered deltas before emitting a
                # non-DELTA event so envelope ordering is preserved on the
                # wire (a MODEL_CALL_COMPLETED never lands ahead of the
                # deltas that preceded it).
                await delta_coalescer.flush()
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
                    candidate_context: UsageAttributionContext | None = None
                    if candidate_id is not None and metrics.chunk_has_usage(candidate):
                        candidate_context = attribution_builder.build_for_chunk(
                            candidate
                        )
                    metrics.record_usage_from(
                        candidate, message_id=candidate_id, context=candidate_context
                    )
                    await cls._maybe_emit_model_call_completed(
                        run=run,
                        metrics=metrics,
                        event_producer=event_producer,
                        message_id=candidate_id,
                        source=candidate,
                    )
                delta = stream_event_mapper.stream_delta(chunk)
                if citation_pipeline is not None:
                    # Hook the provider citation pipeline (PRD 01) between the
                    # parsed delta and the wire emission. The pipeline returns
                    # the (possibly rewritten) delta with ``[c<id>]`` chips
                    # appended for any native citation primitives the chunk
                    # carries; the ledger registers the source as a side
                    # effect, firing one ``source_ingested`` event per unique
                    # source. Pass-through providers (no native citations) and
                    # unbound ledgers return ``raw_delta`` unchanged.
                    delta = await citation_pipeline.adapt_chunk(
                        chunk=chunk, raw_delta=delta
                    )
                # Activity events (TOOL_CALL, etc.) must land after any
                # buffered deltas — flush before emitting them.
                await delta_coalescer.flush()
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
                        # Phase 2 (`subagent-interrupt-isolation`) — flag
                        # the run as interrupted but DO NOT return early.
                        # The previous early-return abandoned the
                        # supervisor's `astream` mid-iteration, which
                        # cancelled parallel subagent branches that were
                        # healthy and mid-work. By continuing to drain the
                        # stream, LangGraph keeps yielding events from
                        # siblings until each finishes
                        # (`SUBAGENT_COMPLETED`) or itself interrupts. The
                        # paused branch stays paused via LangGraph's
                        # checkpoint; the supervisor's blocked `task` tool
                        # call(s) are resumed by the existing approval
                        # handler. `action_interrupted=True` still carries
                        # back the WAITING_FOR_APPROVAL transition.
                        result.action_interrupted = True
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
                # P4 Stage 2 — buffered through the coalescer instead of
                # appending one round-trip per chunk. With ``window_ms=0``
                # (default) this is a passthrough to ``append_api_event``;
                # with ``window_ms>0`` chunks accumulate until the window
                # or ``max_chunks`` triggers a batched flush.
                await delta_coalescer.add_delta(
                    payload={_Fields.DELTA: delta, _Fields.MESSAGE: delta},
                    summary=delta,
                )
                # PR 1.1-rev2 — feed the streamed delta to the citation
                # resolver so any `[[N]]` markers the model emits resolve to
                # ``citation_made`` events on the same wire (with monotonic
                # ``sequence_no``). The resolver is best-effort and never
                # raises into the streaming path; an unbound resolver
                # (citations disabled, replay path) is a no-op.
                #
                # ``chunk_message_id`` may be ``None`` for some providers
                # (notably OpenAI Responses streaming chunks, where
                # LangChain's adapter doesn't always surface an id on every
                # delta). The FE's chip resolution scans by ordinal across
                # the run via ``anyLinkForOrdinalInRun`` — message_id is
                # only used for positional offset anchoring, not lookup —
                # so we synthesize a per-run id here rather than dropping
                # the delta. This guarantees the resolver always observes
                # text the model emitted, regardless of provider quirks.
                if citation_resolver is None:
                    if "[[" in delta:
                        _LOGGER.warning(
                            "[citations] streaming.skip run=%s reason=no_resolver "
                            "delta_preview=%r",
                            run.run_id,
                            delta[:80],
                        )
                elif active_subagent_tasks:
                    if "[[" in delta:
                        _LOGGER.debug(
                            "[citations] streaming.skip run=%s "
                            "reason=active_subagent active_count=%d",
                            run.run_id,
                            len(active_subagent_tasks),
                        )
                else:
                    effective_message_id = (
                        chunk_message_id
                        if chunk_message_id is not None
                        else f"msg-of-run:{run.run_id}"
                    )
                    await citation_resolver.observe_delta(
                        message_id=effective_message_id,
                        delta_text=delta,
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

        Idempotent on ``message_id``: subsequent chunks for the same
        call are ignored. The payload carries the slot's accumulated
        counts wrapped in the existing ``AssistantPerformanceMetrics``
        shape so SSE consumers share the same schema as
        ``RUN_COMPLETED``.

        Sub-PRD 01b: the slot was attributed via
        :class:`UsageAttributionContext` at ``record_usage_from`` time.
        Reading ``slot.connector_slug`` here goes through the
        context — no DB lookup needed. The dead
        :class:`UsageAttributionResolver` is gone.
        """

        if message_id is None:
            return
        # Only emit when this chunk actually carries usage — otherwise we'd
        # close the call before the provider has reported tokens. The
        # metrics object holds the provider-aware extractor.
        if not metrics.chunk_has_usage(source):
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
            "input": slot.usage.input_tokens,
            "output": slot.usage.output_tokens,
            "cached_input": slot.usage.cached_input_tokens,
            "total": slot.usage.total_tokens,
        }
        performance_metrics: dict[str, object] = {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": duration_ms,
            _Fields.USAGE: usage_payload,
        }
        if slot.connector_slug is not None:
            performance_metrics[_Fields.CONNECTOR_SLUG] = slot.connector_slug
        await event_producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.MODEL_CALL_COMPLETED,
            payload={
                _Fields.MESSAGE_ID: message_id,
                _Fields.PERFORMANCE_METRICS: performance_metrics,
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
