"""A tool call the RUN killed must not be recorded as a tool that threw.

This is the defect a live benchmark surfaced and then mis-read. Run
``2d30ca71ad2b4d8da58ee88deb9d4036`` of ``tools/harness-bench`` failed a
todo-driven task; its ledger showed four ``write_todos`` invocations, three
``completed`` and a fourth ``failed`` with ``safe_error_code=tool_exception``,
``result_summary={}`` and the message "The tool reported an error and didn't
return a result." That reads as one thing only: ``write_todos`` raised. It did
not. The run's own terminal event (``run_failed``, sequence 196) carries
``code=recursion_limit_exceeded`` — the graph was stopped at its step ceiling
while the fourth call was still in flight, and
:meth:`RuntimeRunHandler._reconcile_inflight_tool_calls` closed the orphan with
the catch-all ``tool_exception``.

So the ledger asserted something false about a tool, and a reader holding only
the ledger — which is exactly what the benchmark's scorer had — concluded a tool
bug where there was a step-limit failure. The fix is a typed code of its own:
``tool_run_failed`` means "the run ended for a reason of its own while this call
was open; the tool itself reported nothing".

**What the reproduction drives, stated exactly.** A real queued run, the real
worker, the real Deep Agents graph, with only the chat model faked
(``RUNTIME_FAKE_MODEL``) and scripted to call ``write_todos`` far more times
than the run allows. The run-killer here is the tool-budget guard's
surfaced-rejection hard cap rather than the step ceiling — reaching the step
ceiling first is not arrangeable in-process — but the mechanism under test is
identical and is the only thing these assertions touch: **the run raised while a
tool call was open, and that call's row must not blame the tool.**

The orphan is identified by ``result_summary == {}``. That is not a proxy: a
call the run killed produced no tool output at all, which is precisely what
distinguishes it from every tool that ran and reported something.
"""

from __future__ import annotations

import json

from agent_runtime.execution.tool_outcomes import ToolErrorCode
from agent_runtime.persistence.records import ToolInvocationRecord
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin


class RunDiesWithAToolCallOpenMixin(FakeModelRunMixin):
    """Drive a real run to a raising terminus with a tool call still in flight."""

    TOOL_NAME = "write_todos"

    #: The checklist the benchmark's model actually wrote, one row per step.
    TODOS = [
        {"content": "List three European capitals", "status": "completed"},
        {"content": "Identify the country of each capital", "status": "completed"},
        {"content": "Name one river in each country", "status": "completed"},
        {"content": "Produce final table of all three rows", "status": "in_progress"},
    ]

    #: More tool turns than any per-run allowance, so the run is guaranteed to
    #: be stopped mid-loop rather than finishing its script.
    SCRIPTED_TOOL_TURNS = "60"

    @classmethod
    def _script_endless_write_todos(cls, monkeypatch) -> None:
        """Point the deterministic fake at ``write_todos``, forever."""

        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", cls.SCRIPTED_TOOL_TURNS)
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", cls.TOOL_NAME)
        monkeypatch.setenv(
            "RUNTIME_FAKE_MODEL_TOOL_ARGS", json.dumps({"todos": cls.TODOS})
        )

    @classmethod
    async def _drive(cls, monkeypatch) -> tuple[str, InMemoryRuntimeApiStore]:
        """Run one scripted overrun to completion; return its id and the store."""

        cls._script_endless_write_todos(monkeypatch)
        store = InMemoryRuntimeApiStore()
        settings = cls._settings()
        run_id = await FakeModelRunMixin._enqueue_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        assert await worker.run_until_idle() == 1
        return run_id, store

    @staticmethod
    def _orphaned_invocations(
        store: InMemoryRuntimeApiStore, run_id: str
    ) -> list[ToolInvocationRecord]:
        """Ledger rows for calls that were open when the run died.

        A tool that ran and failed reports something; a call the run killed
        reports nothing, so an empty ``result_summary`` on a failed row is the
        run-level reconciler's own signature.
        """

        return [
            record
            for record in store.tool_invocations.values()
            if record.run_id == run_id
            and record.status.value == "failed"
            and not record.result_summary
        ]

    @staticmethod
    def _reconciled_tool_results(
        store: InMemoryRuntimeApiStore, run_id: str
    ) -> list[dict[str, object]]:
        """Synthetic terminal ``tool_result`` payloads: failed, and outputless."""

        return [
            event.payload
            for event in store.events_by_run[run_id]
            if event.event_type == "tool_result"
            and isinstance(event.payload, dict)
            and event.payload.get("status") == "failed"
            and "output" not in event.payload
        ]

    @staticmethod
    def _run_failure_code(store: InMemoryRuntimeApiStore, run_id: str) -> str:
        """The typed code the run itself recorded on ``run_failed``.

        Read rather than hardcoded on purpose: the assertion is that the tool
        row and the run event name the SAME cause, which stays true as the
        runtime's error taxonomy sharpens.
        """

        [failure] = [
            event
            for event in store.events_by_run[run_id]
            if event.event_type == "run_failed"
        ]
        assert isinstance(failure.payload, dict)
        code = failure.payload["code"]
        assert isinstance(code, str)
        return code


class TestInFlightToolCallAtRunFailure(RunDiesWithAToolCallOpenMixin):
    """The orphan's error code must name the run, not accuse the tool."""

    async def test_the_run_really_dies_with_a_call_still_open(
        self, monkeypatch
    ) -> None:
        # The premise of every assertion below. If the run ever finished its
        # scripted turns there would be no orphan and the rest of this class
        # would pass vacuously.
        run_id, store = await self._drive(monkeypatch)

        names = [event.event_type for event in store.events_by_run[run_id]]
        assert "run_failed" in names, names
        orphans = self._orphaned_invocations(store, run_id)
        assert len(orphans) == 1, [record.call_id for record in orphans]
        assert orphans[0].tool_name == self.TOOL_NAME

    async def test_the_orphan_is_not_recorded_as_a_tool_exception(
        self, monkeypatch
    ) -> None:
        # The whole defect in one assertion: ``tool_exception`` means the tool
        # raised, and reading it off the ledger is what made a run-level
        # failure look like a ``write_todos`` bug.
        run_id, store = await self._drive(monkeypatch)

        for record in self._orphaned_invocations(store, run_id):
            assert record.safe_error_code != ToolErrorCode.TOOL_EXCEPTION.value, (
                f"{record.tool_name} was closed by run-level reconciliation but "
                "recorded as a tool that threw"
            )

    async def test_the_orphan_carries_the_run_failed_code(self, monkeypatch) -> None:
        run_id, store = await self._drive(monkeypatch)

        codes = {
            record.safe_error_code
            for record in self._orphaned_invocations(store, run_id)
        }
        assert codes == {ToolErrorCode.TOOL_RUN_FAILED.value}, codes

    async def test_the_stored_message_names_the_run_cause(self, monkeypatch) -> None:
        # A code alone still leaves "why did the run die?" unanswered, which is
        # the question the ledger exists to answer. The run's own typed code
        # rides along on the row.
        run_id, store = await self._drive(monkeypatch)

        cause = self._run_failure_code(store, run_id)
        [record] = self._orphaned_invocations(store, run_id)
        assert record.safe_error_message is not None
        assert cause in record.safe_error_message, record.safe_error_message

    async def test_the_message_no_longer_accuses_the_tool(self, monkeypatch) -> None:
        # The exact string the benchmark read and believed. It is an assertion
        # about the tool, and on this path it is false.
        run_id, store = await self._drive(monkeypatch)

        for record in self._orphaned_invocations(store, run_id):
            assert "The tool reported an error" not in (record.safe_error_message or "")

    async def test_the_streamed_tool_result_agrees_with_the_ledger(
        self, monkeypatch
    ) -> None:
        # The client renders the event, the auditor reads the row. They must
        # not disagree about why a step stopped.
        run_id, store = await self._drive(monkeypatch)

        payloads = self._reconciled_tool_results(store, run_id)
        assert payloads, "the reconciler emitted no terminal tool_result"
        for payload in payloads:
            assert payload["error_code"] == ToolErrorCode.TOOL_RUN_FAILED.value
