"""Map runtime stream chunks into persisted runtime API events."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from agent_runtime.api.constants import Keys, Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.execution.contracts import StreamEventSource
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
    # PR 3.2.5 Phase 3 — persisted on the approval record's metadata so the
    # resolution handler can detect subagent-scoped pauses without
    # rescanning the event log. Mirrors the envelope-level field.
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

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        self.event_producer = event_producer
        self.update_processor = StreamUpdateProcessor(event_producer)
        self.message_processor = StreamMessageProcessor(
            event_producer, self.update_processor
        )

    async def append_activity_events(
        self,
        *,
        run: RunRecord,
        chunk: object,
        delta: str | None,
    ) -> None:
        part = StreamPartParser.stream_part(chunk)
        if part is None:
            return

        stream_type = StreamPartParser.stream_type(part)
        namespace = StreamPartParser.namespace_for(part)
        data = part[_Fields.DATA]
        metadata = namespace.metadata(stream_type)

        # PR `subagent-call-id-link` — `atlas_task_tool` writes the
        # supervisor's `task` call_id into each subagent's RunnableConfig
        # metadata. LangGraph propagates that metadata onto every chunk
        # the subgraph emits. Read it here and pin a deterministic
        # `(run_id, subgraph_task_id) → supervisor_call_id` mapping so
        # downstream emits resolve to the supervisor call_id (which the
        # FE matches against the `run_subagent` tool part's toolCallId)
        # instead of the raw LangGraph subgraph UUID.
        #
        # Resolution rules:
        # 1. If chunk metadata gave us the linkage (production path with
        #    our patched task tool), use the cached supervisor call_id.
        # 2. If no metadata (legacy / synthetic test fixtures), fall
        #    back to the raw subgraph_task_id so the historical contract
        #    holds. The FIFO-pop fallback intentionally stays inside
        #    `stream_tools.StreamMessageProcessor.process` where it was
        #    the source of truth — pulling it forward here would drain
        #    the queue before later subagents' lifecycle events can
        #    register their call_ids.
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

        for payload in StreamMessageParser.explicit_api_payloads(data):
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
        approval_id = StreamTextHelper.extract(payload.get(Keys.Field.APPROVAL_ID))
        if approval_id is None:
            return
        existing = await self.event_producer.persistence.get_approval_request(
            org_id=run.org_id,
            approval_id=approval_id,
        )
        if existing is not None:
            return
        # PR 3.2.5 Phase 3 — persist `parent_task_id` on the approval
        # record so `RuntimeApprovalHandler.handle` can detect when a
        # resolution targets a subagent-scoped pause and emit
        # `subagent_resumed` before the LangGraph resume kicks in. We
        # write it as a sibling key on `metadata` (a copy of the original
        # event payload) under the same name the chunk metadata uses so
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

    async def append_native_interrupt_events(
        self,
        *,
        run: RunRecord,
        value: object,
    ) -> bool:
        namespace = StreamNamespace(())
        did_append = False
        for payload in self.native_interrupt_payloads(run, value):
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

    # PR 3.2.5 Phase 3 — when an interrupt event fires inside a subagent
    # (i.e. `parent_task_id` resolved to the supervisor's `task`
    # call_id), emit a sibling `subagent_paused` event so the FE
    # reducer can flip `SubagentEntry.status` to `paused` without
    # inferring from "started but never completed". Resume is emitted
    # separately by the approval handler on resolution.
    #
    # `reason` discriminates the FE copy / icon. `MCP_AUTH_REQUIRED` maps
    # to `mcp_auth`. `APPROVAL_REQUESTED` is further refined by inspecting
    # the payload's `approval_kind`: `ask_a_question` is its own reason so
    # the FE can render "Waiting for answer" instead of generic "Waiting on
    # approval"; everything else (action, mcp_tool) collapses to
    # `approval`.
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
        if isinstance(interrupt, Mapping):
            return interrupt.get(_Fields.VALUE) or interrupt
        return getattr(interrupt, _Fields.VALUE, interrupt)

    @classmethod
    def _native_interrupt_id(cls, interrupt: object, *, fallback: str) -> str:
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
        return normalized

    @classmethod
    def _native_ask_a_question_payload(
        cls,
        interrupt_id: str,
        interrupt_value: object,
    ) -> dict[str, object] | None:
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
        return normalized

    @classmethod
    def native_tool_approval_payloads(
        cls,
        *,
        interrupt_id: str,
        interrupt_value: object,
    ) -> tuple[dict[str, object], ...]:
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
            approval_id = (
                interrupt_id if len(action_requests) == 1 else f"{interrupt_id}:{index}"
            )
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
                    # PR 4.4.6.2 — structured consent-card payload. Spreads
                    # vendor / category / reason_code / reversible / params
                    # into the same dict so the FE reads them off the
                    # event's args bag without a nested unwrap. Validation
                    # failures fall through with the flat fields only.
                    **structured,
                }
            )
        return tuple(approvals)

    @classmethod
    def _connector_display_name(cls, value: str) -> str:
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
        normalized = tool_name.lower()
        if any(
            term in normalized
            for term in ("create", "post", "send", "update", "delete", "write")
        ):
            return False
        return True

    # PR 4.4.6.2 — consent-card structured payload. The FE reads the spread
    # fields off the approval event's args bag; if validation fails we drop
    # the structured payload, log a warning, and ship the approval with the
    # flat fields only (the FE has a synthesiser fallback).

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
        # PR 4.4.6.3 — vendor-specific recogniser fronts the generic
        # allow-list projector. None → no vendor matched, fall through
        # to the Phase-2 path so unknown vendors stay byte-identical.
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
        token = display_name.upper()[: cls._APPROVAL_VENDOR_MAX]
        return token or "CONNECTOR"

    @classmethod
    def _approval_category(cls, read_only: bool) -> ApprovalCategory:
        return ApprovalCategory.READ if read_only else ApprovalCategory.WRITE

    @classmethod
    def _approval_reason_code(
        cls, read_only: bool, risk_level: str
    ) -> ApprovalReasonCode:
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
        if read_only:
            return ApprovalReversible.NOT_APPLICABLE
        # PR 4.4.6.4 — recognisers can opt their vendor's writes into the
        # 60s undo window. Default stays NO to avoid promising undo for
        # tools without a compensator.
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
        words = [word for word in key.replace("-", "_").split("_") if word]
        if not words:
            return key
        return " ".join(word[:1].upper() + word[1:] for word in words)

    @classmethod
    def _review_configs_by_action(cls, value: object) -> dict[str, tuple[str, ...]]:
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
