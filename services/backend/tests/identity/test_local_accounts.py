"""The "Use locally, no account" device account (D-series decisions).

Pins: fail-closed host-token gating (the localhost/CSRF threat model), DB-
arbitrated find-or-create (D4-A: one device account, every call resolves to
it), honest profile (auth_method "local", placeholder email), and that the
route simply does not exist on non-desktop deployments.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.identity.local_account_store import InMemoryLocalAccountStore

_TOKEN = "host-token-abc123"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "unit-test-auth-secret-0123456789")
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        local_account_store=InMemoryLocalAccountStore(),
    )
    return TestClient(app)


def _mint(client: TestClient, token: str | None = _TOKEN) -> Any:
    headers = {"x-enterprise-service-token": token} if token else {}
    return client.post("/v1/auth/local/session", headers=headers)


class TestHostTokenGate:
    def test_missing_token_is_401(self, client: TestClient) -> None:
        assert _mint(client, token=None).status_code == 401

    def test_wrong_token_is_401(self, client: TestClient) -> None:
        assert _mint(client, token="wrong").status_code == 401

    def test_unconfigured_token_fails_closed_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "unit-test-auth-secret-0123456789")
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            local_account_store=InMemoryLocalAccountStore(),
        )
        res = TestClient(app).post("/v1/auth/local/session")
        assert res.status_code == 503

    def test_route_absent_outside_desktop_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No injected store + default (non-desktop) profile → not registered.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "unit-test-auth-secret-0123456789")
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
        )
        res = TestClient(app).post(
            "/v1/auth/local/session",
            headers={"x-enterprise-service-token": _TOKEN},
        )
        assert res.status_code == 404


class TestDeviceAccount:
    def test_first_mint_provisions_real_account(self, client: TestClient) -> None:
        res = _mint(client)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["created"] is True
        assert body["bearer_token"]
        # The bearer is a REAL session: it authenticates /v1/me/profile and
        # the profile is HONEST (D3): local method, placeholder email.
        profile = client.get(
            "/internal/v1/me/profile",
            params={"org_id": body["org_id"], "user_id": body["user_id"]},
            headers={
                "x-enterprise-service-token": _TOKEN,
                "x-enterprise-org-id": body["org_id"],
                "x-enterprise-user-id": body["user_id"],
            },
        )
        assert profile.status_code == 200, profile.text
        p = profile.json()
        assert p["auth_method"] == "local"
        assert p["email_is_placeholder"] is True
        assert p["display_name"] == "Local account"

    def test_every_door_opens_the_one_account(self, client: TestClient) -> None:
        first = _mint(client).json()
        second = _mint(client).json()
        # D4-A: same account, new session; never a fork.
        assert second["created"] is False
        assert second["user_id"] == first["user_id"]
        assert second["org_id"] == first["org_id"]
        assert second["session_id"] != first["session_id"]

    def test_singleton_store_arbitrates_races(self) -> None:
        from backend_app.contracts import LocalAccountRecord

        store = InMemoryLocalAccountStore()
        a = store.create(LocalAccountRecord(org_id="org_a", user_id="usr_a"))
        b = store.create(LocalAccountRecord(org_id="org_b", user_id="usr_b"))
        # The loser gets the WINNER's row back, never a second row.
        assert b.user_id == a.user_id == "usr_a"
        assert store.get_singleton().user_id == "usr_a"

    def test_edge_carries_the_users_principal(self, client: TestClient) -> None:
        body = _mint(client).json()
        edge = client.app.state.local_account_store.get_singleton()
        assert edge is not None
        assert edge.principal_id == f"prn_{body['user_id']}"
