"""Facade identity propagation for canonical E1 D6 pending work."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_ORG_ID = "org_pending_v2_facade"
_USER_ID = "user_pending_v2_facade"
_PATH = "/v1/agent/pending-work-v2"


def _bearer(
    *,
    org_id: str = _ORG_ID,
    user_id: str = _USER_ID,
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


def test_facade_stamps_identity_and_forwards_only_paging_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        captured.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "params": kwargs["params"],
            }
        )
        return {
            "v": 2,
            "items": [],
            "warnings": [],
            "next_cursor": None,
            "has_more": False,
        }

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        f"{_PATH}?limit=7&cursor=opaque-cursor&org_id=attacker&user_id=attacker",
        headers={"authorization": _bearer()},
    )

    assert response.status_code == 200, response.text
    assert response.json()["v"] == 2
    assert captured == [
        {
            "method": "GET",
            "path": _PATH,
            "target": "ai_backend",
            "params": {
                "org_id": _ORG_ID,
                "user_id": _USER_ID,
                "limit": 7,
                "cursor": "opaque-cursor",
            },
        }
    ]


def test_facade_requires_bearer() -> None:
    response = TestClient(create_app(FacadeSettings())).get(_PATH)
    assert response.status_code == 401


def test_facade_preserves_upstream_flag_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        raise HTTPException(status_code=404, detail="Not Found")

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    response = TestClient(create_app(FacadeSettings())).get(
        _PATH, headers={"authorization": _bearer()}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
