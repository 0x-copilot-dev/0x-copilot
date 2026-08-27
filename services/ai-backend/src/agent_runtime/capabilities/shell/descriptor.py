"""The PDP inputs for ``run_command`` — identity, principal, allowlist (§4.1, §8.1).

``run_command`` is the **first non-MCP capability through the PDP**
(:class:`~agent_runtime.capabilities.policy.service.PdpPolicyService`), and the
PDP's authorization stage was written connector-shaped. This module supplies the
three builtin analogues PRD-shell-execution §8.1 (OQ-1) says are needed, each one
argued rather than defaulted:

* **``ConnectorState``** — fixed from the *sealed enablement* (§7.4), so a live
  capability is ``LIVE`` and a withdrawn one is ``OFF``. This is what makes
  §7.2's call-time recheck flow **through** the PDP rather than around it: a
  grant detached mid-run rebuilds the descriptor as ``OFF``, Stage 1 denies with
  ``connector_unavailable``, and there is exactly one place a command can be
  refused rather than two that must agree.
* **``descriptor.scopes``** — empty. The authority for a command is the
  workspace grant, not an OAuth scope; there is no token whose scope set could
  say anything about it. Declaring a scope we do not check would be a claim the
  code does not make.
* **The allowlist port** — an empty :class:`ConnectorAllowlist`, i.e. no
  restriction. Org/user allowlists are a *connector-registry* fact keyed by URN;
  a builtin has no registry row, so the honest answer is "this port imposes
  nothing", stated once here rather than left to a ``None`` that each reader
  folds differently.

**The one trap, and why this module exists at all.**
:meth:`PdpPolicyService._has_scopes` is fail-closed at the *connector* level: a
connector absent from ``principal.connector_scopes`` is unauthorized **even when
nothing is required** (``connector_scopes.get(connector) is None`` ⇒ ``False``).
A run's ``connector_scopes`` is keyed by MCP connector slug and will never carry
``shell``, so a naive principal makes Stage 2 deny **every** command with
``permission_denied`` — a dead capability that looks like a policy decision.
:class:`ShellPrincipal` overlays exactly one entry, ``{"shell": frozenset()}``,
which grants nothing (the required set is empty on both sides) and only makes the
stage total for a capability whose authority lives elsewhere. This is the same
move, for the same reason, that
:class:`~agent_runtime.capabilities.mcp.policy_allowlist.McpConnectorPrincipal`
already makes for MCP dispatch.

Nothing here decides anything. The decision is
:meth:`PdpPolicyService.decide`; the enforcement point is
:mod:`agent_runtime.capabilities.shell.policy_gate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    Trust,
)
from agent_runtime.capabilities.policy.service import ConnectorAllowlist
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import AgentRuntimeContext


class ShellCapability:
    """The one identity ``run_command`` is policed under.

    ``NAMESPACE`` is ``shell`` and ``OP`` is ``run_command`` — deliberately
    **not** ``execute``. That name is already claimed in all three occupancy
    declarations by deepagents' filesystem placeholder
    (``capabilities/operations/conformance.py``, ``builtin_operation_catalog.json``,
    ``operation_descriptors.json``), and a collision there is a silent identity
    merge in the policy layer: two different capabilities would resolve to one
    URN and inherit each other's rules.
    """

    #: The middle URN segment. ``server_slug`` normalises it, so the constant and
    #: the parsed namespace agree byte-for-byte.
    NAMESPACE: Final = "shell"

    #: The trailing URN segment AND the model-facing tool name. One string, so a
    #: rename cannot leave the policy identity pointing at the old tool.
    OP: Final = "run_command"

    #: ``builtin:shell:run_command``. The first ``for_builtin`` URN in
    #: production — every URN built in ``src/`` before this one was ``for_mcp``.
    URN: Final = CapabilityUrn.for_builtin(NAMESPACE, OP)

    #: The connector key Stage 2 parses out of the URN and looks up in
    #: ``principal.connector_scopes``. Precomputed so :class:`ShellPrincipal` and
    #: the PDP cannot disagree about the spelling.
    CONNECTOR: Final = server_slug(NAMESPACE)


class RunCommandDescriptor:
    """Builds the :class:`CapabilityDescriptor` the PDP polices one call against.

    Not a cached constant: ``connector_state`` carries the §7.2 recheck, so the
    descriptor is rebuilt for every call from the availability the tool boundary
    has just re-read. A module-level singleton would freeze ``LIVE`` at
    registration and quietly keep a shell pointed at a folder the user revoked.
    """

    @staticmethod
    def for_availability(*, available: bool) -> CapabilityDescriptor:
        """The descriptor for one call. ``available=False`` denies at Stage 1.

        ``trust`` is ``TRUSTED`` and that costs nothing: :class:`Trust` is read
        by exactly one rung of the ladder (3.7, the untrusted-READ gate), which
        an ``EXECUTE`` action never reaches. Declaring ``UNTRUSTED`` would read
        as a second, inert safety control and invite someone to rely on it.
        The control for a command is the EXECUTE rung at 3.5½, which pauses in
        every posture unless the axis itself is authored to ``auto``.
        """

        return CapabilityDescriptor(
            urn=ShellCapability.URN,
            action=Action.EXECUTE,
            trust=Trust.TRUSTED,
            # Empty, and load-bearing: an empty required set is what makes
            # ShellPrincipal's overlay a totality fix rather than a grant.
            scopes=(),
            source="builtin",
            connector_state=(ConnectorState.LIVE if available else ConnectorState.OFF),
        )


@dataclass(frozen=True)
class ShellPrincipal:
    """A :class:`Principal` for one command, seeded so Stage 2 is total.

    Structurally satisfies the P0 ``Principal`` Protocol. Identity, roles and the
    session scope set are copied verbatim from the run context; only
    ``connector_scopes`` is overlaid, with a single empty entry for the ``shell``
    connector (see the module docstring for what happens without it).

    The overlay is applied **over** the run's real ``connector_scopes``, so every
    genuinely-present MCP connector entry survives untouched — this principal is
    only ever handed to a ``builtin:shell:run_command`` decision, but building it
    destructively would make that a fact about the call site instead of a fact
    about the object.
    """

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str]
    connector_scopes: dict[str, frozenset[str]]

    @classmethod
    def for_run(cls, context: AgentRuntimeContext) -> "ShellPrincipal":
        """Build the principal for this run's command lane."""

        scopes = dict(context.connector_scopes)
        scopes[ShellCapability.CONNECTOR] = frozenset()
        return cls(
            user_id=context.user_id,
            org_id=context.org_id,
            roles=context.roles,
            permission_scopes=context.permission_scopes,
            connector_scopes=scopes,
        )


class BuiltinCapabilityAllowlist:
    """A ``ConnectorAllowlistPort`` that imposes no org/user restriction.

    Total by construction: every URN — the shell one, a malformed one, someone
    else's — returns the empty :class:`ConnectorAllowlist`, which the PDP reads
    as "no restriction". It never raises.

    Why this is not a hole. The org/user allowlist exists to answer *which
    tenants may reach a registered connector*, and it is read from the connector
    registry by URN. A builtin has no registry row, so there is no allowlist to
    read and no tenant question to answer — the question a command actually
    raises ("may this workspace run commands at all?") is answered upstream, by
    the per-workspace grant flag and the deployment gate (§7.1), before a
    descriptor is ever built. Returning empty here is stating that plainly, in
    one place, rather than passing ``None`` and having each reader decide.
    """

    __slots__ = ()

    _EMPTY: Final = ConnectorAllowlist()

    def allowlist_for(self, urn: str) -> ConnectorAllowlist:
        """Return the empty allowlist for any URN."""

        del urn
        return self._EMPTY


__all__ = [
    "BuiltinCapabilityAllowlist",
    "RunCommandDescriptor",
    "ShellCapability",
    "ShellPrincipal",
]
