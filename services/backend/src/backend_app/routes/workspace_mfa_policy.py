"""``/internal/v1/workspace/mfa-policy`` (PR 8.3).

Admin-only editor for the ``identity_policies`` row's MFA fields:

* ``mfa_required`` — gate the workspace; flips a sign-in into the
  ``mfa:pending`` state until the user enrolls + verifies a factor.
* ``step_up_window_seconds`` — how long an MFA verification holds
  before another challenge is needed for sensitive actions.

The store layer already exists (`IdentityStore.get_identity_policy` /
`upsert_identity_policy`). The route is the thinnest possible wrapper —
one read, one merge-patch write, one audit append.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from copilot_service_contracts.scopes import ADMIN_USERS
from fastapi import Depends, FastAPI, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord, IdentityPolicyRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


class WorkspaceMfaPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mfa_required: bool
    step_up_window_seconds: int
    updated_at: str


class UpdateWorkspaceMfaPolicyRequest(BaseModel):
    """Merge-patch body — omit a field to leave it untouched."""

    model_config = ConfigDict(extra="forbid")

    mfa_required: bool | None = Field(default=None)
    step_up_window_seconds: int | None = Field(default=None, ge=60, le=86400)


def register_workspace_mfa_policy_routes(
    app: FastAPI,
    *,
    identity_store: IdentityStore,
) -> None:
    @app.get(
        "/internal/v1/workspace/mfa-policy",
        response_model=WorkspaceMfaPolicyResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def get_mfa_policy(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WorkspaceMfaPolicyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = identity_store.get_identity_policy(org_id=identity.org_id)
        if record is None:
            # Materialise defaults — same shape as the live row so the FE
            # can edit + PUT without a separate "create" branch.
            return WorkspaceMfaPolicyResponse(
                mfa_required=False,
                step_up_window_seconds=300,
                updated_at="",
            )
        return WorkspaceMfaPolicyResponse(
            mfa_required=record.mfa_required,
            step_up_window_seconds=record.step_up_window_seconds,
            updated_at=_iso(record.updated_at),
        )

    @app.put(
        "/internal/v1/workspace/mfa-policy",
        response_model=WorkspaceMfaPolicyResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS))],
    )
    def put_mfa_policy(
        request: Request,
        payload: UpdateWorkspaceMfaPolicyRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WorkspaceMfaPolicyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        diff = payload.model_dump(exclude_unset=True)
        existing = identity_store.get_identity_policy(org_id=identity.org_id)
        before = (
            {
                "mfa_required": existing.mfa_required,
                "step_up_window_seconds": existing.step_up_window_seconds,
            }
            if existing
            else {"mfa_required": False, "step_up_window_seconds": 300}
        )
        merged = IdentityPolicyRecord(
            org_id=identity.org_id,
            local_password_enabled=(
                existing.local_password_enabled if existing else True
            ),
            mfa_required=diff.get("mfa_required", before["mfa_required"]),
            step_up_window_seconds=diff.get(
                "step_up_window_seconds", before["step_up_window_seconds"]
            ),
        )
        saved: IdentityPolicyRecord
        # The identity store doesn't expose its own transaction primitive
        # alongside the policy + audit; the audit chain remains
        # append-only either way (the worst-case partial-write is a
        # policy update without an audit row, which the SIEM export will
        # surface as an unaudited diff vs. the previous snapshot).
        saved = identity_store.upsert_identity_policy(merged)
        identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                subject_user_id=identity.user_id,
                action="workspace.mfa_policy.update",
                metadata={
                    "before": before,
                    "after": {
                        "mfa_required": saved.mfa_required,
                        "step_up_window_seconds": saved.step_up_window_seconds,
                    },
                    "diff_keys": sorted(diff.keys()),
                },
                request_ip=_request_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        )
        return WorkspaceMfaPolicyResponse(
            mfa_required=saved.mfa_required,
            step_up_window_seconds=saved.step_up_window_seconds,
            updated_at=_iso(saved.updated_at),
        )


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "UpdateWorkspaceMfaPolicyRequest",
    "WorkspaceMfaPolicyResponse",
    "register_workspace_mfa_policy_routes",
]


# Keep typecheck quiet on unused imports when fastapi/pydantic stubs change.
_ = Any
_ = status
