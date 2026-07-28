"""Integration tests for :class:`McpLoader` + :class:`McpDiscoveryCache`.

These tests exist to confirm two invariants:

1. ``McpLoader(cache=None)`` matches pre-cache behaviour exactly — every
   ``load_server`` call runs the live network path. Wiring an optional
   cache must not regress callers that don't opt in.
2. ``McpLoader(cache=<populated>)`` skips the live network path on the
   second call to the same ``(server_name, org_id, user_id)`` key.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpDiscoveryCache,
    McpLoadErrorCode,
    McpLoadRequest,
    McpLoader,
    McpResourceDiscoveryPage,
    McpToolDiscoveryPage,
)

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


class LoaderCacheMixin(DynamicMcpLoadingMixin):
    """Helpers for building a runtime context + a cache-wired loader."""

    def build_context(self, model_config: ModelConfig) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.TestValues.Ids.USER_123,
            org_id=self.TestValues.Ids.ORG_456,
            roles={self.TestValues.Roles.EMPLOYEE},
            permission_scopes={self.TestValues.Scopes.DOCS_READ},
            model_profile=model_config,
            trace_id="trace_cache_integration",
            feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
        )

    def build_provider(self) -> "DynamicMcpLoadingMixin.FakeMcpProvider":
        client = self.FakeMcpClient(
            tools=(self.make_tool(),),
            resources=(self.make_resource(),),
        )
        card = self.make_card(name=self.TestValues.Names.DRIVE_MCP).model_copy(
            update={"server_id": "server-drive"}
        )
        return self.FakeMcpProvider(
            cards=(card,),
            clients={self.TestValues.Names.DRIVE_MCP: client},
        )

    def build_loader(
        self,
        *,
        cache: McpDiscoveryCache | None,
    ) -> tuple[McpLoader, "DynamicMcpLoadingMixin.FakeMcpProvider"]:
        provider = self.build_provider()
        return (
            McpLoader(DynamicMcpRegistry(providers=(provider,)), cache=cache),
            provider,
        )


@dataclass
class _PaginatedMcpClient(DynamicMcpLoadingMixin.FakeMcpClient):
    tool_pages: dict[str | None, McpToolDiscoveryPage] = field(default_factory=dict)
    resource_pages: dict[str | None, McpResourceDiscoveryPage] = field(
        default_factory=dict
    )
    requested_tool_cursors: list[str | None] = field(default_factory=list)
    requested_resource_cursors: list[str | None] = field(default_factory=list)

    async def list_tools_page(
        self,
        *,
        cursor: str | None,
    ) -> McpToolDiscoveryPage:
        self.requested_tool_cursors.append(cursor)
        return self.tool_pages[cursor]

    async def list_resources_page(
        self,
        *,
        cursor: str | None,
    ) -> McpResourceDiscoveryPage:
        self.requested_resource_cursors.append(cursor)
        return self.resource_pages[cursor]


@dataclass
class _DelayedMcpClient(DynamicMcpLoadingMixin.FakeMcpClient):
    load_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_load: asyncio.Event = field(default_factory=asyncio.Event)

    async def list_tools(self):
        self.load_started.set()
        await self.release_load.wait()
        return await super().list_tools()


class TestLoaderCacheIntegration(LoaderCacheMixin):
    def test_cache_none_preserves_pre_cache_behaviour(self) -> None:
        """``McpLoader(cache=None)`` hits the live path on every call."""

        async def run() -> None:
            loader, provider = self.build_loader(cache=None)
            context = self.build_context(
                ModelConfig(
                    provider="fake",
                    model_name="fake",
                    max_input_tokens=128_000,
                    timeout_seconds=30,
                    temperature=0,
                )
            )
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=context,
            )

            first = await loader.load_server(request)
            second = await loader.load_server(request)

            assert first.succeeded
            assert second.succeeded
            # ``create_client`` is the canonical observable side effect of
            # the live discovery path. With no cache, every load must
            # create a fresh client.
            assert len(provider.created_clients) == 2

        asyncio.run(run())

    def test_second_call_with_cache_skips_network(self) -> None:
        """With a cache wired, the second call to the same key returns a cached record."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            loader, provider = self.build_loader(cache=cache)
            context = self.build_context(
                ModelConfig(
                    provider="fake",
                    model_name="fake",
                    max_input_tokens=128_000,
                    timeout_seconds=30,
                    temperature=0,
                )
            )
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=context,
            )

            first = await loader.load_server(request)
            second = await loader.load_server(request)

            assert first.succeeded and second.succeeded
            # Cache hit on second call → no second client creation.
            assert len(provider.created_clients) == 1
            stats = cache.stats()
            # One miss to populate, one hit for the cached read. The
            # cache also performs an internal ``get`` inside
            # ``get_or_load`` and another ``get`` to return the fresh
            # copy after ``put``, so we assert the user-observable
            # outcome (network call count) plus a hit > 0 invariant.
            assert stats.hits >= 1
            assert stats.current_size == 1

        asyncio.run(run())

    def test_cache_isolation_across_users(self) -> None:
        """Different ``user_id`` values keep separate cache entries."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            loader, provider = self.build_loader(cache=cache)
            model_config = ModelConfig(
                provider="fake",
                model_name="fake",
                max_input_tokens=128_000,
                timeout_seconds=30,
                temperature=0,
            )

            alice = AgentRuntimeContext(
                user_id="user_alice",
                org_id=self.TestValues.Ids.ORG_456,
                roles={self.TestValues.Roles.EMPLOYEE},
                permission_scopes={self.TestValues.Scopes.DOCS_READ},
                model_profile=model_config,
                feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
            )
            bob = AgentRuntimeContext(
                user_id="user_bob",
                org_id=self.TestValues.Ids.ORG_456,
                roles={self.TestValues.Roles.EMPLOYEE},
                permission_scopes={self.TestValues.Scopes.DOCS_READ},
                model_profile=model_config,
                feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
            )

            await loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=alice,
                )
            )
            await loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=bob,
                )
            )

            # Alice and Bob are distinct cache keys, so both pay the
            # live path. A subsequent Alice call must hit the cache.
            assert len(provider.created_clients) == 2
            await loader.load_server(
                McpLoadRequest(
                    server_name=self.TestValues.Names.DRIVE_MCP,
                    runtime_context=alice,
                )
            )
            assert len(provider.created_clients) == 2

        asyncio.run(run())

    def test_cached_hit_rechecks_current_permissions(self) -> None:
        async def run() -> None:
            cache = McpDiscoveryCache()
            loader, provider = self.build_loader(cache=cache)
            model_config = ModelConfig(
                provider="fake",
                model_name="fake",
                max_input_tokens=128_000,
                timeout_seconds=30,
                temperature=0,
            )
            authorized = self.build_context(model_config)
            denied = AgentRuntimeContext(
                user_id=authorized.user_id,
                org_id=authorized.org_id,
                roles=authorized.roles,
                permission_scopes=set(),
                model_profile=model_config,
                feature_flags=authorized.feature_flags,
            )
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=authorized,
            )

            assert (await loader.load_server(request)).succeeded
            denied_result = await loader.load_server(
                request.model_copy(update={"runtime_context": denied})
            )

            assert denied_result.error is not None
            assert denied_result.error.code is McpLoadErrorCode.PERMISSION_DENIED
            assert len(provider.created_clients) == 1

        asyncio.run(run())

    def test_cache_fails_closed_without_backend_source_id(self) -> None:
        async def run() -> None:
            client = self.FakeMcpClient(
                tools=(self.make_tool(),),
                resources=(self.make_resource(),),
            )
            provider = self.FakeMcpProvider(
                cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
                clients={self.TestValues.Names.DRIVE_MCP: client},
            )
            loader = McpLoader(
                DynamicMcpRegistry(providers=(provider,)),
                cache=McpDiscoveryCache(),
            )
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=self.build_context(
                    ModelConfig(
                        provider="fake",
                        model_name="fake",
                        max_input_tokens=128_000,
                        timeout_seconds=30,
                        temperature=0,
                    )
                ),
            )

            result = await loader.load_server(request)

            assert result.error is not None
            assert result.error.code is McpLoadErrorCode.CONNECTION_FAILED
            assert provider.created_clients == []

        asyncio.run(run())

    def test_paginated_client_loads_every_page_before_publication(self) -> None:
        async def run() -> None:
            client = _PaginatedMcpClient(
                tools=(),
                resources=(),
                tool_pages={
                    None: McpToolDiscoveryPage(
                        items=(self.make_tool(name="first_page_tool"),),
                        next_cursor="tools-page-2",
                    ),
                    "tools-page-2": McpToolDiscoveryPage(
                        items=(self.make_tool(name="second_page_tool"),),
                    ),
                },
                resource_pages={
                    None: McpResourceDiscoveryPage(
                        items=(self.make_resource(),),
                    )
                },
            )
            provider = self.FakeMcpProvider(
                cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
                clients={self.TestValues.Names.DRIVE_MCP: client},
            )
            loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=self.build_context(
                    ModelConfig(
                        provider="fake",
                        model_name="fake",
                        max_input_tokens=128_000,
                        timeout_seconds=30,
                        temperature=0,
                    )
                ),
            )

            result = await loader.load_server(request)

            assert result.succeeded
            assert result.loaded_server is not None
            assert tuple(tool.name for tool in result.loaded_server.tools) == (
                "first_page_tool",
                "second_page_tool",
            )
            assert client.requested_tool_cursors == [None, "tools-page-2"]
            assert client.requested_resource_cursors == [None]

        asyncio.run(run())

    def test_paginated_client_rejects_repeated_cursor(self) -> None:
        async def run() -> None:
            repeated = McpToolDiscoveryPage(
                items=(self.make_tool(),),
                next_cursor="repeat",
            )
            client = _PaginatedMcpClient(
                tools=(),
                resources=(),
                tool_pages={None: repeated, "repeat": repeated},
                resource_pages={None: McpResourceDiscoveryPage()},
            )
            provider = self.FakeMcpProvider(
                cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
                clients={self.TestValues.Names.DRIVE_MCP: client},
            )
            loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))
            request = McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=self.build_context(
                    ModelConfig(
                        provider="fake",
                        model_name="fake",
                        max_input_tokens=128_000,
                        timeout_seconds=30,
                        temperature=0,
                    )
                ),
            )

            result = await loader.load_server(request)

            assert result.error is not None
            assert result.error.code is McpLoadErrorCode.MALFORMED_DESCRIPTOR
            assert client.requested_tool_cursors == [None, "repeat"]
            assert client.requested_resource_cursors == []

        asyncio.run(run())

    def test_invalidation_race_does_not_surface_or_cache_loaded_descriptors(
        self,
    ) -> None:
        async def run() -> None:
            cache = McpDiscoveryCache()
            client = _DelayedMcpClient(
                tools=(self.make_tool(),),
                resources=(self.make_resource(),),
            )
            provider = self.FakeMcpProvider(
                cards=(
                    self.make_card(name=self.TestValues.Names.DRIVE_MCP).model_copy(
                        update={"server_id": "server-drive"}
                    ),
                ),
                clients={self.TestValues.Names.DRIVE_MCP: client},
            )
            loader = McpLoader(
                DynamicMcpRegistry(providers=(provider,)),
                cache=cache,
            )
            context = self.build_context(
                ModelConfig(
                    provider="fake",
                    model_name="fake",
                    max_input_tokens=128_000,
                    timeout_seconds=30,
                    temperature=0,
                )
            )
            pending = asyncio.create_task(
                loader.load_server(
                    McpLoadRequest(
                        server_name=self.TestValues.Names.DRIVE_MCP,
                        runtime_context=context,
                    )
                )
            )
            await client.load_started.wait()

            await cache.invalidate(
                server_name=self.TestValues.Names.DRIVE_MCP,
                org_id=context.org_id,
                user_id=context.user_id,
            )
            client.release_load.set()
            result = await pending

            assert result.error is not None
            assert result.error.code is McpLoadErrorCode.CONNECTION_FAILED
            assert result.error.retryable is True
            assert cache.stats().current_size == 0

        asyncio.run(run())
