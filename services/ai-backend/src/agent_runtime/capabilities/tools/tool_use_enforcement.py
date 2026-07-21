"""Run-start enforcement of the per-(org, user) tool-use policy.

The policy (``read`` / ``write`` / ``destructive`` axes × ``auto`` / ``ask`` /
``require`` / ``block`` modes) is stored by the backend, surfaced in Settings,
and hydrated onto ``AgentRuntimeContext.user_policies_json`` at run-create by the
``/internal/v1/policies/runtime`` aggregate. This module is the enforcement seam
that makes the stored setting actually change agent behavior:

* :class:`ToolUsePolicyResolver` builds the run's frozen
  :class:`ToolUsePolicySnapshot` from ``user_policies_json['tool_use']``. When
  the aggregate lane is not configured (``NullUserPoliciesResolver`` yields
  ``{}``) it falls back to the deployment default (``read=auto`` / ``write=ask``
  / ``destructive=require``) so an unconfigured run is byte-identical to the
  behavior before enforcement existed — it NEVER hard-refuses on missing policy.

* :class:`ToolUsePolicyEnforcer` applies the snapshot to the model-visible
  umbrella tool surface. Connector side effects run through the single
  ``call_mcp_tool`` tool, so that is the tool the write/destructive axes gate.
  The enforcer reuses the two mechanisms the runtime ALREADY uses to pause /
  deny a tool call — it invents no new approval engine:

    - ``ask`` / ``require`` → the tool is added to the Deep Agents
      ``interrupt_on`` map, which installs the SAME
      ``HumanInTheLoopMiddleware`` that already gates ``call_mcp_tool`` today.
    - ``block`` → the tool is wrapped in a :class:`PolicyBlockedTool` that
      returns the gate's safe rejection message instead of dispatching, exactly
      as the connector-scope permission-denied path returns a failure result
      rather than crashing the run.
    - ``auto`` → the tool is left untouched (no interrupt, no wrapper).

Scope note (deliberately conservative — see D2): only the umbrella tools whose
calls ALREADY route through the human-approval interrupt today are policy-driven
here. Every other model tool keeps its current behavior. Because the deployment
default write mode is ``ask``, the unconfigured lane still gates
``call_mcp_tool`` exactly as before. Connector-scope gating
(``ToolPermissionChecker.has_scopes_for_connector``) and the ``/workspace/**``
host-write interrupt are separate, orthogonal layers and are left untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.tools.cards import ToolSideEffect
from agent_runtime.capabilities.tools.permissions import (
    ToolPermissionChecker,
    ToolUsePolicySnapshot,
)
from agent_runtime.capabilities.tools.runtime_gate import (
    ToolGateAction,
    ToolUsePolicyGate,
)
from agent_runtime.execution.contracts import AgentRuntimeContext

# ``interrupt_on`` decisions for an ``ask`` / ``require`` gate. Byte-identical to
# the set ``call_mcp_tool`` used before enforcement was policy-driven, so a
# default-policy run offers the same approve / edit / reject card as before.
_APPROVAL_DECISIONS: Final[tuple[str, ...]] = ("approve", "edit", "reject")


class _Keys:
    """Wire keys for the ``tool_use`` sub-policy shape on ``user_policies_json``."""

    TOOL_USE = "tool_use"
    WORKSPACE = "workspace"
    USER = "user"


class ToolUsePolicyResolver:
    """Build the run's tool-use snapshot from ``AgentRuntimeContext``.

    Reads only the ``tool_use`` sub-policy the backend aggregate wrote onto
    ``user_policies_json`` — the ``privacy`` sub-policy and BYOK keys are
    consumed elsewhere. Fails OPEN to the deployment default snapshot whenever
    the sub-policy is absent or malformed, so a run whose backend policy lane is
    unconfigured behaves exactly as it did before enforcement existed.
    """

    @classmethod
    def resolve(cls, runtime_context: AgentRuntimeContext) -> ToolUsePolicySnapshot:
        """Return the frozen ``(kind → mode)`` snapshot for this run."""

        policies = runtime_context.user_policies_json or {}
        tool_use = (
            policies.get(_Keys.TOOL_USE) if isinstance(policies, Mapping) else None
        )
        if not isinstance(tool_use, Mapping):
            # Fail-open lane: no stored policy → deployment defaults.
            return ToolPermissionChecker.from_policy(None)
        workspace = tool_use.get(_Keys.WORKSPACE)
        user = tool_use.get(_Keys.USER)
        return ToolUsePolicySnapshot.from_response(
            workspace=workspace if isinstance(workspace, Mapping) else None,
            user=user if isinstance(user, Mapping) else None,
        )


class PolicyBlockedTool(BaseTool):
    """Model tool wrapper that denies a call blocked by the tool-use policy.

    Preserves the inner tool's ``name`` / ``description`` / ``args_schema`` so
    the model surface is identical — the tool is still offered, but any attempt
    to call it returns the gate's safe rejection message instead of dispatching
    to the real implementation. Nothing is raised, so the run continues and the
    model can adapt, mirroring the connector-scope permission-denied result the
    MCP tool already returns rather than crashing the run.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    safe_message: str

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """Return the safe rejection message without touching the inner tool."""
        del args, kwargs
        return self.safe_message

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        """Async variant of :meth:`_run`; the graph drives tools via ``ainvoke``."""
        del args, kwargs
        return self.safe_message

    @classmethod
    def wrap(cls, tool: BaseTool, *, safe_message: str) -> "PolicyBlockedTool":
        """Return a blocked wrapper carrying ``tool``'s model-visible surface."""
        return cls(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            safe_message=safe_message,
        )


@dataclass(frozen=True)
class EnforcedToolSurface:
    """Result of applying the tool-use policy to the model tool surface.

    ``tools`` is the (possibly block-wrapped) model tool tuple to hand to the
    builder; ``interrupt_on`` is the Deep Agents human-approval config for the
    tools whose axis resolved to ``ask`` / ``require``. ``interrupt_on`` is
    empty when no gated umbrella tool is present or every axis resolved to
    ``auto`` / ``block`` — the builder then installs no HITL middleware, exactly
    as before.
    """

    tools: tuple[object, ...]
    interrupt_on: dict[str, object]


class ToolUsePolicyEnforcer:
    """Apply a resolved snapshot to the model-visible umbrella tool surface."""

    #: Umbrella model tools whose calls today already route through the
    #: human-approval interrupt, mapped to the side-effect class that classifies
    #: them onto a policy axis. ``call_mcp_tool`` funnels every connector action
    #: (an external call → ``write`` axis via ``kind_for_side_effects``). Only
    #: these tools are policy-driven; anything absent from this map keeps its
    #: current (ungated) behavior so an unconfigured deployment is unchanged.
    _GATED_TOOL_SIDE_EFFECTS: ClassVar[Mapping[str, frozenset[ToolSideEffect]]] = {
        McpValues.ToolName.CALL_MCP_TOOL: frozenset({ToolSideEffect.EXTERNAL_CALL}),
    }

    @classmethod
    def enforce(
        cls,
        *,
        model_tools: Sequence[object],
        snapshot: ToolUsePolicySnapshot,
    ) -> EnforcedToolSurface:
        """Return the enforced tool tuple + Deep Agents ``interrupt_on`` config.

        For each gated umbrella tool present in ``model_tools`` the snapshot is
        consulted via :meth:`ToolUsePolicyGate.decide_for_side_effects` and the
        outcome routed: ``auto`` leaves the tool alone, ``ask`` / ``require``
        add an ``interrupt_on`` entry (existing HITL), ``block`` replaces the
        tool with a :class:`PolicyBlockedTool`.
        """

        tools = list(model_tools)
        interrupt_on: dict[str, object] = {}
        for index, tool in enumerate(tools):
            tool_name = str(getattr(tool, "name", "")).strip()
            side_effects = cls._GATED_TOOL_SIDE_EFFECTS.get(tool_name)
            if side_effects is None:
                continue
            decision = ToolUsePolicyGate.decide_for_side_effects(
                snapshot=snapshot,
                side_effects=side_effects,
            )
            if decision.action is ToolGateAction.REQUIRE_APPROVAL:
                interrupt_on[tool_name] = {
                    "allowed_decisions": list(_APPROVAL_DECISIONS)
                }
            elif decision.action is ToolGateAction.REJECT and isinstance(
                tool, BaseTool
            ):
                tools[index] = PolicyBlockedTool.wrap(
                    tool,
                    safe_message=decision.safe_message
                    or "This tool is blocked by your tool-use policy.",
                )
            # ToolGateAction.ALLOW → leave the tool and interrupt_on untouched.
        return EnforcedToolSurface(tools=tuple(tools), interrupt_on=interrupt_on)


__all__ = [
    "EnforcedToolSurface",
    "PolicyBlockedTool",
    "ToolUsePolicyEnforcer",
    "ToolUsePolicyResolver",
]
