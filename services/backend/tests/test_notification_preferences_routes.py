"""Tests for the PR B4 / 8.0.3e notification preferences routes.

Covers the user-visible contract:

* GET hydrates the deployment-default matrix when no row exists.
* PUT preferences upserts cells and leaves siblings untouched.
* PUT quiet-hours upserts the row with HH:MM + IANA tz validation.
* Audit row lands once per privileged write.
* Validation rejects unknown event_kind / channel / time format.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.notifications.store import (
    InMemoryNotificationPrefsStore,
    NotificationChannel,
    NotificationEventKind,
)


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
    *,
    identity_store: InMemoryIdentityStore | None = None,
    notif_store: InMemoryNotificationPrefsStore | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryNotificationPrefsStore]:
    identity = identity_store or _seeded_identity()
    notif = notif_store or InMemoryNotificationPrefsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        notification_prefs_store=notif,
    )
    return TestClient(app), identity, notif


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestGetNotificationPreferences:
    def test_hydrates_deployment_defaults_when_no_rows(self) -> None:
        client, _i, _n = _client()
        response = client.get("/internal/v1/me/notifications", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        # 6 events × 3 channels = 18 entries.
        assert len(body["preferences"]) == 18
        # Spot-check a couple of defaults: in-app on for mention, push
        # off for everything, email on for approvals.
        cells = {
            (entry["event_kind"], entry["channel"]): entry["enabled"]
            for entry in body["preferences"]
        }
        assert cells[("mention", "in_app")] is True
        assert cells[("mention", "push")] is False
        assert cells[("approval_requested", "email")] is True
        assert cells[("weekly_digest", "in_app")] is False
        # Quiet hours default: disabled, 20:00..08:00, UTC.
        assert body["quiet_hours"]["enabled"] is False
        assert body["quiet_hours"]["from_local"] == "20:00"
        assert body["quiet_hours"]["tz"] == "UTC"

    def test_stored_cells_override_defaults(self) -> None:
        notif = InMemoryNotificationPrefsStore()
        from backend_app.notifications.store import NotificationPreferenceRow

        notif.upsert_preference(
            NotificationPreferenceRow(
                user_id="usr_sarah",
                event_kind=NotificationEventKind.MENTION,
                channel=NotificationChannel.IN_APP,
                enabled=False,
            )
        )
        client, _i, _n = _client(notif_store=notif)
        response = client.get("/internal/v1/me/notifications", params=_params())
        assert response.status_code == 200
        cells = {
            (entry["event_kind"], entry["channel"]): entry["enabled"]
            for entry in response.json()["preferences"]
        }
        # Stored override wins over the deployment default.
        assert cells[("mention", "in_app")] is False
        # Non-overridden cells still show defaults.
        assert cells[("mention", "email")] is True


class TestPutNotificationPreferences:
    def test_partial_preferences_only_writes_specified_cells(self) -> None:
        client, identity, notif = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={
                "preferences": [
                    {
                        "event_kind": "long_task_finished",
                        "channel": "push",
                        "enabled": True,
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
        rows = notif.list_preferences(user_id="usr_sarah")
        # Only the one cell was written; the rest stay un-stored and
        # the response hydrates them from defaults.
        assert len(rows) == 1
        assert rows[0].event_kind is NotificationEventKind.LONG_TASK_FINISHED
        assert rows[0].channel is NotificationChannel.PUSH
        assert rows[0].enabled is True
        # Audit row landed.
        events = identity.list_identity_audit(org_id="org_acme")
        notif_events = [e for e in events if e.action == "user.notifications.update"]
        assert len(notif_events) == 1
        meta = notif_events[0].metadata or {}
        assert "preferences.long_task_finished.push" in (meta.get("diff_paths") or [])

    def test_quiet_hours_round_trip(self) -> None:
        client, _i, notif = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={
                "quiet_hours": {
                    "enabled": True,
                    "from_local": "21:30",
                    "to_local": "07:15",
                    "tz": "America/New_York",
                }
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["quiet_hours"]["enabled"] is True
        assert body["quiet_hours"]["from_local"] == "21:30"
        assert body["quiet_hours"]["tz"] == "America/New_York"
        stored = notif.get_quiet_hours(user_id="usr_sarah")
        assert stored is not None and stored.tz == "America/New_York"

    def test_rejects_invalid_event_kind(self) -> None:
        client, _i, _n = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={
                "preferences": [
                    {"event_kind": "made_up_event", "channel": "email", "enabled": True}
                ]
            },
        )
        assert response.status_code == 422

    def test_rejects_invalid_time_format(self) -> None:
        client, _i, _n = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={
                "quiet_hours": {
                    "enabled": True,
                    "from_local": "25:00",
                    "to_local": "07:15",
                    "tz": "UTC",
                }
            },
        )
        assert response.status_code == 422

    def test_rejects_duplicate_cell_in_payload(self) -> None:
        client, _i, _n = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={
                "preferences": [
                    {"event_kind": "mention", "channel": "email", "enabled": True},
                    {"event_kind": "mention", "channel": "email", "enabled": False},
                ]
            },
        )
        assert response.status_code == 422

    def test_rejects_empty_request(self) -> None:
        client, _i, _n = _client()
        response = client.put(
            "/internal/v1/me/notifications",
            params=_params(),
            json={},
        )
        assert response.status_code == 400
