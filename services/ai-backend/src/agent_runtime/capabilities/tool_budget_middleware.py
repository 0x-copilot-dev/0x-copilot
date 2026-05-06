"""B8 — code-enforced per-tool budget middleware.

Pure decision module. Given:

- a snapshot of ``runtime_tool_budgets`` rows for the org (plus the
  global default), and
- the run's :class:`ToolCallLedger` of admitted calls so far,

the middleware decides whether to admit a tool call. Hard violations
return :class:`ToolOutcome.REJECTED` with
:class:`ToolErrorCode.TOOL_BUDGET_EXCEEDED`. Soft violations admit and
emit a ``BUDGET_WARNING``-style payload through ``warnings`` so the
caller can append the event.

Resolution rule: most-specific match wins.

  exact (org_id, tool_name)  >  (org_id, '*')
                             >  (None, tool_name)
                             >  (None, '*')

The seed default (``id='seed_default'``) is ``(None, '*', 6, 'hard')``;
custom rules supersede it.

This module does NOT execute tools. The actual interception of LangGraph
tool dispatch is the responsibility of a follow-up PR — once the
LangGraph harness exposes a per-tool wrap point we plug
:meth:`ToolBudgetMiddleware.check_admit` into it. Until then this
module's tests pin the policy and the supervisor's prompt suffix
references the configured cap (so the model behaves consistently with
the future hard-enforcement contract).
"""

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
    # ``ToolCallLedger`` lives under ``runtime_worker`` whose
    # ``__init__`` re-exports the worker entrypoint; importing it at
    # module scope would drag the whole worker package in (and create a
    # cycle once :mod:`runtime_worker.dependencies` imports the
    # downstream :class:`ToolBudgetGuard`). The middleware only uses
    # ``ToolCallLedger`` in a parameter annotation, so a TYPE_CHECKING
    # import is sufficient.
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
        return ToolOutcome.REJECTED

    @property
    def error_code(self) -> ToolErrorCode:
        return ToolErrorCode.TOOL_BUDGET_EXCEEDED

    @property
    def safe_message(self) -> str:
        return (
            f"Tool '{self.budget.tool_name}' rejected: "
            f"per-run {self.kind} budget ({self.current + 1}/{self.limit}) "
            "exceeded. Continue with a different approach or finalize."
        )


ToolBudgetDecision = Union[ToolBudgetAdmit, ToolBudgetWarn, ToolBudgetReject]


class ToolBudgetMiddleware:
    """Resolve the matching budget per call and admit / warn / reject.

    Constructed once per run with the org's :class:`ToolBudgetRecord`
    snapshot. The middleware is stateless — call accounting lives on
    the ledger so the same instance is safe under concurrent calls
    within a single run (the ledger is per-run-serialized by the
    handler).
    """

    def __init__(self, budgets: Sequence[ToolBudgetRecord]) -> None:
        self._budgets = tuple(budgets)

    def check_admit(
        self,
        *,
        ledger: "ToolCallLedger",
        tool_name: str,
        estimated_input_tokens: int = 0,
    ) -> ToolBudgetDecision:
        """Decide whether ``tool_name`` should be admitted.

        ``estimated_input_tokens`` is the caller's pre-execute count for
        the args blob. The middleware enforces both the per-call cap
        (this single call's tokens) and the per-run cap (sum of admitted
        calls' observed tokens + this estimate).
        """

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
        # Most-specific wins: exact (org, name) > (org, '*') > (None, name) > (None, '*').
        # The persistence port already filters by org, so the ranking
        # collapses to (tool_name match, org_id present).
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
