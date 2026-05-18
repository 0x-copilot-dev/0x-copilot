"""Tests for the public ``/v1/tools/*`` facade proxy (Phase 10 P10-A4).

Mirrors ``test_routines_proxy.py`` setup: HMAC-signed bearer token, fake
``httpx.AsyncClient`` that captures outbound calls. Asserts that every
method (GET / POST / PATCH / DELETE / SSE) reaches the backend with the
verified identity in query params + service-token headers, and that
upstream errors propagate.

The SSE smoke test exercises the streaming proxy end-to-end: we plug a
fake ``send(stream=True)`` that yields ``aiter_bytes`` chunks and assert
that the facade returns a ``StreamingResponse`` with ``text/event-stream``
content-type, the chunks arrive in order, and the ``Last-Event-ID``
header is forwarded.
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


_TOOL_BODY = {
    "id": "tool_abc",
    "tenant_id": "org_acme",
    "owner_user_id": "usr_sarah",
    "kind": "mcp",
    "scope": "tenant",
    "status": "active",
    "name": "Linear: create_issue",
    "description": "Creates an issue in Linear.",
    "args_schema": {},
    "returns_schema": {},
    "tags": [],
    "calls_30d": 0,
    "created_at": "2026-05-18T00:00:00+00:00",
    "updated_at": "2026-05-18T00:00:00+00:00",
}

_LIST_BODY = {"items": [_TOOL_BODY], "next_cursor": None}


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


# ---------------------------------------------------------------------------
# Non-streaming methods (GET / POST / PATCH / DELETE)
# ---------------------------------------------------------------------------


class TestToolRoutesProxy:
    """Each method gets one happy-path assertion against backend forwarding."""

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.get("/v1/tools").status_code == 401

    def test_get_list_preserves_multi_value_filter(self, monkeypatch) -> None:
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
                    {
                        "method": "GET",
                        "url": url,
                        "params": list(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(200, json=_LIST_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/tools?filter[kind]=mcp&filter[kind]=openapi&sort=name",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json() == _LIST_BODY

        call = next(c for c in captured if c["method"] == "GET")
        assert call["url"].endswith("/v1/tools")
        pairs = call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        # Multi-value preserved (OR semantics).
        assert pairs.count(("filter[kind]", "mcp")) == 1
        assert pairs.count(("filter[kind]", "openapi")) == 1
        assert ("sort", "name") in pairs

        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert downstream["x-enterprise-user-id"] == "usr_sarah"

    def test_get_detail_proxies(self, monkeypatch) -> None:
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
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(200, json=_TOOL_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/tools/tool_abc", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        assert resp.json() == _TOOL_BODY
        assert captured[0]["url"].endswith("/v1/tools/tool_abc")
        assert captured[0]["params"]["org_id"] == "org_acme"

    def test_register_proxies_body(self, monkeypatch) -> None:
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
                return httpx.Response(201, json=_TOOL_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/tools",
            json={
                "kind": "mcp",
                "name": "Linear: create_issue",
                "description": "x",
                "scope": "tenant",
                "args_schema": {},
                "returns_schema": {},
                "transport": {"kind": "mcp"},
            },
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 201, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/tools")
        assert call["json"]["kind"] == "mcp"
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"

    def test_patch_proxies_body(self, monkeypatch) -> None:
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
                captured.append({"url": url, "json": json, "params": dict(params)})
                return httpx.Response(200, json={**_TOOL_BODY, "name": "renamed"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/tools/tool_abc",
            json={"name": "renamed"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/tools/tool_abc")
        assert call["json"] == {"name": "renamed"}
        assert call["params"]["org_id"] == "org_acme"

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
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(204)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete("/v1/tools/tool_abc", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 204
        assert captured[0]["url"].endswith("/v1/tools/tool_abc")
        assert captured[0]["params"]["org_id"] == "org_acme"

    def test_test_call_proxies_args(self, monkeypatch) -> None:
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
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "result": {"issue_id": "lin-42"},
                        "latency_ms": 120,
                    },
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/tools/tool_abc/test",
            json={"args": {"title": "x"}},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"]["issue_id"] == "lin-42"
        call = captured[0]
        assert call["url"].endswith("/v1/tools/tool_abc/test")
        assert call["json"] == {"args": {"title": "x"}}

    def test_disable_and_enable_proxy(self, monkeypatch) -> None:
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
                captured.append(
                    {"url": url, "json": json, "params": dict(params or {})}
                )
                return httpx.Response(200, json={**_TOOL_BODY, "status": "disabled"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        d = client.post(
            "/v1/tools/tool_abc/disable",
            json={"reason": "stale"},
            headers=_bearer_headers(monkeypatch),
        )
        assert d.status_code == 200, d.text
        e = client.post(
            "/v1/tools/tool_abc/enable",
            json={},
            headers=_bearer_headers(monkeypatch),
        )
        assert e.status_code == 200, e.text
        # Two backend calls captured; one for disable, one for enable.
        urls = [c["url"] for c in captured]
        assert any(u.endswith("/v1/tools/tool_abc/disable") for u in urls)
        assert any(u.endswith("/v1/tools/tool_abc/enable") for u in urls)

    def test_invocations_history_preserves_filters(self, monkeypatch) -> None:
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
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append({"url": url, "params": list(params)})
                return httpx.Response(200, json={"items": [], "next_cursor": None})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/tools/tool_abc/invocations?status=error&limit=10",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        call = captured[0]
        assert call["url"].endswith("/v1/tools/tool_abc/invocations")
        pairs = call["params"]
        assert ("status", "error") in pairs
        assert ("limit", "10") in pairs
        assert ("org_id", "org_acme") in pairs

    def test_usage_projection_proxies(self, monkeypatch) -> None:
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
                if url.endswith("/v1/identity/touch"):
                    return _touch_response()
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(
                    200,
                    json={"calls_24h": 1, "calls_7d": 5, "calls_30d": 12},
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/tools/tool_abc/usage", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json()["calls_30d"] == 12
        assert captured[0]["url"].endswith("/v1/tools/tool_abc/usage")

    def test_upstream_404_propagates(self, monkeypatch) -> None:
        """Backend 404 must reach the caller unchanged — existence-not-leaked."""

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
                return httpx.Response(404, json={"detail": "tool_not_found"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/tools/tool_unknown", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "tool_not_found"


# ---------------------------------------------------------------------------
# SSE stream proxy
# ---------------------------------------------------------------------------


class TestToolStreamProxy:
    """End-to-end smoke for ``GET /v1/tools/stream`` — bytes pass through."""

    def test_stream_route_is_registered(self) -> None:
        """``GET /v1/tools/stream`` must be mounted on the facade.

        Belt-and-braces: route-registration smoke matches the home /
        inbox style. The next test below actually drives the bytes.
        """

        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/tools/stream"
            ),
            None,
        )
        assert match is not None, "/v1/tools/stream not registered on facade"
        assert "GET" in match.methods

    def test_stream_proxies_chunks_in_order(self, monkeypatch) -> None:
        """Drive the SSE proxy end-to-end.

        We fake ``build_request`` + ``send(stream=True)`` so the route's
        pass-through ``async for chunk in upstream.aiter_bytes()`` loop
        runs against canned SSE frames. Asserts:
        - content-type is text/event-stream
        - chunks arrive in order
        - Last-Event-ID forwards upstream
        """

        sse_payload = [
            b"event: tool.heartbeat\nid: 1\ndata: {}\n\n",
            b'event: tool.created\nid: 2\ndata: {"id":"tool_x"}\n\n',
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
            """Stand-in for ``httpx.Request`` — only carries metadata
            captured by our fake ``send()``."""

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
                captured_outbound["stream"] = stream
                return _FakeUpstreamResponse()

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        headers = _bearer_headers(monkeypatch)
        headers["Last-Event-ID"] = "evt_42"

        with client.stream("GET", "/v1/tools/stream", headers=headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            received = b"".join(resp.iter_bytes())

        # Order preserved + frames concatenated byte-for-byte.
        assert received == b"".join(sse_payload)

        # ``Last-Event-ID`` and identity headers were forwarded upstream.
        downstream = {k.lower(): v for k, v in captured_outbound["headers"].items()}
        assert downstream["last-event-id"] == "evt_42"
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert downstream["x-enterprise-user-id"] == "usr_sarah"

        # Upstream URL + identity query params correct.
        assert str(captured_outbound["url"]).endswith("/v1/tools/stream")
        assert ("org_id", "org_acme") in captured_outbound["params"]
        assert ("user_id", "usr_sarah") in captured_outbound["params"]

        # Streamed flag set on the outbound call.
        assert captured_outbound["stream"] is True

    def test_stream_upstream_error_propagates(self, monkeypatch) -> None:
        """If the backend returns a 4xx on stream open, surface it as HTTP."""

        class _FakeErrorResponse:
            status_code = 403

            async def aread(self) -> bytes:
                return b'{"detail":"tools_destination_disabled"}'

            async def aclose(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"detail": "tools_destination_disabled"}

            @property
            def text(self) -> str:
                return '{"detail":"tools_destination_disabled"}'

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
                return object()

            async def send(self, request, *, stream=False):
                return _FakeErrorResponse()

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/tools/stream", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 403
        assert resp.json()["detail"] == "tools_destination_disabled"
