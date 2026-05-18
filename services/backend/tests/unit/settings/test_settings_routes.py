"""Tests for ``/v1/settings/*`` routes (Phase 12 P12-A6).

Coverage:

* Happy path GET + PATCH on all three namespaces.
* ACL — owner-only on user namespace; admin-only on tenant namespaces.
* Namespace isolation — patching ``notifications`` for a user never
  clobbers the existing ``home.*`` preferences block (Phase 2 +
  P9-A2 single-source-of-truth guarantee).
* Default materialisation — fresh tenant / user gets a fully-formed
  response shape even without a row on disk.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.settings.store import InMemorySettingsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (
        ("usr_sarah", "Sarah"),
        ("usr_bob", "Bob"),
        ("usr_admin", "Admin"),
    ):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=display,
            )
        )
    return store


def _client(
    settings_store: InMemorySettingsStore | None = None,
) -> tuple[TestClient, InMemorySettingsStore]:
    settings = settings_store or InMemorySettingsStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        settings_store=settings,
    )
    return TestClient(app), settings


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


# ---------------------------------------------------------------------------
# User notifications
# ---------------------------------------------------------------------------


class TestUserNotifications:
    def test_get_returns_defaults_when_unset(self) -> None:
        client, _ = _client()
        resp = client.get("/v1/settings/notifications", params=_q())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user_id"] == "usr_sarah"
        assert body["destinations_enabled"] == {}
        # Default quiet hours (off, 20:00->08:00 UTC).
        assert body["quiet_hours"]["enabled"] is False
        assert body["quiet_hours"]["tz"] == "UTC"

    def test_patch_then_get_round_trip(self) -> None:
        client, _ = _client()
        patch_body = {
            "destinations_enabled": {"inbox": True, "home": False},
            "quiet_hours": {
                "enabled": True,
                "from_local": "22:00",
                "to_local": "07:00",
                "tz": "America/Los_Angeles",
            },
        }
        resp = client.patch("/v1/settings/notifications", params=_q(), json=patch_body)
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["destinations_enabled"] == {"inbox": True, "home": False}
        assert patched["quiet_hours"]["tz"] == "America/Los_Angeles"

        # Subsequent GET sees the patched state.
        got = client.get("/v1/settings/notifications", params=_q())
        assert got.status_code == 200
        assert got.json()["destinations_enabled"] == {"inbox": True, "home": False}

    def test_patch_rejects_unknown_keys(self) -> None:
        client, _ = _client()
        resp = client.patch(
            "/v1/settings/notifications",
            params=_q(),
            json={"made_up_field": True},
        )
        # Pydantic ``extra='forbid'`` -> 422.
        assert resp.status_code == 422

    def test_existing_home_prefs_preserved_across_patch(self) -> None:
        """The Phase 2 + P9-A2 ``home.*`` block must survive a notifications PATCH.

        Critical invariant for the JSONB single-source-of-truth design.
        """

        store = InMemorySettingsStore()
        store.user_preferences[("org_acme", "usr_sarah")] = {
            "home": {
                "activity_window_hours": 72,
                "last_visit_iso": "2026-05-17T09:00:00+00:00",
            }
        }
        client, store = _client(settings_store=store)

        resp = client.patch(
            "/v1/settings/notifications",
            params=_q(),
            json={"destinations_enabled": {"inbox": False}},
        )
        assert resp.status_code == 200, resp.text

        # On-disk JSONB blob still has the home.* block intact.
        blob = store.user_preferences[("org_acme", "usr_sarah")]
        assert blob["home"] == {
            "activity_window_hours": 72,
            "last_visit_iso": "2026-05-17T09:00:00+00:00",
        }
        assert blob["notifications"]["destinations_enabled"] == {"inbox": False}


# ---------------------------------------------------------------------------
# Workspace notifications (admin)
# ---------------------------------------------------------------------------


def _admin_headers() -> dict[str, str]:
    """Service-token headers that mark the caller as admin.

    The route reads roles + permission_scopes from the trusted
    facade-headers envelope. In tests we satisfy ``_verify_service_token``
    by setting both ``ENTERPRISE_SERVICE_TOKEN`` env var + the matching
    header on the request, plus the roles header carrying ``admin``.
    """

    return {
        "x-enterprise-service-token": "test-svc",
        "x-enterprise-org-id": "org_acme",
        "x-enterprise-user-id": "usr_admin",
        "x-enterprise-roles": "admin",
        "x-enterprise-permission-scopes": "runtime:use,admin:users",
    }


class TestWorkspaceNotifications:
    def test_admin_gets_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        resp = client.get(
            "/v1/settings/workspace/notifications",
            params=_q("usr_admin"),
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["destinations_enabled"] == {}
        assert body["updated_by_user_id"] is None

    def test_admin_can_patch(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        resp = client.patch(
            "/v1/settings/workspace/notifications",
            params=_q("usr_admin"),
            json={"destinations_enabled": {"inbox": False}},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["destinations_enabled"] == {"inbox": False}
        assert body["updated_by_user_id"] == "usr_admin"

    def test_non_admin_forbidden(self, monkeypatch) -> None:
        # Non-admin: service-token + roles=employee.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        non_admin_headers = {
            "x-enterprise-service-token": "test-svc",
            "x-enterprise-org-id": "org_acme",
            "x-enterprise-user-id": "usr_bob",
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": "runtime:use",
        }
        resp = client.get(
            "/v1/settings/workspace/notifications",
            params=_q("usr_bob"),
            headers=non_admin_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Webhook security defaults (admin)
# ---------------------------------------------------------------------------


class TestWebhookSecurityDefaults:
    def test_admin_gets_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        resp = client.get(
            "/v1/settings/security/webhooks",
            params=_q("usr_admin"),
            headers=_admin_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Defaults: HMAC on, allowlist off, no max age.
        assert body["default_hmac_on"] is True
        assert body["require_ip_allowlist"] is False
        assert body["max_secret_age_days"] == 0

    def test_admin_can_patch(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        resp = client.patch(
            "/v1/settings/security/webhooks",
            params=_q("usr_admin"),
            json={"require_ip_allowlist": True, "max_secret_age_days": 90},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Patched.
        assert body["require_ip_allowlist"] is True
        assert body["max_secret_age_days"] == 90
        # Unspecified field defaults survive via the materialised
        # response (default_hmac_on stays True).
        assert body["default_hmac_on"] is True

    def test_non_admin_forbidden(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        non_admin_headers = {
            "x-enterprise-service-token": "test-svc",
            "x-enterprise-org-id": "org_acme",
            "x-enterprise-user-id": "usr_bob",
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": "runtime:use",
        }
        resp = client.patch(
            "/v1/settings/security/webhooks",
            params=_q("usr_bob"),
            json={"default_hmac_on": False},
            headers=non_admin_headers,
        )
        assert resp.status_code == 403

    def test_patch_rejects_negative_max_age(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-svc")
        client, _ = _client()
        resp = client.patch(
            "/v1/settings/security/webhooks",
            params=_q("usr_admin"),
            json={"max_secret_age_days": -1},
            headers=_admin_headers(),
        )
        assert resp.status_code == 422
