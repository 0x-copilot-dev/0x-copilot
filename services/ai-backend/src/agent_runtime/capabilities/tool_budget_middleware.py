"""Pure admit/warn/reject budget middleware for per-tool call accounting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from agent_runtime.execution.tool_outcomes import ToolErrorCode, ToolOutcome
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)

if TYPE_CHECKING:  # pragma: no cover — typing-only.
    # Late import to avoid a cycle through runtime_worker.dependencies.
    from runtime_worker.tool_call_ledger import ToolCallLedger


_GLOBAL_TOOL_NAME = "*"


@dataclass(frozen=True)
class ToolBudgetAdmit:
    """The call was admitted; the budget (if any) was respected."""


@dataclass(frozen=True)
class ToolBudgetWarn:
    """The call exceeded a soft cap; admit but surface the warning to the run."""

    budget: ToolBudgetRecord
    kind: str  # "calls" or "input_tokens_per_call" or "input_tokens_per_run"
    current: int
    limit: int
    # The tool actually requested. Distinct from ``budget.tool_name``,
    # which is ``"*"`` whenever the global wildcard row matched.
    tool_name: str = ""


@dataclass(frozen=True)
class ToolBudgetReject:
    """The call would exceed a hard cap; reject before invocation."""

    budget: ToolBudgetRecord
    kind: str
    current: int
    limit: int
    # The tool actually requested. Reporting ``budget.tool_name`` here
    # would print ``'*'`` for every wildcard-governed rejection, which
    # names no tool the model can act on and reads as a bug in logs.
    tool_name: str = ""

    @property
    def outcome(self) -> ToolOutcome:
        """Return the fixed ``REJECTED`` outcome for this decision."""
        return ToolOutcome.REJECTED

    @property
    def error_code(self) -> ToolErrorCode:
        """Return the fixed ``TOOL_BUDGET_EXCEEDED`` error code."""
        return ToolErrorCode.TOOL_BUDGET_EXCEEDED

    @property
    def safe_message(self) -> str:
        """Return the model-facing rejection message.

        Written as an instruction, not just a diagnosis: this string is
        handed to the model as a tool result, and the behavior we want
        from it is "stop calling this tool and answer now".
        """
        return (
            f"Tool '{self.tool_name or self.budget.tool_name}' is out of "
            f"budget for this run: the per-run {self.kind} limit was "
            f"exceeded ({self.current}/{self.limit} used). Do not call this "
            "tool again — finalize now and answer with what you have "
            "already gathered, stating plainly what is still uncertain."
        )


ToolBudgetDecision = Union[ToolBudgetAdmit, ToolBudgetWarn, ToolBudgetReject]


@dataclass(frozen=True)
class ToolBudgetUsage:
    """How much of one tool's per-run allowance is spent, for the model.

    The budget used to be invisible until the moment it refused a call, so
    the model planned as if it had unlimited calls and then hit a wall with
    research half-finished. Reporting the remaining count as it goes lets it
    spend what it has deliberately — the difference between "I have two
    searches left, make them count" and an abrupt stop.
    """

    tool_name: str
    used: int
    limit: int

    # Report only once the tail is in sight. Annotating every result would
    # add a line to each of an agent's tool calls for no decision-relevant
    # information while there is plenty of headroom.
    NOTICE_THRESHOLD = 3

    # The invariant head of every rendered note, factored out of the two
    # branches below so a reader of a materialized tool result can recognise
    # the note without re-deriving its shape. The Context Occupancy Ledger
    # splits these appended notes off a ``ToolMessage`` tail to report what
    # they cost (design §4.6, audit item M); that split has to be exact rather
    # than a regex guess, which it can only be while the producer and the
    # recogniser read the same constant.
    NOTE_PREFIX = "[Tool budget — "

    @property
    def remaining(self) -> int:
        """Calls left before the next one is refused; never negative."""
        return max(self.limit - self.used, 0)

    @property
    def should_notify(self) -> bool:
        """True once the remaining count is worth spending context on."""
        return self.remaining <= self.NOTICE_THRESHOLD

    def render_note(self) -> str:
        """Build the model-facing note. Phrased as a plan instruction.

        Says "this turn" because the allowance is per run: a fresh user
        message starts over, so the model must not carry an exhausted
        budget into how it answers the next one.
        """

        if self.remaining == 0:
            return (
                f"{self.NOTE_PREFIX}{self.tool_name}: {self.used} of {self.limit} "
                "calls used this turn. None left. Do not call it again; "
                "answer with what you already have.]"
            )
        call_word = "call" if self.remaining == 1 else "calls"
        return (
            f"{self.NOTE_PREFIX}{self.tool_name}: {self.used} of {self.limit} "
            f"calls used this turn, {self.remaining} {call_word} left. "
            "Make them count, then answer with what you have.]"
        )


class WorkspaceToolBudgetOverride:
    """Applies the workspace's per-tool call cap over the configured rows.

    The Settings "Model & behavior" control writes one number: how many
    times the agent may call any single tool in a run. That number is the
    workspace's own **wildcard** budget, so it replaces the wildcard rows
    and leaves narrower rows alone — an operator who capped one specific
    tool did so deliberately, and a workspace-wide preference must not
    quietly widen it.

    Pure: takes rows in, returns rows out. The caller owns the I/O.
    """

    @classmethod
    def apply(
        cls,
        budgets: Sequence[ToolBudgetRecord],
        *,
        max_calls_per_run: int | None,
    ) -> tuple[ToolBudgetRecord, ...]:
        """Return ``budgets`` with the workspace cap applied to wildcard rows.

        ``None`` (the setting is unset) returns the rows untouched. When a
        cap is set but the deployment has no wildcard row to rewrite, one
        is synthesized so the setting still governs rather than silently
        doing nothing.
        """

        if max_calls_per_run is None:
            return tuple(budgets)
        rewritten = tuple(
            budget.model_copy(update={"max_calls_per_run": max_calls_per_run})
            if budget.tool_name == _GLOBAL_TOOL_NAME
            else budget
            for budget in budgets
        )
        if any(budget.tool_name == _GLOBAL_TOOL_NAME for budget in rewritten):
            return rewritten
        return (*rewritten, cls._synthesized(max_calls_per_run))

    @staticmethod
    def _synthesized(max_calls_per_run: int) -> ToolBudgetRecord:
        """Build the wildcard row a deployment without one is missing."""

        from agent_runtime.persistence.records.tool_budgets import (  # noqa: PLC0415
            DefaultToolBudget,
        )

        return DefaultToolBudget.record().model_copy(
            update={"max_calls_per_run": max_calls_per_run}
        )


class ToolBudgetMiddleware:
    """Stateless admit/warn/reject middleware; call accounting lives on the per-run ledger."""

    def __init__(self, budgets: Sequence[ToolBudgetRecord]) -> None:
        """Initialise with an immutable snapshot of the run's tool budget records."""
        self._budgets = tuple(budgets)

    def check_admit(
        self,
        *,
        ledger: "ToolCallLedger",
        tool_name: str,
        estimated_input_tokens: int = 0,
    ) -> ToolBudgetDecision:
        """Return an Admit, Warn, or Reject decision for ``tool_name`` against the live ledger."""

        budget = self._resolve_budget(tool_name)
        if budget is None:
            return ToolBudgetAdmit()

        current_calls = ledger.charged_calls(tool_name)
        if current_calls + 1 > budget.max_calls_per_run:
            return self._violation(
                budget=budget,
                kind="calls",
                current=current_calls,
                limit=budget.max_calls_per_run,
                tool_name=tool_name,
            )

        if (
            budget.max_input_tokens_per_call is not None
            and estimated_input_tokens > budget.max_input_tokens_per_call
        ):
            return self._violation(
                budget=budget,
                kind="input_tokens_per_call",
                current=estimated_input_tokens,
                limit=budget.max_input_tokens_per_call,
                tool_name=tool_name,
            )

        if budget.max_input_tokens_per_run is not None:
            run_total = ledger.total_input_tokens(tool_name) + estimated_input_tokens
            if run_total > budget.max_input_tokens_per_run:
                return self._violation(
                    budget=budget,
                    kind="input_tokens_per_run",
                    current=ledger.total_input_tokens(tool_name),
                    limit=budget.max_input_tokens_per_run,
                    tool_name=tool_name,
                )

        return ToolBudgetAdmit()

    def usage(
        self,
        *,
        ledger: "ToolCallLedger",
        tool_name: str,
    ) -> "ToolBudgetUsage | None":
        """Return how much of ``tool_name``'s budget is spent, or ``None``.

        ``None`` means no budget governs this tool, so there is nothing
        honest to report — callers must stay silent rather than invent a
        limit.
        """

        budget = self._resolve_budget(tool_name)
        if budget is None:
            return None
        return ToolBudgetUsage(
            tool_name=tool_name,
            used=ledger.charged_calls(tool_name),
            limit=budget.max_calls_per_run,
        )

    def _resolve_budget(self, tool_name: str) -> ToolBudgetRecord | None:
        """Return the most-specific budget that covers ``tool_name``, or ``None``."""
        # Resolution order: exact match > org wildcard > global name > global wildcard.
        # The port already filters by org_id, so ranking collapses to
        # (tool_name match, org_id present).
        ranked = sorted(
            self._budgets,
            key=lambda b: (
                0 if b.tool_name == tool_name else 1,
                0 if b.org_id is not None else 1,
            ),
        )
        for budget in ranked:
            if budget.tool_name == tool_name or budget.tool_name == _GLOBAL_TOOL_NAME:
                return budget
        return None

    @staticmethod
    def _violation(
        *,
        budget: ToolBudgetRecord,
        kind: str,
        current: int,
        limit: int,
        tool_name: str,
    ) -> ToolBudgetDecision:
        """Return a ``ToolBudgetWarn`` or ``ToolBudgetReject`` based on the budget's enforcement mode."""
        if budget.enforcement is ToolBudgetEnforcement.SOFT:
            return ToolBudgetWarn(
                budget=budget,
                kind=kind,
                current=current,
                limit=limit,
                tool_name=tool_name,
            )
        return ToolBudgetReject(
            budget=budget,
            kind=kind,
            current=current,
            limit=limit,
            tool_name=tool_name,
        )
