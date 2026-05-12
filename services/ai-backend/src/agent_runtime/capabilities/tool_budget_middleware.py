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


@dataclass(frozen=True)
class ToolBudgetReject:
    """The call would exceed a hard cap; reject before invocation."""

    budget: ToolBudgetRecord
    kind: str
    current: int
    limit: int

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
        """Return a safe user-facing rejection message."""
        return (
            f"Tool '{self.budget.tool_name}' rejected: "
            f"per-run {self.kind} budget ({self.current + 1}/{self.limit}) "
            "exceeded. Continue with a different approach or finalize."
        )


ToolBudgetDecision = Union[ToolBudgetAdmit, ToolBudgetWarn, ToolBudgetReject]


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
            )

        if budget.max_input_tokens_per_run is not None:
            run_total = ledger.total_input_tokens(tool_name) + estimated_input_tokens
            if run_total > budget.max_input_tokens_per_run:
                return self._violation(
                    budget=budget,
                    kind="input_tokens_per_run",
                    current=ledger.total_input_tokens(tool_name),
                    limit=budget.max_input_tokens_per_run,
                )

        return ToolBudgetAdmit()

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
    ) -> ToolBudgetDecision:
        """Return a ``ToolBudgetWarn`` or ``ToolBudgetReject`` based on the budget's enforcement mode."""
        if budget.enforcement is ToolBudgetEnforcement.SOFT:
            return ToolBudgetWarn(
                budget=budget,
                kind=kind,
                current=current,
                limit=limit,
            )
        return ToolBudgetReject(
            budget=budget,
            kind=kind,
            current=current,
            limit=limit,
        )
