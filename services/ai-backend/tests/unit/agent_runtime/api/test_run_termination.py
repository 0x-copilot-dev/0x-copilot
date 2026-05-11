"""Tests for :class:`RunTerminationCoordinator`.

Covers: reconciliation drains the ledger; missing terminal events are
synthesized in started-order; idempotence; per-entry failures do not
block siblings; happy-path no-op when the ledger is already empty.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# `RuntimeEventProducer` may spawn background presentation enrichment that
# constructs an OpenAI client; placeholder keeps tests hermetic.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-run-termination")

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.observability.lifecycle_ledger import (
    LifecycleKind,
    OpenLifecycleEntry,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
)


def _run_record(run_id: str = "run_term_test") -> RunRecord:
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
        started_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
    )


async def _seeded_producer_and_run() -> tuple[
    RuntimeEventProducer, RunRecord, InMemoryRuntimeApiStore
]:
    store = InMemoryRuntimeApiStore()
    run = _run_record()
    store.runs[run.run_id] = run
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    return producer, run, store


def _entry(
    kind: LifecycleKind,
    entity_id: str,
    payload_snapshot: dict | None = None,
) -> OpenLifecycleEntry:
    return OpenLifecycleEntry(
        kind=kind,
        entity_id=entity_id,
        parent_task_id=None,
        subagent_id=None,
        started_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
        payload_snapshot=payload_snapshot or {},
    )


def _events_of_type(
    store: InMemoryRuntimeApiStore,
    run_id: str,
    event_type: RuntimeApiEventType,
) -> list:
    return [
        ev for ev in store.events_by_run.get(run_id, []) if ev.event_type is event_type
    ]


class TestReconcilesOpenLifecycles:
    async def test_open_subagent_gets_synthesized_completed_event(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        await producer.lifecycle_ledger.open(
            _entry(
                LifecycleKind.SUBAGENT,
                "task_xyz",
                {"task_id": "task_xyz", "subagent_name": "general-purpose"},
            )
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.TOOL_FATAL_ERROR,
        )
        synthesized = _events_of_type(
            store, run.run_id, RuntimeApiEventType.SUBAGENT_COMPLETED
        )
        assert len(synthesized) == 1
        payload = synthesized[0].payload
        assert payload["status"] == "failed"
        assert payload["synthesized"] is True
        assert payload["reason"] == TerminationReason.TOOL_FATAL_ERROR.value
        assert payload["task_id"] == "task_xyz"
        assert payload["subagent_name"] == "general-purpose"

    async def test_open_tool_call_gets_synthesized_completed_event(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        await producer.lifecycle_ledger.open(
            _entry(
                LifecycleKind.TOOL_CALL,
                "call_abc",
                {"call_id": "call_abc", "tool_name": "web_search"},
            )
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
        )
        synthesized = _events_of_type(
            store, run.run_id, RuntimeApiEventType.TOOL_CALL_COMPLETED
        )
        assert len(synthesized) == 1
        assert synthesized[0].payload["call_id"] == "call_abc"
        assert synthesized[0].payload["tool_name"] == "web_search"

    async def test_multiple_open_entries_all_get_terminal_events(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_1", {"task_id": "task_1"})
        )
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_2", {"task_id": "task_2"})
        )
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.TOOL_CALL, "call_a", {"call_id": "call_a"})
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
        )
        subagent_done = _events_of_type(
            store, run.run_id, RuntimeApiEventType.SUBAGENT_COMPLETED
        )
        tool_done = _events_of_type(
            store, run.run_id, RuntimeApiEventType.TOOL_CALL_COMPLETED
        )
        assert {e.payload["task_id"] for e in subagent_done} == {"task_1", "task_2"}
        assert [e.payload["call_id"] for e in tool_done] == ["call_a"]

    async def test_synthesized_events_close_the_ledger(self) -> None:
        producer, run, _ = await _seeded_producer_and_run()
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_z", {"task_id": "task_z"})
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
        )
        # The synthesized SUBAGENT_COMPLETED flows through the producer's
        # own ledger-tracking, so the ledger should be empty afterwards.
        assert await producer.lifecycle_ledger.open_count() == 0


class TestTerminalRunEvent:
    async def test_emits_run_failed_with_reason_and_cause(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.TOOL_FATAL_ERROR,
            cause=ValueError("budget gone"),
            summary="Run failed",
        )
        failed = _events_of_type(store, run.run_id, RuntimeApiEventType.RUN_FAILED)
        assert len(failed) == 1
        assert failed[0].payload["reason"] == TerminationReason.TOOL_FATAL_ERROR.value
        assert failed[0].payload["error_class"] == "ValueError"

    async def test_emits_run_completed_on_normal_completion(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
        )
        completed = _events_of_type(
            store, run.run_id, RuntimeApiEventType.RUN_COMPLETED
        )
        assert len(completed) == 1
        assert (
            completed[0].payload["reason"] == TerminationReason.NORMAL_COMPLETION.value
        )

    async def test_emits_run_cancelled_on_cancellation(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.CANCELLED,
            reason=TerminationReason.CANCELLED,
        )
        assert (
            len(_events_of_type(store, run.run_id, RuntimeApiEventType.RUN_CANCELLED))
            == 1
        )

    async def test_empty_ledger_is_a_no_op_for_reconciliation(self) -> None:
        producer, run, store = await _seeded_producer_and_run()
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
        )
        # Only the run terminal event; nothing synthesized.
        assert (
            _events_of_type(store, run.run_id, RuntimeApiEventType.SUBAGENT_COMPLETED)
            == []
        )
        assert (
            len(_events_of_type(store, run.run_id, RuntimeApiEventType.RUN_COMPLETED))
            == 1
        )


class TestResilientToPerEntryFailure:
    async def test_one_failing_synth_does_not_block_siblings(self) -> None:
        """A producer that raises on one entity_id must not stop reconciliation
        of the rest of the ledger."""

        producer, run, store = await _seeded_producer_and_run()
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_ok_1", {"task_id": "task_ok_1"})
        )
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_boom", {"task_id": "task_boom"})
        )
        await producer.lifecycle_ledger.open(
            _entry(LifecycleKind.SUBAGENT, "task_ok_2", {"task_id": "task_ok_2"})
        )

        original = producer.append_api_event
        boom_seen: list[str] = []

        async def flaky_append(**kwargs):  # type: ignore[no-untyped-def]
            payload = kwargs.get("payload") or {}
            if payload.get("task_id") == "task_boom":
                boom_seen.append("raised")
                raise RuntimeError("write_blocked")
            return await original(**kwargs)

        producer.append_api_event = flaky_append  # type: ignore[assignment]
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
        )
        assert boom_seen == ["raised"]
        completed = _events_of_type(
            store, run.run_id, RuntimeApiEventType.SUBAGENT_COMPLETED
        )
        assert {e.payload["task_id"] for e in completed} == {
            "task_ok_1",
            "task_ok_2",
        }
