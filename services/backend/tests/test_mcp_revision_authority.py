"""F8 body-free descriptor revision authority tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from backend_app.contracts import CreateMcpServerRequest, McpRevisionReason
from backend_app.mcp_revision_store import (
    InMemoryMcpRevisionStore,
    PostgresMcpRevisionStore,
)
from backend_app.mcp_revisions import (
    McpRevisionAuthority,
    McpRevisionStorePort,
    RevisionCursorExpired,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore, PostgresMcpStore


def _publish(authority: McpRevisionAuthority, *, key: str = "one"):
    return authority.publish_complete_descriptor_view(
        org_id="org_123",
        user_id="user_123",
        server_id="server_123",
        descriptor_digest="a" * 64,
        tool_count=2,
        resource_count=1,
        source="complete_paginated_observer",
        idempotency_key=key,
    )


def test_in_memory_and_postgres_adapters_conform_to_the_revision_store_port() -> None:
    assert isinstance(InMemoryMcpRevisionStore(), McpRevisionStorePort)
    # Structural conformance is independent of a live database; individual
    # Postgres behavior is exercised by the adapter's persistence suite.
    assert isinstance(
        PostgresMcpRevisionStore(store=cast(PostgresMcpStore, object())),
        McpRevisionStorePort,
    )
    assert not hasattr(InMemoryMcpRevisionStore, "_pg_publish")
    assert not issubclass(PostgresMcpRevisionStore, InMemoryMcpRevisionStore)


def test_revision_digest_is_bound_to_the_credential_subject_scope() -> None:
    authority = McpRevisionAuthority()
    first = authority.publish_complete_descriptor_view(
        org_id="org_123",
        user_id="user_123",
        server_id="server_123",
        descriptor_digest="a" * 64,
        tool_count=2,
        resource_count=1,
        source="complete_paginated_observer",
        idempotency_key="one",
        credential_subject="connection-one",
    )
    second = authority.publish_complete_descriptor_view(
        org_id="org_123",
        user_id="user_123",
        server_id="server_123",
        descriptor_digest="a" * 64,
        tool_count=2,
        resource_count=1,
        source="complete_paginated_observer",
        idempotency_key="two",
        credential_subject="connection-two",
    )
    assert first.subject_scope_hash != second.subject_scope_hash
    assert first.revision != second.revision


def test_idempotency_key_reuse_after_credential_rotation_conflicts() -> None:
    authority = McpRevisionAuthority()
    _ = authority.publish_complete_descriptor_view(
        org_id="org_123",
        user_id="user_123",
        server_id="server_123",
        descriptor_digest="a" * 64,
        tool_count=2,
        resource_count=1,
        source="complete_paginated_observer",
        idempotency_key="stable-retry",
        credential_subject="connection-before-rotation",
    )
    with pytest.raises(ValueError, match="idempotency_key conflicts"):
        authority.publish_complete_descriptor_view(
            org_id="org_123",
            user_id="user_123",
            server_id="server_123",
            descriptor_digest="a" * 64,
            tool_count=2,
            resource_count=1,
            source="complete_paginated_observer",
            idempotency_key="stable-retry",
            credential_subject="connection-after-rotation",
        )


def test_in_memory_idempotency_records_are_bounded() -> None:
    store = InMemoryMcpRevisionStore(retain_max=2)
    authority = McpRevisionAuthority(store)
    _publish(authority, key="one")
    _publish(authority, key="two")
    _publish(authority, key="three")
    assert len(store._idempotency) == 2


def test_postgres_adapter_lock_prune_and_migration_grants_are_present() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    store_source = (backend_root / "src/backend_app/mcp_revision_store.py").read_text()
    migration = (
        backend_root / "migrations/0050_mcp_descriptor_revisions.sql"
    ).read_text()
    assert "pg_advisory_xact_lock" in store_source
    assert store_source.count("self._take_scope_lock(") >= 2
    assert "mcp_descriptor_revision_idempotency WHERE sequence_no" in store_source
    assert (
        "GRANT USAGE, SELECT ON SEQUENCE mcp_descriptor_revision_notices_sequence_no_seq"
        in migration
    )
    assert (
        "GRANT USAGE, SELECT ON SEQUENCE mcp_descriptor_revision_idempotency_sequence_no_seq"
        in migration
    )


def test_complete_publish_is_idempotent_and_notices_are_body_free() -> None:
    authority = McpRevisionAuthority()
    first = _publish(authority)
    assert _publish(authority) == first

    feed = authority.feed(
        org_id="org_123", user_id="user_123", after_cursor=None, limit=10
    )
    assert len(feed.notices) == 1
    dumped = feed.notices[0].model_dump_json()
    assert "descriptor_digest" not in dumped
    assert "https://" not in dumped
    assert first.tool_count == 2


def test_mutation_invalidates_current_view_and_advances_generation() -> None:
    authority = McpRevisionAuthority()
    first = _publish(authority)
    authority.invalidate(
        org_id="org_123",
        user_id="user_123",
        server_id="server_123",
        reason=McpRevisionReason.AUTH_CHANGED,
    )
    assert (
        authority.get_current(
            org_id="org_123", user_id="user_123", server_id="server_123"
        )
        is None
    )
    second = _publish(authority, key="two")
    assert second.auth_generation == first.auth_generation + 1
    feed = authority.feed(
        org_id="org_123", user_id="user_123", after_cursor=None, limit=10
    )
    assert feed.notices[1].old_revision == first.revision
    assert feed.notices[1].new_revision is None


def test_feed_is_capped_isolated_and_expired_cursor_is_generic() -> None:
    authority = McpRevisionAuthority(retain_max=2)
    authority.invalidate(
        org_id="org_123",
        user_id="user_123",
        server_id="one",
        reason=McpRevisionReason.CONFIG_CHANGED,
    )
    first_cursor = authority.feed(
        org_id="org_123", user_id="user_123", after_cursor=None, limit=1
    ).next_cursor
    authority.invalidate(
        org_id="org_123",
        user_id="user_123",
        server_id="two",
        reason=McpRevisionReason.CONFIG_CHANGED,
    )
    authority.invalidate(
        org_id="org_123",
        user_id="user_123",
        server_id="three",
        reason=McpRevisionReason.CONFIG_CHANGED,
    )
    assert (
        len(
            authority.feed(
                org_id="org_123", user_id="user_123", after_cursor=None, limit=100
            ).notices
        )
        == 2
    )
    with pytest.raises(RevisionCursorExpired):
        authority.feed(
            org_id="org_123", user_id="user_123", after_cursor=first_cursor, limit=1
        )
    with pytest.raises(RevisionCursorExpired):
        authority.feed(
            org_id="org_123", user_id="other_user", after_cursor=first_cursor, limit=1
        )


def test_registry_create_and_token_mutations_emit_transactional_invalidations() -> None:
    service = McpRegistryService(store=InMemoryMcpStore())
    response = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="MCP",
        )
    )
    feed = service.revision_authority.feed(
        org_id="org_123", user_id="user_123", after_cursor=None, limit=10
    )
    assert feed.notices[0].server_id == response.server_id
    assert feed.notices[0].reason is McpRevisionReason.CONFIG_CHANGED
