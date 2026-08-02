"""Build a ``CapabilityDescriptor`` for one MCP tool at the dispatch boundary (P1b).

This is the MCP *source* half of the P0 pipeline contract: given a resolved
:class:`~agent_runtime.capabilities.mcp.cards.McpServerCard` plus the run's
``AgentRuntimeContext``, it emits the :class:`CapabilityDescriptor` the
:class:`~agent_runtime.capabilities.policy.service.PdpPolicyService` polices.
The derivation is kept here — small, pure, and unit-testable in isolation — so
the P2 ``langchain-mcp-adapters`` source can reuse the exact same field logic
instead of re-deriving it inline at a second call site.

Field derivation follows ``docs/specs/mcp-tool-policy-pipeline.md`` §4/§6:

* ``action`` — catalog first (the authoritative rung), else the untrusted
  annotation hints (``destructiveHint`` / ``readOnlyHint``, tighten-only,
  tri-state), else **fail-closed WRITE**. ``readOnlyHint`` counts only when it is
  literally ``True`` (a tri-state hint we cannot trust otherwise).
* ``trust`` — a static per-connector fact: catalog membership OR an
  OAuth-authenticated card ⇒ TRUSTED; otherwise UNTRUSTED.
* ``connector_state`` — the fail-closed availability fold of card + context;
  ``ConnectorState`` alone models only paused/off, so ``enabled`` / ``health``
  are folded to OFF here too.
* ``scopes`` — the per-server ``required_scopes`` (no per-tool scope exists).
* ``posture`` — the run's filesystem-bypass mode, value-identical to
  :class:`Posture`.

:class:`McpDispatchPolicy` composes this descriptor with the committed PDP into
one ``(decision, reason, descriptor)`` verdict, which the ``call_mcp_tool``
middleware maps to ALLOW→dispatch / GATE→interrupt / DENY→typed refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_runtime.capabilities.actions.catalog import ACTION_CATALOG
from agent_runtime.capabilities.actions.contracts import CatalogActionKind
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.mcp.cards import (
    McpAuthState,
    McpServerCard,
    McpServerHealth,
)
from agent_runtime.capabilities.mcp.policy_allowlist import (
    CardConnectorAllowlist,
    McpConnectorPrincipal,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    PolicyDecision,
    Posture,
    Trust,
)
from agent_runtime.capabilities.policy.service import PdpPolicyService
from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ConnectorAccessMode,
)
from agent_runtime.execution.filesystem_bypass import FilesystemBypassMode


class McpCapabilityDescriptorSource:
    """Derive a :class:`CapabilityDescriptor` for one ``(card, server, tool)``."""

    #: Curated catalog kind → the approval axis. Explicit (not value-coerced) so
    #: a future divergence between the two vocabularies fails at review.
    _ACTION_BY_CATALOG_KIND: dict[CatalogActionKind, Action] = {
        CatalogActionKind.READ: Action.READ,
        CatalogActionKind.WRITE: Action.WRITE,
        CatalogActionKind.DESTRUCTIVE: Action.DESTRUCTIVE,
    }

    #: Health states that keep a connector reachable (mirrors
    #: ``McpPermissionPolicy.VISIBLE_HEALTH_STATES``).
    _LIVE_HEALTH: frozenset[McpServerHealth] = frozenset(
        {McpServerHealth.HEALTHY, McpServerHealth.DEGRADED}
    )

    @classmethod
    def describe(
        cls,
        *,
        card: McpServerCard,
        server: str,
        tool: str,
        context: AgentRuntimeContext,
    ) -> CapabilityDescriptor:
        """Return the descriptor the PDP polices for this MCP tool call."""

        return CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp(server, tool),
            action=cls.action_for(server=server, tool=tool),
            trust=cls._trust(server=server, tool=tool, card=card),
            scopes=tuple(sorted(card.required_scopes)),
            source="mcp",
            connector_state=cls._connector_state(card=card, context=context),
        )

    @classmethod
    def posture_for(cls, context: AgentRuntimeContext) -> Posture:
        """Map the run's filesystem-bypass mode onto the approval posture.

        Value-identical enums (``FilesystemBypassMode`` ↔ :class:`Posture`), but
        mapped explicitly so BYPASS is only ever selected for the literal bypass
        mode — never a truthy accident.
        """

        if context.filesystem_bypass.mode is FilesystemBypassMode.BYPASS:
            return Posture.BYPASS
        return Posture.MANUAL

    @classmethod
    def action_for(cls, *, server: str, tool: str) -> Action:
        """Catalog → annotations → fail-closed WRITE (spec §4, Move-1 order).

        Public because the MCP filesystem catalog labels every tool with the
        action class the PDP will police. Reading it from anywhere else — the
        descriptor's own ``readOnlyHint``, say — would let the browsable index
        advertise READ for a call the gateway gates as a write.
        """

        catalog_kind = ACTION_CATALOG.lookup(server, tool)
        if catalog_kind is not None:
            return cls._ACTION_BY_CATALOG_KIND[catalog_kind]
        annotations = McpToolAnnotationsRegistry.get(server, tool)
        if annotations is not None and annotations.destructive_hint is True:
            return Action.DESTRUCTIVE
        if annotations is not None and annotations.read_only_hint is True:
            return Action.READ
        # Silence never means read (fail-closed): an un-annotated, un-catalogued
        # op is a write, gated.
        return Action.WRITE

    @classmethod
    def _trust(cls, *, server: str, tool: str, card: McpServerCard) -> Trust:
        """TRUSTED iff catalog membership OR an OAuth-authenticated card."""

        if ACTION_CATALOG.lookup(server, tool) is not None:
            return Trust.TRUSTED
        if card.auth_state is McpAuthState.AUTHENTICATED:
            return Trust.TRUSTED
        return Trust.UNTRUSTED

    @classmethod
    def _connector_state(
        cls, *, card: McpServerCard, context: AgentRuntimeContext
    ) -> ConnectorState:
        """Fail-closed availability fold (OFF wins, then PAUSED, then LIVE)."""

        access_off = (
            card.server_id is not None
            and context.connector_access_modes.get(card.server_id)
            is ConnectorAccessMode.OFF
        )
        if (
            not card.enabled
            or card.health not in cls._LIVE_HEALTH
            or card.access_mode is ConnectorAccessMode.OFF
            or access_off
        ):
            return ConnectorState.OFF
        if card.server_id is not None and card.server_id in context.paused_connectors:
            return ConnectorState.PAUSED
        return ConnectorState.LIVE


@dataclass(frozen=True)
class McpDispatchDecision:
    """The PDP verdict for one MCP dispatch, plus the descriptor it policed."""

    decision: PolicyDecision
    reason: str
    descriptor: CapabilityDescriptor
    posture: Posture


class McpDispatchPolicy:
    """Compose the descriptor + committed PDP into one dispatch verdict.

    Constructs the :class:`PdpPolicyService` from the run's resolved workspace
    snapshot and per-connector write overrides, seeds the connector allowlist +
    principal from the resolved card (preserving legacy MCP authorization — see
    :mod:`agent_runtime.capabilities.mcp.policy_allowlist`), and returns the
    tri-state ``ALLOW | GATE | DENY`` the middleware acts on.
    """

    @classmethod
    def evaluate(
        cls,
        *,
        card: McpServerCard,
        server: str,
        tool: str,
        arguments: Mapping[str, object],
        context: AgentRuntimeContext,
    ) -> McpDispatchDecision:
        """Return the descriptor + ``(decision, reason)`` for this MCP call."""

        connector = server_slug(server)
        descriptor = McpCapabilityDescriptorSource.describe(
            card=card, server=server, tool=tool, context=context
        )
        posture = McpCapabilityDescriptorSource.posture_for(context)
        pdp = PdpPolicyService(
            snapshot=cls._snapshot(context),
            overrides=ConnectorWritePolicyOverrides.from_user_policies(
                context.user_policies_json
            ),
            allowlist=CardConnectorAllowlist.for_card(connector=connector, card=card),
        )
        principal = McpConnectorPrincipal.for_card(
            context=context, connector=connector, card=card
        )
        decision, reason = pdp.decide(
            principal=principal,
            descriptor=descriptor,
            args=arguments,
            posture=posture,
        )
        return McpDispatchDecision(
            decision=decision,
            reason=reason,
            descriptor=descriptor,
            posture=posture,
        )

    @staticmethod
    def _snapshot(context: AgentRuntimeContext) -> ToolUsePolicySnapshot:
        """Resolve the same workspace policy snapshot the worker binds per run.

        Imported lazily so this module does not pull the tool-enforcement stack
        onto every import path that only needs the descriptor derivation.
        """

        from agent_runtime.capabilities.tools.tool_use_enforcement import (  # noqa: PLC0415
            ToolUsePolicyResolver,
        )

        return ToolUsePolicyResolver.resolve(context)


__all__ = [
    "McpCapabilityDescriptorSource",
    "McpDispatchDecision",
    "McpDispatchPolicy",
]
