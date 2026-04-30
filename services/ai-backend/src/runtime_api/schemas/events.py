"""Replayable runtime event schemas and projection helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, ValidationInfo, field_validator

from agent_runtime.agent.contracts import JsonObject, RuntimeContract, StreamEvent, StreamEventSource, StreamEventType
from agent_runtime.api.constants import Keys, Messages, Values
from runtime_api.schemas.common import (
    AgentRunStatus,
    RuntimeApiEventType,
    RuntimeApiValueNormalizer,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class RuntimeEventPresentationProjector:
    """Project normalized runtime events into stable UI timeline semantics."""

    SUBAGENT_STARTED_STATUSES = frozenset(
        {
            Values.Status.QUEUED,
            Values.Status.STARTED,
        }
    )
    SUBAGENT_COMPLETED_STATUSES = frozenset(
        {
            Values.Status.CANCELLED,
            Values.Status.COMPLETED,
            Values.Status.FAILED,
            "succeeded",
            "success",
        }
    )

    @classmethod
    def event_type_for_stream_event(cls, stream_event: StreamEvent) -> RuntimeApiEventType:
        """Return the most specific API event type for a normalized runtime event."""

        override = cls._event_type_override(stream_event.payload, stream_event.metadata)
        if override is not None:
            return override
        if stream_event.event_type is StreamEventType.TOOL_CALL:
            return RuntimeApiEventType.TOOL_CALL_STARTED
        if stream_event.event_type is StreamEventType.TOOL_RESULT:
            return RuntimeApiEventType.TOOL_CALL_COMPLETED
        if stream_event.event_type in {
            StreamEventType.LIFECYCLE,
            StreamEventType.SUBAGENT_UPDATE,
        }:
            return cls._subagent_event_type(stream_event.payload)
        if stream_event.source is StreamEventSource.SUBAGENT and stream_event.event_type in {
            StreamEventType.CUSTOM,
            StreamEventType.PROGRESS,
        }:
            return RuntimeApiEventType.SUBAGENT_PROGRESS
        return RuntimeApiEventType.from_stream_event_type(stream_event.event_type)

    @classmethod
    def payload_for_event(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        """Return the client-visible payload for an API event type."""

        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return cls._reasoning_summary_payload(event_type=event_type, payload=payload)
        return payload

    @classmethod
    def presentation_fields(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
        parent_task_id: str | None,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> dict[str, object]:
        """Return additive UI timeline fields for an event envelope or draft."""

        task_id = cls._text(payload.get(Keys.Field.TASK_ID)) or parent_task_id
        subagent_id = cls._text(payload.get(Keys.Field.SUBAGENT_NAME)) or cls._text(
            payload.get(Keys.Field.SUBAGENT_ID)
        )
        span_id = cls._span_id_for(event_type=event_type, task_id=task_id, payload=payload)
        return {
            Keys.Field.PARENT_EVENT_ID: cls._text(
                payload.get(Keys.Field.PARENT_EVENT_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_EVENT_ID)),
            Keys.Field.SPAN_ID: span_id,
            Keys.Field.PARENT_SPAN_ID: cls._text(
                payload.get(Keys.Field.PARENT_SPAN_ID),
            )
            or cls._text(metadata.get(Keys.Field.PARENT_SPAN_ID))
            or parent_task_id,
            Keys.Field.TASK_ID: task_id,
            Keys.Field.SUBAGENT_ID: subagent_id,
            Keys.Field.DISPLAY_TITLE: cls._display_title_for(
                event_type=event_type,
                payload=payload,
            ),
            Keys.Field.SUMMARY: cls._summary_for(payload=payload, metadata=metadata),
            Keys.Field.STATUS: cls._status_for(event_type=event_type, payload=payload),
            Keys.Field.VISIBILITY: cls._visibility_for(source=source, payload=payload),
            Keys.Field.REDACTION_STATE: cls._redaction_state_for(
                payload=payload,
                metadata=metadata,
            ),
        }

    @classmethod
    def _event_type_override(
        cls,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeApiEventType | None:
        value = cls._text(payload.get(Keys.Field.API_EVENT_TYPE)) or cls._text(
            metadata.get(Keys.Field.API_EVENT_TYPE)
        )
        if value is None:
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def _subagent_event_type(cls, payload: JsonObject) -> RuntimeApiEventType:
        status = cls._status_text(payload)
        if status in cls.SUBAGENT_STARTED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_STARTED
        if status in cls.SUBAGENT_COMPLETED_STATUSES:
            return RuntimeApiEventType.SUBAGENT_COMPLETED
        return RuntimeApiEventType.SUBAGENT_PROGRESS

    @classmethod
    def _reasoning_summary_payload(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        summary = cls._text(payload.get(Keys.Field.SUMMARY)) or cls._text(
            payload.get(Keys.Payload.MESSAGE)
        )
        safe_payload: JsonObject = {}
        if summary is not None:
            safe_payload[Keys.Field.SUMMARY] = summary
        if event_type is RuntimeApiEventType.REASONING_SUMMARY_DELTA:
            delta = cls._text(payload.get(Keys.Payload.DELTA))
            if delta is not None:
                safe_payload[Keys.Payload.DELTA] = delta
        return safe_payload

    @classmethod
    def _span_id_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        task_id: str | None,
        payload: JsonObject,
    ) -> str | None:
        configured_span_id = cls._text(payload.get(Keys.Field.SPAN_ID))
        if configured_span_id is not None:
            return configured_span_id
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return cls._text(payload.get(Keys.Field.CALL_ID))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        }:
            return task_id
        return None

    @classmethod
    def _display_title_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._text(payload.get(Keys.Field.DISPLAY_TITLE)) or cls._text(
            payload.get(Keys.Payload.DISPLAY_TITLE)
        )
        if configured is not None:
            return configured
        tool_name = cls._text(payload.get(Keys.Field.TOOL_NAME))
        if event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_started_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_CALL_COMPLETED:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_completed_title(tool_name)
        subagent_name = cls._text(payload.get(Keys.Field.SUBAGENT_NAME))
        if event_type in {
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.SUBAGENT_UPDATE,
        }:
            if subagent_name is None:
                return Messages.Event.SUBAGENT
            return Messages.Event.subagent_title(subagent_name)
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return Messages.Event.REASONING
        if event_type is RuntimeApiEventType.MODEL_DELTA:
            return Messages.Event.MODEL_DELTA
        if event_type is RuntimeApiEventType.FINAL_RESPONSE:
            return Messages.Event.FINAL_RESPONSE
        return None

    @classmethod
    def _summary_for(cls, *, payload: JsonObject, metadata: JsonObject) -> str | None:
        return (
            cls._text(payload.get(Keys.Field.SUMMARY))
            or cls._text(payload.get(Keys.Payload.MESSAGE))
            or cls._text(metadata.get(Keys.Field.SUMMARY))
        )

    @classmethod
    def _status_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> str | None:
        configured = cls._status_text(payload)
        if configured is not None:
            return configured
        if event_type in {RuntimeApiEventType.RUN_QUEUED}:
            return Values.Status.QUEUED
        if event_type in {
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.SUBAGENT_STARTED,
        }:
            return Values.Status.STARTED
        if event_type in {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.MODEL_DELTA,
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
        }:
            return Values.Status.RUNNING
        if event_type in {
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.FINAL_RESPONSE,
        }:
            return Values.Status.COMPLETED
        if event_type in {RuntimeApiEventType.RUN_FAILED, RuntimeApiEventType.ERROR}:
            return Values.Status.FAILED
        if event_type is RuntimeApiEventType.RUN_CANCELLED:
            return Values.Status.CANCELLED
        return None

    @classmethod
    def _visibility_for(
        cls,
        *,
        source: StreamEventSource,
        payload: JsonObject,
    ) -> RuntimeEventVisibility:
        configured = cls._text(payload.get(Keys.Field.VISIBILITY))
        if configured is not None:
            try:
                return RuntimeEventVisibility(configured)
            except ValueError:
                return RuntimeEventVisibility.USER
        if source is StreamEventSource.SUMMARIZATION:
            return RuntimeEventVisibility.INTERNAL
        return RuntimeEventVisibility.USER

    @classmethod
    def _redaction_state_for(
        cls,
        *,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> RuntimeEventRedactionState:
        configured = cls._text(payload.get(Keys.Field.REDACTION_STATE)) or cls._text(
            metadata.get(Keys.Field.REDACTION_STATE)
        )
        if configured is not None:
            try:
                return RuntimeEventRedactionState(configured)
            except ValueError:
                return RuntimeEventRedactionState.REDACTED
        if cls._contains_payload_ref(payload):
            return RuntimeEventRedactionState.OFFLOADED
        if "[truncated]" in str(payload):
            return RuntimeEventRedactionState.TRUNCATED
        return RuntimeEventRedactionState.REDACTED

    @classmethod
    def _contains_payload_ref(cls, payload: JsonObject) -> bool:
        return any("ref" in key.lower() for key in payload)

    @classmethod
    def _status_text(cls, payload: JsonObject) -> str | None:
        value = cls._text(payload.get(Keys.Field.STATUS))
        if value is None:
            return None
        return value.lower()

    @classmethod
    def _text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized



class RuntimeEventEnvelope(RuntimeContract):
    """Ordered transport event envelope shared by replay and streaming."""

    event_protocol_version: PositiveInt = Values.EVENT_PROTOCOL_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    sequence_no: PositiveInt
    source: StreamEventSource
    event_type: RuntimeApiEventType
    trace_id: str
    parent_event_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    parent_task_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    display_title: str | None = None
    summary: str | None = None
    status: str | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        Keys.Field.EVENT_ID,
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.PARENT_EVENT_ID,
        Keys.Field.SPAN_ID,
        Keys.Field.PARENT_SPAN_ID,
        Keys.Field.PARENT_TASK_ID,
        Keys.Field.TASK_ID,
        Keys.Field.SUBAGENT_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, info.field_name)

    @field_validator(Keys.Field.PAYLOAD, Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        sequence_no: int,
        stream_event: StreamEvent,
    ) -> "RuntimeEventEnvelope":
        """Wrap an existing normalized runtime event in the API envelope."""

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(stream_event)
        payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=stream_event.payload,
        )
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=stream_event.source,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
        )
        return cls(
            event_id=stream_event.event_id,
            run_id=run_id,
            conversation_id=conversation_id,
            sequence_no=sequence_no,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            created_at=stream_event.timestamp,
            **presentation,
        )



class RuntimeEventReplayResponse(RuntimeContract):
    """Replay response for persisted ordered events."""

    run_id: str
    events: tuple[RuntimeEventEnvelope, ...]
    latest_sequence_no: NonNegativeInt
    run_status: AgentRunStatus
    has_more: bool = False



class RuntimeEventDraft(RuntimeContract):
    """Event data before the event store assigns per-run sequence number."""

    run_id: str
    conversation_id: str
    source: StreamEventSource
    event_type: RuntimeApiEventType
    trace_id: str
    parent_event_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    parent_task_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    display_title: str | None = None
    summary: str | None = None
    status: str | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator(Keys.Field.RUN_ID, Keys.Field.CONVERSATION_ID, Keys.Field.TRACE_ID)
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.PARENT_EVENT_ID,
        Keys.Field.SPAN_ID,
        Keys.Field.PARENT_SPAN_ID,
        Keys.Field.PARENT_TASK_ID,
        Keys.Field.TASK_ID,
        Keys.Field.SUBAGENT_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(value, info.field_name)

    @field_validator(Keys.Field.PAYLOAD, Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return RuntimeApiValueNormalizer.redact_json_object(value)

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        stream_event: StreamEvent,
    ) -> "RuntimeEventDraft":
        """Create an appendable API event draft from a normalized runtime event."""

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(stream_event)
        payload = RuntimeEventPresentationProjector.payload_for_event(
            event_type=event_type,
            payload=stream_event.payload,
        )
        presentation = RuntimeEventPresentationProjector.presentation_fields(
            event_type=event_type,
            source=stream_event.source,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
        )
        return cls(
            run_id=run_id,
            conversation_id=conversation_id,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            **presentation,
        )
