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


# Sub-PRD 01b — pending-attribution carry tests ---------------------------


def test_observed_settled_stamps_settled_at_for_attribution() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    assert ledger._entries["call_1"].settled_at is None
    ledger.observed_settled("call_1")
    assert ledger._entries["call_1"].settled_at is not None


def test_pop_pending_attribution_returns_settled_entry() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    ledger.observed_settled("call_1")
    popped = ledger.pop_pending_attribution(scope_key=None)
    assert popped is not None
    assert popped.call_id == "call_1"
    assert popped.tool_name == "web_search"


def test_pop_pending_attribution_returns_none_when_unsettled() -> None:
    """An in-flight tool call hasn't produced a tool_result yet — no
    pending attribution to pop."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    # No observed_settled() call.
    assert ledger.pop_pending_attribution(scope_key=None) is None


def test_pop_pending_attribution_consumes_entry() -> None:
    """An entry can only attribute one LLM call; a second pop returns None."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_1", tool_name="web_search")
    ledger.observed_settled("call_1")
    first = ledger.pop_pending_attribution(scope_key=None)
    second = ledger.pop_pending_attribution(scope_key=None)
    assert first is not None
    assert second is None


def test_pop_pending_attribution_returns_latest_settled() -> None:
    """Multiple settled tools in same scope → most-recently-settled
    represents the LLM call's primary input."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_A", tool_name="search_a")
    ledger.started("call_B", tool_name="search_b")
    ledger.observed_settled("call_A")
    # Bump call_B's settled_at past call_A's by stamping it after.
    ledger.observed_settled("call_B")
    popped = ledger.pop_pending_attribution(scope_key=None)
    assert popped is not None
    assert popped.call_id == "call_B"


def test_pop_pending_attribution_marks_all_eligible_consumed() -> None:
    """When multiple tools settle before a single LLM emit (parallel
    fan-out), all of them are consumed — the LLM interprets the batch
    but we record one representative. A subsequent emit doesn't
    double-dip on the leftovers."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started("call_A", tool_name="search_a")
    ledger.started("call_B", tool_name="search_b")
    ledger.observed_settled("call_A")
    ledger.observed_settled("call_B")
    first = ledger.pop_pending_attribution(scope_key=None)
    second = ledger.pop_pending_attribution(scope_key=None)
    assert first is not None
    assert second is None


def test_pop_pending_attribution_is_scope_aware() -> None:
    """A parallel subagent's tool result must not stamp an
    orchestrator-scope LLM call."""

    ledger = ToolCallLedger(run_id="run_1")
    ledger.started(
        "call_orch", tool_name="orch_tool", parent_task_id=None, subagent_id=None
    )
    ledger.started(
        "call_sub_a",
        tool_name="sub_a_tool",
        parent_task_id="task_A",
        subagent_id="researcher",
    )
    ledger.started(
        "call_sub_b",
        tool_name="sub_b_tool",
        parent_task_id="task_B",
        subagent_id="writer",
    )
    ledger.observed_settled("call_orch")
    ledger.observed_settled("call_sub_a")
    ledger.observed_settled("call_sub_b")

    # Orchestrator pops only the orchestrator entry.
    orch_pop = ledger.pop_pending_attribution(scope_key=None)
    assert orch_pop is not None and orch_pop.call_id == "call_orch"

    # researcher pops only researcher's entry.
    researcher_pop = ledger.pop_pending_attribution(scope_key="researcher")
    assert researcher_pop is not None and researcher_pop.call_id == "call_sub_a"

    # writer pops only writer's entry.
    writer_pop = ledger.pop_pending_attribution(scope_key="writer")
    assert writer_pop is not None and writer_pop.call_id == "call_sub_b"

    # All popped; nothing left.
    assert ledger.pop_pending_attribution(scope_key=None) is None
    assert ledger.pop_pending_attribution(scope_key="researcher") is None
    assert ledger.pop_pending_attribution(scope_key="writer") is None


def test_pop_pending_attribution_with_empty_ledger() -> None:
    ledger = ToolCallLedger(run_id="run_1")
    assert ledger.pop_pending_attribution(scope_key=None) is None
    assert ledger.pop_pending_attribution(scope_key="researcher") is None
