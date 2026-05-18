"""Tests for the ``/v1/memory/*`` facade proxy (Phase 12 P12-A7).

Mirrors ``test_tool_routes_proxy.py`` setup. Covers list, detail,
create, patch, delete, search, touch, proposals (list / accept /
reject), and the SSE stream.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

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


_MEMORY_BODY = {
    "id": "mem_abc",
    "tenant_id": "org_acme",
    "owner_user_id": "usr_sarah",
    "scope": "personal",
    "title": "Likes dark mode",
    "body": "User prefers dark mode in chat",
}


class TestMemoryRoutesProxy:
    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.get("/v1/memory").status_code == 401

    def test_list_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
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
                    {"url": url, "params": list(params), "headers": dict(headers)}
                )
                return httpx.Response(
                    200, json={"items": [_MEMORY_BODY], "next_cursor": None}
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/memory", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        call = captured[0]
        assert call["url"].endswith("/v1/memory")
        pairs = call["params"]
        assert ("org_id", "org_acme") in pairs

        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"

    def test_search_preserves_query(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                captured.append({"url": url, "params": list(params)})
                return httpx.Response(200, json={"items": []})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/memory/search?q=dark+mode&limit=10",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        call = captured[0]
        assert call["url"].endswith("/v1/memory/search")
        pairs = call["params"]
        assert ("q", "dark mode") in pairs
        assert ("limit", "10") in pairs

    def test_detail_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                captured.append({"url": url})
                return httpx.Response(200, json=_MEMORY_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/memory/mem_abc", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        assert captured[0]["url"].endswith("/v1/memory/mem_abc")

    def test_create_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch") or json is None:
                    return _touch_response()
                captured.append(
                    {"url": url, "json": json, "params": dict(params or {})}
                )
                return httpx.Response(201, json=_MEMORY_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/memory",
            json={"scope": "personal", "title": "x", "body": "y"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 201, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/memory")
        assert call["json"]["scope"] == "personal"

    def test_patch_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append({"url": url, "json": json})
                return httpx.Response(200, json={**_MEMORY_BODY, "scope": "workspace"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/memory/mem_abc",
            json={"scope": "workspace"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/memory/mem_abc")
        assert captured[0]["json"] == {"scope": "workspace"}

    def test_delete_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def delete(self, url, *, params, headers=None, timeout=None):
                captured.append({"url": url})
                return httpx.Response(204)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete("/v1/memory/mem_abc", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 204
        assert captured[0]["url"].endswith("/v1/memory/mem_abc")

    def test_touch_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append({"url": url, "json": json})
                return httpx.Response(
                    200, json={"last_used_at": "2026-05-18T00:00:00+00:00"}
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/memory/mem_abc/touch", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/memory/mem_abc/touch")

    def test_proposals_accept_proxies(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append({"url": url, "json": json})
                return httpx.Response(200, json={"status": "accepted"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/memory/proposals/prop_1/accept",
            json={"scope": "personal"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/memory/proposals/prop_1/accept")


class TestMemoryStreamProxy:
    def test_stream_route_is_registered(self) -> None:
        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/memory/stream"
            ),
            None,
        )
        assert match is not None
        assert "GET" in match.methods

    def test_stream_proxies_chunks(self, monkeypatch) -> None:
        sse_payload = [
            b"event: memory.heartbeat\nid: 1\ndata: {}\n\n",
            b'event: memory.created\nid: 2\ndata: {"id":"mem_x"}\n\n',
        ]
        captured_outbound: dict[str, Any] = {}

        class _FakeUpstreamResponse:
            status_code = 200

            async def aread(self) -> bytes:
                return b""

            async def aclose(self) -> None:
                return None

            async def aiter_bytes(self) -> AsyncIterator[bytes]:
                for chunk in sse_payload:
                    yield chunk

        class _FakeRequest:
            def __init__(self, *, method, url, params, headers) -> None:
                self.method = method
                self.url = url
                self.params = params
                self.headers = headers

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            def build_request(
                self, method, url, *, params=None, headers=None, timeout=None
            ):
                return _FakeRequest(
                    method=method,
                    url=url,
                    params=list(params) if params else [],
                    headers=dict(headers) if headers else {},
                )

            async def send(self, request, *, stream=False):
                captured_outbound["url"] = request.url
                captured_outbound["params"] = request.params
                captured_outbound["headers"] = request.headers
                return _FakeUpstreamResponse()

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        headers = _bearer_headers(monkeypatch)
        headers["Last-Event-ID"] = "evt_42"

        with client.stream("GET", "/v1/memory/stream", headers=headers) as resp:
            assert resp.status_code == 200
            received = b"".join(resp.iter_bytes())

        assert received == b"".join(sse_payload)
        downstream = {k.lower(): v for k, v in captured_outbound["headers"].items()}
        assert downstream["last-event-id"] == "evt_42"
        assert ("org_id", "org_acme") in captured_outbound["params"]
