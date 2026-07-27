"""Actor-isolated model-facing port for staged workspace mutations.

The Deep Agents backend receives only :class:`WorkspaceOperationPort`.  The
gateway and raw-overlay adapter live in a worker-owned actor coroutine, not in
the backend's reachable object graph.  The port is containment only; the
operation gateway's task-bound stage capability remains the authorization
boundary for every proposal.
"""

from __future__ import annotations

import asyncio
from contextvars import Context, copy_context
from dataclasses import dataclass
from weakref import finalize

from agent_runtime.capabilities.operations.contracts import (
    OperationAdapter,
    OperationDisposition,
    OperationRequest,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway


@dataclass(frozen=True)
class _OperationMessage:
    request: OperationRequest
    context: Context
    result: asyncio.Future[OperationDisposition]


_STOP = object()


class WorkspaceOperationPort:
    """One narrow queue-backed route into worker-owned operation composition."""

    __slots__ = ("_queue", "_lease", "__weakref__")

    def __new__(cls) -> WorkspaceOperationPort:
        del cls
        raise TypeError("workspace operation ports are created by worker composition")

    @classmethod
    def bind(
        cls,
        *,
        gateway: OperationGateway,
        adapter: OperationAdapter,
    ) -> WorkspaceOperationPort:
        """Start an isolated actor and return the model-safe queue endpoint."""

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[_OperationMessage | object] = asyncio.Queue()
        loop.create_task(
            _serve_operations(queue=queue, gateway=gateway, adapter=adapter),
            name="workspace-operation-port",
        )
        port = object.__new__(cls)
        port._queue = queue
        # The queue's pending getter retains the actor while the port is live.
        # Finalization sends a sentinel, allowing the actor to release its
        # gateway/adapter graph instead of retaining a finished run.
        port._lease = finalize(port, _close_queue, queue)
        return port

    async def invoke(self, request: OperationRequest) -> OperationDisposition:
        """Submit one canonical request without exposing gateway composition."""

        loop = asyncio.get_running_loop()
        result: asyncio.Future[OperationDisposition] = loop.create_future()
        await self._queue.put(
            _OperationMessage(
                request=request,
                context=copy_context(),
                result=result,
            )
        )
        return await result


async def _serve_operations(
    *,
    queue: asyncio.Queue[_OperationMessage | object],
    gateway: OperationGateway,
    adapter: OperationAdapter,
) -> None:
    """Run gateway work under the submitting operation context, one at a time."""

    while True:
        message = await queue.get()
        if message is _STOP:
            queue.task_done()
            return
        assert isinstance(message, _OperationMessage)
        invocation = message.context.run(
            asyncio.create_task,
            gateway.invoke(message.request, adapter),
        )
        try:
            if not message.result.done():
                message.result.set_result(await invocation)
        except BaseException as exc:
            if not message.result.done():
                message.result.set_exception(exc)
        finally:
            queue.task_done()


def _close_queue(queue: asyncio.Queue[_OperationMessage | object]) -> None:
    queue.put_nowait(_STOP)


__all__ = ("WorkspaceOperationPort",)
