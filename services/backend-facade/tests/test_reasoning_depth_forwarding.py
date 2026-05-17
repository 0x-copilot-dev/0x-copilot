"""Facade must forward ``reasoning_depth`` on ``POST /v1/agent/runs`` verbatim.

The composer's Fast / Balanced / Deep selection rides on the run-start
payload. ``FacadeRunRequest`` declares ``reasoning_depth`` explicitly
because Pydantic's default ``extra='ignore'`` would otherwise silently
drop the field on ``model_dump``, causing the ai-backend to never see
the user's choice. This test pins the pass-through.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


def _bearer(
    *,
    org_id: str = "org_depth_facade",
    user_id: str = "user_depth_facade",
    secret: str = "test-auth-secret",
) -> str:
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


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")


def _install_capturing_forwarder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Intercept ``forward_json`` and capture the outbound JSON body."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "POST" and path == "/v1/agent/runs":
            captured.append(kwargs.get("json", {}))
            return {
                "run_id": "stub",
                "conversation_id": "conv_stub",
                "status": "queued",
            }
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


@pytest.mark.parametrize("depth", ["fast", "balanced", "deep"])
def test_facade_forwards_each_valid_reasoning_depth_verbatim(
    monkeypatch: pytest.MonkeyPatch, depth: str
) -> None:
    """Every literal the api-types union allows reaches ai-backend
    unchanged. Confirms the facade declares the field on its own
    request model (otherwise ``model_dump`` would strip it).
    """

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        "/v1/agent/runs",
        headers={"authorization": _bearer()},
        json={
            "conversation_id": "conv_1",
            "user_input": "go",
            "reasoning_depth": depth,
        },
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]
    assert body.get("reasoning_depth") == depth
    # Identity stamping is preserved.
    assert body.get("org_id") == "org_depth_facade"
    assert body.get("user_id") == "user_depth_facade"


def test_facade_omits_reasoning_depth_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-regression — when the composer doesn't pick a depth, the
    facade does not invent one. ``model_dump(exclude_none=True)`` keeps
    the key out of the upstream payload so ai-backend's default
    behaviour applies.
    """

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        "/v1/agent/runs",
        headers={"authorization": _bearer()},
        json={"conversation_id": "conv_1", "user_input": "go"},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert "reasoning_depth" not in captured[0]
