"""Tests for the facade's REQUIRE_SESSION_BINDING gate (A2)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend_facade.auth_routes as auth_routes_module
from backend_facade.app import create_app
from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings


_TEST_SECRET = "test-auth-secret"


def _hmac_token(payload: dict[str, object], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret.encode(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


class TestSessionBindingGate:
    def test_token_without_sid_accepted_when_binding_off(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.delenv("REQUIRE_SESSION_BINDING", raising=False)

        token = _hmac_token({"org_id": "org_a", "user_id": "usr_a"}, _TEST_SECRET)
        identity = FacadeAuthenticator.verify_identity_token(token, _TEST_SECRET)

        assert identity.org_id == "org_a"

    def test_token_without_sid_rejected_when_binding_on(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("REQUIRE_SESSION_BINDING", "true")

        token = _hmac_token({"org_id": "org_a", "user_id": "usr_a"}, _TEST_SECRET)
        with pytest.raises(HTTPException) as exc:
            FacadeAuthenticator.verify_identity_token(token, _TEST_SECRET)

        assert exc.value.status_code == 401
        assert "sid" in str(exc.value.detail).lower()

    def test_token_with_sid_accepted_when_binding_on(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("REQUIRE_SESSION_BINDING", "true")

        token = _hmac_token(
            {"org_id": "org_a", "user_id": "usr_a", "sid": "sid_test"},
            _TEST_SECRET,
        )
        identity = FacadeAuthenticator.verify_identity_token(token, _TEST_SECRET)

        assert identity.org_id == "org_a"
        assert FacadeAuthenticator.session_id_from_token(token) == "sid_test"

    def test_token_hash_from_signature_matches_backend_storage_format(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        token = _hmac_token({"org_id": "org_a", "user_id": "usr_a"}, _TEST_SECRET)

        signature = token.split(".")[1]
        expected = hashlib.sha256(signature.encode("ascii")).hexdigest()

        assert FacadeAuthenticator.token_hash_from_signature(token) == expected


class TestPublicAuthRoutes:
    """Smoke tests for the new /v1/auth/* surface.

    These mock out backend HTTP so the facade routes can be exercised in
    isolation. Cross-service end-to-end tests live in the integration suite.
    """

    def _auth_headers(self, monkeypatch) -> dict[str, str]:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        token = _hmac_token(
            {
                "org_id": "org_123",
                "user_id": "user_123",
                "roles": ["employee"],
                "permission_scopes": ["runtime:use"],
            },
            _TEST_SECRET,
        )
        return {"authorization": f"Bearer {token}"}

    def test_get_auth_session_mirrors_legacy_session_response(
        self, monkeypatch
    ) -> None:
        client = TestClient(create_app(FacadeSettings()))

        response = client.get(
            "/v1/auth/session", headers=self._auth_headers(monkeypatch)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["identity"]["org_id"] == "org_123"
        assert body["identity"]["user_id"] == "user_123"

    def test_logout_returns_204_even_without_sid_claim(self, monkeypatch) -> None:
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/auth/logout", headers=self._auth_headers(monkeypatch)
        )
        # No `sid` claim on the test token → logout is a no-op (204) rather
        # than an error: the bearer wasn't bound to a server-side session.
        assert response.status_code == 204

    def test_revoke_session_with_sid_calls_backend(self, monkeypatch) -> None:
        # Token with a sid claim → /v1/auth/sessions/<id> attempts a backend
        # revoke. We mock the upstream HTTP so the test runs offline.
        captured: dict[str, object] = {}

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                import httpx

                return httpx.Response(204)

        monkeypatch.setattr(auth_routes_module.httpx, "AsyncClient", _FakeAsyncClient)
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

        token = _hmac_token(
            {
                "org_id": "org_123",
                "user_id": "user_123",
                "roles": ["employee"],
                "permission_scopes": ["runtime:use"],
                "sid": "sid_target",
            },
            _TEST_SECRET,
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.delete(
            "/v1/auth/sessions/sid_target",
            headers={"authorization": f"Bearer {token}"},
        )

        assert response.status_code == 204
        assert captured["url"].endswith("/internal/v1/auth/sessions/sid_target/revoke")
        assert captured["json"]["org_id"] == "org_123"
