"""Concrete ``PolicyService`` (PDP) for the capability tool-policy pipeline.

Implements the P0 :class:`~agent_runtime.capabilities.policy.contracts.PolicyService`
Protocol with :class:`PdpPolicyService` — the Policy Decision Point the generic
Policy middleware (``MIDDLEWARE_ORDER[0]``) will consult once P1b wires it in.
This module ships the decision logic only; **nothing here is registered with the
runtime** (no ``factory.py`` import, no MCP dispatch, no I/O).

The PDP is a **pure, total function** of ``(principal, descriptor, args, posture)``
given four frozen, constructor-injected deps — matching the live resolver's
"Pure and total — no I/O, never raises" contract
(``capabilities/actions/policy.py``). :meth:`PdpPolicyService.decide` reads no
globals, opens no connection, and never raises: the one parse it performs
(``CapabilityUrn.parse``) cannot fail because :class:`CapabilityDescriptor`
already validated its ``urn`` at construction.

By the time ``decide`` runs the **source has already classified and trusted** the
capability: ``descriptor.action`` ∈ {READ, WRITE, DESTRUCTIVE} and
``descriptor.trust`` ∈ {TRUSTED, UNTRUSTED} are given. So the PDP does **not**
re-run the classifier ladder — it consumes the descriptor and layers
authorization + approval posture on top, evaluated **DENY-first**
(availability → authorization → approval), per
``docs/specs/mcp-tool-policy-pipeline.md`` §5.1.

The ``str`` half of the return is a **stable machine reason code** from a closed
set (:class:`PdpPolicyService._Reason`), never prose and never leaking which gate
failed — the render/interrupt layer maps code→copy downstream, mirroring the live
``_REJECTION_MESSAGES`` kind→message table (``tools/runtime_gate.py``). Per
``services/ai-backend/CLAUDE.md`` "never leak internal detail": OFF and PAUSED
collapse to one ``connector_unavailable`` code, and scope-miss / allowlist-miss /
policy ``BLOCK`` collapse to one ``permission_denied`` code, so model output can
distinguish neither "off" from "paused" nor "missing scope" from "not
allowlisted".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent_runtime.capabilities.actions.contracts import ConnectorWritePolicy
from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    PolicyContract,
    PolicyDecision,
    Posture,
    Principal,
    Trust,
)
from agent_runtime.capabilities.policy.rules import (
    PermissionRuleset,
    PolicySubjects,
    RuleAction,
)
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)


class ConnectorAllowlist(PolicyContract):
    """Org/user allowlist for one connector, read PDP-side by URN.

    The migrated home of the allowlist half of
    ``McpPermissionPolicy.is_server_card_authorized`` (``mcp/permissions.py``):
    an **empty** set means "no restriction" (matching the ``if card.allowed_… and
    …`` guards there), a non-empty set restricts to its members. Deliberately NOT
    a :class:`CapabilityDescriptor` field — allowlists are deployment/tenant
    policy read from the frozen connector registry, not a self-description the
    source emits (the P0 docstring is explicit on this seam).
    """

    allowed_org_ids: frozenset[str] = frozenset()
    allowed_user_ids: frozenset[str] = frozenset()


class ConnectorAllowlistPort(Protocol):
    """The one external seam the PDP needs (Stage 2 authorization).

    Production reads the frozen connector registry / run context by URN; a unit
    test passes a dict-backed fake. Total: an unknown URN returns an empty
    :class:`ConnectorAllowlist` ("no restriction"), never raises.
    """

    def allowlist_for(self, urn: str) -> ConnectorAllowlist: ...


class PdpPolicyService:
    """Policy Decision Point implementing the P0 ``PolicyService`` Protocol.

    Pure + injectable: constructed with a resolved workspace ``snapshot``, the
    per-connector write-policy ``overrides``, an ``allowlist`` port, and the
    deployment ``untrusted_read_gate`` knob; :meth:`decide` then reads only its
    arguments and these frozen deps. No global reads, no I/O, never raises.

    ``decide`` evaluates three stages, short-circuiting on the first DENY:

    * **Stage 1 — availability** from ``descriptor.connector_state`` (the
      run-scoped OFF/PAUSED re-check).
    * **Stage 2 — authorization**: session ∧ connector scope subsets plus the
      org/user allowlist (via the port).
    * **Stage 3 — approval posture**: the ``action × trust × posture`` matrix
      (spec §3, "Move 1"), composed from the ``snapshot``, the per-connector
      ``overrides`` (which only ever downgrade WRITE+ASK→AUTO), and the
      ``(permission × pattern)`` rule layer, with the never-list, a workspace
      ``BLOCK`` and the DESTRUCTIVE rung all surviving BYPASS.
      :meth:`_posture_decision` documents the ladder and why its order is the
      safety argument.
    """

    __slots__ = (
        "_snapshot",
        "_overrides",
        "_allowlist",
        "_untrusted_read_gate",
        "_rules",
        "_never",
    )

    class _Reason:
        """The closed set of safe, non-leaking reason codes ``decide`` returns.

        Stable machine codes — the render/interrupt layer resolves human copy.
        The two DENY codes are exactly the pair the P0 ``PolicyService`` docstring
        names (``permission_denied`` / ``connector_unavailable``); the coarsening
        (OFF≡PAUSED, scope-miss≡allowlist-miss≡BLOCK) is what stops the code from
        leaking connector config to model output.
        """

        ALLOW = ""
        CONNECTOR_UNAVAILABLE = "connector_unavailable"
        PERMISSION_DENIED = "permission_denied"
        APPROVAL_READ = "approval_required.read"
        APPROVAL_WRITE = "approval_required.write"
        APPROVAL_DESTRUCTIVE = "approval_required.destructive"

    #: ``descriptor.action`` (the approval axis) → the enforcement axis the
    #: snapshot is keyed on. Value-identical enums, mapped explicitly rather than
    #: coerced by value so a future divergence fails loudly at review, not
    #: silently at runtime.
    _AXIS_BY_ACTION: dict[Action, ToolUsePolicyKind] = {
        Action.READ: ToolUsePolicyKind.READ,
        Action.WRITE: ToolUsePolicyKind.WRITE,
        Action.DESTRUCTIVE: ToolUsePolicyKind.DESTRUCTIVE,
    }

    #: Enforcement axis → the per-axis approval reason code for a GATE.
    _APPROVAL_REASON_BY_AXIS: dict[ToolUsePolicyKind, str] = {
        ToolUsePolicyKind.READ: _Reason.APPROVAL_READ,
        ToolUsePolicyKind.WRITE: _Reason.APPROVAL_WRITE,
        ToolUsePolicyKind.DESTRUCTIVE: _Reason.APPROVAL_DESTRUCTIVE,
    }

    def __init__(
        self,
        *,
        snapshot: ToolUsePolicySnapshot,
        overrides: ConnectorWritePolicyOverrides,
        allowlist: ConnectorAllowlistPort,
        untrusted_read_gate: bool = True,
        rules: PermissionRuleset | None = None,
        never: PermissionRuleset | None = None,
    ) -> None:
        """Inject the frozen policy inputs the PDP resolves against.

        :param snapshot: resolved workspace+user (axis→mode) map, folded once at
            run-create; supplies the fail-open defaults (read=auto / write=ask /
            destructive=require) for any absent cell.
        :param overrides: per-connector ``allow_always`` map; only ever downgrades
            a WRITE-axis ASK to AUTO.
        :param allowlist: the org/user allowlist port (Stage 2.3).
        :param untrusted_read_gate: deployment knob for the §3 untrusted-read
            decision. ``True`` (default, **fail-closed** per the §7 invariant
            "annotations never grant auto-run on their own — only a trusted READ
            is auto-eligible") GATEs an untrusted connector's READ under MANUAL.
            A deployment may set ``False`` to make it ALLOW-visible (lower
            friction, at the cost of auto-dispatching an untrusted server's read).
            A **trusted** READ auto-runs regardless of this knob (Move 1).
        :param rules: the ``(permission × pattern)`` ruleset (config ⧺ this run's
            ``always`` grants). **Strictly additive**: the default empty ruleset
            matches nothing, so every existing caller keeps today's decisions
            exactly. See :mod:`agent_runtime.capabilities.policy.rules`.
        :param never: the user's never-list — a durable DENY consulted above
            everything a posture can touch. Separate from ``rules`` so "Bypass
            cannot lift this" is a property of the call site (stage 3.1), not a
            convention about a row's action value.
        """

        self._snapshot = snapshot
        self._overrides = overrides
        self._allowlist = allowlist
        self._untrusted_read_gate = untrusted_read_gate
        self._rules = rules if rules is not None else PermissionRuleset()
        self._never = never if never is not None else PermissionRuleset()

    def decide(
        self,
        *,
        principal: Principal,
        descriptor: CapabilityDescriptor,
        args: Mapping[str, object],
        posture: Posture,
    ) -> tuple[PolicyDecision, str]:
        """Return the tri-state decision + a safe reason code, DENY-first.

        ``args`` is now **consulted** — this is the per-argument policy the P1b
        docstring reserved it for. It is folded into :class:`PolicySubjects`
        (URN + every top-level string argument) and matched against the injected
        rulesets; nothing here parses a tool name or an argument name, so the
        derivation cannot be broken by a change to how either is composed.
        ``decide`` stays pure and total: the subjects come from its arguments,
        the rules from its constructor.
        """

        # Stage 1 — Availability (the frozen, run-scoped connector_state).
        if descriptor.connector_state is not ConnectorState.LIVE:
            return PolicyDecision.DENY, self._Reason.CONNECTOR_UNAVAILABLE

        # Stage 2 — Authorization (scopes ∧ allowlists). The URN was validated at
        # descriptor construction, so parse is total here. Re-slug the namespace
        # so the scope-path key matches the override-path key (which re-slugs
        # internally) — a non-canonical-but-valid URN cannot make the two stages
        # disagree on connector identity.
        connector = server_slug(CapabilityUrn.parse(descriptor.urn).namespace)
        required = frozenset(descriptor.scopes)
        if not self._has_scopes(principal, connector=connector, required=required):
            return PolicyDecision.DENY, self._Reason.PERMISSION_DENIED
        if not self._is_allowlisted(principal, urn=descriptor.urn):
            return PolicyDecision.DENY, self._Reason.PERMISSION_DENIED

        # Stage 3 — Approval posture (the Move 1 matrix + the rule layer).
        return self._posture_decision(
            descriptor=descriptor,
            posture=posture,
            connector=connector,
            subjects=PolicySubjects.of(urn=descriptor.urn, args=args),
        )

    def _has_scopes(
        self, principal: Principal, *, connector: str, required: frozenset[str]
    ) -> bool:
        """Session ∧ connector scope subset — reproduces ``has_scopes_for_connector``.

        Both levels must hold (``tools/permissions.py``): the session must carry
        every required scope, AND the connector must be present in
        ``connector_scopes`` with every required scope. A connector absent from
        the map is unauthorized even when nothing is required — the same
        fail-closed shape the live helper keeps.
        """

        if not required.issubset(principal.permission_scopes):
            return False
        connector_scopes = principal.connector_scopes.get(connector)
        if connector_scopes is None:
            return False
        return required.issubset(connector_scopes)

    def _is_allowlisted(self, principal: Principal, *, urn: str) -> bool:
        """Org/user allowlist — reproduces the ``mcp/permissions.py`` authz guards.

        An empty allowlist set imposes no restriction; a non-empty one restricts
        membership. Read PDP-side by URN through the injected port.
        """

        allowlist = self._allowlist.allowlist_for(urn)
        if (
            allowlist.allowed_org_ids
            and principal.org_id not in allowlist.allowed_org_ids
        ):
            return False
        if (
            allowlist.allowed_user_ids
            and principal.user_id not in allowlist.allowed_user_ids
        ):
            return False
        return True

    def _posture_decision(
        self,
        *,
        descriptor: CapabilityDescriptor,
        posture: Posture,
        connector: str,
        subjects: tuple[str, ...],
    ) -> tuple[PolicyDecision, str]:
        """Resolve ``(action × trust × posture × rules)`` → decision (§3 / §5.1).

        Terminal-first, and the ORDER is the safety argument. Reading downward,
        each stage is strictly harder to lift than the one below it:

        ``never-list`` › ``workspace BLOCK`` › ``rule tightenings`` ›
        ``DESTRUCTIVE`` › ``rule ALLOW`` › ``BYPASS`` › ``the axis``.

        Two placements are the point of this ladder:

        * **DESTRUCTIVE sits ABOVE BYPASS**, and GATEs rather than DENYs. It used
          to sit below, which meant the destructive rung — the one rung that
          exists to stop an irreversible act — was never evaluated in the Bypass
          posture at all. The only thing above BYPASS was an admin-authored
          workspace BLOCK, and on a single-user desktop (which is the product)
          nobody authors one, so in practice Bypass auto-ran deletes.

          This mirrors what the filesystem lane already ships correctly:
          ``desktop/host_filesystem.py:46-48`` — "bypass removes the PAUSE, never
          widens the SET" — where rules 4 and 5 are identical in both postures and
          only the granted-root WRITE rule moves (:305). GATE, not DENY, is the
          same asymmetry: the user keeps the ability to say yes on a card that
          names the act; what they lose is the ability to have said yes in advance
          to a class of acts, once, with a pill.

        * **A rule's ALLOW sits BELOW DESTRUCTIVE**, so an authored ``allow`` can
          tighten a destructive op but never loosen one — exactly the conjunction
          3.8's per-connector override already enforces ("never DESTRUCTIVE").
          A rule's ``deny``/``ask`` sit ABOVE Bypass, because a tightening the
          user wrote down should not be undone by a posture toggle.
        """

        action = descriptor.action
        axis = self._AXIS_BY_ACTION[action]
        mode = self._snapshot.mode_for_kind(axis)

        # 3.1 — The never-list. A durable, user-authored floor: no posture, no
        # rule, and no per-connector override reaches above it. It is checked
        # before the workspace BLOCK only so that both orders give the same
        # answer (both DENY) and the strictest statement is evaluated first.
        if self._never.verdict(descriptor.urn, subjects) is RuleAction.DENY:
            return PolicyDecision.DENY, self._Reason.PERMISSION_DENIED

        # 3.2 — A workspace BLOCK is terminal. Neither the per-connector override
        # nor BYPASS lifts an admin prohibition (§7: "posture removes the pause,
        # never the ... gates"; the override conjunction never fires on BLOCK).
        if mode is ToolUsePolicyMode.BLOCK:
            return PolicyDecision.DENY, self._Reason.PERMISSION_DENIED

        # 3.3 — Rule TIGHTENINGS, above BYPASS. `deny` refuses outright; `ask`
        # parks on a card. Both are things the user wrote down about a specific
        # (permission × pattern), so a posture pill must not silently undo them.
        verdict = self._rules.verdict(descriptor.urn, subjects)
        if verdict is RuleAction.DENY:
            return PolicyDecision.DENY, self._Reason.PERMISSION_DENIED
        if verdict is RuleAction.ASK:
            return PolicyDecision.GATE, self._APPROVAL_REASON_BY_AXIS[axis]

        # 3.4 — DESTRUCTIVE pauses in EVERY posture. See the docstring: this is
        # the branch that used to sit below BYPASS and therefore never ran where
        # it mattered.
        #
        # The one thing that still lifts it is the AXIS ITSELF being authored to
        # AUTO, and that exception is the whole "posture ≠ policy" distinction
        # rather than a hole in it. ``destructive=auto`` is a workspace/user
        # statement about destructive ops specifically, written where policy is
        # written; BYPASS is a posture pill about pausing. Honouring the first
        # and not the second is what "bypass removes the PAUSE, never widens the
        # SET" means here, and it is also what keeps this strictly additive —
        # ``mode_for_kind(DESTRUCTIVE)`` still decides for anyone who authored it.
        if action is Action.DESTRUCTIVE and mode is not ToolUsePolicyMode.AUTO:
            return PolicyDecision.GATE, self._Reason.APPROVAL_DESTRUCTIVE

        # 3.5 — A rule's ALLOW: the `always` grant, and the one place a rule
        # widens. Unreachable for a destructive op unless the axis itself was
        # authored to AUTO (3.4), which is the only case where widening a
        # destructive op was already the authored answer.
        if verdict is RuleAction.ALLOW:
            return PolicyDecision.ALLOW, self._Reason.ALLOW

        # 3.6 — BYPASS lifts every remaining ASK/REQUIRE gate to ALLOW: the §3
        # "Bypass" column, now correctly excluding the destructive rung as well as
        # the terminal BLOCK (§3 bypass note; §7 "posture removes the pause").
        if posture is Posture.BYPASS:
            return PolicyDecision.ALLOW, self._Reason.ALLOW

        # --- MANUAL posture below ---

        # 3.7 — Untrusted READ never earns silent auto-run on its own annotation
        # (§7: "annotations never grant auto-run"), so it is NOT driven by
        # ``mode_for_kind(READ)`` toward AUTO. It is ALLOW-visible by default
        # (the visible card is an Observe concern — render ≠ approve), and GATEs
        # when the deployment knob is on OR the workspace tightened read to
        # ask/require (the stricter of the two; BLOCK already denied at 3.2).
        if action is Action.READ and descriptor.trust is Trust.UNTRUSTED:
            if self._untrusted_read_gate or mode in {
                ToolUsePolicyMode.ASK,
                ToolUsePolicyMode.REQUIRE,
            }:
                return PolicyDecision.GATE, self._Reason.APPROVAL_READ
            return PolicyDecision.ALLOW, self._Reason.ALLOW

        # 3.8 — Per-connector override downgrades ONLY a WRITE-axis ASK to AUTO
        # (never DESTRUCTIVE, never REQUIRE, never BLOCK) — the exact conjunction
        # ``EffectiveActionPolicyResolver.resolve`` enforces, and the precedent
        # 3.4/3.5 follow for the rule layer.
        if (
            axis is ToolUsePolicyKind.WRITE
            and mode is ToolUsePolicyMode.ASK
            and self._overrides.for_connector(connector)
            is ConnectorWritePolicy.ALLOW_ALWAYS
        ):
            mode = ToolUsePolicyMode.AUTO

        # 3.9 — Base mode → decision (trusted READ and WRITE under MANUAL): AUTO
        # allows; ASK/REQUIRE gate with the per-axis reason. DESTRUCTIVE can no
        # longer reach here — 3.4 returns for it in every posture.
        if mode is ToolUsePolicyMode.AUTO:
            return PolicyDecision.ALLOW, self._Reason.ALLOW
        return PolicyDecision.GATE, self._APPROVAL_REASON_BY_AXIS[axis]


__all__ = [
    "ConnectorAllowlist",
    "ConnectorAllowlistPort",
    "PdpPolicyService",
]
