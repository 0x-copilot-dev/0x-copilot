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
