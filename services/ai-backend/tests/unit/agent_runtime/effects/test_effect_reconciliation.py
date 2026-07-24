from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.effects.claims import EffectClaim, EffectClaimState
from agent_runtime.effects.coordinator import EffectReconcileCommand
from agent_runtime.effects.reconciliation import EffectReconciliationScheduler
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore


@dataclass
class _Queue:
    commands: list[EffectReconcileCommand] = field(default_factory=list)

    async def enqueue_reconcile(self, command: EffectReconcileCommand) -> None:
        self.commands.append(command)


def _claim(*, claim_id: str, updated_at: str) -> EffectClaim:
    return EffectClaim(
        org_id="org_reconcile_test",
        run_id="run_reconcile_test",
        stage_id="stg_123e4567-e89b-42d3-a456-426614174000",
        revision=1,
        claim_id=claim_id,
        idempotency_key=f"effect-{claim_id}",
        executor=EffectExecutorKind.BUILTIN,
        proposal_digest="a" * 64,
        target_digest="b" * 64,
        target_ref="artifact://org_reconcile_test/target",
        proposal_ref="artifact://org_reconcile_test/proposal",
        actor=EffectActor.USER,
        decision_ledger_id="rtest·001",
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_only_quiet_incomplete_claims_are_enqueued() -> None:
    store = InMemoryEffectClaimStore()
    old = _claim(claim_id="clm_old", updated_at="2026-07-24T00:00:00+00:00")
    new = _claim(claim_id="clm_new", updated_at="2026-07-24T00:01:59+00:00")
    await store.claim(claim=old)
    await store.claim(claim=new)
    queue = _Queue()
    scheduler = EffectReconciliationScheduler(
        claims=store,
        queue=queue,
        minimum_age=timedelta(minutes=2),
    )

    scheduled = await scheduler.schedule_incomplete(
        now=datetime(2026, 7, 24, 0, 2, tzinfo=UTC)
    )

    assert scheduled == (
        EffectReconcileCommand(org_id=old.org_id, claim_id=old.claim_id),
    )
    assert queue.commands == list(scheduled)


@pytest.mark.asyncio
async def test_completed_claim_is_never_scheduled() -> None:
    store = InMemoryEffectClaimStore()
    claim = _claim(claim_id="clm_done", updated_at="2026-07-24T00:00:00+00:00")
    await store.claim(claim=claim)
    completed = EffectClaim.model_validate(
        {
            **claim.model_dump(mode="json"),
            "state": EffectClaimState.COMPLETED.value,
            "outcome": EffectOutcome.APPLIED.value,
            "updated_at": "2026-07-24T00:01:00+00:00",
        }
    )
    await store.update(claim=completed)
    queue = _Queue()

    scheduled = await EffectReconciliationScheduler(
        claims=store,
        queue=queue,
    ).schedule_incomplete(now=datetime(2026, 7, 24, 0, 5, tzinfo=UTC))

    assert scheduled == ()
    assert queue.commands == []
