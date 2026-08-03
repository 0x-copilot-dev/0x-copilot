"""The run permit must survive nesting: ``task`` hosts calls that also take it.

Every other tool this middleware wraps is a leaf — it does its own work and
returns. ``task`` is a container: its body awaits a whole child graph, and that
child's tool calls come back through this same middleware, on the same run, and
reach the same run-scoped admission. Taking the exclusive permit for the
container therefore parks the child on a lock its own parent holds.

``test_subagent_tool_call_real_run`` proves the end-to-end consequence on the
real graph. This module pins the property at the seam, where it can be stated
in one sentence, and covers the synchronous lane the real graph never enters.

Both hang guards here are *assertions*: a permit that has stopped being
re-entrant for delegation is exactly a call that never returns, so expiry is the
failure this file exists to catch. Neither guard's duration is load-bearing —
the operations they bound complete in microseconds.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent_runtime.api.constants import Values
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import RunControlContext

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _binding,
)

#: Failsafe on an operation that returns immediately when the seam is correct.
_HANG_GUARD_SECONDS = 5.0

#: Spelled from the shared constant rather than imported from the middleware, so
#: every assertion below is about *behaviour*: this module fails on a regressed
#: seam, not merely on a renamed symbol.
_TASK = Values.Tool.TASK


def _request(*, name: str, call_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id, "type": "tool_call"},
        tool=None,
        state={},
        runtime=cast(Any, object()),
    )


def _reply(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=str(request.tool_call["name"]),
        tool_call_id=str(request.tool_call["id"]),
    )


class TestDelegationDoesNotBlockTheCallsItContains:
    """The defect, at the seam that produced it."""

    async def test_a_nested_leaf_call_completes_inside_a_delegation_call(
        self,
    ) -> None:
        """A child's tool call must not wait on the ``task`` call that spawned it.

        Two middleware instances, because that is production: Deep Agents
        materialises this middleware separately for the supervisor and for each
        local child graph. They share one admission because it is run-scoped,
        which is what makes the nesting contended rather than incidental.
        """

        supervisor = RuntimeToolControlMiddleware()
        child = RuntimeToolControlMiddleware()
        completed: list[str] = []

        async def leaf_handler(request: ToolCallRequest) -> ToolMessage:
            completed.append(str(request.tool_call["name"]))
            return _reply(request)

        async def delegation_handler(request: ToolCallRequest) -> ToolMessage:
            # Stands in for ``subagent.ainvoke``: the container's body IS a
            # nested pass through this seam.
            await child.awrap_tool_call(
                _request(name="write_todos", call_id="child-call"),
                leaf_handler,
            )
            completed.append(str(request.tool_call["name"]))
            return _reply(request)

        token = RunControlContext.bind_for_run(_binding())
        try:
            async with asyncio.timeout(_HANG_GUARD_SECONDS):
                await supervisor.awrap_tool_call(
                    _request(name=_TASK, call_id="task-call"),
                    delegation_handler,
                )
        finally:
            RunControlContext.unbind(token)

        assert completed == ["write_todos", _TASK]

    def test_a_nested_leaf_call_completes_on_the_synchronous_seam_too(self) -> None:
        """The sync lane is the worse one to get wrong, so it is pinned as well.

        ``sync_permit`` holds a ``threading.Lock``. Re-entering it on the thread
        that already owns it blocks that thread outright — there is no run
        timeout underneath a blocked thread, so the failure mode is a wedged
        worker rather than a failed run. The whole nesting therefore runs on a
        worker thread, whose join is the assertion.
        """

        middleware = RuntimeToolControlMiddleware()
        completed: list[str] = []
        finished = threading.Event()

        def leaf_handler(request: ToolCallRequest) -> ToolMessage:
            completed.append(str(request.tool_call["name"]))
            return _reply(request)

        def delegation_handler(request: ToolCallRequest) -> ToolMessage:
            middleware.wrap_tool_call(
                _request(name="write_todos", call_id="child-call"),
                leaf_handler,
            )
            completed.append(str(request.tool_call["name"]))
            return _reply(request)

        def drive() -> None:
            # Bound inside the thread: the admission lives in a ContextVar, and
            # a ContextVar is not inherited by a thread started from here.
            token = RunControlContext.bind_for_run(_binding())
            try:
                middleware.wrap_tool_call(
                    _request(name=_TASK, call_id="task-call"),
                    delegation_handler,
                )
            finally:
                RunControlContext.unbind(token)
                finished.set()

        threading.Thread(target=drive, daemon=True).start()

        assert finished.wait(timeout=_HANG_GUARD_SECONDS), (
            "the synchronous delegation seam never returned — a nested leaf "
            "call is blocking on the sync permit its own caller holds"
        )
        assert completed == ["write_todos", _TASK]


class TestTheExemptionIsOnlyTheContainer:
    """What the exemption must NOT do: relax serialization of real tool work."""

    async def test_leaf_calls_inside_one_delegation_are_still_serialized(
        self,
    ) -> None:
        """The child's own tool calls take the run permit and cannot overlap.

        This is the whole reason the container is exempted rather than the lock
        made re-entrant: actual tool work stays serial across the run, including
        work a subagent performs.
        """

        supervisor = RuntimeToolControlMiddleware()
        child = RuntimeToolControlMiddleware()
        active = 0
        maximum_active = 0

        async def leaf_handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            return _reply(request)

        async def delegation_handler(request: ToolCallRequest) -> ToolMessage:
            await asyncio.gather(
                *(
                    child.awrap_tool_call(
                        _request(name="write_todos", call_id=f"child-{index}"),
                        leaf_handler,
                    )
                    for index in range(3)
                )
            )
            return _reply(request)

        token = RunControlContext.bind_for_run(_binding())
        try:
            async with asyncio.timeout(_HANG_GUARD_SECONDS):
                await supervisor.awrap_tool_call(
                    _request(name=_TASK, call_id="task-call"),
                    delegation_handler,
                )
        finally:
            RunControlContext.unbind(token)

        assert maximum_active == 1

    async def test_a_non_delegation_call_still_takes_the_run_permit(self) -> None:
        """The exemption is keyed on one name, not on nesting in general.

        A sibling fan-out of ordinary tools is unchanged: still serial, exactly
        as ``test_runtime_tool_control`` pins it.
        """

        middleware = RuntimeToolControlMiddleware()
        active = 0
        maximum_active = 0

        async def handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            return _reply(request)

        token = RunControlContext.bind_for_run(_binding())
        try:
            await asyncio.gather(
                *(
                    middleware.awrap_tool_call(
                        _request(name="write_todos", call_id=f"call-{index}"),
                        handler,
                    )
                    for index in range(3)
                )
            )
        finally:
            RunControlContext.unbind(token)

        assert maximum_active == 1


def test_the_delegation_name_is_the_one_the_worker_projects_subagents_from() -> None:
    """One name, one constant — the seam and the projection cannot drift apart.

    The middleware decides what is a container; ``stream_subagents`` decides
    what raises a subagent card. If those two ever disagreed, a delegation would
    either deadlock again or render as a plain tool call, so the seam is not
    allowed to reintroduce a private spelling of the name.
    """

    from agent_runtime.capabilities.middleware.runtime_tool_control import (
        DELEGATION_TOOL_NAME,
    )

    assert DELEGATION_TOOL_NAME is Values.Tool.TASK
