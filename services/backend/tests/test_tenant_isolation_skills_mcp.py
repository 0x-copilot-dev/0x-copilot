"""Cross-tenant isolation for public MCP and skills routes (org-scoped)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from backend_app.app import create_app
from backend_app.service import McpRegistryService, SkillRegistryService
from backend_app.store import InMemoryMcpStore, InMemorySkillStore
from test_mcp_api_flow import (
    FakeOAuthClient,
    FakeOAuthTokenExchanger,
)

SKILL_MARKDOWN = """---
name: cross-tenant-skill
description: fixture
---
# X
"""


def _auth_headers(*, org_id: str, user_id: str, token: str) -> dict[str, str]:
    return {
        SERVICE_TOKEN_HEADER: token,
        ORG_HEADER: org_id,
        USER_HEADER: user_id,
    }


def test_other_org_cannot_get_or_mutate_skill(monkeypatch) -> None:
    token = "cross-tenant-skill-test-token"
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", token)
    app = create_app(skill_service=SkillRegistryService(store=InMemorySkillStore()))
    client = TestClient(app)
    h_a = _auth_headers(org_id="org_alpha", user_id="user_1", token=token)
    h_b = _auth_headers(org_id="org_beta", user_id="user_1", token=token)

    created = client.post(
        "/v1/skills",
        headers=h_a,
        json={
            "org_id": "forged",
            "user_id": "forged",
            "markdown": SKILL_MARKDOWN,
        },
    )
    assert created.status_code == 200
    skill_id = created.json()["skill_id"]

    res_get = client.get(
        f"/v1/skills/{skill_id}",
        headers=h_b,
        params={"org_id": "forged", "user_id": "forged"},
    )
    assert res_get.status_code == 404
    assert "org_alpha" not in res_get.text

    res_put = client.put(
        f"/v1/skills/{skill_id}",
        headers=h_b,
        params={"org_id": "forged", "user_id": "forged"},
        json={"enabled": False},
    )
    assert res_put.status_code == 400


def test_other_org_cannot_list_or_delete_mcp_server(monkeypatch) -> None:
    token = "cross-tenant-mcp-test-token"
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", token)
    store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        )
    )
    client = TestClient(app)
    h_a = _auth_headers(org_id="org_alpha", user_id="user_mcp", token=token)
    h_b = _auth_headers(org_id="org_beta", user_id="user_mcp", token=token)

    created = client.post(
        "/v1/mcp/servers",
        headers=h_a,
        json={
            "org_id": "x",
            "user_id": "y",
            "url": "https://mcp.example.com",
            "display_name": "Isolated MCP",
        },
    )
    assert created.status_code == 200
    server_id = created.json()["server_id"]

    listed = client.get(
        "/v1/mcp/servers",
        headers=h_b,
        params={"org_id": "x", "user_id": "y"},
    ).json()
    assert not any(s["server_id"] == server_id for s in listed["servers"])

    delete = client.delete(
        f"/v1/mcp/servers/{server_id}",
        headers=h_b,
        params={"org_id": "x", "user_id": "y"},
    )
    assert delete.status_code == 404
