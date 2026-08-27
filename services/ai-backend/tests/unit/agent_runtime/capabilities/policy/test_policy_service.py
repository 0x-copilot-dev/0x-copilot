"""Unit tests for :class:`PdpPolicyService` — the concrete PDP (P1b-inert).

Exhaustive over the decision surface the design specifies:

* every ``(action × trust × posture)`` cell of the §3 "Move 1" matrix under the
  default snapshot;
* Stage 1 availability (OFF / PAUSED → DENY ``connector_unavailable``);
* Stage 2 authorization (session-scope miss, connector-absent, connector-scope
  miss, org-allowlist miss, user-allowlist miss → DENY ``permission_denied``);
* the per-connector override downgrade (WRITE+ASK→AUTO) AND that it never touches
  DESTRUCTIVE, REQUIRE, or BLOCK;
* the ``untrusted_read_gate`` knob and workspace tightening of reads;
* DENY-first ordering (availability outermost);
* the safe, non-leaking reason vocabulary (assert the typed decision AND the
  exact reason code, and that the code echoes no connector / scope / identity);
* the ``EXECUTE`` rung — that a command GATEs under ``BYPASS`` rather than
  auto-running, that an authored ``RuleAction.ALLOW`` still wins over it, and
  that it fires for nothing else (:class:`TestExecuteRungUnderBypass`,
  :class:`TestLegacyAxesAreUnmoved`).

Fakes + builders live in :class:`PolicyServiceFixtureMixin` per
``tests/CLAUDE.md``; concrete test classes hold only ``test_*`` methods.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    PolicyDecision,
    Posture,
    Principal,
    Trust,
)
from agent_runtime.capabilities.policy.rules import (
    PermissionRule,
    PermissionRuleset,
    RuleAction,
)
from agent_runtime.capabilities.policy.service import (
    ConnectorAllowlist,
    PdpPolicyService,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicySnapshot,
)


class PolicyServiceFixtureMixin:
    """Fakes, builders, and constants shared by every PDP test class."""

    _CONNECTOR = "linear"
    _TOOL = "op"
    _ORG = "org-acme"
    _USER = "user-sarah"

    #: The full closed set of reason codes ``decide`` may return. Nothing outside
    #: this set is a safe, non-leaking code.
    _SAFE_REASONS = frozenset(
        {
            "",
            "connector_unavailable",
            "permission_denied",
            "approval_required.read",
            "approval_required.write",
            "approval_required.destructive",
            "approval_required.execute",
        }
    )

    @dataclass(frozen=True)
    class _FakePrincipal:
        """Trivial structural :class:`Principal` — the "desktop trivial impl"."""

        user_id: str
        org_id: str
        roles: frozenset[str]
        permission_scopes: frozenset[str]
        connector_scopes: dict[str, frozenset[str]]

    class _FakeAllowlist:
        """Dict-backed :class:`ConnectorAllowlistPort` (urn → allowlist).

        An unknown URN yields an empty allowlist ("no restriction"), mirroring the
        production port's total contract.
        """

        def __init__(self, by_urn: dict[str, ConnectorAllowlist] | None = None) -> None:
            self._by_urn = dict(by_urn or {})

        def allowlist_for(self, urn: str) -> ConnectorAllowlist:
            return self._by_urn.get(urn, ConnectorAllowlist())

    def _snapshot(self, **modes: str) -> ToolUsePolicySnapshot:
        """Build a snapshot from wire-format ``kind=mode`` kwargs (empty→defaults)."""

        return ToolUsePolicySnapshot.from_response(workspace=modes or None)

    def _overrides(
        self, mapping: dict[str, str] | None = None
    ) -> ConnectorWritePolicyOverrides:
        """Build per-connector write-policy overrides from a ``{slug: mode}`` map."""

        if not mapping:
            return ConnectorWritePolicyOverrides({})
        return ConnectorWritePolicyOverrides.from_user_policies(
            {"tool_use": {"connector_write_policy": mapping}}
        )

    def _principal(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        permission_scopes: tuple[str, ...] | None = None,
        connector_scopes: dict[str, frozenset[str]] | None = None,
        connector: str | None = None,
    ) -> Principal:
        """Build a principal that, by default, authorizes the default descriptor.

        The connector is present in ``connector_scopes`` with an empty scope set,
        so a descriptor requiring no scopes passes Stage 2 unless a test opts into
        a miss explicitly.
        """

        connector = connector or self._CONNECTOR
        if connector_scopes is None:
            connector_scopes = {connector: frozenset()}
        return self._FakePrincipal(
            user_id=user_id or self._USER,
            org_id=org_id or self._ORG,
            roles=frozenset(),
            permission_scopes=frozenset(permission_scopes or ()),
            connector_scopes=connector_scopes,
        )

    def _urn(self, *, connector: str | None = None, tool: str | None = None) -> str:
        return CapabilityUrn.for_mcp(connector or self._CONNECTOR, tool or self._TOOL)

    def _descriptor(
        self,
        *,
        action: Action = Action.READ,
        trust: Trust = Trust.TRUSTED,
        connector: str | None = None,
        tool: str | None = None,
        scopes: tuple[str, ...] = (),
        connector_state: ConnectorState = ConnectorState.LIVE,
        source: str = "mcp",
    ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            urn=self._urn(connector=connector, tool=tool),
            action=action,
            trust=trust,
            scopes=scopes,
            source=source,  # type: ignore[arg-type]
            connector_state=connector_state,
        )

    def _service(
        self,
        *,
        snapshot: ToolUsePolicySnapshot | None = None,
        overrides: ConnectorWritePolicyOverrides | None = None,
        allowlist: "PolicyServiceFixtureMixin._FakeAllowlist | None" = None,
        untrusted_read_gate: bool = True,
        rules: PermissionRuleset | None = None,
        never: PermissionRuleset | None = None,
    ) -> PdpPolicyService:
        return PdpPolicyService(
            snapshot=self._snapshot() if snapshot is None else snapshot,
            overrides=self._overrides() if overrides is None else overrides,
            allowlist=self._FakeAllowlist() if allowlist is None else allowlist,
            untrusted_read_gate=untrusted_read_gate,
            rules=rules,
            never=never,
        )

    def _decide(
        self,
        service: PdpPolicyService,
        *,
        principal: Principal | None = None,
        descriptor: CapabilityDescriptor | None = None,
        posture: Posture = Posture.MANUAL,
        args: dict[str, object] | None = None,
    ) -> tuple[PolicyDecision, str]:
        return service.decide(
            principal=self._principal() if principal is None else principal,
            descriptor=self._descriptor() if descriptor is None else descriptor,
            args={} if args is None else args,
            posture=posture,
        )


class TestAvailabilityStage(PolicyServiceFixtureMixin):
    @pytest.mark.parametrize("state", [ConnectorState.OFF, ConnectorState.PAUSED])
    def test_unavailable_connector_denies(self, state: ConnectorState) -> None:
        service = self._service()
        decision, reason = self._decide(
            service, descriptor=self._descriptor(connector_state=state)
        )
        assert decision is PolicyDecision.DENY
        assert reason == "connector_unavailable"

    def test_off_and_paused_are_indistinguishable(self) -> None:
        # Deliberate coarsening: the code must not reveal *which* unavailable
        # state fired, or it leaks connector config.
        service = self._service()
        _, off = self._decide(
            service, descriptor=self._descriptor(connector_state=ConnectorState.OFF)
        )
        _, paused = self._decide(
            service, descriptor=self._descriptor(connector_state=ConnectorState.PAUSED)
        )
        assert off == paused == "connector_unavailable"

    def test_live_connector_proceeds(self) -> None:
        service = self._service()
        decision, _ = self._decide(
            service, descriptor=self._descriptor(connector_state=ConnectorState.LIVE)
        )
        # Default read+trusted+manual → ALLOW: availability did not deny.
        assert decision is PolicyDecision.ALLOW


class TestAuthorizationStage(PolicyServiceFixtureMixin):
    def test_session_scope_miss_denies(self) -> None:
        # Connector level holds the scope, but the session does not.
        service = self._service()
        principal = self._principal(
            permission_scopes=(),
            connector_scopes={self._CONNECTOR: frozenset({"linear:write"})},
        )
        decision, reason = self._decide(
            service,
            principal=principal,
            descriptor=self._descriptor(scopes=("linear:write",)),
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_connector_absent_denies_even_with_no_required_scopes(self) -> None:
        # The connector must be present in the map even when nothing is required —
        # the fail-closed shape of ``has_scopes_for_connector``.
        service = self._service()
        principal = self._principal(permission_scopes=(), connector_scopes={})
        decision, reason = self._decide(
            service, principal=principal, descriptor=self._descriptor(scopes=())
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_connector_scope_miss_denies(self) -> None:
        # Session holds the scope; the connector-level set does not.
        service = self._service()
        principal = self._principal(
            permission_scopes=("linear:write",),
            connector_scopes={self._CONNECTOR: frozenset()},
        )
        decision, reason = self._decide(
            service,
            principal=principal,
            descriptor=self._descriptor(scopes=("linear:write",)),
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_scopes_present_at_both_levels_proceeds(self) -> None:
        service = self._service()
        principal = self._principal(
            permission_scopes=("linear:write",),
            connector_scopes={self._CONNECTOR: frozenset({"linear:write"})},
        )
        decision, _ = self._decide(
            service,
            principal=principal,
            descriptor=self._descriptor(scopes=("linear:write",)),
        )
        assert decision is PolicyDecision.ALLOW  # read+trusted default

    def test_org_allowlist_miss_denies(self) -> None:
        allowlist = self._FakeAllowlist(
            {self._urn(): ConnectorAllowlist(allowed_org_ids=frozenset({"org-other"}))}
        )
        service = self._service(allowlist=allowlist)
        decision, reason = self._decide(service)
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_org_allowlist_hit_proceeds(self) -> None:
        allowlist = self._FakeAllowlist(
            {self._urn(): ConnectorAllowlist(allowed_org_ids=frozenset({self._ORG}))}
        )
        service = self._service(allowlist=allowlist)
        decision, _ = self._decide(service)
        assert decision is PolicyDecision.ALLOW

    def test_user_allowlist_miss_denies(self) -> None:
        allowlist = self._FakeAllowlist(
            {
                self._urn(): ConnectorAllowlist(
                    allowed_user_ids=frozenset({"user-other"})
                )
            }
        )
        service = self._service(allowlist=allowlist)
        decision, reason = self._decide(service)
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_user_allowlist_hit_proceeds(self) -> None:
        allowlist = self._FakeAllowlist(
            {self._urn(): ConnectorAllowlist(allowed_user_ids=frozenset({self._USER}))}
        )
        service = self._service(allowlist=allowlist)
        decision, _ = self._decide(service)
        assert decision is PolicyDecision.ALLOW

    def test_empty_allowlist_imposes_no_restriction(self) -> None:
        # An empty allowlist set means "no restriction", not "deny everyone".
        allowlist = self._FakeAllowlist({self._urn(): ConnectorAllowlist()})
        service = self._service(allowlist=allowlist)
        decision, _ = self._decide(service)
        assert decision is PolicyDecision.ALLOW


class TestApprovalMatrixDefaultSnapshot(PolicyServiceFixtureMixin):
    """Every §3 matrix cell under the default snapshot (read=auto/write=ask/
    destructive=require), no overrides, at the production default (untrusted
    reads gated fail-closed)."""

    @pytest.mark.parametrize(
        ("action", "trust", "posture", "expected_decision", "expected_reason"),
        [
            # READ · trusted — ALLOW in both postures (Move 1 auto-run).
            (Action.READ, Trust.TRUSTED, Posture.MANUAL, PolicyDecision.ALLOW, ""),
            (Action.READ, Trust.TRUSTED, Posture.BYPASS, PolicyDecision.ALLOW, ""),
            # READ · untrusted — GATE under MANUAL (fail-closed default per §7:
            # annotations never grant auto-run), ALLOW under BYPASS.
            (
                Action.READ,
                Trust.UNTRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.read",
            ),
            (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, PolicyDecision.ALLOW, ""),
            # WRITE — trust-independent: GATE under MANUAL, ALLOW under BYPASS.
            (
                Action.WRITE,
                Trust.TRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.write",
            ),
            (
                Action.WRITE,
                Trust.UNTRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.write",
            ),
            (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, PolicyDecision.ALLOW, ""),
            (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, PolicyDecision.ALLOW, ""),
            # DESTRUCTIVE — trust-independent AND posture-independent: GATE in
            # BOTH postures.
            #
            # CHANGED (was ALLOW under BYPASS, "bypass surrenders even the
            # destructive hard-gate"). That cell was the bug this row now pins:
            # the BYPASS branch returned ALLOW *above* the action check, so the
            # one rung that exists to stop an irreversible act was never
            # evaluated in the posture where it mattered. The only thing above it
            # was an admin-authored workspace BLOCK, which nobody authors on a
            # single-user desktop — the product — so in practice Bypass deleted
            # without asking.
            #
            # GATE (not DENY) is the same asymmetry the filesystem lane already
            # ships: `desktop/host_filesystem.py:46-48` — "bypass removes the
            # PAUSE, never widens the SET". The user keeps the ability to say
            # yes; what they lose is having said yes in advance, via a pill, to a
            # whole class of destructive acts. A deployment that genuinely wants
            # destructive auto-run still has one — it authors
            # `destructive=auto` on the axis, which is asserted separately by
            # `TestDestructiveRungUnderBypass`.
            (
                Action.DESTRUCTIVE,
                Trust.TRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.destructive",
            ),
            (
                Action.DESTRUCTIVE,
                Trust.UNTRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.destructive",
            ),
            (
                Action.DESTRUCTIVE,
                Trust.TRUSTED,
                Posture.BYPASS,
                PolicyDecision.GATE,
                "approval_required.destructive",
            ),
            (
                Action.DESTRUCTIVE,
                Trust.UNTRUSTED,
                Posture.BYPASS,
                PolicyDecision.GATE,
                "approval_required.destructive",
            ),
            # EXECUTE — trust-independent AND posture-independent: GATE in BOTH
            # postures, on the deployment default (execute=ask).
            #
            # Trust-independent because trust is a statement about the connector
            # that emitted the capability, and a command's danger is in the
            # command; posture-independent because the bypass pill means "writes
            # auto" and a shell command is exactly the second way to touch the
            # disk that pill promises never to create. The rung that makes the
            # BYPASS cells GATE rather than ALLOW is asserted directly by
            # `TestExecuteRungUnderBypass`.
            (
                Action.EXECUTE,
                Trust.TRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.execute",
            ),
            (
                Action.EXECUTE,
                Trust.UNTRUSTED,
                Posture.MANUAL,
                PolicyDecision.GATE,
                "approval_required.execute",
            ),
            (
                Action.EXECUTE,
                Trust.TRUSTED,
                Posture.BYPASS,
                PolicyDecision.GATE,
                "approval_required.execute",
            ),
            (
                Action.EXECUTE,
                Trust.UNTRUSTED,
                Posture.BYPASS,
                PolicyDecision.GATE,
                "approval_required.execute",
            ),
        ],
    )
    def test_matrix_cell(
        self,
        action: Action,
        trust: Trust,
        posture: Posture,
        expected_decision: PolicyDecision,
        expected_reason: str,
    ) -> None:
        service = self._service()
        descriptor = self._descriptor(action=action, trust=trust)
        decision, reason = self._decide(service, descriptor=descriptor, posture=posture)
        assert decision is expected_decision
        assert reason == expected_reason


class TestUntrustedReadGate(PolicyServiceFixtureMixin):
    def test_gate_on_gates_untrusted_read_under_manual(self) -> None:
        service = self._service(untrusted_read_gate=True)
        descriptor = self._descriptor(action=Action.READ, trust=Trust.UNTRUSTED)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.read"

    def test_gate_on_still_allows_untrusted_read_under_bypass(self) -> None:
        # BYPASS is the §3 "Bypass" column: ALLOW even with the gate configured on.
        service = self._service(untrusted_read_gate=True)
        descriptor = self._descriptor(action=Action.READ, trust=Trust.UNTRUSTED)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_gate_on_does_not_affect_trusted_read(self) -> None:
        # The knob is scoped to UNTRUSTED reads; a trusted read still auto-runs.
        service = self._service(untrusted_read_gate=True)
        descriptor = self._descriptor(action=Action.READ, trust=Trust.TRUSTED)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_gate_off_allows_untrusted_read_under_manual(self) -> None:
        # The relaxed, opt-in direction: a deployment that sets the knob off makes
        # an untrusted read ALLOW-visible under MANUAL (the visible card is an
        # Observe concern, so the Policy decision is ALLOW).
        service = self._service(untrusted_read_gate=False)
        descriptor = self._descriptor(action=Action.READ, trust=Trust.UNTRUSTED)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""


class TestWorkspaceSnapshotTightens(PolicyServiceFixtureMixin):
    @pytest.mark.parametrize("read_mode", ["ask", "require"])
    def test_workspace_read_gates_trusted_read(self, read_mode: str) -> None:
        # A non-default read axis tightens even a trusted read to a GATE (§C.2).
        service = self._service(snapshot=self._snapshot(read=read_mode))
        descriptor = self._descriptor(action=Action.READ, trust=Trust.TRUSTED)
        decision, reason = self._decide(service, descriptor=descriptor)
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.read"

    @pytest.mark.parametrize("read_mode", ["ask", "require"])
    def test_workspace_read_tightens_untrusted_read_even_gate_off(
        self, read_mode: str
    ) -> None:
        # Stricter-of-the-two: the workspace tightening wins over ALLOW-visible.
        service = self._service(
            snapshot=self._snapshot(read=read_mode), untrusted_read_gate=False
        )
        descriptor = self._descriptor(action=Action.READ, trust=Trust.UNTRUSTED)
        decision, reason = self._decide(service, descriptor=descriptor)
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.read"

    def test_workspace_read_block_denies_read(self) -> None:
        service = self._service(snapshot=self._snapshot(read="block"))
        descriptor = self._descriptor(action=Action.READ, trust=Trust.TRUSTED)
        decision, reason = self._decide(service, descriptor=descriptor)
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_workspace_write_auto_allows_write_under_manual(self) -> None:
        service = self._service(snapshot=self._snapshot(write="auto"))
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_workspace_write_require_gates_write(self) -> None:
        service = self._service(snapshot=self._snapshot(write="require"))
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.write"

    def test_workspace_destructive_auto_allows_destructive_under_manual(self) -> None:
        # "(always)" in the §3 matrix means trust-independent + not downgradable by
        # the per-connector override — NOT workspace-immutable. A workspace that
        # sets destructive=auto lifts the gate, exactly as the live
        # EffectiveActionPolicyResolver derives it from the snapshot axis.
        service = self._service(snapshot=self._snapshot(destructive="auto"))
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_workspace_read_block_denies_untrusted_read(self) -> None:
        # The untrusted variant of the block case: BLOCK is terminal at stage 3.1,
        # before the untrusted-read branch, so the DENY reason is permission_denied
        # (not an approval gate).
        service = self._service(snapshot=self._snapshot(read="block"))
        descriptor = self._descriptor(action=Action.READ, trust=Trust.UNTRUSTED)
        decision, reason = self._decide(service, descriptor=descriptor)
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"


class TestPerConnectorOverride(PolicyServiceFixtureMixin):
    def test_allow_always_downgrades_write_ask_to_allow(self) -> None:
        service = self._service(
            overrides=self._overrides({self._CONNECTOR: "allow_always"})
        )
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_allow_always_never_touches_destructive(self) -> None:
        service = self._service(
            overrides=self._overrides({self._CONNECTOR: "allow_always"})
        )
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.destructive"

    def test_allow_always_never_downgrades_write_require(self) -> None:
        service = self._service(
            snapshot=self._snapshot(write="require"),
            overrides=self._overrides({self._CONNECTOR: "allow_always"}),
        )
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.write"

    def test_allow_always_never_overrides_write_block(self) -> None:
        service = self._service(
            snapshot=self._snapshot(write="block"),
            overrides=self._overrides({self._CONNECTOR: "allow_always"}),
        )
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_ask_first_override_does_not_downgrade(self) -> None:
        service = self._service(
            overrides=self._overrides({self._CONNECTOR: "ask_first"})
        )
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.write"

    def test_override_for_other_connector_does_not_apply(self) -> None:
        service = self._service(overrides=self._overrides({"github": "allow_always"}))
        descriptor = self._descriptor(action=Action.WRITE, connector=self._CONNECTOR)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.MANUAL
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.write"


class TestBlockIsTerminal(PolicyServiceFixtureMixin):
    def test_write_block_survives_bypass(self) -> None:
        # BYPASS lifts ASK/REQUIRE, never a workspace BLOCK (§7 invariant).
        service = self._service(snapshot=self._snapshot(write="block"))
        descriptor = self._descriptor(action=Action.WRITE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_destructive_block_survives_bypass(self) -> None:
        service = self._service(snapshot=self._snapshot(destructive="block"))
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"


class TestDenyFirstOrdering(PolicyServiceFixtureMixin):
    def test_availability_precedes_authorization(self) -> None:
        # OFF + a scope miss reports unavailable, not denied: availability is the
        # outermost DENY.
        service = self._service()
        principal = self._principal(permission_scopes=(), connector_scopes={})
        descriptor = self._descriptor(
            connector_state=ConnectorState.OFF, scopes=("linear:write",)
        )
        decision, reason = self._decide(
            service, principal=principal, descriptor=descriptor
        )
        assert decision is PolicyDecision.DENY
        assert reason == "connector_unavailable"

    def test_availability_precedes_block(self) -> None:
        # OFF + a workspace BLOCK also reports unavailable.
        service = self._service(snapshot=self._snapshot(write="block"))
        descriptor = self._descriptor(
            action=Action.WRITE, connector_state=ConnectorState.OFF
        )
        decision, reason = self._decide(service, descriptor=descriptor)
        assert decision is PolicyDecision.DENY
        assert reason == "connector_unavailable"

    def test_authorization_precedes_posture(self) -> None:
        # A scope miss denies before the posture matrix runs, even under BYPASS.
        service = self._service()
        principal = self._principal(permission_scopes=(), connector_scopes={})
        descriptor = self._descriptor(action=Action.WRITE, scopes=("linear:write",))
        decision, reason = self._decide(
            service, principal=principal, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"


class TestSafeReasonStrings(PolicyServiceFixtureMixin):
    def test_every_returned_reason_is_in_the_closed_safe_set(self) -> None:
        services = [
            self._service(),
            self._service(untrusted_read_gate=True),
            self._service(
                snapshot=self._snapshot(
                    read="block", write="block", destructive="block"
                )
            ),
            self._service(overrides=self._overrides({self._CONNECTOR: "allow_always"})),
        ]
        for service in services:
            for action in Action:
                for trust in Trust:
                    for posture in Posture:
                        for state in ConnectorState:
                            descriptor = self._descriptor(
                                action=action, trust=trust, connector_state=state
                            )
                            _, reason = self._decide(
                                service, descriptor=descriptor, posture=posture
                            )
                            assert reason in self._SAFE_REASONS

    def test_reason_shape_matches_decision(self) -> None:
        service = self._service()
        for action in Action:
            for trust in Trust:
                for posture in Posture:
                    descriptor = self._descriptor(action=action, trust=trust)
                    decision, reason = self._decide(
                        service, descriptor=descriptor, posture=posture
                    )
                    if decision is PolicyDecision.ALLOW:
                        assert reason == ""
                    elif decision is PolicyDecision.DENY:
                        assert reason in {"connector_unavailable", "permission_denied"}
                    else:
                        assert reason.startswith("approval_required.")

    def test_all_six_deny_causes_collapse_to_two_codes(self) -> None:
        # Six distinct denial causes → exactly {connector_unavailable,
        # permission_denied}, so model output cannot tell them apart.
        service = self._service(
            snapshot=self._snapshot(write="block"),
            allowlist=self._FakeAllowlist(
                {
                    self._urn(tool="allowlisted"): ConnectorAllowlist(
                        allowed_org_ids=frozenset({"org-other"})
                    )
                }
            ),
        )
        no_scope_principal = self._principal(permission_scopes=(), connector_scopes={})
        causes = {
            self._decide(
                service, descriptor=self._descriptor(connector_state=ConnectorState.OFF)
            )[1],
            self._decide(
                service,
                descriptor=self._descriptor(connector_state=ConnectorState.PAUSED),
            )[1],
            self._decide(
                service,
                principal=no_scope_principal,
                descriptor=self._descriptor(scopes=("linear:write",)),
            )[1],
            self._decide(
                service,
                principal=self._principal(connector_scopes={}),
                descriptor=self._descriptor(),
            )[1],
            self._decide(
                service,
                descriptor=self._descriptor(action=Action.WRITE, tool="allowlisted"),
            )[1],
            self._decide(service, descriptor=self._descriptor(action=Action.WRITE))[1],
        }
        assert causes == {"connector_unavailable", "permission_denied"}

    def test_denied_reason_echoes_no_connector_scope_or_identity(self) -> None:
        service = self._service()
        principal = self._principal(permission_scopes=(), connector_scopes={})
        descriptor = self._descriptor(scopes=("linear:super-secret-scope",))
        decision, reason = self._decide(
            service, principal=principal, descriptor=descriptor
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"
        for leaked in ("linear", "super-secret-scope", self._ORG, self._USER):
            assert leaked not in reason


class TestDestructiveRungUnderBypass(PolicyServiceFixtureMixin):
    """The rung that survives BYPASS, and the one authored statement that lifts it.

    Pins the asymmetry the filesystem lane already ships
    (``desktop/host_filesystem.py:46-48``, "bypass removes the PAUSE, never
    widens the SET"): a posture pill may stop the runtime pausing on ordinary
    writes, and may not buy a standing yes to irreversible ones.
    """

    @pytest.mark.parametrize("trust", list(Trust))
    def test_destructive_gates_under_bypass(self, trust: Trust) -> None:
        service = self._service()
        descriptor = self._descriptor(action=Action.DESTRUCTIVE, trust=trust)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.destructive"

    def test_destructive_gate_is_not_a_refusal(self) -> None:
        # GATE, never DENY: the user keeps the ability to say yes on a card that
        # names the act. Losing that would make Bypass strictly worse than
        # Manual for destructive ops, which is not the asymmetry being ported.
        service = self._service()
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        decision, _ = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is not PolicyDecision.DENY

    def test_connector_write_override_does_not_lift_the_destructive_rung(self) -> None:
        # ``allow_always`` is a WRITE-axis downgrade. It must not reach
        # DESTRUCTIVE in either posture.
        service = self._service(
            overrides=self._overrides({self._CONNECTOR: "allow_always"})
        )
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        for posture in Posture:
            decision, reason = self._decide(
                service, descriptor=descriptor, posture=posture
            )
            assert decision is PolicyDecision.GATE
            assert reason == "approval_required.destructive"

    def test_a_rule_allow_does_not_lift_the_destructive_rung(self) -> None:
        # An authored ``allow`` may tighten a destructive op, never loosen one —
        # the same conjunction the per-connector override already obeys.
        service = self._service(
            rules=PermissionRuleset(
                rules=(
                    PermissionRule(
                        permission="*", pattern="*", action=RuleAction.ALLOW
                    ),
                )
            )
        )
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        for posture in Posture:
            decision, reason = self._decide(
                service, descriptor=descriptor, posture=posture
            )
            assert decision is PolicyDecision.GATE
            assert reason == "approval_required.destructive"

    @pytest.mark.parametrize("posture", list(Posture))
    def test_authoring_the_axis_to_auto_is_the_one_lift(self, posture: Posture) -> None:
        # "Posture ≠ policy": ``destructive=auto`` is a statement about
        # destructive ops written where policy is written, so it still decides.
        # This is what keeps the change strictly additive for anyone who authored
        # the axis.
        service = self._service(snapshot=self._snapshot(destructive="auto"))
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        decision, reason = self._decide(service, descriptor=descriptor, posture=posture)
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_bypass_still_lifts_the_write_gate(self) -> None:
        # The change is scoped to the destructive rung: BYPASS keeps meaning
        # "writes auto" for WRITE, which is the whole point of the pill.
        service = self._service()
        for trust in Trust:
            decision, reason = self._decide(
                service,
                descriptor=self._descriptor(action=Action.WRITE, trust=trust),
                posture=Posture.BYPASS,
            )
            assert decision is PolicyDecision.ALLOW
            assert reason == ""


class TestExecuteRungUnderBypass(PolicyServiceFixtureMixin):
    """The EXECUTE rung: the security property, and the two things that lift it.

    ``EXECUTE`` is not an enum addition with a test — it is a *position* in
    ``_posture_decision``'s ladder, and the enum is inert without it. Delete the
    branch and an ``EXECUTE`` call falls straight through to rung 3.6
    (``if posture is BYPASS: return ALLOW``), so flipping the composer bypass
    pill would auto-run shell commands with no gate at all — while every enum
    test still passed. :meth:`test_bypass_gates_execute` is the test that stops
    that: it is red with the rung removed, which is the only property that makes
    the rung undeletable.

    The rung sits in the one slot satisfying both constraints — above 3.6 so no
    posture lifts it, below 3.5 so an authored ``RuleAction.ALLOW`` still
    resolves to ALLOW (:meth:`test_a_rule_allow_still_wins`, the prerequisite
    for a run-scoped always-grant).
    """

    @pytest.mark.parametrize("trust", list(Trust))
    def test_bypass_gates_execute(self, trust: Trust) -> None:
        # THE security property. BYPASS means "writes auto"; a shell command is
        # exactly the second way to touch the disk the bypass module says it
        # never creates, and it is invisible to every path-keyed filesystem
        # control we own — so a human reading the literal command before it runs
        # is not one control among several, it is the control.
        service = self._service()
        descriptor = self._descriptor(action=Action.EXECUTE, trust=trust)
        decision, reason = self._decide(
            service, descriptor=descriptor, posture=Posture.BYPASS
        )
        assert decision is PolicyDecision.GATE
        assert decision is not PolicyDecision.ALLOW
        assert reason == "approval_required.execute"

    @pytest.mark.parametrize("mode", ["ask", "require"])
    @pytest.mark.parametrize("posture", list(Posture))
    def test_an_authored_non_auto_axis_gates_in_every_posture(
        self, posture: Posture, mode: str
    ) -> None:
        service = self._service(snapshot=self._snapshot(execute=mode))
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.EXECUTE),
            posture=posture,
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.execute"

    def test_execute_gate_is_not_a_refusal(self) -> None:
        # GATE, never DENY. A refusal would make BYPASS strictly worse than
        # MANUAL for commands, which is not the asymmetry being ported: the user
        # keeps the ability to say yes on a card that names the command; what
        # they lose is having said yes in advance, via a pill, to the class.
        service = self._service()
        for posture in Posture:
            decision, _ = self._decide(
                service,
                descriptor=self._descriptor(action=Action.EXECUTE),
                posture=posture,
            )
            assert decision is not PolicyDecision.DENY

    def test_a_rule_allow_still_wins(self) -> None:
        # The BELOW-3.5 half of the position. A run-scoped always-grant is an
        # authored ``RuleAction.ALLOW`` row in ``_rules``; if the EXECUTE rung
        # sat above 3.5 it would eat the very grant it exists to answer, and
        # "don't ask me forty times about pytest" would be unbuildable. Approval
        # fatigue is itself a security failure, so this direction matters too.
        service = self._service(
            rules=PermissionRuleset(
                rules=(
                    PermissionRule(
                        permission="*", pattern="*", action=RuleAction.ALLOW
                    ),
                )
            )
        )
        descriptor = self._descriptor(action=Action.EXECUTE)
        for posture in Posture:
            decision, reason = self._decide(
                service, descriptor=descriptor, posture=posture
            )
            assert decision is PolicyDecision.ALLOW
            assert reason == ""

    def test_a_rule_allow_is_scoped_to_its_pattern(self) -> None:
        # ...and the grant is a rule, not a switch: a call whose subjects miss
        # the pattern still gates. Otherwise "always allow pytest" would be
        # "always allow anything".
        service = self._service(
            rules=PermissionRuleset(
                rules=(
                    PermissionRule(
                        permission="*", pattern="pytest*", action=RuleAction.ALLOW
                    ),
                )
            )
        )
        descriptor = self._descriptor(action=Action.EXECUTE)
        granted, granted_reason = self._decide(
            service,
            descriptor=descriptor,
            args={"command": "pytest -q"},
            posture=Posture.BYPASS,
        )
        assert (granted, granted_reason) == (PolicyDecision.ALLOW, "")
        other, other_reason = self._decide(
            service,
            descriptor=descriptor,
            args={"command": "rm -rf /"},
            posture=Posture.BYPASS,
        )
        assert other is PolicyDecision.GATE
        assert other_reason == "approval_required.execute"

    @pytest.mark.parametrize("posture", list(Posture))
    def test_authoring_the_axis_to_auto_is_the_one_lift(self, posture: Posture) -> None:
        # "Posture ≠ policy", the same exception rung 3.4 makes: ``execute=auto``
        # is a statement about commands written where policy is written, so it
        # still decides. This is what keeps the rung strictly additive.
        service = self._service(snapshot=self._snapshot(execute="auto"))
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.EXECUTE),
            posture=posture,
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    @pytest.mark.parametrize("posture", list(Posture))
    def test_a_workspace_block_still_denies_above_the_rung(
        self, posture: Posture
    ) -> None:
        # 3.2 is terminal and sits above the rung: an admin prohibition is a
        # DENY, not a card.
        service = self._service(snapshot=self._snapshot(execute="block"))
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.EXECUTE),
            posture=posture,
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_the_connector_write_override_does_not_lift_the_rung(self) -> None:
        # ``allow_always`` is a WRITE-axis downgrade (3.8) and EXECUTE is not the
        # WRITE axis — the whole point of not reusing an existing axis.
        service = self._service(
            overrides=self._overrides({self._CONNECTOR: "allow_always"})
        )
        descriptor = self._descriptor(action=Action.EXECUTE)
        for posture in Posture:
            decision, reason = self._decide(
                service, descriptor=descriptor, posture=posture
            )
            assert decision is PolicyDecision.GATE
            assert reason == "approval_required.execute"

    def test_the_never_list_still_denies_above_the_rung(self) -> None:
        service = self._service(
            never=PermissionRuleset(
                rules=(PermissionRule(pattern="*sudo*", action=RuleAction.DENY),)
            ),
            snapshot=self._snapshot(execute="auto"),
        )
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.EXECUTE),
            args={"command": "sudo rm -rf /"},
            posture=Posture.BYPASS,
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"


class TestLegacyAxesAreUnmoved(PolicyServiceFixtureMixin):
    """The EXECUTE rung moved no decision for READ, WRITE or DESTRUCTIVE.

    :attr:`_GOLDEN` is frozen from the implementation as it stood *before* the
    rung existed (``git show HEAD:...policy/service.py``), captured by a
    differential sweep of 960 cells — every ``(action × trust × posture × axis
    mode × untrusted_read_gate × connector override × rule action)`` combination
    for the three pre-existing axes — which reported zero mismatches. So these
    expectations are not this code describing itself; they are the previous
    code's answers, written down.

    :meth:`test_the_execute_rung_never_fires_off_its_own_axis` re-runs that
    cross-product here and asserts the one thing the golden table cannot: the
    new reason code never appears on a call that is not an ``EXECUTE``.
    """

    #: (action, trust, posture, axis mode | None) → (decision, reason), under
    #: the default knobs (untrusted reads gated, no override, no rules).
    _GOLDEN: dict[tuple[Action, Trust, Posture, str | None], tuple[PolicyDecision, str]]
    _GOLDEN = {
        # READ · trusted — auto-runs unless the axis is authored tighter, and
        # BYPASS lifts that tightening (a read is not a second way to write).
        (Action.READ, Trust.TRUSTED, Posture.MANUAL, None): (PolicyDecision.ALLOW, ""),
        (Action.READ, Trust.TRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.TRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.TRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.TRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.READ, Trust.TRUSTED, Posture.BYPASS, None): (PolicyDecision.ALLOW, ""),
        (Action.READ, Trust.TRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.TRUSTED, Posture.BYPASS, "ask"): (PolicyDecision.ALLOW, ""),
        (Action.READ, Trust.TRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.TRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        # READ · untrusted — gated under MANUAL even at ``auto`` (§7:
        # annotations never grant auto-run on their own).
        (Action.READ, Trust.UNTRUSTED, Posture.MANUAL, None): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.read",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, None): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, "ask"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.READ, Trust.UNTRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        # WRITE — trust-independent; BYPASS lifts it, which is the pill's
        # entire meaning and must survive the new rung untouched.
        (Action.WRITE, Trust.TRUSTED, Posture.MANUAL, None): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, None): (PolicyDecision.ALLOW, ""),
        (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, "ask"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.TRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.MANUAL, None): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.write",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, None): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, "ask"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.WRITE, Trust.UNTRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        # DESTRUCTIVE — gates in both postures; only ``auto`` on its own axis
        # lifts it. The EXECUTE rung must not have disturbed that shape.
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.MANUAL, None): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.BYPASS, None): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.BYPASS, "ask"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.TRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.MANUAL, None): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.MANUAL, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.MANUAL, "ask"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.MANUAL, "require"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.MANUAL, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.BYPASS, None): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.BYPASS, "auto"): (
            PolicyDecision.ALLOW,
            "",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.BYPASS, "ask"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.BYPASS, "require"): (
            PolicyDecision.GATE,
            "approval_required.destructive",
        ),
        (Action.DESTRUCTIVE, Trust.UNTRUSTED, Posture.BYPASS, "block"): (
            PolicyDecision.DENY,
            "permission_denied",
        ),
    }

    _LEGACY_ACTIONS = (Action.READ, Action.WRITE, Action.DESTRUCTIVE)

    def test_the_golden_table_covers_every_legacy_cell(self) -> None:
        # A frozen table only guards what it enumerates, so pin its shape too:
        # 3 actions × 2 trusts × 2 postures × (default + 4 authored modes).
        expected_keys = {
            (action, trust, posture, mode)
            for action in self._LEGACY_ACTIONS
            for trust in Trust
            for posture in Posture
            for mode in (None, "auto", "ask", "require", "block")
        }
        assert set(self._GOLDEN) == expected_keys
        assert len(self._GOLDEN) == 60

    def test_every_legacy_decision_matches_the_pre_execute_answer(self) -> None:
        for (action, trust, posture, mode), expected in self._GOLDEN.items():
            service = self._service(
                snapshot=(
                    self._snapshot()
                    if mode is None
                    else self._snapshot(**{action.value: mode})
                )
            )
            actual = self._decide(
                service,
                descriptor=self._descriptor(action=action, trust=trust),
                posture=posture,
            )
            assert actual == expected, (action, trust, posture, mode)

    def test_the_execute_rung_never_fires_off_its_own_axis(self) -> None:
        # The inertness claim, over the same cross-product the differential
        # swept: no non-EXECUTE call may ever come back with the EXECUTE reason,
        # under any axis mode, posture, trust, read-gate knob, connector
        # override or authored rule.
        rulesets = {
            "none": None,
            "allow": PermissionRuleset(
                rules=(PermissionRule(pattern="*", action=RuleAction.ALLOW),)
            ),
            "ask": PermissionRuleset(
                rules=(PermissionRule(pattern="*", action=RuleAction.ASK),)
            ),
            "deny": PermissionRuleset(
                rules=(PermissionRule(pattern="*", action=RuleAction.DENY),)
            ),
        }
        overrides = {
            "none": self._overrides(),
            "allow_always": self._overrides({self._CONNECTOR: "allow_always"}),
        }
        for action in self._LEGACY_ACTIONS:
            for trust in Trust:
                for posture in Posture:
                    for mode in (None, "auto", "ask", "require", "block"):
                        for gate in (True, False):
                            for override in overrides.values():
                                for rules in rulesets.values():
                                    service = self._service(
                                        snapshot=(
                                            self._snapshot()
                                            if mode is None
                                            else self._snapshot(**{action.value: mode})
                                        ),
                                        overrides=override,
                                        untrusted_read_gate=gate,
                                        rules=rules,
                                    )
                                    _, reason = self._decide(
                                        service,
                                        descriptor=self._descriptor(
                                            action=action, trust=trust
                                        ),
                                        posture=posture,
                                    )
                                    assert reason != "approval_required.execute"
                                    assert reason in self._SAFE_REASONS


class TestRuleLayerPlacement(PolicyServiceFixtureMixin):
    """Where ``(permission × pattern)`` rules sit in the ladder, and what that buys.

    Reading downward the ladder is: never-list › workspace BLOCK › rule
    tightenings › DESTRUCTIVE › rule ALLOW › BYPASS › the axis. Each test below
    pins one adjacency; the destructive adjacencies live in
    :class:`TestDestructiveRungUnderBypass`.
    """

    def _rule(
        self,
        action: RuleAction,
        *,
        permission: str = "*",
        pattern: str = "*",
    ) -> PermissionRule:
        return PermissionRule(permission=permission, pattern=pattern, action=action)

    def test_empty_ruleset_is_todays_behaviour(self) -> None:
        # The additivity claim, asserted rather than asserted-in-a-docstring:
        # the default (no rules) must reproduce the no-rules service exactly.
        with_default = self._service()
        with_empty = self._service(rules=PermissionRuleset(), never=PermissionRuleset())
        for action in Action:
            for trust in Trust:
                for posture in Posture:
                    descriptor = self._descriptor(action=action, trust=trust)
                    assert self._decide(
                        with_default, descriptor=descriptor, posture=posture
                    ) == self._decide(
                        with_empty, descriptor=descriptor, posture=posture
                    )

    def test_rule_deny_survives_bypass(self) -> None:
        # A tightening the user wrote down is not undone by a posture pill.
        service = self._service(
            rules=PermissionRuleset(rules=(self._rule(RuleAction.DENY),))
        )
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            posture=Posture.BYPASS,
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_rule_ask_survives_bypass(self) -> None:
        service = self._service(
            rules=PermissionRuleset(rules=(self._rule(RuleAction.ASK),))
        )
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            posture=Posture.BYPASS,
        )
        assert decision is PolicyDecision.GATE
        assert reason == "approval_required.write"

    def test_rule_allow_lifts_a_write_ask_under_manual(self) -> None:
        # The widening direction: this is what an `always` reply buys.
        service = self._service(
            rules=PermissionRuleset(rules=(self._rule(RuleAction.ALLOW),))
        )
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            posture=Posture.MANUAL,
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""

    def test_rule_allow_does_not_lift_a_workspace_block(self) -> None:
        service = self._service(
            snapshot=self._snapshot(write="block"),
            rules=PermissionRuleset(rules=(self._rule(RuleAction.ALLOW),)),
        )
        decision, reason = self._decide(
            service, descriptor=self._descriptor(action=Action.WRITE)
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_last_match_wins(self) -> None:
        # Ordering IS the semantic: config ⧺ session means a later rule overrides
        # an earlier one, which is what makes layering plain concatenation.
        tighten_then_widen = PermissionRuleset(
            rules=(self._rule(RuleAction.DENY), self._rule(RuleAction.ALLOW))
        )
        widen_then_tighten = PermissionRuleset(
            rules=(self._rule(RuleAction.ALLOW), self._rule(RuleAction.DENY))
        )
        descriptor = self._descriptor(action=Action.WRITE)
        assert self._decide(
            self._service(rules=tighten_then_widen), descriptor=descriptor
        ) == (PolicyDecision.ALLOW, "")
        assert self._decide(
            self._service(rules=widen_then_tighten), descriptor=descriptor
        ) == (PolicyDecision.DENY, "permission_denied")

    def test_a_narrow_later_rule_overrides_a_broad_earlier_one(self) -> None:
        service = self._service(
            rules=PermissionRuleset(
                rules=(
                    self._rule(RuleAction.ALLOW, permission="mcp:linear:*"),
                    self._rule(
                        RuleAction.DENY, permission=self._urn(tool="delete_issue")
                    ),
                )
            )
        )
        allowed, _ = self._decide(
            service, descriptor=self._descriptor(action=Action.WRITE, tool="op")
        )
        denied, _ = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE, tool="delete_issue"),
        )
        assert allowed is PolicyDecision.ALLOW
        assert denied is PolicyDecision.DENY

    def test_an_argument_is_a_subject(self) -> None:
        # The per-argument policy the axis alone cannot express: same tool, two
        # arguments, two answers.
        service = self._service(
            rules=PermissionRuleset(
                rules=(self._rule(RuleAction.DENY, pattern="*id_rsa*"),)
            )
        )
        descriptor = self._descriptor(action=Action.WRITE)
        benign, _ = self._decide(
            service, descriptor=descriptor, args={"path": "/tmp/notes.txt"}
        )
        secret, reason = self._decide(
            service, descriptor=descriptor, args={"path": "/home/s/.ssh/id_rsa"}
        )
        assert benign is PolicyDecision.GATE
        assert secret is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_the_strictest_thing_said_about_any_subject_wins(self) -> None:
        service = self._service(
            rules=PermissionRuleset(
                rules=(
                    self._rule(RuleAction.ALLOW, pattern="mcp:linear:*"),
                    self._rule(RuleAction.DENY, pattern="*id_rsa*"),
                )
            )
        )
        decision, _ = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            args={"path": "/home/s/.ssh/id_rsa"},
        )
        assert decision is PolicyDecision.DENY

    def test_rule_reason_codes_stay_in_the_safe_set(self) -> None:
        for rule_action in RuleAction:
            service = self._service(
                rules=PermissionRuleset(rules=(self._rule(rule_action),)),
                never=PermissionRuleset(rules=(self._rule(RuleAction.DENY),)),
            )
            for action in Action:
                for posture in Posture:
                    _, reason = self._decide(
                        service,
                        descriptor=self._descriptor(action=action),
                        posture=posture,
                    )
                    assert reason in self._SAFE_REASONS


class TestNeverListIsAFloor(PolicyServiceFixtureMixin):
    """The durable never-list: above every posture, rule and override."""

    _NEVER = PermissionRuleset(
        rules=(PermissionRule(pattern="*id_rsa*", action=RuleAction.DENY),)
    )

    @pytest.mark.parametrize("posture", list(Posture))
    def test_never_list_survives_bypass(self, posture: Posture) -> None:
        service = self._service(never=self._NEVER)
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            args={"path": "/home/s/.ssh/id_rsa"},
            posture=posture,
        )
        assert decision is PolicyDecision.DENY
        assert reason == "permission_denied"

    def test_never_list_survives_an_allow_rule(self) -> None:
        # A run-scoped `always` cannot climb over a durable never.
        service = self._service(
            never=self._NEVER,
            rules=PermissionRuleset(
                rules=(PermissionRule(pattern="*", action=RuleAction.ALLOW),)
            ),
        )
        decision, _ = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            args={"path": "/home/s/.ssh/id_rsa"},
            posture=Posture.BYPASS,
        )
        assert decision is PolicyDecision.DENY

    def test_never_list_survives_the_connector_write_override(self) -> None:
        service = self._service(
            never=self._NEVER,
            overrides=self._overrides({self._CONNECTOR: "allow_always"}),
        )
        decision, _ = self._decide(
            service,
            descriptor=self._descriptor(action=Action.WRITE),
            args={"path": "/home/s/.ssh/id_rsa"},
        )
        assert decision is PolicyDecision.DENY

    def test_never_list_survives_an_axis_authored_to_auto(self) -> None:
        service = self._service(
            never=self._NEVER,
            snapshot=self._snapshot(read="auto", write="auto", destructive="auto"),
        )
        for action in Action:
            decision, _ = self._decide(
                service,
                descriptor=self._descriptor(action=action),
                args={"path": "/home/s/.ssh/id_rsa"},
                posture=Posture.BYPASS,
            )
            assert decision is PolicyDecision.DENY

    def test_never_list_does_not_touch_an_unmatched_call(self) -> None:
        service = self._service(never=self._NEVER)
        decision, reason = self._decide(
            service,
            descriptor=self._descriptor(action=Action.READ),
            args={"path": "/tmp/notes.txt"},
        )
        assert decision is PolicyDecision.ALLOW
        assert reason == ""


class TestPurity(PolicyServiceFixtureMixin):
    def test_args_do_not_change_the_decision_without_rules(self) -> None:
        # ``args`` is consulted only through the rule layer, so with the default
        # empty ruleset it cannot move a decision. The per-argument behaviour it
        # unlocks is asserted by ``TestRuleLayerPlacement``.
        service = self._service()
        descriptor = self._descriptor(action=Action.WRITE)
        without = self._decide(service, descriptor=descriptor, args={})
        with_args = self._decide(
            service,
            descriptor=descriptor,
            args={"path": "/etc/passwd", "force": True},
        )
        assert without == with_args == (PolicyDecision.GATE, "approval_required.write")

    def test_decide_is_repeatable(self) -> None:
        service = self._service()
        descriptor = self._descriptor(action=Action.DESTRUCTIVE)
        first = self._decide(service, descriptor=descriptor)
        second = self._decide(service, descriptor=descriptor)
        assert first == second == (PolicyDecision.GATE, "approval_required.destructive")

    def test_builtin_urn_connector_is_resolved(self) -> None:
        # A builtin URN parses to its namespace for scope + override lookup.
        service = self._service(overrides=self._overrides({"fs": "allow_always"}))
        principal = self._principal(
            connector="fs", connector_scopes={"fs": frozenset()}
        )
        descriptor = CapabilityDescriptor(
            urn=CapabilityUrn.for_builtin("fs", "write"),
            action=Action.WRITE,
            trust=Trust.TRUSTED,
            source="builtin",
            connector_state=ConnectorState.LIVE,
        )
        decision, reason = self._decide(
            service, principal=principal, descriptor=descriptor, posture=Posture.MANUAL
        )
        # allow_always on "fs" downgrades the write ASK → AUTO.
        assert decision is PolicyDecision.ALLOW
        assert reason == ""
