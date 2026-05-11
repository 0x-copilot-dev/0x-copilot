"""Shared runtime API schema enums and value normalization."""

from __future__ import annotations

import logging
from enum import StrEnum

from agent_runtime.execution.contracts import StreamEventType


class ConversationStatus(StrEnum):
    """Conversation lifecycle states visible to API clients."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    """Conversation message roles persisted by the API producer."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageStatus(StrEnum):
    """Message lifecycle states."""

    CREATED = "created"
    DELETED = "deleted"


class AgentRunStatus(StrEnum):
    """Runtime run states used by the producer/consumer event contract."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class RuntimeEventVisibility(StrEnum):
    """Client visibility class for timeline and audit events."""

    USER = "user"
    INTERNAL = "internal"
    AUDIT = "audit"


class RuntimeEventRedactionState(StrEnum):
    """How event payload details were prepared before persistence."""

    REDACTED = "redacted"
    TRUNCATED = "truncated"
    OFFLOADED = "offloaded"


class RuntimeActivityKind(StrEnum):
    """Server-projected UI activity bucket for runtime events."""

    RUN = "run"
    MESSAGE = "message"
    TOOL = "tool"
    SUBAGENT = "subagent"
    REASONING = "reasoning"
    MCP_AUTH = "mcp_auth"
    APPROVAL = "approval"
    HEARTBEAT = "heartbeat"
    EVENT = "event"
    DRAFT = "draft"
    # PR A1 — context-compression note ("Atlas summarised N older
    # messages…"). Renders as a single dim line in-thread, not a card.
    NOTE = "note"


class RuntimeApiEventType(StrEnum):
    """Versioned event types emitted through the API transport envelope."""

    RUN_QUEUED = "run_queued"
    RUN_STARTED = "run_started"
    RUN_CANCELLING = "run_cancelling"
    RUN_CANCELLED = "run_cancelled"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    PROGRESS = "progress"
    REASONING_SUMMARY = "reasoning_summary"
    REASONING_SUMMARY_DELTA = "reasoning_summary_delta"
    TOOL_CALL = "tool_call"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_RESULT = "tool_result"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    MCP_AUTH_REQUIRED = "mcp_auth_required"
    SUBAGENT_UPDATE = "subagent_update"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_PROGRESS = "subagent_progress"
    SUBAGENT_COMPLETED = "subagent_completed"
    # PR A2 — parallel-batch grouping. When the orchestrator dispatches
    # > 1 subagent in a single tick, it wraps them in a fleet so the FE
    # can render a single `<SubagentFleetCard>` instead of N siblings.
    # Each child subagent event carries `parent_fleet_id` in
    # `payload.parent_fleet_id` for binding.
    SUBAGENT_FLEET_STARTED = "subagent_fleet_started"
    SUBAGENT_FLEET_FINISHED = "subagent_fleet_finished"
    # PR 3.2.5 Phase 3 — explicit per-subagent pause/resume signals so the
    # FE marks a fleet row "paused" without inferring from the absence of
    # SUBAGENT_COMPLETED. Emitted by `stream_events.append_activity_events`
    # when an APPROVAL_REQUESTED / MCP_AUTH_REQUIRED / ASK_A_QUESTION
    # interrupt fires AND `parent_task_id` resolves to a subagent's
    # supervisor `task` call_id; resumed by the approval handler when the
    # paused branch's interrupt is resolved. Both events carry
    # `task_id == parent_task_id == supervisor_call_id` so the existing
    # `applySubagentEvent` reducer slot finds them by task_id.
    SUBAGENT_PAUSED = "subagent_paused"
    SUBAGENT_RESUMED = "subagent_resumed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    # PR 1.4 — two-stage approval forwarding. Emitted between
    # APPROVAL_RESOLVED (status=forwarded) on the parent and
    # APPROVAL_REQUESTED on the child so the FE can transform the original
    # in-thread card into a "Waiting on @marcus" pill in one reducer step.
    APPROVAL_FORWARDED = "approval_forwarded"
    # PR 4.4.6.4 — user requested undo of an approved write within the
    # 60s reversibility window. Emitted alongside the audit row; replay
    # via ?after_sequence=N retains it like any other run event.
    APPROVAL_UNDO_REQUESTED = "approval_undo_requested"
    OBSERVATION = "observation"
    ERROR = "error"
    MODEL_CALL_STARTED = "model_call_started"
    MODEL_CALL_COMPLETED = "model_call_completed"
    MODEL_DELTA = "model_delta"
    FINAL_RESPONSE = "final_response"
    HEARTBEAT = "heartbeat"
    PRESENTATION_UPDATED = "presentation_updated"
    # Budget enforcement (B7). RUN_REJECTED is distinct from RUN_FAILED so
    # the UI can show "budget exceeded" rather than a generic error.
    BUDGET_WARNING = "budget_warning"
    RUN_REJECTED = "run_rejected"
    # PR 1.3 — Workspace pane Draft tab. Emitted by DraftBackend on every
    # successful awrite/aedit and by RuntimeApiService on user PATCH /
    # POST send / POST discard.
    DRAFT_UPDATED = "draft_updated"
    # PR 1.1 — citations live registry. One event per (run, source) the
    # CitationLedger registers; payload carries `CitationSourceRef` under
    # `payload.citation` and projects to RuntimeActivityKind.TOOL.
    SOURCE_INGESTED = "source_ingested"
    # P7 (refactor) — batched variant of SOURCE_INGESTED. Emitted by
    # CitationLedger.register_many when N>1 sources are ingested in a
    # single call; payload carries an ordered list of CitationSourceRef
    # under `payload.citations`. Same activity_kind / status / display
    # treatment as the singular variant; FE reducers iterate the list.
    SOURCES_INGESTED = "sources_ingested"
    # PR 1.1-rev2 — model-declared citation. Emitted by CitationResolver each
    # time a `[[N]]` token in the streamed assistant text resolves to a
    # tool invocation by `conversation_ordinal`. Payload carries `CitationLink`
    # under `payload.link` (message_id + prose_offset/length + ordinal +
    # source_tool_call_id). Projects to RuntimeActivityKind.TOOL.
    CITATION_MADE = "citation_made"
    # PR A1 — context-compression note. Emitted by the compression hook
    # when the context window manager redacts older messages to keep
    # the run efficient. Payload carries
    # ``CompressionNotePayload`` (before_tokens, after_tokens, strategy,
    # summary_text, payload_refs). Projects to
    # ``RuntimeActivityKind.NOTE``; the FE renders an inline
    # ``<NoteCard>`` ("Atlas summarised N older messages to keep this
    # conversation efficient.").
    COMPRESSION_NOTE = "compression_note"

    @classmethod
    def from_stream_event_type(
        cls, event_type: StreamEventType
    ) -> "RuntimeApiEventType":
        """Map normalized runtime stream events into API transport events."""

        mapping = {
            StreamEventType.PROGRESS: cls.PROGRESS,
            StreamEventType.TOOL_CALL: cls.TOOL_CALL,
            StreamEventType.TOOL_RESULT: cls.TOOL_RESULT,
            StreamEventType.CUSTOM: cls.PROGRESS,
            StreamEventType.LIFECYCLE: cls.SUBAGENT_UPDATE,
            StreamEventType.SUBAGENT_UPDATE: cls.SUBAGENT_UPDATE,
            StreamEventType.OBSERVATION: cls.OBSERVATION,
            StreamEventType.ERROR: cls.ERROR,
            StreamEventType.FINAL: cls.FINAL_RESPONSE,
            StreamEventType.FINAL_RESPONSE: cls.FINAL_RESPONSE,
        }
        result = mapping.get(event_type)
        if result is None:
            logging.getLogger(__name__).warning(
                "Unmapped stream event type: %s", event_type
            )
            return cls.PROGRESS
        return result


class ApprovalDecision(StrEnum):
    """Allowed user decisions for side-effecting approval requests.

    PR 1.4 — ``FORWARDED`` is an API-edge variant: it routes the pending
    approval to a second workspace user and never reaches the LangGraph
    harness. The worker discriminates on this enum and skips
    ``Command(resume=...)`` for the forwarded case.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"


class ApprovalStatus(StrEnum):
    """Approval request state after a decision is accepted.

    PR 1.4 — ``FORWARDED`` is a terminal state for the parent row in a
    chain. Resume of the underlying run hangs off the child row's
    eventual ``APPROVED`` / ``REJECTED`` instead.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"


# PR 4.4.6.2 — structured consent-card payload for ``approval_kind ==
# "mcp_tool"``. The runtime worker projects connector / tool / call-arg
# context into this vocabulary so the FE renders without inferring from
# Booleans. All three enums are ``StrEnum`` so wire serialisation is the
# enum value itself; api-types mirrors the same string literals.


class ApprovalCategory(StrEnum):
    """Access category for the right-hand vendor pill."""

    READ = "read"
    WRITE = "write"
    ACTION = "action"


class ApprovalReasonCode(StrEnum):
    """Why the user is being asked. Drives the FE's reason sentence.

    Open-ended on purpose — unrecognised values fall back to
    ``DEFAULT`` on the FE so a server can ship new variants ahead of a
    bundle without breaking old clients.
    """

    READ_ONLY_FIRST_USE = "read_only_first_use"
    WRITES_OUT_OF_WORKSPACE = "writes_out_of_workspace"
    RISK_HIGH = "risk_high"
    IRREVERSIBLE = "irreversible"
    DEFAULT = "default"


class ApprovalReversible(StrEnum):
    """Tri-state reversibility marker. ``NOT_APPLICABLE`` for read-only
    actions where the question doesn't make sense."""

    YES = "yes"
    NO = "no"
    NOT_APPLICABLE = "n/a"
