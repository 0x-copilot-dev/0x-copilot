"""Map runtime stream chunks into persisted runtime API events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType


@dataclass(frozen=True)
class StreamNamespace:
    """Parsed LangGraph v2 namespace metadata."""

    parts: tuple[str, ...]

    @classmethod
    def from_value(cls, value: object) -> "StreamNamespace":
        if isinstance(value, str):
            return cls((value,))
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return cls(tuple(str(item) for item in value))
        return cls(())

    @property
    def subagent_task_id(self) -> str | None:
        for part in self.parts:
            if part.startswith("tools:"):
                return part.split(":", maxsplit=1)[1] or None
        return None

    @property
    def is_subagent(self) -> bool:
        return self.subagent_task_id is not None

    def metadata(self, stream_type: str) -> JsonObject:
        metadata: JsonObject = {"stream_type": stream_type}
        if self.parts:
            metadata["namespace"] = list(self.parts)
        return metadata


class RuntimeStreamPartAdapter:
    """Project LangGraph v2 StreamPart chunks into stable runtime API events."""

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        self.event_producer = event_producer

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

        for payload in self.explicit_api_payloads(data):
            event_type = self.api_event_type(payload)
            if event_type is None:
                continue
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

        if stream_type not in {"updates", "custom"} or self.contains_explicit_api_event(data):
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
            source=StreamEventSource.SUBAGENT if namespace.is_subagent else StreamEventSource.MAIN_AGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS if namespace.is_subagent else RuntimeApiEventType.PROGRESS,
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
            payload = self.tool_call_payload(tool_call)
            event_type = (
                RuntimeApiEventType.TOOL_CALL_STARTED
                if payload.get("tool_name") != "unknown_tool"
                else RuntimeApiEventType.TOOL_CALL_DELTA
            )
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if self.is_tool_result_message(message):
            payload = self.tool_result_payload(message)
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload={
                    "tool_name": payload["tool_name"],
                    "call_id": payload["call_id"],
                    "status": "completed",
                },
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

    def append_subagent_lifecycle_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: JsonObject,
    ) -> bool:
        """Append lifecycle events derived from documented Deep Agents update chunks."""

        emitted = False
        for payload in self.task_tool_call_payloads(data):
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SUBAGENT,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        for payload in self.task_tool_result_payloads(data):
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SUBAGENT,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        if emitted or not namespace.is_subagent:
            return emitted

        payload = self.safe_activity_payload(data)
        payload.setdefault("task_id", namespace.subagent_task_id)
        payload.setdefault("status", "running")
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=namespace.subagent_task_id,
        )
        return True

    @classmethod
    def stream_delta(cls, chunk: object) -> str | None:
        part = cls.stream_part(chunk)
        if part is None or cls.stream_type(part) != "messages":
            return None
        message = cls.message_from_stream_payload(part["data"])
        if cls.tool_call_chunks(message) or cls.is_tool_result_message(message):
            return None
        return cls.message_delta(message)

    @classmethod
    def stream_result_candidate(cls, chunk: object) -> object | None:
        part = cls.stream_part(chunk)
        if part is not None and cls.stream_type(part) == "values":
            return part["data"]
        return None

    @classmethod
    def stream_part(cls, chunk: object) -> dict[str, object] | None:
        if not isinstance(chunk, Mapping):
            return None
        stream_type = chunk.get("type")
        if not isinstance(stream_type, str) or "data" not in chunk:
            return None
        return dict(chunk)

    @classmethod
    def stream_type(cls, part: Mapping[str, object]) -> str:
        return str(part["type"])

    @classmethod
    def namespace_for(cls, part: Mapping[str, object]) -> StreamNamespace:
        return StreamNamespace.from_value(part.get("ns", ()))

    @classmethod
    def explicit_api_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        cls.collect_explicit_api_payloads(value, payloads)
        return tuple(payloads)

    @classmethod
    def collect_explicit_api_payloads(cls, value: object, payloads: list[JsonObject]) -> None:
        if isinstance(value, Mapping):
            payload = cls.payload_mapping(value)
            if cls.api_event_type(payload) is not None:
                payloads.append(payload)
                return
            for item in value.values():
                cls.collect_explicit_api_payloads(item, payloads)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                cls.collect_explicit_api_payloads(item, payloads)

    @classmethod
    def contains_explicit_api_event(cls, value: object) -> bool:
        return bool(cls.explicit_api_payloads(value))

    @classmethod
    def safe_activity_payload(cls, value: object) -> JsonObject:
        payload = cls.payload_mapping(value)
        safe_payload: JsonObject = {}
        for key in (
            "task_id",
            "subagent_id",
            "subagent_name",
            "display_title",
            "summary",
            "message",
            "status",
        ):
            text = cls.text(payload.get(key))
            if text is not None:
                safe_payload[key] = text
        return safe_payload

    @classmethod
    def api_event_type(cls, payload: Mapping[str, object]) -> RuntimeApiEventType | None:
        value = payload.get("api_event_type") or payload.get("event_type") or payload.get("event")
        if not isinstance(value, str):
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def source_for_event(
        cls,
        event_type: RuntimeApiEventType,
        namespace: StreamNamespace,
    ) -> StreamEventSource:
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return StreamEventSource.MCP
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return StreamEventSource.TOOL
        if event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        } or namespace.is_subagent:
            return StreamEventSource.SUBAGENT
        return StreamEventSource.MAIN_AGENT

    @classmethod
    def tool_call_chunks(cls, message: object) -> tuple[object, ...]:
        if isinstance(message, Mapping):
            value = message.get("tool_call_chunks") or message.get("tool_calls") or ()
        else:
            value = getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", ())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(value)
        return ()

    @classmethod
    def tool_call_payload(cls, tool_call: object) -> JsonObject:
        payload = cls.payload_mapping(tool_call)
        tool_name = cls.text(payload.get("name")) or cls.text(payload.get("tool_name")) or "unknown_tool"
        call_id = (
            cls.text(payload.get("id"))
            or cls.text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        args = payload.get("args", {})
        result: JsonObject = {
            "tool_name": tool_name,
            "call_id": call_id,
            "args": args if isinstance(args, Mapping) else {"delta": str(args)},
            "delta": str(args) if args and not isinstance(args, Mapping) else "",
            "status": payload.get("status", "started"),
        }
        summary = cls.text(payload.get("summary"))
        if summary is not None:
            result["summary"] = summary
        return result

    @classmethod
    def is_tool_result_message(cls, message: object) -> bool:
        if isinstance(message, Mapping):
            return message.get("type") in {"tool", "tool_result"}
        return bool(getattr(message, "tool_call_id", None)) or getattr(message, "type", None) == "tool"

    @classmethod
    def tool_result_payload(cls, message: object) -> JsonObject:
        payload = cls.payload_mapping(message)
        tool_name = cls.text(payload.get("name")) or cls.text(payload.get("tool_name")) or "unknown_tool"
        call_id = (
            cls.text(payload.get("tool_call_id"))
            or cls.text(payload.get("id"))
            or cls.text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        excluded = {"type", "name", "id", "tool_call_id", "call_id", "tool_name", "status"}
        output = {key: value for key, value in payload.items() if key not in excluded}
        return {
            "tool_name": tool_name,
            "call_id": call_id,
            "status": payload.get("status", "completed"),
            "output": output or payload,
        }

    @classmethod
    def task_tool_call_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        for message in cls.update_messages(value):
            for tool_call in cls.tool_call_chunks(message):
                payload = cls.payload_mapping(tool_call)
                tool_name = cls.text(payload.get("name")) or cls.text(payload.get("tool_name"))
                if tool_name != "task":
                    continue
                call_id = cls.text(payload.get("id")) or cls.text(payload.get("call_id"))
                if call_id is None:
                    continue
                args = payload.get("args")
                args_payload = args if isinstance(args, Mapping) else {}
                subagent_name = (
                    cls.text(args_payload.get("subagent_type"))
                    or cls.text(args_payload.get("subagent_name"))
                    or "subagent"
                )
                summary = cls.text(args_payload.get("description")) or cls.text(
                    args_payload.get("task")
                )
                event_payload: JsonObject = {
                    "task_id": call_id,
                    "subagent_name": subagent_name,
                    "status": "queued",
                }
                if summary is not None:
                    event_payload["summary"] = summary
                payloads.append(event_payload)
        return tuple(payloads)

    @classmethod
    def task_tool_result_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        for message in cls.update_messages(value):
            if not cls.is_tool_result_message(message):
                continue
            payload = cls.payload_mapping(message)
            tool_name = cls.text(payload.get("name")) or cls.text(payload.get("tool_name"))
            if tool_name != "task":
                continue
            call_id = (
                cls.text(payload.get("tool_call_id"))
                or cls.text(payload.get("id"))
                or cls.text(payload.get("call_id"))
            )
            if call_id is None:
                continue
            summary = cls.content_delta_to_text(payload.get("content")) or cls.text(
                payload.get("message")
            )
            event_payload: JsonObject = {
                "task_id": call_id,
                "subagent_name": "subagent",
                "status": "completed",
            }
            if summary is not None:
                event_payload["summary"] = summary
            payloads.append(event_payload)
        return tuple(payloads)

    @classmethod
    def update_messages(cls, value: object) -> tuple[object, ...]:
        messages: list[object] = []
        cls.collect_update_messages(value, messages)
        return tuple(messages)

    @classmethod
    def collect_update_messages(cls, value: object, messages: list[object]) -> None:
        if isinstance(value, Mapping):
            raw_messages = value.get("messages")
            if isinstance(raw_messages, Sequence) and not isinstance(
                raw_messages,
                (str, bytes, bytearray),
            ):
                messages.extend(raw_messages)
            for item in value.values():
                cls.collect_update_messages(item, messages)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                cls.collect_update_messages(item, messages)

    @classmethod
    def payload_mapping(cls, value: object) -> JsonObject:
        if isinstance(value, Mapping):
            return {str(key): cls.json_value(item) for key, item in value.items()}
        if value is None:
            return {}
        return {"content": cls.json_value(value)}

    @classmethod
    def json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): cls.json_value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = [cls.json_value(item) for item in value]
            if all(isinstance(item, str | int | float | bool) or item is None for item in values):
                return values
            text = cls.text_from_content_blocks(values)
            return text if text is not None else str(values)
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        return str(value)

    @classmethod
    def text_from_content_blocks(cls, values: Sequence[object]) -> str | None:
        parts: list[str] = []
        for item in values:
            if isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        text = "".join(parts).strip()
        return text or None

    @classmethod
    def message_from_stream_payload(cls, payload: object) -> object:
        if isinstance(payload, tuple) and payload:
            return payload[0]
        if isinstance(payload, Mapping):
            return payload.get("message") or payload
        return payload

    @classmethod
    def message_delta(cls, message: object) -> str | None:
        if isinstance(message, Mapping):
            return cls.content_delta_to_text(message.get("content"))
        return cls.content_delta_to_text(getattr(message, "content", None))

    @classmethod
    def content_delta_to_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value or None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts)
            return text or None
        return None

    @classmethod
    def text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip() or None
