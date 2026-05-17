"""Facade adapter-registry proxy semantics.

Verifies that:
* The verified bearer's tenant id (org_id) is forwarded to the
  backend's ``/internal/v1/adapter_registry`` surface, not whatever
  the caller put in the body.
* Auth gating (401 without bearer) holds for every route in the set.
* Admin routes and tenant routes round-trip to the backend path the
  spec calls out, in the right HTTP method.
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
from backend_facade.auth import AuthenticatedIdentity
from backend_facade.settings import FacadeSettings


_AUTH_SECRET = "test-auth-secret"
_SERVICE_TOKEN = "test-service-token"


def _bearer(org_id: str, user_id: str) -> str:
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
                _AUTH_SECRET.encode("utf-8"),
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"Bearer {payload}.{signature}"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _AUTH_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)


@pytest.fixture
def captured_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Capture every forward_json call so we can assert on routing."""

    captured: list[dict[str, Any]] = []

    async def _capture(app, method, path, **kwargs):
        captured.append(
            {
                "method": method,
                "path": path,
                "target": kwargs.get("target"),
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
                "identity": kwargs.get("identity"),
            }
        )
        return {"ok": True}

    monkeypatch.setattr(facade_app, "forward_json", _capture)
    return captured


@pytest.fixture
def client(captured_forwards: list[dict[str, Any]]) -> TestClient:
    return TestClient(create_app(FacadeSettings()))


class TestAuthRequired:
    @pytest.mark.parametrize(
        ("method", "url", "body"),
        [
            ("POST", "/v1/adapter_registry/candidates", {}),
            ("GET", "/v1/adapter_registry/promoted", None),
            ("GET", "/v1/adapter_registry/opt-out", None),
            ("PUT", "/v1/adapter_registry/opt-out", {}),
            ("GET", "/v1/admin/adapter_registry/candidates", None),
            ("GET", "/v1/admin/adapter_registry/candidates/acan_x", None),
            (
                "POST",
                "/v1/admin/adapter_registry/candidates/acan_x/decisions",
                {},
            ),
        ],
    )
    def test_401_without_bearer(
        self,
        client: TestClient,
        method: str,
        url: str,
        body: dict[str, object] | None,
    ) -> None:
        response = client.request(method, url, json=body)
        assert response.status_code == 401, response.text


class TestSubmitCandidateProxy:
    def test_forwards_to_backend_internal_path(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        body = {
            "scheme": "saas:salesforce",
            "version": 1,
            "layout": "form",
            "source": "// source",
            "harvest_metrics": {
                "zero_error_sessions": 10,
                "total_sessions": 10,
            },
        }
        response = client.post(
            "/v1/adapter_registry/candidates",
            json=body,
            headers={"authorization": _bearer("org_acme", "usr_alice")},
        )
        assert response.status_code == 201
        assert len(captured_forwards) == 1
        call = captured_forwards[0]
        assert call["method"] == "POST"
        assert call["path"] == "/internal/v1/adapter_registry/candidates"
        assert call["target"] == "backend"
        assert call["json"] == body
        identity: AuthenticatedIdentity = call["identity"]
        assert identity.org_id == "org_acme"
        assert identity.user_id == "usr_alice"
        params: dict[str, object] = call["params"] or {}
        assert params["org_id"] == "org_acme"
        assert params["user_id"] == "usr_alice"


class TestListPromotedProxy:
    def test_forwards_verified_tenant_only(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        response = client.get(
            "/v1/adapter_registry/promoted",
            headers={"authorization": _bearer("org_globex", "usr_bob")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["path"] == "/internal/v1/adapter_registry/promoted"
        params: dict[str, object] = call["params"] or {}
        assert params["org_id"] == "org_globex"


class TestOptOutProxy:
    def test_put_round_trips(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        response = client.put(
            "/v1/adapter_registry/opt-out",
            json={"opted_out": True},
            headers={"authorization": _bearer("org_acme", "usr_admin")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["method"] == "PUT"
        assert call["path"] == "/internal/v1/adapter_registry/opt-out"
        assert call["json"] == {"opted_out": True}

    def test_get_round_trips(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        response = client.get(
            "/v1/adapter_registry/opt-out",
            headers={"authorization": _bearer("org_acme", "usr_admin")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["method"] == "GET"
        assert call["path"] == "/internal/v1/adapter_registry/opt-out"


class TestAdminCandidatesProxy:
    def test_list_forwards_status_filter(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        response = client.get(
            "/v1/admin/adapter_registry/candidates?status=submitted&limit=25",
            headers={"authorization": _bearer("org_platform", "usr_admin")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["path"] == "/internal/v1/adapter_registry/candidates"
        params: dict[str, object] = call["params"] or {}
        assert params.get("status") == "submitted"
        assert params.get("limit") == "25"

    def test_get_specific_candidate_forwards_id(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        response = client.get(
            "/v1/admin/adapter_registry/candidates/acan_abc123",
            headers={"authorization": _bearer("org_platform", "usr_admin")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["path"] == "/internal/v1/adapter_registry/candidates/acan_abc123"

    def test_decision_forwards_payload(
        self,
        client: TestClient,
        captured_forwards: list[dict[str, Any]],
    ) -> None:
        body = {"action": "approve", "notes": "LGTM"}
        response = client.post(
            "/v1/admin/adapter_registry/candidates/acan_abc123/decisions",
            json=body,
            headers={"authorization": _bearer("org_platform", "usr_admin")},
        )
        assert response.status_code == 200
        call = captured_forwards[0]
        assert call["method"] == "POST"
        assert (
            call["path"]
            == "/internal/v1/adapter_registry/candidates/acan_abc123/decisions"
        )
        assert call["json"] == body
