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

# Lazy import: ``McpDispatcherUnwrap`` lives under ``agent_runtime.capabilities.mcp``,
# whose package ``__init__`` eagerly imports the MCP middleware. That middleware
# imports back through ``runtime_api.schemas`` (via the citation tooling), so a
# top-level import here triggers a circular load during ``agent_runtime`` init.
# ``_display_title_for`` resolves the helper at call time when both modules are
# fully initialised.
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.surfaces_v2.constants import Keys as _LedgerKeys
from agent_runtime.surfaces_v2.constants import Values as _LedgerValues
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    AgentRunStatus,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)

# PRD-D3 — hard cap for row-set text the projector lets through (hold reasons,
# row titles, change field names / string values). Rendered UI text is treated
# as plain, length-capped strings; the domain validator caps the source too.
_ROWSET_TEXT_MAX = 200


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
    # PR 1.1-rev2 — model-declared citation pointer payload key.
    LINK = "link"
    CITED_ORDINALS = "cited_ordinals"
    CONVERSATION_ORDINAL = "conversation_ordinal"
    MESSAGE_ID = "message_id"
    PROSE_OFFSET = "prose_offset"
    PROSE_LENGTH = "prose_length"
    SOURCE_TOOL_CALL_ID = "source_tool_call_id"
    # Generative-UI (PRD-01) — surface_spec_generated payload keys + title.
    SURFACE_URI = "surface_uri"
    ARCHETYPE = "archetype"
    SPEC = "spec"
    SPEC_VERSION = "spec_version"
    GENERATOR_MODEL = "generator_model"
    SKILL_VERSION = "skill_version"
    SURFACE_PREPARED_TITLE = "Prepared a view"
    # Generative Surfaces v2 (PRD-D2, FR-C3) — write.applied display microcopy.
    # ``applied`` is the FR-C3 requirement string (verbatim); Phase-2 polishes
    # the ``failed`` wording. Single-use titles inlined here (matches
    # ``SURFACE_PREPARED_TITLE``).
    WRITE_APPLIED_TITLE = "Sent — exactly the revision you approved."
    WRITE_FAILED_TITLE = "Apply refused — nothing was sent."
    # Generative Surfaces v2 (PRD-E1, FR-E2) — the receipt seal's timeline title.
    RECEIPT_EMITTED_TITLE = "Run receipt"
    # Generative Surfaces v2 (PRD-A2, SDR §5) — usage.recorded payload keys.
    USAGE_V = "v"
    USAGE_PURPOSE = "purpose"
    USAGE_MODEL = "model"
    USAGE_TOKENS_IN = "tokens_in"
    USAGE_TOKENS_OUT = "tokens_out"
    USAGE_SURFACE_ID = "surface_id"


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
        if event_type is RuntimeApiEventType.SOURCES_INGESTED:
            return cls._sources_ingested_payload(payload)
        if event_type is RuntimeApiEventType.CITATION_MADE:
            return cls._citation_made_payload(payload)
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            return cls._surface_spec_generated_payload(payload)
        if event_type is RuntimeApiEventType.USAGE_RECORDED:
            return cls._usage_recorded_payload(payload)
        if event_type is RuntimeApiEventType.ACTION_CLASSIFIED:
            return cls._action_classified_payload(payload)
        if event_type is RuntimeApiEventType.READ_EXECUTED:
            return cls._read_executed_payload(payload)
        if event_type is RuntimeApiEventType.SURFACE_CREATED:
            return cls._surface_created_payload(payload)
        if event_type is RuntimeApiEventType.VIEW_DERIVED:
            return cls._view_derived_payload(payload)
        if event_type is RuntimeApiEventType.VIEW_PREFERENCE:
            return cls._view_preference_payload(payload)
        if event_type is RuntimeApiEventType.GATE_OPENED:
            return cls._gate_opened_payload(payload)
        if event_type is RuntimeApiEventType.GATE_RESOLVED:
            return cls._gate_resolved_payload(payload)
        if event_type is RuntimeApiEventType.WRITE_STAGED:
            return cls._write_staged_payload(payload)
        if event_type is RuntimeApiEventType.REVISION_ADDED:
            return cls._revision_added_payload(payload)
        if event_type is RuntimeApiEventType.DECISION_RECORDED:
            return cls._decision_recorded_payload(payload)
        if event_type is RuntimeApiEventType.WRITE_APPLIED:
            return cls._write_applied_payload(payload)
        if event_type is RuntimeApiEventType.RECEIPT_EMITTED:
            return cls._receipt_emitted_payload(payload)
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
        """Extract and validate the ``presentation`` sub-object from event metadata, or return None."""
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
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            # Generative-UI (PRD-01) — an out-of-band "prepared a view" note.
            # Explicit so a TOOL-sourced emit can't reroute it into the tool
            # bucket; the FE consumes it as a surface-state merge, not a card.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.USAGE_RECORDED:
            # Generative Surfaces v2 (PRD-A2) — a metering ledger event, not a
            # timeline card. Explicit (matches the default) so a MODEL-sourced
            # emit can't be rerouted; A3's UsageTotals fold consumes it.
            return RuntimeActivityKind.EVENT
        if event_type in {
            RuntimeApiEventType.ACTION_CLASSIFIED,
            RuntimeApiEventType.READ_EXECUTED,
            RuntimeApiEventType.SURFACE_CREATED,
            RuntimeApiEventType.VIEW_DERIVED,
            RuntimeApiEventType.VIEW_PREFERENCE,
            RuntimeApiEventType.GATE_OPENED,
            RuntimeApiEventType.GATE_RESOLVED,
            RuntimeApiEventType.WRITE_STAGED,
            RuntimeApiEventType.REVISION_ADDED,
            RuntimeApiEventType.DECISION_RECORDED,
            RuntimeApiEventType.WRITE_APPLIED,
            RuntimeApiEventType.RECEIPT_EMITTED,
        }:
            # Generative Surfaces v2 (PRD-A3/B3/C2/D1/D2/E1) — ledger events the SurfaceStore
            # + client ledger fold consume as surface/gate-state merges, never
            # timeline cards. Explicit so a TOOL/SYSTEM-sourced emit can't reroute
            # into the tool bucket. The gate pair rides beside the
            # ``mcp_auth_required`` approval (which keeps its own MCP_AUTH kind);
            # the canvas gate card + posture chip read these, not the legacy
            # approval event, when the v2 flag is on.
            return RuntimeActivityKind.EVENT
        if event_type is RuntimeApiEventType.COMPRESSION_NOTE:
            # PR A1 — context-compression note. Renders as an inline
            # dim line ("Atlas summarised 3 older messages…") rather
            # than a card; FE consumes via `<NoteCard>`.
            return RuntimeActivityKind.NOTE
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
            RuntimeApiEventType.SOURCES_INGESTED,
            RuntimeApiEventType.CITATION_MADE,
        }:
            return RuntimeActivityKind.TOOL
        if source is StreamEventSource.SUBAGENT or event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
            # PR A2 — fleet group bookends share the SUBAGENT bucket so
            # the FE can render fleets and singletons through the same
            # reducer; per-event `parent_fleet_id` discriminates.
            RuntimeApiEventType.SUBAGENT_FLEET_STARTED,
            RuntimeApiEventType.SUBAGENT_FLEET_FINISHED,
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
        # Use the dispatcher-unwrap helper so ``call_mcp_tool`` events render
        # their inner tool name (e.g. ``"list_issues"``) instead of the raw
        # dispatcher name. For non-dispatcher events the helper just returns
        # ``payload.tool_name`` verbatim. Imported lazily (see module docstring
        # at top) to avoid a circular import during ``agent_runtime`` init.
        from agent_runtime.capabilities.mcp.dispatcher import McpDispatcherUnwrap

        tool_name = McpDispatcherUnwrap.effective_tool_name(payload)
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
        if event_type is RuntimeApiEventType.SOURCES_INGESTED:
            citations = payload.get("citations")
            if isinstance(citations, list) and citations:
                return Messages.Event.sources_cited_title(len(citations))
            return Messages.Event.SOURCES_INGESTED
        if event_type is RuntimeApiEventType.CITATION_MADE:
            link = payload.get(_Fields.LINK)
            if isinstance(link, dict):
                ordinal = link.get(_Fields.CONVERSATION_ORDINAL)
                if isinstance(ordinal, int) and ordinal > 0:
                    return Messages.Event.citation_made_title(ordinal)
            return Messages.Event.CITATION_MADE
        if event_type is RuntimeApiEventType.SURFACE_SPEC_GENERATED:
            # Generative-UI (PRD-01). The user-facing message class lives in
            # ``agent_runtime.api.constants`` (out of this PR's scope); the
            # single-use title is inlined here until PRD-02 wires the emitter.
            return _Fields.SURFACE_PREPARED_TITLE
        if event_type is RuntimeApiEventType.WRITE_APPLIED:
            # Generative Surfaces v2 (PRD-D2, FR-C3). ``applied`` shows the
            # requirement microcopy verbatim; ``failed`` shows the refusal line.
            result = cls._text(payload.get(_LedgerKeys.Field.RESULT))
            if result == _LedgerValues.RESULT_FAILED:
                return _Fields.WRITE_FAILED_TITLE
            return _Fields.WRITE_APPLIED_TITLE
        if event_type is RuntimeApiEventType.RECEIPT_EMITTED:
            # Generative Surfaces v2 (PRD-E1, FR-E2) — the accountability seal.
            return _Fields.RECEIPT_EMITTED_TITLE
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
            RuntimeApiEventType.SOURCES_INGESTED,
            RuntimeApiEventType.CITATION_MADE,
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
        # PR 3.3 — ``DISCOVERY_REASON`` and ``EXPECTED_VALUE`` are optional
        # additions that flip the FE card variant from blocking auth-gate
        # to non-blocking Connect/Skip suggestion. Both pass through the
        # same allow-list — emitters never set them on a blocking call.
        for key in (
            Keys.Field.APPROVAL_ID,
            "action_id",
            Keys.Field.APPROVAL_KIND,
            Keys.Field.BATCH_ID,
            Keys.Field.SERVER_ID,
            Keys.Field.SERVER_NAME,
            "display_name",
            Keys.Field.AUTH_URL,
            Keys.Field.EXPIRES_AT,
            Keys.Payload.MESSAGE,
            Keys.Field.STATUS,
            Keys.Field.SOURCE_TOOL_CALL_ID,
            Keys.Field.DISCOVERY_REASON,
            Keys.Field.EXPECTED_VALUE,
            # PR 4.4.7 Phase 2 (Slice C) — present iff the suggestion
            # came from the catalog (uninstalled connector). The FE
            # branches Connect on this so it routes to the install
            # flow rather than starting OAuth against a server row
            # that doesn't exist yet.
            "catalog_slug",
        ):
            value = cls._text(payload.get(key))
            if value is not None:
                safe_payload[key] = value
        # PR 4.4.7 follow-up — boolean flag (string-only ``_text``
        # would coerce False to None). Pass through bool values
        # verbatim; absent/non-bool keys are dropped.
        requires_pre = payload.get("requires_pre_registered_client")
        if isinstance(requires_pre, bool):
            safe_payload["requires_pre_registered_client"] = requires_pre
        # PR #43 — preserve typed batch_index through projection so the FE
        # receives it alongside batch_id on every approval-style event.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
        return safe_payload

    @classmethod
    def _source_ingested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``source_ingested`` payloads through a strict allow-list.

        The CitationLedger is the only intended emitter and always supplies
        the full ``CitationSourceRef`` shape, but we whitelist defensively
        in case a future caller (e.g. a provider adapter) over-shares.
        """

        citation = payload.get(_Fields.CITATION)
        safe_citation = cls._safe_citation_ref(citation)
        if safe_citation is None:
            return {}
        return {_Fields.CITATION: safe_citation}

    @classmethod
    def _sources_ingested_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``sources_ingested`` payloads through a strict allow-list.

        Plural variant of :meth:`_source_ingested_payload` (P7). Iterates
        ``payload.citations`` and applies the same per-citation allow-list,
        preserving order so the FE registry sees ordinals in the order the
        ledger allocated them.
        """

        citations = payload.get("citations")
        if not isinstance(citations, list):
            return {"citations": []}
        safe_citations: list[JsonObject] = []
        for citation in citations:
            safe = cls._safe_citation_ref(citation)
            if safe is not None:
                safe_citations.append(safe)
        return {"citations": safe_citations}

    @staticmethod
    def _safe_citation_ref(value: object) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        safe: JsonObject = {}
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
            v = value.get(text_key)
            if isinstance(v, str) and v.strip():
                safe[text_key] = v
            elif v is None and text_key in {
                "source_url",
                "snippet",
                "freshness_at",
                "source_tool_call_id",
            }:
                safe[text_key] = None
        ordinal = value.get("ordinal")
        if isinstance(ordinal, int) and ordinal > 0:
            safe["ordinal"] = ordinal
        return safe

    @classmethod
    def _citation_made_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``citation_made`` payloads through a strict allow-list.

        The CitationResolver is the only intended emitter and always supplies
        the full ``CitationLink`` shape (conversation_ordinal, message_id,
        prose offsets, source_tool_call_id). We whitelist defensively so a
        future emitter can't over-share.
        """

        link = payload.get(_Fields.LINK)
        if not isinstance(link, dict):
            return {}
        safe_link: JsonObject = {}
        ordinal = link.get(_Fields.CONVERSATION_ORDINAL)
        if isinstance(ordinal, int) and ordinal > 0:
            safe_link[_Fields.CONVERSATION_ORDINAL] = ordinal
        # ``message_id`` must be a non-empty string — without it the FE
        # cannot key the chip back to its assistant message.
        message_id = link.get(_Fields.MESSAGE_ID)
        if isinstance(message_id, str) and message_id.strip():
            safe_link[_Fields.MESSAGE_ID] = message_id
        # ``source_tool_call_id`` is *allowed* to be empty: when the
        # model emits ``[[N]]`` for an ordinal the allocator hasn't
        # bound to a tool_call_id (hallucinated ordinal, or
        # provider-native passthrough that fired before the tool
        # message materialized), the resolver still emits the event so
        # the chip can render — the FE renders it as a muted
        # placeholder when the call_id is empty. Preserve the field as
        # a string (possibly empty) so the FE type guard accepts it.
        source_tool_call_id = link.get(_Fields.SOURCE_TOOL_CALL_ID)
        if isinstance(source_tool_call_id, str):
            safe_link[_Fields.SOURCE_TOOL_CALL_ID] = source_tool_call_id
        else:
            safe_link[_Fields.SOURCE_TOOL_CALL_ID] = ""
        for offset_key in (_Fields.PROSE_OFFSET, _Fields.PROSE_LENGTH):
            value = link.get(offset_key)
            if isinstance(value, int) and value >= 0:
                safe_link[offset_key] = value
        return {_Fields.LINK: safe_link}

    @classmethod
    def _surface_spec_generated_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``surface_spec_generated`` payloads through a strict allow-list.

        The async spec generator (PRD-07) is the intended emitter; we whitelist
        defensively so a future caller cannot over-share. ``spec`` is the
        SurfaceSpec dict — passed through only when it is an object (it was
        schema-validated upstream, and the SurfaceSpec schema has no
        side-effectful members, plan D9). PRD-01 freezes this projection; no
        emitter exists yet.
        """

        safe_payload: JsonObject = {}
        for text_key in (
            _Fields.SURFACE_URI,
            _Fields.ARCHETYPE,
            _Fields.GENERATOR_MODEL,
            _Fields.SKILL_VERSION,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        spec_version = payload.get(_Fields.SPEC_VERSION)
        if isinstance(spec_version, int) and not isinstance(spec_version, bool):
            safe_payload[_Fields.SPEC_VERSION] = spec_version
        spec = payload.get(_Fields.SPEC)
        if isinstance(spec, dict):
            safe_payload[_Fields.SPEC] = spec
        return safe_payload

    @classmethod
    def _usage_recorded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``usage.recorded`` payloads through a strict allow-list.

        Keeps exactly the SDR §5 fields — ``v`` / ``purpose`` / ``model`` /
        ``tokens_in`` / ``tokens_out`` / ``surface_id`` — so an emitter can
        never over-share (tenant ids stay off the envelope; ``surface_id`` is
        optional). The :class:`UsageMeter` is the intended emitter (PRD-A2);
        this projection re-filters on append regardless.
        """

        safe_payload: JsonObject = {}
        version = payload.get(_Fields.USAGE_V)
        if isinstance(version, int) and not isinstance(version, bool):
            safe_payload[_Fields.USAGE_V] = version
        for text_key in (
            _Fields.USAGE_PURPOSE,
            _Fields.USAGE_MODEL,
            _Fields.USAGE_SURFACE_ID,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        for token_key in (_Fields.USAGE_TOKENS_IN, _Fields.USAGE_TOKENS_OUT):
            tokens = payload.get(token_key)
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                safe_payload[token_key] = tokens
        return safe_payload

    @classmethod
    def _action_classified_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``action.classified`` through a strict allow-list (PRD-A3 D5).

        Keeps exactly the SDR §5 fields — ``v`` / ``call_id`` / ``connector`` /
        ``op`` / ``class`` / ``basis`` — so an emitter can never over-share. In
        Wave A ``class`` is always ``"unknown"`` and ``basis`` ``"default"`` (no
        classifier yet); this projection re-filters regardless of the emitter.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.CALL_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.OP,
            _LedgerKeys.Field.CLASS,
            _LedgerKeys.Field.BASIS,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _read_executed_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``read.executed`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``call_id`` / ``connector`` / ``op`` / ``payload_ref`` and
        the optional non-negative ``latency_ms``. ``payload_ref`` trips the
        ``"ref"``-key OFFLOADED marker in ``_redaction_state_for`` — correct, it
        *is* a reference.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.CALL_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.OP,
            _LedgerKeys.Field.PAYLOAD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        latency = payload.get(_LedgerKeys.Field.LATENCY_MS)
        if isinstance(latency, int) and not isinstance(latency, bool) and latency >= 0:
            safe_payload[_LedgerKeys.Field.LATENCY_MS] = latency
        return safe_payload

    @classmethod
    def _surface_created_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``surface.created`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``surface_id`` / ``kind`` / ``source{connector,op}`` /
        ``title`` / ``payload_ref``. ``source`` is re-built from its own nested
        allow-list so untrusted extra keys cannot ride through.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.KIND,
            _LedgerKeys.Field.TITLE,
            _LedgerKeys.Field.PAYLOAD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        source = cls._op_ref(payload.get(_LedgerKeys.Field.SOURCE))
        if source is not None:
            safe_payload[_LedgerKeys.Field.SOURCE] = source
        return safe_payload

    @classmethod
    def _view_derived_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``view.derived`` through a strict allow-list (PRD-A3 D5).

        Keeps ``v`` / ``surface_id`` / ``tier`` / ``basis`` / optional
        ``spec_ref`` / optional ``gen{model}``. ``gen`` is re-built from a nested
        allow-list (``ms`` is not measured in A3, so only ``model`` survives).
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.TIER,
            _LedgerKeys.Field.BASIS,
            _LedgerKeys.Field.SPEC_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        gen = payload.get(_LedgerKeys.Field.GEN)
        if isinstance(gen, dict):
            model = cls._text(gen.get(_LedgerKeys.Field.MODEL))
            if model is not None:
                safe_gen: JsonObject = {_LedgerKeys.Field.MODEL: model}
                # PRD-B3 widens A3's ``gen`` allow-list to admit the generation
                # duration ``ms`` (int) the ViewDeriver now populates. Without it
                # the projector would silently drop ``gen.ms`` and the B3 payload
                # spec (``gen: {model, ms}``) would not survive the wire.
                ms = gen.get(_LedgerKeys.Field.MS)
                if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
                    safe_gen[_LedgerKeys.Field.MS] = ms
                safe_payload[_LedgerKeys.Field.GEN] = safe_gen
        return safe_payload

    @classmethod
    def _view_preference_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``view.preference`` through a strict allow-list (PRD-B3).

        Keeps exactly the SDR §5 fields — ``v`` / ``surface_id`` / ``keep`` /
        ``actor`` — so a user-initiated preference append can never over-share.
        ``keep`` and ``actor`` are constrained value strings; anything else is
        dropped (the ledger append re-filters regardless of the caller).
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.KEEP,
            _LedgerKeys.Field.ACTOR,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _gate_opened_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``gate.opened`` through a strict allow-list (PRD-C2, SDR §5).

        Keeps exactly ``v`` / ``gate_id`` / ``connector`` / ``purpose`` /
        ``scopes[]`` / ``auth_state`` — so a gate emit can never over-share (the
        interrupt payload carries the connect URL + display copy; none of it
        rides the ledger row). ``scopes`` is re-built from its own list so a
        non-string element can't slip through.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.GATE_ID,
            _LedgerKeys.Field.CONNECTOR,
            _LedgerKeys.Field.PURPOSE,
            _LedgerKeys.Field.AUTH_STATE,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        scopes = payload.get(_LedgerKeys.Field.SCOPES)
        if isinstance(scopes, (list, tuple)):
            safe_payload[_LedgerKeys.Field.SCOPES] = [
                s for s in scopes if isinstance(s, str)
            ]
        return safe_payload

    @classmethod
    def _gate_resolved_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``gate.resolved`` through a strict allow-list (PRD-C2, SDR §5).

        Keeps ``v`` / ``gate_id`` / ``outcome`` and the optional ``write_policy``
        (``ask_first`` / ``allow_always``). Nothing else survives.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.GATE_ID,
            _LedgerKeys.Field.OUTCOME,
            _LedgerKeys.Field.WRITE_POLICY,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _write_staged_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``write.staged`` through a strict allow-list (PRD-D1, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``surface_id`` / ``target{connector,op}`` /
        ``proposal_ref`` — the single-artifact shape (``rows`` / ``agent_holds``
        are D3). ``target`` is rebuilt from its own allow-list so no extra keys
        ride through.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.PROPOSAL_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        target = cls._op_ref(payload.get(_LedgerKeys.Field.TARGET))
        if target is not None:
            safe_payload[_LedgerKeys.Field.TARGET] = target
        # PRD-D3 — a row-set stage carries the ``rows`` count + the ``agent_holds``
        # (each rebuilt from its own ``{row_key, reason}`` allow-list; reasons are
        # rendered UI text, so length-capped + kept as plain strings).
        rows = payload.get(_LedgerKeys.Field.ROWS)
        if isinstance(rows, int) and not isinstance(rows, bool):
            safe_payload[_LedgerKeys.Field.ROWS] = rows
        holds = payload.get(_LedgerKeys.Field.AGENT_HOLDS)
        if isinstance(holds, (list, tuple)):
            safe_payload[_LedgerKeys.Field.AGENT_HOLDS] = [
                hold
                for hold in (cls._agent_hold(raw) for raw in holds)
                if hold is not None
            ]
        return safe_payload

    @classmethod
    def _agent_hold(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, reason}`` agent-hold from its own allow-list."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        reason = cls._text(value.get(_LedgerKeys.Field.REASON))
        if row_key is None or reason is None:
            return None
        return {
            _LedgerKeys.Field.ROW_KEY: row_key,
            _LedgerKeys.Field.REASON: reason[:_ROWSET_TEXT_MAX],
        }

    @classmethod
    def _revision_added_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``revision.added`` through a strict allow-list (PRD-D1, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``rev`` / ``author`` / ``diff_ref`` plus the
        additive ``proposal_ref`` (this rev's snapshot) and ``authorship_spans``
        (the server-computed "edited by you" ranges). Each span is rebuilt from
        its own allow-list — only int ``start``/``end`` and a known ``author``
        survive, so nothing extra rides the ledger row.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.AUTHOR,
            _LedgerKeys.Field.DIFF_REF,
            _LedgerKeys.Field.PROPOSAL_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        rev = payload.get(_LedgerKeys.Field.REV)
        if isinstance(rev, int) and not isinstance(rev, bool):
            safe_payload[_LedgerKeys.Field.REV] = rev
        spans = payload.get(_LedgerKeys.Field.AUTHORSHIP_SPANS)
        if isinstance(spans, (list, tuple)):
            safe_payload[_LedgerKeys.Field.AUTHORSHIP_SPANS] = [
                span
                for span in (cls._authorship_span(raw) for raw in spans)
                if span is not None
            ]
        # PRD-D3 — the additive inline ``rowset`` (full row content). Each row is
        # rebuilt from its own allow-list so nothing extra rides the ledger row.
        rowset = cls._rowset(payload.get(_LedgerKeys.Field.ROWSET))
        if rowset is not None:
            safe_payload[_LedgerKeys.Field.ROWSET] = rowset
        return safe_payload

    @classmethod
    def _rowset(cls, value: object) -> JsonObject | None:
        """Rebuild ``{rows: [StagedRow…]}`` from a strict per-field allow-list."""

        if not isinstance(value, dict):
            return None
        raw_rows = value.get(_LedgerKeys.Field.ROWS)
        if not isinstance(raw_rows, (list, tuple)):
            return None
        rows = [row for row in (cls._staged_row(raw) for raw in raw_rows) if row]
        return {_LedgerKeys.Field.ROWS: rows}

    @classmethod
    def _staged_row(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, title, target_args, changes}`` staged row."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        title = cls._text(value.get(_LedgerKeys.Field.TITLE))
        if row_key is None or title is None:
            return None
        row: JsonObject = {
            _LedgerKeys.Field.ROW_KEY: row_key[:_ROWSET_TEXT_MAX],
            _LedgerKeys.Field.TITLE: title[:_ROWSET_TEXT_MAX],
        }
        target_args = value.get(_LedgerKeys.Field.TARGET_ARGS)
        if isinstance(target_args, dict):
            # Connector args are the server-held WYSIWYG unit — passed through as a
            # JSON object (keys coerced to str); the client never re-sends them.
            row[_LedgerKeys.Field.TARGET_ARGS] = {
                str(key): val for key, val in target_args.items()
            }
        changes = value.get(_LedgerKeys.Field.CHANGES)
        if isinstance(changes, (list, tuple)):
            row[_LedgerKeys.Field.CHANGES] = [
                change
                for change in (cls._row_change(raw) for raw in changes)
                if change is not None
            ]
        return row

    @classmethod
    def _row_change(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{field, old?, new?}`` field diff from its allow-list."""

        if not isinstance(value, dict):
            return None
        field_name = cls._text(value.get(_LedgerKeys.Field.FIELD))
        if field_name is None:
            return None
        change: JsonObject = {_LedgerKeys.Field.FIELD: field_name[:_ROWSET_TEXT_MAX]}
        if _LedgerKeys.Field.OLD in value:
            change[_LedgerKeys.Field.OLD] = value.get(_LedgerKeys.Field.OLD)
        if _LedgerKeys.Field.NEW in value:
            change[_LedgerKeys.Field.NEW] = value.get(_LedgerKeys.Field.NEW)
        return change

    @classmethod
    def _authorship_span(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{start, end, author}`` span from its own allow-list."""

        if not isinstance(value, dict):
            return None
        start = value.get(_LedgerKeys.Field.START)
        end = value.get(_LedgerKeys.Field.END)
        author = cls._text(value.get(_LedgerKeys.Field.AUTHOR))
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(end, int)
            and not isinstance(end, bool)
            and author in ("agent", "user")
        ):
            return {
                _LedgerKeys.Field.START: start,
                _LedgerKeys.Field.END: end,
                _LedgerKeys.Field.AUTHOR: author,
            }
        return None

    @classmethod
    def _decision_recorded_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``decision.recorded`` through a strict allow-list (PRD-D1).

        Keeps ``v`` / ``stage_id`` / ``decision`` / ``actor`` and the
        ``scope{rev}`` (single artifact — ``row_keys`` is D3). Nothing else
        survives; the ``scope`` object is rebuilt from its own ``rev`` key.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.DECISION,
            _LedgerKeys.Field.ACTOR,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        scope = payload.get(_LedgerKeys.Field.SCOPE)
        if isinstance(scope, dict):
            rev = scope.get(_LedgerKeys.Field.REV)
            row_keys = scope.get(_LedgerKeys.Field.ROW_KEYS)
            if isinstance(rev, int) and not isinstance(rev, bool):
                safe_payload[_LedgerKeys.Field.SCOPE] = {_LedgerKeys.Field.REV: rev}
            elif isinstance(row_keys, (list, tuple)):
                # PRD-D3 — a row-scoped decision. Only string row keys survive.
                safe_payload[_LedgerKeys.Field.SCOPE] = {
                    _LedgerKeys.Field.ROW_KEYS: [
                        key[:_ROWSET_TEXT_MAX]
                        for key in row_keys
                        if isinstance(key, str) and key
                    ]
                }
        # PRD-D3 — ``apply: true`` marks the apply-scoped approve (freezes the set).
        if payload.get(_LedgerKeys.Field.APPLY) is True:
            safe_payload[_LedgerKeys.Field.APPLY] = True
        return safe_payload

    @classmethod
    def _write_applied_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``write.applied`` through a strict allow-list (PRD-D2, SDR §5).

        Keeps ``v`` / ``stage_id`` / ``rev`` / ``result`` plus the additive
        ``connector_receipt_ref`` (an opaque ``commit://`` ref), ``failure``
        (rebuilt from its own ``{code, detail}`` allow-list — ``failed`` only)
        and ``decided_by`` (``{actor, decision_seq}`` — the receipt row). The
        single-artifact shape only (``row_keys`` / ``partial`` are D3; they never
        emit here). Nothing else survives — the connector result is NEVER echoed
        raw into the event; only the ref rides.
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.STAGE_ID,
            _LedgerKeys.Field.RESULT,
            _LedgerKeys.Field.CONNECTOR_RECEIPT_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        rev = payload.get(_LedgerKeys.Field.REV)
        if isinstance(rev, int) and not isinstance(rev, bool):
            safe_payload[_LedgerKeys.Field.REV] = rev
        failure = cls._write_applied_failure(payload.get(_LedgerKeys.Field.FAILURE))
        if failure is not None:
            safe_payload[_LedgerKeys.Field.FAILURE] = failure
        decided_by = cls._write_applied_decided_by(
            payload.get(_LedgerKeys.Field.DECIDED_BY)
        )
        if decided_by is not None:
            safe_payload[_LedgerKeys.Field.DECIDED_BY] = decided_by
        # PRD-D3 — the applied row set + per-row outcomes (partial-apply). Each
        # ``row_results`` entry is rebuilt from its own ``{row_key, outcome, detail?}``
        # allow-list; nothing else survives.
        row_keys = payload.get(_LedgerKeys.Field.ROW_KEYS)
        if isinstance(row_keys, (list, tuple)):
            safe_payload[_LedgerKeys.Field.ROW_KEYS] = [
                key[:_ROWSET_TEXT_MAX]
                for key in row_keys
                if isinstance(key, str) and key
            ]
        row_results = payload.get(_LedgerKeys.Field.ROW_RESULTS)
        if isinstance(row_results, (list, tuple)):
            safe_payload[_LedgerKeys.Field.ROW_RESULTS] = [
                entry
                for entry in (cls._row_result(raw) for raw in row_results)
                if entry is not None
            ]
        return safe_payload

    @classmethod
    def _row_result(cls, value: object) -> JsonObject | None:
        """Rebuild one ``{row_key, outcome, detail?}`` row result from its allow-list."""

        if not isinstance(value, dict):
            return None
        row_key = cls._text(value.get(_LedgerKeys.Field.ROW_KEY))
        outcome = cls._text(value.get(_LedgerKeys.Field.OUTCOME))
        if row_key is None or outcome not in (
            _LedgerValues.ROW_OUTCOME_APPLIED,
            _LedgerValues.ROW_OUTCOME_FAILED,
        ):
            return None
        result: JsonObject = {
            _LedgerKeys.Field.ROW_KEY: row_key[:_ROWSET_TEXT_MAX],
            _LedgerKeys.Field.OUTCOME: outcome,
        }
        detail = cls._text(value.get(_LedgerKeys.Field.DETAIL))
        if detail is not None:
            result[_LedgerKeys.Field.DETAIL] = detail[:_ROWSET_TEXT_MAX]
        return result

    @classmethod
    def _write_applied_failure(cls, value: object) -> JsonObject | None:
        """Rebuild ``{code, detail?}`` from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        code = cls._text(value.get(_LedgerKeys.Field.CODE))
        if code is None:
            return None
        failure: JsonObject = {_LedgerKeys.Field.CODE: code}
        detail = cls._text(value.get(_LedgerKeys.Field.DETAIL))
        if detail is not None:
            failure[_LedgerKeys.Field.DETAIL] = detail
        return failure

    @classmethod
    def _write_applied_decided_by(cls, value: object) -> JsonObject | None:
        """Rebuild ``{actor, decision_seq}`` from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        actor = cls._text(value.get(_LedgerKeys.Field.ACTOR))
        decision_seq = value.get(_LedgerKeys.Field.DECISION_SEQ)
        if actor is None or not (
            isinstance(decision_seq, int) and not isinstance(decision_seq, bool)
        ):
            return None
        return {
            _LedgerKeys.Field.ACTOR: actor,
            _LedgerKeys.Field.DECISION_SEQ: decision_seq,
        }

    @classmethod
    def _receipt_emitted_payload(cls, payload: JsonObject) -> JsonObject:
        """Project ``receipt.emitted`` through a strict allow-list (PRD-E1, SDR §5).

        Keeps only ``v`` / ``surface_id`` / ``fold_ref`` — nothing else rides.
        ``fold_ref`` contains ``"ref"`` so ``_redaction_state_for`` marks the row
        ``OFFLOADED`` (it IS a reference — the receipt is re-derivable by folding
        the run's events, never a stored blob).
        """

        safe_payload: JsonObject = {}
        cls._copy_payload_version(payload, safe_payload)
        for text_key in (
            _LedgerKeys.Field.SURFACE_ID,
            _LedgerKeys.Field.FOLD_REF,
        ):
            value = cls._text(payload.get(text_key))
            if value is not None:
                safe_payload[text_key] = value
        return safe_payload

    @classmethod
    def _copy_payload_version(
        cls, payload: JsonObject, safe_payload: JsonObject
    ) -> None:
        """Copy the ``v`` payload-version integer through when it is a real int."""

        version = payload.get(_LedgerKeys.Field.V)
        if isinstance(version, int) and not isinstance(version, bool):
            safe_payload[_LedgerKeys.Field.V] = version

    @classmethod
    def _op_ref(cls, value: object) -> JsonObject | None:
        """Rebuild a ``{connector, op}`` ref from its own allow-list, or None."""

        if not isinstance(value, dict):
            return None
        connector = cls._text(value.get(_LedgerKeys.Field.CONNECTOR))
        op = cls._text(value.get(_LedgerKeys.Field.OP))
        if connector is None or op is None:
            return None
        return {_LedgerKeys.Field.CONNECTOR: connector, _LedgerKeys.Field.OP: op}

    @classmethod
    def _approval_requested_payload(cls, payload: JsonObject) -> JsonObject:
        approval_kind = cls._text(payload.get(Keys.Field.APPROVAL_KIND))
        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            return cls._ask_a_question_requested_payload(payload)
        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            # P1-A re-scoped — SUGGEST_EDIT child rows surface the parent
            # link + the editing user; both are short opaque ids.
            Keys.Field.CHAIN_PARENT_APPROVAL_ID,
            "edited_by_user_id",
            Keys.Field.BATCH_ID,
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
        # PR #43 — batch_index is a typed int, not a string; preserve it
        # through the projection so the FE receives the typed shape.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
        read_only = payload.get("read_only")
        if isinstance(read_only, bool):
            safe_payload["read_only"] = read_only
        arguments = payload.get("arguments")
        if isinstance(arguments, dict):
            safe_payload["arguments"] = arguments
        # P1-A re-scoped — SUGGEST_EDIT carries the approver's proposed
        # tool-call arguments. Same shape as ``arguments``: arbitrary
        # JSON object the FE renders as a diff vs the original.
        edited_payload = payload.get("edited_payload")
        if isinstance(edited_payload, dict):
            safe_payload["edited_payload"] = edited_payload
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
        """Project ask-a-question payloads, preserving question text and structured options.

        The standard approval allow-list strips these fields, so this approval kind
        gets its own projection path.
        """

        safe_payload: JsonObject = {}
        for key in (
            Keys.Field.APPROVAL_ID,
            Keys.Field.APPROVAL_KIND,
            Keys.Field.BATCH_ID,
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
        # PR #43 — typed batch_index preserved through projection.
        batch_index = payload.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            safe_payload[Keys.Field.BATCH_INDEX] = batch_index
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
        """Coerce option dicts and bare strings into a sanitised list.

        Bare strings are upgraded to ``{label: ...}`` for backwards compatibility
        with callers that haven't adopted the structured shape yet.
        """

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
        """Strip HTML angle brackets, collapse whitespace, and truncate to ``max_length``."""
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
        return JsonObjectCoercer.coerce(value)

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
    """Event data before the event store assigns per-run sequence number.

    Carries ``org_id`` so the persistence adapter can scope its tenant
    connection BEFORE the canonical ``agent_runs`` row is read. The field
    lives on the draft only — :class:`RuntimeEventEnvelope` (the wire shape
    SSE/replay returns to clients) deliberately omits ``org_id`` so tenant
    identifiers are not exposed in user-visible payloads.
    """

    org_id: str

    @field_validator(Keys.Field.ORG_ID, mode="before")
    @classmethod
    def _normalize_org_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @classmethod
    def from_stream_event(
        cls,
        *,
        run_id: str,
        conversation_id: str,
        org_id: str,
        stream_event: StreamEvent,
    ) -> "RuntimeEventDraft":
        """Create an appendable API event draft from a normalized runtime event."""

        kwargs = cls._build_from_stream_event(
            run_id=run_id,
            conversation_id=conversation_id,
            stream_event=stream_event,
        )
        return cls(org_id=org_id, **kwargs)
