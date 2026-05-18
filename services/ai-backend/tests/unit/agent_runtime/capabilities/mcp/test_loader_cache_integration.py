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

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpDiscoveryCache,
    McpLoadRequest,
    McpLoader,
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
        return self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
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
