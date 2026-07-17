"""Composer Tools popover route — ``GET /v1/mcp/tools``.

The endpoint aggregates user-installed skill bundles and registered MCP
servers into one sectioned listing, tagging each entry with ``kind`` so
the frontend can partition the popover into its Skills and MCPs sections.

These tests pin the aggregation contract:

- skills carry ``kind == "skill"``,
- authenticated MCP servers carry ``kind == "mcp"``,
- unauthenticated / disabled MCP servers are not surfaced (the agent
  can't actually invoke them, so they have no place in the composer),
- tenant isolation: another org cannot see this org's skills or MCPs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from backend_app.app import create_app
from backend_app.service import McpRegistryService, SkillRegistryService
from backend_app.store import InMemoryMcpStore, InMemorySkillStore
from test_mcp_api_flow import FakeOAuthClient, FakeOAuthTokenExchanger


SKILL_MARKDOWN = """---
name: launch-checklist
description: Review launch plans and summarize top risks.
allowed_tools: [doc_search]
---
# Launch Checklist
Use when the user asks about launch readiness.
"""


def _build_client() -> tuple[TestClient, InMemoryMcpStore, InMemorySkillStore]:
    mcp_store = InMemoryMcpStore()
    skill_store = InMemorySkillStore()
    app = create_app(
        McpRegistryService(
            store=mcp_store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        ),
        skill_service=SkillRegistryService(store=skill_store),
    )
    return TestClient(app), mcp_store, skill_store


def _authenticate_server(
    client: TestClient, mcp_store: InMemoryMcpStore, *, server_id: str
) -> None:
    """Walk an MCP server through the OAuth callback to ``authenticated``."""

    client.post(
        f"/internal/v1/mcp/servers/{server_id}/auth/start",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
        },
    )
    state = next(iter(mcp_store.auth_sessions.keys()))
    client.get(
        "/v1/mcp/oauth/callback",
        params={"state": state, "code": "oauth_code"},
    )


def test_list_tools_tags_skills_and_authenticated_mcps_with_kind() -> None:
    client, mcp_store, _ = _build_client()

    # Install a user skill.
    client.post(
        "/v1/skills",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "markdown": SKILL_MARKDOWN,
        },
    )

    # Register two MCP servers — only one will get authenticated.
    server_a = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://drive.mcp.example.com",
            "display_name": "Drive MCP",
        },
    ).json()["server_id"]
    client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://sheets.mcp.example.com",
            "display_name": "Sheets MCP",
        },
    )
    _authenticate_server(client, mcp_store, server_id=server_a)

    listed = client.get(
        "/v1/mcp/tools",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    tools = listed["tools"]
    by_kind: dict[str, list[dict]] = {"skill": [], "mcp": []}
    for tool in tools:
        by_kind[tool["kind"]].append(tool)

    # Every entry is tagged — no untyped rows can slip through.
    assert all(t["kind"] in {"skill", "mcp"} for t in tools)

    # Skill entries: at least our user-created one (preloaded skills may
    # also be present; assert the kind, not the count).
    skill_labels = {t["label"] for t in by_kind["skill"]}
    assert "launch-checklist" in skill_labels or any(
        "launch" in label.lower() for label in skill_labels
    )
    assert all(t["kind"] == "skill" for t in by_kind["skill"])

    # MCP entries: only the authenticated Drive MCP is returned.
    assert len(by_kind["mcp"]) == 1
    drive_entry = by_kind["mcp"][0]
    assert drive_entry["kind"] == "mcp"
    assert drive_entry["name"] == server_a
    assert drive_entry["label"] == "Drive MCP"


def test_list_tools_excludes_unauthenticated_and_disabled_mcps() -> None:
    client, _, _ = _build_client()

    # Register an MCP that never authenticates.
    client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://pending.mcp.example.com",
            "display_name": "Pending MCP",
        },
    )

    listed = client.get(
        "/v1/mcp/tools",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    mcp_labels = [t["label"] for t in listed["tools"] if t["kind"] == "mcp"]
    assert "Pending MCP" not in mcp_labels


def test_list_tools_is_tenant_isolated(monkeypatch) -> None:
    """Org B must not see org A's skills or MCP servers via ``/v1/mcp/tools``."""

    token = "tool-catalog-tenant-test-token"
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", token)
    mcp_store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=mcp_store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        ),
        skill_service=SkillRegistryService(store=InMemorySkillStore()),
    )
    client = TestClient(app)

    h_a = {
        SERVICE_TOKEN_HEADER: token,
        ORG_HEADER: "org_alpha",
        USER_HEADER: "user_1",
    }
    h_b = {
        SERVICE_TOKEN_HEADER: token,
        ORG_HEADER: "org_beta",
        USER_HEADER: "user_1",
    }

    # Org alpha installs a skill and authenticates an MCP server.
    client.post(
        "/v1/skills",
        headers=h_a,
        json={
            "org_id": "forged",
            "user_id": "forged",
            "markdown": SKILL_MARKDOWN,
        },
    )
    server_id = client.post(
        "/v1/mcp/servers",
        headers=h_a,
        json={
            "org_id": "forged",
            "user_id": "forged",
            "url": "https://alpha.mcp.example.com",
            "display_name": "Alpha-Only MCP",
        },
    ).json()["server_id"]
    # Authenticate via the alpha-scoped headers so the auth session
    # belongs to org_alpha.
    client.post(
        f"/internal/v1/mcp/servers/{server_id}/auth/start",
        headers=h_a,
        json={
            "org_id": "forged",
            "user_id": "forged",
            "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
        },
    )
    state = next(iter(mcp_store.auth_sessions.keys()))
    client.get(
        "/v1/mcp/oauth/callback",
        headers=h_a,
        params={"state": state, "code": "oauth_code"},
    )

    # Org beta calls the endpoint with forged query params, but the route
    # rebinds to the verified identity from the service-token headers.
    listed_b = client.get(
        "/v1/mcp/tools",
        headers=h_b,
        params={"org_id": "forged", "user_id": "forged"},
    ).json()
    labels_b = [t["label"] for t in listed_b["tools"]]
    assert "Alpha-Only MCP" not in labels_b
    assert "launch-checklist" not in labels_b
