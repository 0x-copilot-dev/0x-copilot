"""Unit tests for the list_connected_servers tool.

The tool is the first step of the connector protocol: it answers "what can I
already reach?" so the model does not propose connecting something the user has
already connected. Tests cover:

  * Only usable servers are listed; installed-but-unauthenticated servers are
    surfaced separately with the ``auth_mcp`` route rather than dropped.
  * Disabled servers never appear.
  * The listing costs no MCP round-trip unless ``include_tools`` is set.
  * ``include_tools`` loads through ``McpLoader`` and tolerates a load failure.
  * The registry is the only authority on visibility.
"""

from __future__ import annotations

import asyncio

from agent_runtime.api.constants import Values
from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpAuthState,
    McpConnectionMetadata,
    McpLoadResult,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.tools.builtin.list_connected_servers import (
    ListConnectedServersInput,
    ListConnectedServersInputParser,
    ListConnectedServersTool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext


class McpCardMixin:
    """Builders for the compact server cards the registry hands the runtime."""

    @staticmethod
    def card(
        name: str,
        *,
        auth_state: McpAuthState = McpAuthState.AUTHENTICATED,
        enabled: bool = True,
        health: McpServerHealth = McpServerHealth.HEALTHY,
    ) -> McpServerCard:
        return McpServerCard(
            name=name,
            server_id=f"seed:{name}",
            display_name=name.title(),
            short_description=f"{name} workspace data.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=auth_state,
            health=health,
            load_cost=1,
            enabled=enabled,
        )


class FakeRegistryMixin:
    """A registry stand-in exposing only the runtime's read port."""

    class Registry:
        def __init__(self, cards: tuple[McpServerCard, ...]) -> None:
            self._cards = cards
            self.calls = 0

        async def list_available_servers(
            self, _context: object
        ) -> tuple[McpServerCard, ...]:
            self.calls += 1
            return self._cards


class FakeLoaderMixin:
    """A loader stand-in that records the servers a listing actually opened."""

    class Loader:
        def __init__(self, *, failing: frozenset[str] = frozenset()) -> None:
            self.loaded: list[str] = []
            self._failing = failing

        async def load_server_by_name(
            self,
            *,
            server_name: str,
            runtime_context: object,
            local_tool_names: object = (),
        ) -> McpLoadResult:
            self.loaded.append(server_name)
            if server_name in self._failing:
                return McpLoadResult.fail(
                    "connection_failed",
                    "The MCP server could not be reached.",
                    server_name=server_name,
                )
            card = McpCardMixin.card(server_name)
            return McpLoadResult.ok(
                LoadedMcpServer(
                    server_card=card,
                    tools=(
                        McpToolDescriptor(
                            name=f"{server_name}_search",
                            description=f"Search {server_name}.",
                            input_schema={"type": "object"},
                            output_shape={"type": "object"},
                        ),
                    ),
                    connection_metadata=McpConnectionMetadata(
                        server_name=server_name,
                        transport=McpTransport.HTTP,
                        auth_mode=McpAuthMode.OAUTH2,
                    ),
                )
            )


class TestInputParser:
    def test_defaults_to_the_cheap_listing(self) -> None:
        parsed = ListConnectedServersInputParser.parse({})
        assert isinstance(parsed, ListConnectedServersInput)
        assert parsed.include_tools is False

    def test_accepts_the_opt_in(self) -> None:
        assert ListConnectedServersInputParser.parse(
            {"include_tools": True}
        ).include_tools

    def test_malformed_input_degrades_to_the_default(self) -> None:
        # The tool has no failure mode that depends on the flag, so a bad
        # argument must not deny the model a listing it is entitled to.
        assert (
            ListConnectedServersInputParser.parse(
                {"include_tools": ["nonsense"]}
            ).include_tools
            is False
        )
        assert ListConnectedServersInputParser.parse("linear").include_tools is False
        assert ListConnectedServersInputParser.parse(None).include_tools is False


class TestListing(McpCardMixin, FakeRegistryMixin, FakeLoaderMixin):
    def test_tool_metadata_matches_the_catalog_constant(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = ListConnectedServersTool(
            registry=self.Registry(()), runtime_context=runtime_context_admin
        )
        assert tool.name == Values.Tool.LIST_CONNECTED_SERVERS
        assert "already connected" in tool.description

    def test_lists_connected_and_routes_unauthenticated_to_auth_mcp(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        registry = self.Registry(
            (
                self.card("linear"),
                self.card("notion", auth_state=McpAuthState.UNAUTHENTICATED),
                self.card("slack", auth_state=McpAuthState.AUTH_SKIPPED),
            )
        )
        result = asyncio.run(
            ListConnectedServersTool(
                registry=registry, runtime_context=runtime_context_admin
            ).ainvoke({})
        )

        assert [entry["server_name"] for entry in result["connected"]] == [
            "linear",
            "slack",
        ]
        assert result["count"] == 2
        assert [entry["server_name"] for entry in result["needs_auth"]] == ["notion"]
        # The routing note is the whole point: an installed-but-unauthenticated
        # server is an ``auth_mcp`` call, never a fresh connector suggestion.
        assert "auth_mcp" in result["note"]
        assert "suggest_mcp_connector" in result["note"]
        assert "load_mcp_server" in result["next_step"]

    def test_disabled_servers_never_appear(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        registry = self.Registry(
            (
                self.card("linear", enabled=False),
                self.card("notion", health=McpServerHealth.DISABLED),
            )
        )
        result = asyncio.run(
            ListConnectedServersTool(
                registry=registry, runtime_context=runtime_context_admin
            ).ainvoke({})
        )

        assert result["connected"] == []
        assert result["count"] == 0
        assert "suggest_mcp_connector" in result["next_step"]

    def test_default_listing_opens_no_mcp_server(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        loader = self.Loader()
        asyncio.run(
            ListConnectedServersTool(
                registry=self.Registry((self.card("linear"),)),
                runtime_context=runtime_context_admin,
                loader=loader,  # type: ignore[arg-type]
            ).ainvoke({})
        )

        assert loader.loaded == []

    def test_include_tools_loads_descriptors_through_the_loader(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        loader = self.Loader()
        result = asyncio.run(
            ListConnectedServersTool(
                registry=self.Registry((self.card("linear"),)),
                runtime_context=runtime_context_admin,
                loader=loader,  # type: ignore[arg-type]
            ).ainvoke({"include_tools": True})
        )

        assert loader.loaded == ["linear"]
        assert result["connected"][0]["tools"] == ["linear_search"]

    def test_a_load_failure_annotates_rather_than_drops_the_server(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The listing's answer is "this is connected"; a transient descriptor
        # failure does not make that false, so the row must survive.
        loader = self.Loader(failing=frozenset({"linear"}))
        result = asyncio.run(
            ListConnectedServersTool(
                registry=self.Registry((self.card("linear"),)),
                runtime_context=runtime_context_admin,
                loader=loader,  # type: ignore[arg-type]
            ).ainvoke({"include_tools": True})
        )

        entry = result["connected"][0]
        assert entry["server_name"] == "linear"
        assert "tools" not in entry
        assert "load_mcp_server" in entry["tools_unavailable"]

    def test_a_registry_without_the_read_port_lists_nothing(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The registry is the only authority on visibility; the tool never
        # widens the set it was given.
        result = asyncio.run(
            ListConnectedServersTool(
                registry=object(), runtime_context=runtime_context_admin
            ).ainvoke({})
        )

        assert result["connected"] == []
        assert result["count"] == 0
