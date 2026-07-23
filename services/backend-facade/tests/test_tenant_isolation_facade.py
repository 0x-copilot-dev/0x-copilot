"""Tenant-scoped identity through the product facade (bearer -> upstream headers).

This does not re-prove database isolation (see ai-backend and backend tests); it
verifies that each distinct app identity results in a distinct
``AuthenticatedIdentity`` forwarded toward upstream HTTP calls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings
from backend_facade.auth import AuthenticatedIdentity
import backend_facade.app as facade_app


async def _unused_backend(*a, **k):
    raise AssertionError("backend should not have been called")


def _dispatch(backend_fake, ai_fake):
    async def _f(*args, target, **kwargs):
        return await (ai_fake if target == "ai_backend" else backend_fake)(
            *args, **kwargs
        )

    return _f


def _bearer(org_id: str, user_id: str, *, secret: str = "test-auth-secret") -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": org_id,
                    "user_id": user_id,
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


def test_distinct_bearer_tokens_yield_distinct_ai_backend_identity(monkeypatch) -> None:
    """Regression: facade must not reuse one tenant's headers for another."""
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

    captured: list[AuthenticatedIdentity] = []

    async def capture_forward_to_ai(app, method, path, **kwargs):
        captured.append(kwargs["identity"])
        return {"conversations": []}

    monkeypatch.setattr(
        facade_app, "forward_json", _dispatch(_unused_backend, capture_forward_to_ai)
    )
    client = TestClient(create_app(FacadeSettings()))

    client.get(
        "/v1/agent/conversations",
        headers={"authorization": _bearer("org_acme", "user_alice")},
    )
    client.get(
        "/v1/agent/conversations",
        headers={"authorization": _bearer("org_beta", "user_bob")},
    )

    assert len(captured) == 2
    assert captured[0].org_id == "org_acme"
    assert captured[0].user_id == "user_alice"
    assert captured[1].org_id == "org_beta"
    assert captured[1].user_id == "user_bob"


def test_run_history_overrides_client_supplied_tenant(monkeypatch) -> None:
    """PRD-05 — GET /v1/agent/runs forwards the SESSION's org/user, never the
    client-supplied ``?org_id=&user_id=`` query params (which the route does not
    even read). Guards against a caller reading another tenant's run history."""
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

    calls: list[dict[str, object]] = []

    async def capture(app, method, path, *, target, identity, **kwargs):
        calls.append(
            {"path": path, "params": kwargs.get("params"), "identity": identity}
        )
        return {"runs": [], "next_cursor": None, "has_more": False}

    monkeypatch.setattr(facade_app, "forward_json", capture)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        "/v1/agent/runs",
        params={"org_id": "other_org", "user_id": "other_user", "limit": 10},
        headers={"authorization": _bearer("org_acme", "user_alice")},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    call = calls[0]
    assert call["path"] == "/v1/agent/runs"
    # The forwarded params carry the verified session's tenant, not the client's.
    assert call["params"]["org_id"] == "org_acme"
    assert call["params"]["user_id"] == "user_alice"
    assert call["params"]["limit"] == 10
    assert call["identity"].org_id == "org_acme"


def test_active_run_count_overrides_client_supplied_tenant(monkeypatch) -> None:
    """PRD-12 — GET /v1/agent/runs/active_count forwards the SESSION's org/user,
    never the client-supplied ``?org_id=&user_id=`` query params. Guards against
    a caller reading another tenant's in-flight run count, and proves the literal
    path is not shadowed by ``/v1/agent/runs/{run_id}``."""
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

    calls: list[dict[str, object]] = []

    async def capture(app, method, path, *, target, identity, **kwargs):
        calls.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "params": kwargs.get("params"),
                "identity": identity,
            }
        )
        return {"active_run_count": 0}

    monkeypatch.setattr(facade_app, "forward_json", capture)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        "/v1/agent/runs/active_count",
        params={"org_id": "other_org", "user_id": "other_user"},
        headers={"authorization": _bearer("org_acme", "user_alice")},
    )

    assert response.status_code == 200
    assert response.json() == {"active_run_count": 0}
    assert len(calls) == 1
    call = calls[0]
    # The literal path reached the active-count handler, NOT get_run("active_count").
    assert call["method"] == "GET"
    assert call["path"] == "/v1/agent/runs/active_count"
    assert call["target"] == "ai_backend"
    # The forwarded params carry the verified session's tenant, not the client's.
    assert call["params"]["org_id"] == "org_acme"
    assert call["params"]["user_id"] == "user_alice"
    assert call["identity"].org_id == "org_acme"


def test_pin_route_forwards_post_to_ai_backend(monkeypatch) -> None:
    """PRD-H.4 — POST /conversations/{id}/pin proxies to ai-backend as POST."""
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")

    calls: list[dict[str, object]] = []

    async def capture(app, method, path, *, target, identity, **kwargs):
        calls.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "identity": identity,
                "json": kwargs.get("json"),
            }
        )
        return {"conversation_id": "conv_1", "pinned": True}

    monkeypatch.setattr(facade_app, "forward_json", capture)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        "/v1/agent/conversations/conv_1/pin",
        headers={"authorization": _bearer("org_acme", "user_alice")},
        json={"pinned": True},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/agent/conversations/conv_1/pin"
    assert call["target"] == "ai_backend"
    assert call["json"] == {"pinned": True}
    assert call["identity"].org_id == "org_acme"
    assert call["identity"].user_id == "user_alice"
