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
from agent_runtime.capabilities.policy.decisions import (
    DecisionScope,
    RunDecisionLedgers,
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


class TestDestructiveUnderBypassAtTheDispatchSeam(DescriptorSourceMixin):
    """The safety hole, closed at the seam the MCP middleware actually calls.

    ``PolicyGatedMcpTool._authorize`` (``mcp/middleware/policy_tool.py:382``) has
    exactly this call and maps GATE onto the write-approval interrupt, so a
    verdict asserted here is the verdict a real connector dispatch gets.
    """

    def test_destructive_gates_under_bypass(self) -> None:
        # Was ALLOW: the BYPASS branch returned above the action check, so on a
        # single-user desktop — where nobody authors the workspace BLOCK that
        # was the only thing above it — Bypass auto-ran deletes.
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            arguments={"id": "L-1"},
            context=self.context(bypass=True),
        )
        assert decision.decision is PolicyDecision.GATE
        assert decision.reason == "approval_required.destructive"
        # GATE carries the descriptor whose ``action`` the middleware passes to
        # ``park_for_approval`` as ``op_class`` — which is what makes the card
        # withhold the ``allow_always`` option for a destructive op.
        assert decision.descriptor.action is Action.DESTRUCTIVE
        assert decision.posture is Posture.BYPASS

    def test_bypass_still_auto_runs_an_ordinary_write(self) -> None:
        # The pill keeps meaning "writes auto"; only the destructive rung moved.
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "x"},
            context=self.context(bypass=True),
        )
        assert decision.decision is PolicyDecision.ALLOW

    def test_authoring_the_destructive_axis_to_auto_still_lifts_it(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            arguments={"id": "L-1"},
            context=self.context(
                bypass=True,
                # The axis is authored under ``tool_use.workspace`` — the same
                # sub-policy ``ToolUsePolicyResolver.resolve`` already reads, so
                # anyone who had written ``destructive: auto`` keeps their
                # behaviour and the change stays strictly additive.
                user_policies_json={"tool_use": {"workspace": {"destructive": "auto"}}},
            ),
        )
        assert decision.decision is PolicyDecision.ALLOW


class TestAuthoredRulesReachTheDispatchSeam(DescriptorSourceMixin):
    """``user_policies_json`` → ``PermissionRuleset.authored`` → the verdict.

    This is the half of the rule layer a user authors in settings. It is read
    off the run context that ``McpDispatchPolicy.evaluate`` already receives, so
    it is snapshot-at-run-start policy data enforced in-process — never a
    per-tool-call HTTP hop (``services/ai-backend/CLAUDE.md``, PDP/PEP).
    """

    def _policies(
        self,
        *,
        rules: dict[str, object] | None = None,
        never: list[str] | None = None,
    ) -> dict[str, object]:
        tool_use: dict[str, object] = {}
        if rules is not None:
            tool_use["permission_rules"] = rules
        if never is not None:
            tool_use["never"] = never
        return {"tool_use": tool_use}

    def test_an_authored_allow_lifts_a_write_gate_under_manual(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "x"},
            context=self.context(
                user_policies_json=self._policies(rules={"mcp:linear:*": "allow"})
            ),
        )
        assert decision.decision is PolicyDecision.ALLOW

    def test_an_authored_deny_survives_bypass(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "x"},
            context=self.context(
                bypass=True,
                user_policies_json=self._policies(
                    rules={"mcp:linear:create_issue": "deny"}
                ),
            ),
        )
        assert decision.decision is PolicyDecision.DENY
        assert decision.reason == "permission_denied"

    def test_an_authored_rule_can_discriminate_on_an_argument(self) -> None:
        policies = self._policies(
            rules={"mcp:linear:*": {"*DROP TABLE*": "deny", "mcp:linear:*": "allow"}}
        )
        allowed = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "Fix the login bug"},
            context=self.context(user_policies_json=policies),
        )
        denied = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "DROP TABLE issues"},
            context=self.context(user_policies_json=policies),
        )
        assert allowed.decision is PolicyDecision.ALLOW
        assert denied.decision is PolicyDecision.DENY

    def test_the_never_list_survives_bypass(self) -> None:
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"path": "/Users/sarah/.ssh/id_rsa"},
            context=self.context(
                bypass=True,
                user_policies_json=self._policies(
                    rules={"mcp:linear:*": "allow"},
                    never=["/Users/sarah/.ssh/**"],
                ),
            ),
        )
        # Above the posture AND above the run's own allow rule: the never-list is
        # a floor, not a row whose action happens to be "deny".
        assert decision.decision is PolicyDecision.DENY
        assert decision.reason == "permission_denied"

    def test_a_malformed_policy_row_costs_that_row_and_not_the_run(self) -> None:
        # ``user_policies_json`` is hydrated untrusted input.
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="list_issues",
            arguments={},
            context=self.context(
                user_policies_json={"tool_use": {"permission_rules": "nonsense"}}
            ),
        )
        assert decision.decision is PolicyDecision.ALLOW

    def test_subjects_are_carried_on_the_verdict_for_the_middleware(self) -> None:
        # The middleware registers the pending ask from these, and a later
        # ``always`` writes its rule over them — two derivations of "what did
        # this call touch" that could drift is how a grant ends up covering
        # something the card never named.
        decision = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "Fix bug", "count": 3},
            context=self.context(),
        )
        assert decision.subjects == ("mcp:linear:create_issue", "Fix bug")


class TestRunScopedAlwaysGrant(DescriptorSourceMixin):
    """``register_pending`` → ``record_reply(always)`` → the NEXT ``evaluate``.

    The loop the policy middleware drives: it registers before parking
    (``policy_tool.py:405``), and calls ``record_reply`` the moment
    ``park_for_approval`` returns APPROVED (``policy_tool.py:428``). What makes
    the rule layer reachable rather than merely present is that the second
    identical write in the same run reads the rule back.
    """

    _RUN = "run_p1b"

    def setup_method(self) -> None:
        RunDecisionLedgers.reset()

    def teardown_method(self) -> None:
        RunDecisionLedgers.reset()

    def _evaluate(self, context: AgentRuntimeContext) -> object:
        return McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "Fix bug"},
            context=context,
        )

    def test_always_makes_the_next_identical_write_allow(self) -> None:
        context = self.context()
        first = self._evaluate(context)
        assert first.decision is PolicyDecision.GATE  # type: ignore[attr-defined]

        McpDispatchPolicy.register_pending(
            context=context, decision=first, approval_id="ap-1"
        )  # type: ignore[arg-type]
        outcome = McpDispatchPolicy.record_reply(
            context=context, approval_id="ap-1", scope="always"
        )
        assert outcome.scope is DecisionScope.ALWAYS

        second = self._evaluate(context)
        assert second.decision is PolicyDecision.ALLOW  # type: ignore[attr-defined]

    def test_once_leaves_the_next_write_gated(self) -> None:
        context = self.context()
        first = self._evaluate(context)
        McpDispatchPolicy.register_pending(
            context=context, decision=first, approval_id="ap-1"
        )  # type: ignore[arg-type]
        outcome = McpDispatchPolicy.record_reply(
            context=context, approval_id="ap-1", scope="once"
        )
        assert outcome.scope is DecisionScope.ONCE
        assert self._evaluate(context).decision is PolicyDecision.GATE  # type: ignore[attr-defined]

    def test_an_absent_scope_is_once(self) -> None:
        # ``GateResume.decision_scope`` is ``None`` whenever the client named no
        # scope, and for every rejection. Fail-closed to the narrow answer.
        context = self.context()
        first = self._evaluate(context)
        McpDispatchPolicy.register_pending(
            context=context, decision=first, approval_id="ap-1"
        )  # type: ignore[arg-type]
        McpDispatchPolicy.record_reply(context=context, approval_id="ap-1", scope=None)
        assert self._evaluate(context).decision is PolicyDecision.GATE  # type: ignore[attr-defined]

    def test_an_always_does_not_lift_the_destructive_rung(self) -> None:
        # A grant raised from a card cannot buy standing authority over deletes,
        # which is also why ``ToolAccessGate._grant_options`` does not offer the
        # ``allow_always`` control on a destructive card in the first place.
        context = self.context()
        destructive = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            arguments={"id": "L-1"},
            context=context,
        )
        McpDispatchPolicy.register_pending(
            context=context, decision=destructive, approval_id="ap-del"
        )
        McpDispatchPolicy.record_reply(
            context=context, approval_id="ap-del", scope="always"
        )
        again = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="delete_issue",
            arguments={"id": "L-1"},
            context=context,
        )
        assert again.decision is PolicyDecision.GATE
        assert again.reason == "approval_required.destructive"

    def test_an_always_reports_the_sibling_asks_it_now_covers(self) -> None:
        context = self.context()
        first = self._evaluate(context)
        McpDispatchPolicy.register_pending(
            context=context, decision=first, approval_id="ap-1"
        )  # type: ignore[arg-type]
        sibling = McpDispatchPolicy.evaluate(
            card=self.card(),
            server=self.SERVER,
            tool="create_issue",
            arguments={"title": "Another bug"},
            context=context,
        )
        McpDispatchPolicy.register_pending(
            context=context, decision=sibling, approval_id="ap-2"
        )
        outcome = McpDispatchPolicy.record_reply(
            context=context, approval_id="ap-1", scope="always"
        )
        assert outcome.resolved == ("ap-2",)
        # And the report agrees with the decision: the sibling really does
        # dispatch on replay rather than raising a second card.
        assert (
            McpDispatchPolicy.evaluate(
                card=self.card(),
                server=self.SERVER,
                tool="create_issue",
                arguments={"title": "Another bug"},
                context=context,
            ).decision
            is PolicyDecision.ALLOW
        )

    def test_a_grant_does_not_leak_into_another_run(self) -> None:
        context = self.context()
        first = self._evaluate(context)
        McpDispatchPolicy.register_pending(
            context=context, decision=first, approval_id="ap-1"
        )  # type: ignore[arg-type]
        McpDispatchPolicy.record_reply(
            context=context, approval_id="ap-1", scope="always"
        )
        other = self.context().model_copy(update={"run_id": "run_other"})
        assert self._evaluate(other).decision is PolicyDecision.GATE  # type: ignore[attr-defined]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
