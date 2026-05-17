from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import httpx

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


async def _unused_backend(*a, **k):
    raise AssertionError("backend should not have been called")


def _dispatch(backend_fake, ai_fake):
    async def _f(*args, target, **kwargs):
        return await (ai_fake if target == "ai_backend" else backend_fake)(
            *args, **kwargs
        )

    return _f


class FacadeAuthTestMixin:
    def auth_headers(self, monkeypatch) -> dict[str, str]:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "org_id": "org_123",
                        "user_id": "user_123",
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
                    b"test-auth-secret", payload.encode("ascii"), hashlib.sha256
                ).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
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
        """`/v1/skills` aggregates backend (user/preloaded) + ai-backend (system).

        Both upstreams must be called even when the backend returns an empty
        list — system skills come from the runtime, not the backend store.
        """

        async def fake_forward_json(*args, **kwargs):
            assert args[1] == "GET"
            assert args[2] == "/v1/skills"
            return {"skills": []}

        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "GET"
            assert args[2] == "/internal/v1/skills/system"
            return {"skills": []}

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(fake_forward_json, fake_forward_json_to_ai),
        )
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 200
        assert response.json() == {"skills": []}

    def test_facade_skill_list_concatenates_system_first_then_backend(
        self, monkeypatch
    ) -> None:
        """System skills must lead the merged list so the settings UI can
        render them at the top without re-sorting. Backend's payload follows
        in its existing order — no shuffling of user/preloaded items."""

        async def fake_forward_json(*args, **kwargs):
            return {
                "skills": [
                    {
                        "skill_id": "preloaded:org_123:user_123:report",
                        "name": "report",
                        "source_type": "preloaded",
                    },
                    {
                        "skill_id": "user:abc",
                        "name": "my-skill",
                        "source_type": "user",
                    },
                ]
            }

        async def fake_forward_json_to_ai(*args, **kwargs):
            return {
                "skills": [
                    {
                        "skill_id": "system:search-subagent-logs",
                        "name": "search-subagent-logs",
                        "source_type": "system",
                    }
                ]
            }

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(fake_forward_json, fake_forward_json_to_ai),
        )
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 200
        body = response.json()
        names = [skill["name"] for skill in body["skills"]]
        assert names == ["search-subagent-logs", "report", "my-skill"]

    def test_facade_skill_list_tolerates_non_list_upstream_payload(
        self, monkeypatch
    ) -> None:
        """A misshapen upstream response should not 500 the facade — drop
        non-list/non-object items and merge what's valid."""

        async def fake_forward_json(*args, **kwargs):
            return {"skills": "not a list"}

        async def fake_forward_json_to_ai(*args, **kwargs):
            return {
                "skills": [
                    {"skill_id": "system:x", "name": "x", "source_type": "system"},
                    "garbage-item",
                ]
            }

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(fake_forward_json, fake_forward_json_to_ai),
        )
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 200
        body = response.json()
        assert [skill["name"] for skill in body["skills"]] == ["x"]

    def test_facade_preserves_upstream_error_detail(self, monkeypatch) -> None:
        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def request(self, *args, **kwargs) -> httpx.Response:
                return httpx.Response(
                    409,
                    json={"detail": "Skill name already exists"},
                )

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", FakeAsyncClient
        )
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )

        response = client.get("/v1/skills", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 409
        assert response.json() == {"detail": "Skill name already exists"}

    def test_facade_forwards_conversation_create_to_ai(self, monkeypatch) -> None:
        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "POST"
            assert args[2] == "/v1/agent/conversations"
            assert kwargs["json"]["org_id"] == "org_123"
            assert kwargs["json"]["user_id"] == "user_123"
            assert "request_context" not in kwargs["json"]
            return {"conversation_id": "conv_123"}

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(_unused_backend, fake_forward_json_to_ai),
        )
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/conversations",
            json={"org_id": "forged_org", "user_id": "forged_user"},
            headers=self.auth_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json() == {"conversation_id": "conv_123"}

    def test_facade_forwards_conversation_list_to_ai(self, monkeypatch) -> None:
        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "GET"
            assert args[2] == "/v1/agent/conversations"
            params = kwargs["params"]
            assert params["org_id"] == "org_123"
            assert params["user_id"] == "user_123"
            assert params["limit"] == 25
            assert params["include_archived"] is False
            return {"conversations": []}

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(_unused_backend, fake_forward_json_to_ai),
        )
        client = TestClient(create_app(FacadeSettings()))

        response = client.get(
            "/v1/agent/conversations?limit=25",
            headers=self.auth_headers(monkeypatch),
        )

        assert response.status_code == 200
        assert response.json() == {"conversations": []}

    def test_facade_rejects_missing_bearer_token_when_dev_bypass_is_disabled(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("ENTERPRISE_AUTH_SECRET", raising=False)
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
        monkeypatch.setenv("FACADE_ENVIRONMENT", "development")
        client = TestClient(create_app(FacadeSettings()))

        response = client.post(
            "/v1/agent/conversations",
            json={"org_id": "forged_org", "user_id": "forged_user"},
        )

        assert response.status_code == 401

    def test_dev_auth_bypass_env_var_is_no_longer_honored(self, monkeypatch) -> None:
        """W0.1 — DEV_AUTH_BYPASS=true is dead. Without a bearer the facade
        replies 401 even in development; the dev IdP is the only path."""

        monkeypatch.delenv("ENTERPRISE_AUTH_SECRET", raising=False)
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        monkeypatch.setenv("FACADE_ENVIRONMENT", "development")
        monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
        # Skip deployment_profile fail-loud (which catches the same env var
        # at boot under non-saas profiles); we are exercising the request
        # path here, not the boot check.
        monkeypatch.setenv("DEPLOYMENT_PROFILE", "saas_multi_tenant")
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills")

        assert response.status_code == 401

    def test_facade_exposes_authenticated_session_identity(self, monkeypatch) -> None:
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/session", headers=self.auth_headers(monkeypatch))

        assert response.status_code == 200
        assert response.json() == {
            "identity": {
                "org_id": "org_123",
                "user_id": "user_123",
                "roles": ["employee"],
                "permission_scopes": ["runtime:use"],
            }
        }

    def test_facade_forwards_run_cancel_to_ai(self, monkeypatch) -> None:
        async def fake_forward_json_to_ai(*args, **kwargs):
            assert args[1] == "POST"
            assert args[2] == "/v1/agent/runs/run_123/cancel"
            assert kwargs["params"] == {"org_id": "org_123", "user_id": "user_123"}
            assert kwargs["json"] == {"requested_by_user_id": "user_123"}
            return {
                "run_id": "run_123",
                "status": "cancelling",
                "latest_sequence_no": 3,
            }

        monkeypatch.setattr(
            facade_app,
            "forward_json",
            _dispatch(_unused_backend, fake_forward_json_to_ai),
        )
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
            params={
                "state": "state_123",
                "error": "access_denied",
                "error_description": "Denied",
            },
            headers=self.auth_headers(monkeypatch),
        )

        assert patch_response.status_code == 200
        assert callback_response.status_code == 200
        assert calls[0][0][1] == "PATCH"
        assert calls[0][0][2] == "/v1/mcp/servers/srv_123"
        assert calls[1][0][1] == "GET"
        assert calls[1][0][2] == "/v1/mcp/oauth/callback"
        assert calls[1][1]["params"] == {
            "state": "state_123",
            "error": "access_denied",
            "error_description": "Denied",
        }
