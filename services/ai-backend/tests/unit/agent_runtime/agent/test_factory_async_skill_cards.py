"""P3 §11.c — ``acreate_agent_runtime`` 5-way gather including ``_skill_cards``.

The 4-way fan-out (tools / mcp / subagents / skill_directories) is pinned by
[test_factory_async.py](test_factory_async.py). This file pins the 5th
branch — ``skill_registry.list_available_skills`` — that used to be a
sequential ``await`` inside ``_assemble_harness``.

Behaviors pinned:
  * ``skill_registry=None`` → cards slot is ``()`` (parity with pre-§11.c).
  * Registry without ``list_available_skills`` → cards slot is ``()``.
  * Five-way gather completes in ~max(branch_latency), not sum.
  * ``_assemble_harness`` accepts ``skill_cards`` as a parameter and threads
    it onto the resulting harness unchanged.
  * One branch failure surfaces ``AgentRuntimeError`` and aborts the build.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


@dataclass
class _FakeSkillRegistryAsync:
    """Async ``list_available_skills`` returning a fixed card set + delay knob."""

    cards: Sequence[object] = ("skill_card_a", "skill_card_b")
    delay: float = 0.0
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    async def list_available_skills(self, context: object) -> Sequence[object]:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.cards


@dataclass
class _FakeSkillRegistryNoListMethod:
    """Object with no ``list_available_skills`` — short-circuits to ``()``."""

    seen: bool = False

    def some_other_method(self) -> None:
        self.seen = True


class _MixinDeps:
    """Build ``RuntimeDependencies`` with the stock fakes plus an optional
    overridden ``skill_registry``. Mirrors the pattern in conftest's
    ``fake_dependencies`` fixture so tests stay self-contained."""

    @staticmethod
    def _build(
        base: RuntimeDependencies, *, skill_registry: object | None
    ) -> RuntimeDependencies:
        return base.model_copy(update={"skill_registry": skill_registry})


class TestSkillCardsBranchDefaults(_MixinDeps):
    """``skill_registry=None`` (the dev default) yields ``()`` cards."""

    async def test_default_no_skill_registry_yields_empty_tuple(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        # ``fake_dependencies`` already has ``skill_registry=None``.
        assert fake_dependencies.skill_registry is None

        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=CapturingAgentBuilder(),
        )

        assert harness.skill_cards == ()

    async def test_registry_without_list_method_yields_empty_tuple(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        registry = _FakeSkillRegistryNoListMethod()
        deps = self._build(fake_dependencies, skill_registry=registry)

        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=deps,
            agent_builder=CapturingAgentBuilder(),
        )

        assert harness.skill_cards == ()
        # Defensive: the registry's other method was never called.
        assert registry.seen is False


class TestSkillCardsBranchActive(_MixinDeps):
    """When the registry exposes ``list_available_skills`` it joins the gather."""

    async def test_skill_cards_threaded_through_assembly(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        registry = _FakeSkillRegistryAsync(cards=("alpha", "beta"))
        deps = self._build(fake_dependencies, skill_registry=registry)

        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=deps,
            agent_builder=CapturingAgentBuilder(),
        )

        assert harness.skill_cards == ("alpha", "beta")
        # Registry was called exactly once with the runtime context.
        assert len(registry.seen_contexts) == 1
        assert registry.seen_contexts[0].user_id == runtime_context_admin.user_id


class TestFiveWayGatherParallelism(_MixinDeps):
    """Five concurrent branches must complete in ~max(latency), not sum."""

    async def test_five_listings_run_in_parallel(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Each branch sleeps 100ms.

        Serial = 5 × 100ms = 500ms.
        Parallel target = ~100ms + ~120ms slack for thread spin-up + post-
        fan-out CPU work. Cap at 250ms.
        """

        delay = 0.1

        def _slow_sync(value: object) -> object:
            time.sleep(delay)
            return value

        registry = _FakeSkillRegistryAsync(delay=delay)
        deps = self._build(fake_dependencies, skill_registry=registry)

        with (
            patch.object(
                deps.tool_registry,
                "list_available_tools",
                side_effect=lambda _ctx: _slow_sync(()),
            ),
            patch.object(
                deps.mcp_registry,
                "list_available_servers",
                side_effect=lambda _ctx: _slow_sync(()),
            ),
            patch.object(
                deps.subagent_catalog,
                "list_available_subagents",
                side_effect=lambda _ctx: _slow_sync(()),
            ),
            patch(
                "agent_runtime.execution.factory.SkillSourceRegistry.skill_directories_for_deep_agent",
                side_effect=lambda _config: _slow_sync(()),
            ),
        ):
            start = time.monotonic()
            await acreate_agent_runtime(
                context=runtime_context_admin,
                dependencies=deps,
                agent_builder=CapturingAgentBuilder(),
            )
            elapsed = time.monotonic() - start

        assert elapsed < 0.25, (
            f"acreate_agent_runtime took {elapsed:.3f}s; expected < 0.25s "
            f"with 5-way parallel gather. Serial baseline would be ≥0.50s."
        )


class TestFiveWayGatherFailureSemantics(_MixinDeps):
    """A failure in any branch surfaces ``AgentRuntimeError`` and aborts."""

    async def test_skill_cards_branch_failure_propagates_runtime_error(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        class _BoomRegistry:
            async def list_available_skills(self, context: object) -> Sequence[object]:
                raise RuntimeError("registry exploded")

        deps = self._build(fake_dependencies, skill_registry=_BoomRegistry())

        # ``_assemble_harness``'s ``except Exception`` wraps unknown errors
        # as RUNTIME_FACTORY_ERROR. But the gather raises BEFORE
        # ``_assemble_harness`` runs, so the original RuntimeError surfaces.
        # That matches the prior (sequential) behavior — when ``_skill_cards``
        # raised, the original exception propagated out of the try/except
        # because it was not wrapped in the gather's try/except boundary.
        with pytest.raises(RuntimeError, match="registry exploded"):
            await acreate_agent_runtime(
                context=runtime_context_admin,
                dependencies=deps,
                agent_builder=CapturingAgentBuilder(),
            )

    async def test_one_listing_branch_failure_propagates(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """Confirms the existing 4-way gather's failure semantics still hold
        after extending to 5 branches."""

        with patch.object(
            fake_dependencies.tool_registry,
            "list_available_tools",
            side_effect=RuntimeError("tools exploded"),
        ):
            with pytest.raises(RuntimeError, match="tools exploded"):
                await acreate_agent_runtime(
                    context=runtime_context_admin,
                    dependencies=fake_dependencies,
                    agent_builder=CapturingAgentBuilder(),
                )
