"""Worker lifecycle ownership tests for the bounded F8 feed poller."""

from __future__ import annotations

import asyncio

import pytest

from runtime_worker.loop import RuntimeWorker
from runtime_worker.mcp_revision_poller import McpRevisionFeedPoller


class _Runner:
    def __init__(self) -> None:
        self.calls = 0

    async def run_once(self) -> object:
        from agent_runtime.capabilities.mcp.revision_feed import (
            McpRevisionFeedRunResult,
        )

        self.calls += 1
        return McpRevisionFeedRunResult(subjects=0, http_calls=0, results=())


async def test_poller_start_stop_is_idempotent_and_has_one_task() -> None:
    runner = _Runner()
    poller = McpRevisionFeedPoller(runner=runner, interval_seconds=60)

    await poller.start()
    first = poller.task
    await poller.start()
    assert poller.task is first
    await asyncio.sleep(0)
    assert runner.calls == 1
    await poller.stop()
    await poller.stop()
    assert poller.task is None


class _SuppressesOneCancellationRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled_once = asyncio.Event()

    async def run_once(self) -> object:
        from agent_runtime.capabilities.mcp.revision_feed import (
            McpRevisionFeedRunResult,
        )

        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled_once.set()
            # Simulate a poorly behaved dependency that consumes one
            # cancellation. The poller must send a bounded final cancellation
            # rather than return with its task still alive.
            return McpRevisionFeedRunResult(subjects=0, http_calls=0, results=())
        raise AssertionError("unreachable")


async def test_poller_stop_drains_runner_that_suppresses_one_cancellation() -> None:
    runner = _SuppressesOneCancellationRunner()
    poller = McpRevisionFeedPoller(
        runner=runner, interval_seconds=60, stop_grace_seconds=0.01
    )
    await poller.start()
    await runner.started.wait()

    await poller.stop()

    assert runner.cancelled_once.is_set()
    assert poller.task is None
    assert poller.diagnostics() == {"stop_timeouts": 1}


async def test_start_cannot_replace_task_while_stop_is_draining() -> None:
    runner = _SuppressesOneCancellationRunner()
    poller = McpRevisionFeedPoller(
        runner=runner, interval_seconds=60, stop_grace_seconds=0.01
    )
    await poller.start()
    first = poller.task
    stopping = asyncio.create_task(poller.stop())
    await runner.cancelled_once.wait()

    await poller.start()
    assert poller.task is first
    await stopping
    assert poller.task is None


class _SuppressesRepeatedCancellationRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_once(self) -> object:
        from agent_runtime.capabilities.mcp.revision_feed import (
            McpRevisionFeedRunResult,
        )

        self.started.set()
        while not self.release.is_set():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                continue
        return McpRevisionFeedRunResult(subjects=0, http_calls=0, results=())


async def test_poller_retains_live_task_when_repeated_cancellation_is_suppressed() -> (
    None
):
    runner = _SuppressesRepeatedCancellationRunner()
    poller = McpRevisionFeedPoller(
        runner=runner, interval_seconds=60, stop_grace_seconds=0.01
    )
    await poller.start()
    await runner.started.wait()
    live = poller.task

    with pytest.raises(RuntimeError, match="did not stop"):
        await poller.stop()
    assert poller.task is live
    assert live is not None and not live.done()

    # A concurrent start while draining/retrying cannot create a second task.
    await poller.start()
    assert poller.task is live
    runner.release.set()
    await poller.stop()
    assert poller.task is None


class _Poller:
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1

    async def stop(self) -> None:
        self.stops += 1


async def test_runtime_worker_starts_and_stops_poller_before_returning() -> None:
    poller = _Poller()
    worker = object.__new__(RuntimeWorker)
    worker._mcp_revision_poller = poller
    worker._provider_circuit_snapshot = None

    async def idle() -> bool:
        return False

    worker.run_once = idle  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            worker.run_forever(poll_interval_seconds=60), timeout=0.01
        )

    assert (poller.starts, poller.stops) == (1, 1)


async def test_runtime_worker_stops_poller_before_flushing_other_lifecycle() -> None:
    order: list[str] = []

    class OrderedPoller(_Poller):
        async def stop(self) -> None:
            await super().stop()
            order.append("poller")

    class Snapshot:
        def flush(self, _health: object) -> None:
            order.append("snapshot")

    worker = object.__new__(RuntimeWorker)
    worker._mcp_revision_poller = OrderedPoller()
    worker._provider_circuit_snapshot = Snapshot()
    worker._provider_circuit_health = object()

    async def idle() -> bool:
        return False

    worker.run_once = idle  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            worker.run_forever(poll_interval_seconds=60), timeout=0.01
        )

    assert order == ["poller", "snapshot"]


async def test_runtime_worker_drains_poller_when_startup_fails() -> None:
    class FailingPoller(_Poller):
        async def start(self) -> None:
            self.starts += 1
            raise RuntimeError("feed startup failed")

    poller = FailingPoller()
    worker = object.__new__(RuntimeWorker)
    worker._mcp_revision_poller = poller
    worker._provider_circuit_snapshot = None

    with pytest.raises(RuntimeError, match="feed startup failed"):
        await worker.run_forever(poll_interval_seconds=60)
    assert (poller.starts, poller.stops) == (1, 1)
