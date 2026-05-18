"""Section composers for the Phase 9 Home destination.

The Phase 2 seven-section model is dead (pinned_chats / recent_runs /
favorite_tools / todays_focus / upcoming_meetings + activity + greeting).
Phase 9 replaces it with the morning-briefing model defined in
``packages/api-types/src/home.ts``:

* ``greeting``          — IdP given_name → display_name first-token →
                          ``None``; plus tenant_local_date / tenant_local_iso.
* ``triage``            — four small COUNT queries (approvals,
                          failed-runs-24h, todos-overdue, todos-due-today).
* ``today_timeline``    — merged meetings / routine_fires / todo_dues /
                          run_scheduled, sorted by ``when_iso``.
* ``whats_new``         — rows since ``users.home_last_visit_at`` (cap 7).
* ``in_flight_projects``— top-N projects with ``last_activity_at > now-7d``
                          ordered by recency.
* ``live_activity``     — initial backfill for the LiveActivityRail (the
                          SSE stream feeds further rows on top).
* ``quick_actions``     — server-driven defaults (admin-only filter).

Every composer that talks to a store returns a ``SectionResult``-shaped
dict (``{status, data?, error?, retry_after_ms?}``) so the route layer
stitches them into the wire shape without per-section schema work. A
composer that *cannot* fail (greeting / triage / quick_actions) returns
a flat dict instead.

The composers stay free of HTTP / FastAPI imports — they take stores +
plain values and return plain dicts. The route layer wires them.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Stores are referenced by Protocol-shape only — no behavioural import — so
# the composer module never grows a hard dep on a sibling destination
# beyond what the type already exposes.
from backend_app.inbox.store import InboxStore
from backend_app.projects.store import ProjectsStore
from backend_app.todos.store import TodosStore


# ---------------------------------------------------------------------------
# Greeting
# ---------------------------------------------------------------------------


def compose_greeting(
    *,
    now: datetime,
    user: Any,
    tenant_timezone: str | None = None,
) -> dict[str, Any]:
    """Build the Phase 9 greeting payload.

    Adds Phase 9 fields ``tenant_local_date`` + ``tenant_local_iso`` —
    the caller's tenant-local wall clock so the FE renders the greeting
    line + timeline against the same clock regardless of the browser's
    timezone. Falls back to UTC when the tenant timezone is unset or
    invalid; never raises.

    Fallback chain for ``display_name`` (cross-audit §9.5): IdP given_name
    → first-token of display_name → ``None``. We never reach into
    ``primary_email``.
    """

    tz = _resolve_timezone(tenant_timezone)
    local_now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(tz)
    return {
        "display_name": _resolve_greeting_name(user),
        "time_segment": _time_segment(local_now),
        "tenant_local_date": local_now.date().isoformat(),
        "tenant_local_iso": local_now.isoformat(),
    }


def _resolve_greeting_name(user: Any) -> str | None:
    metadata = getattr(user, "metadata", None)
    if isinstance(metadata, dict):
        idp_given = metadata.get("given_name")
        if isinstance(idp_given, str):
            trimmed = idp_given.strip()
            if trimmed:
                return trimmed
    display_name = getattr(user, "display_name", None)
    if isinstance(display_name, str):
        trimmed = display_name.strip()
        if trimmed:
            first_token = trimmed.split()[0]
            if first_token:
                return first_token
    return None


def _time_segment(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _resolve_timezone(name: str | None) -> "ZoneInfo | timezone":
    """Resolve an IANA timezone name; UTC fallback on anything invalid.

    Tenant timezone lives on user_profiles.timezone (per-user) — we
    accept it from the caller because a tenant-wide column doesn't
    exist yet. Invalid / unknown values fall back to UTC; we never let
    a bad tz crash the morning briefing.
    """

    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return timezone.utc


# ---------------------------------------------------------------------------
# Triage counts — four small COUNT queries, never wrapped in SectionResult.
# ---------------------------------------------------------------------------


class _RunsCountReader(Protocol):
    """Hook for the ai-backend runs upstream.

    Phase 9 §3.3 requires querying ``ai-backend.runs`` over HTTP for
    runs_failed_24h. The HTTP integration is out of scope for this PR
    (the runs-list internal route doesn't exist on ai-backend yet) — the
    composer accepts a callable that returns the count; the route binds
    a zero-returning default. Adding the real reader is a single
    constructor swap, no composer change.
    """

    def runs_failed_24h(self, *, org_id: str, user_id: str, now: datetime) -> int: ...

    def scheduled_runs_today(
        self,
        *,
        org_id: str,
        user_id: str,
        tenant_today_start: datetime,
        tenant_today_end: datetime,
    ) -> tuple[dict[str, Any], ...]: ...


def compose_triage_counts(
    *,
    org_id: str,
    user_id: str,
    now: datetime,
    todos_store: TodosStore,
    inbox_store: InboxStore,
    runs_reader: _RunsCountReader,
    tenant_today_start: datetime,
    tenant_today_end: datetime,
) -> dict[str, int]:
    """Build the four triage chips. Best-effort: a store error is logged
    upstream and the value falls back to 0 — these are advisory, not
    load-bearing for the page (Phase 9 §3.3 partial-failure rule).
    """

    return {
        "approvals_waiting": _safe_count(
            lambda: _count_approvals_waiting(
                inbox_store=inbox_store, org_id=org_id, user_id=user_id
            )
        ),
        "runs_failed_24h": _safe_count(
            lambda: runs_reader.runs_failed_24h(org_id=org_id, user_id=user_id, now=now)
        ),
        "todos_overdue": _safe_count(
            lambda: _count_todos_overdue(
                todos_store=todos_store,
                org_id=org_id,
                user_id=user_id,
                now=now,
            )
        ),
        "todos_due_today": _safe_count(
            lambda: _count_todos_due_today(
                todos_store=todos_store,
                org_id=org_id,
                user_id=user_id,
                today_start=tenant_today_start,
                today_end=tenant_today_end,
            )
        ),
    }


def _safe_count(callable_: Any) -> int:
    try:
        value = callable_()
    except Exception:  # noqa: BLE001 — advisory chip; never blanks the page
        return 0
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _count_approvals_waiting(
    *, inbox_store: InboxStore, org_id: str, user_id: str
) -> int:
    items, _ = inbox_store.list_items(
        tenant_id=org_id,
        owner_user_id=user_id,
        kinds=("approval",),
        states=("unread",),
        limit=1_000,
    )
    return len(items)


def _count_todos_overdue(
    *, todos_store: TodosStore, org_id: str, user_id: str, now: datetime
) -> int:
    items, _ = todos_store.list_todos(
        tenant_id=org_id,
        owner_user_id=user_id,
        statuses=("open",),
        limit=1_000,
    )
    now_utc = _as_utc(now)
    return sum(1 for r in items if _is_overdue(r.due, now_utc))


def _count_todos_due_today(
    *,
    todos_store: TodosStore,
    org_id: str,
    user_id: str,
    today_start: datetime,
    today_end: datetime,
) -> int:
    items, _ = todos_store.list_todos(
        tenant_id=org_id,
        owner_user_id=user_id,
        statuses=("open",),
        limit=1_000,
    )
    return sum(1 for r in items if _is_due_within(r.due, today_start, today_end))


# ---------------------------------------------------------------------------
# Timeline — merged today's meetings / routine_fires / todo_dues / runs.
# ---------------------------------------------------------------------------


def compose_today_timeline(
    *,
    org_id: str,
    user_id: str,
    now: datetime,
    todos_store: TodosStore,
    runs_reader: _RunsCountReader,
    tenant_today_start: datetime,
    tenant_today_end: datetime,
) -> dict[str, Any]:
    """Merge todo_dues + scheduled_runs into a single sorted timeline.

    Meetings + routine_fires require upstream tables that aren't owned
    by this service (connector_calendar_events / routine_fires); their
    branches return an empty list rather than ``status: "unavailable"``
    so the timeline still renders the todo + run entries it does have.
    The frontend FE composes per-kind icons + empty-state copy.
    """

    entries: list[dict[str, Any]] = []
    try:
        entries.extend(
            _build_todo_due_entries(
                todos_store=todos_store,
                org_id=org_id,
                user_id=user_id,
                now=now,
                today_start=tenant_today_start,
                today_end=tenant_today_end,
            )
        )
    except Exception:  # noqa: BLE001 — partial-failure rule
        return _section_error("todos_unavailable")

    try:
        scheduled = runs_reader.scheduled_runs_today(
            org_id=org_id,
            user_id=user_id,
            tenant_today_start=tenant_today_start,
            tenant_today_end=tenant_today_end,
        )
        entries.extend(scheduled)
    except Exception:  # noqa: BLE001 — partial-failure rule
        pass  # advisory; timeline still renders

    entries.sort(key=lambda e: e.get("when_iso") or "")
    return {"status": "ok", "data": tuple(entries)}


def _build_todo_due_entries(
    *,
    todos_store: TodosStore,
    org_id: str,
    user_id: str,
    now: datetime,
    today_start: datetime,
    today_end: datetime,
) -> list[dict[str, Any]]:
    """Materialise ``todo_due`` entries — overdue + due-today open todos."""

    items, _ = todos_store.list_todos(
        tenant_id=org_id,
        owner_user_id=user_id,
        statuses=("open",),
        limit=200,
    )
    now_utc = _as_utc(now)
    out: list[dict[str, Any]] = []
    for record in items:
        due_dt = _parse_iso_datetime(record.due)
        if due_dt is None:
            continue
        is_overdue = due_dt < now_utc
        is_due_today = today_start <= due_dt <= today_end
        if not (is_overdue or is_due_today):
            continue
        source_kind = "user"
        source = record.source if isinstance(record.source, dict) else {}
        raw_kind = source.get("kind")
        if raw_kind in {"user", "chat", "agent"}:
            source_kind = raw_kind
        out.append(
            {
                "id": f"todo-due-{record.id}",
                "kind": "todo_due",
                "when_iso": due_dt.isoformat(),
                "title": record.text or "Untitled task",
                "subtitle": "Overdue" if is_overdue else "Due",
                "status": "overdue" if is_overdue else "upcoming",
                "target": {"kind": "todo", "id": record.id},
                "priority": record.priority
                if record.priority in {"low", "med", "high"}
                else "med",
                "is_overdue": is_overdue,
                "source_kind": source_kind,
            }
        )
    return out


# ---------------------------------------------------------------------------
# WhatsNew — agent-activity rows since users.home_last_visit_at.
# ---------------------------------------------------------------------------


def compose_whats_new(
    *,
    since_iso: str,
    rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Build the WhatsNewSection. ``rows`` is provided by the route
    layer (composed from project activity + the SSE bus's buffered
    events); the composer's only job is to slot them into the
    section-shape envelope with ``since_iso`` carried through.

    Cap at 7 rows (home-prd §5.3) so the digest is glanceable.
    """

    return {
        "status": "ok",
        "since_iso": since_iso,
        "data": tuple(rows[:7]),
    }


# ---------------------------------------------------------------------------
# InFlightProjects — top-N recent projects with open_item_count.
# ---------------------------------------------------------------------------


def compose_in_flight_projects(
    *,
    org_id: str,
    user_id: str,
    now: datetime,
    projects_store: ProjectsStore,
    todos_store: TodosStore,
    inbox_store: InboxStore,
    limit: int = 6,
    window_days: int = 7,
) -> dict[str, Any]:
    """List projects with ``last_activity_at > now - window_days``.

    ``open_item_count`` denormalizes open todos + unread inbox items
    scoped to the project (cheap aggregate for the strip; the canonical
    breakdown is fetched on click — home-prd §4.4).
    """

    try:
        projects, _ = projects_store.list_projects(
            tenant_id=org_id,
            member_user_id=user_id,
            statuses=("active",),
            sort="updated_at:desc",
            limit=50,
        )
    except Exception:  # noqa: BLE001 — partial-failure rule
        return _section_error("projects_unavailable")

    cutoff = _as_utc(now) - timedelta(days=window_days)
    candidates = [
        p
        for p in projects
        if p.last_activity_at is not None and _as_utc(p.last_activity_at) >= cutoff
    ]
    candidates.sort(key=lambda p: _as_utc(p.last_activity_at), reverse=True)
    out: list[dict[str, Any]] = []
    for project in candidates[:limit]:
        open_count = _safe_count(
            lambda pid=project.id: _project_open_item_count(
                todos_store=todos_store,
                inbox_store=inbox_store,
                org_id=org_id,
                project_id=pid,
            )
        )
        if open_count <= 0:
            continue
        out.append(
            {
                "ref": {"kind": "project", "id": project.id},
                "name": project.name,
                "icon_emoji": project.icon_emoji or "📁",
                "color_hue": int(project.color_hue)
                if project.color_hue is not None
                else 210,
                "open_item_count": open_count,
                "last_activity_at": _as_utc(project.last_activity_at).isoformat(),
            }
        )
    return {"status": "ok", "data": tuple(out)}


def _project_open_item_count(
    *,
    todos_store: TodosStore,
    inbox_store: InboxStore,
    org_id: str,
    project_id: str,
) -> int:
    todos, _ = todos_store.list_todos(
        tenant_id=org_id,
        project_ids=(project_id,),
        statuses=("open",),
        limit=500,
    )
    inbox_items, _ = inbox_store.list_items(
        tenant_id=org_id,
        project_ids=(project_id,),
        states=("unread",),
        limit=500,
    )
    return len(todos) + len(inbox_items)


# ---------------------------------------------------------------------------
# Quick actions — server-driven defaults with admin filter.
# ---------------------------------------------------------------------------


# Defaults baked into code per home-prd §5.1 — the optional tenant-
# override row (``home_quick_actions_config``) is wave 2 and lives behind
# an audit-chain PATCH (home-prd §6). Defaults are deliberately concise:
# four tiles, all-user safe, plus one admin-only tile.
_QUICK_ACTION_DEFAULTS: tuple[dict[str, Any], ...] = (
    {
        "id": "qa_chat_new",
        "label": "Start a chat",
        "icon_name": "message-square-plus",
        "target": {"kind": "chat_new"},
    },
    {
        "id": "qa_todo_new",
        "label": "Add a todo",
        "icon_name": "list-plus",
        "target": {"kind": "todo_new"},
    },
    {
        "id": "qa_routine_new",
        "label": "Create a routine",
        "icon_name": "clock",
        "target": {"kind": "routine_new"},
    },
    {
        "id": "qa_tools_onboard",
        "label": "Connect a tool",
        "icon_name": "plug",
        "target": {"kind": "tools_onboard"},
    },
    {
        "id": "qa_team_invite",
        "label": "Invite a teammate",
        "icon_name": "user-plus",
        "target": {"kind": "team_invite"},
        "is_admin_only": True,
    },
)


def compose_quick_actions(*, roles: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    """Return the quick-action tiles for the caller.

    Admin-only tiles (``is_admin_only=True``) are filtered out for
    non-admin callers per the wire contract — clients never receive a
    tile they cannot use.
    """

    is_admin = any(r in {"admin", "workspace_admin", "org_admin"} for r in roles)
    out: list[dict[str, Any]] = []
    for tile in _QUICK_ACTION_DEFAULTS:
        if tile.get("is_admin_only") and not is_admin:
            continue
        out.append(dict(tile))
    return tuple(out)


# ---------------------------------------------------------------------------
# Live activity backfill — replayed from the SSE buffer.
# ---------------------------------------------------------------------------


def compose_live_activity(
    *,
    buffered_rows: tuple[dict[str, Any], ...],
    limit: int = 15,
) -> dict[str, Any]:
    """Initial backfill for the LiveActivityRail.

    The route layer reads the buffered envelopes off the SSE bus (per-
    channel ring deque, capped at 256) and passes the most-recent
    ``limit`` rows here. The SSE stream feeds further rows on top — this
    is just the first-paint cache.
    """

    return {"status": "ok", "data": tuple(buffered_rows[-limit:])}


# ---------------------------------------------------------------------------
# Helpers — datetime arithmetic + section error shape.
# ---------------------------------------------------------------------------


def tenant_today_bounds(
    *, now: datetime, tenant_timezone: str | None = None
) -> tuple[datetime, datetime]:
    """Return ``(start_of_today_utc, end_of_today_utc)`` for the caller's
    tenant clock. Boundaries are local midnight → next-midnight, then
    converted back to UTC so the store queries (UTC throughout) line up.
    """

    tz = _resolve_timezone(tenant_timezone)
    now_local = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(tz)
    today_local: date = now_local.date()
    start_local = datetime.combine(today_local, time.min, tzinfo=tz)
    end_local = datetime.combine(today_local, time.max, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse a wire ISO string (``YYYY-MM-DD`` OR full ISO datetime).

    Todos store ``due`` as ISO date-or-datetime; we coerce to a UTC
    datetime so the comparison is uniform. Bare-date inputs anchor at
    end-of-day local (UTC here — tenant-tz aware widening is handled by
    the route via ``tenant_today_bounds``).
    """

    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), time(23, 59, 59))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_overdue(due: str | None, now_utc: datetime) -> bool:
    parsed = _parse_iso_datetime(due)
    return parsed is not None and parsed < now_utc


def _is_due_within(due: str | None, start: datetime, end: datetime) -> bool:
    parsed = _parse_iso_datetime(due)
    return parsed is not None and start <= parsed <= end


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _section_error(code: str) -> dict[str, Any]:
    """Section-shape error envelope. ``error`` is a stable code the FE
    switches on for empty-state copy — never an exception trace (the
    wire contract is human-readable but bounded)."""

    return {"status": "unavailable", "data": (), "error": code}


# ---------------------------------------------------------------------------
# Default runs reader — zero-returning stub until the ai-backend HTTP
# integration lands. The composer accepts a Protocol-shaped reader, so
# the swap is a single constructor argument when the upstream is ready.
# ---------------------------------------------------------------------------


class _ZeroRunsReader:
    """Default reader — returns 0 / empty until the ai-backend HTTP path
    ships. Keeps the composer signature stable across the integration."""

    def runs_failed_24h(self, *, org_id: str, user_id: str, now: datetime) -> int:
        del org_id, user_id, now
        return 0

    def scheduled_runs_today(
        self,
        *,
        org_id: str,
        user_id: str,
        tenant_today_start: datetime,
        tenant_today_end: datetime,
    ) -> tuple[dict[str, Any], ...]:
        del org_id, user_id, tenant_today_start, tenant_today_end
        return ()


def default_runs_reader() -> _RunsCountReader:
    return _ZeroRunsReader()


__all__ = [
    "compose_greeting",
    "compose_in_flight_projects",
    "compose_live_activity",
    "compose_quick_actions",
    "compose_today_timeline",
    "compose_triage_counts",
    "compose_whats_new",
    "default_runs_reader",
    "tenant_today_bounds",
]
