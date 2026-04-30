"""Approval decision API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys
from runtime_api.schemas.common import (
    ApprovalDecision,
    ApprovalStatus,
    RuntimeApiValueNormalizer,
)


class ApprovalDecisionRequest(RuntimeContract):
    """Request to resolve a pending side-effect approval."""

    decision: ApprovalDecision
    decided_by_user_id: str
    reason: str | None = None

    @field_validator("decided_by_user_id")
    @classmethod
    def _normalize_decided_by_user_id(cls, value: object) -> str:
        return RuntimeApiValueNormalizer.normalize_id(value, "decided_by_user_id")

    @field_validator(Keys.Field.REASON, mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str | None:
        return RuntimeApiValueNormalizer.normalize_optional_text(
            value, Keys.Field.REASON
        )


class ApprovalDecisionRecord(RuntimeContract):
    """Persisted approval decision."""

    approval_id: str
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus
    decided_by_user_id: str
    reason: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRequestRecord(RuntimeContract):
    """Persisted pending approval request created by a runtime worker."""

    approval_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    conversation_id: str
    org_id: str
    user_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ApprovalDecisionResponse(RuntimeContract):
    """Approval decision result returned to clients."""

    approval_id: str
    run_id: str
    status: ApprovalStatus
    decided_at: datetime
