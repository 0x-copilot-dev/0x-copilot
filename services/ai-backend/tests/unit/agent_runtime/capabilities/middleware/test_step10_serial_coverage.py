"""F6.8 gate — the serial-coverage criterion, including BUG-11.

Step 10's exit list claims that "writes/effects/approvals/resource conflicts
never overlap improperly". Per PRD §8 that claim rests on **one** run-scoped
permit covering every graph-visible tool call, because everything F6 does is
composed *inside* it.

BUG-11 says the permit is not one gate but two.
:class:`~agent_runtime.control_plane.context.RunSerialAdmission` holds a
``threading.Lock`` for :meth:`sync_permit` and an ``asyncio.Lock`` for
:meth:`async_permit` (``control_plane/context.py:431-432``), and nothing
serializes the two against each other.

This file establishes three separate facts, because the gate's verdict needs
all three and they have different lifetimes:

1. **The hole is real.** The two permits genuinely do not exclude one another.
   That is a characterization test: it asserts today's behaviour so the defect
   is a pinned fact rather than a paragraph in a backlog.
2. **Nothing reaches it today.** A run drives the graph asynchronously, so the
   sync seam is never entered and the two locks are never contended at once.
3. **What holds fact 2 up is not ours.** The sync and async tool seams are
   mutually exclusive per node invocation because of a LangChain/LangGraph
   invariant, not because of anything in ``agent_runtime``. It is pinned here so
   that a framework upgrade that breaks it fails a named test instead of
   silently opening the hole in fact 1.

Nothing here sleeps on the wall clock as part of an assertion. The one timeout
is a hang-guard whose expiry is reported as the *opposite* verdict rather than
as a failure, so this file cannot hang CI whichever way the defect goes.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import RunControlContext, RunSerialAdmission

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _FanoutModel,
    _binding,
)

#: Failsafe only. It bounds a wait that is expected to return immediately; its
#: expiry is interpreted as a verdict, never asserted against, so no assertion
#: in this file depends on how loaded the machine is.
_HANG_GUARD_SECONDS = 5.0


class TestTheRunPermitIsTwoGatesNotOne:
    """BUG-11, stated as an executable fact rather than a claim."""

    async def test_the_sync_and_async_permits_do_not_exclude_each_other(
        self,
    ) -> None:
        """A held ``sync_permit`` does not keep ``async_permit`` from entering.

        This is the §8 hole: "one run-scoped permit covering every graph-visible
        tool call" is two permits, and a synchronous graph tool call and an
        asynchronous one were never serialized against each other.

        **When BUG-11 is closed this test goes red, and that is correct.** It
        asserts the defect, so whoever fixes it must come here and invert the
        expectation deliberately. That is the point of pinning it: a silent
        change in either direction is what a gate exists to prevent.
        """

        admission = RunSerialAdmission()
        sync_permit_held = threading.Event()
        release_sync_permit = threading.Event()

        def hold_the_sync_permit() -> None:
            with admission.sync_permit():
                sync_permit_held.set()
                release_sync_permit.wait(timeout=_HANG_GUARD_SECONDS)

        holder = threading.Thread(target=hold_the_sync_permit, daemon=True)
        holder.start()
        try:
            assert sync_permit_held.wait(timeout=_HANG_GUARD_SECONDS), (
                "the worker thread never took the sync permit"
            )

            async def take_the_async_permit() -> bool:
                async with admission.async_permit():
                    return True

            try:
                async with asyncio.timeout(_HANG_GUARD_SECONDS):
                    overlapped = await take_the_async_permit()
            except TimeoutError:
                # The permits *did* exclude each other. BUG-11 is closed and
                # this test is the one that must be rewritten.
                overlapped = False
        finally:
            release_sync_permit.set()
            holder.join(timeout=_HANG_GUARD_SECONDS)

        assert overlapped, (
            "the async permit was refused while the sync permit was held — "
            "BUG-11 appears to be fixed; update this characterization test"
        )

    def test_the_two_locks_are_distinct_objects(self) -> None:
        """The mechanism behind the behaviour above, named at its source.

        Asserting the behaviour alone would leave a future reader guessing which
        line produced it. These are the two attributes the backlog entry cites.
        """

        admission = RunSerialAdmission()

        assert isinstance(admission._sync_lock, type(threading.Lock()))
        assert isinstance(admission._async_lock, asyncio.Lock)


class TestNothingReachesTheHoleThroughTheRealGraph:
    """Why BUG-11 is latent rather than live, proven rather than assumed."""

    async def test_a_real_async_turn_never_takes_the_sync_permit(self) -> None:
        """Every graph-visible call of a real turn enters the *async* seam.

        The middleware does not choose its lane — ``ToolNode`` does, once per
        node invocation, for the whole batch of tool calls. A run driven by
        ``ainvoke``/``astream`` therefore takes ``async_permit`` for every call
        and ``sync_permit`` for none, which is why two locks that do not exclude
        each other are never contended at the same time.
        """

        seams: list[str] = []

        class _SeamRecordingMiddleware(RuntimeToolControlMiddleware):
            def wrap_tool_call(self, request: Any, handler: Any) -> Any:
                seams.append("sync")
                return super().wrap_tool_call(request, handler)

            async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
                seams.append("async")
                return await super().awrap_tool_call(request, handler)

        async def observe(value: int) -> str:
            return str(value)

        graph = create_agent(
            model=_FanoutModel(),
            tools=[
                StructuredTool.from_function(
                    name="observed_tool",
                    description="Record one observed value.",
                    coroutine=observe,
                )
            ],
            middleware=[_SeamRecordingMiddleware()],
        )

        token = RunControlContext.bind_for_run(_binding())
        try:
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content="Run all observations.")]}
            )
        finally:
            RunControlContext.unbind(token)

        assert result["messages"][-1].content == "done"
        assert seams, "the middleware tool seam was never entered at all"
        assert set(seams) == {"async"}, (
            f"a synchronous graph tool seam was entered in an async run: {seams}"
        )

    def test_the_middleware_overrides_both_tool_seams(self) -> None:
        """The local half of the invariant that keeps the seams exclusive.

        LangChain builds its sync and async wrapper chains from middleware that
        override *either* hook, so overriding both is what guarantees the async
        node is handed an async wrapper and never falls back to the sync one.
        """

        assert (
            RuntimeControlMiddleware.wrap_tool_call
            is not AgentMiddleware.wrap_tool_call
        )
        assert (
            RuntimeControlMiddleware.awrap_tool_call
            is not AgentMiddleware.awrap_tool_call
        )

    def test_the_compiled_graph_is_handed_an_async_tool_seam(self) -> None:
        """The framework half, pinned so an upgrade cannot quietly drop it.

        ``ToolNode._arun_one`` falls back to calling the **synchronous**
        ``wrap_tool_call`` — and therefore ``sync_permit`` — from inside
        ``asyncio.gather`` when it holds a sync wrapper and no async one. That
        branch is unreachable only because the agent factory always installs
        both. Nothing in this repository owns that guarantee, so it is asserted
        against a really compiled graph rather than trusted.
        """

        async def observe(value: int) -> str:
            return str(value)

        graph = create_agent(
            model=_FanoutModel(),
            tools=[
                StructuredTool.from_function(
                    name="observed_tool",
                    description="Record one observed value.",
                    coroutine=observe,
                )
            ],
            middleware=[RuntimeToolControlMiddleware()],
        )

        tool_nodes = [
            candidate
            for candidate in _compiled_node_callables(graph)
            if hasattr(candidate, "_awrap_tool_call")
            or hasattr(candidate, "_wrap_tool_call")
        ]
        assert tool_nodes, "no tool node was found in the compiled graph"
        checked = 0
        for node in tool_nodes:
            if getattr(node, "_wrap_tool_call", None) is None:
                continue
            checked += 1
            assert getattr(node, "_awrap_tool_call", None) is not None, (
                "the tool node holds a sync wrapper and no async one, so an "
                "async turn would take sync_permit from inside asyncio.gather"
            )
        assert checked, (
            "no compiled tool node carried a sync wrapper, so the assertion "
            "above never ran — the discovery above has gone stale"
        )


def _compiled_node_callables(graph: object) -> tuple[object, ...]:
    """Return every plausible node implementation of a compiled graph.

    Deliberately structural rather than keyed by node name: the agent factory
    owns the node's name and may rename it, and a rename must not turn the
    assertion above into a silent no-op. An empty result fails the test.
    """

    found: list[object] = []
    for node in getattr(graph, "nodes", {}).values():
        for attribute in ("bound", "node", "runnable", "func", "afunc"):
            candidate = getattr(node, attribute, None)
            if candidate is not None:
                found.append(candidate)
                inner = getattr(candidate, "__self__", None)
                if inner is not None:
                    found.append(inner)
        found.append(node)
    return tuple(found)
