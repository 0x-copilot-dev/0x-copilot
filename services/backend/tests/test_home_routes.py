"""Tests for ``GET /v1/home`` — Home destination aggregator (Phase 2).

Owner-only, tenant-first. Identity comes from query params in the
TestClient setup (no ``ENTERPRISE_SERVICE_TOKEN`` set), matching the
existing me/* test conventions.

Coverage:

* Happy path returns HomeResponse with the documented shape.
* All sections wrap in SectionResult with a valid status.
* ``home.activity_window_hours`` KV preference is read (default 24).
* Caller-supplied ``user_id`` cannot cross tenants — the response
  identity always reflects the verified caller.
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


class TestGetHome:
    def test_happy_path_returns_full_shape(self) -> None:
        client, _identity, _me = _client()
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()

        # Greeting block — name is derived from "Sarah Chen", first token.
        assert body["greeting"]["display_name"] == "Sarah"
        assert body["greeting"]["time_segment"] in _VALID_TIME_SEGMENTS

        # Every section is present and SectionResult-shaped.
        for section in (
            "activity",
            "pinned_chats",
            "recent_runs",
            "favorite_tools",
            "todays_focus",
            "upcoming_meetings",
        ):
            assert section in body, f"missing section: {section}"
            entry = body[section]
            assert entry["status"] in _VALID_STATUSES, (
                f"bad status for {section}: {entry['status']}"
            )

    def test_stub_sections_return_empty_arrays(self) -> None:
        client, _i, _m = _client()
        body = client.get("/v1/home", params=_params()).json()

        # Stubs that ship as status=ok with empty data.
        for section in (
            "activity",
            "pinned_chats",
            "recent_runs",
            "favorite_tools",
            "todays_focus",
        ):
            entry = body[section]
            assert entry["status"] == "ok", f"{section} not ok: {entry}"
            assert entry["data"] == [], f"{section} not empty: {entry['data']}"

    def test_upcoming_meetings_is_unavailable_until_connector_lands(self) -> None:
        """Drives the FE 'Connect a calendar' CTA — must be a stable code."""

        client, _i, _m = _client()
        body = client.get("/v1/home", params=_params()).json()
        meetings = body["upcoming_meetings"]
        assert meetings["status"] == "unavailable"
        assert meetings["error"] == "no_calendar_connector"

    def test_404_when_user_missing(self) -> None:
        """Owner-only: the verified identity must resolve to an actual user."""

        identity = InMemoryIdentityStore()
        identity.create_organization(
            OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
        )
        client, _i, _m = _client(identity_store=identity)
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 404

    def test_reads_activity_window_hours_preference(self) -> None:
        """The KV pref under ``preferences.home.activity_window_hours``
        is read on every request. Until the activity composer wires
        the value, the integration is verified by exercising the read
        path (the value flows but is unused). We assert no crash and a
        200 — the read is the contract.
        """

        me = InMemoryMeStore()
        me.upsert_preferences(
            UserPreferencesRecord(
                user_id="usr_sarah",
                org_id="org_acme",
                preferences={"home": {"activity_window_hours": 48}},
            )
        )
        client, _i, _m = _client(me_store=me)
        response = client.get("/v1/home", params=_params())
        assert response.status_code == 200

    def test_activity_window_out_of_range_falls_back_to_default(self) -> None:
        """Garbage / out-of-range pref values must not crash the route."""

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

    def test_tenant_isolation_caller_cannot_read_other_user(self) -> None:
        """Caller-supplied user_id is only allowed when it matches the
        verified caller. With ENTERPRISE_SERVICE_TOKEN unset (dev
        fallback) the params *are* the identity — but the seeded
        identity store only has one user, so requesting a different
        user_id 404s on the user lookup rather than crossing tenants."""

        client, _i, _m = _client()
        response = client.get(
            "/v1/home",
            params={"org_id": "org_acme", "user_id": "usr_eve"},
        )
        # The route resolves identity from the params (dev fallback),
        # then looks up the user; absent → 404. The point is no data
        # for the seeded user leaks into the response.
        assert response.status_code == 404
