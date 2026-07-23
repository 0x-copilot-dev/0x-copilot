"""Shared runtime API schema enums and value normalization."""

from __future__ import annotations

import logging
from enum import StrEnum

from agent_runtime.execution.contracts import StreamEventType
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


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
    # successful awrite/aedit and by ConversationCoordinator on user PATCH /
    # POST send / POST discard.
    DRAFT_UPDATED = "draft_updated"
    # AC5 slice 3b — host write-through. Emitted by the workspace backend's
    # snapshot-before-write step: BEFORE an approved overwrite/edit mutates a
    # user's host file, the pre-image bytes are put into the content-addressed
    # object store and this event records the reference (op / mount / virtual
    # path / object_sha256 / size — never a host path) so the change is
    # auditable and undoable. Projects to the default RuntimeActivityKind.EVENT.
    WORKSPACE_SNAPSHOT_CAPTURED = "workspace_snapshot_captured"
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
    # Phase 6B — agent-generated tier-2 render adapter ready to install.
    # Emitted by ``RenderAdapterGenerator`` when the constrained-template
    # codegen produces a complete ``SaaSRendererAdapter`` source string.
    # Payload carries ``scheme`` / ``layout`` / ``schema_version`` /
    # ``adapter_source``. Projects to ``RuntimeActivityKind.EVENT``; the
    # desktop's tier-2 lifecycle (6C) subscribes via the existing SSE
    # channel, persists to ``{userData}/adapters/{scheme}-v{n}.js``, and
    # hands the source to the local quality gate (6D).
    ADAPTER_GENERATED = "adapter_generated"
    # Generative-UI (PRD-01) — the async spec generator produced a validated
    # ``SurfaceSpec`` for a ``(server, tool, output_shape)``. Payload carries
    # ``surface_uri`` / ``archetype`` / ``spec`` / ``spec_version`` /
    # ``generator_model`` / ``skill_version``. Projects to
    # ``RuntimeActivityKind.EVENT``; the FE projector merges ``spec`` into
    # ``surfaceState[surface_uri]`` so the next render upgrades in place from
    # tier-3 to the archetype view (plan D4). No emitter/renderer yet — PRD-01
    # freezes the contract only.
    SURFACE_SPEC_GENERATED = "surface_spec_generated"
    # Generative Surfaces v2 (PRD-A2, SDR §5). One per usage-bearing LLM call
    # whose store purpose maps to a ledger purpose (run / subagent /
    # view_shaping / shape_request). The wire value is the SDR §5 ledger
    # constant ``usage.recorded`` (dotted, matching the A1 vocabulary — not the
    # underscore convention of the transport events above). Emission is gated on
    # ``SURFACES_V2``; the projector's ``_usage_recorded_payload`` allow-list
    # keeps only ``v`` / ``purpose`` / ``model`` / ``tokens_in`` / ``tokens_out``
    # / ``surface_id`` — tenant ids never ride the envelope.
    USAGE_RECORDED = "usage.recorded"
    # Generative Surfaces v2 (PRD-A3, SDR §5). The first four ledger *emission*
    # types the runtime records behind ``SURFACES_V2`` for what the v1 pipeline
    # already does (MCP reads, v1 surface envelopes, async spec upgrades). Wire
    # values are sourced from the A1 ``LedgerEventType`` vocabulary (``.value``),
    # never re-typed literals, so the transport enum cannot drift from the SSOT.
    # All four project to ``RuntimeActivityKind.EVENT`` (surface-state merges,
    # not timeline cards); A3's SurfaceStore fold consumes them.
    ACTION_CLASSIFIED = LedgerEventType.ACTION_CLASSIFIED.value
    READ_EXECUTED = LedgerEventType.READ_EXECUTED.value
    SURFACE_CREATED = LedgerEventType.SURFACE_CREATED.value
    VIEW_DERIVED = LedgerEventType.VIEW_DERIVED.value
    # Generative Surfaces v2 (PRD-B3). The durable tier preference — a user pin
    # ("Keep generic") that survives reload by replay. Projects to
    # ``RuntimeActivityKind.EVENT`` (a SurfaceStore fold input, not a card).
    VIEW_PREFERENCE = LedgerEventType.VIEW_PREFERENCE.value
    # Generative Surfaces v2 (PRD-C2, SDR §5). The ToolAccessGate's park/resume
    # ledger pair, emitted behind ``SURFACES_V2``: ``gate.opened`` beside the
    # ``mcp_auth_required`` interrupt (SYSTEM source), ``gate.resolved`` when the
    # decision endpoint records the connect/cancel. Both project to
    # ``RuntimeActivityKind.EVENT`` (canvas gate-card merges, not timeline cards);
    # the client ledger fold + posture chip consume them. Wire values come from
    # the A1 ``LedgerEventType`` vocabulary so the transport enum cannot drift.
    GATE_OPENED = LedgerEventType.GATE_OPENED.value
    GATE_RESOLVED = LedgerEventType.GATE_RESOLVED.value

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

    ``FORWARDED`` is an API-edge variant: it routes the pending approval to a
    second workspace user and never reaches the LangGraph harness. The worker
    skips ``Command(resume=...)`` for the forwarded case.

    ``SUGGEST_EDIT`` is also an API-edge variant: it resolves the current
    pending row with the suggested edits captured and immediately emits a
    fresh ``APPROVAL_REQUESTED`` payload so the originator can accept the
    edited form. The LangGraph harness is **not** resumed; the run remains
    in ``WAITING_FOR_APPROVAL`` until the new request is accepted or rejected.

    ``APPROVE_WITH_EDITS`` (PRD-09) is an approval variant: the reviewer
    approves AND supplies edit deltas (:class:`SurfaceEdits`) in the same
    decision. The server re-derives the final payload = proposal ⊕ edits and
    commits it through the gated commit executor. Unlike ``SUGGEST_EDIT`` it
    does not re-ask — it commits immediately with the edits applied. The wire
    value mirrors the frozen api-types contract (PRD-09a).
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"
    SUGGEST_EDIT = "suggest_edit"
    APPROVE_WITH_EDITS = "approve_with_edits"


class ApprovalStatus(StrEnum):
    """Approval request lifecycle state.

    ``FORWARDED`` is a terminal state for the parent row in a chain; the
    underlying run resumes only when the child row reaches ``APPROVED`` or
    ``REJECTED``.

    ``SUGGEST_EDIT`` is a terminal state for the parent row when an approver
    suggests edits; a fresh pending row is created carrying the edited
    payload, and the LangGraph harness resumes only when that new row
    reaches ``APPROVED`` / ``REJECTED``.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"
    SUGGEST_EDIT = "suggest_edit"


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
    """Tri-state reversibility marker; ``NOT_APPLICABLE`` for read-only actions."""

    YES = "yes"
    NO = "no"
    NOT_APPLICABLE = "n/a"
