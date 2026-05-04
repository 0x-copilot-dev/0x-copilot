"""B8 — pure middleware decisions: most-specific wins, hard / soft, ledger interaction."""

from __future__ import annotations

from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetAdmit,
    ToolBudgetMiddleware,
    ToolBudgetReject,
    ToolBudgetWarn,
)
from agent_runtime.execution.tool_outcomes import ToolErrorCode, ToolOutcome
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_worker.tool_call_ledger import ToolCallLedger


def _budget(
    *,
    org_id: str | None,
    tool_name: str,
    max_calls_per_run: int = 6,
    max_input_tokens_per_call: int | None = None,
    max_input_tokens_per_run: int | None = None,
    enforcement: ToolBudgetEnforcement = ToolBudgetEnforcement.HARD,
) -> ToolBudgetRecord:
    return ToolBudgetRecord(
        org_id=org_id,
        tool_name=tool_name,
        max_calls_per_run=max_calls_per_run,
        max_input_tokens_per_call=max_input_tokens_per_call,
        max_input_tokens_per_run=max_input_tokens_per_run,
        enforcement=enforcement,
    )


def _ledger_with(*, tool_name: str, calls: int) -> ToolCallLedger:
    ledger = ToolCallLedger(run_id="run-1")
    for index in range(calls):
        ledger.started(call_id=f"call-{index}", tool_name=tool_name)
    return ledger


class TestAdmitAndReject:
    def test_admits_under_cap(self) -> None:
        middleware = ToolBudgetMiddleware([_budget(org_id=None, tool_name="*")])
        ledger = _ledger_with(tool_name="web_search", calls=2)
        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")
        assert isinstance(decision, ToolBudgetAdmit)

    def test_rejects_at_cap_under_hard_enforcement(self) -> None:
        middleware = ToolBudgetMiddleware(
            [_budget(org_id=None, tool_name="*", max_calls_per_run=6)]
        )
        ledger = _ledger_with(tool_name="web_search", calls=6)
        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")
        assert isinstance(decision, ToolBudgetReject)
        assert decision.outcome is ToolOutcome.REJECTED
        assert decision.error_code is ToolErrorCode.TOOL_BUDGET_EXCEEDED
        assert decision.kind == "calls"
        assert decision.limit == 6
        assert "exceeded" in decision.safe_message

    def test_warns_under_soft_enforcement(self) -> None:
        middleware = ToolBudgetMiddleware(
            [
                _budget(
                    org_id=None,
                    tool_name="*",
                    max_calls_per_run=2,
                    enforcement=ToolBudgetEnforcement.SOFT,
                )
            ]
        )
        ledger = _ledger_with(tool_name="web_search", calls=2)
        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")
        assert isinstance(decision, ToolBudgetWarn)


class TestMostSpecificWins:
    def test_per_org_per_tool_overrides_global(self) -> None:
        # Global allows 6, per-org-per-tool allows 3.
        middleware = ToolBudgetMiddleware(
            [
                _budget(org_id=None, tool_name="*", max_calls_per_run=6),
                _budget(org_id="org_a", tool_name="web_search", max_calls_per_run=3),
            ]
        )
        ledger = _ledger_with(tool_name="web_search", calls=3)
        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")
        assert isinstance(decision, ToolBudgetReject)
        assert decision.limit == 3

    def test_no_match_admits(self) -> None:
        middleware = ToolBudgetMiddleware([])
        ledger = _ledger_with(tool_name="web_search", calls=100)
        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")
        assert isinstance(decision, ToolBudgetAdmit)


class TestInputTokenCaps:
    def test_per_call_cap_rejects_oversized(self) -> None:
        middleware = ToolBudgetMiddleware(
            [
                _budget(
                    org_id=None,
                    tool_name="*",
                    max_calls_per_run=10,
                    max_input_tokens_per_call=100,
                )
            ]
        )
        ledger = ToolCallLedger(run_id="run-1")
        decision = middleware.check_admit(
            ledger=ledger, tool_name="web_search", estimated_input_tokens=200
        )
        assert isinstance(decision, ToolBudgetReject)
        assert decision.kind == "input_tokens_per_call"

    def test_per_run_cap_rejects_when_cumulative_exceeds(self) -> None:
        middleware = ToolBudgetMiddleware(
            [
                _budget(
                    org_id=None,
                    tool_name="*",
                    max_calls_per_run=10,
                    max_input_tokens_per_run=300,
                )
            ]
        )
        ledger = ToolCallLedger(run_id="run-1")
        # Two prior admitted calls with 100 + 150 input tokens = 250.
        for index, tokens in enumerate([100, 150]):
            call_id = f"prev-{index}"
            ledger.started(call_id=call_id, tool_name="web_search")
            ledger.record_input_tokens(call_id, tokens)
        # New call of 60 tokens → 310 > 300 cap.
        decision = middleware.check_admit(
            ledger=ledger, tool_name="web_search", estimated_input_tokens=60
        )
        assert isinstance(decision, ToolBudgetReject)
        assert decision.kind == "input_tokens_per_run"


class TestLedgerHelpers:
    def test_charged_calls_excludes_rejected(self) -> None:
        ledger = ToolCallLedger(run_id="run-1")
        ledger.started(call_id="c1", tool_name="web_search")
        ledger.started(call_id="c2", tool_name="web_search")
        ledger.mark_rejected("c2")
        assert ledger.charged_calls("web_search") == 1

    def test_total_input_tokens_excludes_rejected(self) -> None:
        ledger = ToolCallLedger(run_id="run-1")
        ledger.started(call_id="c1", tool_name="web_search")
        ledger.record_input_tokens("c1", 100)
        ledger.started(call_id="c2", tool_name="web_search")
        ledger.record_input_tokens("c2", 200)
        ledger.mark_rejected("c2")
        assert ledger.total_input_tokens("web_search") == 100
