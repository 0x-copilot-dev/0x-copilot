"""Facade must proxy the PRD-D1 staged-write routes verbatim.

Three passthroughs to the ai-backend stage engine
(``GET /v1/agent/stages/{id}``, ``POST …/revisions``, ``POST …/decisions``):
pure proxies, no logic. These pin that the body reaches ai-backend unchanged,
the verified identity is stamped onto the params (``org_id``), the owning
``run_id`` rides the query string, and the ``hold`` decision — which the server,
not the facade, rejects — passes straight through.
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

_ORG_ID = "org_stage_facade"
_USER_ID = "user_stage_facade"
_STAGE_ID = "stage_abc123"
_RUN_ID = "run_launch"


def _bearer(
    *, org_id: str = _ORG_ID, user_id: str = _USER_ID, secret: str = "test-auth-secret"
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
    monkeypatch: pytest.MonkeyPatch, *, expected_path: str, expected_method: str
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if (
            target == "ai_backend"
            and method == expected_method
            and path == expected_path
        ):
            captured.append(
                {"json": kwargs.get("json"), "params": kwargs.get("params")}
            )
            return {"stage_id": _STAGE_ID, "run_id": _RUN_ID, "status": "staged"}
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def _headers() -> dict[str, str]:
    return {"authorization": _bearer()}


def test_facade_proxies_get_stage_with_identity_and_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_capturing_forwarder(
        monkeypatch,
        expected_path=f"/v1/agent/stages/{_STAGE_ID}",
        expected_method="GET",
    )
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(
        f"/v1/agent/stages/{_STAGE_ID}?run_id={_RUN_ID}", headers=_headers()
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    params = captured[0]["params"]
    assert params["run_id"] == _RUN_ID
    assert params["org_id"] == _ORG_ID  # facade stamps the verified identity


def test_facade_proxies_revision_body_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_capturing_forwarder(
        monkeypatch,
        expected_path=f"/v1/agent/stages/{_STAGE_ID}/revisions",
        expected_method="POST",
    )
    client = TestClient(create_app(FacadeSettings()))

    body = {"base_rev": 2, "content_text": "Edited body copy.", "title": "New title"}
    response = client.post(
        f"/v1/agent/stages/{_STAGE_ID}/revisions?run_id={_RUN_ID}",
        headers=_headers(),
        json=body,
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert captured[0]["json"] == body  # nothing added, nothing dropped
    assert captured[0]["params"]["run_id"] == _RUN_ID
    assert captured[0]["params"]["org_id"] == _ORG_ID


def test_facade_proxies_decision_body_including_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``hold`` must reach ai-backend (which 422s it); the facade adds no logic.
    captured = _install_capturing_forwarder(
        monkeypatch,
        expected_path=f"/v1/agent/stages/{_STAGE_ID}/decisions",
        expected_method="POST",
    )
    client = TestClient(create_app(FacadeSettings()))

    for body in (
        {"decision": "approve", "rev": 3},
        {"decision": "reject", "rev": 3},
        {"decision": "restore"},
        {"decision": "hold", "rev": 3},
    ):
        captured.clear()
        response = client.post(
            f"/v1/agent/stages/{_STAGE_ID}/decisions?run_id={_RUN_ID}",
            headers=_headers(),
            json=body,
        )
        assert response.status_code == 200, response.text
        assert captured[0]["json"] == body
        assert captured[0]["params"]["run_id"] == _RUN_ID
        assert captured[0]["params"]["org_id"] == _ORG_ID


def test_facade_proxies_row_scoped_decision_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PRD-D3 — a row-set stance toggle carries ``row_keys`` (not ``rev``); the
    # facade passes it straight through (the server enforces the matrix).
    captured = _install_capturing_forwarder(
        monkeypatch,
        expected_path=f"/v1/agent/stages/{_STAGE_ID}/decisions",
        expected_method="POST",
    )
    client = TestClient(create_app(FacadeSettings()))

    body = {"decision": "hold", "row_keys": ["row1", "row2"]}
    response = client.post(
        f"/v1/agent/stages/{_STAGE_ID}/decisions?run_id={_RUN_ID}",
        headers=_headers(),
        json=body,
    )
    assert response.status_code == 200, response.text
    assert captured[0]["json"] == body  # row_keys reaches ai-backend unchanged
    assert captured[0]["params"]["run_id"] == _RUN_ID
    assert captured[0]["params"]["org_id"] == _ORG_ID


def test_facade_proxies_apply_body_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PRD-D3 — the NEW ``/apply`` passthrough (pure proxy; the ai-backend
    # re-checks the approved set + routes through the D2 commit pipeline).
    captured = _install_capturing_forwarder(
        monkeypatch,
        expected_path=f"/v1/agent/stages/{_STAGE_ID}/apply",
        expected_method="POST",
    )
    client = TestClient(create_app(FacadeSettings()))

    body = {"rev": 1, "row_keys": ["row0", "row1", "row2"]}
    response = client.post(
        f"/v1/agent/stages/{_STAGE_ID}/apply?run_id={_RUN_ID}",
        headers=_headers(),
        json=body,
    )
    assert response.status_code == 200, response.text
    assert captured[0]["json"] == body  # nothing added, nothing dropped
    assert captured[0]["params"]["run_id"] == _RUN_ID
    assert captured[0]["params"]["org_id"] == _ORG_ID
