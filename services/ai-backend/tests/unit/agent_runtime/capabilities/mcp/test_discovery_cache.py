"""Unit tests for :class:`McpDiscoveryCache`.

Covers hit, miss, TTL expiry, LRU eviction, wildcard invalidation, exact-key
invalidation, and the thundering-herd guarantee on ``get_or_load``.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.capabilities.mcp import (
    LoadedMcpServer,
    McpAuthMode,
    McpConnectionMetadata,
    McpDiscoveryCache,
    McpDiscoveryCacheKey,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)


class DiscoveryCacheMixin:
    """Shared fakes and factories for discovery-cache tests.

    All builder methods are pure — no network, no shared state — so each
    test gets a fresh ``McpDiscoveryCache`` and brand-new
    ``LoadedMcpServer`` records.
    """

    class TestValues:
        class Servers:
            DRIVE = "drive_mcp"
            SLACK = "slack_mcp"
            LINEAR = "linear_mcp"

        class Orgs:
            ACME = "org_acme"
            BETA = "org_beta"

        class Users:
            ALICE = "user_alice"
            BOB = "user_bob"

        class Tools:
            DRIVE_SEARCH = "drive_search"

    def make_card(self, *, name: str) -> McpServerCard:
        return McpServerCard(
            name=name,
            short_description="MCP server for tests.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=10,
        )

    def make_tool(self) -> McpToolDescriptor:
        return McpToolDescriptor(
            name=self.TestValues.Tools.DRIVE_SEARCH,
            description="Search Drive.",
            input_schema={"type": "object"},
            output_shape={"type": "object"},
        )

    def make_metadata(self, *, server_name: str) -> McpConnectionMetadata:
        return McpConnectionMetadata(
            server_name=server_name,
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
        )

    def make_loaded(self, *, server_name: str = "drive_mcp") -> LoadedMcpServer:
        return LoadedMcpServer(
            server_card=self.make_card(name=server_name),
            tools=(self.make_tool(),),
            resources=(),
            connection_metadata=self.make_metadata(server_name=server_name),
        )

    def make_key(
        self,
        *,
        server_name: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> McpDiscoveryCacheKey:
        return McpDiscoveryCacheKey(
            server_name=server_name or self.TestValues.Servers.DRIVE,
            org_id=org_id or self.TestValues.Orgs.ACME,
            user_id=user_id or self.TestValues.Users.ALICE,
        )


class TestMcpDiscoveryCache(DiscoveryCacheMixin):
    def test_put_then_get_returns_equal_but_not_same_record(self) -> None:
        """``get`` after ``put`` returns an equal — but defensively copied — record."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            key = self.make_key()
            original = self.make_loaded()
            await cache.put(key, original)

            retrieved = await cache.get(key)

            assert retrieved is not None
            assert retrieved == original
            # Defensive copy: the cache must not hand out shared mutable refs
            # to a frozen Pydantic record. The cache stores a deep copy on
            # put, so retrieving twice yields two distinct objects.
            second = await cache.get(key)
            assert second is not None
            assert second is not retrieved

        asyncio.run(run())

    def test_get_unknown_key_returns_none_and_increments_miss(self) -> None:
        """Unknown keys return ``None`` and bump the ``misses`` counter."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            result = await cache.get(self.make_key())
            assert result is None
            assert cache.stats().misses == 1
            assert cache.stats().hits == 0

        asyncio.run(run())

    def test_ttl_expiry_evicts_stale_entry(self) -> None:
        """Once ``ttl_seconds`` has elapsed, ``get`` returns ``None`` and removes the entry."""
        # Injected clock so the test is deterministic — no ``asyncio.sleep``.
        current = [0.0]

        def clock() -> float:
            return current[0]

        async def run() -> None:
            cache = McpDiscoveryCache(ttl_seconds=10.0, clock=clock)
            key = self.make_key()
            await cache.put(key, self.make_loaded())

            current[0] = 5.0
            fresh = await cache.get(key)
            assert fresh is not None

            current[0] = 10.5  # past TTL boundary
            stale = await cache.get(key)
            assert stale is None
            stats = cache.stats()
            assert stats.expired == 1
            assert stats.current_size == 0

        asyncio.run(run())

    def test_lru_eviction_drops_oldest_when_full(self) -> None:
        """Filling beyond ``max_entries`` evicts the LRU entry."""

        async def run() -> None:
            cache = McpDiscoveryCache(max_entries=2)
            key_a = self.make_key(server_name="drive_mcp")
            key_b = self.make_key(server_name="slack_mcp")
            key_c = self.make_key(server_name="linear_mcp")

            await cache.put(key_a, self.make_loaded(server_name="drive_mcp"))
            await cache.put(key_b, self.make_loaded(server_name="slack_mcp"))
            # Touch key_a so key_b becomes the LRU.
            assert await cache.get(key_a) is not None
            await cache.put(key_c, self.make_loaded(server_name="linear_mcp"))

            assert await cache.get(key_b) is None
            assert await cache.get(key_a) is not None
            assert await cache.get(key_c) is not None
            assert cache.stats().evictions == 1

        asyncio.run(run())

    def test_invalidate_wildcard_removes_matching_entries(self) -> None:
        """Wildcard fields (``None``) match any value on that field."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            drive_alice_acme = self.make_key(
                server_name="drive_mcp",
                org_id=self.TestValues.Orgs.ACME,
                user_id=self.TestValues.Users.ALICE,
            )
            drive_alice_beta = self.make_key(
                server_name="drive_mcp",
                org_id=self.TestValues.Orgs.BETA,
                user_id=self.TestValues.Users.ALICE,
            )
            slack_alice_acme = self.make_key(
                server_name="slack_mcp",
                org_id=self.TestValues.Orgs.ACME,
                user_id=self.TestValues.Users.ALICE,
            )
            drive_bob_acme = self.make_key(
                server_name="drive_mcp",
                org_id=self.TestValues.Orgs.ACME,
                user_id=self.TestValues.Users.BOB,
            )

            for key in (
                drive_alice_acme,
                drive_alice_beta,
                slack_alice_acme,
                drive_bob_acme,
            ):
                await cache.put(key, self.make_loaded(server_name=key.server_name))

            removed = await cache.invalidate(
                server_name="drive_mcp",
                user_id=self.TestValues.Users.ALICE,
            )

            assert removed == 2
            assert await cache.get(drive_alice_acme) is None
            assert await cache.get(drive_alice_beta) is None
            # slack_mcp survives (different server) and drive_bob survives
            # (different user) — the wildcard org_id is the only "match any".
            assert await cache.get(slack_alice_acme) is not None
            assert await cache.get(drive_bob_acme) is not None

        asyncio.run(run())

    def test_invalidate_exact_match_removes_only_that_entry(self) -> None:
        """All three fields specified narrows the bust to a single key."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            target = self.make_key()
            other = self.make_key(user_id=self.TestValues.Users.BOB)
            await cache.put(target, self.make_loaded())
            await cache.put(other, self.make_loaded())

            removed = await cache.invalidate(
                server_name=target.server_name,
                org_id=target.org_id,
                user_id=target.user_id,
            )

            assert removed == 1
            assert await cache.get(target) is None
            assert await cache.get(other) is not None

        asyncio.run(run())

    def test_get_or_load_thundering_herd_calls_load_once(self) -> None:
        """Two concurrent waiters on a cold key trigger ``load()`` exactly once."""

        call_count = [0]
        loader_started = asyncio.Event()

        async def run() -> None:
            cache = McpDiscoveryCache()
            key = self.make_key()
            loaded = self.make_loaded()

            async def slow_load() -> LoadedMcpServer | None:
                call_count[0] += 1
                # Signal that the first call entered ``load``; the second
                # waiter is parked on the per-key lock and must NOT call
                # ``slow_load`` independently.
                loader_started.set()
                # Yield control so the second waiter can race to enter
                # the lock. The lock serialises them — only the first
                # caller actually runs ``slow_load``.
                await asyncio.sleep(0)
                return loaded

            results = await asyncio.gather(
                cache.get_or_load(key, slow_load),
                cache.get_or_load(key, slow_load),
            )

            assert call_count[0] == 1
            assert all(r is not None and r == loaded for r in results)
            # Both waiters got equal records; defensive copies mean they
            # are not the same instance.
            assert results[0] is not results[1]
            assert loader_started.is_set()

        asyncio.run(run())

    def test_get_or_load_failure_is_not_cached(self) -> None:
        """``load()`` returning ``None`` leaves the cache cold so the next caller retries."""
        call_count = [0]

        async def run() -> None:
            cache = McpDiscoveryCache()
            key = self.make_key()

            async def first_fails_second_succeeds() -> LoadedMcpServer | None:
                call_count[0] += 1
                if call_count[0] == 1:
                    return None
                return self.make_loaded()

            first = await cache.get_or_load(key, first_fails_second_succeeds)
            assert first is None
            second = await cache.get_or_load(key, first_fails_second_succeeds)
            assert second is not None
            assert call_count[0] == 2

        asyncio.run(run())

    def test_get_or_load_propagates_exception_without_caching(self) -> None:
        """Exceptions raised from ``load()`` propagate; the next caller retries."""
        call_count = [0]

        async def run() -> None:
            cache = McpDiscoveryCache()
            key = self.make_key()

            async def first_raises_second_succeeds() -> LoadedMcpServer | None:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("transient upstream failure")
                return self.make_loaded()

            with pytest.raises(RuntimeError):
                await cache.get_or_load(key, first_raises_second_succeeds)
            second = await cache.get_or_load(key, first_raises_second_succeeds)
            assert second is not None
            assert call_count[0] == 2

        asyncio.run(run())

    def test_stats_track_invalidations(self) -> None:
        """``invalidate`` increments the ``invalidations`` counter by the removed count."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            for user in (self.TestValues.Users.ALICE, self.TestValues.Users.BOB):
                await cache.put(
                    self.make_key(user_id=user),
                    self.make_loaded(),
                )
            removed = await cache.invalidate(server_name=self.TestValues.Servers.DRIVE)
            assert removed == 2
            assert cache.stats().invalidations == 2

        asyncio.run(run())

    def test_constructor_rejects_non_positive_ttl_or_max_entries(self) -> None:
        """Defensive guards: TTL and max_entries must be positive."""

        with pytest.raises(ValueError):
            McpDiscoveryCache(ttl_seconds=0)
        with pytest.raises(ValueError):
            McpDiscoveryCache(max_entries=0)
