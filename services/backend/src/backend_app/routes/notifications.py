"""``GET / PUT /internal/v1/me/notifications`` (PR B4 / 8.0.3e).

Per-user typed notification preferences + quiet hours. Replaces the
JSONB blob in ``user_preferences.preferences.notifications`` (PR 4.1
placeholder) with two indexed tables — see
``services/backend/migrations/0024_notification_preferences.sql``.

Hydration semantics: when a ``(event_kind, channel)`` cell is absent
the route surfaces the deployment default so the FE always sees a
complete matrix. PUT semantics are partial — the FE may send a single
cell change and the rest of the matrix stays intact.

Quiet hours: optional sub-object on the same response. ``approval_requested``
is the one event type the dispatcher MUST honor regardless of quiet
hours (critical-by-default); the route here only stores the toggle —
the dispatcher implements the carve-out at send time.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore
from backend_app.notifications.store import (
    NotificationChannel,
    NotificationEventKind,
    NotificationPrefsStore,
    NotificationPreferenceRow,
    NotificationQuietHoursRow,
)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class NotificationPreferenceEntry(BaseModel):
    """Single ``(event_kind, channel) → enabled`` row on the wire."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_kind: str
    channel: str
    enabled: bool

    @field_validator("event_kind")
    @classmethod
    def _validate_event_kind(cls, value: str) -> str:
        try:
            NotificationEventKind(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: str) -> str:
        try:
            NotificationChannel(value)
        except ValueError as exc:
            raise ValueError("invalid_request") from exc
        return value


class NotificationQuietHoursPayload(BaseModel):
    """Wire shape for the quiet-hours sub-object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    from_local: str = Field(min_length=5, max_length=5)
    to_local: str = Field(min_length=5, max_length=5)
    tz: str = Field(min_length=1, max_length=64)

    @field_validator("from_local", "to_local")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        if (
            len(value) != 5
            or value[2] != ":"
            or not value[:2].isdigit()
            or not value[3:].isdigit()
        ):
            raise ValueError("invalid_time_format")
        hour = int(value[:2])
        minute = int(value[3:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("invalid_time_format")
        return value


class NotificationPreferencesResponse(BaseModel):
    """Shape returned by ``GET``. ``preferences`` is the hydrated
    full matrix (one entry per ``(event_kind, channel)`` pair the
    dispatcher knows about) so the FE can render every toggle without
    a second round-trip."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    preferences: tuple[NotificationPreferenceEntry, ...]
    quiet_hours: NotificationQuietHoursPayload


class UpdateNotificationPreferencesRequest(BaseModel):
    """Body shape for ``PUT``. Both fields are optional — the FE may
    send only the cells that changed (partial preferences) or only the
    quiet-hours sub-object."""

    model_config = ConfigDict(extra="forbid")

    preferences: tuple[NotificationPreferenceEntry, ...] | None = None
    quiet_hours: NotificationQuietHoursPayload | None = None

    @field_validator("preferences")
    @classmethod
    def _validate_unique(
        cls, value: tuple[NotificationPreferenceEntry, ...] | None
    ) -> tuple[NotificationPreferenceEntry, ...] | None:
        if value is None:
            return None
        seen: set[tuple[str, str]] = set()
        for entry in value:
            key = (entry.event_kind, entry.channel)
            if key in seen:
                raise ValueError("duplicate_cell")
            seen.add(key)
        return value


# ---------------------------------------------------------------------------
# Deployment defaults — the matrix the FE sees on a fresh user
# ---------------------------------------------------------------------------


def deployment_default_matrix() -> tuple[NotificationPreferenceEntry, ...]:
    """Single source of truth for "what does a fresh user see".

    Conservative-by-default: in-app on for everything except the
    weekly digest + product updates; email on for the high-signal
    events (long-task done, approvals, mentions, connector errors);
    push off for everything (the user opts in once the device is
    enrolled).
    """

    matrix: list[NotificationPreferenceEntry] = []
    in_app_defaults = {
        NotificationEventKind.LONG_TASK_FINISHED: True,
        NotificationEventKind.APPROVAL_REQUESTED: True,
        NotificationEventKind.MENTION: True,
        NotificationEventKind.CONNECTOR_ERROR: True,
        NotificationEventKind.WEEKLY_DIGEST: False,
        NotificationEventKind.PRODUCT_UPDATES: False,
    }
    email_defaults = {
        NotificationEventKind.LONG_TASK_FINISHED: False,
        NotificationEventKind.APPROVAL_REQUESTED: True,
        NotificationEventKind.MENTION: True,
        NotificationEventKind.CONNECTOR_ERROR: True,
        NotificationEventKind.WEEKLY_DIGEST: True,
        NotificationEventKind.PRODUCT_UPDATES: False,
    }
    for event in NotificationEventKind:
        matrix.append(
            NotificationPreferenceEntry(
                event_kind=event.value,
                channel=NotificationChannel.IN_APP.value,
                enabled=in_app_defaults[event],
            )
        )
        matrix.append(
            NotificationPreferenceEntry(
                event_kind=event.value,
                channel=NotificationChannel.EMAIL.value,
                enabled=email_defaults[event],
            )
        )
        matrix.append(
            NotificationPreferenceEntry(
                event_kind=event.value,
                channel=NotificationChannel.PUSH.value,
                enabled=False,
            )
        )
    return tuple(matrix)


def deployment_default_quiet_hours() -> NotificationQuietHoursPayload:
    return NotificationQuietHoursPayload(
        enabled=False,
        from_local="20:00",
        to_local="08:00",
        tz="UTC",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_notification_preferences_routes(
    app: FastAPI,
    *,
    notification_prefs_store: NotificationPrefsStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``/internal/v1/me/notifications`` GET + PUT to the app."""

    @app.get(
        "/internal/v1/me/notifications",
        response_model=NotificationPreferencesResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_my_notifications(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> NotificationPreferencesResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        rows = notification_prefs_store.list_preferences(user_id=identity.user_id)
        quiet = notification_prefs_store.get_quiet_hours(user_id=identity.user_id)
        return NotificationPreferencesResponse(
            user_id=identity.user_id,
            preferences=_hydrate_preferences(rows),
            quiet_hours=_quiet_hours_response(quiet),
        )

    @app.put(
        "/internal/v1/me/notifications",
        response_model=NotificationPreferencesResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_my_notifications(
        request: Request,
        payload: UpdateNotificationPreferencesRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> NotificationPreferencesResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if payload.preferences is None and payload.quiet_hours is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_request")
        diff_paths: list[str] = []
        with notification_prefs_store.transaction() as conn:
            if payload.preferences is not None:
                rows = tuple(
                    NotificationPreferenceRow(
                        user_id=identity.user_id,
                        event_kind=NotificationEventKind(entry.event_kind),
                        channel=NotificationChannel(entry.channel),
                        enabled=entry.enabled,
                    )
                    for entry in payload.preferences
                )
                notification_prefs_store.replace_preferences(
                    user_id=identity.user_id,
                    rows=rows,
                    conn=conn,
                )
                for entry in payload.preferences:
                    diff_paths.append(f"preferences.{entry.event_kind}.{entry.channel}")
            if payload.quiet_hours is not None:
                notification_prefs_store.upsert_quiet_hours(
                    NotificationQuietHoursRow(
                        user_id=identity.user_id,
                        enabled=payload.quiet_hours.enabled,
                        from_local=payload.quiet_hours.from_local,
                        to_local=payload.quiet_hours.to_local,
                        tz=payload.quiet_hours.tz,
                    ),
                    conn=conn,
                )
                diff_paths.append("quiet_hours")
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="user.notifications.update",
                    metadata={"diff_paths": sorted(diff_paths)},
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )
        rows = notification_prefs_store.list_preferences(user_id=identity.user_id)
        quiet = notification_prefs_store.get_quiet_hours(user_id=identity.user_id)
        return NotificationPreferencesResponse(
            user_id=identity.user_id,
            preferences=_hydrate_preferences(rows),
            quiet_hours=_quiet_hours_response(quiet),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate_preferences(
    rows: tuple[NotificationPreferenceRow, ...],
) -> tuple[NotificationPreferenceEntry, ...]:
    """Materialise deployment defaults under stored cells so the FE
    always sees the full matrix."""

    by_cell: dict[tuple[str, str], bool] = {
        (row.event_kind.value, row.channel.value): row.enabled for row in rows
    }
    out: list[NotificationPreferenceEntry] = []
    for default in deployment_default_matrix():
        enabled = by_cell.get((default.event_kind, default.channel), default.enabled)
        out.append(
            NotificationPreferenceEntry(
                event_kind=default.event_kind,
                channel=default.channel,
                enabled=enabled,
            )
        )
    return tuple(out)


def _quiet_hours_response(
    row: NotificationQuietHoursRow | None,
) -> NotificationQuietHoursPayload:
    if row is None:
        return deployment_default_quiet_hours()
    return NotificationQuietHoursPayload(
        enabled=row.enabled,
        from_local=row.from_local,
        to_local=row.to_local,
        tz=row.tz,
    )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


__all__ = [
    "NotificationPreferenceEntry",
    "NotificationPreferencesResponse",
    "NotificationQuietHoursPayload",
    "UpdateNotificationPreferencesRequest",
    "deployment_default_matrix",
    "deployment_default_quiet_hours",
    "register_notification_preferences_routes",
]
