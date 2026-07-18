"""Tests for the per-user profile + preferences sidecar routes (PR 4.1).

Both endpoints are caller-scoped — identity comes from the
``x-enterprise-org-id`` / ``x-enterprise-user-id`` headers when the
service token is configured, or from the dev-fallback query params
otherwise. The TestClient runs with ``ENTERPRISE_SERVICE_TOKEN`` unset so
the dev path applies; that's the same setup ``test_me_routes.py`` uses
for the existing /me/workspaces endpoint.

The tests focus on the user-visible promises:

* GET hydrates deployment defaults when no row exists.
* PUT round-trips with merge-patch (omit / null / explicit value).
* Validation rejects bad timezone / locale / working_hours / chord.
* One audit row per privileged write into ``identity_audit_events``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.me_store import InMemoryMeStore
from backend_app.identity.store import InMemoryIdentityStore


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


class TestGetProfile:
    def test_hydrates_defaults_when_row_absent(self) -> None:
        client, _identity, _me = _client()
        response = client.get("/internal/v1/me/profile", params=_params())
        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == "usr_sarah"
        assert body["email"] == "sarah@acme.com"
        # Verified date came from the seeded UserRecord.
        assert body["email_verified_at"] is not None
        # Sidecar fields default to null when no row exists yet.
        assert body["title"] is None
        assert body["timezone"] is None
        assert body["locale"] is None
        assert body["working_hours"] is None
        assert body["avatar_url"] is None

    def test_404_when_user_missing(self) -> None:
        identity = InMemoryIdentityStore()
        identity.create_organization(
            OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
        )
        client, _i, _m = _client(identity_store=identity)
        response = client.get("/internal/v1/me/profile", params=_params())
        assert response.status_code == 404


class TestPutProfile:
    def test_round_trips_with_merge_patch(self) -> None:
        client, identity, _me = _client()

        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={
                "title": "Marketing Ops",
                "timezone": "America/Los_Angeles",
                "locale": "en-US",
                "working_hours": {
                    "tz": "America/Los_Angeles",
                    "start": "09:00",
                    "end": "18:00",
                    "days": [1, 2, 3, 4, 5],
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "Marketing Ops"
        assert body["timezone"] == "America/Los_Angeles"
        assert body["locale"] == "en-US"
        assert body["working_hours"]["start"] == "09:00"

        # PATCH semantics: omitting a field leaves it untouched, sending null clears.
        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={"title": None, "locale": "fr-FR"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] is None
        assert body["timezone"] == "America/Los_Angeles"  # untouched
        assert body["locale"] == "fr-FR"

        # Audit chain — one row per write.
        events = identity.list_identity_audit(org_id="org_acme")
        actions = [e.action for e in events]
        assert actions.count("user.profile.update") == 2

    def test_rejects_bad_timezone(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={"timezone": "Mars/Olympus_Mons"},
        )
        assert response.status_code == 422

    def test_rejects_bad_locale(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={"locale": "not_a_locale!@"},
        )
        assert response.status_code == 422

    def test_rejects_inverted_working_hours(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={
                "working_hours": {
                    "tz": "America/Los_Angeles",
                    "start": "18:00",
                    "end": "09:00",
                    "days": [1, 2],
                }
            },
        )
        assert response.status_code == 422

    def test_display_name_round_trips_via_users_table(self) -> None:
        client, identity, _me = _client()
        response = client.put(
            "/internal/v1/me/profile",
            params=_params(),
            json={"display_name": "Sarah C."},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Sarah C."
        # The identity row reflects the change so the directory + workspaces
        # endpoint sees it without a separate write path.
        user = identity.get_user(org_id="org_acme", user_id="usr_sarah")
        assert user is not None and user.display_name == "Sarah C."


class TestGetPreferences:
    def test_hydrates_defaults_when_row_absent(self) -> None:
        client, _i, _m = _client()
        response = client.get("/internal/v1/me/preferences", params=_params())
        assert response.status_code == 200
        body = response.json()
        # Defaults match the design's notification matrix: mention email+desktop;
        # approval email+desktop; run_finished desktop only; weekly_digest email.
        assert body["appearance"]["theme"] == "dark"
        assert body["appearance"]["accent"] == "sky"
        assert body["appearance"]["density"] == "comfortable"
        assert body["appearance"]["reduce_motion"] == "auto"
        matrix = body["notifications"]["matrix"]
        assert matrix["mention"] == {"email": True, "slack": False, "desktop": True}
        assert matrix["run_finished"] == {
            "email": False,
            "slack": False,
            "desktop": True,
        }


class TestPutPreferences:
    def test_deep_merge_only_changes_one_cell(self) -> None:
        client, identity, _me = _client()

        # Toggle one cell: notifications.matrix.mention.email = false.
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={"notifications": {"matrix": {"mention": {"email": False}}}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # The single cell flipped; siblings untouched.
        assert body["notifications"]["matrix"]["mention"]["email"] is False
        assert body["notifications"]["matrix"]["mention"]["desktop"] is True
        # Other events default through unchanged.
        assert body["notifications"]["matrix"]["approval_needed"]["email"] is True

        # Audit row landed.
        events = identity.list_identity_audit(org_id="org_acme")
        prefs_events = [e for e in events if e.action == "user.preferences.update"]
        assert len(prefs_events) == 1
        assert "notifications.matrix.mention.email" in (
            prefs_events[0].metadata.get("diff_paths") or []
        )

    def test_appearance_round_trip(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={
                "appearance": {
                    "accent": "violet",
                    "density": "compact",
                    "reduce_motion": "always",
                }
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["appearance"]["accent"] == "violet"
        assert body["appearance"]["density"] == "compact"
        assert body["appearance"]["reduce_motion"] == "always"
        # Theme defaulted through (was not in the patch).
        assert body["appearance"]["theme"] == "dark"

    def test_rejects_unknown_accent(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={"appearance": {"accent": "neon-pink"}},
        )
        assert response.status_code == 422

    def test_rejects_unknown_shortcut_id(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={"shortcuts": {"overrides": {"chat.fly.airplane": "$mod+P"}}},
        )
        assert response.status_code == 422

    def test_rejects_unknown_event_type(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={"notifications": {"matrix": {"daily_random": {"email": True}}}},
        )
        assert response.status_code == 422

    def test_shortcut_override_persists(self) -> None:
        client, _i, _m = _client()
        response = client.put(
            "/internal/v1/me/preferences",
            params=_params(),
            json={"shortcuts": {"overrides": {"chat.search": "$mod+P"}}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["shortcuts"]["overrides"]["chat.search"] == "$mod+P"
