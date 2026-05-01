"""Protocol boundaries for MCP client adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agent_runtime.capabilities.mcp.cards import (
    McpConnectionMetadata,
    McpResourceDescriptor,
    McpServerCard,
    McpToolDescriptor,
)

RawMcpConnectionMetadata = McpConnectionMetadata | Mapping[str, object] | None
RawMcpToolDescriptor = McpToolDescriptor | Mapping[str, object]
RawMcpResourceDescriptor = McpResourceDescriptor | Mapping[str, object]
RawMcpToolCallResult = Mapping[str, Any]


class McpClientError(Exception):
    """Base exception for MCP client failures."""


class McpAuthError(McpClientError):
    """Authentication expired or was denied by the MCP server."""


class McpConnectionError(McpClientError):
    """The MCP server was unavailable or disconnected during loading."""


class McpTimeoutError(McpClientError):
    """The MCP server exceeded the loader timeout budget."""


class McpUnsupportedMethodError(McpClientError):
    """The MCP server does not implement an optional JSON-RPC method."""


@runtime_checkable
class McpClient(Protocol):
    """Async-ready MCP client boundary used by the dynamic loader."""

    async def connect(self) -> RawMcpConnectionMetadata:
        """Open a server connection and return safe connection metadata."""

    async def list_tools(self) -> Sequence[RawMcpToolDescriptor]:
        """Return raw MCP tool descriptors from the connected server."""

    async def list_resources(self) -> Sequence[RawMcpResourceDescriptor]:
        """Return raw MCP resource descriptors from the connected server."""

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> RawMcpToolCallResult:
        """Invoke a selected MCP tool and return the raw JSON-RPC result."""


@runtime_checkable
class McpClientFactory(Protocol):
    """Factory for request-scoped MCP clients."""

    def create_client(self, card: McpServerCard) -> McpClient:
        """Create a client for the selected server card."""
