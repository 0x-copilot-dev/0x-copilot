"""Map runtime stream chunks into persisted runtime API events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import (
    ApprovalRequestRecord,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventVisibility,
)
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser
from runtime_worker.stream_subagents import SubagentEventProjector
from runtime_worker.stream_tools import ToolCallStreamState


class RuntimeStreamPartAdapter(SubagentEventProjector, StreamPartParser):
    """Project LangGraph v2 StreamPart chunks into stable runtime API events."""

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        self.event_producer = event_producer
        self._tool_call_states: dict[
            tuple[str, tuple[str, ...], str], ToolCallStreamState
        ] = {}
        self._tool_call_ids: dict[tuple[str, str], ToolCallStreamState] = {}
        self._subagent_lifecycle_keys: set[tuple[str, RuntimeApiEventType, str]] = set()

    def append_activity_events(
        self,
        *,
        run: RunRecord,
        chunk: object,
        delta: str | None,
    ) -> None:
        part = self.stream_part(chunk)
        if part is None:
            return

        stream_type = self.stream_type(part)
        namespace = self.namespace_for(part)
        data = part["data"]
        metadata = namespace.metadata(stream_type)
        parent_task_id = namespace.subagent_task_id
        source_tool_call_id = (
            self.source_tool_call_id_for_payload(data)
            if stream_type == "messages"
            else None
        )

        native_payloads = self.native_interrupt_payloads(run, data)
        for payload in native_payloads:
            event_type = self.api_event_type(payload)
            if event_type is None:
                continue
            self.create_approval_request(run=run, payload=payload)
            self.event_producer.append_api_event(
                run=run,
                source=self.source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
        if native_payloads:
            return

        for payload in self.explicit_api_payloads(data):
            event_type = self.api_event_type(payload)
            if event_type is None:
                continue
            if (
                event_type
                in {
                    RuntimeApiEventType.APPROVAL_REQUESTED,
                    RuntimeApiEventType.MCP_AUTH_REQUIRED,
                }
                and source_tool_call_id is not None
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
                self.create_approval_request(run=run, payload=payload)
            self.event_producer.append_api_event(
                run=run,
                source=self.source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if stream_type == "messages":
            message = self.message_from_stream_payload(data)
            self.append_message_activity_events(
                run=run,
                namespace=namespace,
                message=message,
                delta=delta,
            )
            return

        if stream_type not in {"updates", "custom"} or self.contains_explicit_api_event(
            data
        ):
            return

        if stream_type == "updates" and self.append_subagent_lifecycle_events(
            run=run,
            namespace=namespace,
            data=data,
            metadata=metadata,
        ):
            return

        payload = self.safe_activity_payload(data)
        if not payload:
            return
        self.event_producer.append_api_event(
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

    def append_message_activity_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        message: object,
        delta: str | None,
    ) -> None:
        metadata = namespace.metadata("messages")
        parent_task_id = namespace.subagent_task_id

        for tool_call in self.tool_call_chunks(message):
            self.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call=tool_call,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if self.is_tool_result_message(message):
            payload = self.tool_result_payload(message)
            payload = self.tool_result_payload_with_state(run.run_id, payload)
            if payload[Keys.Field.TOOL_NAME] == Values.Tool.TASK:
                state = self.tool_call_state_for_payload(run.run_id, payload)
                self.append_task_lifecycle_event(
                    run=run,
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    payload=self.task_tool_result_payload(
                        payload,
                        subagent_name=state.subagent_name
                        if state is not None
                        else None,
                        short_summary=state.short_summary
                        if state is not None
                        else None,
                    ),
                    metadata=metadata,
                )
                return
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            completed_payload = {
                Keys.Field.TOOL_NAME: payload[Keys.Field.TOOL_NAME],
                Keys.Field.CALL_ID: payload[Keys.Field.CALL_ID],
                Keys.Field.STATUS: Values.Status.COMPLETED,
            }
            if (
                self.text(payload.get(Keys.Field.VISIBILITY))
                == RuntimeEventVisibility.INTERNAL.value
            ):
                self.mark_internal_visibility(completed_payload)
            else:
                self.apply_tool_visibility(completed_payload)
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload=completed_payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

    @classmethod
    def stream_delta(cls, chunk: object) -> str | None:
        part = cls.stream_part(chunk)
        if part is None or cls.stream_type(part) != "messages":
            return None
        if cls.namespace_for(part).is_subagent:
            return None
        message = cls.message_from_stream_payload(part["data"])
        if cls.tool_call_chunks(message) or cls.is_tool_result_message(message):
            return None
        return cls.message_delta(message)

    @classmethod
    def source_tool_call_id_for_payload(cls, payload: object) -> str | None:
        message = cls.message_from_stream_payload(payload)
        if not cls.is_tool_result_message(message):
            return None
        message_payload = cls.payload_mapping(message)
        return (
            cls.text(message_payload.get(Keys.Field.TOOL_CALL_ID))
            or cls.text(message_payload.get(Keys.Field.CALL_ID))
            or cls.text(message_payload.get(Keys.Field.ID))
        )

    def create_approval_request(
        self,
        *,
        run: RunRecord,
        payload: dict[str, object],
    ) -> None:
        approval_id = self.text(payload.get(Keys.Field.APPROVAL_ID))
        if approval_id is None:
            return
        if (
            self.event_producer.persistence.get_approval_request(
                org_id=run.org_id,
                approval_id=approval_id,
            )
            is not None
        ):
            return
        self.event_producer.persistence.create_approval_request(
            record=ApprovalRequestRecord(
                approval_id=approval_id,
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                user_id=run.user_id,
                metadata=payload,
            )
        )

    def append_native_interrupt_events(
        self,
        *,
        run: RunRecord,
        value: object,
    ) -> bool:
        namespace = StreamNamespace(())
        did_append = False
        for payload in self.native_interrupt_payloads(run, value):
            event_type = self.api_event_type(payload)
            if event_type is None:
                continue
            self.create_approval_request(run=run, payload=payload)
            self.event_producer.append_api_event(
                run=run,
                source=self.source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=namespace.metadata("values"),
            )
            did_append = True
        return did_append

    @classmethod
    def payload_with_action_id(
        cls,
        event_type: RuntimeApiEventType,
        payload: dict[str, object],
    ) -> dict[str, object]:
        approval_id = cls.text(payload.get(Keys.Field.APPROVAL_ID)) or cls.text(
            payload.get("action_id")
        )
        if approval_id is None:
            return payload
        normalized = {
            **payload,
            Keys.Field.APPROVAL_ID: approval_id,
            "action_id": cls.text(payload.get("action_id")) or approval_id,
        }
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            normalized.setdefault(Keys.Field.APPROVAL_KIND, "mcp_auth")
        return normalized

    @classmethod
    def native_interrupt_payloads(
        cls,
        run: RunRecord,
        value: object,
    ) -> tuple[dict[str, object], ...]:
        payloads: list[dict[str, object]] = []
        for interrupt_index, interrupt in enumerate(cls.native_interrupts(value)):
            interrupt_id = cls.native_interrupt_id(
                interrupt,
                fallback=f"interrupt:{run.run_id}:{interrupt_index}",
            )
            interrupt_value = cls.native_interrupt_value(interrupt)
            auth_payload = cls.native_auth_payload(interrupt_id, interrupt_value)
            if auth_payload is not None:
                payloads.append(auth_payload)
                continue
            payloads.extend(
                cls.native_tool_approval_payloads(
                    interrupt_id=interrupt_id,
                    interrupt_value=interrupt_value,
                )
            )
        return tuple(payloads)

    @classmethod
    def native_interrupts(cls, value: object) -> tuple[object, ...]:
        raw = value.get("__interrupt__") if isinstance(value, Mapping) else None
        if raw is None and isinstance(value, Mapping):
            raw = value.get("interrupts")
        if raw is None:
            raw = getattr(value, "interrupts", None)
        if raw is None:
            raw = cls.payload_mapping(value).get("__interrupt__")
        if raw is None:
            return ()
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return tuple(raw)
        return (raw,)

    @classmethod
    def native_interrupt_value(cls, interrupt: object) -> object:
        if isinstance(interrupt, Mapping):
            return interrupt.get("value") or interrupt
        return getattr(interrupt, "value", interrupt)

    @classmethod
    def native_interrupt_id(cls, interrupt: object, *, fallback: str) -> str:
        if isinstance(interrupt, Mapping):
            value = interrupt.get("id") or interrupt.get("interrupt_id")
        else:
            value = getattr(interrupt, "id", None)
        return cls.text(value) or fallback

    @classmethod
    def native_auth_payload(
        cls,
        interrupt_id: str,
        interrupt_value: object,
    ) -> dict[str, object] | None:
        payload = cls.payload_mapping(interrupt_value)
        if cls.api_event_type(payload) is not RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return None
        normalized = cls.payload_with_action_id(
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            {
                **payload,
                "native_interrupt_id": interrupt_id,
                "action_id": cls.text(payload.get("action_id")) or interrupt_id,
            },
        )
        normalized.setdefault(Keys.Field.APPROVAL_ID, interrupt_id)
        normalized.setdefault(Keys.Field.APPROVAL_KIND, "mcp_auth")
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
            else cls.payload_mapping(interrupt_value)
        )
        action_requests = payload.get("action_requests")
        if not isinstance(action_requests, Sequence) or isinstance(
            action_requests, (str, bytes, bytearray)
        ):
            return ()
        review_configs = cls.review_configs_by_action(payload.get("review_configs"))
        approvals: list[dict[str, object]] = []
        for index, raw_action in enumerate(action_requests):
            if not isinstance(raw_action, Mapping):
                continue
            action = raw_action
            action_name = cls.text(action.get("name"))
            if action_name != McpValues.ToolName.CALL_MCP_TOOL:
                continue
            args = action.get("args")
            if not isinstance(args, Mapping):
                args = {}
            server_name = cls.text(args.get("server_name")) or "MCP server"
            tool_name = cls.text(args.get("tool_name")) or "MCP tool"
            arguments = args.get("arguments")
            display_name = cls.connector_display_name(server_name)
            action_label = cls.connector_action_name(tool_name)
            read_only = cls.connector_action_is_read_only(tool_name)
            approval_id = (
                interrupt_id if len(action_requests) == 1 else f"{interrupt_id}:{index}"
            )
            allowed_decisions = review_configs.get(action_name, ())
            approvals.append(
                {
                    "api_event_type": RuntimeApiEventType.APPROVAL_REQUESTED.value,
                    "event_type": RuntimeApiEventType.APPROVAL_REQUESTED.value,
                    Keys.Field.APPROVAL_ID: approval_id,
                    "action_id": approval_id,
                    Keys.Field.APPROVAL_KIND: "mcp_tool",
                    "native_interrupt_id": interrupt_id,
                    "action_index": index,
                    "action_count": len(action_requests),
                    "server_name": server_name,
                    "display_name": display_name,
                    "tool_name": tool_name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "message": f"Allow {display_name} {action_label}?",
                    "read_only": read_only,
                    "risk_level": "low" if read_only else "medium",
                    "status": "pending",
                    "allowed_decisions": list(allowed_decisions),
                    "grant_options": ["allow_once"],
                }
            )
        return tuple(approvals)

    @classmethod
    def connector_display_name(cls, value: str) -> str:
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
            word.upper() if word.lower() in acronyms else cls.connector_brand_word(word)
            for word in words
        )

    @classmethod
    def connector_brand_word(cls, value: str) -> str:
        brands = {
            "clickup": "ClickUp",
            "github": "GitHub",
            "gitlab": "GitLab",
            "slack": "Slack",
            "google": "Google",
        }
        return brands.get(value.lower(), value.capitalize())

    @classmethod
    def connector_action_name(cls, tool_name: str) -> str:
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
    def connector_action_is_read_only(cls, tool_name: str) -> bool:
        normalized = tool_name.lower()
        if any(
            term in normalized
            for term in ("create", "post", "send", "update", "delete", "write")
        ):
            return False
        return True

    @classmethod
    def review_configs_by_action(cls, value: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            return {}
        result: dict[str, tuple[str, ...]] = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            action_name = cls.text(item.get("action_name"))
            if action_name is None:
                continue
            allowed = item.get("allowed_decisions")
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
        part = cls.stream_part(chunk)
        if (
            part is not None
            and cls.stream_type(part) == "values"
            and not cls.namespace_for(part).is_subagent
        ):
            return part["data"]
        return None

    @classmethod
    def source_for_event(
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
