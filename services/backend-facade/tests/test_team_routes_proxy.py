"""Tests for the ``/v1/team/*`` facade proxy (Phase 12 P12-A7).

Mirrors ``test_tool_routes_proxy.py`` setup: HMAC-signed bearer token,
fake httpx.AsyncClient that captures outbound calls. Covers the
non-streaming methods (list, detail, invite, offboard, role patch) and
the SSE stream (Last-Event-ID forwarding + chunk pass-through).
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


_PERSON_BODY = {
    "id": "usr_bob",
    "org_id": "org_acme",
    "primary_email": "bob@acme.com",
    "display_name": "Bob",
    "role": "employee",
}


class TestTeamRoutesProxy:
    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.get("/v1/team").status_code == 401

    def test_list_preserves_multi_value_filters(self, monkeypatch) -> None:
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
                    200, json={"items": [_PERSON_BODY], "next_cursor": None}
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/team?filter[role]=employee&filter[role]=admin",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["items"][0]["id"] == "usr_bob"

        call = captured[0]
        assert call["url"].endswith("/v1/team")
        pairs = call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        assert pairs.count(("filter[role]", "employee")) == 1
        assert pairs.count(("filter[role]", "admin")) == 1

        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert downstream["x-enterprise-user-id"] == "usr_sarah"

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
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(200, json={"person": _PERSON_BODY})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/team/usr_bob", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 200
        assert captured[0]["url"].endswith("/v1/team/usr_bob")
        assert captured[0]["params"]["org_id"] == "org_acme"

    def test_invite_proxies_body(self, monkeypatch) -> None:
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
                return httpx.Response(201, json={"person": _PERSON_BODY})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/team/invite",
            json={"email": "bob@acme.com", "role": "employee"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 201, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/team/invite")
        assert call["json"]["email"] == "bob@acme.com"
        assert call["params"]["org_id"] == "org_acme"

    def test_role_patch_proxies_body(self, monkeypatch) -> None:
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
                return httpx.Response(200, json={**_PERSON_BODY, "role": "admin"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/team/usr_bob/role",
            json={"role": "admin"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/team/usr_bob/role")
        assert call["json"] == {"role": "admin"}

    def test_offboard_proxies(self, monkeypatch) -> None:
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
                return httpx.Response(200, json={"status": "offboarded"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.post(
            "/v1/team/usr_bob/offboard",
            json={"reassign_to_user_id": "usr_sarah"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/team/usr_bob/offboard")
        assert captured[0]["json"]["reassign_to_user_id"] == "usr_sarah"


class TestTeamStreamProxy:
    def test_stream_route_is_registered(self) -> None:
        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/team/stream"
            ),
            None,
        )
        assert match is not None
        assert "GET" in match.methods

    def test_stream_proxies_chunks_in_order(self, monkeypatch) -> None:
        sse_payload = [
            b"event: team.heartbeat\nid: 1\ndata: {}\n\n",
            b'event: team.invited\nid: 2\ndata: {"id":"usr_x"}\n\n',
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
                captured_outbound["stream"] = stream
                return _FakeUpstreamResponse()

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        headers = _bearer_headers(monkeypatch)
        headers["Last-Event-ID"] = "evt_42"

        with client.stream("GET", "/v1/team/stream", headers=headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            received = b"".join(resp.iter_bytes())

        assert received == b"".join(sse_payload)
        downstream = {k.lower(): v for k, v in captured_outbound["headers"].items()}
        assert downstream["last-event-id"] == "evt_42"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert ("org_id", "org_acme") in captured_outbound["params"]
        assert ("user_id", "usr_sarah") in captured_outbound["params"]
