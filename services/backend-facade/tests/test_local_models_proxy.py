"""Tests for the ``/v1/local-models/*`` facade proxy (Round 2).

Covers auth gating, identity + service-header forwarding to ai-backend for
the JSON routes, and byte-for-byte SSE passthrough for the pull stream.
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


def _bearer_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    token = _hmac_token(
        {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
        },
        _TEST_SECRET,
    )
    return {"authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


def _install_fake_upstream(monkeypatch, captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path == "/v1/local-models/status":
            return httpx.Response(
                200,
                json={
                    "enabled": True,
                    "ollama_running": True,
                    "ollama_version": "0.5.1",
                },
            )
        if path == "/v1/local-models/size":
            return httpx.Response(
                200,
                json={
                    "repo": request.url.params.get("repo"),
                    "quant": request.url.params.get("quant"),
                    "filename": "Model-Q4_K_M.gguf",
                    "size_bytes": 808_000_000,
                },
            )
        if path == "/v1/local-models" and request.method == "GET":
            return httpx.Response(200, json={"models": []})
        if path == "/v1/local-models/pull" and request.method == "GET":
            frame = (
                b"event: local_model_pull\nid: 1\n"
                b'data: {"sequence_no":1,"status":"success","done":true}\n\n'
            )
            return httpx.Response(
                200, content=frame, headers={"content-type": "text/event-stream"}
            )
        if request.method == "DELETE" and path.startswith("/v1/local-models/"):
            return httpx.Response(204)
        return httpx.Response(404)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Client)


def _client() -> TestClient:
    return TestClient(
        create_app(
            FacadeSettings(
                backend_url="http://backend.local",
                ai_backend_url="http://ai.local",
            )
        )
    )


class TestGating:
    def test_status_unauthenticated_rejected(self) -> None:
        assert _client().get("/v1/local-models/status").status_code == 401


class TestJsonProxy:
    def test_status_forwards_identity_and_service_headers(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/status", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json()["ollama_version"] == "0.5.1"
        req = captured[-1]
        assert str(req.url).startswith("http://ai.local/v1/local-models/status")
        assert req.url.params.get("org_id") == "org_acme"
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"
        assert headers["x-enterprise-user-id"] == "usr_sarah"

    def test_size_forwards_repo_and_quant(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/size",
            params={"repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["size_bytes"] == 808_000_000
        assert captured[-1].url.params.get("repo") == "acme/Tiny-GGUF"
        assert captured[-1].url.params.get("quant") == "Q4_K_M"

    def test_delete_returns_204(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().request(
            "DELETE",
            "/v1/local-models/hf.co/acme/Tiny-GGUF:Q4_K_M",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 204
        assert captured[-1].method == "DELETE"
        assert "Tiny-GGUF:Q4_K_M" in captured[-1].url.path


class TestSseProxy:
    def test_pull_streams_sse_through(self, monkeypatch) -> None:
        captured: list[httpx.Request] = []
        _install_fake_upstream(monkeypatch, captured)
        resp = _client().get(
            "/v1/local-models/pull",
            params={"repo": "acme/Tiny-GGUF", "quant": "Q4_K_M"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "event: local_model_pull" in resp.text
        assert '"done":true' in resp.text
        assert captured[-1].method == "GET"
        assert captured[-1].url.params.get("repo") == "acme/Tiny-GGUF"
