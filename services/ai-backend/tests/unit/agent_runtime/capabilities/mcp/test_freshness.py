"""Tests for subject-scoped MCP descriptor freshness control."""

from __future__ import annotations

import asyncio

from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpConnectionMetadata,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache
from agent_runtime.capabilities.mcp.freshness import (
    McpDescriptorFreshnessRequest,
    McpDescriptorFreshnessState,
    McpDescriptorRevision,
    McpDescriptorSubject,
    RevisionAwareMcpDiscoveryCache,
)


def _subject(*, user_id: str) -> McpDescriptorSubject:
    return McpDescriptorSubject(org_id="org-acme", user_id=user_id)


def _request(
    *,
    user_id: str = "user-alice",
    server_name: str = "drive",
    revision: str = "revision-1",
) -> McpDescriptorFreshnessRequest:
    return McpDescriptorFreshnessRequest(
        server_name=server_name,
        subject=_subject(user_id=user_id),
        revision=McpDescriptorRevision(value=revision),
    )


def _loaded(*, server_name: str, tool_name: str) -> LoadedMcpServer:
    return LoadedMcpServer(
        server_card=McpServerCard(
            name=server_name,
            short_description="Test MCP server.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        ),
        tools=(
            McpToolDescriptor(
                name=tool_name,
                description="A subject-visible test tool.",
                input_schema={"type": "object"},
                output_shape={"type": "object"},
            ),
        ),
        resources=(),
        connection_metadata=McpConnectionMetadata(
            server_name=server_name,
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
        ),
    )


def test_subjects_with_same_server_never_share_descriptors() -> None:
    async def run() -> None:
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
        )
        alice = _request(user_id="user-alice")
        bob = _request(user_id="user-bob")
        await cache.put(
            alice,
            _loaded(server_name="drive", tool_name="alice_search"),
        )
        await cache.put(
            bob,
            _loaded(server_name="drive", tool_name="bob_search"),
        )

        alice_result = await cache.get(alice)
        bob_result = await cache.get(bob)

        assert alice_result.record is not None
        assert alice_result.record.tools[0].name == "alice_search"
        assert bob_result.record is not None
        assert bob_result.record.tools[0].name == "bob_search"

    asyncio.run(run())


def test_revision_change_invalidates_only_exact_subject_entry() -> None:
    async def run() -> None:
        base = McpDiscoveryCache()
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
        )
        alice_v1 = _request(user_id="user-alice", revision="revision-1")
        alice_v2 = _request(user_id="user-alice", revision="revision-2")
        bob_v1 = _request(user_id="user-bob", revision="revision-1")
        await cache.put(
            alice_v1,
            _loaded(server_name="drive", tool_name="alice_search"),
        )
        await cache.put(
            bob_v1,
            _loaded(server_name="drive", tool_name="bob_search"),
        )

        changed = await cache.get(alice_v2)

        assert changed.record is None
        assert changed.decision.state is McpDescriptorFreshnessState.REVISION_CHANGED
        assert changed.decision.cached_revision == alice_v1.revision
        assert await base.get(alice_v1.cache_key()) is None
        assert (await cache.get(bob_v1)).record is not None

    asyncio.run(run())


def test_subject_invalidation_cannot_widen_to_other_subject_or_server() -> None:
    async def run() -> None:
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
        )
        alice_drive = _request(user_id="user-alice", server_name="drive")
        alice_slack = _request(user_id="user-alice", server_name="slack")
        bob_drive = _request(user_id="user-bob", server_name="drive")
        for request, tool_name in (
            (alice_drive, "alice_drive_search"),
            (alice_slack, "alice_slack_search"),
            (bob_drive, "bob_drive_search"),
        ):
            await cache.put(
                request,
                _loaded(
                    server_name=request.server_name,
                    tool_name=tool_name,
                ),
            )

        removed = await cache.invalidate_subject(
            alice_drive.subject,
            server_name="drive",
        )

        assert removed.cached_records_removed == 1
        assert removed.revision_records_removed == 1
        assert (await cache.get(alice_drive)).record is None
        assert (await cache.get(alice_slack)).record is not None
        assert (await cache.get(bob_drive)).record is not None

    asyncio.run(run())


def test_max_staleness_is_enforced_before_longer_base_ttl() -> None:
    current = [0.0]

    def clock() -> float:
        return current[0]

    async def run() -> None:
        base = McpDiscoveryCache(ttl_seconds=100, clock=clock)
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=10,
            clock=clock,
        )
        request = _request()
        await cache.put(
            request,
            _loaded(server_name="drive", tool_name="drive_search"),
        )

        current[0] = 9.999
        fresh = await cache.get(request)
        assert fresh.record is not None
        assert fresh.decision.state is McpDescriptorFreshnessState.FRESH
        assert fresh.decision.reuse_allowed is True

        current[0] = 10.0
        stale = await cache.get(request)
        assert stale.record is None
        assert (
            stale.decision.state is McpDescriptorFreshnessState.MAX_STALENESS_EXCEEDED
        )
        assert stale.decision.age_seconds == 10.0
        assert await base.get(request.cache_key()) is None

    asyncio.run(run())


def test_untracked_legacy_entry_is_never_reused_or_invalidated() -> None:
    async def run() -> None:
        base = McpDiscoveryCache()
        request = _request()
        legacy = _loaded(server_name="drive", tool_name="legacy_search")
        await base.put(request.cache_key(), legacy)
        cache = RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
        )

        result = await cache.get(request)

        assert result.record is None
        assert result.decision.state is McpDescriptorFreshnessState.NOT_TRACKED
        assert await base.get(request.cache_key()) == legacy

    asyncio.run(run())


def test_get_or_load_coalesces_same_subject_and_revision() -> None:
    async def run() -> None:
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
        )
        request = _request()
        calls = 0

        async def load() -> LoadedMcpServer:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return _loaded(server_name="drive", tool_name="drive_search")

        first, second = await asyncio.gather(
            cache.get_or_load(request, load),
            cache.get_or_load(request, load),
        )

        assert calls == 1
        assert first.record is not None
        assert second.record is not None
        assert first.loaded is True
        assert second.loaded is False
        assert second.decision.state is McpDescriptorFreshnessState.FRESH

    asyncio.run(run())
