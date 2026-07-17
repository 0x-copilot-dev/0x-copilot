"""``GET / PUT /internal/v1/policies/privacy`` (PR B2 / 8.0.3f).

Per-workspace + per-user privacy & data settings. Five toggles + one
knob:

* ``training_opt_out`` (bool, default True)
* ``region`` (us-east-1 / eu-west-1 / ap-northeast-1, default None)
* ``retention_days`` (positive int or None, default None)
* ``share_metadata`` (bool, default True)
* ``memory_enabled`` (bool, default True)

Workspace default (``scope_user_id`` query param omitted) requires the
``ADMIN_USERS`` scope; per-user override (``scope_user_id`` present and
equal to caller) requires ``RUNTIME_USE``.

The user-override row WINS for that user when present; the AI backend's
retention sweeper + memory consumer + provider integration each read
this once at run start (out of scope for this PR — the storage and the
admin/user surface land here).
"""

from __future__ import annotations

from copilot_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, field_validator

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore
from backend_app.privacy.store import (
    DataResidencyRegion,
    PrivacySettingsRow,
    PrivacySettingsStore,
)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class PrivacySettingsResponse(BaseModel):
    """Hydrated full shape returned by ``GET``. ``user_id`` is None on
    a workspace-scope read, populated on a user-scope read."""

    model_config = ConfigDict(extra="forbid")

    scope: str  # "workspace" | "user"
    org_id: str
    user_id: str | None = None
    training_opt_out: bool
    region: str | None = None
    retention_days: int | None = None
    share_metadata: bool
    memory_enabled: bool
    updated_at: str


class UpdatePrivacySettingsRequest(BaseModel):
    """Body shape for ``PUT``. All fields optional — partial replace.

    A field omitted from the body leaves the stored value alone (or
    falls through to the deployment default if no row exists yet).
    Setting ``region`` or ``retention_days`` to ``null`` explicitly
    clears them; the route discriminates ``None`` (omitted) from
    explicit-null via Pydantic's ``model_fields_set``.
    """

    model_config = ConfigDict(extra="forbid")

    training_opt_out: bool | None = None
    region: str | None = None
    retention_days: int | None = None
    share_metadata: bool | None = None
    memory_enabled: bool | None = None

    @field_validator("region")
    @classmethod
    def _validate_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            DataResidencyRegion(value)
        except ValueError as exc:
            raise ValueError("invalid_region") from exc
        return value

    @field_validator("retention_days")
    @classmethod
    def _validate_retention_days(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("retention_days_must_be_positive")
        return value


# ---------------------------------------------------------------------------
# Deployment defaults
# ---------------------------------------------------------------------------


def deployment_default_privacy(
    *, org_id: str, user_id: str | None
) -> PrivacySettingsResponse:
    """Single source of truth for "what does a fresh scope see"."""

    return PrivacySettingsResponse(
        scope="user" if user_id else "workspace",
        org_id=org_id,
        user_id=user_id,
        training_opt_out=True,
        region=None,
        retention_days=None,
        share_metadata=True,
        memory_enabled=True,
        updated_at="",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_privacy_settings_routes(
    app: FastAPI,
    *,
    privacy_store: PrivacySettingsStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``/internal/v1/policies/privacy`` GET + PUT to the app."""

    @app.get(
        "/internal/v1/policies/privacy",
        response_model=PrivacySettingsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_privacy_settings(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        scope_user_id: str | None = Query(default=None),
    ) -> PrivacySettingsResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        target_user_id = _resolve_scope_user(scope_user_id, identity.user_id)
        row = privacy_store.get_for_scope(
            org_id=identity.org_id, user_id=target_user_id
        )
        return _to_response(row, org_id=identity.org_id, user_id=target_user_id)

    @app.put(
        "/internal/v1/policies/privacy",
        response_model=PrivacySettingsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_privacy_settings(
        request: Request,
        payload: UpdatePrivacySettingsRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        scope_user_id: str | None = Query(default=None),
    ) -> PrivacySettingsResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        target_user_id = _resolve_scope_user(scope_user_id, identity.user_id)
        # Workspace-default writes require ADMIN_USERS; user-overrides
        # are allowed for the caller's own user_id under RUNTIME_USE.
        if target_user_id is None:
            scopes = (
                request.state.scopes
                if hasattr(request.state, "scopes") and request.state.scopes
                else set()
            )
            if ADMIN_USERS not in scopes:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "admin_users_required")
        existing = privacy_store.get_for_scope(
            org_id=identity.org_id, user_id=target_user_id
        )
        # Only fields the caller actually sent override stored values;
        # an omitted field falls through to the existing row (or the
        # deployment default when no row is stored yet).
        sent = payload.model_dump(exclude_unset=True)
        defaults = deployment_default_privacy(
            org_id=identity.org_id, user_id=target_user_id
        )
        merged = PrivacySettingsRow(
            org_id=identity.org_id,
            user_id=target_user_id,
            training_opt_out=sent.get(
                "training_opt_out",
                existing.training_opt_out if existing else defaults.training_opt_out,
            ),
            region=_coerce_region(
                sent["region"]
                if "region" in sent
                else (existing.region.value if existing and existing.region else None)
            ),
            retention_days=(
                sent["retention_days"]
                if "retention_days" in sent
                else (existing.retention_days if existing else None)
            ),
            share_metadata=sent.get(
                "share_metadata",
                existing.share_metadata if existing else defaults.share_metadata,
            ),
            memory_enabled=sent.get(
                "memory_enabled",
                existing.memory_enabled if existing else defaults.memory_enabled,
            ),
            updated_by_user_id=identity.user_id,
        )
        with privacy_store.transaction() as conn:
            saved = privacy_store.upsert(merged, conn=conn)
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=target_user_id or identity.user_id,
                    action="policy.privacy.update",
                    metadata={
                        "scope": target_user_id or "workspace",
                        "diff_paths": sorted(sent.keys()),
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        return _to_response(saved, org_id=identity.org_id, user_id=target_user_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(
    row: PrivacySettingsRow | None,
    *,
    org_id: str,
    user_id: str | None,
) -> PrivacySettingsResponse:
    if row is None:
        return deployment_default_privacy(org_id=org_id, user_id=user_id)
    return PrivacySettingsResponse(
        scope="user" if user_id else "workspace",
        org_id=org_id,
        user_id=user_id,
        training_opt_out=row.training_opt_out,
        region=row.region.value if row.region else None,
        retention_days=row.retention_days,
        share_metadata=row.share_metadata,
        memory_enabled=row.memory_enabled,
        updated_at=row.updated_at.isoformat(),
    )


def _coerce_region(value: str | None) -> DataResidencyRegion | None:
    if value is None:
        return None
    return DataResidencyRegion(value)


def _resolve_scope_user(scope_user_id: str | None, caller_user_id: str) -> str | None:
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
    "PrivacySettingsResponse",
    "UpdatePrivacySettingsRequest",
    "deployment_default_privacy",
    "register_privacy_settings_routes",
]
