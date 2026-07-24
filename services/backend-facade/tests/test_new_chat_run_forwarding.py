"""Facade must accept a new-chat run-start that OMITS ``conversation_id`` and
forward its ``conversation_idempotency_key`` verbatim.

desktop-run-identity §D3: a first send from the standby / RunEmptyComposer has no
conversation yet — the client POSTs ``/v1/agent/runs`` WITHOUT ``conversation_id``
and instead carries a stable ``conversation_idempotency_key``; ai-backend's route
get-or-creates the conversation atomically.

``FacadeRunRequest`` had ``conversation_id: str`` (required) and did not declare
``conversation_idempotency_key`` — so the facade 422'd the new-chat send before
ever proxying (the "Couldn't start the run. Is the backend running and a model
configured?" bug), and even with an optional id the key would be dropped on
``model_dump`` by Pydantic's ``extra='ignore'``. These tests pin BOTH halves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient
import pytest

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


def _bearer(
    *,
    org_id: str = "org_newchat_facade",
    user_id: str = "user_newchat_facade",
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
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "POST" and path == "/v1/agent/runs":
            captured.append(kwargs.get("json", {}))
            return {
                "run_id": "stub",
                "conversation_id": "conv_created",
                "status": "queued",
            }
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def test_facade_accepts_new_chat_send_without_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run-start that omits conversation_id but carries a
    conversation_idempotency_key must be accepted (not 422) and the key
    must reach ai-backend so its ensure-conversation step can fire."""

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        "/v1/agent/runs",
        headers={"authorization": _bearer()},
        json={
            "user_input": "is 89 prime?",
            "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
            "conversation_idempotency_key": "idem-abc-123",
        },
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]
    # The key is forwarded (declared on FacadeRunRequest, so model_dump keeps it).
    assert body.get("conversation_idempotency_key") == "idem-abc-123"
    # conversation_id is absent (None dropped by exclude_none), leaving the
    # ensure-conversation decision server-authoritative in ai-backend.
    assert "conversation_id" not in body
    # Identity stamping still applied.
    assert body.get("org_id") == "org_newchat_facade"
    assert body.get("user_id") == "user_newchat_facade"


def test_facade_still_forwards_existing_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-regression — the historical path (an existing conversation) still
    forwards conversation_id and omits the (absent) idempotency key."""

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        "/v1/agent/runs",
        headers={"authorization": _bearer()},
        json={"conversation_id": "conv_existing", "user_input": "go"},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]
    assert body.get("conversation_id") == "conv_existing"
    assert "conversation_idempotency_key" not in body
