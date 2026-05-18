"""Confirm that :class:`AuthMcpTool` busts the discovery cache on successful re-auth.

The cache bust matters because a successful auth grant can change the set
of OAuth scopes the user has for a server, which in turn changes the set
of tools the loader will surface. If the cache served the pre-auth
record, the model would see a stale tool list until the TTL expired.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agent_runtime.execution.contracts import AgentRuntimeContext
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
from agent_runtime.capabilities.mcp.middleware.auth_mcp import (
    AuthMcpTool,
    McpAuthSession,
)


@dataclass(frozen=True)
class FakeAuthSessionCreator:
    """Returns a deterministic auth session for whichever server we ask for."""

    server_name: str = "drive_mcp"
    display_name: str = "Drive MCP"

    async def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> McpAuthSession:
        return McpAuthSession(
            server_id=server_id,
            server_name=self.server_name,
            display_name=self.display_name,
            auth_url=f"https://auth.example.com/{runtime_context.user_id}/{server_id}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )


class AuthMcpCacheBustMixin:
    """Shared builders for auth-tool + cache integration tests."""

    SERVER_NAME = "drive_mcp"
    OTHER_SERVER_NAME = "slack_mcp"

    def make_loaded(self, server_name: str = SERVER_NAME) -> LoadedMcpServer:
        return LoadedMcpServer(
            server_card=McpServerCard(
                name=server_name,
                short_description="Test server.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                health=McpServerHealth.HEALTHY,
                load_cost=10,
            ),
            tools=(
                McpToolDescriptor(
                    name="drive_search",
                    description="Search Drive.",
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

    def populate_cache(
        self,
        cache: McpDiscoveryCache,
        *,
        runtime_context: AgentRuntimeContext,
        server_name: str,
    ) -> McpDiscoveryCacheKey:
        key = McpDiscoveryCacheKey(
            server_name=server_name,
            org_id=runtime_context.org_id,
            user_id=runtime_context.user_id,
        )

        async def _put() -> None:
            await cache.put(key, self.make_loaded(server_name=server_name))

        asyncio.run(_put())
        return key


class TestAuthMcpCacheBust(AuthMcpCacheBustMixin):
    def test_successful_auth_invalidates_matching_cache_entries(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """Approve decision busts the cache; failure decision leaves it intact."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            target_key = McpDiscoveryCacheKey(
                server_name=self.SERVER_NAME,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
            )
            await cache.put(target_key, self.make_loaded())

            def fake_interrupt(_payload: dict[str, object]) -> dict[str, object]:
                return {"decision": "approved"}

            tool = AuthMcpTool(
                auth_session_creator=FakeAuthSessionCreator(
                    server_name=self.SERVER_NAME
                ),
                runtime_context=runtime_context_admin,
                interrupt_handler=fake_interrupt,
                cache=cache,
            )

            result = await tool.ainvoke({"server_name": self.SERVER_NAME})

            assert result["ok"] is True
            # Cache bust ran for the matching ``(server_name, user_id)``.
            assert await cache.get(target_key) is None

        asyncio.run(run())

    def test_failed_auth_does_not_invalidate_cache(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """A rejected approval must not touch the cache."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            target_key = McpDiscoveryCacheKey(
                server_name=self.SERVER_NAME,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
            )
            await cache.put(target_key, self.make_loaded())

            def fake_interrupt(_payload: dict[str, object]) -> dict[str, object]:
                return {"decision": "rejected"}

            tool = AuthMcpTool(
                auth_session_creator=FakeAuthSessionCreator(
                    server_name=self.SERVER_NAME
                ),
                runtime_context=runtime_context_admin,
                interrupt_handler=fake_interrupt,
                cache=cache,
            )

            result = await tool.ainvoke({"server_name": self.SERVER_NAME})

            assert result["ok"] is False
            # Cache entry is untouched — failure paths don't bust.
            cached = await cache.get(target_key)
            assert cached is not None

        asyncio.run(run())

    def test_invalidation_scope_only_matches_target_server_and_user(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """Re-auth for one server must not bust unrelated cache entries."""

        async def run() -> None:
            cache = McpDiscoveryCache()
            # Drive server for the same user — should be invalidated.
            target_key = McpDiscoveryCacheKey(
                server_name=self.SERVER_NAME,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
            )
            # Slack server for the same user — must survive.
            other_server_key = McpDiscoveryCacheKey(
                server_name=self.OTHER_SERVER_NAME,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
            )
            # Drive server for a different user — must survive (org_id is
            # wildcard on the bust, but user_id is not).
            other_user_key = McpDiscoveryCacheKey(
                server_name=self.SERVER_NAME,
                org_id=runtime_context_admin.org_id,
                user_id="user_someone_else",
            )

            await cache.put(target_key, self.make_loaded(server_name=self.SERVER_NAME))
            await cache.put(
                other_server_key,
                self.make_loaded(server_name=self.OTHER_SERVER_NAME),
            )
            await cache.put(
                other_user_key, self.make_loaded(server_name=self.SERVER_NAME)
            )

            def fake_interrupt(_payload: dict[str, object]) -> dict[str, object]:
                return {"decision": "approved"}

            tool = AuthMcpTool(
                auth_session_creator=FakeAuthSessionCreator(
                    server_name=self.SERVER_NAME
                ),
                runtime_context=runtime_context_admin,
                interrupt_handler=fake_interrupt,
                cache=cache,
            )
            await tool.ainvoke({"server_name": self.SERVER_NAME})

            assert await cache.get(target_key) is None
            assert await cache.get(other_server_key) is not None
            assert await cache.get(other_user_key) is not None

        asyncio.run(run())

    def test_no_cache_wired_is_a_noop(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        """``AuthMcpTool(cache=None)`` returns success without touching anything."""

        async def run() -> None:
            tool = AuthMcpTool(
                auth_session_creator=FakeAuthSessionCreator(
                    server_name=self.SERVER_NAME
                ),
                runtime_context=runtime_context_admin,
                interrupt_handler=lambda _payload: {"decision": "approved"},
                cache=None,
            )
            result = await tool.ainvoke({"server_name": self.SERVER_NAME})
            assert result["ok"] is True

        asyncio.run(run())
