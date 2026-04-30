"""LangGraph stream normalization for product-ready runtime events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    JsonObject,
    ObservationEvent,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
    SubagentLifecycleEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent_runtime.observability.constants import Defaults, Keys, Messages, Values
from agent_runtime.observability.tracing import TraceContext


class LangGraphStreamNormalizer:
    """Translate raw LangGraph v2 chunks into stable, redacted stream events."""

    def normalize(
        self,
        raw_event: Mapping[str, object],
        context: object,
        *,
        include_internal: bool = False,
    ) -> Sequence[StreamEvent]:
        """Return normalized events without exposing raw LangGraph namespace tuples."""

        try:
            trace_id = TraceContext.trace_id_for(raw_event=raw_event, context=context)
            namespace = self._namespace_for(raw_event)
            if self._is_internal_summarization(raw_event, namespace) and not include_internal:
                return ()

            mode = self._stream_mode_for(raw_event)
            chunk = self._chunk_for(raw_event)
            metadata = self._metadata_for(raw_event, namespace=namespace, mode=mode)

            if self._has_explicit_event_type(raw_event, chunk):
                return self._normalize_explicit_event(
                    raw_event=raw_event,
                    chunk=chunk,
                    context=context,
                    trace_id=trace_id,
                    namespace=namespace,
                    metadata=metadata,
                )
            if mode == Values.StreamMode.MESSAGES:
                return self._normalize_message_event(
                    chunk=chunk,
                    raw_event=raw_event,
                    trace_id=trace_id,
                    namespace=namespace,
                    metadata=metadata,
                )
            if mode == Values.StreamMode.CUSTOM:
                return self._normalize_progress_event(
                    raw_event=raw_event,
                    chunk=chunk,
                    trace_id=trace_id,
                    namespace=namespace,
                    metadata=metadata,
                    event_type=StreamEventType.CUSTOM,
                )
            if mode in {
                Values.StreamMode.UPDATES,
                Values.StreamMode.VALUES,
                Values.StreamMode.DEBUG,
            }:
                return self._normalize_progress_event(
                    raw_event=raw_event,
                    chunk=chunk,
                    trace_id=trace_id,
                    namespace=namespace,
                    metadata=metadata,
                    event_type=StreamEventType.PROGRESS,
                )
            return (
                self._error_event(
                    trace_id=trace_id,
                    metadata=metadata,
                    safe_message=Messages.Events.UNKNOWN_STREAM_MODE,
                ),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return (
                self._error_event(
                    trace_id=TraceContext.trace_id_for(raw_event=raw_event, context=context),
                    metadata={},
                    safe_message=Messages.Events.MALFORMED_CHUNK,
                ),
            )

    @classmethod
    def _normalize_explicit_event(
        cls,
        *,
        raw_event: Mapping[str, object],
        chunk: object,
        context: object,
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
    ) -> Sequence[StreamEvent]:
        event_name = cls._event_type_for(raw_event, chunk)
        payload = cls._payload_mapping(chunk)
        if event_name == Values.EventType.TOOL_CALL:
            return cls._tool_call_events(
                payload=payload,
                raw_event=raw_event,
                trace_id=trace_id,
                namespace=namespace,
                metadata=metadata,
            )
        if event_name == Values.EventType.TOOL_RESULT:
            return cls._tool_result_events(
                payload=payload,
                raw_event=raw_event,
                trace_id=trace_id,
                namespace=namespace,
                metadata=metadata,
            )
        if event_name in {Values.EventType.LIFECYCLE, Values.EventType.SUBAGENT_UPDATE}:
            return cls._subagent_lifecycle_events(
                payload=payload,
                raw_event=raw_event,
                trace_id=trace_id,
                namespace=namespace,
                metadata=metadata,
            )
        if event_name in cls._api_event_types():
            payload[Keys.Field.API_EVENT_TYPE] = event_name
            return cls._normalize_progress_event(
                raw_event=raw_event,
                chunk=payload,
                trace_id=trace_id,
                namespace=namespace,
                metadata=metadata,
                event_type=StreamEventType.CUSTOM,
            )
        if event_name == Values.EventType.OBSERVATION:
            observation = ObservationEvent(
                metric_name=payload[Keys.Field.METRIC_NAME],
                value=float(payload[Keys.Field.VALUE]),
                trace_id=trace_id,
                tags=payload.get(Keys.Field.TAGS, {}),
            )
            return (
                cls._event(
                    source=StreamEventSource.SYSTEM,
                    event_type=StreamEventType.OBSERVATION,
                    trace_id=trace_id,
                    payload=observation.model_dump(mode="json"),
                    metadata=metadata,
                ),
            )
        return cls._normalize_progress_event(
            raw_event=raw_event,
            chunk=payload,
            trace_id=trace_id,
            namespace=namespace,
            metadata=metadata,
            event_type=cls._stream_event_type(event_name),
        )

    @classmethod
    def _normalize_message_event(
        cls,
        *,
        chunk: object,
        raw_event: Mapping[str, object],
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
    ) -> Sequence[StreamEvent]:
        message = cls._message_for(chunk)
        tool_calls = cls._tool_calls_for(message)
        if tool_calls:
            events: list[StreamEvent] = []
            for tool_call in tool_calls:
                events.extend(
                    cls._tool_call_events(
                        payload=cls._payload_mapping(tool_call),
                        raw_event=raw_event,
                        trace_id=trace_id,
                        namespace=namespace,
                        metadata=metadata,
                    )
                )
            return tuple(events)

        if cls._is_tool_result_message(message):
            return cls._tool_result_events(
                payload=cls._payload_mapping(message),
                raw_event=raw_event,
                trace_id=trace_id,
                namespace=namespace,
                metadata=metadata,
            )

        return cls._normalize_progress_event(
            raw_event=raw_event,
            chunk=message,
            trace_id=trace_id,
            namespace=namespace,
            metadata=metadata,
            event_type=StreamEventType.PROGRESS,
        )

    @classmethod
    def _normalize_progress_event(
        cls,
        *,
        raw_event: Mapping[str, object],
        chunk: object,
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
        event_type: StreamEventType,
    ) -> Sequence[StreamEvent]:
        payload = cls._payload_mapping(chunk)
        if not payload and Keys.Raw.MESSAGE in raw_event:
            payload = {Keys.Raw.MESSAGE: raw_event[Keys.Raw.MESSAGE]}
        return (
            cls._event(
                source=cls._source_for(raw_event, namespace, payload),
                event_type=event_type,
                trace_id=trace_id,
                parent_task_id=cls._parent_task_id_for(raw_event, payload),
                payload=payload,
                metadata=metadata,
            ),
        )

    @classmethod
    def _tool_call_events(
        cls,
        *,
        payload: JsonObject,
        raw_event: Mapping[str, object],
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
    ) -> Sequence[StreamEvent]:
        tool_call = ToolCallEvent(
            tool_name=cls._tool_name_for(payload),
            call_id=cls._call_id_for(payload),
            args=payload.get(Keys.Field.ARGS, {}),
            status=str(payload.get(Keys.Field.STATUS, Values.Status.PENDING)),
        )
        return (
            cls._event(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.TOOL_CALL,
                trace_id=trace_id,
                parent_task_id=cls._parent_task_id_for(raw_event, payload),
                payload=tool_call.model_dump(mode="json"),
                metadata=metadata,
            ),
        )

    @classmethod
    def _tool_result_events(
        cls,
        *,
        payload: JsonObject,
        raw_event: Mapping[str, object],
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
    ) -> Sequence[StreamEvent]:
        tool_result = ToolResultEvent(
            tool_name=cls._tool_name_for(payload),
            call_id=cls._call_id_for(payload),
            status=str(payload.get(Keys.Field.STATUS, Values.Status.COMPLETED)),
            output=cls._tool_output_for(payload),
        )
        completed_payload = {
            Keys.Field.API_EVENT_TYPE: Values.ApiEventType.TOOL_CALL_COMPLETED,
            Keys.Field.TOOL_NAME: tool_result.tool_name,
            Keys.Field.CALL_ID: tool_result.call_id,
            Keys.Field.STATUS: tool_result.status,
        }
        return (
            cls._event(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.TOOL_RESULT,
                trace_id=trace_id,
                parent_task_id=cls._parent_task_id_for(raw_event, payload),
                payload=tool_result.model_dump(mode="json"),
                metadata=metadata,
            ),
            cls._event(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.CUSTOM,
                trace_id=trace_id,
                parent_task_id=cls._parent_task_id_for(raw_event, payload),
                payload=completed_payload,
                metadata=metadata,
            ),
        )

    @classmethod
    def _subagent_lifecycle_events(
        cls,
        *,
        payload: JsonObject,
        raw_event: Mapping[str, object],
        trace_id: str,
        namespace: tuple[str, ...],
        metadata: JsonObject,
    ) -> Sequence[StreamEvent]:
        task_id = str(
            payload.get(Keys.Field.TASK_ID)
            or raw_event.get(Keys.Raw.TASK_ID)
            or Values.Status.UNKNOWN,
        )
        lifecycle = SubagentLifecycleEvent(
            task_id=task_id,
            subagent_name=str(
                payload.get(Keys.Field.SUBAGENT_NAME) or cls._subagent_name_for(namespace),
            ),
            status=str(payload.get(Keys.Field.STATUS, Values.Status.UNKNOWN)),
            summary=payload.get(Keys.Field.SUMMARY),  # type: ignore[arg-type]
        )
        return (
            cls._event(
                source=StreamEventSource.SUBAGENT,
                event_type=StreamEventType.LIFECYCLE,
                trace_id=trace_id,
                parent_task_id=cls._parent_task_id_for(raw_event, payload),
                payload=lifecycle.model_dump(mode="json", exclude_none=True),
                metadata=metadata,
            ),
        )

    @classmethod
    def _event(
        cls,
        *,
        source: StreamEventSource,
        event_type: StreamEventType,
        trace_id: str,
        payload: JsonObject,
        metadata: JsonObject,
        parent_task_id: str | None = None,
    ) -> StreamEvent:
        return StreamEvent(
            source=source,
            event_type=event_type,
            trace_id=trace_id,
            parent_task_id=parent_task_id,
            payload=payload,
            metadata=metadata,
        )

    @classmethod
    def _error_event(
        cls,
        *,
        trace_id: str,
        metadata: JsonObject,
        safe_message: str,
    ) -> StreamEvent:
        return StreamEvent(
            source=StreamEventSource.SYSTEM,
            event_type=StreamEventType.ERROR,
            trace_id=trace_id,
            payload={Keys.Raw.MESSAGE: safe_message},
            metadata=metadata,
        )

    @classmethod
    def _namespace_for(cls, raw_event: Mapping[str, object]) -> tuple[str, ...]:
        namespace = raw_event.get(Keys.Raw.NS, raw_event.get(Keys.Raw.NAMESPACE, ()))
        if namespace is None:
            return ()
        if isinstance(namespace, str):
            return (namespace,)
        if isinstance(namespace, Sequence):
            return tuple(str(part) for part in namespace)
        return ()

    @classmethod
    def _stream_mode_for(cls, raw_event: Mapping[str, object]) -> str:
        mode = raw_event.get(Keys.Raw.MODE)
        if isinstance(mode, str) and mode.strip():
            return mode.strip()
        return Defaults.UNKNOWN_MODE

    @classmethod
    def _chunk_for(cls, raw_event: Mapping[str, object]) -> object:
        if Keys.Raw.CHUNK in raw_event:
            return raw_event[Keys.Raw.CHUNK]
        if Keys.Raw.DATA in raw_event:
            return raw_event[Keys.Raw.DATA]
        return raw_event

    @classmethod
    def _metadata_for(
        cls,
        raw_event: Mapping[str, object],
        *,
        namespace: tuple[str, ...],
        mode: str,
    ) -> JsonObject:
        raw_metadata = raw_event.get(Keys.Raw.METADATA, {})
        metadata = cls._payload_mapping(raw_metadata)
        metadata[Keys.Raw.MODE] = mode
        if namespace:
            metadata[Keys.Raw.NAMESPACE] = list(namespace)
        return metadata

    @classmethod
    def _payload_mapping(cls, value: object) -> JsonObject:
        if isinstance(value, Mapping):
            return dict(value)  # type: ignore[arg-type]
        if isinstance(value, tuple) and len(value) == 2:
            return cls._payload_mapping(value[0])
        if value is None:
            return {}
        return {Keys.Raw.CONTENT: value}  # type: ignore[dict-item]

    @classmethod
    def _message_for(cls, chunk: object) -> object:
        if isinstance(chunk, tuple) and chunk:
            return chunk[0]
        if isinstance(chunk, Mapping) and Keys.Raw.MESSAGES in chunk:
            messages = chunk[Keys.Raw.MESSAGES]
            if isinstance(messages, Sequence) and not isinstance(messages, str) and messages:
                return messages[-1]
        return chunk

    @classmethod
    def _tool_calls_for(cls, message: object) -> tuple[object, ...]:
        if isinstance(message, Mapping):
            tool_calls = message.get(Keys.Raw.TOOL_CALLS, ())
        else:
            tool_calls = getattr(message, Keys.Raw.TOOL_CALLS, ())
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str):
            return tuple(tool_calls)
        return ()

    @classmethod
    def _is_tool_result_message(cls, message: object) -> bool:
        if isinstance(message, Mapping):
            raw_type = message.get(Keys.Raw.TYPE)
            return raw_type in {Values.EventType.TOOL_RESULT, Values.Source.TOOL}
        return bool(getattr(message, "tool_call_id", None))

    @classmethod
    def _has_explicit_event_type(cls, raw_event: Mapping[str, object], chunk: object) -> bool:
        if isinstance(raw_event.get(Keys.Raw.EVENT_TYPE), str):
            return True
        if isinstance(raw_event.get(Keys.Raw.EVENT), str):
            return True
        if isinstance(raw_event.get(Keys.Field.API_EVENT_TYPE), str):
            return True
        return isinstance(chunk, Mapping) and (
            isinstance(chunk.get(Keys.Raw.EVENT_TYPE), str)
            or isinstance(chunk.get(Keys.Raw.EVENT), str)
            or isinstance(chunk.get(Keys.Field.API_EVENT_TYPE), str)
        )

    @classmethod
    def _event_type_for(cls, raw_event: Mapping[str, object], chunk: object) -> str:
        if isinstance(raw_event.get(Keys.Field.API_EVENT_TYPE), str):
            return str(raw_event[Keys.Field.API_EVENT_TYPE])
        if isinstance(raw_event.get(Keys.Raw.EVENT_TYPE), str):
            return str(raw_event[Keys.Raw.EVENT_TYPE])
        if isinstance(raw_event.get(Keys.Raw.EVENT), str):
            return str(raw_event[Keys.Raw.EVENT])
        if isinstance(chunk, Mapping) and isinstance(chunk.get(Keys.Field.API_EVENT_TYPE), str):
            return str(chunk[Keys.Field.API_EVENT_TYPE])
        if isinstance(chunk, Mapping) and isinstance(chunk.get(Keys.Raw.EVENT_TYPE), str):
            return str(chunk[Keys.Raw.EVENT_TYPE])
        if isinstance(chunk, Mapping) and isinstance(chunk.get(Keys.Raw.EVENT), str):
            return str(chunk[Keys.Raw.EVENT])
        return Values.EventType.PROGRESS

    @classmethod
    def _source_for(
        cls,
        raw_event: Mapping[str, object],
        namespace: tuple[str, ...],
        payload: JsonObject,
    ) -> StreamEventSource:
        if cls._is_internal_summarization(raw_event, namespace):
            return StreamEventSource.SUMMARIZATION
        if cls._parent_task_id_for(raw_event, payload) is not None or cls._has_subagent_namespace(
            namespace
        ):
            return StreamEventSource.SUBAGENT
        return StreamEventSource.MAIN_AGENT

    @classmethod
    def _is_internal_summarization(
        cls,
        raw_event: Mapping[str, object],
        namespace: tuple[str, ...],
    ) -> bool:
        searchable = " ".join(
            (
                *(part.lower() for part in namespace),
                str(raw_event.get(Keys.Raw.EVENT_TYPE, "")).lower(),
                str(raw_event.get(Keys.Raw.EVENT, "")).lower(),
            )
        )
        return "summar" in searchable

    @classmethod
    def _has_subagent_namespace(cls, namespace: tuple[str, ...]) -> bool:
        return any("subagent" in part.lower() for part in namespace)

    @classmethod
    def _parent_task_id_for(
        cls,
        raw_event: Mapping[str, object],
        payload: JsonObject,
    ) -> str | None:
        value = payload.get(Keys.Field.PARENT_TASK_ID) or raw_event.get(Keys.Raw.PARENT_TASK_ID)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @classmethod
    def _subagent_name_for(cls, namespace: tuple[str, ...]) -> str:
        for part in reversed(namespace):
            if "subagent" in part.lower():
                candidate = part.split(":", maxsplit=1)[-1].strip().lower().replace("-", "_")
                if candidate and candidate != "subagent":
                    return candidate
        return "subagent"

    @classmethod
    def _tool_name_for(cls, payload: JsonObject) -> str:
        value = payload.get(Keys.Field.TOOL_NAME) or payload.get(Keys.Raw.NAME)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "unknown_tool"

    @classmethod
    def _call_id_for(cls, payload: JsonObject) -> str:
        value = payload.get(Keys.Field.CALL_ID) or payload.get(Keys.Raw.ID)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return TraceContext.event_id()

    @classmethod
    def _tool_output_for(cls, payload: JsonObject) -> JsonObject:
        excluded = {
            Keys.Field.API_EVENT_TYPE,
            Keys.Field.CALL_ID,
            Keys.Field.STATUS,
            Keys.Field.TOOL_NAME,
            Keys.Raw.EVENT,
            Keys.Raw.EVENT_TYPE,
            Keys.Raw.ID,
            Keys.Raw.NAME,
            Keys.Raw.TYPE,
        }
        output = {key: value for key, value in payload.items() if key not in excluded}
        return output or payload

    @classmethod
    def _api_event_types(cls) -> frozenset[str]:
        return frozenset(
            {
                Values.ApiEventType.MCP_AUTH_REQUIRED,
                Values.ApiEventType.REASONING_SUMMARY,
                Values.ApiEventType.REASONING_SUMMARY_DELTA,
                Values.ApiEventType.SUBAGENT_COMPLETED,
                Values.ApiEventType.SUBAGENT_PROGRESS,
                Values.ApiEventType.SUBAGENT_STARTED,
                Values.ApiEventType.TOOL_CALL_COMPLETED,
                Values.ApiEventType.TOOL_CALL_DELTA,
                Values.ApiEventType.TOOL_CALL_STARTED,
                Values.ApiEventType.TOOL_RESULT,
            }
        )

    @classmethod
    def _stream_event_type(cls, value: str) -> StreamEventType:
        try:
            return StreamEventType(value)
        except ValueError:
            return StreamEventType.PROGRESS
