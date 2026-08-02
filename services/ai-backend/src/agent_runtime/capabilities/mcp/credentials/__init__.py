"""Credential-plane adapters for direct-connect MCP (migration P2).

The credential plane is the half of a direct connection that never becomes
model-visible: the endpoint config
(:class:`~agent_runtime.capabilities.mcp.connection.McpServerConnectionConfig`)
and the rotating bearer. This package owns the bearer side — P2-2 lands the
transport-agnostic :class:`RefreshingBearerAuth`; the deployment-specific
providers that feed it (desktop loopback broker, gated backend mint) land here
in P2-7.

Additive and unwired: nothing in the running app imports this yet.
"""

from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    McpCredentialConfigError,
    McpCredentialFetchError,
    McpCredentialFlowError,
    MintedTokenFetch,
    RefreshingBearerAuth,
    UtcClock,
)

__all__ = [
    "McpCredentialConfigError",
    "McpCredentialFetchError",
    "McpCredentialFlowError",
    "MintedTokenFetch",
    "RefreshingBearerAuth",
    "UtcClock",
]
