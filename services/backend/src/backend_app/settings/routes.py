"""Public ``/v1/settings/*`` routes — Phase 12 Settings module.

Six endpoints across three namespaces (sub-PRD §4.4):

  user notifications:
    GET   /v1/settings/notifications
    PATCH /v1/settings/notifications

  workspace notifications (admin):
    GET   /v1/settings/workspace/notifications
    PATCH /v1/settings/workspace/notifications

  workspace webhook security (admin):
    GET   /v1/settings/security/webhooks
    PATCH /v1/settings/security/webhooks

The shapes mirror ``packages/api-types/src/settings.ts`` exactly.
Validation is strict (``extra='forbid'``) on the partial-update bodies
so a typo doesn't silently persist.

Identity comes from ``BackendServiceAuthenticator.scoped_identity`` —
roles + permission scopes ride the trusted facade-headers envelope and
flow into ``CallerIdentity`` so the service layer can ACL on either
``admin:users`` scope or the coarse-grained admin role.

HMAC algorithm + header constants stay canonical in
``backend_app.webhooks.signer``; this module never redefines them.
The ``security.webhooks`` payload here only toggles behavior.
"""

from __future__ import annotations

from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator, ScopedIdentity
from backend_app.identity.rbac import RequireScopes
from backend_app.settings.service import (
    CallerIdentity,
    SettingsAccessDenied,
    SettingsInvalidNamespace,
    SettingsService,
)
from backend_app.settings.store import NamespaceRecord


# ---------------------------------------------------------------------------
# Wire shapes (mirror packages/api-types/src/settings.ts)
# ---------------------------------------------------------------------------


class NotificationQuietHoursBlob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    from_local: str = Field(..., min_length=4, max_length=5)
    to_local: str = Field(..., min_length=4, max_length=5)
    tz: str = Field(..., min_length=1, max_length=64)


class _UserNotificationDefaultsResponse(BaseModel):
    """Wire mirror of ``NotificationDefaults`` (sub-PRD §4.4)."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    destinations_enabled: dict[str, bool]
    quiet_hours: NotificationQuietHoursBlob
    updated_at: str


class _WorkspaceNotificationDefaultsResponse(BaseModel):
    """Wire mirror of ``WorkspaceNotificationDefaults``."""

    model_config = ConfigDict(extra="forbid")

    destinations_enabled: dict[str, bool]
    quiet_hours: NotificationQuietHoursBlob
    updated_at: str
    updated_by_user_id: str | None = None


class _WebhookSecurityDefaultsResponse(BaseModel):
    """Wire mirror of ``WebhookSecurityDefaults``."""

    model_config = ConfigDict(extra="forbid")

    default_hmac_on: bool
    require_ip_allowlist: bool
    max_secret_age_days: int = Field(..., ge=0)
    updated_at: str
    updated_by_user_id: str | None = None


class _UpdateNotificationDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destinations_enabled: dict[str, bool] | None = None
    quiet_hours: NotificationQuietHoursBlob | None = None


class _UpdateWorkspaceNotificationDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destinations_enabled: dict[str, bool] | None = None
    quiet_hours: NotificationQuietHoursBlob | None = None


class _UpdateWebhookSecurityDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_hmac_on: bool | None = None
    require_ip_allowlist: bool | None = None
    max_secret_age_days: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Defaults — the materialised shape returned when no row exists yet.
# Single source of truth for "what does a fresh tenant / user see".
# ---------------------------------------------------------------------------


def _default_notification_quiet_hours() -> dict[str, Any]:
    return {
        "enabled": False,
        "from_local": "20:00",
        "to_local": "08:00",
        "tz": "UTC",
    }


def _default_user_notifications(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "destinations_enabled": {},
        "quiet_hours": _default_notification_quiet_hours(),
        "updated_at": "",
    }


def _default_workspace_notifications() -> dict[str, Any]:
    return {
        "destinations_enabled": {},
        "quiet_hours": _default_notification_quiet_hours(),
        "updated_at": "",
        "updated_by_user_id": None,
    }


def _default_webhook_security() -> dict[str, Any]:
    """Workspace webhook signing defaults.

    HMAC default-on (sub-PRD §U-S3 + Routines §9.7 Q6). Allowlist
    default-off so existing webhooks keep working; admin opts in.
    Max-secret-age zero = never expire; we surface a Settings panel
    warning when admin sets it >0 and a webhook's secret is older.
    """

    return {
        "default_hmac_on": True,
        "require_ip_allowlist": False,
        "max_secret_age_days": 0,
        "updated_at": "",
        "updated_by_user_id": None,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_settings_routes(
    app: FastAPI,
    *,
    service: SettingsService,
) -> None:
    """Attach the six ``/v1/settings/*`` routes to ``app``."""

    # ----- User notifications --------------------------------------------

    @app.get(
        "/v1/settings/notifications",
        response_model=_UserNotificationDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_user_notifications(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _UserNotificationDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        record = _call(
            service.get_user_namespace,
            caller=_caller(identity),
            target_user_id=identity.user_id,
            namespace="notifications",
        )
        merged = _merge_with_defaults(
            record, defaults=_default_user_notifications(identity.user_id)
        )
        # Always include user_id so the wire shape matches even when the
        # row is absent.
        merged["user_id"] = identity.user_id
        return _UserNotificationDefaultsResponse.model_validate(merged)

    @app.patch(
        "/v1/settings/notifications",
        response_model=_UserNotificationDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_user_notifications(
        request: Request,
        body: _UpdateNotificationDefaultsRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _UserNotificationDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=False)
        saved = _call(
            service.patch_user_namespace,
            caller=_caller(identity),
            target_user_id=identity.user_id,
            namespace="notifications",
            patch=patch,
            request_ip=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        merged = _merge_with_defaults(
            saved, defaults=_default_user_notifications(identity.user_id)
        )
        merged["user_id"] = identity.user_id
        return _UserNotificationDefaultsResponse.model_validate(merged)

    # ----- Workspace notifications (admin) -------------------------------

    @app.get(
        "/v1/settings/workspace/notifications",
        response_model=_WorkspaceNotificationDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_workspace_notifications(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _WorkspaceNotificationDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        record = _call(
            service.get_tenant_namespace,
            caller=_caller(identity),
            namespace="notifications",
        )
        merged = _merge_with_defaults(
            record, defaults=_default_workspace_notifications()
        )
        return _WorkspaceNotificationDefaultsResponse.model_validate(merged)

    @app.patch(
        "/v1/settings/workspace/notifications",
        response_model=_WorkspaceNotificationDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_workspace_notifications(
        request: Request,
        body: _UpdateWorkspaceNotificationDefaultsRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _WorkspaceNotificationDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=False)
        saved = _call(
            service.patch_tenant_namespace,
            caller=_caller(identity),
            namespace="notifications",
            patch=patch,
            request_ip=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        merged = _merge_with_defaults(
            saved, defaults=_default_workspace_notifications()
        )
        return _WorkspaceNotificationDefaultsResponse.model_validate(merged)

    # ----- Workspace webhook security (admin) ----------------------------

    @app.get(
        "/v1/settings/security/webhooks",
        response_model=_WebhookSecurityDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_security_webhooks(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _WebhookSecurityDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        record = _call(
            service.get_tenant_namespace,
            caller=_caller(identity),
            namespace="security.webhooks",
        )
        merged = _merge_with_defaults(record, defaults=_default_webhook_security())
        return _WebhookSecurityDefaultsResponse.model_validate(merged)

    @app.patch(
        "/v1/settings/security/webhooks",
        response_model=_WebhookSecurityDefaultsResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def patch_security_webhooks(
        request: Request,
        body: _UpdateWebhookSecurityDefaultsRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> _WebhookSecurityDefaultsResponse:
        identity = _identity(request, org_id=org_id, user_id=user_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=False)
        saved = _call(
            service.patch_tenant_namespace,
            caller=_caller(identity),
            namespace="security.webhooks",
            patch=patch,
            request_ip=_request_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        merged = _merge_with_defaults(saved, defaults=_default_webhook_security())
        return _WebhookSecurityDefaultsResponse.model_validate(merged)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(request: Request, *, org_id: str, user_id: str) -> ScopedIdentity:
    return BackendServiceAuthenticator.scoped_identity(
        request, org_id=org_id, user_id=user_id
    )


def _caller(identity: ScopedIdentity) -> CallerIdentity:
    return CallerIdentity(
        org_id=identity.org_id,
        user_id=identity.user_id,
        roles=identity.roles,
        permission_scopes=identity.permission_scopes,
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _call(func: Any, **kwargs: Any) -> Any:
    """Run the service call, projecting domain errors to HTTP."""

    try:
        return func(**kwargs)
    except SettingsAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc) or "forbidden") from exc
    except SettingsInvalidNamespace as exc:  # pragma: no cover - guarded earlier
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"unknown_namespace: {exc}"
        ) from exc


def _merge_with_defaults(
    record: NamespaceRecord | None,
    *,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Apply persisted values over the defaults; project audit metadata.

    Returns a dict shaped for the wire response. ``updated_at`` is the
    record's timestamp ISO-formatted (empty string when absent so the
    Pydantic model still validates).
    """

    out: dict[str, Any] = dict(defaults)
    if record is None:
        return out
    for key, value in record.settings.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged: dict[str, Any] = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    out["updated_at"] = record.updated_at.isoformat()
    if "updated_by_user_id" in out:
        out["updated_by_user_id"] = record.updated_by_user_id
    return out


__all__ = ["register_settings_routes"]
