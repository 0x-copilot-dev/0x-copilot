"""Facade contract for the owner-routed Sources v2 opener (E1 D4/D5)."""

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

_ORG_ID = "org_source_open"
_USER_ID = "user_source_open"
_RUN_ID = "run_source_open"
_SOURCE_ID = "source:v2:004:artifact"
_PATH = f"/v1/agent/runs/{_RUN_ID}/sources/{_SOURCE_ID}/open"


def _bearer(secret: str = "test-auth-secret") -> str:
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
                secret.encode("utf-8"),
                payload.encode("ascii"),
                hashlib.sha256,
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


def test_facade_forwards_only_identity_scope_and_empty_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001
        captured.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
            }
        )
        return {
            "v": 2,
            "source_id": _SOURCE_ID,
            "kind": "artifact",
            "disposition": "artifact",
            "artifact_id": "art_safe_target",
            "artifact_revision": 2,
            "artifact_kind": "document",
        }

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"{_PATH}?org_id=attacker&user_id=attacker",
        headers={"authorization": _bearer()},
        json={"path": "/private/should-not-forward", "cookie": "secret"},
    )

    assert response.status_code == 200
    assert captured == [
        {
            "method": "POST",
            "path": _PATH,
            "target": "ai_backend",
            "params": {"org_id": _ORG_ID, "user_id": _USER_ID},
            "json": {},
        }
    ]


def test_missing_bearer_never_reaches_ai_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def _forward(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))
        raise AssertionError("must not forward without a verified bearer")

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(_PATH)

    assert response.status_code == 401
    assert calls == []


def test_upstream_not_found_is_preserved_without_facade_reinterpretation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _forward(*_args, **_kwargs):
        raise HTTPException(
            status_code=404, detail="Source is not available for this scope."
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(_PATH, headers={"authorization": _bearer()})

    assert response.status_code == 404
