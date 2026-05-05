"""Public request/response schemas for /v1/retention/* (C8 admin CRUD)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionScope,
)


class RetentionPolicyUpsertRequest(RuntimeContract):
    """Body for ``POST /v1/retention/policies``.

    Idempotent — keyed by ``(org_id, scope, COALESCE(resource_id, ''), kind)``
    server-side, so resubmitting the same shape updates the existing row.
    ``resource_id`` is required for ``user`` / ``conversation`` / ``assistant``
    scopes and must be ``None`` for ``org``.
    """

    scope: RetentionScope
    kind: RetentionKind
    ttl_seconds: PositiveInt
    resource_id: str | None = Field(default=None, min_length=1)


class RetentionPolicyView(RuntimeContract):
    """Read shape for one retention policy."""

    id: str
    org_id: str
    scope: RetentionScope
    resource_id: str | None
    kind: RetentionKind
    ttl_seconds: int
    created_by_user_id: str | None
    created_at: datetime
    updated_at: datetime


class RetentionPolicyListResponse(RuntimeContract):
    """Response for ``GET /v1/retention/policies``."""

    policies: tuple[RetentionPolicyView, ...] = ()
