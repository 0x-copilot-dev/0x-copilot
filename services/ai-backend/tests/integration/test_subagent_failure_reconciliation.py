"""Repro of PRD §1.1 — open subagent must not stay "running" after run_failed.

Setup mirrors the failing real-world run: a parent run starts; the
producer opens lifecycle entries for two in-flight subagents (one of
them via the supervisor's ``task`` tool); a tool exception path
terminates the run via :class:`RunTerminationCoordinator`. Assertion:
every started subagent receives a terminal event before / when the run
event lands. Without reconciliation the FE would show a stuck subagent.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# Producer may construct an OpenAI client for presentation enrichment;
# placeholder keeps tests hermetic.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-subagent-recon")

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEventSource,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
)


def _run_record(run_id: str = "run_reconcile_test") -> RunRecord:
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


async def _seeded() -> tuple[RuntimeEventProducer, RunRecord, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    run = _run_record()
    store.runs[run.run_id] = run
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    return producer, run, store


class TestSubagentLeakIsClosed:
    async def test_in_flight_subagent_gets_terminal_event_on_run_failure(
        self,
    ) -> None:
        producer, run, store = await _seeded()
        # Mirror the real-world flow: supervisor dispatches three
        # subagents. Two complete normally; the third's inner tool throws
        # before the supervisor's task tool returns its result, so its
        # SUBAGENT_COMPLETED is never naturally emitted.
        for task_id in ("task_alpha", "task_beta", "task_gamma"):
            await producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                payload={
                    "task_id": task_id,
                    "subagent_name": "general-purpose",
                },
            )
        # Two subagents complete naturally.
        for task_id in ("task_alpha", "task_beta"):
            await producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                payload={"task_id": task_id, "status": "completed"},
            )

        # The run fails before subagent #3 ever closes — exactly the
        # condition in run 8475dbace42f4e34a2d2fb1555a542e0.
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
            cause=RuntimeError("inner web_search exploded"),
        )

        events = store.events_by_run.get(run.run_id, [])
        starts = [
            e for e in events if e.event_type is RuntimeApiEventType.SUBAGENT_STARTED
        ]
        completes = [
            e for e in events if e.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
        ]
        run_failed = [
            e for e in events if e.event_type is RuntimeApiEventType.RUN_FAILED
        ]

        # The headline assertion: 3 started, 3 completed.
        assert len(starts) == 3
        assert len(completes) == 3, (
            "Subagent leak — without reconciliation, only 2 SUBAGENT_COMPLETED "
            "events would land for 3 SUBAGENT_STARTED. The FE would show one "
            "subagent as still running."
        )
        # The synthesized completion identifies itself + the failure status.
        synthesized = [e for e in completes if e.payload.get("synthesized")]
        assert len(synthesized) == 1
        assert synthesized[0].payload["task_id"] == "task_gamma"
        assert synthesized[0].payload["status"] == "failed"

        # Run terminal event lands once with the right reason.
        assert len(run_failed) == 1
        assert (
            run_failed[0].payload["reason"] == TerminationReason.EXECUTION_ERROR.value
        )
        assert run_failed[0].payload["error_class"] == "RuntimeError"

        # Ledger is empty after termination.
        assert await producer.lifecycle_ledger.open_count() == 0

    async def test_inflight_tool_call_also_reconciled(self) -> None:
        """Defense in depth: an open TOOL_CALL_STARTED whose handler
        hasn't fired its TOOL_CALL_COMPLETED also gets a terminal event."""

        producer, run, store = await _seeded()
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload={"call_id": "call_xyz", "tool_name": "web_search"},
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.FAILED,
            reason=TerminationReason.EXECUTION_ERROR,
        )
        events = store.events_by_run.get(run.run_id, [])
        completed = [
            e for e in events if e.event_type is RuntimeApiEventType.TOOL_CALL_COMPLETED
        ]
        assert len(completed) == 1
        assert completed[0].payload["call_id"] == "call_xyz"
        assert completed[0].payload["status"] == "failed"

    async def test_normal_completion_synthesizes_nothing(self) -> None:
        """Green path: every started entity closed normally, ledger empty,
        coordinator.terminate emits only the run terminal event."""

        producer, run, store = await _seeded()
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            payload={"task_id": "task_clean"},
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            payload={"task_id": "task_clean", "status": "completed"},
        )
        coordinator = RunTerminationCoordinator(event_producer=producer)
        await coordinator.terminate(
            run=run,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
        )
        events = store.events_by_run.get(run.run_id, [])
        synthesized = [
            e
            for e in events
            if e.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
            and e.payload.get("synthesized")
        ]
        assert synthesized == []
