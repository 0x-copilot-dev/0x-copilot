"""Message and payload helpers for runtime stream projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from agent_runtime.execution.contracts import JsonObject
from runtime_api.schemas import RuntimeApiEventType


class StreamMessageParser:
    """Normalize provider messages and JSON-ish stream payloads."""

    @classmethod
    def explicit_api_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        cls.collect_explicit_api_payloads(value, payloads)
        return tuple(payloads)

    @classmethod
    def collect_explicit_api_payloads(
        cls, value: object, payloads: list[JsonObject]
    ) -> None:
        if isinstance(value, str):
            parsed = cls.parse_json_mapping(value)
            if parsed is not None:
                cls.collect_explicit_api_payloads(parsed, payloads)
            return
        if isinstance(value, Mapping):
            payload = cls.payload_mapping(value)
            if cls.api_event_type(payload) is not None:
                payloads.append(payload)
                return
            for item in value.values():
                cls.collect_explicit_api_payloads(item, payloads)
            return
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                cls.collect_explicit_api_payloads(item, payloads)
            return
        payload = cls.object_payload_mapping(value)
        if not payload:
            return
        if cls.api_event_type(payload) is not None:
            payloads.append(payload)
            return
        for item in payload.values():
            cls.collect_explicit_api_payloads(item, payloads)

    @classmethod
    def contains_explicit_api_event(cls, value: object) -> bool:
        return bool(cls.explicit_api_payloads(value))

    @classmethod
    def parse_json_mapping(cls, value: str) -> JsonObject | None:
        if not value.strip().startswith("{"):
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, Mapping):
            return None
        return cls.payload_mapping(parsed)

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
    def api_event_type(
        cls, payload: Mapping[str, object]
    ) -> RuntimeApiEventType | None:
        value = (
            payload.get("api_event_type")
            or payload.get("event_type")
            or payload.get("event")
        )
        if not isinstance(value, str):
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def tool_call_chunks(cls, message: object) -> tuple[object, ...]:
        if isinstance(message, Mapping):
            value = message.get("tool_call_chunks") or message.get("tool_calls") or ()
        else:
            value = getattr(message, "tool_call_chunks", None) or getattr(
                message, "tool_calls", ()
            )
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return tuple(value)
        return ()

    @classmethod
    def is_tool_result_message(cls, message: object) -> bool:
        if isinstance(message, Mapping):
            return message.get("type") in {"tool", "tool_result"}
        return (
            bool(getattr(message, "tool_call_id", None))
            or getattr(message, "type", None) == "tool"
        )

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
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                cls.collect_update_messages(item, messages)

    @classmethod
    def payload_mapping(cls, value: object) -> JsonObject:
        if isinstance(value, Mapping):
            return {str(key): cls.json_value(item) for key, item in value.items()}
        if value is None:
            return {}
        object_payload = cls.object_payload_mapping(value)
        if object_payload:
            return object_payload
        return {"content": cls.json_value(value)}

    @classmethod
    def object_payload_mapping(cls, value: object) -> JsonObject:
        payload: JsonObject = {}
        for key in (
            "type",
            "name",
            "id",
            "tool_call_id",
            "call_id",
            "tool_name",
            "content",
            "status",
            "args",
            "summary",
            "index",
        ):
            if not hasattr(value, key):
                continue
            item = getattr(value, key)
            if item is not None:
                payload[key] = cls.json_value(item)
        return payload

    @classmethod
    def json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): cls.json_value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            values = [cls.json_value(item) for item in value]
            if all(
                isinstance(item, str | int | float | bool) or item is None
                for item in values
            ):
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
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
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

    @classmethod
    def raw_text(cls, value: object) -> str | None:
        if not isinstance(value, str) or value == "":
            return None
        return value
