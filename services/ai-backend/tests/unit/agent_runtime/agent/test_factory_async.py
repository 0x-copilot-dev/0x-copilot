"""``acreate_agent_runtime`` parallel-listing contract.

The async factory must run the registry-listing calls concurrently via
``asyncio.gather`` (for async-native registries) and ``asyncio.to_thread``
(for CPU-only registries) so the worker's event loop is never blocked.

These tests use the shared ``fake_dependencies`` / ``runtime_context_admin``
fixtures from ``tests/unit/conftest.py`` and patch the registry methods to
gate or sleep, exposing the parallelism contract.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


class TestAsyncFactoryParallelism:
    """The five listing calls must run concurrently.

    Three listings go through ``asyncio.to_thread`` (tools / subagents /
    skill directories) and two are async-native (mcp servers / skill cards).
    The cross-coordination barrier therefore needs all five parties to be
    in flight at once — async-native listings reach the barrier via a
    nested ``asyncio.to_thread`` so a single ``threading.Barrier`` works
    across both groups.
    """

    async def test_listing_calls_run_in_parallel(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        import threading

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
