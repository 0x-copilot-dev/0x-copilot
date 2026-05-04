"""Unit tests for the per-run tool call ledger."""

from __future__ import annotations

from runtime_worker.tool_call_ledger import ToolCallLedger


def test_started_records_an_unsettled_entry() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    ledger.started(
        "call_1",
        tool_name="web_search",
        parent_task_id="task_1",
        subagent_id="sub_1",
    )
    unsettled = ledger.unsettled()
    assert len(unsettled) == 1
    assert unsettled[0].call_id == "call_1"
    assert unsettled[0].tool_name == "web_search"
    assert unsettled[0].parent_task_id == "task_1"
    assert unsettled[0].subagent_id == "sub_1"
    assert unsettled[0].settled is False


def test_started_is_idempotent_on_repeat_call_id() -> None:
    """LangGraph occasionally re-emits a tool_call_started chunk for the
    same call_id (e.g. on resumption after approval). The ledger must not
    overwrite the original entry — that would reset started_at and corrupt
    duration calculations."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    first_started_at = ledger.unsettled()[0].started_at
    ledger.started("call_1", tool_name="web_search")
    assert ledger.unsettled()[0].started_at == first_started_at


def test_observed_settled_clears_an_entry_from_unsettled() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    ledger.observed_settled("call_1")
    assert ledger.unsettled() == []


def test_observed_settled_no_op_for_unknown_call_id() -> None:
    """Tolerate replays where a tool_result fires without a matching
    started — e.g. event-store replays into a fresh ledger."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.observed_settled("call_unknown")  # should not raise
    assert ledger.unsettled() == []


def test_unsettled_only_returns_in_flight_entries() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_done", tool_name="web_search")
    ledger.started("call_inflight_1", tool_name="fetch_doc")
    ledger.started("call_inflight_2", tool_name="grep")
    ledger.observed_settled("call_done")
    in_flight = {entry.call_id for entry in ledger.unsettled()}
    assert in_flight == {"call_inflight_1", "call_inflight_2"}


def test_has_entries_reports_started_calls_even_after_settlement() -> None:
    """Used by the handler to decide whether to bother reconciling — `has_entries`
    is True if any call was ever started, `unsettled` filters to in-flight only."""

    ledger = ToolCallLedger(run_id="run_1")
    assert ledger.has_entries() is False
    ledger.started("call_1", tool_name="web_search")
    assert ledger.has_entries() is True
    ledger.observed_settled("call_1")
    assert ledger.has_entries() is True
    assert ledger.unsettled() == []
