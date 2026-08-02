"""Credential-plane adapters for direct-connect MCP (migration P2).

The credential plane is the half of a direct connection that never becomes
model-visible: the endpoint config
(:class:`~agent_runtime.capabilities.mcp.connection.McpServerConnectionConfig`)
and the rotating bearer. This package owns the bearer side — P2-2 lands the
transport-agnostic :class:`RefreshingBearerAuth`; the deployment-specific
providers that feed it (desktop loopback broker, gated backend mint) land here
in P2-7.

P2-7a's ``desktop`` module is **gone**. It brokered a keychain secret through
the Electron capability broker, and nothing ever wrote that record, so it was
blocked from the day it landed. ``backend`` supersedes it on every deployment
including the desktop: the vault owner mints a short-lived, per-server token, and
ai-backend never holds a refresh token or a client secret either way.

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
