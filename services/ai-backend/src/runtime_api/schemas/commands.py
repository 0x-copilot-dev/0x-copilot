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
from agent_runtime.execution.run_steering import SteeringMessage
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)
from agent_runtime.rollout import RolloutCapability
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


class RuntimeSteerCommand(RuntimeContract):
    """Durable command delivering one user steer into a run already in flight.

    Deliberately the same shape as :class:`RuntimeCancelCommand`: both are
    out-of-band control over a run whose executing task nothing in the request
    path can reach. The worker claims it above the execution semaphore for the
    same reason a cancel is claimed there — the moment steering matters most is
    the moment every execution slot is busy.

    Carrying the message body is what makes this NOT a cancel: the handler has
    nothing to look up, so a steer can be delivered by any process that happens
    to be executing the run, with no second read to disagree with.
    """

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    requested_by_user_id: str
    steer: SteeringMessage
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _steer_author_matches_requester(self) -> "RuntimeSteerCommand":
        if self.steer.requested_by_user_id != self.requested_by_user_id:
            raise ValueError("steer author must match the command requester")
        return self


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
    # E2: copied from the authoritative ``write.staged.rollout`` mark when a
    # stage entered a governed lane. The worker re-folds the ledger mark and
    # rejects disagreement, so this body-free command is never authority.
    governed_capabilities: tuple[RolloutCapability, ...] | None = Field(
        default=None,
        # Preserve the historical command shape for ungoverned work while
        # retaining the durable mark whenever it is present.
        exclude_if=lambda value: value is None,
    )
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeEffectCommitCommand(RuntimeContract):
    """Durable transport envelope for one digest-pinned A4 effect approval.

    This command intentionally carries references, digests, and the decision ledger
    identifier only.  Proposal bytes, target bodies, credentials, and executor
    arguments are resolved by the A5 worker coordinator after it has revalidated
    the approved stage.  Enqueueing this command therefore cannot execute an
    effect inline.
    """

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    stage_id: str = Field(min_length=1, max_length=128)
    revision: PositiveInt
    decision_ledger_id: str = Field(min_length=1, max_length=128)
    proposal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=256)
    row_keys: tuple[str, ...] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    retry_basis_ledger_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        exclude_if=lambda value: value is None,
    )
    # A durable explicit-E2 mark. It prevents a queued command from becoming a
    # legacy permit if a process restarts with the corresponding lane disabled.
    governed_capabilities: tuple[RolloutCapability, ...] | None = Field(
        default=None,
        # Preserve the historical command shape for ungoverned work while
        # retaining the durable mark whenever it is present.
        exclude_if=lambda value: value is None,
    )
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _row_keys_are_unique(self) -> RuntimeEffectCommitCommand:
        if self.row_keys is not None and (
            not self.row_keys or len(self.row_keys) != len(set(self.row_keys))
        ):
            raise ValueError("row_keys must contain unique row keys")
        return self


class RuntimeEffectReconcileCommand(RuntimeContract):
    """Durable request to reconcile an existing uncertain A5 effect claim.

    The command has no proposal or target body and cannot create a new effect. It
    names only the durable tenant/run/claim scope. The worker must rehydrate the
    stage and principal from the claim and run records before asking an executor
    to reconcile that already-claimed attempt.
    """

    command_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    claim_id: str = Field(min_length=1, max_length=128)
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
            LedgerEventType.ARTIFACT_PRESENTATION_DECIDED,
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
    # ``once`` / ``always`` — how far the approval reaches. Carried verbatim
    # from ``ApprovalDecisionRequest.decision_scope`` (which already validated it
    # is approve-only) and threaded into the LangGraph resume value, where the
    # policy lane that raised the gate turns ``always`` into a run-scoped rule.
    # Typed as a plain string here, not the Literal: this is the durable queue
    # contract, and a command written by an older API must still deserialize.
    # ``DecisionScope.from_wire`` fails closed to ``once`` at the reading end.
    decision_scope: str | None = None
    trace_propagation: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
