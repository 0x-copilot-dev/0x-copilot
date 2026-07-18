"""Map runtime stream chunks into persisted runtime API events."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from agent_runtime.api.constants import Keys, Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
)
from runtime_api.schemas import (
    ApprovalRequestRecord,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_api.schemas.approvals import (
    APPROVAL_MAX_PARAMS,
    ApprovalParam,
    McpApprovalMetadata,
)
from runtime_api.schemas.common import (
    ApprovalCategory,
    ApprovalReasonCode,
    ApprovalReversible,
)
from runtime_worker.approval_recognisers import ApprovalParamRecogniserRegistry
from runtime_worker.stream_messages import StreamMessageParser, StreamTextHelper
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.stream_tools import StreamMessageProcessor
from runtime_worker.tool_result_offload import ToolResultOffloader

_logger = logging.getLogger(__name__)


class _Fields:
    DATA = "data"
    MESSAGES = "messages"
    UPDATES = "updates"
    CUSTOM = "custom"
    VALUES = "values"
    ACTION_ID = "action_id"
    NATIVE_INTERRUPT_ID = "native_interrupt_id"
    INTERRUPT = "__interrupt__"
    INTERRUPTS = "interrupts"
    VALUE = "value"
    ID = "id"
    INTERRUPT_ID = "interrupt_id"
    ACTION_REQUESTS = "action_requests"
    REVIEW_CONFIGS = "review_configs"
    ACTION_NAME = "action_name"
    ALLOWED_DECISIONS = "allowed_decisions"
    ACTION_REQUIRED = "action_required"
    SERVER_NAME = "server_name"
    TOOL_NAME = "tool_name"
    ARGUMENTS = "arguments"
    DISPLAY_NAME = "display_name"
    READ_ONLY = "read_only"
    RISK_LEVEL = "risk_level"
    GRANT_OPTIONS = "grant_options"
    ACTION_INDEX = "action_index"
    ACTION_COUNT = "action_count"
    # PR #43 — ApprovalBatch projection. Each ``approval_requested`` event
    # carries the typed ``batch_id`` + ``batch_index`` so the frontend can
    # group the per-item cards by batch (and a future PR can add an
    # "approve all" affordance). Backward-compatible: existing FE handlers
    # ignore unknown keys.
    BATCH_ID = "batch_id"
    BATCH_INDEX = "batch_index"
    # Persisted on the approval record's metadata so the resolution handler
    # can detect subagent-scoped pauses without rescanning the event log.
    # Mirrors the envelope-level field.
    PARENT_TASK_ID = "parent_task_id"


class StreamCustomProcessor:
    """Process custom and fallthrough stream events into progress payloads.

    Standalone processor — no inheritance.
    """

    @classmethod
    async def process(
        cls,
        *,
        event_producer: RuntimeEventProducer,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: dict[str, object],
        parent_task_id: str | None,
    ) -> None:
        """Emit a progress or subagent_progress event for any parseable activity payload in ``data``."""
        payload = StreamMessageParser.safe_activity_payload(data)
        if not payload:
            return
        await event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT
            if namespace.is_subagent
            else StreamEventSource.MAIN_AGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS
            if namespace.is_subagent
            else RuntimeApiEventType.PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )


class StreamOrchestrator:
    """Compose stream processors and route events by stream type.

    Uses composition instead of deep inheritance — delegates to
    StreamMessageProcessor, StreamUpdateProcessor, and StreamCustomProcessor.
    """

    def __init__(
        self,
        event_producer: RuntimeEventProducer,
        *,
        tool_result_offloader: ToolResultOffloader | None = None,
    ) -> None:
        """Wire the event producer and instantiate the message and update sub-processors.

        ``tool_result_offloader`` is threaded to the message processor so
        oversized tool output is offloaded on the desktop file store; ``None``
        (the default) keeps the historical inline behavior everywhere else.
        """
        self.event_producer = event_producer
        self.update_processor = StreamUpdateProcessor(event_producer)
        self.message_processor = StreamMessageProcessor(
            event_producer,
            self.update_processor,
            tool_result_offloader=tool_result_offloader,
        )

    async def append_activity_events(
        self,
        *,
        run: RunRecord,
        chunk: object,
        delta: str | None,
    ) -> None:
        """Parse ``chunk`` and dispatch it to the appropriate sub-processor, persisting any events emitted."""
        part = StreamPartParser.stream_part(chunk)
        if part is None:
            return

        stream_type = StreamPartParser.stream_type(part)
        namespace = StreamPartParser.namespace_for(part)
        data = part[_Fields.DATA]
        metadata = namespace.metadata(stream_type)

        # ``atlas_task_tool`` writes the supervisor's ``task`` call_id into
        # each subagent's RunnableConfig metadata. LangGraph propagates that
        # metadata onto every chunk the subgraph emits. Read it here and pin
        # a deterministic ``(run_id, subgraph_task_id) → supervisor_call_id``
        # mapping so downstream emits resolve to the supervisor call_id (which
        # the client matches against the ``run_subagent`` tool part's
        # toolCallId) instead of the raw LangGraph subgraph UUID.
        #
        # Resolution rules:
        # 1. If chunk metadata supplied the linkage (production path with the
        #    patched task tool), use the cached supervisor call_id.
        # 2. If no metadata (legacy / synthetic test fixtures), fall back to
        #    the raw subgraph_task_id so the historical contract holds. The
        #    FIFO-pop fallback intentionally stays inside
        #    ``stream_tools.StreamMessageProcessor.process`` where it was the
        #    source of truth — pulling it forward here would drain the queue
        #    before later subagents' lifecycle events can register their
        #    call_ids.
        subgraph_task_id = namespace.subagent_task_id
        chunk_supervisor_call_id = StreamPartParser.supervisor_task_call_id_for(part)
        if chunk_supervisor_call_id is not None and subgraph_task_id is not None:
            self.update_processor.register_supervisor_call_id_for_subgraph(
                run_id=run.run_id,
                subgraph_task_id=subgraph_task_id,
                supervisor_call_id=chunk_supervisor_call_id,
            )

        cached_call_id = self.update_processor.cached_subagent_call_id_for_subgraph(
            run_id=run.run_id,
            subgraph_task_id=subgraph_task_id,
        )
        parent_task_id = (
            cached_call_id if cached_call_id is not None else subgraph_task_id
        )
        source_tool_call_id = (
            self._source_tool_call_id_for_payload(data)
            if stream_type == _Fields.MESSAGES
            else None
        )

        native_payloads = self.native_interrupt_payloads(run, data)
        # PR #43 — Insert the ApprovalBatch + N items atomically BEFORE emitting
        # any per-item ``approval_requested`` event. The batch is the lock
        # target for the worker's resume gate; per-item rows + per-item events
        # carry batch_id/batch_index so the resume handler can read the batch
        # state.
        await self._insert_approval_batch_for_payloads(
            run=run, payloads=native_payloads
        )
        for payload in native_payloads:
            event_type = StreamMessageParser.api_event_type(payload)
            if event_type is None:
                continue
            await self.create_approval_request(
                run=run, payload=payload, parent_task_id=parent_task_id
            )
            interrupt_envelope = await self.event_producer.append_api_event(
                run=run,
                source=self._source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            await self._maybe_emit_subagent_paused(
                run=run,
                metadata=metadata,
                interrupt_event_type=event_type,
                interrupt_envelope=interrupt_envelope,
                parent_task_id=parent_task_id,
            )
        if native_payloads:
            return

        explicit_payloads = tuple(StreamMessageParser.explicit_api_payloads(data))
        # Explicit (non-native) approval payloads also belong to a batch — they
        # are emitted by tools that produce their own approval cards (e.g. the
        # draft-send tool). Each such payload is its own 1-item batch with
        # batch_id == approval_id. Stamping batch_id/batch_index here before
        # the per-item event keeps the projection consistent for the FE.
        stamped_explicit = tuple(
            self._stamp_batch_fields_on_explicit_payload(payload)
            for payload in explicit_payloads
        )
        await self._insert_approval_batch_for_payloads(
            run=run, payloads=stamped_explicit
        )
        for payload in stamped_explicit:
            event_type = StreamMessageParser.api_event_type(payload)
            if event_type is None:
                continue
            if (
                source_tool_call_id is not None
                and self._approval_event_morphs_tool_bubble(event_type, payload)
            ):
                payload = {
                    **payload,
                    Keys.Field.SOURCE_TOOL_CALL_ID: source_tool_call_id,
                }
            if event_type in {
                RuntimeApiEventType.APPROVAL_REQUESTED,
                RuntimeApiEventType.MCP_AUTH_REQUIRED,
            }:
                payload = self.payload_with_action_id(event_type, payload)
                await self.create_approval_request(
                    run=run, payload=payload, parent_task_id=parent_task_id
                )
            interrupt_envelope = await self.event_producer.append_api_event(
                run=run,
                source=self._source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            await self._maybe_emit_subagent_paused(
                run=run,
                metadata=metadata,
                interrupt_event_type=event_type,
                interrupt_envelope=interrupt_envelope,
                parent_task_id=parent_task_id,
            )

        if stream_type == _Fields.MESSAGES:
            message = StreamMessageParser.message_from_stream_payload(data)
            await self.message_processor.process(
                run=run,
                namespace=namespace,
                message=message,
                delta=delta,
            )
            return

        if stream_type not in {
            _Fields.UPDATES,
            _Fields.CUSTOM,
        } or StreamMessageParser.contains_explicit_api_event(data):
            return

        if stream_type == _Fields.UPDATES and await self.update_processor.process(
            run=run,
            namespace=namespace,
            data=data,
            metadata=metadata,
        ):
            return

        await StreamCustomProcessor.process(
            event_producer=self.event_producer,
            run=run,
            namespace=namespace,
            data=data,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )

    @classmethod
    def stream_delta(cls, chunk: object) -> str | None:
        """Extract the text delta from a main-agent message chunk; returns ``None`` for tool, result, or subagent chunks."""
        part = StreamPartParser.stream_part(chunk)
        if part is None or StreamPartParser.stream_type(part) != _Fields.MESSAGES:
            return None
        if StreamPartParser.namespace_for(part).is_subagent:
            return None
        message = StreamMessageParser.message_from_stream_payload(part[_Fields.DATA])
        if StreamMessageParser.tool_call_chunks(
            message
        ) or StreamMessageParser.is_tool_result_message(message):
            return None
        return StreamMessageParser.message_delta(message)

    @classmethod
    def _source_tool_call_id_for_payload(cls, payload: object) -> str | None:
        """Return the tool-call ID from a tool-result message payload, or ``None`` if not applicable."""
        message = StreamMessageParser.message_from_stream_payload(payload)
        if not StreamMessageParser.is_tool_result_message(message):
            return None
        message_payload = StreamMessageParser.payload_mapping(message)
        return (
            StreamTextHelper.extract(message_payload.get(Keys.Field.TOOL_CALL_ID))
            or StreamTextHelper.extract(message_payload.get(Keys.Field.CALL_ID))
            or StreamTextHelper.extract(message_payload.get(Keys.Field.ID))
        )

    @classmethod
    def _approval_event_morphs_tool_bubble(
        cls,
        event_type: RuntimeApiEventType,
        payload: Mapping[str, object],
    ) -> bool:
        """True when the approval/auth event should reuse the originating tool's UI slot.

        Frontend renders mcp_auth_required and mcp_tool approvals by morphing
        the tool's existing card via ``source_tool_call_id``. Other approval
        kinds (ask_a_question, action) are free-standing interrupts unrelated
        to whichever tool happened to finish in the same stream chunk; if they
        carry ``source_tool_call_id`` they displace that tool's bubble in the
        chat timeline.
        """
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return True
        if event_type is not RuntimeApiEventType.APPROVAL_REQUESTED:
            return False
        return (
            StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_KIND))
            == ApiValues.ApprovalKind.MCP_TOOL
        )

    async def create_approval_request(
        self,
        *,
        run: RunRecord,
        payload: dict[str, object],
        parent_task_id: str | None = None,
    ) -> None:
        """Persist an ``ApprovalRequestRecord`` for the given payload if one does not already exist."""
        approval_id = StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_ID))
        if approval_id is None:
            return
        existing = await self.event_producer.persistence.get_approval_request(
            org_id=run.org_id,
            approval_id=approval_id,
        )
        if existing is not None:
            return
        # Persist ``parent_task_id`` on the approval record so the
        # resolution handler can detect when the approval targets a
        # subagent-scoped pause and emit ``subagent_resumed`` before the
        # LangGraph resume kicks in. Written as a sibling key on
        # ``metadata`` under the same name the chunk metadata uses so
        # readers don't have to special-case it.
        metadata: dict[str, object] = dict(payload)
        if parent_task_id is not None:
            metadata[_Fields.PARENT_TASK_ID] = parent_task_id
        await self.event_producer.persistence.create_approval_request(
            record=ApprovalRequestRecord(
                approval_id=approval_id,
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                user_id=run.user_id,
                metadata=metadata,
            )
        )

    async def _insert_approval_batch_for_payloads(
        self,
        *,
        run: RunRecord,
        payloads: Sequence[Mapping[str, object]],
    ) -> None:
        """Group ``approval_requested`` payloads by ``batch_id`` and insert each ApprovalBatch atomically.

        Called once per stream chunk, BEFORE any per-item event is emitted.
        ``mcp_auth_required`` payloads also produce a batch (size 1) so the
        resume gate logic is uniform across approval kinds.

        Idempotent on ``batch_id`` — a stream replay that re-projects the same
        interrupt is a no-op at the persistence layer.
        """
        by_batch: dict[str, list[Mapping[str, object]]] = {}
        for payload in payloads:
            event_type = StreamMessageParser.api_event_type(payload)
            if event_type not in {
                RuntimeApiEventType.APPROVAL_REQUESTED,
                RuntimeApiEventType.MCP_AUTH_REQUIRED,
            }:
                continue
            batch_id = StreamTextHelper.extract(payload.get(_Fields.BATCH_ID))
            if batch_id is None:
                continue
            by_batch.setdefault(batch_id, []).append(payload)

        for batch_id, items_for_batch in by_batch.items():
            # Items are emitted in interrupt order, but sort defensively so the
            # spec validator's ``0..N-1 contiguous`` check passes regardless of
            # caller ordering.
            ordered = sorted(
                items_for_batch,
                key=lambda payload: self._coerce_batch_index(
                    payload.get(_Fields.BATCH_INDEX)
                ),
            )
            item_records: list[ApprovalBatchItemRecord] = []
            for payload in ordered:
                item_id = StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_ID))
                if item_id is None:
                    return
                item_records.append(
                    ApprovalBatchItemRecord(
                        item_id=item_id,
                        batch_id=batch_id,
                        index=self._coerce_batch_index(
                            payload.get(_Fields.BATCH_INDEX)
                        ),
                    )
                )
            batch = ApprovalBatchRecord(
                batch_id=batch_id,
                run_id=run.run_id,
                org_id=run.org_id,
            )
            try:
                spec = ApprovalBatchSpec.build(batch=batch, items=item_records)
            except ValueError:
                # Should not happen — payloads come from
                # ``native_tool_approval_payloads`` which assigns contiguous
                # indices. Defensive guard so a malformed payload never blocks
                # the per-item event emit (the FE still renders the cards).
                _logger.exception(
                    "ApprovalBatchSpec.build failed; skipping batch insert",
                    extra={"batch_id": batch_id, "run_id": run.run_id},
                )
                continue
            await self.event_producer.persistence.insert_approval_batch(spec=spec)

    @staticmethod
    def _coerce_batch_index(value: object) -> int:
        """Coerce a payload's ``batch_index`` value to an int, defaulting to 0.

        Defensive against payloads that round-tripped through JSON serialisers
        where an int became a string. The fan-out always emits ints; this is
        purely a safety belt for replay paths.
        """
        if isinstance(value, bool):
            # ``bool`` is a subclass of int but should never be a batch index.
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @classmethod
    def _stamp_batch_fields_on_explicit_payload(
        cls,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        """Ensure an explicit approval payload carries ``batch_id`` and ``batch_index``.

        Explicit (non-native) approval payloads come from tools that emit their
        own approval cards (e.g. ``draft_send``). They are always single-item
        batches; ``batch_id == approval_id`` and ``batch_index == 0``. Stamping
        the fields here keeps every ``approval_requested`` event uniformly
        shaped so the FE can group by batch_id without branching on source.
        """
        normalized: dict[str, object] = dict(payload)
        if _Fields.BATCH_ID not in normalized:
            approval_id = StreamTextHelper.extract(
                normalized.get(Keys.Field.APPROVAL_ID)
            ) or StreamTextHelper.extract(normalized.get(_Fields.ACTION_ID))
            if approval_id is not None:
                normalized[_Fields.BATCH_ID] = approval_id
        normalized.setdefault(_Fields.BATCH_INDEX, 0)
        return normalized

    async def append_native_interrupt_events(
        self,
        *,
        run: RunRecord,
        value: object,
    ) -> bool:
        """Emit events for each native LangGraph interrupt in ``value``; returns ``True`` if any were emitted."""
        namespace = StreamNamespace(())
        did_append = False
        payloads = self.native_interrupt_payloads(run, value)
        # PR #43 — same batch-insertion contract as ``append_activity_events``.
        await self._insert_approval_batch_for_payloads(run=run, payloads=payloads)
        for payload in payloads:
            event_type = StreamMessageParser.api_event_type(payload)
            if event_type is None:
                continue
            await self.create_approval_request(run=run, payload=payload)
            await self.event_producer.append_api_event(
                run=run,
                source=self._source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=namespace.metadata(_Fields.VALUES),
            )
            did_append = True
        return did_append

    @classmethod
    def payload_with_action_id(
        cls,
        event_type: RuntimeApiEventType,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Normalise ``approval_id`` and ``action_id`` fields on the payload, adding ``mcp_auth`` kind when applicable."""
        approval_id = StreamTextHelper.extract(
            payload.get(Keys.Field.APPROVAL_ID)
        ) or StreamTextHelper.extract(payload.get(_Fields.ACTION_ID))
        if approval_id is None:
            return payload
        normalized = {
            **payload,
            Keys.Field.APPROVAL_ID: approval_id,
            _Fields.ACTION_ID: StreamTextHelper.extract(payload.get(_Fields.ACTION_ID))
            or approval_id,
        }
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            normalized.setdefault(Keys.Field.APPROVAL_KIND, "mcp_auth")
        return normalized

    # When an interrupt event fires inside a subagent (i.e. ``parent_task_id``
    # resolved to the supervisor's task call_id), emit a sibling
    # ``subagent_paused`` event so the client can flip the subagent's status
    # to ``paused`` without inferring from "started but never completed".
    # Resume is emitted separately by the approval handler on resolution.
    #
    # ``reason`` discriminates the client copy / icon. ``MCP_AUTH_REQUIRED``
    # maps to ``mcp_auth``. ``APPROVAL_REQUESTED`` is further refined by
    # inspecting the payload's ``approval_kind``: ``ask_a_question`` is its
    # own reason so the client can render "Waiting for answer" instead of the
    # generic "Waiting on approval"; everything else collapses to ``approval``.
    _SUBAGENT_INTERRUPT_REASONS = {
        RuntimeApiEventType.APPROVAL_REQUESTED: "approval",
        RuntimeApiEventType.MCP_AUTH_REQUIRED: "mcp_auth",
    }

    async def _maybe_emit_subagent_paused(
        self,
        *,
        run: RunRecord,
        metadata: dict[str, object],
        interrupt_event_type: RuntimeApiEventType,
        interrupt_envelope: object,
        parent_task_id: str | None,
    ) -> None:
        """Emit a ``subagent_paused`` event when an interrupt fires inside a subagent context."""
        if parent_task_id is None:
            return
        reason = self._SUBAGENT_INTERRUPT_REASONS.get(interrupt_event_type)
        if reason is None:
            return
        if (
            interrupt_event_type is RuntimeApiEventType.APPROVAL_REQUESTED
            and self._approval_kind_for(interrupt_envelope)
            == ApiValues.ApprovalKind.ASK_A_QUESTION
        ):
            reason = "ask_a_question"
        source_event_id = getattr(interrupt_envelope, "event_id", None)
        payload: dict[str, object] = {
            "task_id": parent_task_id,
            "reason": reason,
        }
        if isinstance(source_event_id, str):
            payload["source_event_id"] = source_event_id
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PAUSED,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )

    @staticmethod
    def _approval_kind_for(interrupt_envelope: object) -> str | None:
        """Extract the ``approval_kind`` string from an interrupt envelope's payload mapping."""
        payload = getattr(interrupt_envelope, "payload", None)
        if not isinstance(payload, Mapping):
            return None
        return StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_KIND))

    @classmethod
    def native_interrupt_payloads(
        cls,
        run: RunRecord,
        value: object,
    ) -> tuple[dict[str, object], ...]:
        """Build approval-event payloads for every native LangGraph interrupt contained in ``value``."""
        payloads: list[dict[str, object]] = []
        for interrupt_index, interrupt in enumerate(cls._native_interrupts(value)):
            interrupt_id = cls._native_interrupt_id(
                interrupt,
                fallback=f"interrupt:{run.run_id}:{interrupt_index}",
            )
            interrupt_value = cls._native_interrupt_value(interrupt)
            auth_payload = cls._native_auth_payload(interrupt_id, interrupt_value)
            if auth_payload is not None:
                payloads.append(auth_payload)
                continue
            ask_payload = cls._native_ask_a_question_payload(
                interrupt_id, interrupt_value
            )
            if ask_payload is not None:
                payloads.append(ask_payload)
                continue
            payloads.extend(
                cls.native_tool_approval_payloads(
                    interrupt_id=interrupt_id,
                    interrupt_value=interrupt_value,
                )
            )
        return tuple(payloads)

    @classmethod
    def _native_interrupts(cls, value: object) -> tuple[object, ...]:
        """Extract the raw interrupt list from a LangGraph stream value, handling both dict and object forms."""
        raw = value.get(_Fields.INTERRUPT) if isinstance(value, Mapping) else None
        if raw is None and isinstance(value, Mapping):
            raw = value.get(_Fields.INTERRUPTS)
        if raw is None:
            raw = getattr(value, _Fields.INTERRUPTS, None)
        if raw is None:
            raw = StreamMessageParser.payload_mapping(value).get(_Fields.INTERRUPT)
        if raw is None:
            return ()
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return tuple(raw)
        return (raw,)

    @classmethod
    def _native_interrupt_value(cls, interrupt: object) -> object:
        """Unwrap the ``value`` field from a native interrupt object or mapping."""
        if isinstance(interrupt, Mapping):
            return interrupt.get(_Fields.VALUE) or interrupt
        return getattr(interrupt, _Fields.VALUE, interrupt)

    @classmethod
    def _native_interrupt_id(cls, interrupt: object, *, fallback: str) -> str:
        """Return the interrupt's ``id`` field, falling back to ``fallback`` when absent."""
        if isinstance(interrupt, Mapping):
            value = interrupt.get(_Fields.ID) or interrupt.get(_Fields.INTERRUPT_ID)
        else:
            value = getattr(interrupt, _Fields.ID, None)
        return StreamTextHelper.extract(value) or fallback

    @classmethod
    def _native_auth_payload(
        cls,
        interrupt_id: str,
        interrupt_value: object,
    ) -> dict[str, object] | None:
        """Return a normalised ``mcp_auth_required`` payload if the interrupt carries an MCP auth event, else ``None``."""
        payload = StreamMessageParser.payload_mapping(interrupt_value)
        if (
            StreamMessageParser.api_event_type(payload)
            is not RuntimeApiEventType.MCP_AUTH_REQUIRED
        ):
            return None
        normalized = cls.payload_with_action_id(
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            {
                **payload,
                _Fields.NATIVE_INTERRUPT_ID: interrupt_id,
                _Fields.ACTION_ID: StreamTextHelper.extract(
                    payload.get(_Fields.ACTION_ID)
                )
                or interrupt_id,
            },
        )
        normalized.setdefault(Keys.Field.APPROVAL_ID, interrupt_id)
        normalized.setdefault(Keys.Field.APPROVAL_KIND, "mcp_auth")
        # PR #43 — single-action interrupts still belong to a batch of size 1.
        # batch_id is the interrupt_id, batch_index is 0. The same projection
        # rule applies as for multi-action MCP tool batches.
        normalized.setdefault(_Fields.BATCH_ID, interrupt_id)
        normalized.setdefault(_Fields.BATCH_INDEX, 0)
        return normalized

    @classmethod
    def _native_ask_a_question_payload(
        cls,
        interrupt_id: str,
        interrupt_value: object,
    ) -> dict[str, object] | None:
        """Return a normalised ``ask_a_question`` approval payload if the interrupt matches, else ``None``."""
        payload = StreamMessageParser.payload_mapping(interrupt_value)
        if (
            StreamMessageParser.api_event_type(payload)
            is not RuntimeApiEventType.APPROVAL_REQUESTED
        ):
            return None
        if (
            StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_KIND))
            != ApiValues.ApprovalKind.ASK_A_QUESTION
        ):
            return None
        normalized = cls.payload_with_action_id(
            RuntimeApiEventType.APPROVAL_REQUESTED,
            {
                **payload,
                _Fields.NATIVE_INTERRUPT_ID: interrupt_id,
                _Fields.ACTION_ID: StreamTextHelper.extract(
                    payload.get(_Fields.ACTION_ID)
                )
                or interrupt_id,
            },
        )
        normalized.setdefault(Keys.Field.APPROVAL_ID, interrupt_id)
        normalized[Keys.Field.APPROVAL_KIND] = ApiValues.ApprovalKind.ASK_A_QUESTION
        # PR #43 — ask_a_question is a single-action interrupt; the batch has
        # one item with index 0.
        normalized.setdefault(_Fields.BATCH_ID, interrupt_id)
        normalized.setdefault(_Fields.BATCH_INDEX, 0)
        return normalized

    @classmethod
    def native_tool_approval_payloads(
        cls,
        *,
        interrupt_id: str,
        interrupt_value: object,
    ) -> tuple[dict[str, object], ...]:
        """Build ``mcp_tool`` approval payloads for each ``call_mcp_tool`` action in a native interrupt value."""
        payload = (
            interrupt_value
            if isinstance(interrupt_value, Mapping)
            else StreamMessageParser.payload_mapping(interrupt_value)
        )
        action_requests = payload.get(_Fields.ACTION_REQUESTS)
        if not isinstance(action_requests, Sequence) or isinstance(
            action_requests, (str, bytes, bytearray)
        ):
            return ()
        review_configs = cls._review_configs_by_action(
            payload.get(_Fields.REVIEW_CONFIGS)
        )
        approvals: list[dict[str, object]] = []
        for index, raw_action in enumerate(action_requests):
            if not isinstance(raw_action, Mapping):
                continue
            action = raw_action
            action_name = StreamTextHelper.extract(action.get(Keys.Field.NAME))
            if action_name != McpValues.ToolName.CALL_MCP_TOOL:
                continue
            args = action.get(Keys.Field.ARGS)
            if not isinstance(args, Mapping):
                args = {}
            server_name = (
                StreamTextHelper.extract(args.get(_Fields.SERVER_NAME)) or "MCP server"
            )
            tool_name = (
                StreamTextHelper.extract(args.get(_Fields.TOOL_NAME)) or "MCP tool"
            )
            arguments = args.get(_Fields.ARGUMENTS)
            display_name = cls._connector_display_name(server_name)
            action_label = cls._connector_action_name(tool_name)
            read_only = cls._connector_action_is_read_only(tool_name)
            # PR #43 — ApprovalBatch projection.
            #
            # The item_id format is the SAME for every batch size: a
            # ``<batch_id>:<index>`` suffix. The old N==1 special case
            # ("use the bare interrupt_id when there is only one action")
            # hid the batch identity in a string and caused the multi-tool
            # resume crash — N=1 and N=N now follow the same code path.
            approval_id = f"{interrupt_id}:{index}"
            allowed_decisions = review_configs.get(action_name, ())
            risk_level = "low" if read_only else "medium"
            structured = cls._mcp_approval_structured(
                server_name=server_name,
                display_name=display_name,
                tool_name=tool_name,
                read_only=read_only,
                risk_level=risk_level,
                arguments=arguments if isinstance(arguments, Mapping) else {},
            )
            approvals.append(
                {
                    "api_event_type": RuntimeApiEventType.APPROVAL_REQUESTED.value,
                    "event_type": RuntimeApiEventType.APPROVAL_REQUESTED.value,
                    Keys.Field.APPROVAL_ID: approval_id,
                    _Fields.ACTION_ID: approval_id,
                    Keys.Field.APPROVAL_KIND: "mcp_tool",
                    _Fields.NATIVE_INTERRUPT_ID: interrupt_id,
                    _Fields.ACTION_INDEX: index,
                    _Fields.ACTION_COUNT: len(action_requests),
                    # PR #43 — typed batch membership. The batch_id is the
                    # interrupt_id (1:1 with a LangGraph interrupt); the index
                    # is the typed position inside action_requests.
                    _Fields.BATCH_ID: interrupt_id,
                    _Fields.BATCH_INDEX: index,
                    _Fields.SERVER_NAME: server_name,
                    _Fields.DISPLAY_NAME: display_name,
                    _Fields.TOOL_NAME: tool_name,
                    _Fields.ARGUMENTS: arguments if isinstance(arguments, dict) else {},
                    "message": f"Allow {display_name} {action_label}?",
                    _Fields.READ_ONLY: read_only,
                    _Fields.RISK_LEVEL: risk_level,
                    Keys.Field.STATUS: "pending",
                    _Fields.ALLOWED_DECISIONS: list(allowed_decisions),
                    _Fields.GRANT_OPTIONS: ["allow_once"],
                    # Structured consent-card payload. Spreads vendor /
                    # category / reason_code / reversible / params into the
                    # same dict so the client reads them off the event's args
                    # bag without a nested unwrap. Validation failures fall
                    # through with the flat fields only.
                    **structured,
                }
            )
        return tuple(approvals)

    @classmethod
    def _connector_display_name(cls, value: str) -> str:
        """Convert a raw MCP server name slug to a human-readable connector display name."""
        normalized = value.strip()
        lowered = normalized.lower()
        if lowered.startswith("mcp_"):
            normalized = normalized[4:]
        if lowered.endswith("_mcp"):
            normalized = normalized[:-4]
        normalized = normalized.removesuffix("_com").removesuffix("-com")
        words = [word for word in normalized.replace("-", "_").split("_") if word]
        if not words:
            return "Connector"
        acronyms = {"api", "url", "id", "mcp"}
        return " ".join(
            word.upper()
            if word.lower() in acronyms
            else cls._connector_brand_word(word)
            for word in words
        )

    @staticmethod
    def _connector_brand_word(value: str) -> str:
        """Return the canonical brand capitalisation for known connector slugs, falling back to ``capitalize``."""
        brands = {
            "clickup": "ClickUp",
            "github": "GitHub",
            "gitlab": "GitLab",
            "slack": "Slack",
            "google": "Google",
        }
        return brands.get(value.lower(), value.capitalize())

    @classmethod
    def _connector_action_name(cls, tool_name: str) -> str:
        """Classify a tool name into a human-readable action label: search, read, modify, or action."""
        normalized = tool_name.lower()
        if any(term in normalized for term in ("search", "filter", "find", "list")):
            return "search"
        if any(term in normalized for term in ("read", "get", "fetch")):
            return "read"
        if any(
            term in normalized
            for term in ("create", "post", "send", "update", "delete")
        ):
            return "modify"
        return "action"

    @classmethod
    def _connector_action_is_read_only(cls, tool_name: str) -> bool:
        """Return ``True`` when the tool name contains no write-operation terms."""
        normalized = tool_name.lower()
        if any(
            term in normalized
            for term in ("create", "post", "send", "update", "delete", "write")
        ):
            return False
        return True

    # Consent-card structured payload. The client reads the spread fields off
    # the approval event's args bag; if validation fails the structured payload
    # is dropped, a warning is logged, and the approval ships with the flat
    # fields only (the client has a synthesiser fallback).

    # Allow-list of argument keys whose values are safe to project into
    # the consent card's params frame. Body / text / secrets / freeform
    # description fields are excluded by omission — never block-listed.
    _APPROVAL_PARAM_KEYS: tuple[str, ...] = (
        "channel",
        "to",
        "recipient",
        "team",
        "project",
        "repo",
        "ref",
        "branch",
        "issue",
        "page_id",
        "database_id",
        "subject",
        "title",
        "id",
        "name",
        "query",
        "filter",
        "assignee",
        "label",
    )
    _APPROVAL_PARAM_VALUE_MAX = 128
    _APPROVAL_VENDOR_MAX = 32
    _APPROVAL_IRREVERSIBLE_TOKENS: tuple[str, ...] = (
        "delete",
        "remove",
        "drop",
    )

    @classmethod
    def _mcp_approval_structured(
        cls,
        *,
        server_name: str,
        display_name: str,
        tool_name: str,
        read_only: bool,
        risk_level: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        """Build and validate the structured consent-card fields; returns ``{}`` on validation failure."""
        # Vendor-specific recogniser fronts the generic allow-list
        # projector. ``None`` means no vendor matched; fall through to
        # the generic path so unknown vendors stay byte-identical.
        recognised = ApprovalParamRecogniserRegistry.recognise(
            server_name=server_name, arguments=arguments
        )
        params = (
            recognised if recognised is not None else cls._approval_params(arguments)
        )
        try:
            metadata = McpApprovalMetadata(
                vendor=cls._approval_vendor(display_name),
                category=cls._approval_category(read_only),
                reason_code=cls._approval_reason_code(read_only, risk_level),
                reversible=cls._approval_reversible(
                    read_only, tool_name, server_name=server_name
                ),
                params=params,
            )
        except ValidationError as exc:
            _logger.warning(
                "mcp approval metadata validation failed; falling back to flat fields",
                extra={"tool_name": tool_name, "display_name": display_name},
                exc_info=exc,
            )
            return {}
        return metadata.model_dump(mode="json")

    @classmethod
    def _approval_vendor(cls, display_name: str) -> str:
        """Return an uppercase vendor token truncated to the configured max length."""
        token = display_name.upper()[: cls._APPROVAL_VENDOR_MAX]
        return token or "CONNECTOR"

    @classmethod
    def _approval_category(cls, read_only: bool) -> ApprovalCategory:
        """Map the read-only flag to the corresponding ``ApprovalCategory`` enum value."""
        return ApprovalCategory.READ if read_only else ApprovalCategory.WRITE

    @classmethod
    def _approval_reason_code(
        cls, read_only: bool, risk_level: str
    ) -> ApprovalReasonCode:
        """Derive the approval reason code from the read-only flag and risk level."""
        if risk_level in {"high", "critical"}:
            return ApprovalReasonCode.RISK_HIGH
        if read_only:
            return ApprovalReasonCode.READ_ONLY_FIRST_USE
        return ApprovalReasonCode.WRITES_OUT_OF_WORKSPACE

    @classmethod
    def _approval_reversible(
        cls,
        read_only: bool,
        tool_name: str,
        server_name: str = "",
    ) -> ApprovalReversible:
        """Return the reversibility opinion: NOT_APPLICABLE for reads; vendor override or NO for writes."""
        if read_only:
            return ApprovalReversible.NOT_APPLICABLE
        # Recognisers can opt their vendor's writes into the undo window.
        # Default stays NO to avoid promising undo for tools without a
        # compensator.
        opinion = ApprovalParamRecogniserRegistry.reversibility_for(
            server_name=server_name, tool_name=tool_name, read_only=read_only
        )
        if opinion is not None:
            return opinion
        return ApprovalReversible.NO

    @classmethod
    def _approval_params(
        cls, arguments: Mapping[str, object]
    ) -> tuple[ApprovalParam, ...]:
        """Project allow-listed argument keys from ``arguments`` into ``ApprovalParam`` rows."""
        params: list[ApprovalParam] = []
        for key in cls._APPROVAL_PARAM_KEYS:
            if key not in arguments:
                continue
            value = cls._approval_param_value(arguments[key])
            if value is None:
                continue
            params.append(
                ApprovalParam(label=cls._approval_param_label(key), value=value)
            )
            if len(params) >= APPROVAL_MAX_PARAMS:
                break
        return tuple(params)

    @classmethod
    def _approval_param_value(cls, raw: object) -> str | None:
        """Coerce a raw argument value to a displayable string, returning ``None`` for empty or unsupported types."""
        if isinstance(raw, bool):
            return "Yes" if raw else "No"
        if isinstance(raw, (int, float)):
            return str(raw)
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return None
            return stripped[: cls._APPROVAL_PARAM_VALUE_MAX]
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return f"<list of {len(raw)} items>"
        if isinstance(raw, Mapping):
            return f"<object with {len(raw)} keys>"
        return None

    @staticmethod
    def _approval_param_label(key: str) -> str:
        """Convert a snake_case argument key to a title-cased label string."""
        words = [word for word in key.replace("-", "_").split("_") if word]
        if not words:
            return key
        return " ".join(word[:1].upper() + word[1:] for word in words)

    @classmethod
    def _review_configs_by_action(cls, value: object) -> dict[str, tuple[str, ...]]:
        """Parse the ``review_configs`` sequence into an action-name → allowed-decisions mapping."""
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            return {}
        result: dict[str, tuple[str, ...]] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            action_name = StreamTextHelper.extract(item.get(_Fields.ACTION_NAME))
            if action_name is None:
                continue
            allowed = item.get(_Fields.ALLOWED_DECISIONS)
            if isinstance(allowed, Sequence) and not isinstance(
                allowed,
                (str, bytes, bytearray),
            ):
                result[action_name] = tuple(
                    decision for decision in allowed if isinstance(decision, str)
                )
        return result

    @classmethod
    def stream_result_candidate(cls, chunk: object) -> object | None:
        """Return the ``values`` stream data from a main-agent chunk, or ``None`` if the chunk is not a values part."""
        part = StreamPartParser.stream_part(chunk)
        if (
            part is not None
            and StreamPartParser.stream_type(part) == _Fields.VALUES
            and not StreamPartParser.namespace_for(part).is_subagent
        ):
            return part[_Fields.DATA]
        return None

    @classmethod
    def _source_for_event(
        cls,
        event_type: RuntimeApiEventType,
        namespace: StreamNamespace,
    ) -> StreamEventSource:
        """Map an event type and stream namespace to the appropriate ``StreamEventSource`` variant."""
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return StreamEventSource.MCP
        if event_type is RuntimeApiEventType.APPROVAL_REQUESTED:
            return StreamEventSource.RUNTIME
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return StreamEventSource.TOOL
        if (
            event_type
            in {
                RuntimeApiEventType.SUBAGENT_UPDATE,
                RuntimeApiEventType.SUBAGENT_STARTED,
                RuntimeApiEventType.SUBAGENT_PROGRESS,
                RuntimeApiEventType.SUBAGENT_COMPLETED,
            }
            or namespace.is_subagent
        ):
            return StreamEventSource.SUBAGENT
        return StreamEventSource.MAIN_AGENT
