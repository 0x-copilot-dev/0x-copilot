"""The run-scoped approval authority F6's graph seam was built to consult.

BUG-15 established the rule and left it unreachable. It declared the port —
:class:`~agent_runtime.capabilities.concurrency.graph_admission.GraphApprovalRequirementSource`
— proved that folding a run's answer with the catalog's can only narrow, and
named the two production authorities the catalog author cannot see: the run's
``ToolUsePolicySnapshot`` and the connector's live auth state. Nothing satisfied
the port. ``RunBatchAdmission`` was constructed in exactly one place in ``src/``
and that place passed no ``approvals=``, so
``GraphApprovalRequirementResolver.resolve(None, ...)`` answered ``None`` on
every call, the catalog's own claim survived untouched, and the tool-use policy
was never asked. The guarantee was real and the wiring that feeds it was absent.

This module is the first of the two authorities, on the worker's side of the
boundary. Three properties are structural rather than conventional.

**It asks the enforcer rather than modelling it.** Whether this run installs a
human-approval interrupt for a graph tool is decided by exactly one thing in
production: :meth:`ToolUsePolicyEnforcer.enforce`, whose ``interrupt_on`` map the
factory hands to Deep Agents verbatim. This source asks *that* method what it
would do for a tool of this name under this run's snapshot, rather than
re-deriving the answer from the tool→axis table the enforcer keeps private.
There is therefore no second copy of the policy's reach that could drift from
the first, and a tool added to the enforcer's gated set is gated here the moment
it is gated there.

**It answers about one axis, and says so.** ``NEVER`` is ``narrowest``'s identity
element: ``narrowest(declared, NEVER)`` is ``declared``. Returning it is how a
*narrowing* authority declines to speak without either lying — claiming a
capability cannot park — or destroying the catalog's own claim, which
``UNKNOWN`` would do for every capability at once and leave F6 planning nothing
at all. Every answer this source gives is therefore about the tool-use policy
and nothing else; the second authority BUG-15 names, the MCP access gate's live
auth state, is still unwired and is not this module's to guess at. That leaves
the deployment strictly narrower than it is today and never wider, which is the
property that matters.

**Absent means unknown, not silent.** A source that cannot reach its authority —
no snapshot, an unnamed tool, an enforcer that raises — answers ``UNKNOWN`` for
every call, which makes every capability unplannable. That is deliberately *not*
the same as passing no source at all: an absent source leaves the catalog
untouched, so collapsing the two would make a broken policy lane quieter than a
missing one. The composition root consequently always passes a source, and this
class carries the fail-closed state internally.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any, ClassVar

from agent_runtime.capabilities.concurrency.contracts import ApprovalRequirement
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.capabilities.tools.tool_use_enforcement import ToolUsePolicyEnforcer

_LOGGER = logging.getLogger(__name__)


class ToolUsePolicyApprovalSource:
    """Read one run's tool-use policy as F6's run-scoped approval authority.

    Satisfies ``GraphApprovalRequirementSource`` structurally rather than by
    inheritance, the same way every other port in this lane is satisfied.

    Immutable after construction, and synchronous: the graph seam consults it
    while preparing a plan entry, before anything is awaited, so an answer that
    could block would put a store read on the planning path of every turn.
    """

    #: ``narrowest``'s identity. Returning it means "this authority contributes
    #: no narrowing to this call", which is the only honest answer a single-axis
    #: authority has when its axis is silent. It is never a claim that the
    #: capability cannot park — that claim belongs to the catalog, and the fold
    #: in ``RunBatchAdmission._approval_for`` leaves it exactly where it was.
    CONTRIBUTES_NO_NARROWING: ClassVar[ApprovalRequirement] = ApprovalRequirement.NEVER

    #: The answer for every call this source could not evaluate. ``UNKNOWN``
    #: carries ``may_park``, so it makes the capability unplannable and the turn
    #: serial — the pre-F6 path, which is what an unreadable authority is owed.
    UNREADABLE: ClassVar[ApprovalRequirement] = ApprovalRequirement.UNKNOWN

    __slots__ = ("_snapshot",)

    @dataclass(frozen=True, slots=True)
    class _ToolNameProbe:
        """A model tool reduced to the only field the enforcer classifies on.

        ``ToolUsePolicyEnforcer.enforce`` reads ``name`` to look the tool up in
        its gated table and otherwise touches a tool only to wrap a ``block``
        decision, which requires a ``BaseTool`` this deliberately is not. So a
        probe can obtain the run's ``interrupt_on`` decision for a tool name
        without materialising the tool — which the composition root could not do
        anyway, since it composes before the tool surface is built.
        """

        name: str

    def __init__(self, snapshot: ToolUsePolicySnapshot | None) -> None:
        self._snapshot = snapshot

    @property
    def is_readable(self) -> bool:
        """Return whether this source reached its authority at all."""

        return self._snapshot is not None

    def approval_requirement_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ApprovalRequirement:
        """Return what this run's tool-use policy adds to one call's dispatch.

        ``ALWAYS`` when the policy would install a human-approval interrupt for
        this graph tool — under ``ask`` it parks on the first dispatch and under
        ``require`` on every one, and neither permits a cohort of siblings to
        park together, so the distinction carries no decision here.

        Arguments are deliberately unread. The policy gates the *umbrella* model
        tool, which is the unit the interrupt is installed on; an MCP call names
        its server and tool inside ``call_mcp_tool``'s arguments and is gated as
        one tool regardless. Reading them would invent a per-argument reach the
        enforcer does not have.
        """

        del arguments
        name = tool_name.strip()
        if self._snapshot is None or not name:
            return self.UNREADABLE
        try:
            surface = ToolUsePolicyEnforcer.enforce(
                model_tools=(self._ToolNameProbe(name=name),),
                # Left at its default on purpose. A run that delegates the MCP
                # umbrella to the operation gateway installs no interrupt here
                # and classifies later instead, so naming the delegation would
                # make this source answer *less* often — and this seam may only
                # ever narrow.
                snapshot=self._snapshot,
            )
        except Exception:  # noqa: BLE001 - an unreadable policy answers unknown.
            _LOGGER.warning(
                "F6 could not read the run's tool-use policy; treating the "
                "capability as approval-bound",
                exc_info=True,
            )
            return self.UNREADABLE
        if name in surface.interrupt_on:
            return ApprovalRequirement.ALWAYS
        return self.CONTRIBUTES_NO_NARROWING


__all__ = ("ToolUsePolicyApprovalSource",)
