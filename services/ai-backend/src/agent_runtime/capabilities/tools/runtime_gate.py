"""Run-start gate that enforces tool-use policy modes for each loaded tool.

Four branches: ``auto`` (allow), ``ask`` (approval once per run + tool_name),
``require`` (approval on every dispatch), ``block`` (reject the run). Reuses the
existing approval flow for ask/require; ``block`` maps to ``RUN_REJECTED``. Pure
logic — callers feed in the snapshot and spec; the gate returns a
:class:`ToolGateDecision` with the chosen branch and the policy axis that fired.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_runtime.capabilities.tools.cards import LoadedToolSpec, ToolSideEffect
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
    kind_for_side_effects,
)


class ToolGateAction(StrEnum):
    """The branch the gate selected."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REJECT = "reject"


@dataclass(frozen=True)
class ToolGateDecision:
    """Outcome of one gate evaluation.

    ``policy_fired`` is the axis (read/write/destructive) the mode
    came from. The audit emitter writes it to the existing
    ``tool_call_outcome`` row's metadata; SIEM exports can pivot
    on it without a new audit action.

    ``one_time`` is True only on ``mode = ask`` so the harness can
    cache the user's approval per (run, tool_name); ``mode = require``
    re-prompts every dispatch.
    """

    action: ToolGateAction
    policy_fired: ToolUsePolicyKind | None = None
    mode: ToolUsePolicyMode | None = None
    one_time: bool = False
    safe_message: str | None = None

    @classmethod
    def allow(cls) -> "ToolGateDecision":
        """Return an ALLOW decision with no policy axis recorded."""
        return cls(action=ToolGateAction.ALLOW)

    @classmethod
    def require_approval(
        cls,
        *,
        kind: ToolUsePolicyKind,
        mode: ToolUsePolicyMode,
    ) -> "ToolGateDecision":
        """Return a REQUIRE_APPROVAL decision; ``one_time`` is set only for ask mode."""
        return cls(
            action=ToolGateAction.REQUIRE_APPROVAL,
            policy_fired=kind,
            mode=mode,
            one_time=mode is ToolUsePolicyMode.ASK,
        )

    @classmethod
    def reject(cls, *, kind: ToolUsePolicyKind) -> "ToolGateDecision":
        """Return a REJECT decision with the per-axis safe rejection message."""
        return cls(
            action=ToolGateAction.REJECT,
            policy_fired=kind,
            mode=ToolUsePolicyMode.BLOCK,
            safe_message=_REJECTION_MESSAGES[kind],
        )


_REJECTION_MESSAGES: dict[ToolUsePolicyKind, str] = {
    ToolUsePolicyKind.READ: "Read tools are blocked by your tool-use policy.",
    ToolUsePolicyKind.WRITE: "Write tools are blocked by your tool-use policy.",
    ToolUsePolicyKind.DESTRUCTIVE: (
        "Destructive tools are blocked by your tool-use policy."
    ),
}


class ToolUsePolicyGate:
    """Single decision point for run-start tool dispatch."""

    @classmethod
    def decide(
        cls,
        *,
        snapshot: ToolUsePolicySnapshot,
        spec: LoadedToolSpec,
    ) -> ToolGateDecision:
        """Run the policy lookup + branch for a fully loaded tool spec."""

        return cls.decide_for_side_effects(
            snapshot=snapshot,
            side_effects=spec.side_effects,
        )

    @classmethod
    def decide_for_side_effects(
        cls,
        *,
        snapshot: ToolUsePolicySnapshot,
        side_effects: frozenset[ToolSideEffect],
    ) -> ToolGateDecision:
        """Run the policy lookup + branch from a raw side-effect set.

        The side-effect-only entry point exists for callers that gate an
        umbrella model tool (e.g. ``call_mcp_tool``) at run-start, where the
        concrete per-invocation :class:`LoadedToolSpec` is not yet resolved but
        the tool's coarse side-effect class is known. :meth:`decide` delegates
        here so both entry points classify identically.
        """

        kind = kind_for_side_effects(side_effects)
        mode = snapshot.mode_for_kind(kind)
        if mode is ToolUsePolicyMode.BLOCK:
            return ToolGateDecision.reject(kind=kind)
        if mode in {ToolUsePolicyMode.ASK, ToolUsePolicyMode.REQUIRE}:
            return ToolGateDecision.require_approval(kind=kind, mode=mode)
        return ToolGateDecision.allow()


__all__ = [
    "ToolGateAction",
    "ToolGateDecision",
    "ToolUsePolicyGate",
]
