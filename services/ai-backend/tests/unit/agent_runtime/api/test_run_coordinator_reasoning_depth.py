"""D1 — a run that omits ``reasoning_depth`` is seeded from the workspace default.

Closes the "stored-but-unconsumed" gap: the persisted
``behavior_overrides.default_reasoning_depth`` (canonical fast/balanced/deep, or
the legacy low/medium/high reconciled into it) governs runs that omit the
per-turn depth, while an explicit per-turn pick (composer / agent / routine)
always wins. Reuses the BYOK coordinator harness (in-memory store, a user key
satisfies the credential gate) so a real run seals and enqueues, exercising the
DepthBudgetTable scaling end-to-end.
"""

from __future__ import annotations

from runtime_api.schemas import (
    CreateRunRequest,
    WorkspaceBehaviorOverrides,
    WorkspaceDefaultsRecord,
)
from tests.unit.agent_runtime.api.test_run_coordinator_byok import (
    _ORG_ID,
    _USER_ID,
    ByokCoordinatorMixin,
)


def _run_request(
    conversation_id: str, *, reasoning_depth: str | None
) -> CreateRunRequest:
    kwargs: dict[str, object] = {
        "conversation_id": conversation_id,
        "org_id": _ORG_ID,
        "user_id": _USER_ID,
        "user_input": "hello",
        "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
    }
    if reasoning_depth is not None:
        kwargs["reasoning_depth"] = reasoning_depth
    return CreateRunRequest(**kwargs)


async def _seed_default_depth(store, *, blob: dict[str, object]) -> None:
    """Persist a workspace ``behavior_overrides`` blob for the harness org."""
    await store.upsert_workspace_defaults(
        record=WorkspaceDefaultsRecord(
            org_id=_ORG_ID,
            behavior_overrides=WorkspaceBehaviorOverrides.model_validate(blob),
        )
    )


class TestReasoningDepthDefaultSeeding(ByokCoordinatorMixin):
    async def test_omitted_uses_no_default_when_unset(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        await run_coordinator.create_run(
            _run_request(conversation_id, reasoning_depth=None)
        )

        # No workspace default + no per-turn pick == Auto: the runtime baseline,
        # so ``model_profile`` carries no depth (DepthBudgetTable no-op).
        profile = store.run_commands[0].runtime_context.model_profile
        assert profile.reasoning_depth is None

    async def test_omitted_seeds_persisted_deep_and_scales_budgets(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)

        # Baseline run (no default) captures the un-scaled budgets.
        await run_coordinator.create_run(
            _run_request(conversation_id, reasoning_depth=None)
        )
        baseline = store.run_commands[0].runtime_context.model_profile

        await _seed_default_depth(store, blob={"default_reasoning_depth": "deep"})
        await run_coordinator.create_run(
            _run_request(conversation_id, reasoning_depth=None)
        )
        seeded = store.run_commands[1].runtime_context.model_profile

        assert seeded.reasoning_depth == "deep"
        # Deep = 2.0x timeout / 2.0x tool-call budget over the baseline.
        assert seeded.timeout_seconds > baseline.timeout_seconds
        assert seeded.tool_call_budget > baseline.tool_call_budget

    async def test_explicit_per_turn_overrides_workspace_default(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)
        await _seed_default_depth(store, blob={"default_reasoning_depth": "deep"})

        await run_coordinator.create_run(
            _run_request(conversation_id, reasoning_depth="fast")
        )

        # The per-turn 'fast' wins over the workspace 'deep' — agents/routines
        # (which always carry a concrete depth) are protected by this guard too.
        assert store.run_commands[0].runtime_context.model_profile.reasoning_depth == (
            "fast"
        )

    async def test_legacy_effort_default_reconciles_to_depth(self) -> None:
        run_coordinator, store, conversation_id = await self._build(with_key=True)
        # A row written by the still-live web panel carries the legacy effort
        # key only; it must be consumed as a depth at run-create.
        await _seed_default_depth(store, blob={"default_reasoning_effort": "high"})

        await run_coordinator.create_run(
            _run_request(conversation_id, reasoning_depth=None)
        )

        assert store.run_commands[0].runtime_context.model_profile.reasoning_depth == (
            "deep"
        )
