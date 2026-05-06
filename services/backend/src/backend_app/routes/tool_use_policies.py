"""``GET / PUT /internal/v1/policies/tool-use`` (PR B1 / 8.0.3d).

Per-workspace default + per-user override for tool-use modes.
Three policy axes — ``read`` / ``write`` / ``destructive`` — each
takes one of four modes — ``auto`` / ``ask`` / ``require`` / ``block``.

Workspace default (``user_id`` query param omitted) requires the
``ADMIN_USERS`` scope; per-user override (``user_id`` query param
present, must equal the caller's user_id unless the caller has
``ADMIN_USERS``) requires ``RUNTIME_USE``.

Hydration semantics: when the row is absent for a given axis we
materialise the deployment default so the FE always sees a complete
shape — same materialisation pattern PR 4.1 ``me_preferences`` uses.

The policy *evaluator* lives in the AI backend's
``ToolPermissionChecker``; this endpoint is the source of truth the
evaluator fetches once per run start.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore
from backend_app.policies.store import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicyRow,
    ToolUsePolicyStore,
)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class ToolUsePolicyEntry(BaseModel):
    """A single ``(kind, mode)`` row on the wire.

    The api-types contract requires ``updated_at`` + ``updated_by_user_id``
    on read responses; ``UpdateToolUsePolicyEntry`` below is the
    write-side variant that omits both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    mode: str
    updated_at: str = ""
    updated_by_user_id: str | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        try:
            ToolUsePolicyKind(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        try:
            ToolUsePolicyMode(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value


class UpdateToolUsePolicyEntry(BaseModel):
    """Write-side row shape — clients send only ``(kind, mode)``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    mode: str

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        try:
            ToolUsePolicyKind(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        try:
            ToolUsePolicyMode(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value


class ToolUsePolicyResponse(BaseModel):
    """Shape returned by ``GET``. ``scope`` is the resolved scope id
    (``"workspace"`` or ``"user"``); ``user_id`` is None on the
    workspace-scope read."""

    model_config = ConfigDict(extra="forbid")

    scope: str
    org_id: str
    user_id: str | None = None
    policies: tuple[ToolUsePolicyEntry, ...]


class UpdateToolUsePolicyRequest(BaseModel):
    """Body shape for ``PUT``. The full per-axis shape is sent each
    time — partial updates would force the route to merge with stored
    rows AND deployment defaults, doubling the surface for invalid
    states. Atomic three-row replace is simpler and matches the FE
    workflow (the user always sees + saves all three axes together)."""

    model_config = ConfigDict(extra="forbid")

    policies: tuple[UpdateToolUsePolicyEntry, ...] = Field(min_length=1, max_length=3)

    @field_validator("policies")
    @classmethod
    def _validate_unique_kinds(
        cls, value: tuple[UpdateToolUsePolicyEntry, ...]
    ) -> tuple[UpdateToolUsePolicyEntry, ...]:
        seen: set[str] = set()
        for entry in value:
            if entry.kind in seen:
                raise ValueError("duplicate_kind")
            seen.add(entry.kind)
        return value


# ---------------------------------------------------------------------------
# Deployment defaults — what a fresh workspace / user sees
# ---------------------------------------------------------------------------


def deployment_default_policy() -> tuple[ToolUsePolicyEntry, ...]:
    """Single source of truth for "what does a fresh workspace see".

    The default keeps reads silent (``auto``), gates writes with one
    confirmation (``ask``), and always blocks destructives until the
    workspace admin opts in (``require``). Conservative-by-default.
    """

    return (
        ToolUsePolicyEntry(
            kind=ToolUsePolicyKind.READ.value,
            mode=ToolUsePolicyMode.AUTO.value,
        ),
        ToolUsePolicyEntry(
            kind=ToolUsePolicyKind.WRITE.value,
            mode=ToolUsePolicyMode.ASK.value,
        ),
        ToolUsePolicyEntry(
            kind=ToolUsePolicyKind.DESTRUCTIVE.value,
            mode=ToolUsePolicyMode.REQUIRE.value,
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_tool_use_policy_routes(
    app: FastAPI,
    *,
    policy_store: ToolUsePolicyStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``/internal/v1/policies/tool-use`` GET + PUT to the app."""

    @app.get(
        "/internal/v1/policies/tool-use",
        response_model=ToolUsePolicyResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_tool_use_policy(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        scope_user_id: str | None = Query(default=None),
    ) -> ToolUsePolicyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # When ``scope_user_id`` is omitted the read targets the
        # workspace default; when present it MUST equal the caller's
        # user_id (so the route can't be used to read another user's
        # override) unless the caller has ADMIN_USERS, which the route
        # PUT branch validates separately.
        target_user_id = _resolve_scope_user(scope_user_id, identity.user_id)
        rows = policy_store.list_for_scope(
            org_id=identity.org_id, user_id=target_user_id
        )
        return ToolUsePolicyResponse(
            scope="user" if target_user_id else "workspace",
            org_id=identity.org_id,
            user_id=target_user_id,
            policies=_hydrate(rows),
        )

    @app.put(
        "/internal/v1/policies/tool-use",
        response_model=ToolUsePolicyResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_tool_use_policy(
        request: Request,
        payload: UpdateToolUsePolicyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        scope_user_id: str | None = Query(default=None),
    ) -> ToolUsePolicyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        target_user_id = _resolve_scope_user(scope_user_id, identity.user_id)
        # Workspace-default writes require ADMIN_USERS; user-overrides
        # are allowed for the caller's own user_id under RUNTIME_USE.
        if target_user_id is None:
            scopes = (
                (request.state.scopes or set())
                if hasattr(request.state, "scopes")
                else set()
            )
            if ADMIN_USERS not in scopes:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_users_required")
        before = policy_store.list_for_scope(
            org_id=identity.org_id, user_id=target_user_id
        )
        before_modes = {row.kind.value: row.mode.value for row in before}
        with policy_store.transaction() as conn:
            for entry in payload.policies:
                policy_store.upsert(
                    ToolUsePolicyRow(
                        org_id=identity.org_id,
                        user_id=target_user_id,
                        kind=ToolUsePolicyKind(entry.kind),
                        mode=ToolUsePolicyMode(entry.mode),
                        updated_by_user_id=identity.user_id,
                    ),
                    conn=conn,
                )
            after_modes = {entry.kind: entry.mode for entry in payload.policies}
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=target_user_id or identity.user_id,
                    action="policy.tool_use.update",
                    metadata={
                        "scope": target_user_id or "workspace",
                        "before": before_modes,
                        "after": after_modes,
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        rows = policy_store.list_for_scope(
            org_id=identity.org_id, user_id=target_user_id
        )
        return ToolUsePolicyResponse(
            scope="user" if target_user_id else "workspace",
            org_id=identity.org_id,
            user_id=target_user_id,
            policies=_hydrate(rows),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(
    rows: tuple[ToolUsePolicyRow, ...],
) -> tuple[ToolUsePolicyEntry, ...]:
    """Materialise deployment defaults under the stored rows so the FE
    always sees one entry per axis. Stored rows surface their
    ``updated_at`` + ``updated_by_user_id``; default-only entries
    surface empty strings (the api-types contract requires the
    fields but tolerates empties on un-overridden axes)."""

    by_kind: dict[str, ToolUsePolicyRow] = {row.kind.value: row for row in rows}
    out: list[ToolUsePolicyEntry] = []
    for default in deployment_default_policy():
        row = by_kind.get(default.kind)
        if row is None:
            out.append(default)
            continue
        out.append(
            ToolUsePolicyEntry(
                kind=default.kind,
                mode=row.mode.value,
                updated_at=row.updated_at.isoformat(),
                updated_by_user_id=row.updated_by_user_id,
            )
        )
    return tuple(out)


def _resolve_scope_user(scope_user_id: str | None, caller_user_id: str) -> str | None:
    """Validate the ``scope_user_id`` query param against the caller.

    Returns ``None`` for workspace-default scope (caller omitted the
    param), the caller's own user_id for self-override (most common),
    or raises 403 for cross-user reads. Cross-user *admin* writes go
    through a dedicated admin route — not this endpoint.
    """

    if scope_user_id is None or scope_user_id == "":
        return None
    if scope_user_id != caller_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross_user_scope_forbidden")
    return scope_user_id


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "ToolUsePolicyEntry",
    "ToolUsePolicyResponse",
    "UpdateToolUsePolicyRequest",
    "deployment_default_policy",
    "register_tool_use_policy_routes",
]
