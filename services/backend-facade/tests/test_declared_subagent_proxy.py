"""Facade boundary tests for the declare-an-agent routes.

The facade is a per-route proxy, so this file is not a formality: it is the
half that makes ``/v1/agent/subagents`` reachable at all. Apps may call nothing
but the facade, which is exactly why ``subagent_defs/*.json`` stayed
undiscoverable while being completely implemented upstream — a route absent
from this table cannot be reached however good the handler behind it is.

The identity assertions are the same ones every other route on this boundary
carries: caller-supplied ``org_id`` in the query string is discarded and the
verified session's own scoped params are what get forwarded.
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

_SECRET = "declared-subagent-proxy-test-secret"
_ORG = "org_declared_proxy"
_USER = "user_declared_proxy"


def _headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "declared-subagent-service-token")
    payload = {
        "org_id": _ORG,
        "user_id": _USER,
        "roles": ["employee"],
        "permission_scopes": ["runtime:use"],
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    signature = hmac.new(_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return {"authorization": f"Bearer {encoded}.{signed}"}


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "session_declared",
            "org_id": _ORG,
            "user_id": _USER,
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


def _client(monkeypatch, captured: list[dict[str, object]], upstream: httpx.Response):
    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url, *, json=None, headers=None, timeout=None):
            return _touch_response()

        async def request(
            self, method, url, *, params=None, json=None, headers=None, timeout=None
        ):
            captured.append(
                {
                    "method": method,
                    "url": str(url),
                    "params": dict(params),
                    "json": json,
                }
            )
            return upstream

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _FakeClient)
    return TestClient(create_app(FacadeSettings(backend_url="http://backend.local")))


def _definition() -> dict[str, object]:
    return {
        "name": "doc-reader",
        "description": "Reads documents and reports what they say.",
        "graph_id": "research_graph",
        "tools": ["read_file"],
    }


def test_declare_forwards_to_ai_backend_with_the_verified_identity(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []
    client = _client(monkeypatch, captured, httpx.Response(200, json=_definition()))

    response = client.put(
        "/v1/agent/subagents/doc-reader?org_id=attacker-org",
        headers=_headers(monkeypatch),
        json=_definition(),
    )

    assert response.status_code == 200
    assert len(captured) == 1
    forwarded = captured[0]
    assert forwarded["method"] == "PUT"
    assert str(forwarded["url"]).endswith("/v1/agent/subagents/doc-reader")
    # The attacker-supplied org_id never reaches upstream.
    assert forwarded["params"] == {"org_id": _ORG, "user_id": _USER}
    assert forwarded["json"] == _definition()


def test_list_forwards_and_returns_the_upstream_body(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    upstream = httpx.Response(200, json={"subagents": [_definition()]})
    client = _client(monkeypatch, captured, upstream)

    response = client.get("/v1/agent/subagents", headers=_headers(monkeypatch))

    assert response.status_code == 200
    assert response.json()["subagents"][0]["tools"] == ["read_file"]
    assert str(captured[0]["url"]).endswith("/v1/agent/subagents")
    assert captured[0]["method"] == "GET"


def test_undeclare_forwards_a_delete_and_answers_204(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    client = _client(monkeypatch, captured, httpx.Response(204))

    response = client.delete(
        "/v1/agent/subagents/doc-reader", headers=_headers(monkeypatch)
    )

    assert response.status_code == 204
    assert captured[0]["method"] == "DELETE"
    assert str(captured[0]["url"]).endswith("/v1/agent/subagents/doc-reader")
