"""Worker-owned scheduling for the bounded MCP revision feed runner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from agent_runtime.capabilities.mcp.revision_feed import McpRevisionFeedRunner
from agent_runtime.capabilities.mcp.control_plane_metrics import (
    McpControlPlaneEvent,
    McpControlPlaneMetricsPort,
    McpControlPlaneOutcome,
    NoopMcpControlPlaneMetrics,
)


class McpRevisionFeedPoller:
    """Own exactly one cancellable task while its worker is running.

    The poller deliberately has no module task, daemon, or entrypoint of its
    own.  ``RuntimeWorker.run_forever`` starts it after construction and stops
    it before the caller can close the worker's HTTP client or persistence.
    """

    def __init__(
        self,
        *,
        runner: McpRevisionFeedRunner,
        interval_seconds: float = 15,
        stop_grace_seconds: float = 5,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        metrics: McpControlPlaneMetricsPort | None = None,
    ) -> None:
        if interval_seconds <= 0 or stop_grace_seconds <= 0:
            raise ValueError("poller interval and stop grace must be positive")
        self._runner = runner
        self._interval = float(interval_seconds)
        self._stop_grace = float(stop_grace_seconds)
        self._sleep = sleep
        self._metrics = metrics or NoopMcpControlPlaneMetrics()
        self._task: asyncio.Task[None] | None = None
        self._guard = asyncio.Lock()
        self._stopping = False
        self._stop_timeouts = 0

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Expose the task only for lifecycle observation/tests."""

        return self._task

    def diagnostics(self) -> dict[str, int]:
        """Content-free lifecycle counters for worker diagnostics."""

        return {"stop_timeouts": self._stop_timeouts}

    async def start(self) -> None:
        """Idempotently start one task on the owning worker's event loop."""

        async with self._guard:
            if self._stopping or (self._task is not None and not self._task.done()):
                return
            self._task = None
            self._task = asyncio.create_task(self._run(), name="mcp-revision-feed")
            self._metrics.event(
                event=McpControlPlaneEvent.POLLER,
                outcome=McpControlPlaneOutcome.STARTED,
            )

    async def stop(self) -> None:
        """Idempotently cancel and boundedly drain the scheduled pass."""

        async with self._guard:
            task = self._task
            if self._stopping:
                return
            self._stopping = True
        if task is None:
            async with self._guard:
                self._stopping = False
            return
        if task.done():
            async with self._guard:
                if self._task is task:
                    self._task = None
                self._stopping = False
            return
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_grace)
        except asyncio.CancelledError:
            if task.cancelled():
                await self._clear_completed(task)
                return
            # The worker is itself cancelling.  The child has received its
            # cancellation and must not keep the parent from unwinding.
            raise
        except TimeoutError:
            # ``wait_for`` must not cancel the task itself: a runner which
            # intentionally catches one cancellation would otherwise continue
            # polling as an orphan after this method returned.  Count the
            # bounded first drain, send one explicit final cancellation, and
            # require that task to finish before reporting shutdown complete.
            self._stop_timeouts += 1
            self._metrics.event(
                event=McpControlPlaneEvent.POLLER,
                outcome=McpControlPlaneOutcome.TIMED_OUT,
            )
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_grace)
            except asyncio.CancelledError:
                if task.cancelled():
                    await self._clear_completed(task)
                    return
                raise
            except TimeoutError as exc:
                # Python cannot forcibly terminate a coroutine which ignores
                # cancellation. Do not lie by clearing its reference or report
                # a successful stop while it is live; fail closed so the host
                # leaves the task live; the host must not claim shutdown or
                # close shared HTTP/store resources underneath it.
                raise RuntimeError("MCP revision feed poller did not stop") from exc
        else:
            await self._clear_completed(task)
        finally:
            if task.done():
                self._metrics.event(
                    event=McpControlPlaneEvent.POLLER,
                    outcome=McpControlPlaneOutcome.STOPPED,
                )
            async with self._guard:
                self._stopping = False

    async def _clear_completed(self, task: asyncio.Task[None]) -> None:
        """Clear the owned task only after its completion is observable."""

        async with self._guard:
            if self._task is task and task.done():
                self._task = None

    async def _run(self) -> None:
        while True:
            result = await self._runner.run_once()
            delay = result.retry_after_seconds or self._interval
            await self._sleep(delay)


class McpRevisionPollerLifecyclePort(Protocol):
    """Narrow worker ownership seam for the poller's lifecycle only."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
