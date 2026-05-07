"""Approval decision API schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys, Messages
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    ApprovalCategory,
    ApprovalDecision,
    ApprovalReasonCode,
    ApprovalReversible,
    ApprovalStatus,
)


class _Fields:
    DECIDED_BY_USER_ID = "decided_by_user_id"
    ANSWER = "answer"
    FORWARD_TO = "forward_to"
    USER_ID = "user_id"


class ApprovalForwardTarget(RuntimeContract):
    """Two-stage approval forwarding target (PR 1.4).

    The chain v1 only addresses workspace users; ``external_email`` is
    deferred to PR 6 alongside the share schema (which already gives us
    a token vault, recipient table, and ACL story).
    """

    kind: Literal["workspace_user"] = "workspace_user"
    user_id: str

    @field_validator(_Fields.USER_ID)
    @classmethod
    def _normalize_user_id(cls, value: object) -> str:
        return ValueNormalizer.normalize_id(value, _Fields.USER_ID)


class ApprovalDecisionRequest(RuntimeContract):
    """Request to resolve a pending side-effect approval."""

    decision: ApprovalDecision
    decided_by_user_id: str
    reason: str | None = None
    answer: str | None = None
    # PR 1.4 — required when ``decision == FORWARDED``; rejected otherwise.
    forward_to: ApprovalForwardTarget | None = None

    @field_validator(_Fields.DECIDED_BY_USER_ID)
    @classmethod
    def _normalize_decided_by_user_id(cls, value: object) -> str:
        return ValueNormalizer.normalize_id(value, _Fields.DECIDED_BY_USER_ID)

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, Keys.Field.REASON)

    @field_validator(_Fields.ANSWER, mode="before")
    @classmethod
    def _normalize_answer(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, _Fields.ANSWER)

    @model_validator(mode="after")
    def _validate_forward_to(self) -> "ApprovalDecisionRequest":
        is_forward = self.decision is ApprovalDecision.FORWARDED
        has_target = self.forward_to is not None
        if is_forward and not has_target:
            raise ValueError(Messages.Error.APPROVAL_FORWARD_INVALID_TARGET)
        if not is_forward and has_target:
            # Forward target makes no sense with approve / reject; reject
            # explicitly rather than silently ignoring.
            raise ValueError("forward_to is only allowed when decision == 'forwarded'.")
        if is_forward and self.forward_to is not None:
            if self.forward_to.user_id == self.decided_by_user_id:
                raise ValueError(Messages.Error.APPROVAL_FORWARD_SELF)
        return self


class ApprovalDecisionRecord(RuntimeContract):
    """Persisted approval decision.

    PR 1.4 — ``forwarded_to_user_id`` is set only when ``status ==
    FORWARDED``; the worker discriminates on ``status`` to decide whether
    to resume the LangGraph harness.
    """

    approval_id: str
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus
    decided_by_user_id: str
    reason: str | None = None
    answer: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    forwarded_to_user_id: str | None = None


class ApprovalRequestRecord(RuntimeContract):
    """Persisted pending approval request created by a runtime worker."""

    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    # PR 1.4 — populated on the *child* row when it is created via a forward
    # decision. Also populated on the parent row at read time so a single
    # row carries the full chain link without a join.
    chain_parent_approval_id: str | None = None
    forwarded_to_user_id: str | None = None
    forwarded_at: datetime | None = None
    forwarded_decided_at: datetime | None = None
    # PR 1.4.1 Gap #7 — chain depth, set on insert (parent.chain_depth + 1).
    # Reading the column makes the depth guard O(1); the column's CHECK
    # constraint mirrors APPROVAL_FORWARD_MAX_CHAIN_DEPTH.
    chain_depth: int = 0


class ApprovalDecisionResponse(RuntimeContract):
    """Approval decision result returned to clients."""

    approval_id: str
    run_id: str
    status: ApprovalStatus
    decided_at: datetime
    # PR 1.4 — present only when the response is for a forward decision; the
    # FE uses this to render "Waiting on @marcus" without an extra fetch.
    forwarded_to_user_id: str | None = None
    child_approval_id: str | None = None
    # PR 4.4.6.4 — non-null only when status==APPROVED AND the original
    # request was tagged reversible=YES. Computed by the service layer
    # at decision time; persisted via the existing decision metadata.
    undo_expires_at: datetime | None = None


# PR 4.4.6.4 — reversibility window. Server constant, not configurable
# per vendor; widening it would lower the consent bar without lowering
# the cost (the audit chain has the same shape regardless of seconds).
UNDO_WINDOW_SECONDS: int = 60


class ApprovalUndoResponse(RuntimeContract):
    """Result of a successful (or idempotent) undo request.

    The server is authoritative on ``undo_expires_at`` — even though the
    FE has the same value already, returning it lets the client trust
    the response without consulting its own clock.
    """

    approval_id: str
    run_id: str
    undo_requested_at: datetime
    undo_expires_at: datetime


class AssignedApproval(RuntimeContract):
    """One row in the recipient inbox (PR 1.4.1).

    Returned by ``GET /v1/agent/approvals?assigned_to_me=true``. Carries
    enough chain context for the FE to render "Forwarded by Sarah ·
    10:41 — Post draft to #launch-aurora" + a deep link back into the
    source conversation, without a second fetch.
    """

    approval_id: str
    conversation_id: str
    run_id: str
    approval_kind: str
    status: ApprovalStatus
    chain_parent_approval_id: str | None = None
    forwarded_by_user_id: str | None = None
    forwarded_at: datetime | None = None
    action_summary: str
    risk_class: str | None = None
    expires_at: datetime | None = None
    created_at: datetime


class AssignedApprovalsResponse(RuntimeContract):
    """Paginated inbox listing returned to the recipient (PR 1.4.1)."""

    approvals: tuple[AssignedApproval, ...] = Field(default_factory=tuple)
    next_cursor: str | None = None


# PR 4.4.6.2 — structured consent-card payload for ``approval_kind ==
# "mcp_tool"``. ``McpApprovalMetadata`` round-trips through the existing
# ``ApprovalRequestRecord.metadata`` JsonObject — no schema migration.
# Validated on emit (worker) and on read (API layer) so the FE never
# sees malformed payloads. Forward-compatible: ``extra="allow"`` keeps
# unrelated keys (e.g. existing flat fields) intact through round-trip.

_PARAM_LABEL = Annotated[str, StringConstraints(min_length=1, max_length=24)]
_PARAM_VALUE = Annotated[str, StringConstraints(min_length=1, max_length=128)]
_PARAM_HINT = Annotated[str, StringConstraints(max_length=80)]
_VENDOR_TOKEN = Annotated[str, StringConstraints(min_length=1, max_length=32)]

# Cap stops a malicious model from packing the consent card with 50 rows.
APPROVAL_MAX_PARAMS = 6


class ApprovalParam(RuntimeContract):
    """One row in the consent-card params frame."""

    label: _PARAM_LABEL
    value: _PARAM_VALUE
    hint: _PARAM_HINT | None = None


class McpApprovalMetadata(RuntimeContract):
    """Structured payload nested inside ``ApprovalRequestRecord.metadata``."""

    model_config = ConfigDict(extra="allow")

    vendor: _VENDOR_TOKEN
    category: ApprovalCategory
    reason_code: ApprovalReasonCode
    reversible: ApprovalReversible = ApprovalReversible.NOT_APPLICABLE
    params: tuple[ApprovalParam, ...] = ()

    @field_validator("params")
    @classmethod
    def _max_six(cls, value: tuple[ApprovalParam, ...]) -> tuple[ApprovalParam, ...]:
        if len(value) > APPROVAL_MAX_PARAMS:
            raise ValueError(f"approval params capped at {APPROVAL_MAX_PARAMS} rows")
        return value
