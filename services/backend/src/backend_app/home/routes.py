"""``GET /v1/home`` — Home destination aggregator (Phase 2).

Tenant-first, owner-only. Identity is the verified caller; ``org_id`` /
``user_id`` headers from a service-token caller (the facade) bind the
read scope.

Sections wrap in ``SectionResult`` so the FE renders partial outages
gracefully. The greeting composer runs real logic; every other section
is a stub today (see ``home.service`` TODOs).

Reads the per-user KV preference ``home.activity_window_hours`` (default
24) from the existing ``user_preferences`` JSONB blob — no new column.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.home.service import (
    compose_activity_stub,
    compose_favorite_tools_stub,
    compose_greeting,
    compose_pinned_chats_stub,
    compose_recent_runs_stub,
    compose_todays_focus_stub,
    compose_upcoming_meetings_stub,
)
from backend_app.identity.me_store import MeStore
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# Default activity window (in hours) when the user has not customised
# the ``home.activity_window_hours`` preference. Matches the Home PRD
# §4 default ("last 24 hours of activity").
_DEFAULT_ACTIVITY_WINDOW_HOURS = 24


class HomeGreetingModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    display_name: str | None
    time_segment: str


class SectionResultModel(BaseModel):
    """Mirror of ``SectionResult<T>`` from packages/api-types.

    ``data`` is intentionally typed as ``Any`` because each section
    carries a different payload shape; the wire contract (api-types)
    is the source of truth for the per-section item shapes.
    """

    model_config = ConfigDict(frozen=True)
    status: str
    data: Any | None = None
    error: str | None = None
    retry_after_ms: int | None = None


class HomeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    greeting: HomeGreetingModel
    activity: SectionResultModel
    pinned_chats: SectionResultModel
    recent_runs: SectionResultModel
    favorite_tools: SectionResultModel
    todays_focus: SectionResultModel
    upcoming_meetings: SectionResultModel


def register_home_routes(
    app: FastAPI,
    *,
    me_store: MeStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``GET /v1/home`` to a backend FastAPI app."""

    @app.get(
        "/v1/home",
        response_model=HomeResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_home(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> HomeResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Tenant-first: every read below is scoped to ``identity.org_id``
        # / ``identity.user_id`` (the verified session, not the
        # caller-supplied query). The verified identity is the only
        # legitimate read target — Home is strictly owner-only.
        user = identity_store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        if user is None:
            # 404 keeps consistent with the rest of the me/* routes
            # when the session points at a deleted user (race after
            # deprovisioning); the FE redirects to login.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")

        # KV read — ``home.activity_window_hours`` lives in the
        # user_preferences JSONB blob; absent → default 24 hours.
        # Reading it now (even though every consumer is a stub today)
        # so the wire path is exercised by the integration test before
        # the activity composer comes online.
        activity_window_hours = _read_activity_window_hours(
            me_store=me_store, org_id=identity.org_id, user_id=identity.user_id
        )
        # ``activity_window_hours`` will be consumed by the activity
        # composer in a follow-up; binding now so the value flows.
        del activity_window_hours  # noqa: F841 — placeholder until wired

        now = datetime.now(timezone.utc)

        return HomeResponse(
            greeting=HomeGreetingModel(**compose_greeting(now=now, user=user)),
            activity=SectionResultModel(**compose_activity_stub()),
            pinned_chats=SectionResultModel(**compose_pinned_chats_stub()),
            recent_runs=SectionResultModel(**compose_recent_runs_stub()),
            favorite_tools=SectionResultModel(**compose_favorite_tools_stub()),
            todays_focus=SectionResultModel(**compose_todays_focus_stub()),
            upcoming_meetings=SectionResultModel(**compose_upcoming_meetings_stub()),
        )


def _read_activity_window_hours(
    *,
    me_store: MeStore,
    org_id: str,
    user_id: str,
) -> int:
    """Read ``home.activity_window_hours`` from the JSONB prefs blob.

    Layout: ``user_preferences.preferences['home']['activity_window_hours']``.
    Absent / wrong-type / out-of-range → default ``24``. Strict range
    [1, 168] (1 hour to 1 week) prevents an arbitrary-int from
    silently breaking downstream queries.
    """

    record = me_store.get_preferences(org_id=org_id, user_id=user_id)
    if record is None:
        return _DEFAULT_ACTIVITY_WINDOW_HOURS
    blob = record.preferences
    if not isinstance(blob, dict):
        return _DEFAULT_ACTIVITY_WINDOW_HOURS
    home_kv = blob.get("home")
    if not isinstance(home_kv, dict):
        return _DEFAULT_ACTIVITY_WINDOW_HOURS
    raw = home_kv.get("activity_window_hours")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return _DEFAULT_ACTIVITY_WINDOW_HOURS
    if raw < 1 or raw > 168:
        return _DEFAULT_ACTIVITY_WINDOW_HOURS
    return raw


__all__ = [
    "HomeGreetingModel",
    "HomeResponse",
    "SectionResultModel",
    "register_home_routes",
]
