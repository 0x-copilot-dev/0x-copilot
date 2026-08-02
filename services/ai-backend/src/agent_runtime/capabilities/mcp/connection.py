"""Credential-plane connection contracts for direct-connect MCP (migration P2-1).

**Additive and unwired** — nothing in the running app imports this module yet
(P2-PLAN §3, P2-1). Direct-connect has ai-backend open the MCP server itself
instead of proxying JSON-RPC through ``services/backend``, which needs three
things per server that today never cross into ai-backend: the endpoint URL, the
transport, and a short-lived token. Those live on the **credential plane** — an
:class:`McpConnectionDirectory` round-trip for ``(url, transport, headers)`` plus
a rotating ``httpx.Auth`` from the P0
:class:`~agent_runtime.capabilities.policy.contracts.CredentialProvider` for the
token — deliberately kept off the model-visible
:class:`~agent_runtime.capabilities.mcp.cards.McpServerCard` (P2-PLAN §1).

Design rules this module keeps (per ``services/ai-backend/CLAUDE.md``, mirroring
``capabilities/policy/contracts.py``):

* Pydantic at the boundary — frozen, ``extra="forbid"`` — for every data contract
  (:class:`McpServerConnectionConfig`, :class:`MintedToken`).
* Reuse the existing :class:`~agent_runtime.capabilities.mcp.cards.McpTransport`
  ``StrEnum`` rather than re-declaring the transport vocabulary — one connector,
  one transport vocabulary, everywhere.
* The minted token's plaintext is a :class:`~pydantic.SecretStr` with ``repr``
  additionally suppressed at the field level, so the secret never renders into a
  ``repr``/``str``, a structured-log dump, or a traceback.
* The endpoint round-trip is a :class:`Protocol` (:class:`McpConnectionDirectory`)
  so the desktop (loopback broker) and web (backend mint) impls plug in behind one
  injected seam, chosen per deployment profile.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent_runtime.capabilities.mcp.cards import McpTransport


class ConnectionContract(BaseModel):
    """Frozen, strictly-validated base for the credential-plane data contracts.

    Redeclares the ``extra="forbid"`` / ``frozen`` / ``validate_assignment``
    config locally — the same shape and rationale as
    ``policy.contracts.PolicyContract`` and ``execution.filesystem_bypass``'s
    ``_BypassContract`` — so this module stays a leaf and cannot close an import
    cycle with the runtime foundation once a later phase threads a connection
    through the run context.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class McpServerConnectionConfig(ConnectionContract):
    """The endpoint half of a direct-connect: how ai-backend reaches one server.

    Supplied by an :class:`McpConnectionDirectory` on the credential plane and fed
    — together with the rotating ``httpx.Auth`` from the ``CredentialProvider`` —
    into the ``langchain-mcp-adapters`` connection at client-construction time
    (P2-3). Kept off :class:`McpServerCard` so the server URL never becomes
    model-visible (P2-PLAN §1).
    """

    #: The server endpoint (e.g. an HTTP / SSE URL). Typed ``str`` — not
    #: ``HttpUrl`` — because a local / stdio server's connection is a command
    #: spec rather than a URL; the stdio shape is deferred to P2-3 (P2-PLAN §6.4).
    url: str
    #: Which MCP transport the endpoint speaks. Reuses the boundary's existing
    #: ``StrEnum`` so there is one transport vocabulary, not two.
    transport: McpTransport
    #: Non-secret request headers to send on every call. The bearer rides the
    #: ``httpx.Auth`` from the ``CredentialProvider``, never here. Empty default.
    headers: dict[str, str] = Field(default_factory=dict)


class MintedToken(ConnectionContract):
    """A short-lived access token freshly minted for one server, plus its expiry.

    Returned by the credential fetch (desktop broker read / web backend mint) and
    cached by ``RefreshingBearerAuth`` (P2-2) until ``expires_at − skew``. The
    plaintext is a :class:`~pydantic.SecretStr` with ``repr`` suppressed at the
    field level, so neither ``repr(token)`` nor ``str(token)`` nor
    ``token.model_dump_json()`` (structured logs) ever exposes the secret — only
    the ``SecretStr`` mask. The plaintext is recovered solely by an explicit
    ``token.value.get_secret_value()`` at the point of use.
    """

    #: The bearer plaintext. ``SecretStr`` masks it in every ``repr`` / ``str`` /
    #: JSON rendering; ``repr=False`` additionally drops the field from the model
    #: ``repr`` so the mask itself does not advertise the field's presence.
    value: SecretStr = Field(repr=False)
    #: When the token stops being valid. ``RefreshingBearerAuth`` refreshes at
    #: ``expires_at − skew``; the ``datetime`` is accepted as supplied (the skew
    #: arithmetic lives in P2-2, not on this contract).
    expires_at: datetime


class McpConnectionDirectory(Protocol):
    """Yields the endpoint config for one server on the credential plane (P2-PLAN §1).

    The desktop impl reads it from the authenticated loopback capability broker;
    the web / self-host impl reads it from a ``services/backend`` mint endpoint.
    Both keep ``(url, transport, headers)`` on the credential plane and off the
    model-visible card. One injected port, chosen per deployment profile — the
    sibling seam to the ``CredentialProvider`` (``policy.contracts``) a single
    provider impl may satisfy jointly (P2-7).
    """

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig: ...


__all__ = [
    "ConnectionContract",
    "McpConnectionDirectory",
    "McpServerConnectionConfig",
    "MintedToken",
]
