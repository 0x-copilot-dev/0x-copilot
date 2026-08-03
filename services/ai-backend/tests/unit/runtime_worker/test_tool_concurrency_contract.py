"""What has to stay true now that LangGraph, not us, schedules a turn's tools.

The runtime used to take a run-wide exclusive lock around every graph-visible
tool call. That lock was load-bearing for more than it advertised: it also meant
every piece of per-run state the tool seam touches — the budget ledger, the
citation ordinals, the event sequence — was only ever reached by one caller at a
time. Removing it hands scheduling to the framework, which is the point, and
puts all of that under real concurrency for the first time.

These drive the **real** worker, the **real** Deep Agents graph and the **real**
streaming executor, with only the chat model faked (``RUNTIME_FAKE_MODEL``),
because the property under test is a property of the composition. Every one of
them fails on the pre-change tree or on a tree where the concurrency-safety fixes
are reverted; none of them passes by simply not running the tools concurrently,
which is why each asserts the observed overlap as well as the invariant.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from agent_runtime.capabilities.citation_capturing_tool import CitationHint
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.persistence.records import (
    DefaultToolBudget,
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_worker import dependencies as worker_dependencies
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker
from runtime_worker.tool_call_ledger import ToolCallLedger

from tests.unit.runtime_worker.test_fake_model_run_stream import FakeModelRunMixin


class _WebSearchArgs(BaseModel):
    """Mirrors the shipped ``web_search`` argument surface."""

    query: str
    display_title: str | None = None
    display_summary: str | None = None


#: How long a sibling waits for the rest of its turn to arrive before the probe
#: gives up and records the peak it actually saw. Only reached when the calls
#: are NOT concurrent, so a green run never spends it.
_RENDEZVOUS_TIMEOUT_SECONDS = 5.0


class _OverlapProbe:
    """Record how many copies of the leaf tool were in flight at once.

    ``expected`` turns the observation into a rendezvous rather than a race
    against a sleep: every arrival waits for the rest of its turn, so the peak
    is exact when the calls are concurrent and times out to the true (smaller)
    peak when they are not. No assertion in this file depends on wall-clock
    timing, which is what keeps them from becoming a CI flake.
    """

    def __init__(self, expected: int = 0) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.executions = 0
        self._expected = expected
        self._all_present: asyncio.Event | None = None

    def _enter(self) -> None:
        with self.lock:
            self.executions += 1
            self.active += 1
            self.peak = max(self.peak, self.active)

    def _leave(self) -> None:
        with self.lock:
            self.active -= 1

    async def arrive(self) -> None:
        """Count this call in and wait for its siblings (async lane)."""

        if self._all_present is None:
            self._all_present = asyncio.Event()
        self._enter()
        try:
            if self.active >= self._expected:
                self._all_present.set()
            try:
                await asyncio.wait_for(
                    self._all_present.wait(),
                    timeout=_RENDEZVOUS_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                pass
        finally:
            self._leave()

    def occupy(self, seconds: float) -> None:
        """Count this call in and hold for ``seconds`` (threaded lane).

        The sync tool node's pool may be narrower than the fan-out, so a
        rendezvous over every call would deadlock rather than measure. This
        lane therefore only establishes *that* threads overlapped.
        """

        self._enter()
        try:
            time.sleep(seconds)
        finally:
            self._leave()


class _OverlapWebSearchTool(BaseTool):
    """Offline stand-in for the DuckDuckGo leaf; everything above stays real."""

    name: str = "web_search"
    description: str = "Search the web for information."
    args_schema: type[BaseModel] = _WebSearchArgs

    probe: Any = None

    def _run(self, *args: object, **kwargs: object) -> str:
        self.probe.occupy(0.01)
        return "[{'title': 'Result', 'link': 'https://example.test', 'snippet': 'x'}]"

    async def _arun(self, *args: object, **kwargs: object) -> str:
        await self.probe.arrive()
        return "[{'title': 'Result', 'link': 'https://example.test', 'snippet': 'x'}]"


class ConcurrentFanoutRunMixin(FakeModelRunMixin):
    """Drive one real run whose single model turn emits ``fanout`` tool calls."""

    @classmethod
    async def _run_fanout(
        cls,
        monkeypatch: pytest.MonkeyPatch,
        *,
        fanout: int,
        cap: int,
    ) -> tuple[InMemoryRuntimeApiStore, str, _OverlapProbe]:
        settings = cls._settings()
        # The rendezvous is over the calls that can actually be in flight at
        # once. Three things bound that, and all three are real: the fan-out
        # itself, the per-tool budget (a refused call never reaches the body),
        # and the graph's own ``max_concurrency`` — which the runtime already
        # sets from ``execution.max_parallel_tasks`` (default 4). That last one
        # is the framework-native control this change defers to, so the test
        # reads it rather than hard-coding a number that would drift.
        probe = _OverlapProbe(
            expected=min(fanout, cap, settings.execution.max_parallel_tasks)
        )
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", "web_search")
        monkeypatch.setenv(
            "RUNTIME_FAKE_MODEL_PARALLEL_TOOL_CALLS",
            json.dumps(
                [
                    {"name": "web_search", "args": {"query": f"question {index}"}}
                    for index in range(fanout)
                ]
            ),
        )
        monkeypatch.setattr(
            worker_dependencies.WebSearchToolRegistry,
            "_web_search_tool",
            classmethod(lambda _cls: _OverlapWebSearchTool(probe=probe)),
        )

        store = InMemoryRuntimeApiStore()
        store.tool_budgets[DefaultToolBudget.ID] = ToolBudgetRecord(
            id=DefaultToolBudget.ID,
            org_id=None,
            tool_name=DefaultToolBudget.TOOL_NAME,
            max_calls_per_run=cap,
            enforcement=ToolBudgetEnforcement.HARD,
        )
        run_id = await cls._enqueue_run(store, settings)
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
        return (store, run_id, probe)

    @staticmethod
    def _tool_results(store: InMemoryRuntimeApiStore, run_id: str) -> list[str]:
        return [
            str(event.payload)
            for event in store.events_by_run[run_id]
            if event.event_type == "tool_result"
        ]


class TestFrameworkOwnsToolConcurrency(ConcurrentFanoutRunMixin):
    """The change itself: a turn's tool calls really do overlap now."""

    async def test_a_turns_tool_calls_run_concurrently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Four sibling calls overlap. Before this change the peak was always 1.

        This is the whole point of removing the run-wide admission lock, so it
        is asserted on the real graph rather than on the middleware in isolation
        — a lock reintroduced anywhere between the tool node and the leaf would
        fail here even if the middleware itself looked unchanged.
        """

        settings = self._settings()
        fanout = settings.execution.max_parallel_tasks
        assert fanout > 1, "this deployment configures no parallelism to observe"
        _store, _run_id, probe = await self._run_fanout(
            monkeypatch,
            fanout=fanout,
            cap=100,
        )

        assert probe.executions == fanout
        assert probe.peak == fanout, (
            f"peak overlap was {probe.peak} of {fanout} sibling calls — "
            "something is serializing the tool node again"
        )


class TestBudgetSurvivesConcurrency(ConcurrentFanoutRunMixin):
    """(a) Per-tool budget accounting under a concurrent turn."""

    async def test_cap_is_not_overrun_by_concurrent_siblings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Six concurrent calls against a cap of two spend exactly two.

        The regression this pins is a check-then-charge race: ``check_admit``
        reads the ledger and ``record_started`` writes to it, and while the run
        held one exclusive lock nothing could read in between. Concurrent
        siblings can, so the pair is charged under
        :meth:`ToolBudgetGuard.admit_and_charge` instead. Without that, several
        callers observe the same "one left" and all of them are admitted.
        """

        store, run_id, probe = await self._run_fanout(
            monkeypatch,
            fanout=6,
            cap=2,
        )

        assert probe.peak > 1, "the calls did not overlap; the test proves nothing"
        assert probe.executions == 2, (
            f"{probe.executions} executions against a cap of 2 — concurrent "
            "admission overran the per-tool budget"
        )
        refusals = [
            body
            for body in self._tool_results(store, run_id)
            if "ToolBudgetRejected" in body
        ]
        assert len(refusals) == 4, self._tool_results(store, run_id)


class TestEventSequencingSurvivesConcurrency(ConcurrentFanoutRunMixin):
    """(c) The run journal stays a total order under concurrent tool results."""

    async def test_sequence_numbers_stay_dense_and_unique(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``sequence_no`` is strictly monotonic with no gaps and no duplicates.

        Clients resume a stream with the highest ``sequence_no`` they received,
        so a duplicate silently drops an event for every live reader and a gap
        stalls the reconnect. Eight concurrent calls each emit a start and a
        result pair into the same journal.
        """

        store, run_id, probe = await self._run_fanout(
            monkeypatch,
            fanout=8,
            cap=100,
        )

        assert probe.peak > 1, "the calls did not overlap; the test proves nothing"
        sequences = [event.sequence_no for event in store.events_by_run[run_id]]
        assert sequences == sorted(sequences), sequences
        assert len(sequences) == len(set(sequences)), "duplicate sequence_no"
        assert sequences == list(range(1, len(sequences) + 1)), "gap in sequence_no"

        starts = [
            event
            for event in store.events_by_run[run_id]
            if event.event_type == "tool_call_started"
        ]
        results = [
            event
            for event in store.events_by_run[run_id]
            if event.event_type == "tool_result"
        ]
        assert len(starts) == 8, [
            event.event_type for event in store.events_by_run[run_id]
        ]
        assert len(results) == 8


class TestCitationNumberingSurvivesConcurrency(ConcurrentFanoutRunMixin):
    """(d) The ``[Tool call #N]`` pointers the model cites stay unique."""

    async def test_concurrent_calls_get_distinct_ordinals(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Six overlapping calls produce six distinct, gap-free ordinals.

        The ordinal is what the model cites as ``[[N]]`` and what the citation
        resolver maps back to a tool call, so two results sharing an ordinal
        makes every citation to it ambiguous. Allocation is a read-then-write on
        a shared counter, which only one caller could reach at a time before.
        """

        store, run_id, probe = await self._run_fanout(
            monkeypatch,
            fanout=6,
            cap=100,
        )

        assert probe.peak > 1, "the calls did not overlap; the test proves nothing"
        ordinals = sorted(
            int(body.split(CitationHint.NOTE_PREFIX, 1)[1].split(" ", 1)[0])
            for body in self._tool_results(store, run_id)
            if CitationHint.NOTE_PREFIX in body
        )
        assert ordinals, "no citation pointers were emitted at all"
        assert len(ordinals) == len(set(ordinals)), f"colliding ordinals: {ordinals}"
        assert ordinals == list(range(1, len(ordinals) + 1)), ordinals


# ---------------------------------------------------------------------------
# The synchronous tool node, which is a *thread* pool rather than a task group.
# ---------------------------------------------------------------------------


class _SyncFanoutModel(BaseChatModel):
    """Emit ``_SYNC_FANOUT`` sibling calls in one turn, then a final answer."""

    fanout: int = 8

    @property
    def _llm_type(self) -> str:
        return "sync-fanout-concurrency-contract"

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "observed_tool",
                    "args": {"value": value},
                    "id": f"call-{value}",
                }
                for value in range(self.fanout)
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        del tools, kwargs
        return self


class TestSyncToolNodeIsThreadSafe:
    """The sync lane is a thread pool, so its shared state needs real locks.

    LangGraph's synchronous ``ToolNode`` fans a turn's calls across
    ``executor.map`` — genuine OS threads, not coroutines. The run-wide
    ``threading.Lock`` that used to wrap this seam is gone, so the per-run tool
    ledger is reached by several threads at once. Two distinct defects follow,
    and this pins both:

    * ``ToolCallLedger.charged_calls`` iterates the entry dict. A concurrent
      insert during that iteration raises ``RuntimeError: dictionary changed
      size during iteration`` — a hard failure of an unrelated tool call.
    * ``check_admit`` then ``record_started`` is a read-then-write. Without one
      lock across the pair, several threads observe the same remaining budget.

    The GIL switch interval is squeezed for the duration so the window is
    reached deterministically rather than once in a few thousand CI runs. That
    changes only the scheduler's granularity, never the code under test: at the
    default interval this same body overran the cap by 2–6 calls and crashed on
    the dict iteration, and it is only the *rate* that the squeeze changes.
    """

    FANOUT = 64
    CAP = 40

    def test_threaded_fanout_neither_crashes_nor_overruns_the_cap(self) -> None:
        probe = _OverlapProbe()

        def observed_tool(value: int) -> str:
            probe.occupy(0.005)
            return str(value)

        tool = StructuredTool.from_function(
            name="observed_tool",
            description="Record one observed value.",
            func=observed_tool,
        )
        ledger = ToolCallLedger("run-concurrency-contract")
        # A ledger with real history: every counting read walks it, so this is
        # what makes the read long enough to be preempted mid-iteration.
        for index in range(4000):
            ledger.started(f"seed-{index}", tool_name="other_tool", budget_scoped=True)
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [
                    ToolBudgetRecord(
                        id="concurrency-contract",
                        org_id=None,
                        tool_name="*",
                        max_calls_per_run=self.CAP,
                        enforcement=ToolBudgetEnforcement.HARD,
                    )
                ]
            ),
            ledger=ledger,
            # The run must reach the end rather than escalating to a fatal
            # ``BudgetExceeded``; the cap is what is under test, not the
            # escalation policy.
            max_surfaced_rejections=10_000,
        )
        graph = create_agent(
            model=_SyncFanoutModel(fanout=self.FANOUT),
            tools=[tool],
            middleware=[RuntimeControlMiddleware()],
        )

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            graph.invoke({"messages": [HumanMessage(content="Observe everything.")]})
        finally:
            ToolBudgetGuard.unbind(token)
            sys.setswitchinterval(previous_interval)

        assert probe.peak > 1, "the threads did not overlap; the test proves nothing"
        assert probe.executions == self.CAP, (
            f"{probe.executions} executions against a cap of {self.CAP} — "
            "concurrent threads overran the per-tool budget"
        )
