"""Replayable runtime event schemas and projection helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationInfo,
    field_validator,
)

from agent_runtime.execution.contracts import (
    JsonObject,
    RuntimeContract,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    AgentRunStatus,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class _Fields:
    """Field name constants for presentation model validators and key references."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    URL = "url"
    BADGE = "badge"
    SUMMARY = "summary"
    GROUP_KEY = "group_key"
    PRIMARY_ENTITY = "primary_entity"
    ACTION_LABEL = "action_label"
    DEBUG_LABEL = "debug_label"
    ACTIVITY_KIND = "activity_kind"
    CITATION = "citation"
    CITATIONS = "citations"


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
    def event_type_for_stream_event(
        cls, stream_event: StreamEvent
    ) -> RuntimeApiEventType:
        """Return the most specific API event type for a normalized runtime event."""

        override = cls._event_type_override(stream_event.payload, stream_event.metadata)
        if override is not None:
            return override
        if stream_event.event_type is StreamEventType.TOOL_CALL:
            return RuntimeApiEventType.TOOL_CALL_STARTED
        if stream_event.event_type is StreamEventType.TOOL_RESULT:
            return RuntimeApiEventType.TOOL_RESULT
        if stream_event.event_type in {
            StreamEventType.LIFECYCLE,
            StreamEventType.SUBAGENT_UPDATE,
        }:
            return cls._subagent_event_type(stream_event.payload)
        if (
            stream_event.source is StreamEventSource.SUBAGENT
            and stream_event.event_type
            in {
                StreamEventType.CUSTOM,
                StreamEventType.PROGRESS,
            }
        ):
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
            return cls._reasoning_summary_payload(
                event_type=event_type, payload=payload
            )
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return cls._mcp_auth_required_payload(payload)
        if event_type is RuntimeApiEventType.APPROVAL_REQUESTED:
            return cls._approval_requested_payload(payload)
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return cls._approval_forwarded_payload(payload)
        if event_type is RuntimeApiEventType.SOURCE_INGESTED:
            return cls._source_ingested_payload(payload)
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
        subagent_id: str | None = None,
    ) -> dict[str, object]:
        """Return additive UI timeline fields for an event envelope or draft."""

        task_id = cls._text(payload.get(Keys.Field.TASK_ID)) or parent_task_id
        subagent_id = (
            cls._text(subagent_id)
            or cls._text(payload.get(Keys.Field.SUBAGENT_NAME))
            or cls._text(payload.get(Keys.Field.SUBAGENT_ID))
        )
        span_id = cls._span_id_for(
            event_type=event_type, task_id=task_id, payload=payload
        )
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
            _Fields.ACTIVITY_KIND: cls.activity_kind_for(
                event_type=event_type, source=source
            ),
            Keys.Field.VISIBILITY: cls._visibility_for(source=source, payload=payload),
            Keys.Field.REDACTION_STATE: cls._redaction_state_for(
                payload=payload,
                metadata=metadata,
            ),
        }

    @classmethod
    def presentation_metadata(
        cls, metadata: JsonObject
    ) -> RuntimeEventPresentation | None:
        raw = metadata.get("presentation")
        if not isinstance(raw, dict):
            return None
        try:
            return RuntimeEventPresentation.model_validate(raw)
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to validate presentation metadata", exc_info=True
            )
            return None

    @classmethod
    def activity_kind_for(
        cls,
        *,
        event_type: RuntimeApiEventType,
        source: StreamEventSource,
    ) -> RuntimeActivityKind:
        """Project transport event details into a stable client activity bucket."""

        if event_type is RuntimeApiEventType.HEARTBEAT:
            return RuntimeActivityKind.HEARTBEAT
        if event_type is RuntimeApiEventType.PRESENTATION_UPDATED:
            return RuntimeActivityKind.EVENT
        if event_type in {
            RuntimeApiEventType.MODEL_DELTA,
            RuntimeApiEventType.FINAL_RESPONSE,
        }:
            return RuntimeActivityKind.MESSAGE
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return RuntimeActivityKind.REASONING
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return RuntimeActivityKind.MCP_AUTH
        if event_type is RuntimeApiEventType.DRAFT_UPDATED:
            return RuntimeActivityKind.DRAFT
        if event_type in {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.APPROVAL_RESOLVED,
            RuntimeApiEventType.APPROVAL_FORWARDED,
        }:
            return RuntimeActivityKind.APPROVAL
        if source is StreamEventSource.TOOL or event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.SOURCE_INGESTED,
        }:
            return RuntimeActivityKind.TOOL
        if source is StreamEventSource.SUBAGENT or event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        }:
            return RuntimeActivityKind.SUBAGENT
        if event_type in {
            RuntimeApiEventType.RUN_QUEUED,
            RuntimeApiEventType.RUN_STARTED,
            RuntimeApiEventType.RUN_CANCELLING,
            RuntimeApiEventType.RUN_CANCELLED,
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.RUN_FAILED,
            RuntimeApiEventType.MODEL_CALL_STARTED,
        }:
            return RuntimeActivityKind.RUN
        return RuntimeActivityKind.EVENT

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
        if event_type is RuntimeApiEventType.TOOL_CALL_DELTA:
            if tool_name is None:
                return Messages.Event.TOOL_CALL
            return Messages.Event.tool_running_title(tool_name)
        if event_type is RuntimeApiEventType.TOOL_RESULT:
            if tool_name is None:
                return Messages.Event.TOOL_RESULT
            return Messages.Event.tool_result_title(tool_name)
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
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return Messages.Event.MCP_AUTH_REQUIRED
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return Messages.Event.APPROVAL_FORWARDED
        if event_type is RuntimeApiEventType.SOURCE_INGESTED:
            citation = payload.get(_Fields.CITATION)
            if isinstance(citation, dict):
                title = cls._text(citation.get(Keys.Field.TITLE))
                if title is not None:
                    return Messages.Event.source_cited_title(title)
            return Messages.Event.SOURCE_INGESTED
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
            RuntimeApiEventType.MODEL_CALL_STARTED,
        }:
            return Values.Status.STARTED
        if event_type in {
            RuntimeApiEventType.PROGRESS,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            RuntimeApiEventType.MODEL_DELTA,
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.TOOL_CALL_DELTA,
        }:
            return Values.Status.RUNNING
        if event_type in {
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            RuntimeApiEventType.FINAL_RESPONSE,
            RuntimeApiEventType.SOURCE_INGESTED,
        }:
            return Values.Status.COMPLETED
        # PR 1.4 — forwarded approvals project as a non-terminal "waiting"
        # status so the FE renders the card as "Waiting on @marcus" (not
        # "Done"). The actual terminal state is the child's APPROVAL_RESOLVED.
        if event_type is RuntimeApiEventType.APPROVAL_FORWARDED:
            return Values.Status.WAITING
        if event_type in {RuntimeApiEventType.RUN_FAILED, RuntimeApiEventType.ERROR}:
            return Values.Status.FAILED
        if event_type is RuntimeApiEventType.RUN_CANCELLED:
            return Values.Status.CANCELLED
        return None

    @classmethod
    def _mcp_auth_required_payload(cls, payload: JsonObject) -> JsonObject:
        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            "action_id",
            Keys.Field.APPROVAL_KIND,
            Keys.Field.SERVER_ID,
            Keys.Field.SERVER_NAME,
            "display_name",
            Keys.Field.AUTH_URL,
            Keys.Field.EXPIRES_AT,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        return safe_payload

    @classmethod
    def _source_ingested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``source_ingested`` payloads through a strict allow-list.

        The CitationLedger is the only intended emitter and always supplies
        the full ``CitationSourceRef`` shape, but we whitelist defensively
        in case a future caller (e.g. a provider adapter) over-shares.
        """

        citation = payload.get(_Fields.CITATION)
        if not isinstance(citation, dict):
            return {}
        safe_citation: JsonObject = {}
        for text_key in (
            "citation_id",
            "source_connector",
            "source_doc_id",
            "source_url",
            "title",
            "snippet",
            "freshness_at",
            "source_tool_call_id",
        ):
            value = citation.get(text_key)
            if isinstance(value, str) and value.strip():
                safe_citation[text_key] = value
            elif value is None and text_key in {
                "source_url",
                "snippet",
                "freshness_at",
                "source_tool_call_id",
            }:
                safe_citation[text_key] = None
        ordinal = citation.get("ordinal")
        if isinstance(ordinal, int) and ordinal > 0:
            safe_citation["ordinal"] = ordinal
        return {_Fields.CITATION: safe_citation}

    @classmethod
    def _approval_requested_payload(cls, payload: JsonObject) -> JsonObject:
        approval_kind = cls._text(payload.get(Keys.Field.APPROVAL_KIND))
        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            return cls._ask_a_question_requested_payload(payload)
        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Field.SERVER_ID,
            Keys.Field.SERVER_NAME,
            "display_name",
            Keys.Field.TOOL_NAME,
            "risk_level",
            Keys.Payload.MESSAGE,
            Keys.Field.REASON,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        read_only = payload.get("read_only")
        if isinstance(read_only, bool):
            safe_payload["read_only"] = read_only
        arguments = payload.get("arguments")
        if isinstance(arguments, dict):
            safe_payload["arguments"] = arguments
        grant_options = payload.get("grant_options")
        if isinstance(grant_options, list | tuple):
            safe_payload["grant_options"] = [
                option for option in grant_options if isinstance(option, str)
            ]
        return safe_payload

    @classmethod
    def _approval_forwarded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``approval_forwarded`` payloads through a strict allow-list.

        PR 1.4 — emitted in the same transaction as ``APPROVAL_RESOLVED``
        (status=forwarded) for the parent and ``APPROVAL_REQUESTED`` for the
        child. The reducer keys on ``chain_parent_approval_id`` to transform
        the original in-thread card into a "Waiting on @marcus" pill.
        """

        safe_payload: JsonObject = {}
        for text_key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.CHAIN_PARENT_APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Field.FORWARDED_BY_USER_ID,
            Keys.Field.FORWARDED_TO_USER_ID,
            Keys.Field.FORWARDED_AT,
            Keys.Field.ACTION_SUMMARY,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _ask_a_question_requested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ask_a_question approval payloads, preserving question text and
        structured options. The narrow approval allow-list strips these, so
        ask_a_question gets its own projection."""

        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
            "header",
            "question",
            "hint",
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        options = payload.get("options")
        if isinstance(options, list | tuple):
            safe_payload["options"] = cls._safe_question_options(options)
        for flag_key in ("multi_select", "allow_free_text"):
            flag = payload.get(flag_key)
            if isinstance(flag, bool):
                safe_payload[flag_key] = flag
        return safe_payload

    @classmethod
    def _safe_question_options(cls, options: list | tuple) -> list[JsonObject]:
        """Coerce structured option dicts (and bare strings) into a sanitized list.

        Bare strings are upgraded to ``{label: ...}`` for backwards compatibility
        with callers that haven't moved to the structured shape yet."""

        sanitized: list[JsonObject] = []
        for option in options:
            if isinstance(option, str):
                label = cls._text(option)
                if label is not None:
                    sanitized.append({"label": label})
                continue
            if not isinstance(option, dict):
                continue
            label = cls._text(option.get("label"))
            if label is None:
                continue
            entry: JsonObject = {"label": label}
            description = cls._text(option.get("description"))
            if description is not None:
                entry["description"] = description
            recommended = option.get("recommended")
            if isinstance(recommended, bool):
                entry["recommended"] = recommended
            sanitized.append(entry)
        return sanitized

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


class AssistantUsageMetrics(RuntimeContract):
    """Exact provider token usage counts without secret-like field names."""

    input: NonNegativeInt | None = None
    output: NonNegativeInt | None = None
    total: NonNegativeInt | None = None
    cached_input: NonNegativeInt | None = None
    output_per_second: float | None = Field(default=None, ge=0)


class AssistantSubagentUsageRollup(RuntimeContract):
    """Aggregate token usage for one subagent task (B2).

    Sum of every ``MODEL_CALL_COMPLETED`` row attributed to a single
    ``task_id`` between SUBAGENT_STARTED and SUBAGENT_COMPLETED. ``call_count``
    is the number of distinct LLM calls. Optional payload on
    ``SUBAGENT_COMPLETED`` events; absent when the worker can't correlate
    calls to the task (e.g. provider didn't return a stable message id).
    """

    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    call_count: NonNegativeInt = 0


class AssistantPerformanceMetrics(RuntimeContract):
    """Assistant response timing and exact provider usage metadata."""

    started_at: datetime
    completed_at: datetime
    duration_ms: NonNegativeInt
    chunk_count: NonNegativeInt = 0
    first_chunk_at: datetime | None = None
    first_chunk_ms: NonNegativeInt | None = None
    usage: AssistantUsageMetrics | None = None


class RuntimeEventPresentationPreviewRow(RuntimeContract):
    """Small user-facing row rendered in an activity card result preview."""

    title: str = Field(min_length=1, max_length=120)
    subtitle: str | None = Field(default=None, max_length=240)
    url: str | None = Field(default=None, max_length=500)
    badge: str | None = Field(default=None, max_length=40)

    @field_validator(
        _Fields.TITLE, _Fields.SUBTITLE, _Fields.URL, _Fields.BADGE, mode="before"
    )
    @classmethod
    def _plain_text(cls, value: object, info: ValidationInfo) -> str | None:
        max_lengths = {
            _Fields.TITLE: 120,
            _Fields.SUBTITLE: 240,
            _Fields.URL: 500,
            _Fields.BADGE: 40,
        }
        return RuntimeEventPresentation.safe_text(
            value,
            max_length=max_lengths[info.field_name],
        )


class RuntimeEventPresentation(RuntimeContract):
    """Validated LLM-generated card presentation metadata."""

    title: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=240)
    status_label: Literal["Running", "Waiting for permission", "Done", "Failed"]
    kind: Literal["progress", "result", "approval", "auth", "error"]
    group_key: str | None = Field(default=None, max_length=160)
    primary_entity: str | None = Field(default=None, max_length=80)
    action_label: str | None = Field(default=None, max_length=60)
    result_preview: tuple[RuntimeEventPresentationPreviewRow, ...] = ()
    debug_label: str | None = Field(default="Tool details", max_length=40)

    @field_validator(
        _Fields.TITLE,
        _Fields.SUMMARY,
        _Fields.GROUP_KEY,
        _Fields.PRIMARY_ENTITY,
        _Fields.ACTION_LABEL,
        _Fields.DEBUG_LABEL,
        mode="before",
    )
    @classmethod
    def _safe_optional_text(cls, value: object, info: ValidationInfo) -> str | None:
        max_lengths = {
            _Fields.TITLE: 80,
            _Fields.SUMMARY: 240,
            _Fields.GROUP_KEY: 160,
            _Fields.PRIMARY_ENTITY: 80,
            _Fields.ACTION_LABEL: 60,
            _Fields.DEBUG_LABEL: 40,
        }
        return cls.safe_text(value, max_length=max_lengths[info.field_name])

    @staticmethod
    def safe_text(value: object, *, max_length: int) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.replace("<", "").replace(">", "").split())
        if not text:
            return None
        return text[:max_length]


class _RuntimeEventBase(RuntimeContract):
    """Shared fields and validators for event envelopes and drafts."""

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
    activity_kind: RuntimeActivityKind | None = None
    visibility: RuntimeEventVisibility = RuntimeEventVisibility.USER
    redaction_state: RuntimeEventRedactionState = RuntimeEventRedactionState.REDACTED
    presentation: RuntimeEventPresentation | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator(
        Keys.Field.RUN_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

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
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(
        Keys.Field.DISPLAY_TITLE,
        Keys.Field.SUMMARY,
        Keys.Field.STATUS,
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(
        cls, value: object, info: ValidationInfo
    ) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, info.field_name)

    @field_validator(Keys.Field.PAYLOAD, Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return ObservabilityRedactor.redact_json_object(value)

    @classmethod
    def _build_from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        stream_event: StreamEvent,
    ) -> dict[str, object]:
        """Return the common constructor kwargs from a normalized stream event."""

        event_type = RuntimeEventPresentationProjector.event_type_for_stream_event(
            stream_event
        )
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
        return dict(
            run_id=run_id,
            conversation_id=conversation_id,
            source=stream_event.source,
            event_type=event_type,
            trace_id=stream_event.trace_id,
            parent_task_id=stream_event.parent_task_id,
            payload=payload,
            metadata=stream_event.metadata,
            presentation=RuntimeEventPresentationProjector.presentation_metadata(
                stream_event.metadata
            ),
            **presentation,
        )


class RuntimeEventEnvelope(_RuntimeEventBase):
    """Ordered transport event envelope shared by replay and streaming."""

    event_protocol_version: PositiveInt = Values.EVENT_PROTOCOL_VERSION
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    sequence_no: PositiveInt
    activity_kind: RuntimeActivityKind
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(Keys.Field.EVENT_ID, mode="before")
    @classmethod
    def _normalize_event_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

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

        kwargs = cls._build_from_stream_event(
            run_id=run_id,
            conversation_id=conversation_id,
            stream_event=stream_event,
        )
        return cls(
            event_id=stream_event.event_id,
            sequence_no=sequence_no,
            created_at=stream_event.timestamp,
            **kwargs,
        )


class RuntimeEventReplayResponse(RuntimeContract):
    """Replay response for persisted ordered events."""

    run_id: str
    events: tuple[RuntimeEventEnvelope, ...]
    latest_sequence_no: NonNegativeInt
    run_status: AgentRunStatus
    has_more: bool = False


class RuntimeEventDraft(_RuntimeEventBase):
    """Event data before the event store assigns per-run sequence number."""

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        stream_event: StreamEvent,
    ) -> "RuntimeEventDraft":
        """Create an appendable API event draft from a normalized runtime event."""

        kwargs = cls._build_from_stream_event(
            run_id=run_id,
            conversation_id=conversation_id,
            stream_event=stream_event,
        )
        return cls(**kwargs)
