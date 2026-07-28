"""``acreate_agent_runtime`` parallel-listing contract.

The async factory must run the registry-listing calls concurrently via
``asyncio.gather`` (for async-native registries) and ``asyncio.to_thread``
(for CPU-only registries) so the worker's event loop is never blocked.

These tests use the shared ``fake_dependencies`` / ``runtime_context_admin``
fixtures from ``tests/unit/conftest.py`` and patch the registry methods to
gate the listings, exposing the parallelism contract.

Both tests assert on *scheduling*, never on elapsed wall-clock time. A
stopwatch assertion ("the build finished within N ms") measures the host's
load as much as the factory's fan-out, so it decays into a flake under a
loaded suite. The observable property the fan-out actually promises is that
every listing is in flight before any of them completes — assert that
directly and the result is identical on an idle laptop and a saturated CI
runner.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


class ListingOverlapProbe:
    """Thread-safe recorder that proves the listing branches overlap.

    Each patched listing calls :meth:`observe`. The probe appends a
    ``STARTED`` entry, blocks that branch until *every* branch has started,
    then appends ``FINISHED``. On the parallel path the last arrival
    releases all parties at once, so a passing run sleeps for exactly no
    time. On a serialized path the branches can never all be in flight, so
    the transcript interleaves ``STARTED``/``FINISHED`` pairs and the test
    reports which branches were actually running together.

    The only clock read is ``release_timeout``, which bounds how long a
    *failing* run stays blocked — no assertion depends on it, so there is
    no threshold to tune and nothing for host load to tip over.
    """

    class Phase:
        STARTED = "started"
        FINISHED = "finished"

    def __init__(self, *, parties: int, release_timeout: float = 10.0) -> None:
        self._parties = parties
        self._release_timeout = release_timeout
        self._lock = threading.Lock()
        self._all_started = threading.Event()
        self._deadline: float | None = None
        self._in_flight = 0
        self.peak_in_flight = 0
        self.transcript: list[tuple[str, str]] = []

    def observe(self, branch: str) -> tuple[object, ...]:
        """Stand in for one listing call: record, wait for the rest, return."""

        self._enter(branch)
        self._all_started.wait(self._remaining_wait())
        self._exit(branch)
        return ()

    @property
    def phases(self) -> tuple[str, ...]:
        """Transcript phases in recorded order (``STARTED`` / ``FINISHED``)."""

        return tuple(phase for phase, _branch in self.transcript)

    def branches_in_flight_together(self) -> frozenset[str]:
        """Branches that started before the first branch completed."""

        started: list[str] = []
        for phase, branch in self.transcript:
            if phase == self.Phase.FINISHED:
                break
            started.append(branch)
        return frozenset(started)

    def _enter(self, branch: str) -> None:
        with self._lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
            self.transcript.append((self.Phase.STARTED, branch))
            if self._deadline is None:
                self._deadline = time.monotonic() + self._release_timeout
            if self._in_flight == self._parties:
                self._all_started.set()

    def _exit(self, branch: str) -> None:
        with self._lock:
            self._in_flight -= 1
            self.transcript.append((self.Phase.FINISHED, branch))

    def _remaining_wait(self) -> float:
        with self._lock:
            deadline = self._deadline
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())


class ListingFanOutMixin:
    """Patch all five listing branches of the gather onto one probe.

    ``list_available_tools`` / ``list_available_subagents`` /
    ``skill_directories_for_deep_agent`` are the CPU-bound listings the
    factory pushes through ``asyncio.to_thread``; their fakes block on a
    worker thread, exactly where production would.

    ``list_available_servers`` / ``_skill_cards`` are async-native — in
    production their I/O is awaited (``httpx.AsyncClient``) and the event
    loop stays free — so their fakes hop to a thread before blocking. A
    fake that blocked the loop instead would stall the branches
    ``asyncio.gather`` has not scheduled yet, serializing the fan-out the
    test is trying to observe.
    """

    class Branches:
        TOOLS = "list_available_tools"
        MCP_SERVERS = "list_available_servers"
        SUBAGENTS = "list_available_subagents"
        SKILL_DIRECTORIES = "skill_directories_for_deep_agent"
        SKILL_CARDS = "_skill_cards"
        ALL = (
            TOOLS,
            MCP_SERVERS,
            SUBAGENTS,
            SKILL_DIRECTORIES,
            SKILL_CARDS,
        )

    @contextmanager
    def patched_listings(
        self,
        *,
        dependencies: RuntimeDependencies,
        probe: ListingOverlapProbe,
    ) -> Iterator[None]:
        branches = self.Branches

        async def _async_native_ctx(_ctx: object) -> object:
            return await asyncio.to_thread(probe.observe, branches.MCP_SERVERS)

        async def _async_native_kwargs(**_kwargs: object) -> object:
            return await asyncio.to_thread(probe.observe, branches.SKILL_CARDS)

        with (
            patch.object(
                dependencies.tool_registry,
                "list_available_tools",
                side_effect=lambda _ctx: probe.observe(branches.TOOLS),
            ),
            patch.object(
                dependencies.mcp_registry,
                "list_available_servers",
                new_callable=AsyncMock,
                side_effect=_async_native_ctx,
            ),
            patch.object(
                dependencies.subagent_catalog,
                "list_available_subagents",
                side_effect=lambda _ctx: probe.observe(branches.SUBAGENTS),
            ),
            patch(
                "agent_runtime.execution.factory.SkillSourceRegistry.skill_directories_for_deep_agent",
                side_effect=lambda _config: probe.observe(branches.SKILL_DIRECTORIES),
            ),
            patch(
                "agent_runtime.execution.factory._skill_cards",
                new_callable=AsyncMock,
                side_effect=_async_native_kwargs,
            ),
        ):
            yield


class TestAsyncFactoryParallelism(ListingFanOutMixin):
    """The five listing calls must run concurrently.

    Three listings go through ``asyncio.to_thread`` (tools / subagents /
    skill directories) and two are async-native (mcp servers / skill cards).
    The cross-coordination barrier therefore needs all five parties to be
    in flight at once — async-native listings reach the barrier via a
    nested ``asyncio.to_thread`` so a single ``threading.Barrier`` works
    across both groups.

    Two complementary shapes: the barrier test is a liveness check (a
    serial factory cannot get past it), the overlap test records the
    scheduling transcript so a regression says which branches ran alone.
    """

    async def test_listing_calls_run_in_parallel(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        # Wide timeout to avoid flakes under CI load — parallelism is the
        # contract under test; absolute latency is not. 10s is well within
        # the asyncio-to-thread / thread-pool worst-case stall budget.
        barrier = threading.Barrier(5, timeout=10.0)

        def _gated(value: object) -> object:
            barrier.wait()
            return value

        async def _agated_ctx(_ctx: object) -> object:
            return await asyncio.to_thread(_gated, ())

        async def _agated_kwargs(**_kwargs: object) -> object:
            return await asyncio.to_thread(_gated, ())

        with (
            patch.object(
                fake_dependencies.tool_registry,
                "list_available_tools",
                side_effect=lambda _ctx: _gated(()),
            ),
            patch.object(
                fake_dependencies.mcp_registry,
                "list_available_servers",
                new_callable=AsyncMock,
                side_effect=_agated_ctx,
            ),
            patch.object(
                fake_dependencies.subagent_catalog,
                "list_available_subagents",
                side_effect=lambda _ctx: _gated(()),
            ),
            patch(
                "agent_runtime.execution.factory.SkillSourceRegistry.skill_directories_for_deep_agent",
                side_effect=lambda _config: _gated(()),
            ),
            patch(
                "agent_runtime.execution.factory._skill_cards",
                new_callable=AsyncMock,
                side_effect=_agated_kwargs,
            ),
        ):
            harness = await asyncio.wait_for(
                acreate_agent_runtime(
                    context=runtime_context_admin,
                    dependencies=fake_dependencies,
                    agent_builder=CapturingAgentBuilder(),
                ),
                timeout=5.0,
            )

        assert harness is not None

    async def test_all_listings_start_before_any_completes(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Every listing is in flight before the first one completes.

        That overlap is what makes the fan-out cost ``max(branch)`` rather
        than ``sum(branch)``: a branch whose start is ordered after another
        branch's completion adds its own latency to the total, and shows up
        here as a ``FINISHED`` entry ahead of a ``STARTED`` one. Asserting
        the transcript pins the same contract the old ``elapsed < 0.22``
        stopwatch aimed at, without measuring the host.
        """

        expected = len(self.Branches.ALL)
        probe = ListingOverlapProbe(parties=expected)

        with self.patched_listings(dependencies=fake_dependencies, probe=probe):
            harness = await acreate_agent_runtime(
                context=runtime_context_admin,
                dependencies=fake_dependencies,
                agent_builder=CapturingAgentBuilder(),
            )

        assert harness is not None
        assert probe.phases == (
            (ListingOverlapProbe.Phase.STARTED,) * expected
            + (ListingOverlapProbe.Phase.FINISHED,) * expected
        ), (
            "listings did not overlap: only "
            f"{sorted(probe.branches_in_flight_together())} were in flight when "
            f"the first listing completed, expected all {expected}. "
            f"Transcript: {probe.transcript}"
        )
        # Restates the cost model directly: five branches resident at once
        # means the gather pays max(branch), not sum(branch).
        assert probe.peak_in_flight == expected
