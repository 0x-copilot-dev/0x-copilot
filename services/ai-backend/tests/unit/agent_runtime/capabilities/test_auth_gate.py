"""Unit tests for :class:`CapabilityAuthGate` (PR 1.3.5)."""

from __future__ import annotations

from agent_runtime.capabilities.auth_gate import (
    CapabilityAuthGate,
    CapabilityAuthOutcome,
)


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubServer:
    def __init__(
        self,
        *,
        name: str,
        server_id: str | None = None,
        auth_state: str = "authenticated",
        enabled: bool = True,
    ) -> None:
        self.name = name
        self.server_id = server_id
        self.auth_state = auth_state
        self.enabled = enabled


class _StubToolRegistry:
    def __init__(self, tools: list[_StubTool]) -> None:
        self._tools = tools

    def list_available_tools(self, _context: object) -> tuple[_StubTool, ...]:
        return tuple(self._tools)


class _StubMcpRegistry:
    def __init__(self, servers: list[_StubServer]) -> None:
        self._servers = servers

    def list_available_servers(self, _context: object) -> tuple[_StubServer, ...]:
        return tuple(self._servers)


def _gate(
    *,
    tools: list[_StubTool] | None = None,
    servers: list[_StubServer] | None = None,
) -> CapabilityAuthGate:
    return CapabilityAuthGate(
        tool_registry=_StubToolRegistry(tools or []),
        mcp_registry=_StubMcpRegistry(servers or []),
    )


class TestCapabilityAuthGate:
    def test_built_in_tool_authenticated(self) -> None:
        result = _gate(tools=[_StubTool(name="slack_post")]).check(
            target_connector="slack_post", runtime_context=object()
        )
        assert result.outcome is CapabilityAuthOutcome.AUTHENTICATED
        assert result.mcp_server_id is None

    def test_mcp_authenticated(self) -> None:
        result = _gate(servers=[_StubServer(name="linear", server_id="srv_1")]).check(
            target_connector="linear", runtime_context=object()
        )
        assert result.outcome is CapabilityAuthOutcome.AUTHENTICATED
        assert result.mcp_server_id == "srv_1"

    def test_mcp_not_authenticated_surfaces_server_id(self) -> None:
        result = _gate(
            servers=[
                _StubServer(
                    name="linear",
                    server_id="srv_1",
                    auth_state="unauthenticated",
                )
            ]
        ).check(target_connector="linear", runtime_context=object())
        assert result.outcome is CapabilityAuthOutcome.NOT_AUTHENTICATED
        assert result.mcp_server_id == "srv_1"
        assert result.safe_message is not None

    def test_mcp_disabled(self) -> None:
        result = _gate(
            servers=[
                _StubServer(
                    name="linear",
                    server_id="srv_1",
                    auth_state="authenticated",
                    enabled=False,
                )
            ]
        ).check(target_connector="linear", runtime_context=object())
        assert result.outcome is CapabilityAuthOutcome.WORKSPACE_DISABLED
        assert result.mcp_server_id == "srv_1"

    def test_unknown_capability(self) -> None:
        result = _gate().check(target_connector="ghost_tool", runtime_context=object())
        assert result.outcome is CapabilityAuthOutcome.UNKNOWN_CAPABILITY
        assert result.mcp_server_id is None

    def test_empty_target(self) -> None:
        result = _gate().check(target_connector="", runtime_context=object())
        assert result.outcome is CapabilityAuthOutcome.UNKNOWN_CAPABILITY
