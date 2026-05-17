"""Tests for the facade's REQUIRE_SESSION_BINDING gate (A2)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from datetime import datetime, timedelta, timezone

from backend_facade.auth import (
    AuthenticatedIdentity,
    FacadeAuthenticator,
    SessionRevoked,
    StepUpRequired,
    _TouchCache,
    requires_recent_mfa,
)
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


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


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
        # Revoke now does verify_with_touch (cache_bypass=True) before the
        # actual revoke, so we expect TWO upstream calls: /touch then /revoke.
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                captured.append({"url": url, "json": json, "headers": dict(headers)})
                if url.endswith("/touch"):
                    return httpx.Response(
                        200,
                        json={
                            "session_id": "sid_target",
                            "org_id": "org_123",
                            "user_id": "user_123",
                            "roles": ["employee"],
                            "permission_scopes": ["runtime:use"],
                            "connector_scopes": {},
                            "mfa_satisfied": False,
                            "expires_at": "2099-01-01T00:00:00+00:00",
                        },
                    )
                return httpx.Response(204)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )
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
        assert len(captured) == 2
        assert captured[0]["url"].endswith("/internal/v1/auth/sessions/touch")
        assert captured[1]["url"].endswith(
            "/internal/v1/auth/sessions/sid_target/revoke"
        )
        assert captured[1]["json"]["org_id"] == "org_123"


class TestTouchCache:
    """Behavior of the per-process LRU cache for the session touch result."""

    def test_get_returns_none_on_first_lookup(self) -> None:
        cache = _TouchCache()
        assert cache.get(token_hash="abc") is None
        assert cache.misses == 1
        assert cache.hits == 0

    def test_put_then_get_returns_cached_identity(self) -> None:
        cache = _TouchCache()
        identity = AuthenticatedIdentity(org_id="org_a", user_id="usr_a")
        cache.put(token_hash="abc", identity=identity)
        assert cache.get(token_hash="abc") == identity
        assert cache.hits == 1

    def test_bucket_rollover_invalidates_entry(self) -> None:
        cache = _TouchCache(ttl_seconds=30)
        identity = AuthenticatedIdentity(org_id="org_a", user_id="usr_a")
        cache.put(token_hash="abc", identity=identity, now=0.0)
        # Same bucket — hit.
        assert cache.get(token_hash="abc", now=29.999) == identity
        # Next bucket — miss (new TTL window forces fresh touch).
        assert cache.get(token_hash="abc", now=30.0) is None

    def test_size_bound_evicts_oldest(self) -> None:
        cache = _TouchCache(max_size=2)
        first = AuthenticatedIdentity(org_id="org_a", user_id="u1")
        second = AuthenticatedIdentity(org_id="org_a", user_id="u2")
        third = AuthenticatedIdentity(org_id="org_a", user_id="u3")
        cache.put(token_hash="t1", identity=first, now=0.0)
        cache.put(token_hash="t2", identity=second, now=0.0)
        cache.put(token_hash="t3", identity=third, now=0.0)
        assert cache.get(token_hash="t1", now=0.0) is None
        assert cache.get(token_hash="t2", now=0.0) == second
        assert cache.get(token_hash="t3", now=0.0) == third

    def test_invalidate_drops_all_buckets_for_token(self) -> None:
        cache = _TouchCache(ttl_seconds=30)
        identity = AuthenticatedIdentity(org_id="org_a", user_id="usr_a")
        cache.put(token_hash="abc", identity=identity, now=0.0)
        cache.put(token_hash="abc", identity=identity, now=60.0)
        cache.invalidate(token_hash="abc")
        assert cache.get(token_hash="abc", now=0.0) is None
        assert cache.get(token_hash="abc", now=60.0) is None


class TestVerifyWithTouch:
    """End-to-end behavior of the facade's verify-with-touch + cache."""

    def _token(self) -> str:
        return _hmac_token(
            {
                "org_id": "org_123",
                "user_id": "user_123",
                "roles": ["employee"],
                "permission_scopes": ["runtime:use"],
                "sid": "sid_test",
            },
            _TEST_SECRET,
        )

    def test_back_compat_token_skips_touch_call(self, monkeypatch) -> None:
        # Token without `sid` → no touch HTTP call should be made.
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        captured: list[str] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                captured.append(url)
                return httpx.Response(204)

            async def get(self, url, *, params, headers, timeout=None):
                captured.append(url)
                return httpx.Response(200, json={"sessions": []})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )
        token = _hmac_token(
            {"org_id": "org_a", "user_id": "usr_a"},  # no sid
            _TEST_SECRET,
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get(
            "/v1/auth/sessions",
            headers={"authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        # Only the list call upstream — no touch because no sid.
        assert captured == ["http://backend.local/internal/v1/auth/sessions"]

    def test_revoked_session_propagates_401(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                if url.endswith("/touch"):
                    return httpx.Response(401, json={"detail": "revoked"})
                return httpx.Response(204)

            async def get(self, url, *, params, headers, timeout=None):
                # Should never reach here — touch fails first.
                return httpx.Response(500)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get(
            "/v1/auth/sessions",
            headers={"authorization": f"Bearer {self._token()}"},
        )
        assert response.status_code == 401

    def test_cache_bypass_forces_fresh_touch_on_revoke(self, monkeypatch) -> None:
        # Even when a fresh touch result is cached, the revoke route should
        # bypass it and re-touch (so revocation can't ride a stale window).
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

        token = self._token()
        token_hash = FacadeAuthenticator.token_hash_from_signature(token)
        cached = AuthenticatedIdentity(org_id="org_123", user_id="user_123")
        FacadeAuthenticator.touch_cache().put(token_hash=token_hash, identity=cached)

        touch_calls: list[str] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                touch_calls.append(url)
                if url.endswith("/touch"):
                    return httpx.Response(
                        200,
                        json={
                            "session_id": "sid_test",
                            "org_id": "org_123",
                            "user_id": "user_123",
                            "roles": ["employee"],
                            "permission_scopes": ["runtime:use"],
                            "connector_scopes": {},
                            "mfa_satisfied": False,
                            "expires_at": "2099-01-01T00:00:00+00:00",
                        },
                    )
                return httpx.Response(204)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.delete(
            "/v1/auth/sessions/sid_test",
            headers={"authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        # /touch was called even with a cached entry — cache_bypass=True.
        assert any(url.endswith("/touch") for url in touch_calls)


class TestSessionRevokedException:
    def test_session_revoked_is_401(self) -> None:
        exc = SessionRevoked()
        assert exc.status_code == 401


class TestRequiresRecentMfa:
    def _now(self) -> datetime:
        return datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    def _identity(
        self,
        *,
        sid: str | None = "sid_a",
        mfa_satisfied_at: datetime | None = None,
    ) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            org_id="org_a",
            user_id="usr_a",
            session_id=sid,
            mfa_satisfied_at=mfa_satisfied_at,
        )

    def test_no_op_when_max_age_zero(self) -> None:
        # Defensive — ``max_age_seconds=0`` means "no step-up requirement".
        requires_recent_mfa(self._identity(mfa_satisfied_at=None), max_age_seconds=0)

    def test_skips_back_compat_tokens_without_sid(self) -> None:
        # Back-compat / dev-bypass tokens have no session row; the gate
        # must not 403 them or every dev login breaks.
        requires_recent_mfa(
            self._identity(sid=None, mfa_satisfied_at=None),
            max_age_seconds=300,
        )

    def test_raises_when_mfa_never_satisfied(self) -> None:
        with pytest.raises(StepUpRequired) as exc:
            requires_recent_mfa(
                self._identity(mfa_satisfied_at=None),
                max_age_seconds=300,
                now=self._now(),
            )
        assert exc.value.status_code == 403
        assert "x-step-up" in exc.value.headers["WWW-Authenticate"]
        assert 'max_age="300"' in exc.value.headers["WWW-Authenticate"]

    def test_passes_when_mfa_within_window(self) -> None:
        recent = self._now() - timedelta(seconds=120)
        requires_recent_mfa(
            self._identity(mfa_satisfied_at=recent),
            max_age_seconds=300,
            now=self._now(),
        )

    def test_raises_when_mfa_outside_window(self) -> None:
        stale = self._now() - timedelta(seconds=900)
        with pytest.raises(StepUpRequired) as exc:
            requires_recent_mfa(
                self._identity(mfa_satisfied_at=stale),
                max_age_seconds=300,
                now=self._now(),
            )
        assert exc.value.status_code == 403
        assert "elapsed=900s" in exc.value.detail


class TestPasswordChangeStepUp:
    """The password-change route is the canonical step-up consumer."""

    def _make_token(self, *, sid: str | None) -> str:
        payload: dict[str, object] = {
            "org_id": "org_123",
            "user_id": "user_123",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
        }
        if sid is not None:
            payload["sid"] = sid
        return _hmac_token(payload, _TEST_SECRET)

    def _fake_async_client(self, *, mfa_satisfied_at: str | None):
        captured: list[str] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                captured.append(url)
                if url.endswith("/touch"):
                    return httpx.Response(
                        200,
                        json={
                            "session_id": "sid_target",
                            "org_id": "org_123",
                            "user_id": "user_123",
                            "roles": ["employee"],
                            "permission_scopes": ["runtime:use"],
                            "connector_scopes": {},
                            "mfa_satisfied": mfa_satisfied_at is not None,
                            "mfa_satisfied_at": mfa_satisfied_at,
                            "expires_at": "2099-01-01T00:00:00+00:00",
                        },
                    )
                return httpx.Response(204)

        return _FakeAsyncClient, captured

    def test_returns_403_with_step_up_header_when_mfa_stale(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        # mfa_satisfied_at = far in the past → outside the 300s window.
        client_class, _ = self._fake_async_client(
            mfa_satisfied_at="2000-01-01T00:00:00+00:00"
        )
        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", client_class
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.post(
            "/v1/auth/password/change",
            headers={"authorization": f"Bearer {self._make_token(sid='sid_target')}"},
            json={
                "current_password": "old-password",
                "new_password": "new-password",
            },
        )
        assert response.status_code == 403
        assert "x-step-up" in response.headers.get("www-authenticate", "")

    def test_passes_when_mfa_recent(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        client_class, captured = self._fake_async_client(mfa_satisfied_at=recent)
        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", client_class
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.post(
            "/v1/auth/password/change",
            headers={"authorization": f"Bearer {self._make_token(sid='sid_target')}"},
            json={
                "current_password": "old-password",
                "new_password": "new-password",
            },
        )
        assert response.status_code == 204
        # Both upstream calls fired: /touch + /password/change.
        assert any(url.endswith("/touch") for url in captured)
        assert any(
            url.endswith("/internal/v1/auth/password/change") for url in captured
        )
