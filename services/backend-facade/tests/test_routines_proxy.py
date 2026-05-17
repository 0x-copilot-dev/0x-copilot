"""Tests for the public ``/v1/routines`` facade proxy (Phase 5 P5-A1).

Mirrors ``test_inbox_proxy.py`` setup. Asserts:

* Unauthenticated request rejected (401).
* Authenticated request proxies to backend with the verified identity.
* Multi-value ``filter[status]`` query params survive the proxy
  (cross-audit §1.5 OR semantics).
* Upstream 4xx propagates through.
* POST + PATCH + DELETE + POST /run all reach the right backend route.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

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


def _bearer_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    token = _hmac_token(
        {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
        },
        _TEST_SECRET,
    )
    return {"authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


_ROUTINE_BODY = {
    "id": "rt_1",
    "tenant_id": "org_acme",
    "owner_user_id": "usr_sarah",
    "project_id": None,
    "name": "Daily standup digest",
    "instructions": "Summarise yesterday's standup transcripts.",
    "agent_id": "agent_atlas",
    "agent_version_pin": None,
    "triggers": [{"kind": "cron", "spec": "0 9 * * 1-5"}],
    "connectors_scope": None,
    "behavior": None,
    "permissions": {"manual_fire": "owner"},
    "code": None,
    "status": "draft",
    "pause_reason": None,
    "missed_fire_policy": "fire_once",
    "created_at": "2026-05-18T00:00:00+00:00",
    "updated_at": "2026-05-18T00:00:00+00:00",
}

_LIST_BODY = {"items": [_ROUTINE_BODY], "next_cursor": None}


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "sid_test",
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
            "connector_scopes": {},
            "mfa_satisfied": False,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )


class TestRoutinesProxy:
    def test_get_list_proxies_to_backend_preserving_multi_value_filter(
        self, monkeypatch
    ) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                captured.append(
                    {
                        "method": "GET",
                        "url": url,
                        "params": list(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(200, json=_LIST_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/routines?filter[status]=active&filter[status]=paused",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json() == _LIST_BODY

        get_call = next(c for c in captured if c["method"] == "GET")
        assert get_call["url"].endswith("/v1/routines")
        pairs = get_call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        # Multi-value preserved.
        assert pairs.count(("filter[status]", "active")) == 1
        assert pairs.count(("filter[status]", "paused")) == 1

        downstream_headers = {k.lower(): v for k, v in get_call["headers"].items()}
        assert downstream_headers["x-enterprise-service-token"] == (
            "test-service-token"
        )
        assert downstream_headers["x-enterprise-org-id"] == "org_acme"
        assert downstream_headers["x-enterprise-user-id"] == "usr_sarah"

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/routines")
        assert resp.status_code == 401

    def test_create_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch") or json is None:
                    return _touch_response()
                captured.append(
                    {"url": url, "json": json, "params": dict(params or {})}
                )
                return httpx.Response(201, json=_ROUTINE_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/routines",
            json={
                "name": "Daily standup digest",
                "instructions": "x",
                "agent_id": "agent_atlas",
                "triggers": [{"kind": "cron", "spec": "0 9 * * 1-5"}],
            },
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 201, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/routines")
        assert call["json"]["name"] == "Daily standup digest"
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"

    def test_patch_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append({"url": url, "json": json, "params": dict(params)})
                return httpx.Response(200, json={**_ROUTINE_BODY, "status": "active"})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/routines/rt_1",
            json={"status": "active"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        call = captured[0]
        assert call["json"] == {"status": "active"}
        assert call["params"]["org_id"] == "org_acme"

    def test_delete_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def delete(self, url, *, params, headers=None, timeout=None):
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(204)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete("/v1/routines/rt_1", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 204
        assert captured[0]["url"].endswith("/v1/routines/rt_1")

    def test_manual_fire_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params, headers, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append(
                    {"url": url, "json": json, "params": dict(params or {})}
                )
                return httpx.Response(200, json={"fire_id": "rfire_x", "run_id": None})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/routines/rt_1/run", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fire_id"] == "rfire_x"
        call = captured[0]
        assert call["url"].endswith("/v1/routines/rt_1/run")
        assert call["params"]["org_id"] == "org_acme"

    def test_upstream_404_propagates(self, monkeypatch) -> None:
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return httpx.Response(404, json={"detail": "routine_not_found"})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/routines/rt_unknown", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 404
