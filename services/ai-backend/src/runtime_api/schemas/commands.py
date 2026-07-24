"""Durable runtime command schemas produced by the API."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, PositiveInt, model_validator

from agent_runtime.capabilities.surfaces.commit import SurfaceEdits
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    JsonObject,
    RuntimeContract,
)
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)
from runtime_api.schemas.common import ApprovalDecision


# P13 step 1 — every command carries a W3C trace-propagation carrier
# (``traceparent`` / ``tracestate``) so the worker can continue the
# API's trace tree across the queue boundary. The dict is populated by
# ``QueueTracePropagator.inject`` on enqueue and consumed by
# ``QueueTracePropagator.extract`` on claim. An empty dict (the default)
# means "no propagation": the worker starts a fresh trace, which is the
# same behavior the system had before P13.
class RuntimeRunCommand(RuntimeContract):
    """Durable command enqueued after run creation; carries trace-propagation headers for the worker."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    trace_id: str
    runtime_context: AgentRuntimeContext
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeCancelCommand(RuntimeContract):
    """Durable command requesting best-effort run cancellation."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    requested_by_user_id: str
    reason: str | None = None
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeStageCommitCommand(RuntimeContract):
    """Durable command a staged-write approve enqueues (PRD-D2).

    The worker-side ``RuntimeStageCommitHandler`` is its ONLY consumer: it
    re-validates the approval against the folded ledger, claims an idempotency
    row BEFORE any side effect, re-checks preconditions, dispatches EXACTLY the
    approved revision through the real MCP client, and emits ``write.applied``.
    The command is emitted only when a NEW ``decision.recorded{approve}`` event
    was actually recorded — idempotent re-approves and reject/restore never
    enqueue, so at most one commit attempt exists per approve.
    """

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    stage_id: str
    run_id: str
    org_id: str
    user_id: str
    conversation_id: str
    # The rev pinned by the approving decision; the commit dispatches exactly it.
    rev: PositiveInt
    # ``sequence_no`` of the ``decision.recorded{approve}`` event — the handler's
    # approval gate refuses unless the folded approving decision matches this.
    decision_seq: int
    # PRD-D3 — the approved row set for a bulk row-set apply, or ``None`` for a
    # single-artifact (D1) commit. The worker gate re-checks that this equals the
    # apply decision's scope exactly; held rows are never present here.
    row_keys: tuple[str, ...] | None = None
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeArtifactEventCommand(RuntimeContract):
    """Durable publication of one canonical artifact ledger event.

    The artifact metadata adapter writes this command to the existing runtime
    outbox in the same transaction as the artifact mutation. The worker
    appends it to the existing run event store with ``event_id`` as the stable
    idempotency key; no second event transport exists.
    """

    command_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^artevt_[0-9a-f]{32,64}$",
    )
    event_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^artevt_[0-9a-f]{32,64}$",
    )
    org_id: str
    user_id: str
    run_id: str
    conversation_id: str
    trace_id: str
    event_type: LedgerEventType
    payload: JsonObject
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _validate_publication(self) -> "RuntimeArtifactEventCommand":
        if self.command_id != self.event_id:
            raise ValueError("artifact event command_id must equal event_id")
        if self.event_type not in {
            LedgerEventType.ARTIFACT_CREATED,
            LedgerEventType.ARTIFACT_REVISED,
            LedgerEventType.ARTIFACT_PROMOTED,
        }:
            raise ValueError("artifact event command accepts artifact events only")
        WorkLedgerVocabulary.validate_payload(self.event_type, self.payload)
        return self


class RuntimeApprovalResolvedCommand(RuntimeContract):
    """Durable command notifying workers that an approval was resolved."""

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    approval_id: str
    run_id: str
    org_id: str
    decision: ApprovalDecision
    answer: str | None = None
    # PRD-09 — reviewer edit deltas, populated only for ``approve_with_edits``.
    # The worker/commit executor re-derives the final payload = proposal ⊕ edits
    # server-side; the client never sends a merged artifact.
    edits: SurfaceEdits | None = None
    # Populated by the API service from the request, or by the expiry sweeper
    # as ``Values.SYSTEM_USER_ID`` for system-driven rejections (timeout /
    # membership cascade). The audit emitter promotes ``actor_type=system``
    # for sentinel values.
    decided_by_user_id: str | None = None
    # Short reason code for audit metadata; lets operational dashboards
    # distinguish "expired" from "recipient_membership_revoked" without
    # parsing free-text fields.
    reason: str | None = None
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
