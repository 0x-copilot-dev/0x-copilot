"""Tests for the session service + store + routes (A2)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.identity import (
    DevMintNotAllowed,
    InMemorySessionStore,
    SessionInvalidToken,
    SessionNotActive,
    SessionService,
)
from backend_app.identity.session_sweeper import SessionSweeper


_TEST_AUTH_SECRET = "test-auth-secret"
_TEST_SERVICE_TOKEN = "test-service-token"


# ---------------------------------------------------------------------------
# SessionService unit tests
# ---------------------------------------------------------------------------


class SessionServiceFixtureMixin:
    def service(self, *, dev_mint_allowed: bool = True) -> SessionService:
        return SessionService(
            store=InMemorySessionStore(),
            auth_secret=_TEST_AUTH_SECRET,
            dev_mint_allowed=dev_mint_allowed,
        )


class TestDevMint(SessionServiceFixtureMixin):
    def test_dev_mint_returns_signed_bearer_with_sid_claim(self) -> None:
        svc = self.service()
        result = svc.dev_mint(org_id="org_a", user_id="usr_a")

        # Token shape: payload.signature
        payload_b64, _, _ = result.bearer_token.partition(".")
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
        )
        assert payload["sid"] == result.session_id
        assert payload["org_id"] == "org_a"
        assert payload["user_id"] == "usr_a"
        assert "exp" in payload

    def test_dev_mint_disabled_under_locked_profile(self) -> None:
        svc = self.service(dev_mint_allowed=False)
        with pytest.raises(DevMintNotAllowed):
            svc.dev_mint(org_id="org_a", user_id="usr_a")

    def test_token_hash_in_store_is_sha256_of_signature(self) -> None:
        svc = self.service()
        result = svc.dev_mint(org_id="org_a", user_id="usr_a")

        # Reach into the in-memory store to check the on-disk shape.
        store: InMemorySessionStore = svc._store  # type: ignore[assignment]
        record = store.sessions[result.session_id]
        signature_b64 = result.bearer_token.split(".")[1]
        expected_hash = hashlib.sha256(signature_b64.encode("ascii")).hexdigest()
        assert record.token_hash == expected_hash
        # Plaintext bearer must NOT appear anywhere in the row.
        for value in record.model_dump().values():
            assert result.bearer_token not in repr(value)


class TestTouch(SessionServiceFixtureMixin):
    def test_touch_with_valid_token_returns_identity(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(
            org_id="org_a",
            user_id="usr_a",
            roles=("admin",),
            permission_scopes=("admin:users", "runtime:use"),
        )

        touched = svc.touch_by_token(minted.bearer_token)

        assert touched.session_id == minted.session_id
        assert touched.org_id == "org_a"
        assert touched.user_id == "usr_a"
        assert touched.roles == ("admin",)
        assert touched.permission_scopes == ("admin:users", "runtime:use")
        assert touched.mfa_satisfied is False  # MFA lands in A6

    def test_touch_after_revoke_returns_401_class_error(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(org_id="org_a", user_id="usr_a")

        assert svc.revoke(org_id="org_a", session_id=minted.session_id) is True
        with pytest.raises(SessionNotActive):
            svc.touch_by_token(minted.bearer_token)

    def test_touch_with_forged_signature_rejected(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(org_id="org_a", user_id="usr_a")

        payload, _ = minted.bearer_token.split(".", 1)
        forged = f"{payload}.deadbeef"  # wrong signature
        with pytest.raises(SessionInvalidToken):
            svc.touch_by_token(forged)

    def test_touch_after_expiry_returns_inactive(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(org_id="org_a", user_id="usr_a", ttl_seconds=24 * 60 * 60)

        # Force the stored expiry into the past.
        store: InMemorySessionStore = svc._store  # type: ignore[assignment]
        record = store.sessions[minted.session_id]
        store.sessions[minted.session_id] = record.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
        )
        with pytest.raises(SessionNotActive):
            svc.touch_by_token(minted.bearer_token)


class TestRevoke(SessionServiceFixtureMixin):
    def test_revoke_is_idempotent(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(org_id="org_a", user_id="usr_a")

        assert svc.revoke(org_id="org_a", session_id=minted.session_id) is True
        # Second revoke is also "true" — the row stays revoked. Idempotent.
        assert svc.revoke(org_id="org_a", session_id=minted.session_id) is True

    def test_revoke_with_wrong_org_does_nothing(self) -> None:
        svc = self.service()
        minted = svc.dev_mint(org_id="org_a", user_id="usr_a")

        assert svc.revoke(org_id="org_other", session_id=minted.session_id) is False
        # Original session still live.
        touched = svc.touch_by_token(minted.bearer_token)
        assert touched.session_id == minted.session_id


class TestList(SessionServiceFixtureMixin):
    def test_list_active_excludes_expired_and_revoked(self) -> None:
        svc = self.service()
        live = svc.dev_mint(org_id="org_a", user_id="usr_a")
        revoked = svc.dev_mint(org_id="org_a", user_id="usr_a")
        expired = svc.dev_mint(org_id="org_a", user_id="usr_a")

        svc.revoke(org_id="org_a", session_id=revoked.session_id)
        store: InMemorySessionStore = svc._store  # type: ignore[assignment]
        store.sessions[expired.session_id] = store.sessions[
            expired.session_id
        ].model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}
        )

        listed = svc.list_active(org_id="org_a", user_id="usr_a")
        assert {s.session_id for s in listed} == {live.session_id}

    def test_list_active_is_org_scoped(self) -> None:
        svc = self.service()
        a = svc.dev_mint(org_id="org_a", user_id="shared")
        b = svc.dev_mint(org_id="org_b", user_id="shared")

        only_a = svc.list_active(org_id="org_a", user_id="shared")
        only_b = svc.list_active(org_id="org_b", user_id="shared")

        assert {s.session_id for s in only_a} == {a.session_id}
        assert {s.session_id for s in only_b} == {b.session_id}


class TestSessionSweeper(SessionServiceFixtureMixin):
    def test_sweep_purges_rows_older_than_retention(self) -> None:
        svc = self.service()
        live = svc.dev_mint(org_id="org_a", user_id="usr_a")
        ancient = svc.dev_mint(org_id="org_a", user_id="usr_a")

        store: InMemorySessionStore = svc._store  # type: ignore[assignment]
        # Push the ancient row well beyond the retention window.
        store.sessions[ancient.session_id] = store.sessions[
            ancient.session_id
        ].model_copy(
            update={
                "expires_at": datetime.now(timezone.utc) - timedelta(days=365),
            }
        )

        purged = svc.sweep_expired()
        assert purged == 1
        assert ancient.session_id not in store.sessions
        assert live.session_id in store.sessions

    def test_sweeper_loop_runs_and_stops_cleanly(self) -> None:
        svc = self.service()
        sweeper = SessionSweeper(sessions=svc, interval_seconds=5)

        async def exercise() -> int:
            await sweeper.start()
            count = await sweeper.sweep_once()
            await sweeper.stop()
            return count

        result = asyncio.run(exercise())
        assert result == 0  # nothing to purge


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


class _BackendApiFixtureMixin:
    """Boots a TestClient with the test secret + service token wired up."""

    def client(self, monkeypatch) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_AUTH_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)
        # Ensure dev_mint_allowed=True via dev profile defaults.
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")

        return TestClient(create_app())

    def service_headers(
        self, *, org_id: str = "org_a", user_id: str = "usr_a"
    ) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _TEST_SERVICE_TOKEN,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": user_id,
            "x-enterprise-roles": "admin",
            "x-enterprise-permission-scopes": "admin:users",
            "x-enterprise-connector-scopes": "{}",
        }


class TestDevMintRoute(_BackendApiFixtureMixin):
    def test_dev_mint_returns_session_id_and_bearer(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        response = client.post(
            "/internal/v1/auth/sessions/dev-mint",
            headers=self.service_headers(),
            json={"org_id": "org_a", "user_id": "usr_a"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["session_id"].startswith("sid_")
        assert "." in body["bearer_token"]


class TestSessionRoutes(_BackendApiFixtureMixin):
    def _mint(self, client: TestClient) -> dict[str, str]:
        response = client.post(
            "/internal/v1/auth/sessions",
            headers=self.service_headers(),
            json={
                "org_id": "org_a",
                "user_id": "usr_a",
                "roles": ["admin"],
                "permission_scopes": ["admin:users"],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_create_then_touch_round_trip(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        minted = self._mint(client)

        signature_b64 = minted["bearer_token"].split(".")[1]
        token_hash = hashlib.sha256(signature_b64.encode("ascii")).hexdigest()

        response = client.post(
            "/internal/v1/auth/sessions/touch",
            headers=self.service_headers(),
            json={"session_id": minted["session_id"], "token_hash": token_hash},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["org_id"] == "org_a"
        assert body["user_id"] == "usr_a"

    def test_revoke_then_touch_returns_401(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        minted = self._mint(client)
        signature_b64 = minted["bearer_token"].split(".")[1]
        token_hash = hashlib.sha256(signature_b64.encode("ascii")).hexdigest()

        revoke = client.post(
            f"/internal/v1/auth/sessions/{minted['session_id']}/revoke",
            headers=self.service_headers(),
            json={"org_id": "org_a", "reason": "user_revoked"},
        )
        assert revoke.status_code == 204

        touch = client.post(
            "/internal/v1/auth/sessions/touch",
            headers=self.service_headers(),
            json={"session_id": minted["session_id"], "token_hash": token_hash},
        )
        assert touch.status_code == 401

    def test_cross_tenant_revoke_is_no_op(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        minted = self._mint(client)

        # Caller from a different org tries to revoke the session — gets 204
        # (idempotent / does-not-leak) but the underlying row stays active.
        cross = client.post(
            f"/internal/v1/auth/sessions/{minted['session_id']}/revoke",
            headers=self.service_headers(org_id="org_other", user_id="usr_other"),
            json={"org_id": "org_other", "reason": "x"},
        )
        assert cross.status_code == 204

        # Original org can still touch the session.
        signature_b64 = minted["bearer_token"].split(".")[1]
        token_hash = hashlib.sha256(signature_b64.encode("ascii")).hexdigest()
        touch = client.post(
            "/internal/v1/auth/sessions/touch",
            headers=self.service_headers(),
            json={"session_id": minted["session_id"], "token_hash": token_hash},
        )
        assert touch.status_code == 200

    def test_list_sessions_is_scoped(self, monkeypatch) -> None:
        client = self.client(monkeypatch)
        self._mint(client)

        response = client.get(
            "/internal/v1/auth/sessions",
            headers=self.service_headers(),
            params={"org_id": "org_a", "user_id": "usr_a"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["sessions"]) == 1
        # Cross-tenant list returns nothing.
        cross = client.get(
            "/internal/v1/auth/sessions",
            headers=self.service_headers(org_id="org_other", user_id="usr_other"),
            params={"org_id": "org_other", "user_id": "usr_other"},
        )
        assert cross.json() == {"sessions": []}


# Facade-side back-compat for the REQUIRE_SESSION_BINDING gate is exercised
# in services/backend-facade/tests/test_session_binding.py — that lives in
# the facade's pytest scope so it can import backend_facade.* without
# violating the hard service boundary.
