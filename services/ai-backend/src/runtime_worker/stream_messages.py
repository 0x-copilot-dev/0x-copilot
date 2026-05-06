"""Message and payload helpers for runtime stream projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json

from agent_runtime.execution.contracts import JsonObject
from runtime_api.schemas import RuntimeApiEventType


class StreamTextHelper:
    """Canonical text-extraction helper shared across stream processors."""

    @staticmethod
    def extract(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip() or None


class _ReasoningBlock:
    """Provider-specific content-block constants for reasoning chunks.

    LangChain surfaces native thinking/reasoning content uniformly through
    ``AIMessageChunk.content`` as a list of typed blocks; only the block
    ``type`` discriminator differs per provider. Centralising the strings
    here keeps the extractor logic provider-agnostic and makes "add a new
    provider" a one-line edit instead of a grep across the worker.
    """

    ANTHROPIC_THINKING = "thinking"
    ANTHROPIC_TEXT_KEY = "thinking"
    ANTHROPIC_SIGNATURE = "thinking_signature"
    OPENAI_DELTA = "reasoning_summary_text_delta"
    OPENAI_DONE = "reasoning_summary_text_done"


class _Fields:
    API_EVENT_TYPE = "api_event_type"
    EVENT_TYPE = "event_type"
    EVENT = "event"
    TOOL_CALL_CHUNKS = "tool_call_chunks"
    TOOL_CALLS = "tool_calls"
    TYPE = "type"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"
    MESSAGES = "messages"
    MESSAGE = "message"
    TEXT = "text"
    CONTENT = "content"
    NAME = "name"
    ID = "id"
    TOOL_CALL_ID = "tool_call_id"
    CALL_ID = "call_id"
    TOOL_NAME = "tool_name"
    STATUS = "status"
    ARGS = "args"
    SUMMARY = "summary"
    INDEX = "index"
    TASK_ID = "task_id"
    SUBAGENT_ID = "subagent_id"
    SUBAGENT_NAME = "subagent_name"
    DISPLAY_TITLE = "display_title"


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
            _Fields.TASK_ID,
            _Fields.SUBAGENT_ID,
            _Fields.SUBAGENT_NAME,
            _Fields.DISPLAY_TITLE,
            _Fields.SUMMARY,
            _Fields.MESSAGE,
            _Fields.STATUS,
        ):
            text = StreamTextHelper.extract(payload.get(key))
            if text is not None:
                safe_payload[key] = text
        return safe_payload

    @classmethod
    def api_event_type(
        cls, payload: Mapping[str, object]
    ) -> RuntimeApiEventType | None:
        value = (
            payload.get(_Fields.API_EVENT_TYPE)
            or payload.get(_Fields.EVENT_TYPE)
            or payload.get(_Fields.EVENT)
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
            value = (
                message.get(_Fields.TOOL_CALL_CHUNKS)
                or message.get(_Fields.TOOL_CALLS)
                or ()
            )
        else:
            value = getattr(message, _Fields.TOOL_CALL_CHUNKS, None) or getattr(
                message, _Fields.TOOL_CALLS, ()
            )
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            return tuple(value)
        return ()

    @classmethod
    def is_tool_result_message(cls, message: object) -> bool:
        if isinstance(message, Mapping):
            return message.get(_Fields.TYPE) in {_Fields.TOOL, _Fields.TOOL_RESULT}
        return (
            bool(getattr(message, _Fields.TOOL_CALL_ID, None))
            or getattr(message, _Fields.TYPE, None) == _Fields.TOOL
        )

    @classmethod
    def update_messages(cls, value: object) -> tuple[object, ...]:
        messages: list[object] = []
        cls.collect_update_messages(value, messages)
        return tuple(messages)

    @classmethod
    def collect_update_messages(cls, value: object, messages: list[object]) -> None:
        if isinstance(value, Mapping):
            raw_messages = value.get(_Fields.MESSAGES)
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
        return {_Fields.CONTENT: cls.json_value(value)}

    @classmethod
    def object_payload_mapping(cls, value: object) -> JsonObject:
        payload: JsonObject = {}
        for key in (
            _Fields.TYPE,
            _Fields.NAME,
            _Fields.ID,
            _Fields.TOOL_CALL_ID,
            _Fields.CALL_ID,
            _Fields.TOOL_NAME,
            _Fields.CONTENT,
            _Fields.STATUS,
            _Fields.ARGS,
            _Fields.SUMMARY,
            _Fields.INDEX,
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
                text = item.get(_Fields.TEXT) or item.get(_Fields.CONTENT)
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
            return payload.get(_Fields.MESSAGE) or payload
        return payload

    @classmethod
    def message_delta(cls, message: object) -> str | None:
        if isinstance(message, Mapping):
            return cls.content_delta_to_text(message.get(_Fields.CONTENT))
        return cls.content_delta_to_text(getattr(message, _Fields.CONTENT, None))

    @classmethod
    def raw_content(cls, message: object) -> object:
        """Return the chunk's ``content`` without ``payload_mapping``'s flattening.

        ``payload_mapping`` collapses list-of-mappings to a single string via
        ``text_from_content_blocks`` — that drops the per-block ``type`` field
        we need to distinguish reasoning blocks from text blocks. Reasoning
        extractors must walk the raw structure.
        """

        if isinstance(message, Mapping):
            return message.get(_Fields.CONTENT)
        return getattr(message, _Fields.CONTENT, None)

    @classmethod
    def reasoning_delta(cls, message: object) -> str | None:
        """Extract reasoning text from one parsed ``AIMessageChunk``.

        Recognises the two provider shapes LangChain surfaces today:

        - **Anthropic** (`langchain-anthropic`): ``{"type": "thinking",
          "thinking": "…"}`` blocks while extended thinking is on.
        - **OpenAI Responses** (`langchain-openai` with ``output_version=
          "responses/v1"``): ``{"type": "reasoning_summary_text_delta",
          "text": "…"}`` blocks while ``reasoning.summary`` is configured.

        Returns the concatenated reasoning text for the chunk, or ``None``
        when no reasoning blocks are present. The plain-text path
        (`message_delta`) is unaffected — text and reasoning are extracted
        independently from the same chunk.
        """

        content = cls.raw_content(message)
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            return None
        parts: list[str] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get(_Fields.TYPE)
            if block_type == _ReasoningBlock.ANTHROPIC_THINKING:
                # Preserve leading/trailing whitespace — provider delta
                # chunks carry meaningful spaces between word fragments;
                # stripping each chunk would coalesce ``"summary "`` +
                # ``"tail"`` into ``"summarytail"``.
                value = cls.raw_text(block.get(_ReasoningBlock.ANTHROPIC_TEXT_KEY))
            elif block_type == _ReasoningBlock.OPENAI_DELTA:
                value = cls.raw_text(block.get(_Fields.TEXT))
            else:
                continue
            if value is not None:
                parts.append(value)
        return "".join(parts) or None

    @classmethod
    def reasoning_finalised(cls, message: object) -> bool:
        """True when the chunk closes a reasoning span.

        Anthropic stamps ``thinking_signature`` on the final block of a
        thinking span; OpenAI Responses emits a sentinel
        ``reasoning_summary_text_done`` block. Either marker means the
        worker should emit a final ``reasoning_summary`` cap and clear the
        per-span buffer. When neither marker shows up before the next
        non-reasoning content arrives, the FE falls back to its own
        ``closeReasoningIfRunning`` heuristic.
        """

        content = cls.raw_content(message)
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            return False
        for block in content:
            if not isinstance(block, Mapping):
                continue
            block_type = block.get(_Fields.TYPE)
            if block_type == _ReasoningBlock.ANTHROPIC_THINKING and block.get(
                _ReasoningBlock.ANTHROPIC_SIGNATURE
            ):
                return True
            if block_type == _ReasoningBlock.OPENAI_DONE:
                return True
        return False

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
                    # Skip reasoning content blocks here — they are
                    # extracted independently by ``reasoning_delta`` and
                    # would otherwise double-fire as visible MODEL_DELTA
                    # text. OpenAI Responses' ``reasoning_summary_text_delta``
                    # block carries a ``text`` field that would match the
                    # generic extractor below; Anthropic's ``thinking``
                    # block uses a ``thinking`` key (so it's already
                    # invisible to the generic path) but we filter on
                    # ``type`` for symmetry.
                    if cls._is_reasoning_block(item):
                        continue
                    text = item.get(_Fields.TEXT) or item.get(_Fields.CONTENT)
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts)
            return text or None
        return None

    @classmethod
    def _is_reasoning_block(cls, block: Mapping[str, object]) -> bool:
        block_type = block.get(_Fields.TYPE)
        return block_type in {
            _ReasoningBlock.ANTHROPIC_THINKING,
            _ReasoningBlock.OPENAI_DELTA,
            _ReasoningBlock.OPENAI_DONE,
        }

    @classmethod
    def text(cls, value: object) -> str | None:
        return StreamTextHelper.extract(value)

    @classmethod
    def raw_text(cls, value: object) -> str | None:
        if not isinstance(value, str) or value == "":
            return None
        return value
