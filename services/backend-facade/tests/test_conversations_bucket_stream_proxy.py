"""Facade proxy for the PRD-09 Chats surface — bucket/cursor forwarding + SSE.

Two properties (DoD #7):

* ``GET /v1/agent/conversations`` forwards ``bucket`` and ``cursor`` VERBATIM to
  ai-backend alongside the scoped identity.
* ``GET /v1/agent/conversations/stream`` proxies the ai-backend SSE stream: the
  response is ``text/event-stream`` and the upstream body lands on the wire
  unmodified.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_ORG_ID = "org_chats_facade"
_USER_ID = "user_chats_facade"


def _bearer(*, secret: str = "test-auth-secret") -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": _ORG_ID,
                    "user_id": _USER_ID,
                    "roles": ["employee"],
                    "permission_scopes": ["runtime:use"],
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"Bearer {payload}.{signature}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")


def test_bucket_and_cursor_forwarded_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if (
            target == "ai_backend"
            and method == "GET"
            and path == "/v1/agent/conversations"
        ):
            captured.append({"params": kwargs.get("params")})
            return {"conversations": [], "has_more": False}
        raise AssertionError(f"unexpected forward: {target} {method} {path}")

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        "/v1/agent/conversations",
        params={"bucket": "pinned", "cursor": "opaque-cursor", "limit": 1},
        headers={"authorization": _bearer()},
    )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    params = captured[0]["params"]
    assert params["bucket"] == "pinned"
    assert params["cursor"] == "opaque-cursor"
    assert params["org_id"] == _ORG_ID
    assert params["user_id"] == _USER_ID


def test_legacy_list_forwards_no_bucket_or_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        captured.append({"params": kwargs.get("params")})
        return {"conversations": [], "has_more": False}

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        "/v1/agent/conversations", headers={"authorization": _bearer()}
    )
    assert response.status_code == 200
    params = captured[0]["params"]
    assert "bucket" not in params
    assert "cursor" not in params


_SSE_PAYLOAD = [
    b": keepalive\n\n",
    b'event: conversation_changed\nid: abc\ndata: {"event_type":'
    b'"conversation_changed","conversation":{},"cursor":"abc"}\n\n',
]


def _install_fake_stream_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class _FakeUpstreamResponse:
        status_code = 200

        async def aread(self) -> bytes:
            return b""

        async def aclose(self) -> None:
            return None

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            for chunk in _SSE_PAYLOAD:
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

        def build_request(
            self, method, url, *, params=None, headers=None, timeout=None
        ):
            return _FakeRequest(
                method=method,
                url=url,
                params=list(params.items()) if hasattr(params, "items") else [],
                headers=dict(headers) if headers else {},
            )

        async def send(self, request, *, stream=False):
            captured["url"] = str(request.url)
            captured["params"] = request.params
            return _FakeUpstreamResponse()

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)
    return captured


def test_conversations_stream_proxies_sse_unmodified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_stream_client(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    with client.stream(
        "GET",
        "/v1/agent/conversations/stream",
        params={"after": "abc"},
        headers={"authorization": _bearer()},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        received = b"".join(resp.iter_bytes())

    # Upstream body byte-for-byte, targeting ai-backend with the scoped identity.
    assert received == b"".join(_SSE_PAYLOAD)
    assert captured["url"].endswith("/v1/agent/conversations/stream")
    assert ("org_id", _ORG_ID) in captured["params"]
    assert ("user_id", _USER_ID) in captured["params"]
    assert ("after", "abc") in captured["params"]


def test_conversations_stream_requires_bearer() -> None:
    client = TestClient(create_app(FacadeSettings()))
    assert client.get("/v1/agent/conversations/stream").status_code == 401


def test_stream_route_registered_before_conversation_id_template() -> None:
    from starlette.routing import Route

    app = create_app(FacadeSettings())
    get_paths = [
        route.path
        for route in app.routes
        if isinstance(route, Route) and "GET" in (route.methods or set())
    ]
    assert "/v1/agent/conversations/stream" in get_paths
    assert "/v1/agent/conversations/{conversation_id}" in get_paths
    assert get_paths.index("/v1/agent/conversations/stream") < get_paths.index(
        "/v1/agent/conversations/{conversation_id}"
    )
