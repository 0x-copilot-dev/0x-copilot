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


# PR 4.3 — read-only effective-TTL view for the Privacy & data panel.
# Re-uses the same ``RetentionPolicyResolver`` the sweeper uses, so the
# UI never displays a number different from what gets applied.


class RetentionEffectivePolicyEntry(RuntimeContract):
    """Per-kind effective TTL with provenance.

    ``source_scope`` is the scope of the policy that won the resolver's
    specificity walk; ``None`` means the value came from the
    deployment default (``DEPLOYMENT_DEFAULT_TTL_SECONDS``) — which the
    UI renders as "deployment_default" so admins see they haven't set
    a per-tenant policy yet.

    ``source_policy_id`` is populated when ``source_scope`` is non-None
    so a forensic reader can chase the displayed number back to a
    single ``retention_policies`` row.
    """

    kind: RetentionKind
    ttl_seconds: int | None
    source_scope: RetentionScope | None
    source_policy_id: str | None = None


class RetentionEffectiveResponse(RuntimeContract):
    """Response for ``GET /v1/retention/effective``.

    The map is keyed by ``RetentionKind`` so the FE can render a
    deterministic table without string-matching on ``kind``.
    """

    effective: dict[RetentionKind, RetentionEffectivePolicyEntry]
