"""Typed, tenant-scoped legal-hold persistence records.

The database has carried ``runtime_legal_holds`` since the initial runtime
schema, but it deliberately was not exposed as a generic resource API.  These
records preserve that constraint: callers select one of the closed ownership
scopes and the application resolves the canonical backing identifiers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, PositiveInt, model_validator

from agent_runtime.execution.contracts import RuntimeContract


class LegalHoldScope(StrEnum):
    """The only resource families that retention can safely cover today."""

    ORG = "org"
    USER = "user"
    CONVERSATION = "conversation"


class LegalHoldReasonCode(StrEnum):
    """Closed, non-sensitive rationale vocabulary persisted with a hold."""

    LEGAL_REQUEST = "legal_request"
    INVESTIGATION = "investigation"
    COMPLIANCE = "compliance"
    SECURITY = "security"
    LEGACY = "legacy"


class LegalHoldConflict(RuntimeError):
    """Idempotency or optimistic-concurrency conflict at the persistence boundary."""


class LegalHoldRecord(RuntimeContract):
    """One durable legal hold; release is a revisioned state transition."""

    id: str = Field(default_factory=lambda: f"lh_{uuid4().hex}")
    org_id: str = Field(min_length=1, max_length=200)
    scope: LegalHoldScope
    # Internal canonical key.  It is never accepted as a generic HTTP field.
    resource_id: str = Field(min_length=1, max_length=200)
    # The resolved owner for user/conversation holds. User holds are always
    # normalized by migration 0011; legacy conversation rows may lack this
    # denormalization after their source conversation was already deleted,
    # but remain safely enforceable by their canonical conversation id.
    subject_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason_code: LegalHoldReasonCode
    created_by_user_id: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    released_by_user_id: str | None = Field(default=None, min_length=1, max_length=200)
    released_at: datetime | None = None
    revision: PositiveInt = 1
    create_idempotency_key: str = Field(min_length=8, max_length=255)
    create_request_digest: str = Field(min_length=64, max_length=64)
    release_idempotency_key: str | None = Field(
        default=None, min_length=8, max_length=255
    )
    release_request_digest: str | None = Field(
        default=None, min_length=64, max_length=64
    )

    @model_validator(mode="after")
    def _scope_and_release_shape_are_canonical(self) -> "LegalHoldRecord":
        if self.scope is LegalHoldScope.ORG:
            if self.resource_id != self.org_id or self.subject_user_id is not None:
                raise ValueError("org hold must target its owning organization")
        elif self.scope is LegalHoldScope.USER:
            if self.resource_id != self.subject_user_id:
                raise ValueError("user hold must target its resolved user")
        # A conversation hold's resource_id is authoritative even when a
        # pre-D11 row cannot recover its former user's id. New application
        # writes always resolve and set subject_user_id before persistence.
        if (self.released_at is None) != (self.released_by_user_id is None):
            raise ValueError("release actor and timestamp must be set together")
        if self.released_at is None and (
            self.release_idempotency_key is not None
            or self.release_request_digest is not None
        ):
            raise ValueError("active hold cannot carry release idempotency data")
        if self.released_at is not None and (
            self.release_idempotency_key is None or self.release_request_digest is None
        ):
            raise ValueError("released hold must retain its release idempotency data")
        return self


class LegalHoldMutationResult(RuntimeContract):
    """Adapter result distinguishing a committed transition from an exact retry."""

    hold: LegalHoldRecord
    replayed: bool = False


__all__ = (
    "LegalHoldConflict",
    "LegalHoldMutationResult",
    "LegalHoldReasonCode",
    "LegalHoldRecord",
    "LegalHoldScope",
)
