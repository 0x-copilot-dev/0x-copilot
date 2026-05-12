"""Internal paginated audit-list endpoint for the ``runtime_audit_log`` chain.

Read-only. Service-token auth plus ``ADMIN_AUDIT_EXPORT`` scope required.
The facade composes this with backend's own audit chains to produce
the unified ``GET /v1/audit`` surface.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Final, Literal

from enterprise_service_contracts.scopes import ADMIN_AUDIT_EXPORT
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.rbac import RequireScopes


_DEFAULT_LIMIT: Final = 50
_MAX_LIMIT: Final = 200


class _AuditChainView(BaseModel):
    """Chain-integrity fields projected from one audit-log row."""

    model_config = ConfigDict(extra="forbid")

    seq: int | None = None
    prev_hash: str | None = None
    signature: str | None = None
    key_version: int | None = None


class _AuditRowResponse(BaseModel):
    """Wire shape for a single ``runtime_audit_log`` row."""

    model_config = ConfigDict(extra="forbid")

    stream: Literal["runtime_audit_log"] = "runtime_audit_log"
    seq: int | None
    audit_id: str
    org_id: str
    actor_user_id: str | None
    actor_kind: Literal["user", "runtime", "worker", "system"]
    subject_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "failure", "denied"]
    metadata: dict[str, Any] = Field(default_factory=dict)
    chain: _AuditChainView
    created_at: datetime


class _AuditListResponse(BaseModel):
    """Paginated audit-log listing with cursor-based navigation."""

    model_config = ConfigDict(extra="forbid")

    rows: tuple[_AuditRowResponse, ...] = ()
    next_cursor: str | None = None
    has_more: bool = False


def _decode_cursor(raw: str | None) -> int:
    """Decode the cursor's ``seq`` field. Returns 0 when absent/empty."""

    if raw is None or raw.strip() == "":
        return 0
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc
    seq_value = payload.get("seq", 0)
    try:
        return int(seq_value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc


def _encode_cursor(seq: int) -> str:
    """Encode a sequence number as a URL-safe base64 cursor token."""

    raw = json.dumps({"seq": seq}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _row_to_response(record: dict[str, Any]) -> _AuditRowResponse:
    """Project a raw persistence dict into the audit-row wire shape."""
    actor_type = str(record.get("actor_type") or "user")
    if actor_type not in {"user", "runtime", "worker", "system"}:
        actor_type = "user"
    raw_outcome = str(record.get("outcome") or "success")
    outcome: Literal["success", "failure", "denied"] = (
        raw_outcome  # type: ignore[assignment]
        if raw_outcome in {"success", "failure", "denied"}
        else "success"
    )
    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    seq_value = record.get("seq")
    seq = int(seq_value) if seq_value is not None else None
    return _AuditRowResponse(
        seq=seq,
        audit_id=str(record.get("audit_id") or record.get("id") or ""),
        org_id=str(record.get("org_id") or ""),
        actor_user_id=(
            str(record["user_id"]) if record.get("user_id") is not None else None
        ),
        actor_kind=actor_type,  # type: ignore[arg-type]
        subject_user_id=None,
        action=str(record.get("action") or ""),
        resource_type=str(record.get("resource_type") or ""),
        resource_id=str(record.get("resource_id") or ""),
        outcome=outcome,
        metadata=metadata,
        chain=_AuditChainView(
            seq=seq,
            prev_hash=record.get("prev_hash"),
            signature=record.get("signature"),
            key_version=(
                int(record["key_version"])
                if record.get("key_version") is not None
                else None
            ),
        ),
        created_at=_coerce_dt(record.get("created_at")),
    )


def _coerce_dt(value: Any) -> datetime:
    """Coerce an arbitrary datetime-like value to a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return datetime.now(timezone.utc)


def _to_utc(value: datetime | None) -> datetime | None:
    """Normalise an optional datetime to UTC, returning None when absent."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def register_audit_list_routes(router: APIRouter) -> None:
    """Attach ``GET /audit/list`` to the ``/internal/v1`` ai-backend router.

    The router is the internal-only one whose prefix is already
    ``/internal/v1`` (see ``InternalRuntimeApiRouter``); we attach a
    bare ``/audit/list`` here so the full path resolves to
    ``/internal/v1/audit/list``.
    """

    @router.get(
        "/audit/list",
        response_model=_AuditListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    async def list_runtime_audit(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
        action: str | None = Query(default=None, min_length=1, max_length=200),
        actor_user_id: str | None = Query(default=None, min_length=1),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
    ) -> _AuditListResponse:
        identity = RuntimeServiceAuthenticator.require_identity(request)
        if identity.org_id != org_id or identity.user_id != user_id:
            # Caller's header-derived identity must match the URL's
            # query-supplied org/user — defence in depth on top of the
            # service-token check.
            raise HTTPException(status.HTTP_403_FORBIDDEN, "identity_mismatch")
        if since is not None and until is not None and since >= until:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "since must be before until",
            )
        after_seq = _decode_cursor(cursor)
        service = request.app.state.runtime_api_service
        persistence = service.persistence
        rows = await persistence.list_audit_log_events(
            org_id=identity.org_id,
            after_seq=after_seq,
            limit=limit,
            action_prefix=action,
            actor_user_id=actor_user_id,
            since=_to_utc(since),
            until=_to_utc(until),
        )
        responses = tuple(_row_to_response(row) for row in rows)
        next_seq = max(
            (row.seq or 0 for row in responses),
            default=after_seq,
        )
        next_cursor = _encode_cursor(next_seq) if len(responses) == limit else None
        return _AuditListResponse(
            rows=responses,
            next_cursor=next_cursor,
            has_more=len(responses) == limit,
        )


__all__ = ["register_audit_list_routes"]
