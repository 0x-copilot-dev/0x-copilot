"""Facade must proxy ``POST /v1/agent/approvals/{id}/decision`` verbatim.

PRD-09a (Wave 3) adds ``decision == "approve_with_edits"`` with an
``edits`` object (``SurfaceEdits`` = ``{fields?, body?, accepted_hunk_ids?}``)
to the api-types decision contract. The facade route is passthrough ONLY:
it takes an untyped ``dict[str, object]`` body (NOT a typed Pydantic model
like ``FacadeRunRequest``), so every key — including the new ``edits`` —
already reaches ai-backend unchanged. The merge/validation is ai-backend's
job (09b); the facade must add nothing and drop nothing.

These tests pin that passthrough: the new fields survive, the identity is
stamped, and the legacy approve/reject bodies are byte-identical.
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

_ORG_ID = "org_appr_facade"
_USER_ID = "user_appr_facade"
_APPROVAL_ID = "appr_123"


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


def _install_capturing_forwarder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Intercept ``forward_json`` and capture the outbound decision body."""

    captured: list[dict[str, Any]] = []
    expected_path = f"/v1/agent/approvals/{_APPROVAL_ID}/decision"

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "POST" and path == expected_path:
            captured.append(
                {"json": kwargs.get("json", {}), "params": kwargs.get("params")}
            )
            return {
                "approval_id": _APPROVAL_ID,
                "run_id": "run_stub",
                "status": "approved",
                "decided_at": "2026-07-21T00:00:00Z",
            }
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def _post_decision(client: TestClient, body: dict[str, Any]):
    return client.post(
        f"/v1/agent/approvals/{_APPROVAL_ID}/decision",
        headers={"authorization": _bearer()},
        json=body,
    )


def test_facade_proxies_approve_with_edits_and_edits_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PRD-09a decision + full ``SurfaceEdits`` reach ai-backend
    unchanged, alongside facade identity stamping."""

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    edits = {
        "fields": {"subject": "Revised subject"},
        "body": "Edited body copy.",
        "accepted_hunk_ids": ["h1", "h3"],
    }
    response = _post_decision(
        client,
        {"decision": "approve_with_edits", "edits": edits, "reason": "looks good"},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]["json"]
    # New decision literal survives verbatim.
    assert body.get("decision") == "approve_with_edits"
    # `edits` passes through structurally identical — nothing dropped,
    # nothing merged (merge is ai-backend 09b's job).
    assert body.get("edits") == edits
    # Unrelated caller fields are preserved too.
    assert body.get("reason") == "looks good"
    # Facade stamps the verified identity; org scoping rides params.
    assert body.get("decided_by_user_id") == _USER_ID
    assert captured[0]["params"] == {"org_id": _ORG_ID}


def test_facade_proxies_plain_approve_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy approve is unchanged: no `edits` invented, decision verbatim."""

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = _post_decision(client, {"decision": "approved"})

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]["json"]
    assert body.get("decision") == "approved"
    assert "edits" not in body
    assert body.get("decided_by_user_id") == _USER_ID


def test_facade_proxies_plain_reject_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy reject is unchanged: no `edits` invented, decision verbatim."""

    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = _post_decision(client, {"decision": "rejected", "reason": "not now"})

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = captured[0]["json"]
    assert body.get("decision") == "rejected"
    assert body.get("reason") == "not now"
    assert "edits" not in body
    assert body.get("decided_by_user_id") == _USER_ID
