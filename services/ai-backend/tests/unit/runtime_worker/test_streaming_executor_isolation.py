"""PR 3.2.5 Phase 2 — sibling subagents keep running when one is interrupted.

Locks in the architectural fix that replaced the executor's early‑return
on `APPROVAL_REQUESTED` / `MCP_AUTH_REQUIRED` with continued draining of
the supervisor's `astream`. Before: a single subagent's interrupt
cancelled the iterator and every parallel branch with it. After: the
flag is set, sibling branches keep emitting events until they complete,
the run transitions to `WAITING_FOR_APPROVAL` only when all branches
are either done or paused.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone

# `RuntimeEventProducer` may spawn background presentation enrichment for
# LLM-eligible events; that path constructs an OpenAI client and would
# fail without an api key in the environment. Set a placeholder so tests
# can run hermetically — the background task is awaited later (or just
# logs at debug level on failure) and never makes a network call.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-isolation")

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.async_runtime_api_store import (
    AsyncInMemoryRuntimeApiStore,
)
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.streaming_executor import StreamingExecutor


def _run_record(run_id: str = "run_phase2") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        org_id="org_a",
        user_id="user_1",
        conversation_id="conv_1",
        user_message_id="msg_1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        trace_id=f"trace_{run_id}",
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
            run_id=run_id,
            trace_id=f"trace_{run_id}",
        ),
        started_at=datetime(2026, 5, 7, 10, 0, tzinfo=timezone.utc),
    )


async def _empty_iter() -> AsyncIterator[object]:
    if False:
        yield None  # pragma: no cover


def _persist_run(store: InMemoryRuntimeApiStore, run_id: str) -> RunRecord:
    """Seed the store with a queued run + queue entry so RuntimeEventProducer
    can append events keyed to it."""
    record = _run_record(run_id)
    store.runs[run_id] = record
    return record


async def _fake_stream_one_interrupt_then_sibling_completion() -> AsyncIterator[object]:
    """Synthetic stream where:

      seq 1: SUBAGENT_STARTED for call_A (custom api_event)
      seq 2: SUBAGENT_STARTED for call_B (custom api_event)
      seq 3: APPROVAL_REQUESTED inside call_A's subgraph (would have early-returned)
      seq 4: SUBAGENT_COMPLETED for call_B (proves the sibling kept emitting)

    All chunks come from the supervisor's astream, formatted as LangGraph v2
    stream parts (dict with `type` / `ns` / `data`).
    """
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_A_uuid",),
        "data": {
            "api_event_type": "subagent_started",
            "task_id": "call_A",
            "subagent_name": "general-purpose",
            "status": "started",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_B_uuid",),
        "data": {
            "api_event_type": "subagent_started",
            "task_id": "call_B",
            "subagent_name": "general-purpose",
            "status": "started",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_A_uuid",),
        "data": {
            "api_event_type": "approval_requested",
            "approval_id": "appr_1",
            "tool_name": "post_to_slack",
            "summary": "Post the report draft to #launch.",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_B_uuid",),
        "data": {
            "api_event_type": "subagent_completed",
            "task_id": "call_B",
            "subagent_name": "general-purpose",
            "status": "completed",
            "summary": "Sibling B finished its work.",
        },
    }


def test_interrupt_does_not_cancel_sibling_subagent_completion() -> None:
    """Phase 2 acceptance: with an APPROVAL_REQUESTED arriving while sibling
    subagents are still in flight, the executor keeps draining the stream so
    siblings' SUBAGENT_COMPLETED events are observed and persisted."""

    sync_store = InMemoryRuntimeApiStore()
    run = _persist_run(sync_store, "run_phase2_iso")
    store = AsyncInMemoryRuntimeApiStore(sync_store)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    orchestrator = StreamOrchestrator(event_producer=producer)
    metrics = AssistantRunMetrics(started_at=datetime.now(timezone.utc))

    result = asyncio.run(
        StreamingExecutor.run(
            stream=_fake_stream_one_interrupt_then_sibling_completion(),
            run=run,
            metrics=metrics,
            event_store=store,
            event_producer=producer,
            stream_event_mapper=orchestrator,
            track_subagents=True,
        )
    )

    # The interrupt event was seen, so the run will transition to
    # WAITING_FOR_APPROVAL via the run handler — but the executor itself
    # kept draining the stream.
    assert result.action_interrupted is True

    # The sibling's SUBAGENT_COMPLETED event was observed AFTER the
    # interrupt. Proves the executor did not early-return.
    persisted = sync_store.events_by_run["run_phase2_iso"]
    completed = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
        and event.task_id == "call_B"
    ]
    assert len(completed) == 1, [(e.event_type, e.task_id) for e in persisted]

    # And the sibling's summary made it into the executor's accumulated
    # subagent_summaries — the same channel used by `compose_final` to
    # synthesize a final answer when the supervisor itself doesn't.
    assert any("Sibling B finished" in summary for summary in result.subagent_summaries)

    # PR 3.2.5 Phase 3 — the interrupt that paused sub A also produced a
    # sibling `SUBAGENT_PAUSED` event whose `task_id` matches sub A's
    # supervisor call_id. The FE reducer reads this to flip
    # `SubagentEntry.status` to `paused` without inferring from the
    # absence of SUBAGENT_COMPLETED.
    paused = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_PAUSED
    ]
    assert len(paused) == 1, [(e.event_type, e.task_id) for e in persisted]
    paused_payload = dict(paused[0].payload)
    assert paused_payload["task_id"] == "subgraph_A_uuid"
    assert paused_payload["reason"] == "approval"

    # PR 3.2.5 Phase 3 — the approval record persisted alongside the
    # interrupt carries `parent_task_id` in its metadata. The approval
    # handler reads this on resolution to emit the paired
    # `SUBAGENT_RESUMED` event before invoking the LangGraph resumer.
    approval = sync_store.approval_requests["appr_1"]
    assert approval.metadata["parent_task_id"] == "subgraph_A_uuid"


async def _fake_stream_supervisor_only_interrupt() -> AsyncIterator[object]:
    """A run where the supervisor itself triggers the interrupt — no subagents
    in flight. Confirms the no-siblings case still terminates promptly
    (LangGraph's astream exits naturally when the only branch is paused)."""
    yield {
        "type": "custom",
        "ns": (),
        "data": {
            "api_event_type": "approval_requested",
            "approval_id": "appr_supervisor",
            "tool_name": "send_email",
            "summary": "Send the email blast.",
        },
    }


def test_supervisor_only_interrupt_terminates_promptly() -> None:
    """Phase 2 regression guard: a run with no parallel branches still
    flips `action_interrupted` and reaches the end of the stream without
    deadlocking. The fake stream simulates LangGraph's astream returning
    (no further chunks) immediately after a supervisor-level interrupt."""

    sync_store = InMemoryRuntimeApiStore()
    run = _persist_run(sync_store, "run_phase2_solo")
    store = AsyncInMemoryRuntimeApiStore(sync_store)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    orchestrator = StreamOrchestrator(event_producer=producer)
    metrics = AssistantRunMetrics(started_at=datetime.now(timezone.utc))

    result = asyncio.run(
        StreamingExecutor.run(
            stream=_fake_stream_supervisor_only_interrupt(),
            run=run,
            metrics=metrics,
            event_store=store,
            event_producer=producer,
            stream_event_mapper=orchestrator,
            track_subagents=True,
        )
    )

    assert result.action_interrupted is True
    # No siblings, so no completion summary.
    assert result.subagent_summaries == []


async def _fake_stream_ask_a_question_inside_subagent() -> AsyncIterator[object]:
    """A subagent fires an `ask_a_question` approval — the worker must emit
    `SUBAGENT_PAUSED` with `reason="ask_a_question"` (not generic `approval`)
    so the FE can render "Waiting for answer" copy on the paused row."""

    yield {
        "type": "custom",
        "ns": ("tools:subgraph_Q_uuid",),
        "data": {
            "api_event_type": "subagent_started",
            "task_id": "call_Q",
            "subagent_name": "general-purpose",
            "status": "started",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_Q_uuid",),
        "data": {
            "api_event_type": "approval_requested",
            "approval_id": "appr_q",
            "approval_kind": "ask_a_question",
            "summary": "Should we include the Q3 numbers?",
        },
    }


def test_ask_a_question_inside_subagent_emits_paused_with_ask_a_question_reason() -> (
    None
):
    sync_store = InMemoryRuntimeApiStore()
    run = _persist_run(sync_store, "run_phase3_aaq")
    store = AsyncInMemoryRuntimeApiStore(sync_store)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    orchestrator = StreamOrchestrator(event_producer=producer)
    metrics = AssistantRunMetrics(started_at=datetime.now(timezone.utc))

    asyncio.run(
        StreamingExecutor.run(
            stream=_fake_stream_ask_a_question_inside_subagent(),
            run=run,
            metrics=metrics,
            event_store=store,
            event_producer=producer,
            stream_event_mapper=orchestrator,
            track_subagents=True,
        )
    )

    persisted = sync_store.events_by_run["run_phase3_aaq"]
    paused = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_PAUSED
    ]
    assert len(paused) == 1
    payload = dict(paused[0].payload)
    assert payload["task_id"] == "subgraph_Q_uuid"
    # The discriminator: ask_a_question is its own reason; not "approval".
    assert payload["reason"] == "ask_a_question"


async def _fake_stream_mcp_auth_inside_subagent() -> AsyncIterator[object]:
    """A subagent fires an `mcp_auth_required` interrupt — separate path
    from APPROVAL_REQUESTED / ASK_A_QUESTION. Must emit `SUBAGENT_PAUSED`
    with `reason="mcp_auth"`."""

    yield {
        "type": "custom",
        "ns": ("tools:subgraph_M_uuid",),
        "data": {
            "api_event_type": "subagent_started",
            "task_id": "call_M",
            "subagent_name": "general-purpose",
            "status": "started",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_M_uuid",),
        "data": {
            "api_event_type": "mcp_auth_required",
            "approval_id": "appr_mcp",
            "server_name": "github",
            "summary": "Connect GitHub to continue.",
        },
    }


def test_mcp_auth_inside_subagent_emits_paused_with_mcp_auth_reason() -> None:
    sync_store = InMemoryRuntimeApiStore()
    run = _persist_run(sync_store, "run_phase3_mcp")
    store = AsyncInMemoryRuntimeApiStore(sync_store)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    orchestrator = StreamOrchestrator(event_producer=producer)
    metrics = AssistantRunMetrics(started_at=datetime.now(timezone.utc))

    asyncio.run(
        StreamingExecutor.run(
            stream=_fake_stream_mcp_auth_inside_subagent(),
            run=run,
            metrics=metrics,
            event_store=store,
            event_producer=producer,
            stream_event_mapper=orchestrator,
            track_subagents=True,
        )
    )

    persisted = sync_store.events_by_run["run_phase3_mcp"]
    paused = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_PAUSED
    ]
    assert len(paused) == 1
    payload = dict(paused[0].payload)
    assert payload["task_id"] == "subgraph_M_uuid"
    assert payload["reason"] == "mcp_auth"


async def _fake_stream_resume_then_immediate_pause() -> AsyncIterator[object]:
    """The resumed subagent immediately hits a second approval. Sequence:
    SUBAGENT_STARTED → APPROVAL_REQUESTED (#1) → (resume happens
    out-of-band via approval handler) → APPROVAL_REQUESTED (#2). Each
    interrupt should produce its own SUBAGENT_PAUSED event keyed to the
    subagent's task_id; the test confirms a chained pause cycle emits
    paired pauses without losing the second one to dedup or replay."""

    yield {
        "type": "custom",
        "ns": ("tools:subgraph_C_uuid",),
        "data": {
            "api_event_type": "subagent_started",
            "task_id": "call_C",
            "subagent_name": "general-purpose",
            "status": "started",
        },
    }
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_C_uuid",),
        "data": {
            "api_event_type": "approval_requested",
            "approval_id": "appr_first",
            "tool_name": "post_to_slack",
            "summary": "Post the report draft.",
        },
    }
    # In production the supervisor's astream pauses here; the approval
    # handler runs and emits SUBAGENT_RESUMED before reinjecting via
    # astream_runtime_resume. For this test we just continue the synthetic
    # stream — the second interrupt arrives from inside the same subgraph,
    # so the worker's chunk handler must emit a fresh SUBAGENT_PAUSED.
    yield {
        "type": "custom",
        "ns": ("tools:subgraph_C_uuid",),
        "data": {
            "api_event_type": "approval_requested",
            "approval_id": "appr_second",
            "tool_name": "post_to_slack",
            "summary": "Now post the redacted version.",
        },
    }


def test_chained_pause_cycle_emits_paired_paused_events_per_interrupt() -> None:
    """AC-equivalent of `test_resume_then_immediate_pause_emits_both_events`.
    The chunk-side worker is the only emit path for SUBAGENT_PAUSED; both
    interrupts in the chained sequence should produce their own paused
    event without dedup masking the second."""

    sync_store = InMemoryRuntimeApiStore()
    run = _persist_run(sync_store, "run_phase3_chain")
    store = AsyncInMemoryRuntimeApiStore(sync_store)
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    orchestrator = StreamOrchestrator(event_producer=producer)
    metrics = AssistantRunMetrics(started_at=datetime.now(timezone.utc))

    asyncio.run(
        StreamingExecutor.run(
            stream=_fake_stream_resume_then_immediate_pause(),
            run=run,
            metrics=metrics,
            event_store=store,
            event_producer=producer,
            stream_event_mapper=orchestrator,
            track_subagents=True,
        )
    )

    persisted = sync_store.events_by_run["run_phase3_chain"]
    paused = [
        event
        for event in persisted
        if event.event_type is RuntimeApiEventType.SUBAGENT_PAUSED
    ]
    # Both interrupts in the chained sequence emit their own paused event,
    # each keyed to the same supervisor call_id (the FE reducer's
    # ``onPaused`` is a no-op when the row is already paused, so duplicates
    # at the data layer are safe but not helpful).
    assert len(paused) == 2, [(e.event_type, e.task_id) for e in persisted]
    for event in paused:
        assert dict(event.payload)["task_id"] == "subgraph_C_uuid"
    # And every approval row carried `parent_task_id` on its metadata so
    # the resume handler can pair each one to its own SUBAGENT_RESUMED.
    assert (
        sync_store.approval_requests["appr_first"].metadata["parent_task_id"]
        == "subgraph_C_uuid"
    )
    assert (
        sync_store.approval_requests["appr_second"].metadata["parent_task_id"]
        == "subgraph_C_uuid"
    )
