"""Route tests — list / create / patch / delete / rotate / test-fire.

connectors-prd §4.10. Routes are presentation-only; the service layer
owns ACL + audit invariants (those are covered in ``test_webhook_service``).
These tests pin the wire shape and HTTP-status mapping.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.token_vault import LocalTokenVault
from backend_app.webhooks.routes import register_webhook_routes
from backend_app.webhooks.service import WebhooksService
from backend_app.webhooks.store import InMemoryWebhooksStore


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_ORG = "org_acme"
_OWNER = "usr_sarah"
_OTHER = "usr_marcus"


class _StubResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _StubClient:
    """Records every test-fire request so the route test can assert
    headers + body without standing up a real HTTPS receiver."""

    def __init__(self, *, status_code: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code
        self.closed = False

    def post(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> _StubResponse:
        self.calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return _StubResponse(self._status_code)

    def close(self) -> None:  # pragma: no cover - factory closes outside
        self.closed = True


@pytest.fixture
def stub_client() -> _StubClient:
    return _StubClient()


@pytest.fixture
def app(stub_client: _StubClient) -> FastAPI:
    store = InMemoryWebhooksStore()
    vault = LocalTokenVault(secret=_VAULT_SECRET)
    service = WebhooksService(store=store, token_vault=vault)
    app = FastAPI()
    register_webhook_routes(
        app, service=service, http_client_factory=lambda: stub_client
    )
    app.state.webhooks_store = store
    app.state.webhooks_service = service
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _qp(user: str = _OWNER, org: str = _ORG) -> dict[str, str]:
    return {"org_id": org, "user_id": user}


class TestCreateAndList:
    def test_create_returns_secret_once_then_redacted(self, client: TestClient) -> None:
        response = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["secret_plaintext"]
        assert body["webhook"]["id"].startswith("trig_")
        # Subsequent GET does NOT include the secret.
        webhook_id = body["webhook"]["id"]
        plaintext = body["secret_plaintext"]
        detail = client.get(
            f"/v1/connectors/webhooks/{webhook_id}", params=_qp()
        ).json()
        assert "secret_plaintext" not in detail
        # Defense-in-depth: the plaintext is not anywhere in the body.
        assert plaintext not in detail["url"]

    def test_create_rejects_http_url(self, client: TestClient) -> None:
        response = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "http://example.com/hook"},
        )
        assert response.status_code == 400

    def test_list_owner_sees_only_own(self, client: TestClient) -> None:
        # Owner creates one.
        client.post(
            "/v1/connectors/webhooks",
            params=_qp(_OWNER),
            json={"url": "https://example.com/hook"},
        )
        # Other user creates one.
        client.post(
            "/v1/connectors/webhooks",
            params=_qp(_OTHER),
            json={"url": "https://other.example.com/hook"},
        )
        # Owner's list contains only their own (caller_roles empty → not admin).
        rows = client.get("/v1/connectors/webhooks", params=_qp(_OWNER)).json()
        assert len(rows["items"]) == 1
        assert rows["items"][0]["owner_user_id"] == _OWNER


class TestPatchDelete:
    def test_patch_status_round_trip(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        patch = client.patch(
            f"/v1/connectors/webhooks/{wid}",
            params=_qp(),
            json={"status": "paused"},
        )
        assert patch.status_code == 200
        assert patch.json()["status"] == "paused"

    def test_patch_invalid_status_400(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        patch = client.patch(
            f"/v1/connectors/webhooks/{wid}",
            params=_qp(),
            json={"status": "bogus"},
        )
        assert patch.status_code == 400

    def test_delete_returns_204_and_hides_row(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        delete = client.delete(f"/v1/connectors/webhooks/{wid}", params=_qp())
        assert delete.status_code == 204
        assert (
            client.get(f"/v1/connectors/webhooks/{wid}", params=_qp()).status_code
            == 404
        )


class TestRotate:
    def test_rotate_returns_new_secret_and_grace(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        rotated = client.post(f"/v1/connectors/webhooks/{wid}/rotate", params=_qp())
        assert rotated.status_code == 200
        body = rotated.json()
        assert body["secret_plaintext"] != created["secret_plaintext"]
        assert body["grace_secret_plaintext"] == created["secret_plaintext"]


class TestAcl:
    def test_non_owner_get_returns_404(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(_OWNER),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        not_found = client.get(f"/v1/connectors/webhooks/{wid}", params=_qp(_OTHER))
        # 404-not-403 per cross-audit §1.3.
        assert not_found.status_code == 404


class TestTestFire:
    def test_test_fire_posts_signed_payload_and_returns_status(
        self, client: TestClient, stub_client: _StubClient
    ) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        response = client.post(f"/v1/connectors/webhooks/{wid}/test-fire", params=_qp())
        assert response.status_code == 200
        body = response.json()
        assert body["response_status"] == 200
        assert body["response_ok"] is True
        assert len(stub_client.calls) == 1
        call = stub_client.calls[0]
        assert call["url"] == "https://example.com/hook"
        # Canonical headers are present.
        assert "X-Atlas-Routine-Signature" in call["headers"]
        assert "X-Atlas-Signature-Timestamp" in call["headers"]
        assert call["headers"]["X-Atlas-Routine-Signature"].startswith("hmac-sha256=")

    def test_test_fire_non_owner_non_admin_403(self, client: TestClient) -> None:
        created = client.post(
            "/v1/connectors/webhooks",
            params=_qp(_OWNER),
            json={"url": "https://example.com/hook"},
        ).json()
        wid = created["webhook"]["id"]
        # The other user can't see the row at all (404-not-403).
        denied = client.post(
            f"/v1/connectors/webhooks/{wid}/test-fire", params=_qp(_OTHER)
        )
        assert denied.status_code == 404
