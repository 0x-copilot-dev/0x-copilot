"""Recursion controls for the live ``task`` delegation path.

Three rules are enforced here, and they are stated rather than left to be
inferred from the call sites:

**1. Depth is bounded and the bound is carried, not recomputed.**
``build_subagent_invocation_config`` stamps ``delegation_depth`` into the child
graph's ``RunnableConfig``. The supervisor's own config carries no such key, so
it reads as depth ``0``; its children run at ``1``; their children would run at
``2``. A ``task`` call is admitted only while the child's depth stays within
:class:`DelegationAdmissionPolicy.max_depth` (default ``1``: the supervisor may
delegate, a delegate may not delegate again). Depth travels in the config
because that is the only channel that survives the parent -> child graph
invocation; deriving it from process-local state would be wrong the moment two
delegations run concurrently.

**2. A child does not get the ``task`` tool unless its definition grants it.**
Deep Agents hands a subagent that declares no ``tools`` the *parent's* tool
list. If ``task`` is ever in that list, the child can delegate — and its child
can delegate — with nothing structural in the way, on the user's own BYOK key.
:meth:`SubagentRecursionPolicy.narrow_spec` removes it by default and keeps it
only when the spec explicitly opts in. This is belt-and-braces with
rule 1: rule 1 refuses the *call*, rule 2 removes the *capability*.

**3. A child's permission posture is floored at its parent's — never above it.**
On the path this module governs, that floor is structural: Deep Agents hands a
subagent spec that declares no ``tools`` the parent's *already policy-wrapped*
tool objects (``execution.factory`` runs ``ToolUsePolicyEnforcer`` over the
model surface before the subagents are built), so a child's posture is its
parent's — equal to it, never looser. Rule 2 above is what keeps that
inheritance from also handing over the ability to delegate.

The richer form of the rule — intersect against the subagent definition and
take the *stricter* posture per axis, so a definition may tighten but never
loosen — is written and tested in
:mod:`agent_runtime.delegation.subagents.authority`, along with the carve-out
that a parent's bypass must not cross the delegation boundary. Read that
module's header before changing anything here: it is the designed floor, and
it states plainly that its own lane (``SubagentHandoffPolicy`` ->
``DelegationCoordinator``) has no product caller yet, so the bypass carve-out
is not enforced on a live run today. Nothing in this module depends on that
lane; the two are deliberately kept separate rather than one pretending to be
the other.
"""

from __future__ import annotations

from typing import Final

from agent_runtime.delegation.subagents.constants import Messages
from agent_runtime.delegation.subagents.contracts import (
    SubagentError,
    SubagentErrorCode,
)
from agent_runtime.delegation.subagents.coordination import DelegationAdmissionPolicy

#: Config key carrying how many delegation hops below the supervisor a graph is
#: running at. Absent means ``0`` — the supervisor itself.
SUBAGENT_DELEGATION_DEPTH_KEY: Final = "delegation_depth"

#: The built-in delegation tool's name. A child's tool surface excludes it
#: unless its spec opts in through :data:`ALLOW_NESTED_DELEGATION_KEY`.
DELEGATION_TOOL_NAME: Final = "task"

#: Spec key a subagent definition sets to keep ``task`` in its tool surface.
ALLOW_NESTED_DELEGATION_KEY: Final = "allow_nested_delegation"


class DelegationDepthPolicy:
    """Per-run depth admission, snapshotted once and enforced in-process.

    Built at graph-build time (run start) so a mid-run configuration change
    cannot retro-authorize a delegation the run did not start with — the same
    snapshot-then-enforce shape ``ToolUsePolicySnapshot`` uses.
    """

    #: Read once per instance; the document is the source of the number.
    __slots__ = ("_admission",)

    def __init__(self, admission: DelegationAdmissionPolicy | None = None) -> None:
        self._admission = admission or DelegationAdmissionPolicy()

    @classmethod
    def snapshot(cls) -> "DelegationDepthPolicy":
        """Build the policy for this run from the hyperparameter document.

        The document import is function-local on purpose. ``hyperparameters``
        takes its subagent ceilings from ``delegation.subagents.constants``, so
        a module-level import here would close the cycle that
        ``constants.Defaults`` documents and break every import of
        ``agent_runtime``. Deferring it to call time — which is agent-build
        time, once per run — leaves both modules fully loaded.

        A malformed document must not take the delegation path down with it:
        an unreadable or out-of-range value falls back to the packaged default,
        which is the *most* restrictive useful value rather than the least.
        """

        from agent_runtime.hyperparameters.loader import (  # noqa: PLC0415
            HyperparameterLoader,
        )

        try:
            configured = HyperparameterLoader.default().subagents.max_delegation_depth
            return cls(DelegationAdmissionPolicy(max_depth=configured))
        except Exception:  # noqa: BLE001 — see docstring: fail closed, not open
            return cls()

    @property
    def max_depth(self) -> int:
        """The admitted number of delegation hops below the supervisor."""

        return self._admission.max_depth

    @staticmethod
    def parent_depth(config: object) -> int:
        """Read the calling graph's depth out of its ``RunnableConfig``.

        Treated as untrusted: the config reaches here through LangGraph's
        per-key merge and a non-integer, negative, or missing value all resolve
        to ``0`` rather than raising on the tool path.
        """

        if not isinstance(config, dict):
            return 0
        for section in ("metadata", "configurable"):
            values = config.get(section)
            if not isinstance(values, dict):
                continue
            raw = values.get(SUBAGENT_DELEGATION_DEPTH_KEY)
            if isinstance(raw, bool) or not isinstance(raw, int):
                continue
            if raw > 0:
                return raw
        return 0

    def child_depth(self, config: object) -> int:
        """Return the depth the child graph spawned from ``config`` would run at."""

        return self.parent_depth(config) + 1

    def refusal(self, config: object) -> SubagentError | None:
        """Return the typed refusal for this call, or ``None`` when admitted.

        A refusal is a *value*, never an exception: the caller is a model-facing
        tool, and a raise there becomes an opaque runtime failure instead of
        something the model can read and route around.
        """

        if self.child_depth(config) <= self._admission.max_depth:
            return None
        return SubagentError(
            code=SubagentErrorCode.DEPTH_LIMIT_EXCEEDED,
            safe_message=Messages.Delegation.depth_limit_exceeded(
                max_depth=self._admission.max_depth
            ),
            retryable=False,
        )


class SubagentRecursionPolicy:
    """Capability-side half of the recursion controls (rules 2 and 3)."""

    @classmethod
    def grants_nested_delegation(cls, spec: object) -> bool:
        """Return whether this subagent spec was explicitly granted ``task``.

        Deep Agents subagent specs are mappings; the opt-in is an extra key it
        copies through untouched. Anything else — including the auto-added
        ``general-purpose`` subagent, which carries no such key — is not a
        grant.
        """

        if not isinstance(spec, dict):
            return bool(getattr(spec, ALLOW_NESTED_DELEGATION_KEY, False))
        return bool(spec.get(ALLOW_NESTED_DELEGATION_KEY, False))

    @classmethod
    def narrow_spec(cls, spec: object) -> object:
        """Return ``spec`` with ``task`` removed from its child tool surface.

        Called for every subagent Deep Agents compiles, which is where the
        inherited-parent-tools substitution has already happened — the spec's
        ``tools`` here is the list the child will actually run with, whether it
        declared one or inherited ours.

        The spec is returned unchanged (same object) when it declares no tools,
        when it is a pre-compiled runnable, or when it holds the explicit grant.
        A pre-compiled ``CompiledSubAgent`` is out of reach by construction: its
        graph was built by the caller, not here.
        """

        if not isinstance(spec, dict) or "runnable" in spec:
            return spec
        tools = spec.get("tools")
        if tools is None or cls.grants_nested_delegation(spec):
            return spec
        narrowed = [tool for tool in tools if not cls._is_delegation_tool(tool)]
        if len(narrowed) == len(tools):
            return spec
        return {**spec, "tools": narrowed}

    @staticmethod
    def _is_delegation_tool(tool: object) -> bool:
        name = getattr(tool, "name", None)
        if name is None and isinstance(tool, dict):
            name = tool.get("name")
        return name == DELEGATION_TOOL_NAME


__all__ = (
    "ALLOW_NESTED_DELEGATION_KEY",
    "DELEGATION_TOOL_NAME",
    "SUBAGENT_DELEGATION_DEPTH_KEY",
    "DelegationDepthPolicy",
    "SubagentRecursionPolicy",
)
