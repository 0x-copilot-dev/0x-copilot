"""Facade security contract for the Electron-main C2 attestation bridge."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_PATH = "/v1/agent/desktop-workspace-attestation"
_HOST_TOKEN = "desktop-main-host-token"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _HOST_TOKEN)


def _client_with_upstream(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[httpx.Request],
    *,
    upstream_status: int = 204,
) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(upstream_status, json={"detail": "upstream"})

    class MockedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ANN002, ANN003
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", MockedClient)
    return TestClient(
        create_app(
            FacadeSettings(
                backend_url="http://backend.local",
                ai_backend_url="http://ai.local",
            )
        )
    )


def test_forwards_only_main_host_token_and_opaque_signed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []
    client = _client_with_upstream(monkeypatch, captured)

    response = client.post(
        _PATH,
        headers={"x-enterprise-service-token": _HOST_TOKEN},
        json={"payload": "opaque-payload", "signature": "opaque-signature"},
    )

    assert response.status_code == 204
    assert len(captured) == 1
    upstream = captured[0]
    assert upstream.method == "POST"
    assert str(upstream.url) == f"http://ai.local{_PATH}"
    assert upstream.content == (
        b'{"payload":"opaque-payload","signature":"opaque-signature"}'
    )
    headers = {key.lower(): value for key, value in upstream.headers.items()}
    assert headers["x-enterprise-service-token"] == _HOST_TOKEN
    assert "x-enterprise-org-id" not in headers
    assert "x-enterprise-user-id" not in headers


def test_missing_host_token_is_rejected_without_proxying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []
    client = _client_with_upstream(monkeypatch, captured)

    response = client.post(
        _PATH,
        json={"payload": "opaque-payload", "signature": "opaque-signature"},
    )

    assert response.status_code == 401
    assert captured == []


def test_upstream_invalid_signature_status_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[httpx.Request] = []
    client = _client_with_upstream(monkeypatch, captured, upstream_status=422)

    response = client.post(
        _PATH,
        headers={"x-enterprise-service-token": _HOST_TOKEN},
        json={"payload": "tampered", "signature": "invalid"},
    )

    assert response.status_code == 422
    assert len(captured) == 1
