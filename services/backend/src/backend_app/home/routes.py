"""``GET /v1/home`` — Phase 9 Home destination aggregator.

Tenant-first, owner-only. Identity is the verified caller; ``org_id`` /
``user_id`` headers from a service-token caller (the facade) bind the
read scope.

Wire shape is locked in ``packages/api-types/src/home.ts`` (Phase 9
``HomePayload``). Sections wrap in ``SectionResult`` so the FE renders
partial outages gracefully; flat sections (greeting / triage /
quick_actions) never fail (they're derived from session identity,
counts, and server config).

Stores are looked up off ``app.state`` lazily so this route module
does not pin a registration order in ``app.create_app``. The
inbox / todos / projects stores are wired earlier in ``create_app``;
when one is absent (test scaffold), the matching section falls back to
``unavailable`` instead of crashing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.home.last_visit import read_and_advance_last_visit
from backend_app.home.service import (
    compose_greeting,
    compose_in_flight_projects,
    compose_live_activity,
    compose_quick_actions,
    compose_today_timeline,
    compose_triage_counts,
    compose_whats_new,
    default_runs_reader,
    tenant_today_bounds,
)
from backend_app.home.sse import HomeActivityBus
from backend_app.identity.me_store import MeStore
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# ---------------------------------------------------------------------------
# Response models — Pydantic mirrors of packages/api-types/src/home.ts.
# ``data`` is intentionally typed as ``Any`` on the SectionResult mirror
# because each section carries a different payload shape; the TS file is
# the source of truth for per-section item shapes.
# ---------------------------------------------------------------------------


class HomeGreetingModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    display_name: str | None
    time_segment: str
    tenant_local_date: str
    tenant_local_iso: str


class TriageCountsModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    approvals_waiting: int
    runs_failed_24h: int
    todos_overdue: int
    todos_due_today: int


class SectionResultModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    data: Any | None = None
    error: str | None = None
    retry_after_ms: int | None = None


class WhatsNewSectionModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: str
    since_iso: str
    data: Any | None = None
    error: str | None = None
    retry_after_ms: int | None = None


class HomePayloadModel(BaseModel):
    """Mirror of ``HomePayload`` from packages/api-types/src/home.ts."""

    model_config = ConfigDict(frozen=True)
    greeting: HomeGreetingModel
    triage: TriageCountsModel
    today_timeline: SectionResultModel
    whats_new: WhatsNewSectionModel
    in_flight_projects: SectionResultModel
    live_activity: SectionResultModel
    quick_actions: tuple[dict[str, Any], ...]
    cached_at: str
    is_first_run: bool


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_home_routes(
    app: FastAPI,
    *,
    me_store: MeStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``GET /v1/home`` to a backend FastAPI app.

    ``me_store`` and ``identity_store`` are required (greeting +
    last-visit cutoff cannot fall back). The inbox / todos / projects
    stores are looked up off ``app.state`` at request time — they are
    wired earlier in ``create_app`` but this route never imports them
    by reference so the registration order is flexible.
    """

    @app.get(
        "/v1/home",
        response_model=HomePayloadModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_home(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> HomePayloadModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        user = identity_store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        if user is None:
            # 404 keeps consistent with the rest of the me/* routes; a
            # session pointing at a deleted user (post-deprovisioning
            # race) sends the FE to login.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")

        now = datetime.now(timezone.utc)

        # Tenant timezone lives on the user's profile (no tenant-wide
        # column yet). Absent → UTC, which is safe (the FE shows the
        # ISO clock either way; tenant_local_* fields just become UTC).
        tenant_tz = _tenant_timezone(
            me_store=me_store, org_id=identity.org_id, user_id=identity.user_id
        )
        today_start, today_end = tenant_today_bounds(now=now, tenant_timezone=tenant_tz)

        # Visit cutoff — read previous, advance atomically, return as
        # ``since_iso`` for the WhatsNewDigest. First-time visit returns
        # ``now - 24h`` so a brand-new account shows the last 24h.
        since_iso = read_and_advance_last_visit(
            me_store=me_store,
            org_id=identity.org_id,
            user_id=identity.user_id,
            now=now,
        )

        # ---- Greeting (real) ----
        greeting = compose_greeting(now=now, user=user, tenant_timezone=tenant_tz)

        # ---- Stores from app.state (lazy) ----
        todos_store = getattr(request.app.state, "todos_store", None)
        inbox_store = getattr(request.app.state, "inbox_store", None)
        projects_store = getattr(request.app.state, "projects_store", None)
        runs_reader = default_runs_reader()

        # ---- Triage (flat — never fails, advisory zeros on store error) ----
        if todos_store is not None and inbox_store is not None:
            triage = compose_triage_counts(
                org_id=identity.org_id,
                user_id=identity.user_id,
                now=now,
                todos_store=todos_store,
                inbox_store=inbox_store,
                runs_reader=runs_reader,
                tenant_today_start=today_start,
                tenant_today_end=today_end,
            )
        else:
            triage = {
                "approvals_waiting": 0,
                "runs_failed_24h": 0,
                "todos_overdue": 0,
                "todos_due_today": 0,
            }

        # ---- Today's timeline (SectionResult) ----
        if todos_store is not None:
            today_timeline = compose_today_timeline(
                org_id=identity.org_id,
                user_id=identity.user_id,
                now=now,
                todos_store=todos_store,
                runs_reader=runs_reader,
                tenant_today_start=today_start,
                tenant_today_end=today_end,
            )
        else:
            today_timeline = {
                "status": "unavailable",
                "data": (),
                "error": "todos_unavailable",
            }

        # ---- In-flight projects (SectionResult) ----
        if (
            projects_store is not None
            and todos_store is not None
            and inbox_store is not None
        ):
            in_flight = compose_in_flight_projects(
                org_id=identity.org_id,
                user_id=identity.user_id,
                now=now,
                projects_store=projects_store,
                todos_store=todos_store,
                inbox_store=inbox_store,
            )
        else:
            in_flight = {
                "status": "unavailable",
                "data": (),
                "error": "projects_unavailable",
            }

        # ---- Live activity (replayed from the SSE bus buffer) ----
        bus = getattr(request.app.state, "home_activity_bus", None) or (
            HomeActivityBus.get_default()
        )
        buffered = tuple(
            envelope.row
            for envelope in bus.list_after(
                org_id=identity.org_id,
                user_id=identity.user_id,
                after_sequence=0,
            )
            if envelope.row is not None
        )
        live_activity = compose_live_activity(buffered_rows=buffered)

        # ---- WhatsNew digest (rows since_iso; cap 7) ----
        whats_new_rows = _whats_new_rows_from_buffer(
            bus=bus,
            org_id=identity.org_id,
            user_id=identity.user_id,
            since_iso=since_iso,
        )
        whats_new = compose_whats_new(since_iso=since_iso, rows=whats_new_rows)

        # ---- Quick actions ----
        quick_actions = compose_quick_actions(roles=identity.roles)

        # ---- First-run detection ----
        is_first_run = _is_first_run(
            triage=triage,
            today_timeline=today_timeline,
            in_flight=in_flight,
            whats_new=whats_new,
            live_activity=live_activity,
        )

        return HomePayloadModel(
            greeting=HomeGreetingModel(**greeting),
            triage=TriageCountsModel(**triage),
            today_timeline=SectionResultModel(**today_timeline),
            in_flight_projects=SectionResultModel(**in_flight),
            live_activity=SectionResultModel(**live_activity),
            whats_new=WhatsNewSectionModel(**whats_new),
            quick_actions=tuple(quick_actions),
            cached_at=now.isoformat(),
            is_first_run=is_first_run,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_timezone(*, me_store: MeStore, org_id: str, user_id: str) -> str | None:
    """Best-effort tenant timezone resolution.

    Per home-prd §5.1 the greeting reads ``tenants.timezone``; that
    column doesn't exist yet. As a placeholder we read the caller's
    profile timezone (the next-best signal) so the wire fields render
    something meaningful. Returns ``None`` (UTC) when nothing usable.
    """

    try:
        record = me_store.get_profile(org_id=org_id, user_id=user_id)
    except Exception:  # noqa: BLE001 — advisory; fall back to UTC
        return None
    if record is None:
        return None
    tz = getattr(record, "timezone", None)
    return tz if isinstance(tz, str) and tz.strip() else None


def _whats_new_rows_from_buffer(
    *,
    bus: HomeActivityBus,
    org_id: str,
    user_id: str,
    since_iso: str,
) -> tuple[dict[str, Any], ...]:
    """Pull buffered SSE rows since the visit cutoff.

    The SSE bus retains the last 256 events per channel; filtering by
    ``created_at >= since_iso`` is sufficient for the morning briefing.
    Production deployments back this with the agent-activity store
    (home-prd §5.3) — the substitution is one constructor swap.
    """

    cutoff = _parse_iso(since_iso)
    rows: list[dict[str, Any]] = []
    for envelope in bus.list_after(org_id=org_id, user_id=user_id, after_sequence=0):
        if envelope.row is None:
            continue
        if cutoff is not None and envelope.created_at < cutoff:
            continue
        rows.append(envelope.row)
    # Newest-first so the FE prepends to the digest naturally; cap 7
    # is applied in compose_whats_new.
    rows.reverse()
    return tuple(rows)


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_first_run(
    *,
    triage: dict[str, int],
    today_timeline: dict[str, Any],
    in_flight: dict[str, Any],
    whats_new: dict[str, Any],
    live_activity: dict[str, Any],
) -> bool:
    """True iff every aggregated section is empty AND no triage counts.

    Drives the FE empty-state onboarding card. We deliberately consider
    ``unavailable`` sections as "empty" — a brand-new tenant has no
    todos/projects/inbox to query, so an absent store presents the same
    way to the user as a successful empty store.
    """

    if any(int(v) > 0 for v in triage.values()):
        return False
    for section in (today_timeline, in_flight, whats_new, live_activity):
        data = section.get("data")
        if data is not None and len(data) > 0:
            return False
    return True


__all__ = [
    "HomeGreetingModel",
    "HomePayloadModel",
    "SectionResultModel",
    "TriageCountsModel",
    "WhatsNewSectionModel",
    "register_home_routes",
]
