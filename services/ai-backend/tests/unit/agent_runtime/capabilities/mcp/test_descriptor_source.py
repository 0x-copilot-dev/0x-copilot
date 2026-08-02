"""Unit tests for the P1b MCP descriptor source + PDP dispatch policy.

These pin the per-field descriptor derivation (``docs/specs/mcp-tool-policy-pipeline.md``
§4/§6) and the ALLOW/GATE/DENY verdict the ``call_mcp_tool`` middleware acts on,
in isolation from the run graph. They also lock the deliberate authorization
parity: a run whose ``connector_scopes`` omits the MCP connector is NOT denied
(the legacy MCP gate never consulted ``connector_scopes``).
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.cards import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
    McpDispatchPolicy,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    ConnectorState,
    PolicyDecision,
    Posture,
    Trust,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ConnectorAccessMode,
    ModelConfig,
)
from agent_runtime.execution.filesystem_bypass import (
    FilesystemBypassDecision,
    FilesystemBypassMode,
)


class DescriptorSourceMixin:
    """Cards + contexts for the descriptor-derivation tests."""

    SERVER = "linear"

    def card(
        self,
        *,
        auth_state: McpAuthState = McpAuthState.AUTHENTICATED,
        required_scopes: frozenset[str] = frozenset(),
        enabled: bool = True,
        server_id: str | None = "srv_linear",
        access_mode: ConnectorAccessMode = ConnectorAccessMode.READ,
        allowed_org_ids: frozenset[str] = frozenset(),
        allowed_user_ids: frozenset[str] = frozenset(),
    ) -> McpServerCard:
        return McpServerCard(
            name=self.SERVER,
            server_id=server_id,
            short_description="Linear MCP connector.",
            transport="http",
            auth_mode="oauth2",
            auth_state=auth_state,
            required_scopes=required_scopes,
            health="healthy",
            load_cost=10,
            enabled=enabled,
            access_mode=access_mode,
            allowed_org_ids=allowed_org_ids,
            allowed_user_ids=allowed_user_ids,
        )

    def context(
        self,
        *,
        permission_scopes: frozenset[str] = frozenset(),
        connector_scopes: dict[str, frozenset[str]] | None = None,
        paused_connectors: frozenset[str] = frozenset(),
        connector_access_modes: dict[str, ConnectorAccessMode] | None = None,
        bypass: bool = False,
        user_policies_json: dict[str, object] | None = None,
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_p1b",
            org_id="org_p1b",
            roles={"employee"},
            permission_scopes=permission_scopes,
            connector_scopes=connector_scopes or {},
            paused_connectors=paused_connectors,
            connector_access_modes=connector_access_modes or {},
            filesystem_bypass=FilesystemBypassDecision(
                master_enabled=True,
                mode=(
                    FilesystemBypassMode.BYPASS
                    if bypass
                    else FilesystemBypassMode.MANUAL
                ),
            ),
            user_policies_json=user_policies_json or {},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-4o-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0.0,
            ),
            run_id="run_p1b",
            trace_id="trace_p1b",
        )


class TestDescriptorDerivation(DescriptorSourceMixin):
    def test_catalog_read_is_read_and_trusted(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(),
        )
        assert d.action is Action.READ
        assert d.trust is Trust.TRUSTED
        assert d.urn == "mcp:linear:list_issues"
        assert d.source == "mcp"
        assert d.connector_state is ConnectorState.LIVE

    def test_catalog_write_is_write(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            context=self.context(),
        )
        assert d.action is Action.WRITE

    def test_catalog_destructive_is_destructive(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            context=self.context(),
        )
        assert d.action is Action.DESTRUCTIVE

    def test_uncatalogued_unannotated_fails_closed_to_write(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="get_issues",
            context=self.context(),
        )
        assert d.action is Action.WRITE

    def test_read_only_hint_annotation_yields_read(self) -> None:
        token = McpToolAnnotationsRegistry.bind_for_run({})
        McpToolAnnotationsRegistry.register(
            self.SERVER, "get_issues", McpToolAnnotations(read_only_hint=True)
        )
        try:
            d = McpCapabilityDescriptorSource.describe(
                card=self.card(),
                server=self.SERVER,
                tool="get_issues",
                context=self.context(),
            )
        finally:
            McpToolAnnotationsRegistry.unbind(token)
        assert d.action is Action.READ

    def test_destructive_hint_annotation_tightens_to_destructive(self) -> None:
        token = McpToolAnnotationsRegistry.bind_for_run({})
        McpToolAnnotationsRegistry.register(
            self.SERVER, "wipe_it", McpToolAnnotations(destructive_hint=True)
        )
        try:
            d = McpCapabilityDescriptorSource.describe(
                card=self.card(),
                server=self.SERVER,
                tool="wipe_it",
                context=self.context(),
            )
        finally:
            McpToolAnnotationsRegistry.unbind(token)
        assert d.action is Action.DESTRUCTIVE

    def test_unauthenticated_uncatalogued_is_untrusted(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(auth_state=McpAuthState.UNAUTHENTICATED),
            server=self.SERVER,
            tool="get_issues",
            context=self.context(),
        )
        assert d.trust is Trust.UNTRUSTED

    def test_catalog_membership_is_trusted_even_when_unauthenticated(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(auth_state=McpAuthState.UNAUTHENTICATED),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(),
        )
        assert d.trust is Trust.TRUSTED

    def test_paused_connector_state_is_paused(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(paused_connectors=frozenset({"srv_linear"})),
        )
        assert d.connector_state is ConnectorState.PAUSED

    def test_access_mode_off_connector_state_is_off(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(
                connector_access_modes={"srv_linear": ConnectorAccessMode.OFF}
            ),
        )
        assert d.connector_state is ConnectorState.OFF

    def test_disabled_card_connector_state_is_off(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(enabled=False),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(),
        )
        assert d.connector_state is ConnectorState.OFF

    def test_scopes_are_sorted_required_scopes(self) -> None:
        d = McpCapabilityDescriptorSource.describe(
            card=self.card(required_scopes=frozenset({"z:read", "a:read"})),
            server=self.SERVER,
            tool="list_issues",
            context=self.context(permission_scopes=frozenset({"a:read", "z:read"})),
        )
        assert d.scopes == ("a:read", "z:read")

    def test_posture_bypass(self) -> None:
        assert (
            McpCapabilityDescriptorSource.posture_for(self.context(bypass=True))
            is Posture.BYPASS
        )
        assert (
            McpCapabilityDescriptorSource.posture_for(self.context(bypass=False))
            is Posture.MANUAL
        )


class TestDispatchPolicy(DescriptorSourceMixin):
    def test_trusted_read_allows(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            arguments={"team": "ENG"},
            context=self.context(),
        )
        assert decision.decision is PolicyDecision.ALLOW

    def test_write_gates_under_manual(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "x"},
            context=self.context(),
        )
        assert decision.decision is PolicyDecision.GATE
        assert decision.reason == "approval_required.write"

    def test_write_auto_runs_under_bypass(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "x"},
            context=self.context(bypass=True),
        )
        assert decision.decision is PolicyDecision.ALLOW

    def test_destructive_gates_under_manual(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            arguments={"id": "L-1"},
            context=self.context(),
        )
        assert decision.decision is PolicyDecision.GATE
        assert decision.reason == "approval_required.destructive"

    def test_paused_denies_connector_unavailable(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            arguments={},
            context=self.context(paused_connectors=frozenset({"srv_linear"})),
        )
        assert decision.decision is PolicyDecision.DENY
        assert decision.reason == "connector_unavailable"

    def test_missing_scope_denies_permission(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(required_scopes=frozenset({"linear:write"})),
            server=self.SERVER,
            tool="list_issues",
            arguments={},
            context=self.context(permission_scopes=frozenset()),
        )
        assert decision.decision is PolicyDecision.DENY
        assert decision.reason == "permission_denied"

    def test_org_allowlist_miss_denies_permission(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(allowed_org_ids=frozenset({"org_other"})),
            server=self.SERVER,
            tool="list_issues",
            arguments={},
            context=self.context(),
        )
        assert decision.decision is PolicyDecision.DENY
        assert decision.reason == "permission_denied"

    def test_authorization_parity_missing_connector_scopes_does_not_deny(self) -> None:
        # The legacy MCP gate never consulted ``connector_scopes``; a run whose
        # map omits the connector must still authorize on the session scope
        # subset alone. (Regression guard for the deliberate parity choice.)
        decision = McpDispatchPolicy.evaluate(
            card=self.card(required_scopes=frozenset({"linear:read"})),
            server=self.SERVER,
            tool="list_issues",
            arguments={},
            context=self.context(
                permission_scopes=frozenset({"linear:read"}),
                connector_scopes={},  # connector deliberately absent
            ),
        )
        assert decision.decision is PolicyDecision.ALLOW


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
