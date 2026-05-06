"""Run-start gate for tool-use policy modes (PR 8.0.5 §2.2).

Decides whether a loaded tool can dispatch under the user's
tool-use policy. Three branches:

* ``mode = auto``   ⇒ allow; existing fast path.
* ``mode = ask``    ⇒ require approval (one-time per run + tool_name).
* ``mode = require``⇒ require approval (every dispatch).
* ``mode = block``  ⇒ reject the run with a stable safe message.

Reuses the existing approval flow (``APPROVAL_REQUESTED`` /
``APPROVAL_RESOLVED``) for ask/require modes — no new envelope kinds,
no new audit actions, no new FE renderer. ``block`` translates to
``RUN_REJECTED`` (already wired by B7 budget enforcement).

The gate is pure logic: callers feed in the snapshot + spec; the gate
returns a :class:`ToolGateDecision` carrying the chosen branch + the
policy axis that fired (so the audit emitter can stamp ``policy_fired``
without reverse-engineering it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_runtime.capabilities.tools.cards import LoadedToolSpec
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
        return cls(action=ToolGateAction.ALLOW)

    @classmethod
    def require_approval(
        cls,
        *,
        kind: ToolUsePolicyKind,
        mode: ToolUsePolicyMode,
    ) -> "ToolGateDecision":
        return cls(
            action=ToolGateAction.REQUIRE_APPROVAL,
            policy_fired=kind,
            mode=mode,
            one_time=mode is ToolUsePolicyMode.ASK,
        )

    @classmethod
    def reject(cls, *, kind: ToolUsePolicyKind) -> "ToolGateDecision":
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
        """Run the policy lookup + branch."""

        kind = kind_for_side_effects(spec.side_effects)
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
