"""Catalog-seed behaviour for ``McpRegistryService.list_servers``."""

from __future__ import annotations

from backend_app.contracts import (
    CreateMcpServerRequest,
    McpAuthState,
    McpServerHealth,
    UpdateMcpServerRequest,
)
from backend_app.mcp_catalog import DEFAULT_CATALOG
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


def _service() -> McpRegistryService:
    return McpRegistryService(store=InMemoryMcpStore())


def test_first_list_seeds_full_catalog_disabled() -> None:
    service = _service()

    listed = service.list_servers(org_id="org_a", user_id="user_a")

    assert len(listed.servers) == len(DEFAULT_CATALOG)
    seeded_ids = {server.server_id for server in listed.servers}
    catalog_ids = {entry.server_id for entry in DEFAULT_CATALOG}
    assert seeded_ids == catalog_ids
    for server in listed.servers:
        assert server.enabled is False
        assert server.health == McpServerHealth.DISABLED


def test_seeded_servers_are_hidden_from_internal_cards() -> None:
    service = _service()

    service.list_servers(org_id="org_a", user_id="user_a")
    cards = service.list_internal_cards(org_id="org_a", user_id="user_a")

    # Disabled connectors must not be exposed to the agent runtime.
    assert cards.servers == ()


def test_seed_runs_once_then_respects_user_removals() -> None:
    service = _service()
    first = service.list_servers(org_id="org_a", user_id="user_a")
    assert len(first.servers) == len(DEFAULT_CATALOG)

    target = first.servers[0]
    deleted = service.delete_server(
        org_id="org_a", user_id="user_a", server_id=target.server_id
    )
    assert deleted is True

    after_remove = service.list_servers(org_id="org_a", user_id="user_a")

    # Removing one entry leaves the user with N-1 servers; subsequent
    # list calls must NOT re-seed (we only auto-seed when the user has
    # zero servers).
    assert len(after_remove.servers) == len(DEFAULT_CATALOG) - 1
    assert target.server_id not in {s.server_id for s in after_remove.servers}


def test_user_with_existing_server_is_never_auto_seeded() -> None:
    service = _service()
    service.create_server(
        CreateMcpServerRequest(
            org_id="org_a",
            user_id="user_a",
            url="https://mcp.example.com",
            display_name="My MCP",
        )
    )

    listed = service.list_servers(org_id="org_a", user_id="user_a")

    # Existing user (one custom server) doesn't get the seeded catalog
    # injected on top — auto-seed is a fresh-user-only behaviour.
    assert len(listed.servers) == 1
    assert all(not server.server_id.startswith("seed:") for server in listed.servers)


def test_seed_ids_are_stable_across_users() -> None:
    service = _service()

    a = service.list_servers(org_id="org_a", user_id="user_a")
    b = service.list_servers(org_id="org_b", user_id="user_b")

    assert {server.server_id for server in a.servers} == {
        server.server_id for server in b.servers
    }


def test_reset_catalog_re_adds_removed_entries() -> None:
    service = _service()
    first = service.list_servers(org_id="org_a", user_id="user_a")
    target = first.servers[0]

    service.delete_server(org_id="org_a", user_id="user_a", server_id=target.server_id)

    refreshed = service.reset_catalog(org_id="org_a", user_id="user_a")

    assert target.server_id in {s.server_id for s in refreshed.servers}
    # Reset is idempotent: count matches the catalog regardless of how
    # many removals happened in between.
    assert len(refreshed.servers) == len(DEFAULT_CATALOG)


def test_reset_catalog_preserves_enabled_state_on_existing_seeds() -> None:
    service = _service()
    first = service.list_servers(org_id="org_a", user_id="user_a")
    target = first.servers[0]

    enabled = service.update_server(
        org_id="org_a",
        user_id="user_a",
        server_id=target.server_id,
        request=UpdateMcpServerRequest(enabled=True),
    )
    assert enabled.enabled is True

    refreshed = service.reset_catalog(org_id="org_a", user_id="user_a")

    same = next(s for s in refreshed.servers if s.server_id == target.server_id)
    # User-enabled seeds must NOT be reset to disabled by reset_catalog.
    assert same.enabled is True


def test_seeded_oauth_servers_default_to_unauthenticated() -> None:
    service = _service()

    listed = service.list_servers(org_id="org_a", user_id="user_a")

    for server in listed.servers:
        assert server.auth_state == McpAuthState.UNAUTHENTICATED
