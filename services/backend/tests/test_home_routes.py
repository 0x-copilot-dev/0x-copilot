"""Tests for ``GET /v1/home`` — Phase 9 Home aggregator.

Owner-only, tenant-first. Identity comes from query params in the
TestClient setup (no ``ENTERPRISE_SERVICE_TOKEN`` set), matching the
existing me/* test conventions.

The wire shape under test mirrors ``packages/api-types/src/home.ts``
``HomePayload`` exactly — snake_case at the wire, every section
``SectionResult``-shaped (or the sibling ``WhatsNewSection`` shape).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.me_store import (
    InMemoryMeStore,
    UserPreferencesRecord,
)
from backend_app.identity.store import InMemoryIdentityStore


_VALID_STATUSES = {"ok", "error", "unavailable"}
_VALID_TIME_SEGMENTS = {"morning", "afternoon", "evening"}


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, 9, 1, 24, tzinfo=timezone.utc),
        )
    )
    return store


def _client(
    identity_store: InMemoryIdentityStore | None = None,
    me_store: InMemoryMeStore | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryMeStore]:
    identity = identity_store or _seeded_identity()
    me = me_store or InMemoryMeStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        me_store=me,
    )
    return TestClient(app), identity, me


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestGetHomeShape:
    def test_returns_full_phase9_shape(self) -> None:
        client, _i, _m = _client()
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()

        # ---- Top-level keys mirror HomePayload ----
        for key in (
            "greeting",
            "triage",
            "today_timeline",
            "whats_new",
            "in_flight_projects",
            "live_activity",
            "quick_actions",
            "cached_at",
            "is_first_run",
        ):
            assert key in body, f"missing top-level key: {key}"

        # ---- Greeting (Phase 9: tenant_local_* present) ----
        greeting = body["greeting"]
        assert greeting["display_name"] == "Sarah"
        assert greeting["time_segment"] in _VALID_TIME_SEGMENTS
        # snake_case wire shape — Phase 9 additions
        assert isinstance(greeting["tenant_local_date"], str)
        assert greeting["tenant_local_date"].count("-") == 2  # YYYY-MM-DD
        assert isinstance(greeting["tenant_local_iso"], str)
        assert "T" in greeting["tenant_local_iso"]

        # ---- Triage (flat, four int fields) ----
        triage = body["triage"]
        for field in (
            "approvals_waiting",
            "runs_failed_24h",
            "todos_overdue",
            "todos_due_today",
        ):
            assert field in triage
            assert isinstance(triage[field], int)

        # ---- SectionResult-shaped sections ----
        for section_name in ("today_timeline", "in_flight_projects", "live_activity"):
            entry = body[section_name]
            assert entry["status"] in _VALID_STATUSES, f"{section_name}: {entry}"

        # ---- WhatsNewSection carries since_iso ----
        whats_new = body["whats_new"]
        assert whats_new["status"] in _VALID_STATUSES
        assert isinstance(whats_new["since_iso"], str)
        assert "T" in whats_new["since_iso"]

        # ---- Quick actions is a list (never wrapped) ----
        assert isinstance(body["quick_actions"], list)
        assert len(body["quick_actions"]) >= 1

        # ---- Top-level metadata ----
        assert isinstance(body["cached_at"], str)
        assert isinstance(body["is_first_run"], bool)


class TestEmptyState:
    def test_is_first_run_true_for_fresh_user(self) -> None:
        """Brand-new user with no data → is_first_run=true."""

        client, _i, _m = _client()
        body = client.get("/v1/home", params=_params()).json()
        assert body["is_first_run"] is True


class TestUserNotFound:
    def test_404_when_user_missing(self) -> None:
        identity = InMemoryIdentityStore()
        identity.create_organization(
            OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
        )
        client, _i, _m = _client(identity_store=identity)
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 404

    def test_tenant_isolation_caller_cannot_read_other_user(self) -> None:
        client, _i, _m = _client()
        response = client.get(
            "/v1/home", params={"org_id": "org_acme", "user_id": "usr_eve"}
        )
        assert response.status_code == 404


class TestLastVisitAdvance:
    def test_second_call_observes_first_cutoff(self) -> None:
        """``users.home_last_visit_at`` is advanced atomically — the
        second visit's ``since_iso`` is the first visit's ``cached_at``.
        """

        client, _i, _m = _client()
        first = client.get("/v1/home", params=_params()).json()
        second = client.get("/v1/home", params=_params()).json()
        # First visit's since_iso falls back to now-24h (no prior
        # cutoff). Second visit's since_iso is the first visit's
        # cached_at (within a small drift — both come from
        # datetime.now(UTC) in the same process). The invariant we
        # really care about: second.since_iso > first.since_iso.
        assert second["whats_new"]["since_iso"] > first["whats_new"]["since_iso"]

    def test_first_visit_since_iso_is_24h_lookback(self) -> None:
        """No prior cutoff → since_iso falls back to ~ now-24h."""

        client, _i, _m = _client()
        body = client.get("/v1/home", params=_params()).json()
        since = datetime.fromisoformat(
            body["whats_new"]["since_iso"].replace("Z", "+00:00")
        )
        now = datetime.now(timezone.utc)
        delta = (now - since).total_seconds()
        # Allow a couple of seconds of clock drift in either direction.
        assert 23 * 3600 <= delta <= 25 * 3600


class TestTenantTimezone:
    def test_tenant_local_date_uses_profile_timezone(self) -> None:
        """When the caller's profile carries a timezone, the greeting's
        tenant_local_* fields are rendered in that timezone."""

        from backend_app.identity.me_store import UserProfileRecord

        me = InMemoryMeStore()
        me.upsert_profile(
            UserProfileRecord(
                user_id="usr_sarah",
                org_id="org_acme",
                timezone="America/Los_Angeles",
            )
        )
        client, _i, _m = _client(me_store=me)
        body = client.get("/v1/home", params=_params()).json()
        # We don't pin the exact time (the test runs at wall-clock), but
        # the format must be a valid full ISO string in *some* offset
        # other than +00:00 (LA is UTC-7 / -8 year-round).
        iso = body["greeting"]["tenant_local_iso"]
        assert "+00:00" not in iso  # not UTC
        assert iso.endswith(("-07:00", "-08:00"))


class TestQuickActionsAdminFilter:
    def test_non_admin_caller_does_not_see_admin_only_tile(self) -> None:
        client, _i, _m = _client()
        body = client.get("/v1/home", params=_params()).json()
        ids = {action["id"] for action in body["quick_actions"]}
        # team_invite is admin-only; the dev-fallback caller has no
        # role headers so the tile is filtered.
        assert "qa_team_invite" not in ids
        assert {
            "qa_chat_new",
            "qa_todo_new",
            "qa_routine_new",
            "qa_tools_onboard",
        } <= ids


class TestActivityWindowPrefStillRead:
    """Preserves the Phase 2 contract: the activity-window pref is
    read from the JSONB prefs blob without crashing the route. Phase 9
    composers don't use it yet, but the read path must survive a
    garbage value so future composers can wire it without a regression."""

    def test_garbage_pref_does_not_crash(self) -> None:
        me = InMemoryMeStore()
        me.upsert_preferences(
            UserPreferencesRecord(
                user_id="usr_sarah",
                org_id="org_acme",
                preferences={"home": {"activity_window_hours": 9999}},
            )
        )
        client, _i, _m = _client(me_store=me)
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 200
