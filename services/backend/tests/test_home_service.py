"""Tests for the Phase 9 Home section composers.

The Phase 2 stubs are gone — every composer takes real inputs (stores,
identities, timestamps) and returns a typed section dict matching
``packages/api-types/src/home.ts``.

Greeting + time-segment + tenant-local clock are kept verbatim from
Phase 2 (the fallback chain is unchanged), with the new
``tenant_local_*`` fields asserted alongside.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

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
from backend_app.inbox.store import InMemoryInboxStore, InboxItemRecord
from backend_app.projects.store import InMemoryProjectsStore, ProjectRecord
from backend_app.todos.store import InMemoryTodosStore, TodoRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(
    *,
    display_name: str | None = "Sarah Chen",
    metadata: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(display_name=display_name, metadata=metadata or {})


_NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
_ORG = "org_acme"
_USR = "usr_sarah"


# ---------------------------------------------------------------------------
# Greeting — Phase 2 fallback chain preserved + Phase 9 wall-clock fields.
# ---------------------------------------------------------------------------


class TestComposeGreeting:
    def test_idp_given_name_wins(self) -> None:
        user = _user(display_name="Sarah Chen", metadata={"given_name": "Sara"})
        greeting = compose_greeting(now=_NOW, user=user)
        assert greeting["display_name"] == "Sara"

    def test_falls_back_to_display_name_first_token(self) -> None:
        greeting = compose_greeting(now=_NOW, user=_user(display_name="Sarah Chen"))
        assert greeting["display_name"] == "Sarah"

    def test_returns_none_when_no_signal_available(self) -> None:
        greeting = compose_greeting(now=_NOW, user=_user(display_name=None))
        assert greeting["display_name"] is None

    def test_never_uses_email_local_part(self) -> None:
        user = SimpleNamespace(
            display_name=None,
            metadata={},
            primary_email="sarah.chen+marketing@acme.com",
        )
        greeting = compose_greeting(now=_NOW, user=user)
        assert greeting["display_name"] is None

    def test_tenant_local_fields_default_to_utc(self) -> None:
        greeting = compose_greeting(now=_NOW, user=_user())
        # No tenant_timezone passed → UTC fall-through. Wall clock
        # equals the input ``now``.
        assert greeting["tenant_local_date"] == "2026-05-18"
        assert greeting["tenant_local_iso"].startswith("2026-05-18T09:00:00")

    def test_tenant_local_fields_respect_tenant_timezone(self) -> None:
        # 09:00 UTC on 2026-05-18 is 02:00 PDT (UTC-7) the same morning;
        # the wall-clock fields shift accordingly.
        greeting = compose_greeting(
            now=_NOW, user=_user(), tenant_timezone="America/Los_Angeles"
        )
        assert greeting["tenant_local_date"] == "2026-05-18"
        assert "T02:00:00" in greeting["tenant_local_iso"]

    def test_invalid_timezone_falls_back_to_utc(self) -> None:
        greeting = compose_greeting(
            now=_NOW, user=_user(), tenant_timezone="Not/A/Real/Zone"
        )
        # UTC fallback; should not raise.
        assert greeting["tenant_local_date"] == "2026-05-18"

    def test_time_segment_morning(self) -> None:
        for hour in (0, 6, 11):
            greeting = compose_greeting(
                now=datetime(2026, 5, 18, hour, 0, tzinfo=timezone.utc),
                user=_user(),
            )
            assert greeting["time_segment"] == "morning"

    def test_time_segment_afternoon_and_evening(self) -> None:
        afternoon = compose_greeting(
            now=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc), user=_user()
        )
        evening = compose_greeting(
            now=datetime(2026, 5, 18, 20, 0, tzinfo=timezone.utc), user=_user()
        )
        assert afternoon["time_segment"] == "afternoon"
        assert evening["time_segment"] == "evening"


# ---------------------------------------------------------------------------
# Triage counts
# ---------------------------------------------------------------------------


class TestComposeTriageCounts:
    def test_counts_overdue_and_due_today(self) -> None:
        todos = InMemoryTodosStore()
        inbox = InMemoryInboxStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)

        # Overdue (due yesterday)
        todos.insert_todo(
            TodoRecord(
                id="todo_overdue",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="Overdue",
                status="open",
                due=(now - timedelta(days=1)).isoformat(),
            )
        )
        # Due today
        todos.insert_todo(
            TodoRecord(
                id="todo_today",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="Due today",
                status="open",
                due=(now + timedelta(hours=2)).isoformat(),
            )
        )
        # Due far future — must not count.
        todos.insert_todo(
            TodoRecord(
                id="todo_future",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="Future",
                status="open",
                due=(now + timedelta(days=5)).isoformat(),
            )
        )
        # Other-user todo — must not count (tenant scope is enforced
        # via owner_user_id at the store layer).
        todos.insert_todo(
            TodoRecord(
                id="todo_other",
                tenant_id=_ORG,
                owner_user_id="usr_eve",
                text="Other",
                status="open",
                due=(now - timedelta(days=1)).isoformat(),
            )
        )

        counts = compose_triage_counts(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            inbox_store=inbox,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        assert counts["todos_overdue"] == 1
        assert counts["todos_due_today"] == 1
        assert counts["runs_failed_24h"] == 0
        assert counts["approvals_waiting"] == 0

    def test_counts_approvals_waiting(self) -> None:
        todos = InMemoryTodosStore()
        inbox = InMemoryInboxStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)

        # Two approvals waiting (state=unread, kind=approval); one
        # already read; one notification (wrong kind).
        for ident, kind, state in [
            ("a", "approval", "unread"),
            ("b", "approval", "unread"),
            ("c", "approval", "read"),
            ("d", "notification", "unread"),
        ]:
            inbox.insert_item(
                InboxItemRecord(
                    id=f"inbox_{ident}",
                    tenant_id=_ORG,
                    owner_user_id=_USR,
                    kind=kind,
                    state=state,
                    title=f"item {ident}",
                )
            )

        counts = compose_triage_counts(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            inbox_store=inbox,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        assert counts["approvals_waiting"] == 2

    def test_tenant_isolation_does_not_leak(self) -> None:
        todos = InMemoryTodosStore()
        inbox = InMemoryInboxStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)

        # Other-org row — must not be visible to org_acme caller.
        inbox.insert_item(
            InboxItemRecord(
                id="inbox_other",
                tenant_id="org_beta",
                owner_user_id=_USR,
                kind="approval",
                state="unread",
                title="other-tenant",
            )
        )
        counts = compose_triage_counts(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            inbox_store=inbox,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        assert counts["approvals_waiting"] == 0


# ---------------------------------------------------------------------------
# Today timeline
# ---------------------------------------------------------------------------


class TestComposeTodayTimeline:
    def test_includes_overdue_and_due_today_todos(self) -> None:
        todos = InMemoryTodosStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)
        todos.insert_todo(
            TodoRecord(
                id="t_overdue",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="Late thing",
                status="open",
                due=(now - timedelta(hours=2)).isoformat(),
                priority="high",
            )
        )
        todos.insert_todo(
            TodoRecord(
                id="t_today",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="Today thing",
                status="open",
                due=(now + timedelta(hours=4)).isoformat(),
                priority="med",
            )
        )
        result = compose_today_timeline(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        assert result["status"] == "ok"
        kinds = [entry["kind"] for entry in result["data"]]
        assert kinds == ["todo_due", "todo_due"]

        overdue = result["data"][0]
        upcoming = result["data"][1]
        assert overdue["status"] == "overdue"
        assert overdue["is_overdue"] is True
        assert overdue["target"]["kind"] == "todo"
        assert upcoming["status"] == "upcoming"
        assert upcoming["is_overdue"] is False

    def test_excludes_far_future_due(self) -> None:
        todos = InMemoryTodosStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)
        todos.insert_todo(
            TodoRecord(
                id="t_far",
                tenant_id=_ORG,
                owner_user_id=_USR,
                text="next week",
                status="open",
                due=(now + timedelta(days=5)).isoformat(),
            )
        )
        result = compose_today_timeline(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        assert result["data"] == ()

    def test_sort_by_when_iso(self) -> None:
        todos = InMemoryTodosStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        today_start, today_end = tenant_today_bounds(now=now)
        for label, offset_hours in [("late", 6), ("early", 2), ("overdue", -3)]:
            todos.insert_todo(
                TodoRecord(
                    id=f"t_{label}",
                    tenant_id=_ORG,
                    owner_user_id=_USR,
                    text=label,
                    status="open",
                    due=(now + timedelta(hours=offset_hours)).isoformat(),
                )
            )
        result = compose_today_timeline(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            todos_store=todos,
            runs_reader=default_runs_reader(),
            tenant_today_start=today_start,
            tenant_today_end=today_end,
        )
        order = [entry["target"]["id"] for entry in result["data"]]
        # Overdue first (earliest when_iso), then early, then late.
        assert order == ["t_overdue", "t_early", "t_late"]


# ---------------------------------------------------------------------------
# WhatsNew
# ---------------------------------------------------------------------------


class TestComposeWhatsNew:
    def test_caps_at_seven_rows(self) -> None:
        rows = tuple({"i": i} for i in range(20))
        section = compose_whats_new(since_iso="2026-05-18T00:00:00+00:00", rows=rows)
        assert section["status"] == "ok"
        assert section["since_iso"] == "2026-05-18T00:00:00+00:00"
        assert len(section["data"]) == 7
        # Cap from the head (caller provides newest-first ordering).
        assert section["data"][0] == {"i": 0}


# ---------------------------------------------------------------------------
# In-flight projects
# ---------------------------------------------------------------------------


class TestComposeInFlightProjects:
    def test_includes_recent_active_member_projects(self) -> None:
        projects = InMemoryProjectsStore()
        todos = InMemoryTodosStore()
        inbox = InMemoryInboxStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

        recent = ProjectRecord(
            id="prj_recent",
            tenant_id=_ORG,
            owner_user_id=_USR,
            name="Launch prep",
            icon_emoji="🚀",
            color_hue=42,
            status="active",
            last_activity_at=now - timedelta(hours=3),
        )
        old = ProjectRecord(
            id="prj_old",
            tenant_id=_ORG,
            owner_user_id=_USR,
            name="Old thing",
            status="active",
            last_activity_at=now - timedelta(days=30),
        )
        projects.insert_project(recent)
        projects.insert_project(old)
        # Member of both via the owner row — exercise the member filter.
        from backend_app.projects.store import ProjectMembershipRecord

        for project in (recent, old):
            projects.insert_membership(
                ProjectMembershipRecord(
                    project_id=project.id,
                    user_id=_USR,
                    tenant_id=_ORG,
                    role="owner",
                    added_by=_USR,
                )
            )
        # Recent project has one open todo + one unread inbox item.
        todos.insert_todo(
            TodoRecord(
                id="todo_recent",
                tenant_id=_ORG,
                owner_user_id=_USR,
                project_id="prj_recent",
                text="open",
                status="open",
            )
        )
        inbox.insert_item(
            InboxItemRecord(
                id="inbox_recent",
                tenant_id=_ORG,
                owner_user_id=_USR,
                project_id="prj_recent",
                kind="notification",
                state="unread",
                title="msg",
            )
        )

        result = compose_in_flight_projects(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            projects_store=projects,
            todos_store=todos,
            inbox_store=inbox,
        )
        assert result["status"] == "ok"
        ids = [p["ref"]["id"] for p in result["data"]]
        # Old project filtered (>7d cutoff); recent kept.
        assert ids == ["prj_recent"]
        assert result["data"][0]["open_item_count"] == 2
        assert result["data"][0]["icon_emoji"] == "🚀"

    def test_drops_projects_with_zero_open_items(self) -> None:
        projects = InMemoryProjectsStore()
        todos = InMemoryTodosStore()
        inbox = InMemoryInboxStore()
        now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        from backend_app.projects.store import ProjectMembershipRecord

        empty = ProjectRecord(
            id="prj_empty",
            tenant_id=_ORG,
            owner_user_id=_USR,
            name="empty",
            status="active",
            last_activity_at=now - timedelta(hours=1),
        )
        projects.insert_project(empty)
        projects.insert_membership(
            ProjectMembershipRecord(
                project_id="prj_empty",
                user_id=_USR,
                tenant_id=_ORG,
                role="owner",
                added_by=_USR,
            )
        )
        result = compose_in_flight_projects(
            org_id=_ORG,
            user_id=_USR,
            now=now,
            projects_store=projects,
            todos_store=todos,
            inbox_store=inbox,
        )
        assert result["data"] == ()


# ---------------------------------------------------------------------------
# Quick actions
# ---------------------------------------------------------------------------


class TestComposeQuickActions:
    def test_non_admin_does_not_see_admin_tiles(self) -> None:
        tiles = compose_quick_actions(roles=())
        ids = {t["id"] for t in tiles}
        assert "qa_team_invite" not in ids
        # Non-admin still sees the four default tiles.
        assert {
            "qa_chat_new",
            "qa_todo_new",
            "qa_routine_new",
            "qa_tools_onboard",
        } <= ids

    @pytest.mark.parametrize("role", ["admin", "workspace_admin", "org_admin"])
    def test_admin_sees_admin_tiles(self, role: str) -> None:
        tiles = compose_quick_actions(roles=(role,))
        ids = {t["id"] for t in tiles}
        assert "qa_team_invite" in ids


# ---------------------------------------------------------------------------
# Live activity backfill
# ---------------------------------------------------------------------------


class TestComposeLiveActivity:
    def test_caps_at_fifteen_rows(self) -> None:
        rows = tuple({"i": i} for i in range(30))
        section = compose_live_activity(buffered_rows=rows)
        assert section["status"] == "ok"
        assert len(section["data"]) == 15
        # Tail-cap (most recent kept).
        assert section["data"][0] == {"i": 15}


# ---------------------------------------------------------------------------
# tenant_today_bounds — boundary math
# ---------------------------------------------------------------------------


class TestTenantTodayBounds:
    def test_utc_bounds_start_at_midnight(self) -> None:
        start, end = tenant_today_bounds(
            now=datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)
        )
        assert start.hour == 0 and start.minute == 0
        assert end.hour == 23 and end.minute == 59
        assert start.date().isoformat() == "2026-05-18"

    def test_tz_aware_bounds_shift_correctly(self) -> None:
        # 09:00 UTC on 2026-05-18 is 02:00 PDT — bounds should still be
        # 00:00 → 23:59 PDT, returned in UTC.
        start, end = tenant_today_bounds(
            now=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            tenant_timezone="America/Los_Angeles",
        )
        # PDT midnight 2026-05-18 = 07:00 UTC same day.
        assert start.hour == 7
        assert start.date().isoformat() == "2026-05-18"
