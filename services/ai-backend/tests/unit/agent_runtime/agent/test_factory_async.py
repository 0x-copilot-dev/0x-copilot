"""``acreate_agent_runtime`` parallel-listing contract.

The async factory must:
  * produce a ``RuntimeHarness`` indistinguishable from the sync
    ``create_agent_runtime`` for the same inputs;
  * run the four sync registry-listing calls concurrently via
    ``asyncio.gather(asyncio.to_thread(...), ...)`` so the worker's
    asyncio loop is not blocked by sync HTTP inside a provider.

These tests use the shared ``fake_dependencies`` / ``runtime_context_admin``
fixtures from ``tests/unit/conftest.py`` and patch the registry methods to
gate or sleep, exposing the parallelism contract.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import (
    acreate_agent_runtime,
    create_agent_runtime,
)
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


class TestAsyncFactoryEquivalence:
    """``acreate_agent_runtime`` must produce the same shape as the sync path."""

    async def test_async_harness_matches_sync_harness(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        sync_harness = create_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=CapturingAgentBuilder(),
        )
        async_harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=CapturingAgentBuilder(),
        )

        # Resolved capability sets are byte-identical.
        assert async_harness.tools == sync_harness.tools
        assert async_harness.mcp_servers == sync_harness.mcp_servers
        assert async_harness.subagents == sync_harness.subagents
        assert async_harness.skill_directories == sync_harness.skill_directories
        # Context propagates unchanged.
        assert async_harness.context == sync_harness.context


class TestAsyncFactoryParallelism:
    """The four listing calls must run concurrently."""

    async def test_listing_calls_run_in_parallel(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Four-way barrier — would deadlock if any pair of listings ran
        sequentially. ``asyncio.to_thread`` runs each on a thread, so we
        use a ``threading.Barrier`` (cross-thread) not ``asyncio.Barrier``.
        """

        import threading

        barrier = threading.Barrier(4, timeout=2.0)

        def _gated(value: object) -> object:
            barrier.wait()
            return value

        with (
            patch.object(
                fake_dependencies.tool_registry,
                "list_available_tools",
                side_effect=lambda _ctx: _gated(()),
            ),
            patch.object(
                fake_dependencies.mcp_registry,
                "list_available_servers",
                side_effect=lambda _ctx: _gated(()),
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

    async def test_total_latency_tracks_max_not_sum(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Each listing sleeps 100ms. Serial would take ≥400ms; parallel
        ≤220ms (max + slack for thread overhead and post-fan-out work)."""

        delay = 0.1

        def _slow(value: object) -> object:
            time.sleep(delay)
            return value

        with (
            patch.object(
                fake_dependencies.tool_registry,
                "list_available_tools",
                side_effect=lambda _ctx: _slow(()),
            ),
            patch.object(
                fake_dependencies.mcp_registry,
                "list_available_servers",
                side_effect=lambda _ctx: _slow(()),
            ),
            patch.object(
                fake_dependencies.subagent_catalog,
                "list_available_subagents",
                side_effect=lambda _ctx: _slow(()),
            ),
            patch(
                "agent_runtime.execution.factory.SkillSourceRegistry.skill_directories_for_deep_agent",
                side_effect=lambda _config: _slow(()),
            ),
        ):
            start = time.monotonic()
            await acreate_agent_runtime(
                context=runtime_context_admin,
                dependencies=fake_dependencies,
                agent_builder=CapturingAgentBuilder(),
            )
            elapsed = time.monotonic() - start

        # Generous upper bound: max(delay) + 120ms slack for thread pool
        # spin-up + post-fan-out assembly. Serial baseline = 4 × delay = 400ms.
        assert elapsed < 0.22, (
            f"acreate_agent_runtime took {elapsed:.3f}s; expected < 0.22s "
            f"with parallel listings. Serial baseline would be ≥0.40s."
        )
