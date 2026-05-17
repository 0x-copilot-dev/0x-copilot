"""Tests for the public ``/v1/admin/adapter_registry/*`` proxy (Phase 7C).

Identity is established by the bearer (FacadeAuthenticator), then the
verified org/user override whatever was on the wire. The scope check
(``admin:adapter_registry_review``) is enforced on the backend side and
mirrored here only by ``test_upstream_4xx_propagates`` (a 403 from
upstream surfaces verbatim).
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


_TEST_SECRET = "test-auth-secret"


def _hmac_token(payload: dict[str, object], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret.encode(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    token = _hmac_token(
        {
            "org_id": "org_atlas",
            "user_id": "usr_admin",
            "roles": ["admin"],
            "permission_scopes": ["admin:adapter_registry_review"],
        },
        _TEST_SECRET,
    )
    return {"authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


class _FakeAsyncClient:
    """Test double captures each upstream call + serves canned responses."""

    captured: list[dict[str, object]] = []
    next_response: httpx.Response | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args, **kwargs) -> None:
        return None

    async def request(
        self, method, url, *, params=None, json=None, headers=None, timeout=None
    ):
        self.captured.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "json": json,
                "headers": dict(headers or {}),
            }
        )
        return self.next_response or httpx.Response(204)


@pytest.fixture
def fake_upstream(monkeypatch):
    _FakeAsyncClient.captured = []
    _FakeAsyncClient.next_response = None
    monkeypatch.setattr(
        "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
    )
    return _FakeAsyncClient


class TestListCandidates:
    def test_proxies_with_allowlisted_filters(self, monkeypatch, fake_upstream) -> None:
        fake_upstream.next_response = httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "candidate_id": "cand_1",
                        "scheme": "atlas://hubspot",
                        "layout_template": "form",
                        "origin_tenant_redacted": "tenant_abc12345",
                        "generator_model": "anthropic:opus-4.7",
                        "submitted_at": "2026-05-01T12:00:00+00:00",
                        "status": "submitted",
                        "session_count": 12,
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get(
            "/v1/admin/adapter_registry/candidates"
            "?status=submitted&layout=form&scheme=atlas%3A%2F%2Fhubspot"
            "&limit=25&org_id=hacker",
            headers=_admin_headers(monkeypatch),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["candidates"][0]["candidate_id"] == "cand_1"

        # Backend got exactly the allowlisted filters + the *verified*
        # identity from the bearer — the smuggled ``org_id=hacker`` is
        # dropped because identity is stamped after the allowlist.
        call = fake_upstream.captured[-1]
        assert call["method"] == "GET"
        assert call["url"].endswith("/internal/v1/adapter_registry/candidates")
        assert call["params"] == {
            "org_id": "org_atlas",
            "user_id": "usr_admin",
            "status": "submitted",
            "layout": "form",
            "scheme": "atlas://hubspot",
            "limit": "25",
        }
        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_atlas"
        assert downstream["x-enterprise-user-id"] == "usr_admin"

    def test_unauthenticated_request_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/admin/adapter_registry/candidates")
        assert response.status_code == 401

    def test_upstream_403_propagates(self, monkeypatch, fake_upstream) -> None:
        # Caller has a valid bearer but no admin scope. Backend's
        # ``admin:adapter_registry_review`` gate returns 403; the facade
        # surfaces it verbatim.
        fake_upstream.next_response = httpx.Response(
            403, json={"detail": "missing_scope: admin:adapter_registry_review"}
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get(
            "/v1/admin/adapter_registry/candidates",
            headers=_admin_headers(monkeypatch),
        )
        assert response.status_code == 403


class TestGetCandidate:
    def test_proxies_with_path_param(self, monkeypatch, fake_upstream) -> None:
        fake_upstream.next_response = httpx.Response(
            200,
            json={
                "candidate_id": "cand_42",
                "scheme": "atlas://linear",
                "layout_template": "table",
                "origin_tenant_redacted": "tenant_def67890",
                "generator_model": "anthropic:opus-4.7",
                "submitted_at": "2026-05-02T14:00:00+00:00",
                "status": "in-review",
                "candidate_source": "export const adapter = { ... }",
                "schema_version": 1,
                "history": [],
            },
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get(
            "/v1/admin/adapter_registry/candidates/cand_42",
            headers=_admin_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json()["candidate_id"] == "cand_42"
        call = fake_upstream.captured[-1]
        assert call["url"].endswith("/internal/v1/adapter_registry/candidates/cand_42")


class TestDecideCandidate:
    def test_proxies_decision_body(self, monkeypatch, fake_upstream) -> None:
        fake_upstream.next_response = httpx.Response(
            200,
            json={
                "candidate_id": "cand_7",
                "status": "approved",
                "decided_at": "2026-05-17T10:00:00+00:00",
                "decided_by_user_id": "usr_admin",
                "action": "approve",
                "notes": "Looks fine. Synthetic state covers the basics.",
            },
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.post(
            "/v1/admin/adapter_registry/candidates/cand_7/decisions",
            json={
                "action": "approve",
                "notes": "Looks fine. Synthetic state covers the basics.",
            },
            headers=_admin_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        call = fake_upstream.captured[-1]
        assert call["method"] == "POST"
        assert call["url"].endswith(
            "/internal/v1/adapter_registry/candidates/cand_7/decisions"
        )
        assert call["json"] == {
            "action": "approve",
            "notes": "Looks fine. Synthetic state covers the basics.",
        }

    def test_request_changes_action_proxied(self, monkeypatch, fake_upstream) -> None:
        fake_upstream.next_response = httpx.Response(
            200,
            json={
                "candidate_id": "cand_8",
                "status": "changes-requested",
                "decided_at": "2026-05-17T11:00:00+00:00",
                "decided_by_user_id": "usr_admin",
                "action": "request-changes",
                "notes": "Form layout misaligned — please regenerate.",
            },
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.post(
            "/v1/admin/adapter_registry/candidates/cand_8/decisions",
            json={
                "action": "request-changes",
                "notes": "Form layout misaligned — please regenerate.",
            },
            headers=_admin_headers(monkeypatch),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "request-changes"
