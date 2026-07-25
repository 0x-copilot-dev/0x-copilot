"""Application contracts for the legal-hold control plane.

These records deliberately live with the application service rather than the
HTTP transport.  The same closed target contract is used by every caller, and
the runtime API only binds it to JSON/HTTP.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, PositiveInt, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.records import LegalHoldReasonCode, LegalHoldScope


class LegalHoldCreateRequest(RuntimeContract):
    """Create one hold using a closed ownership target, never a generic ref."""

    scope: LegalHoldScope
    reason_code: LegalHoldReasonCode
    target_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    target_conversation_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def _target_matches_scope(self) -> "LegalHoldCreateRequest":
        if self.scope is LegalHoldScope.ORG:
            if (
                self.target_user_id is not None
                or self.target_conversation_id is not None
            ):
                raise ValueError("org holds do not accept a target")
        elif self.scope is LegalHoldScope.USER:
            if self.target_user_id is None or self.target_conversation_id is not None:
                raise ValueError("user hold requires only target_user_id")
        elif self.target_conversation_id is None or self.target_user_id is not None:
            raise ValueError("conversation hold requires only target_conversation_id")
        return self


class LegalHoldReleaseRequest(RuntimeContract):
    """Optimistic-concurrency release request for a named hold."""

    expected_revision: PositiveInt


class LegalHoldView(RuntimeContract):
    """Safe public projection; no raw internal ``resource_id`` is exposed."""

    id: str
    scope: LegalHoldScope
    target_user_id: str | None = None
    target_conversation_id: str | None = None
    reason_code: LegalHoldReasonCode
    created_by_user_id: str
    created_at: datetime
    released_by_user_id: str | None = None
    released_at: datetime | None = None
    revision: PositiveInt
    replayed: bool = False


class LegalHoldListResponse(RuntimeContract):
    """Bounded tenant-scoped legal-hold list."""

    holds: tuple[LegalHoldView, ...] = ()


__all__ = (
    "LegalHoldCreateRequest",
    "LegalHoldListResponse",
    "LegalHoldReleaseRequest",
    "LegalHoldView",
)
