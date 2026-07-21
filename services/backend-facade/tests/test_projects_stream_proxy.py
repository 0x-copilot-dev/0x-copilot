"""Tests for the public ``GET /v1/projects/stream`` facade proxy (PRD-H FR-H.2).

The facade is a thin pass-through onto ``services/backend``
``GET /v1/projects/stream`` — it forwards upstream SSE chunks unmodified
so the framing (``event:``/``id:``/``data:``) lands on the wire
byte-for-byte. Mirrors ``test_memory_routes_proxy.py::TestMemoryStreamProxy``.

Asserts:

* The route is registered and matches ``/v1/projects/stream`` (literal
  path wins over the ``/v1/projects/{project_id}`` template).
* Authenticated request opens the upstream stream with the verified
  identity in query params + service-token headers, forwards
  ``Last-Event-ID``, and streams chunks through unchanged.
* Unauthenticated request is rejected (401) before any upstream open.
* An upstream 4xx on open propagates through as an error.
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


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


class TestProjectsStreamProxy:
    def test_stream_route_is_registered(self) -> None:
        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/projects/stream"
            ),
            None,
        )
        assert match is not None
        assert "GET" in match.methods

    def test_stream_route_registered_before_project_id_template(self) -> None:
        """The literal ``/stream`` path must be registered before the
        ``/{project_id}`` template so ``project_id`` never captures
        ``stream``."""

        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        get_paths = [
            route.path
            for route in app.routes
            if isinstance(route, Route) and "GET" in (route.methods or set())
        ]
        assert "/v1/projects/stream" in get_paths
        assert "/v1/projects/{project_id}" in get_paths
        assert get_paths.index("/v1/projects/stream") < get_paths.index(
            "/v1/projects/{project_id}"
        )

    def test_stream_proxies_chunks(self, monkeypatch) -> None:
        sse_payload = [
            b": keepalive\n\n",
            b'event: project_event\nid: 1\ndata: {"sequence_no":1,'
            b'"event_type":"project_updated","project_id":"prj_x",'
            b'"payload":{},"emitted_at":"2026-07-21T00:00:00+00:00"}\n\n',
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
                    params=(
                        list(params.items())
                        if hasattr(params, "items")
                        else list(params)
                        if params
                        else []
                    ),
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
        headers["Last-Event-ID"] = "5"

        with client.stream("GET", "/v1/projects/stream", headers=headers) as resp:
            assert resp.status_code == 200
            received = b"".join(resp.iter_bytes())

        assert received == b"".join(sse_payload)
        assert str(captured_outbound["url"]).endswith("/v1/projects/stream")
        downstream = {k.lower(): v for k, v in captured_outbound["headers"].items()}
        assert downstream["last-event-id"] == "5"
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert ("org_id", "org_acme") in captured_outbound["params"]
        assert ("user_id", "usr_sarah") in captured_outbound["params"]

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/projects/stream")
        assert resp.status_code == 401

    def test_upstream_error_propagates(self, monkeypatch) -> None:
        class _FakeUpstreamResponse:
            status_code = 403

            async def aread(self) -> bytes:
                return b'{"detail":"forbidden"}'

            async def aclose(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"detail": "forbidden"}

            @property
            def text(self) -> str:
                return '{"detail":"forbidden"}'

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
                    params=(
                        list(params.items())
                        if hasattr(params, "items")
                        else list(params)
                        if params
                        else []
                    ),
                    headers=dict(headers) if headers else {},
                )

            async def send(self, request, *, stream=False):
                return _FakeUpstreamResponse()

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/projects/stream", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 403
