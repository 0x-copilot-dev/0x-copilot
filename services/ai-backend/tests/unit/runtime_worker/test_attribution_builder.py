"""Tests for ``_AttributionBuilder`` in the streaming executor (01b).

The builder converts a LangGraph chunk + orchestrator state + ledger
pop into a typed :class:`UsageAttributionContext`. These tests fake
the orchestrator surface with the minimum methods the builder calls,
plus a real :class:`ToolCallLedger`, so we can exercise every Purpose
classification path without standing up the full streaming loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.observability.attribution import Purpose
from runtime_api.schemas import RunRecord
from runtime_worker.streaming_executor import _AttributionBuilder
from runtime_worker.tool_call_ledger import ToolCallLedger


# ---------------------------------------------------------------------------
# Test doubles — minimal stand-ins for the orchestrator + processor surface
# the attribution builder reads from.
# ---------------------------------------------------------------------------


@dataclass
class _FakeUpdateProcessor:
    """Stub for ``StreamUpdateProcessor`` — answers the two queries
    the attribution builder issues."""

    subagent_call_id_by_subgraph: dict[tuple[str, str], str]
    subagent_id_by_subgraph: dict[tuple[str, str], str]

    def subagent_call_id_for_subgraph(
        self, *, run_id: str, subgraph_task_id: str | None
    ) -> str | None:
        if subgraph_task_id is None:
            return None
        return self.subagent_call_id_by_subgraph.get((run_id, subgraph_task_id))

    def subagent_id_for_subgraph(
        self, *, run_id: str, subgraph_task_id: str | None
    ) -> str | None:
        if subgraph_task_id is None:
            return None
        return self.subagent_id_by_subgraph.get((run_id, subgraph_task_id))


@dataclass
class _FakeMessageProcessor:
    """Stub for ``StreamMessageProcessor`` — only exposes the ledger
    accessor the builder needs."""

    ledger: ToolCallLedger

    def ledger_for_run(self, run_id: str) -> ToolCallLedger:
        return self.ledger


@dataclass
class _FakeOrchestrator:
    """Stub for ``StreamOrchestrator`` — composes the processor stubs."""

    update_processor: _FakeUpdateProcessor
    message_processor: _FakeMessageProcessor


class _ChunkWithToolCalls:
    """Fake AIMessageChunk whose ``tool_calls`` attribute is truthy."""

    def __init__(self) -> None:
        self.tool_calls = [{"id": "call_x", "name": "search"}]


class _ChunkWithoutToolCalls:
    """Fake AIMessageChunk with empty tool_calls."""

    def __init__(self) -> None:
        self.tool_calls: list[Any] = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_1",
        org_id="org_a",
        user_id="user_1",
        conversation_id="conv_1",
        user_message_id="msg_user_1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        trace_id="trace_1",
        runtime_context=AgentRuntimeContext(
            org_id="org_a",
            user_id="user_1",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_1",
            trace_id="trace_1",
        ),
        started_at=datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc),
    )


def _orchestrator(
    *,
    ledger: ToolCallLedger,
    subagent_call_ids: dict[tuple[str, str], str] | None = None,
    subagent_ids: dict[tuple[str, str], str] | None = None,
) -> _FakeOrchestrator:
    return _FakeOrchestrator(
        update_processor=_FakeUpdateProcessor(
            subagent_call_id_by_subgraph=subagent_call_ids or {},
            subagent_id_by_subgraph=subagent_ids or {},
        ),
        message_processor=_FakeMessageProcessor(ledger=ledger),
    )


# ---------------------------------------------------------------------------
# Tests — each Purpose path
# ---------------------------------------------------------------------------


class TestPurposeMain:
    def test_orchestrator_planning_no_tools_no_input_yields_main(self) -> None:
        ledger = ToolCallLedger(run_id="run_1")
        builder = _AttributionBuilder(
            run=_run_record(), orchestrator=_orchestrator(ledger=ledger)
        )
        chunk = {
            "type": "messages",
            "ns": (),
            "data": (_ChunkWithoutToolCalls(), {}),
        }
        ctx = builder.build_for_chunk(chunk)
        assert ctx.purpose == Purpose.MAIN
        assert ctx.task_id is None
        assert ctx.subagent_slug is None
        assert ctx.originating_tool_call_id is None


class TestPurposeToolPlanning:
    def test_no_input_tool_with_tool_calls_in_output(self) -> None:
        ledger = ToolCallLedger(run_id="run_1")
        builder = _AttributionBuilder(
            run=_run_record(), orchestrator=_orchestrator(ledger=ledger)
        )
        chunk = {
            "type": "messages",
            "ns": (),
            "data": (_ChunkWithToolCalls(), {}),
        }
        ctx = builder.build_for_chunk(chunk)
        assert ctx.purpose == Purpose.TOOL_PLANNING
        assert ctx.originating_tool_call_id is None


class TestPurposeToolInterpretation:
    def test_pending_tool_settles_input_consumed_yields_interpretation(self) -> None:
        ledger = ToolCallLedger(run_id="run_1")
        ledger.started("call_jira", tool_name="jira_search")
        ledger.observed_settled("call_jira")
        builder = _AttributionBuilder(
            run=_run_record(), orchestrator=_orchestrator(ledger=ledger)
        )
        chunk = {
            "type": "messages",
            "ns": (),
            "data": (_ChunkWithoutToolCalls(), {}),
        }
        ctx = builder.build_for_chunk(chunk)
        assert ctx.purpose == Purpose.TOOL_INTERPRETATION
        assert ctx.originating_tool_call_id == "call_jira"
        assert ctx.originating_tool_name == "jira_search"

    def test_pending_tool_consumed_only_once(self) -> None:
        """Second LLM emit in the same scope without an intervening
        tool result should fall through to MAIN (the tool was already
        attributed to the first emit)."""

        ledger = ToolCallLedger(run_id="run_1")
        ledger.started("call_jira", tool_name="jira_search")
        ledger.observed_settled("call_jira")
        builder = _AttributionBuilder(
            run=_run_record(), orchestrator=_orchestrator(ledger=ledger)
        )
        chunk = {
            "type": "messages",
            "ns": (),
            "data": (_ChunkWithoutToolCalls(), {}),
        }
        first = builder.build_for_chunk(chunk)
        second = builder.build_for_chunk(chunk)
        assert first.purpose == Purpose.TOOL_INTERPRETATION
        assert second.purpose == Purpose.MAIN


class TestPurposeSubagentWork:
    def test_subagent_chunk_yields_subagent_work(self) -> None:
        ledger = ToolCallLedger(run_id="run_1")
        builder = _AttributionBuilder(
            run=_run_record(),
            orchestrator=_orchestrator(
                ledger=ledger,
                subagent_call_ids={("run_1", "subgraph_A"): "call_A"},
                subagent_ids={("run_1", "subgraph_A"): "researcher"},
            ),
        )
        chunk = {
            "type": "messages",
            "ns": ("tools:subgraph_A",),
            "data": (_ChunkWithoutToolCalls(), {"supervisor_task_call_id": "call_A"}),
        }
        ctx = builder.build_for_chunk(chunk)
        assert ctx.purpose == Purpose.SUBAGENT_WORK
        assert ctx.task_id == "call_A"
        assert ctx.subagent_slug == "researcher"

    def test_subagent_chunk_falls_back_to_orchestrator_mapping(self) -> None:
        """When the chunk metadata doesn't carry supervisor_task_call_id
        (updates-mode chunks), the builder reads from the orchestrator's
        subgraph mapping."""

        ledger = ToolCallLedger(run_id="run_1")
        builder = _AttributionBuilder(
            run=_run_record(),
            orchestrator=_orchestrator(
                ledger=ledger,
                subagent_call_ids={("run_1", "subgraph_A"): "call_A"},
                subagent_ids={("run_1", "subgraph_A"): "researcher"},
            ),
        )
        chunk = {
            "type": "updates",
            "ns": ("tools:subgraph_A",),
            "data": _ChunkWithoutToolCalls(),
            # No supervisor_task_call_id on top-level metadata.
        }
        ctx = builder.build_for_chunk(chunk)
        assert ctx.purpose == Purpose.SUBAGENT_WORK
        assert ctx.task_id == "call_A"
        assert ctx.subagent_slug == "researcher"


class TestParallelSubagentScoping:
    """The deterministic fix for D3: two parallel subagents do NOT
    cross-attribute. Each subagent's chunks resolve to its own
    task_id + subagent_slug via the namespace mapping; each subagent's
    tool result pops only that subagent's pending entry."""

    def test_two_parallel_subagents_distinct_attribution(self) -> None:
        ledger = ToolCallLedger(run_id="run_1")
        # Each subagent has its own tool result in flight.
        ledger.started(
            "call_a_tool",
            tool_name="search_a",
            parent_task_id="call_A",
            subagent_id="researcher",
        )
        ledger.started(
            "call_b_tool",
            tool_name="search_b",
            parent_task_id="call_B",
            subagent_id="writer",
        )
        ledger.observed_settled("call_a_tool")
        ledger.observed_settled("call_b_tool")

        builder = _AttributionBuilder(
            run=_run_record(),
            orchestrator=_orchestrator(
                ledger=ledger,
                subagent_call_ids={
                    ("run_1", "sub_A"): "call_A",
                    ("run_1", "sub_B"): "call_B",
                },
                subagent_ids={
                    ("run_1", "sub_A"): "researcher",
                    ("run_1", "sub_B"): "writer",
                },
            ),
        )
        chunk_a = {
            "type": "messages",
            "ns": ("tools:sub_A",),
            "data": (_ChunkWithoutToolCalls(), {"supervisor_task_call_id": "call_A"}),
        }
        chunk_b = {
            "type": "messages",
            "ns": ("tools:sub_B",),
            "data": (_ChunkWithoutToolCalls(), {"supervisor_task_call_id": "call_B"}),
        }

        ctx_a = builder.build_for_chunk(chunk_a)
        ctx_b = builder.build_for_chunk(chunk_b)

        # Subagent A's context references only A's task + tool.
        assert ctx_a.task_id == "call_A"
        assert ctx_a.subagent_slug == "researcher"
        assert ctx_a.originating_tool_call_id == "call_a_tool"
        assert ctx_a.originating_tool_name == "search_a"
        # Sub-PRD 01b §6.2 precedence: subagent wins over interpretation,
        # so even though A interpreted a tool result, the row's purpose
        # is SUBAGENT_WORK (cross-subagent tool-cost analysis reads from
        # subagent_id, not purpose).
        assert ctx_a.purpose == Purpose.SUBAGENT_WORK

        # Subagent B's context references only B's task + tool.
        assert ctx_b.task_id == "call_B"
        assert ctx_b.subagent_slug == "writer"
        assert ctx_b.originating_tool_call_id == "call_b_tool"
        assert ctx_b.originating_tool_name == "search_b"
        assert ctx_b.purpose == Purpose.SUBAGENT_WORK
