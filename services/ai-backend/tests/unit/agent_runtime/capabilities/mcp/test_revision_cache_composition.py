"""Conformance coverage for the F8 revision-aware discovery composition."""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpConnectionMetadata,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCacheKey,
)
from agent_runtime.capabilities.mcp.freshness import (
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.capabilities.mcp.revision_resolver import (
    RevisionResolveResult,
    RevisionResolveState,
)
from agent_runtime.capabilities.mcp.revision_wire import BackendMcpRevision


def _key(
    *,
    org_id: str = "org-a",
    user_id: str = "user-a",
) -> McpDiscoveryCacheKey:
    return McpDiscoveryCacheKey(
        server_name="drive",
        org_id=org_id,
        user_id=user_id,
    )


def _loaded(tool_name: str) -> LoadedMcpServer:
    return LoadedMcpServer(
        server_card=McpServerCard(
            name="drive",
            server_id="server-drive",
            short_description="Drive test server.",
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
            server_name="drive",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
        ),
    )


def _revision(
    value: str,
    *,
    server_id: str = "server-drive",
) -> BackendMcpRevision:
    return BackendMcpRevision(
        server_id=server_id,
        revision=value,
        subject_scope_hash="scope-a",
        profile_id="profile-a",
        config_generation=1,
        auth_generation=1,
        transport_generation=1,
        tool_filter_generation=1,
        tool_count=1,
        resource_count=0,
        descriptor_digest=f"digest-{value}",
        observed_at="2026-01-01T00:00:00Z",
        source="backend",
    )


class _Resolver:
    def __init__(self) -> None:
        self.results: dict[tuple[str, str, str], RevisionResolveResult] = {}
        self.registrations: list[tuple[str, str, str, str]] = []
        self.invalidations: list[tuple[str, str, str]] = []

    def set(
        self,
        key: McpDiscoveryCacheKey,
        state: RevisionResolveState,
        revision: str | None = None,
    ) -> None:
        self.results[(key.org_id, key.user_id, key.server_name)] = (
            RevisionResolveResult(
                state,
                _revision(revision) if revision is not None else None,
            )
        )

    async def register(
        self,
        *,
        org_id: str,
        user_id: str,
        server_name: str,
        server_id: str,
    ) -> None:
        self.registrations.append((org_id, user_id, server_name, server_id))

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        server_name: str,
    ) -> RevisionResolveResult:
        return self.results[(org_id, user_id, server_name)]

    async def invalidate(
        self,
        *,
        org_id: str,
        user_id: str,
        server_name: str,
    ) -> None:
        self.invalidations.append((org_id, user_id, server_name))


def _cache(
    resolver: _Resolver | None,
    *,
    enabled: bool = True,
) -> tuple[RevisionAwareMcpDiscoveryCache, McpDiscoveryCache]:
    base = McpDiscoveryCache()
    return (
        RevisionAwareMcpDiscoveryCache(
            base,
            max_staleness_seconds=60,
            revision_resolver=resolver,  # type: ignore[arg-type]
            revision_checks_enabled=enabled,
        ),
        base,
    )


def test_enabled_constructor_requires_revision_resolver() -> None:
    with pytest.raises(ValueError, match="revision_resolver is required"):
        RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=60,
            revision_checks_enabled=True,
        )


@pytest.mark.asyncio
async def test_cold_warm_and_single_flight_use_trusted_source_mapping() -> None:
    resolver = _Resolver()
    key = _key()
    resolver.set(key, RevisionResolveState.FRESH, "revision-a")
    cache, _base = _cache(resolver)
    calls = 0

    async def load() -> LoadedMcpServer:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return _loaded("drive_search")

    first, second = await asyncio.gather(
        cache.get_or_load_cache_entry(key, source_id="server-drive", load=load),
        cache.get_or_load_cache_entry(key, source_id="server-drive", load=load),
    )
    third = await cache.get_or_load_cache_entry(
        key, source_id="server-drive", load=load
    )

    assert calls == 1
    assert first == second == third
    assert first is not second
    assert second is not third
    assert resolver.registrations == [
        ("org-a", "user-a", "drive", "server-drive"),
        ("org-a", "user-a", "drive", "server-drive"),
        ("org-a", "user-a", "drive", "server-drive"),
    ]


@pytest.mark.asyncio
async def test_opaque_revision_change_invalidates_and_reloads() -> None:
    resolver = _Resolver()
    key = _key()
    resolver.set(key, RevisionResolveState.FRESH, "revision-z")
    cache, _base = _cache(resolver)
    loaded = ["tool-z", "tool-a"]

    async def load() -> LoadedMcpServer:
        return _loaded(loaded.pop(0))

    first = await cache.get_or_load_cache_entry(
        key, source_id="server-drive", load=load
    )
    resolver.set(key, RevisionResolveState.FRESH, "revision-a")
    second = await cache.get_or_load_cache_entry(
        key, source_id="server-drive", load=load
    )

    assert first is not None and first.tools[0].name == "tool-z"
    assert second is not None and second.tools[0].name == "tool-a"
    assert loaded == []


@pytest.mark.parametrize(
    "state",
    [RevisionResolveState.NOT_FOUND, RevisionResolveState.UNAVAILABLE],
)
@pytest.mark.asyncio
async def test_missing_revision_authority_loads_live_without_admission(
    state: RevisionResolveState,
) -> None:
    resolver = _Resolver()
    key = _key()
    resolver.set(key, RevisionResolveState.FRESH, "revision-a")
    cache, base = _cache(resolver)
    calls = 0

    async def load() -> LoadedMcpServer:
        nonlocal calls
        calls += 1
        return _loaded(f"tool-{calls}")

    await cache.get_or_load_cache_entry(key, source_id="server-drive", load=load)
    resolver.set(key, state)
    first_live = await cache.get_or_load_cache_entry(
        key, source_id="server-drive", load=load
    )
    second_live = await cache.get_or_load_cache_entry(
        key, source_id="server-drive", load=load
    )

    assert first_live is not None and first_live.tools[0].name == "tool-2"
    assert second_live is not None and second_live.tools[0].name == "tool-3"
    assert await base.get(key) is None
    assert calls == 3


@pytest.mark.asyncio
async def test_same_source_id_never_crosses_subjects() -> None:
    resolver = _Resolver()
    alice = _key(org_id="org-a", user_id="alice")
    bob = _key(org_id="org-b", user_id="bob")
    resolver.set(alice, RevisionResolveState.FRESH, "alice-r1")
    resolver.set(bob, RevisionResolveState.FRESH, "bob-r1")
    cache, _base = _cache(resolver)
    calls = {"alice": 0, "bob": 0}

    async def load_alice() -> LoadedMcpServer:
        calls["alice"] += 1
        return _loaded(f"alice-{calls['alice']}")

    async def load_bob() -> LoadedMcpServer:
        calls["bob"] += 1
        return _loaded(f"bob-{calls['bob']}")

    await cache.get_or_load_cache_entry(
        alice, source_id="server-drive", load=load_alice
    )
    await cache.get_or_load_cache_entry(bob, source_id="server-drive", load=load_bob)
    resolver.set(alice, RevisionResolveState.FRESH, "alice-r2")
    await cache.get_or_load_cache_entry(
        alice, source_id="server-drive", load=load_alice
    )
    bob_warm = await cache.get_or_load_cache_entry(
        bob, source_id="server-drive", load=load_bob
    )

    assert calls == {"alice": 2, "bob": 1}
    assert bob_warm is not None and bob_warm.tools[0].name == "bob-1"


@pytest.mark.asyncio
async def test_invalidation_racing_load_neither_publishes_nor_returns() -> None:
    resolver = _Resolver()
    key = _key()
    resolver.set(key, RevisionResolveState.FRESH, "revision-a")
    cache, base = _cache(resolver)
    started = asyncio.Event()
    release = asyncio.Event()

    async def load() -> LoadedMcpServer:
        started.set()
        await release.wait()
        return _loaded("stale-tool")

    pending = asyncio.create_task(
        cache.get_or_load_cache_entry(
            key,
            source_id="server-drive",
            load=load,
        )
    )
    await started.wait()
    await cache.invalidate(
        server_name=key.server_name,
        org_id=key.org_id,
        user_id=key.user_id,
    )
    release.set()

    assert await pending is None
    assert await base.get(key) is None
    assert resolver.invalidations == [("org-a", "user-a", "drive")]


@pytest.mark.asyncio
async def test_enabled_missing_source_id_loads_live_without_admission() -> None:
    resolver = _Resolver()
    key = _key()
    cache, base = _cache(resolver)
    calls = 0

    async def load() -> LoadedMcpServer:
        nonlocal calls
        calls += 1
        return _loaded(f"live-{calls}")

    first = await cache.get_or_load_cache_entry(key, source_id=None, load=load)
    second = await cache.get_or_load_cache_entry(key, source_id=None, load=load)

    assert first is not None and first.tools[0].name == "live-1"
    assert second is not None and second.tools[0].name == "live-2"
    assert await base.get(key) is None
    assert resolver.registrations == []


@pytest.mark.asyncio
async def test_exact_invalidate_reaches_resolver_without_wrapper_metadata() -> None:
    resolver = _Resolver()
    key = _key()
    cache, _base = _cache(resolver)

    removed = await cache.invalidate(
        server_name=key.server_name,
        org_id=key.org_id,
        user_id=key.user_id,
    )

    assert removed == 0
    assert resolver.invalidations == [("org-a", "user-a", "drive")]


@pytest.mark.asyncio
async def test_feature_off_is_base_cache_parity() -> None:
    key = _key()
    direct = McpDiscoveryCache()
    wrapped_base = McpDiscoveryCache()
    wrapped = RevisionAwareMcpDiscoveryCache(
        wrapped_base,
        max_staleness_seconds=60,
        revision_checks_enabled=False,
    )
    direct_calls = 0
    wrapped_calls = 0

    async def direct_load() -> LoadedMcpServer:
        nonlocal direct_calls
        direct_calls += 1
        return _loaded("drive_search")

    async def wrapped_load() -> LoadedMcpServer:
        nonlocal wrapped_calls
        wrapped_calls += 1
        return _loaded("drive_search")

    direct_first = await direct.get_or_load_cache_entry(
        key, source_id="server-drive", load=direct_load
    )
    direct_second = await direct.get_or_load_cache_entry(
        key, source_id="server-drive", load=direct_load
    )
    wrapped_first = await wrapped.get_or_load_cache_entry(
        key, source_id="server-drive", load=wrapped_load
    )
    wrapped_second = await wrapped.get_or_load_cache_entry(
        key, source_id="server-drive", load=wrapped_load
    )

    assert direct_first is not None and wrapped_first is not None
    assert direct_second is not None and wrapped_second is not None
    assert direct_first.tools == wrapped_first.tools
    assert direct_second.tools == wrapped_second.tools
    assert direct_first == direct_second
    assert wrapped_first == wrapped_second
    assert direct_calls == wrapped_calls == 1
    assert direct.stats() == wrapped_base.stats()
    assert (
        await direct.invalidate(
            server_name=key.server_name,
            org_id=key.org_id,
            user_id=key.user_id,
        )
        == await wrapped.invalidate(
            server_name=key.server_name,
            org_id=key.org_id,
            user_id=key.user_id,
        )
        == 1
    )
    assert direct.stats() == wrapped_base.stats()
