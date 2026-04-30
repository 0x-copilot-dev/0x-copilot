from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

class FacadeAuthTestMixin:
    def auth_headers(self, monkeypatch) -> dict[str, str]:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": "org_123",
                    "user_id": "user_123",
                    "roles": ["employee"],
                    "permission_scopes": ["runtime:use"],
                }
            ).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signature = base64.urlsafe_b64encode(
            hmac.new(b"test-auth-secret", payload.encode("ascii"), hashlib.sha256).digest()
        ).decode("ascii").rstrip("=")
        return {"authorization": f"Bearer {payload}.{signature}"}


class TestFacadeSettings(FacadeAuthTestMixin):
    def test_facade_settings_normalize_service_urls(self) -> None:
        settings = FacadeSettings(
            backend_url="http://backend.local/",
            ai_backend_url="http://ai.local/",
        )

        assert settings.backend_url == "http://backend.local/"
        assert settings.ai_backend_url == "http://ai.local/"

    def test_facade_forwards_skill_list(self, monkeypatch) -> None:
        async def fake_forward_json(*args, **kwargs):
            assert args[1] == "GET"
            assert args[2] == "/v1/skills"
            return {"skills": []}

        monkeypatch.setattr(facade_app, "forward_json", fake_forward_json)
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 200
        assert response.json() == {"skills": []}

    def test_facade_forwards_conversation_create_to_ai(self, monkeypatch) -> None:
        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "POST"
            assert args[2] == "/v1/agent/conversations"
            assert kwargs["json"]["org_id"] == "org_123"
            assert kwargs["json"]["user_id"] == "user_123"
            assert kwargs["json"]["request_context"]["permission_scopes"] == ("runtime:use",)
            return {"conversation_id": "conv_123"}

        monkeypatch.setattr(facade_app, "forward_json_to_ai", fake_forward_json_to_ai)
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/conversations",
            json={"org_id": "forged_org", "user_id": "forged_user"},
            headers=self.auth_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json() == {"conversation_id": "conv_123"}

    def test_facade_forwards_run_cancel_to_ai(self, monkeypatch) -> None:
        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "POST"
            assert args[2] == "/v1/agent/runs/run_123/cancel"
            assert kwargs["params"] == {"org_id": "org_123", "user_id": "user_123"}
            assert kwargs["json"] == {"requested_by_user_id": "user_123"}
            return {"run_id": "run_123", "status": "cancelling", "latest_sequence_no": 3}

        monkeypatch.setattr(facade_app, "forward_json_to_ai", fake_forward_json_to_ai)
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/runs/run_123/cancel",
            json={"requested_by_user_id": "forged_user"},
            headers=self.auth_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelling"

    def test_facade_forwards_mcp_update_and_callback(self, monkeypatch) -> None:
        calls = []

        async def fake_forward_json(*args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == "PATCH":
                return {"server_id": "srv_123", "enabled": False}
            return {"server_id": "srv_123", "auth_state": "authenticated"}

        monkeypatch.setattr(facade_app, "forward_json", fake_forward_json)
        client = TestClient(create_app(FacadeSettings()))

        patch_response = client.patch(
            "/v1/mcp/servers/srv_123",
            json={"enabled": False},
            headers=self.auth_headers(monkeypatch),
        )
        callback_response = client.get(
            "/v1/mcp/oauth/callback",
            params={"state": "state_123", "code": "code_123"},
            headers=self.auth_headers(monkeypatch),
        )

        assert patch_response.status_code == 200
        assert callback_response.status_code == 200
        assert calls[0][0][1] == "PATCH"
        assert calls[0][0][2] == "/v1/mcp/servers/srv_123"
        assert calls[1][0][1] == "GET"
        assert calls[1][0][2] == "/v1/mcp/oauth/callback"
