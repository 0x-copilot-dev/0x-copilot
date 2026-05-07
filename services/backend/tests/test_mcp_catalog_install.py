"""Catalog endpoint + install flow for ``McpRegistryService``.

PR 4.4.6 — replaces the prior auto-seed semantics. The catalog is now
read-only (``list_catalog``) and rows are created only by an explicit
``install_from_catalog`` call.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    InstallMcpServerRequest,
    McpAuthState,
    McpOAuthClientRequest,
)
from backend_app.mcp_catalog import DEFAULT_CATALOG, catalog_by_slug
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


def _service() -> McpRegistryService:
    return McpRegistryService(store=InMemoryMcpStore())


# --- Catalog endpoint ------------------------------------------------------


def test_catalog_endpoint_returns_curated_entries() -> None:
    service = _service()
    catalog = service.list_catalog()

    assert len(catalog.entries) == len(DEFAULT_CATALOG)

    # Every entry has the wire shape the frontend depends on.
    for entry in catalog.entries:
        assert entry.slug
        assert entry.display_name
        assert entry.url
        assert entry.transport in {"http", "sse", "stdio"}
        assert entry.auth_mode in {"none", "oauth2", "api_key", "service_account"}
        assert isinstance(entry.requires_pre_registered_client, bool)


def test_catalog_marks_pre_registered_vendors_per_docs() -> None:
    service = _service()
    by_slug = {e.slug: e for e in service.list_catalog().entries}

    # 6 vendors require a pre-registered OAuth client (Atlassian, GitHub,
    # Intercom, PayPal, Plaid, Square per PRD §2.1).
    needs_setup = {
        slug for slug, entry in by_slug.items() if entry.requires_pre_registered_client
    }
    assert needs_setup == {
        "atlassian",
        "github",
        "intercom",
        "paypal",
        "plaid",
        "square",
    }


def test_catalog_endpoint_via_http() -> None:
    app = create_app(_service())
    client = TestClient(app)

    response = client.get("/v1/mcp/catalog")
    assert response.status_code == 200
    body = response.json()
    assert "entries" in body
    assert len(body["entries"]) == len(DEFAULT_CATALOG)


# --- Install flow ----------------------------------------------------------


def test_install_creates_row_with_brand_metadata() -> None:
    service = _service()

    response = service.install_from_catalog(
        InstallMcpServerRequest(
            org_id="org_a",
            user_id="user_a",
            slug="linear",
        )
    )

    catalog_entry = catalog_by_slug()["linear"]
    assert response.server_id == "seed:linear"
    assert response.display_name == catalog_entry.display_name
    assert response.url == catalog_entry.url
    assert response.brand_color == catalog_entry.brand_color
    assert response.scopes_summary == catalog_entry.scopes_summary
    assert tuple(response.default_scopes) == catalog_entry.default_scopes
    assert response.description == catalog_entry.description
    # Newly installed: enabled and unauthenticated until OAuth completes.
    assert response.enabled is True
    assert response.auth_state == McpAuthState.UNAUTHENTICATED


def test_install_is_idempotent_on_slug() -> None:
    service = _service()
    request = InstallMcpServerRequest(org_id="org_a", user_id="user_a", slug="linear")

    first = service.install_from_catalog(request)
    second = service.install_from_catalog(request)

    assert first.server_id == second.server_id
    listed = service.list_servers(org_id="org_a", user_id="user_a")
    # Exactly one row exists despite two install calls.
    matching = [s for s in listed.servers if s.server_id == "seed:linear"]
    assert len(matching) == 1


def test_install_requires_pre_registered_client_when_flagged() -> None:
    service = _service()
    request = InstallMcpServerRequest(
        org_id="org_a", user_id="user_a", slug="atlassian"
    )

    with pytest.raises(ValueError, match="Pre-registered OAuth client required"):
        service.install_from_catalog(request)

    # No row was created on the failed install.
    listed = service.list_servers(org_id="org_a", user_id="user_a")
    assert listed.servers == ()


def test_install_with_pre_registered_client_succeeds() -> None:
    service = _service()
    response = service.install_from_catalog(
        InstallMcpServerRequest(
            org_id="org_a",
            user_id="user_a",
            slug="atlassian",
            oauth_client=McpOAuthClientRequest(
                client_id="atl-client-123",
                client_secret="atl-secret",
                scope="read:jira",
                authorization_endpoint="https://auth.atlassian.com/authorize",
                token_endpoint="https://auth.atlassian.com/token",
            ),
        )
    )

    assert response.server_id == "seed:atlassian"
    assert response.oauth_client_configured is True
    # Secret never round-trips in the response payload.
    assert "atl-secret" not in response.model_dump_json()


def test_install_unknown_slug_raises_value_error() -> None:
    service = _service()
    with pytest.raises(ValueError, match="Unknown catalog entry"):
        service.install_from_catalog(
            InstallMcpServerRequest(
                org_id="org_a",
                user_id="user_a",
                slug="not-real-server",
            )
        )


def test_install_per_user_scoping() -> None:
    service = _service()
    service.install_from_catalog(
        InstallMcpServerRequest(org_id="org_a", user_id="user_a", slug="linear")
    )

    user_a = service.list_servers(org_id="org_a", user_id="user_a")
    user_b = service.list_servers(org_id="org_a", user_id="user_b")

    assert len(user_a.servers) == 1
    assert user_b.servers == ()


def test_list_servers_no_longer_seeds() -> None:
    service = _service()

    # First call must NOT auto-seed the catalog. Plan A: ``connectors.servers``
    # reflects only what the user has explicitly installed.
    listed = service.list_servers(org_id="org_a", user_id="user_a")
    assert listed.servers == ()


def test_install_audits_with_correct_action_string() -> None:
    store = InMemoryMcpStore()
    service = McpRegistryService(store=store)

    service.install_from_catalog(
        InstallMcpServerRequest(org_id="org_a", user_id="user_a", slug="linear")
    )

    audit_actions = [event.action for event in store.audit_events]
    assert "mcp_server_installed" in audit_actions


# --- HTTP-level coverage ---------------------------------------------------


def test_install_http_returns_422_when_pre_registered_missing() -> None:
    app = create_app(_service())
    client = TestClient(app)

    response = client.post(
        "/v1/mcp/servers/install",
        json={"org_id": "org_a", "user_id": "user_a", "slug": "atlassian"},
    )
    assert response.status_code == 422
    assert "Pre-registered OAuth client required" in response.json()["detail"]


def test_install_http_returns_404_for_unknown_slug() -> None:
    app = create_app(_service())
    client = TestClient(app)

    response = client.post(
        "/v1/mcp/servers/install",
        json={"org_id": "org_a", "user_id": "user_a", "slug": "ghost-slug"},
    )
    assert response.status_code == 404
    assert "Unknown catalog entry" in response.json()["detail"]
