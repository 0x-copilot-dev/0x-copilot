"""The PDP inputs for ``run_command`` (§4.1, §8.1 / OQ-1).

The three builtin analogues the PRD asks for are each testable in a different
way, and the most important test in this file is the negative one: without
:class:`ShellPrincipal`'s overlay the PDP's connector-shaped Stage 2 denies
**every** command, which would look exactly like a policy decision and would
never be reported as a bug.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityUrn,
    ConnectorState,
    PolicyDecision,
    Posture,
    Trust,
)
from agent_runtime.capabilities.policy.rules import PermissionRuleset
from agent_runtime.capabilities.policy.service import (
    ConnectorAllowlist,
    PdpPolicyService,
)
from agent_runtime.capabilities.shell.descriptor import (
    BuiltinCapabilityAllowlist,
    RunCommandDescriptor,
    ShellCapability,
    ShellPrincipal,
)
from agent_runtime.capabilities.shell.run_command_tool import TOOL_NAME
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from tests.unit.agent_runtime.capabilities.shell._lanes import runtime_context

_CATALOG = (
    Path(__file__).resolve().parents[5]
    / "src/agent_runtime/capabilities/operations/builtin_operation_catalog.json"
)


def _decide(principal: object, *, available: bool = True):
    service = PdpPolicyService(
        snapshot=ToolUsePolicySnapshot.from_response(workspace={"execute": "auto"}),
        overrides=ConnectorWritePolicyOverrides({}),
        allowlist=BuiltinCapabilityAllowlist(),
        rules=PermissionRuleset(),
        never=PermissionRuleset(),
    )
    return service.decide(
        principal=principal,  # type: ignore[arg-type]
        descriptor=RunCommandDescriptor.for_availability(available=available),
        args={"command": "pytest -q"},
        posture=Posture.MANUAL,
    )


class TestIdentity:
    def test_the_urn_is_the_builtin_shell_one(self) -> None:
        assert str(ShellCapability.URN) == "builtin:shell:run_command"
        assert ShellCapability.URN == CapabilityUrn.for_builtin("shell", "run_command")

    def test_the_op_is_the_tool_name(self) -> None:
        """One string, so a rename cannot split the policy identity from the tool."""

        assert ShellCapability.OP == TOOL_NAME == "run_command"

    def test_the_op_is_not_the_taken_execute_name(self) -> None:
        """§4.1 — ``execute`` is deepagents' placeholder; colliding merges identities."""

        catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
        taken = {
            (row["capability"], row["op"])
            for row in catalog
            if row["tool_name"] != TOOL_NAME
        }

        assert ("builtin", "execute") in taken
        assert ("builtin", ShellCapability.OP) not in taken


class TestDescriptor:
    def test_the_action_is_execute(self) -> None:
        descriptor = RunCommandDescriptor.for_availability(available=True)

        assert descriptor.action is Action.EXECUTE
        assert descriptor.source == "builtin"
        # Empty and load-bearing: the required set being empty is what makes the
        # principal's overlay a totality fix rather than a grant.
        assert descriptor.scopes == ()
        assert descriptor.trust is Trust.TRUSTED

    @pytest.mark.parametrize(
        ("available", "state"),
        [(True, ConnectorState.LIVE), (False, ConnectorState.OFF)],
    )
    def test_availability_becomes_connector_state(
        self, available: bool, state: ConnectorState
    ) -> None:
        descriptor = RunCommandDescriptor.for_availability(available=available)

        assert descriptor.connector_state is state

    def test_an_unavailable_capability_denies_at_stage_one(self) -> None:
        context = runtime_context(run_id="run-desc")

        decision, reason = _decide(ShellPrincipal.for_run(context), available=False)

        assert decision is PolicyDecision.DENY
        assert reason == "connector_unavailable"


class TestTheOperationDescriptor:
    """The checked-in row, and the one field whose name misleads."""

    def test_it_stays_out_of_the_effect_staging_lane(self) -> None:
        """``effect_class`` selects a STAGING LANE here; it is not an undo claim.

        ``validate_effect_descriptor_staging`` requires a stager mapping for
        every descriptor classed ``external_*`` / ``unknown``, because those are
        the operations the Work Ledger stages and reconciles. ``run_command`` is
        a direct tool with its own PEP and never enters that lane, so classing
        it ``external_destructive`` demands a stager that must not exist — the
        gate fails loudly, which is how this was caught.

        ``internal_reversible`` is therefore the same value its two nearest
        neighbours carry — ``run_code_mode``, which runs arbitrary code, and
        deepagents' ``execute`` — and it makes NO undo promise. §10's honesty is
        carried where a human or a model can actually read it: the approval
        card's ``irreversible: true``, the tool description, and
        ``SHELL_EXECUTE_GUIDANCE``.
        """

        from agent_runtime.capabilities.operations.catalog import (
            DEFAULT_OPERATION_DESCRIPTORS,
        )
        from agent_runtime.effects.composition import (
            validate_effect_descriptor_staging,
        )
        from agent_runtime.surfaces_v2.ledger_models import EffectClass

        entry = DEFAULT_OPERATION_DESCRIPTORS.resolve_entry("builtin", "run_command")

        assert entry is not None
        assert entry.descriptor.effect_class is EffectClass.INTERNAL_REVERSIBLE
        # No stager is mapped for it, and the composition gate agrees.
        validate_effect_descriptor_staging(DEFAULT_OPERATION_DESCRIPTORS)

    def test_it_requires_the_capability_and_policy_gates(self) -> None:
        from agent_runtime.capabilities.operations.catalog import (
            DEFAULT_OPERATION_DESCRIPTORS,
        )

        entry = DEFAULT_OPERATION_DESCRIPTORS.resolve_entry("builtin", "run_command")

        assert entry is not None
        assert set(entry.descriptor.required_gate_kinds) == {"capability", "policy"}


class TestPrincipal:
    def test_the_overlay_makes_stage_two_total(self) -> None:
        context = runtime_context(run_id="run-principal")

        decision, _ = _decide(ShellPrincipal.for_run(context))

        assert decision is PolicyDecision.ALLOW

    def test_without_the_overlay_every_command_is_denied(self) -> None:
        """The trap, pinned: a bare principal reads as ``permission_denied``.

        This is what makes the overlay a fix rather than decoration. If a future
        edit drops it, this test is the one that says the capability is dead
        rather than policed.
        """

        context = runtime_context(run_id="run-bare")

        class _Bare:
            user_id = context.user_id
            org_id = context.org_id
            roles = context.roles
            permission_scopes = context.permission_scopes
            connector_scopes: dict[str, frozenset[str]] = {}

        decision, reason = _decide(_Bare())

        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_the_overlay_grants_nothing(self) -> None:
        context = runtime_context(run_id="run-empty-scope")

        principal = ShellPrincipal.for_run(context)

        assert principal.connector_scopes[ShellCapability.CONNECTOR] == frozenset()

    def test_existing_connector_scopes_survive(self) -> None:
        context = runtime_context(run_id="run-preserve").model_copy(
            update={"connector_scopes": {"linear": frozenset({"issues:write"})}}
        )

        principal = ShellPrincipal.for_run(context)

        assert principal.connector_scopes["linear"] == frozenset({"issues:write"})
        assert ShellCapability.CONNECTOR in principal.connector_scopes

    def test_building_the_principal_does_not_mutate_the_run_context(self) -> None:
        context = runtime_context(run_id="run-nomutate").model_copy(
            update={"connector_scopes": {"linear": frozenset()}}
        )

        ShellPrincipal.for_run(context)

        assert ShellCapability.CONNECTOR not in context.connector_scopes


class TestAllowlistPort:
    @pytest.mark.parametrize(
        "urn", ["builtin:shell:run_command", "", "not-a-urn", "mcp:linear:create"]
    )
    def test_it_is_total_and_imposes_nothing(self, urn: str) -> None:
        assert BuiltinCapabilityAllowlist().allowlist_for(urn) == ConnectorAllowlist()
