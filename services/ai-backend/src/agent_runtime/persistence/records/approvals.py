"""Persisted approval request records."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.persistence.records.common import (
    ApprovalRiskClass,
    PersistenceApprovalStatus,
    PersistenceValueNormalizer,
)


class PersistenceApprovalRequestRecord(RuntimeContract):
    """Persisted approval request for a side-effecting runtime action."""

    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    tool_invocation_id: str | None = None
    org_id: str
    requested_by_user_id: str
    status: PersistenceApprovalStatus = PersistenceApprovalStatus.PENDING
    risk_class: ApprovalRiskClass = ApprovalRiskClass.MEDIUM
    action_summary: str
    request_payload: JsonObject = Field(default_factory=dict)
    decided_by_user_id: str | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None
    # Two-stage forwarding chain. ``chain_parent_approval_id`` is set on
    # a child row inserted when the original requester forwards the decision
    # to a second approver. The remaining three fields hang off the parent
    # row and record the forward target plus timing for audit.
    chain_parent_approval_id: str | None = None
    forwarded_to_user_id: str | None = None
    forwarded_at: datetime | None = None
    forwarded_decided_at: datetime | None = None

    @field_validator("request_payload", mode="before")
    @classmethod
    def _redact_request_payload(cls, value: object) -> JsonObject:
        return PersistenceValueNormalizer.coerce_json_object(value)
