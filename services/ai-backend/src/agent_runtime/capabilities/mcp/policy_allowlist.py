"""PDP inputs derived from one resolved MCP card (P1b wiring).

The generic :class:`~agent_runtime.capabilities.policy.service.PdpPolicyService`
reads two things the MCP layer must supply per dispatch: the :class:`Principal`
(identity + scopes) and a :class:`ConnectorAllowlistPort` (the org/user
allowlist keyed by URN). Both are built here from the already-resolved
:class:`~agent_runtime.capabilities.mcp.cards.McpServerCard` and the run's
``AgentRuntimeContext`` at the dispatch boundary — the one place the concrete
card is known.

**Authorization parity (deliberate, documented).** The PDP's scope stage
(:meth:`PdpPolicyService._has_scopes`) reproduces the *tools-lane*
``has_scopes_for_connector``, which additionally requires the connector to be
present in ``connector_scopes``. The legacy MCP gate
(``McpPermissionPolicy.is_server_card_authorized``) never consulted
``connector_scopes`` — it authorized on the *session* scope subset plus the
org/user allowlists only. To keep MCP authorization **byte-identical** across
this wiring (a run whose ``connector_scopes`` omits the MCP connector must not
suddenly be denied), :class:`McpConnectorPrincipal` seeds the principal's
``connector_scopes`` for the dispatched connector with the card's own
``required_scopes``. The PDP's connector-level check then collapses to the
session-level check the legacy gate already performed — no tightening, no
regression. Adopting the stricter connector-level requirement is left as a
reviewed follow-up (it can deny an MCP connector absent from the run's
per-chat ``connector_scopes``, which needs validation against real deployments).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.capabilities.mcp.cards import McpServerCard
from agent_runtime.capabilities.policy.contracts import CapabilityUrn
from agent_runtime.capabilities.policy.service import ConnectorAllowlist
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import AgentRuntimeContext


@dataclass(frozen=True)
class McpConnectorPrincipal:
    """A :class:`Principal` for one MCP dispatch, seeded to preserve MCP authz.

    Structurally satisfies the P0 ``Principal`` Protocol (``user_id`` / ``org_id``
    / ``roles`` / ``permission_scopes`` / ``connector_scopes``). Identity and the
    session scope set are copied verbatim from the run context; only
    ``connector_scopes`` is overlaid, adding a single entry
    ``{connector: card.required_scopes}`` so the PDP's connector-level scope
    check reduces to the legacy MCP session-scope check (see module docstring).
    """

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str]
    connector_scopes: dict[str, frozenset[str]]

    @classmethod
    def for_card(
        cls,
        *,
        context: AgentRuntimeContext,
        connector: str,
        card: McpServerCard,
    ) -> "McpConnectorPrincipal":
        """Build the principal for ``connector`` (already ``server_slug``-normalized).

        The overlay is applied over the run's real ``connector_scopes`` so any
        genuinely-present connector entry is preserved; only the dispatched
        connector is guaranteed present, keyed by the same slug the PDP parses
        out of the descriptor URN.
        """

        scopes = dict(context.connector_scopes)
        scopes[server_slug(connector)] = frozenset(card.required_scopes)
        return cls(
            user_id=context.user_id,
            org_id=context.org_id,
            roles=context.roles,
            permission_scopes=context.permission_scopes,
            connector_scopes=scopes,
        )


class CardConnectorAllowlist:
    """A :class:`ConnectorAllowlistPort` seeded from one resolved MCP card.

    Reproduces the org/user allowlist half of
    ``McpPermissionPolicy.is_server_card_authorized`` PDP-side. Total: the port
    is only ever queried for the dispatched connector's URN, but an unknown URN
    (or a mismatched namespace) returns the empty ``ConnectorAllowlist``
    ("no restriction"), never raises.
    """

    __slots__ = ("_connector", "_allowlist")

    def __init__(self, *, connector: str, allowlist: ConnectorAllowlist) -> None:
        self._connector = server_slug(connector)
        self._allowlist = allowlist

    @classmethod
    def for_card(
        cls, *, connector: str, card: McpServerCard
    ) -> "CardConnectorAllowlist":
        """Build the port from the card's org/user allowlists."""

        return cls(
            connector=connector,
            allowlist=ConnectorAllowlist(
                allowed_org_ids=frozenset(card.allowed_org_ids),
                allowed_user_ids=frozenset(card.allowed_user_ids),
            ),
        )

    def allowlist_for(self, urn: str) -> ConnectorAllowlist:
        """Return the seeded allowlist iff the URN names the seeded connector."""

        try:
            namespace = CapabilityUrn.parse(urn).namespace
        except Exception:  # noqa: BLE001 - a malformed URN imposes no restriction.
            return ConnectorAllowlist()
        if server_slug(namespace) == self._connector:
            return self._allowlist
        return ConnectorAllowlist()


__all__ = ["CardConnectorAllowlist", "McpConnectorPrincipal"]
