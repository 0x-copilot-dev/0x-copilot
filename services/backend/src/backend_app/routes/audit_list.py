"""Unified backend audit list endpoint (PR 7.1).

Fans out across the four backend audit streams (mcp, skill, identity,
deploy) via :class:`backend_app.audit_reader.AuditReader` and returns a
cursor-paginated page. The facade composes this with the ai-backend's
``runtime_audit_log`` stream to produce the unified ``GET /v1/audit``
surface the Settings → Members → Audit log page consumes.

Distinct from ``audit_query.py`` (which serves only the identity stream)
and from ``audit_export.py`` (the SIEM NDJSON pump, kept untouched).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final, Literal

from enterprise_service_contracts.scopes import ADMIN_AUDIT_EXPORT
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.audit_reader import (
    AuditCursor,
    AuditFilters,
    AuditReader,
    AuditRowView,
)
from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes


_DEFAULT_LIMIT: Final = 50
_MAX_LIMIT: Final = 200


class AuditChainView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    prev_hash: str | None = None
    signature: str | None = None
    key_version: int | None = None


class AuditRowResponse(BaseModel):
    """One row of the unified audit feed."""

    model_config = ConfigDict(extra="forbid")

    stream: Literal[
        "mcp_audit_events",
        "skill_audit_events",
        "identity_audit_events",
        "deploy_audit_events",
    ]
    seq: int | None
    audit_id: str
    org_id: str
    actor_user_id: str | None
    actor_kind: Literal["user", "ci", "system"]
    subject_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "failure", "denied"]
    metadata: dict[str, Any] = Field(default_factory=dict)
    chain: AuditChainView
    created_at: datetime


class AuditListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: tuple[AuditRowResponse, ...] = ()
    next_cursor: str | None = None
    has_more: bool = False
    degraded_streams: tuple[str, ...] = ()


def _to_response(row: AuditRowView) -> AuditRowResponse:
    return AuditRowResponse(
        stream=row.stream,
        seq=row.seq,
        audit_id=row.audit_id,
        org_id=row.org_id,
        actor_user_id=row.actor_user_id,
        actor_kind=row.actor_kind,
        subject_user_id=row.subject_user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        outcome=row.outcome,
        metadata=row.metadata,
        chain=AuditChainView(
            seq=row.seq,
            prev_hash=row.prev_hash_hex,
            signature=row.signature_hex,
            key_version=row.key_version,
        ),
        created_at=row.created_at,
    )


def register_audit_list_routes(app: FastAPI) -> None:
    """Attach ``GET /internal/v1/audit/list`` to a backend FastAPI app."""

    @app.get(
        "/internal/v1/audit/list",
        response_model=AuditListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def list_audit_rows(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
        action: str | None = Query(default=None, min_length=1, max_length=200),
        actor_user_id: str | None = Query(default=None, min_length=1),
        resource_type: str | None = Query(default=None, min_length=1),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
    ) -> AuditListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if since is not None and until is not None and since >= until:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "since must be before until",
            )
        try:
            decoded_cursor = AuditCursor.decode(cursor)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc

        reader = AuditReader(
            mcp_store=getattr(getattr(app.state, "mcp_service", None), "store", None),
            skill_store=getattr(
                getattr(app.state, "skill_service", None), "store", None
            ),
            deploy_store=getattr(
                getattr(app.state, "deploy_audit_service", None), "store", None
            ),
            identity_store=getattr(app.state, "identity_store", None),
        )
        page = reader.list(
            org_id=identity.org_id,
            filters=AuditFilters(
                actor_user_id=actor_user_id,
                action_prefix=action,
                resource_type=resource_type,
                since=_to_utc(since),
                until=_to_utc(until),
            ),
            cursor=decoded_cursor,
            limit=limit,
        )
        return AuditListResponse(
            rows=tuple(_to_response(row) for row in page.rows),
            next_cursor=page.next_cursor,
            has_more=page.has_more,
            degraded_streams=page.degraded_streams,
        )


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "AuditChainView",
    "AuditListResponse",
    "AuditRowResponse",
    "register_audit_list_routes",
]
