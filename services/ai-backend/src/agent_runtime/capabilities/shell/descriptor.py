"""The PDP inputs for ``run_command`` — identity, principal, allowlist (§4.1, §8.1).

``run_command`` is the **first non-MCP capability through the PDP**
(:class:`~agent_runtime.capabilities.policy.service.PdpPolicyService`), whose
authorization stage was written connector-shaped. This module supplies the three
builtin analogues §8.1 (OQ-1) asks for: a ``ConnectorState``, an empty
``scopes`` tuple, and an allowlist port. Nothing here decides anything — the
decision is ``PdpPolicyService.decide``, the enforcement point is
:mod:`agent_runtime.capabilities.shell.policy_gate`.

**The trap, and why this module exists at all.** ``PdpPolicyService._has_scopes``
is fail-closed at the *connector* level: ``connector_scopes.get(connector) is
None`` ⇒ ``False``, so a connector absent from the map is unauthorized **even
when nothing is required**. A run's ``connector_scopes`` is keyed by MCP
connector slug and will never carry ``shell``, so a naive principal makes Stage 2
deny every command with ``permission_denied`` — a dead capability that looks like
a policy decision.

It is smaller than the MCP descriptor source it is modelled on
(``mcp/descriptor_source.py``, 357 lines) because that one must *derive* action,
trust and connector state from a catalog kind, tool annotations, and
health-plus-pause-plus-access-mode. All three are constants for a builtin, so
the code that would compute them is absent rather than stubbed.
"""

from __future__ import annotations

from typing import Final

from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    Principal,
    Trust,
)
from agent_runtime.capabilities.policy.service import ConnectorAllowlist
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import AgentRuntimeContext


class ShellCapability:
    """The one identity ``run_command`` is policed under.

    ``OP`` is deliberately **not** ``execute``: that name is already claimed in
    all three occupancy declarations by deepagents' filesystem placeholder
    (``capabilities/operations/conformance.py``, ``builtin_operation_catalog.json``,
    ``operation_descriptors.json``), and a collision there is a silent identity
    merge — two capabilities resolving to one URN and inheriting each other's
    rules.
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
        as a second, inert safety control and invite someone to rely on it. The
        control for a command is the EXECUTE rung at 3.5½, which pauses in every
        posture unless the axis itself is authored to ``auto``.
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


class ShellPrincipal:
    """The run's :class:`Principal` for one command, seeded so Stage 2 is total.

    The overlay is exactly one entry, ``{"shell": frozenset()}``, which grants
    nothing — the required set is empty on both sides — and only makes the stage
    total for a capability whose authority lives elsewhere (see the module
    docstring's trap).

    **Deliberately not a dataclass of its own.** ``AgentRuntimeContext`` already
    *is* the production ``Principal`` and carries exactly the five fields the PDP
    reads, so re-declaring them here would be a third copy of one row — the
    context, this class, and ``mcp.policy_allowlist.McpConnectorPrincipal``,
    which is that shape spelled out field-by-field with a different overlay
    value. ``model_copy`` is non-destructive in both directions: present
    connector entries survive, and the caller's context is not mutated.
    """

    __slots__ = ()

    @staticmethod
    def for_run(context: AgentRuntimeContext) -> Principal:
        """Overlay the one empty ``shell`` entry onto this run's principal."""

        return context.model_copy(
            update={
                "connector_scopes": {
                    **context.connector_scopes,
                    ShellCapability.CONNECTOR: frozenset(),
                }
            }
        )


class BuiltinCapabilityAllowlist:
    """A ``ConnectorAllowlistPort`` that imposes no org/user restriction.

    Total by construction — the shell URN, a malformed one, someone else's, all
    return the empty :class:`ConnectorAllowlist` the PDP reads as "no
    restriction", and it never raises.

    Not a hole. The org/user allowlist answers *which tenants may reach a
    registered connector*, read from the connector registry by URN; a builtin has
    no registry row, so there is no allowlist to read. The question a command
    actually raises — may this workspace run commands at all — is answered
    upstream by the per-workspace grant flag and the deployment gate (§7.1),
    before a descriptor exists.
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
