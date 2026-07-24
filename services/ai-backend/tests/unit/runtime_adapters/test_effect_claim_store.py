from __future__ import annotations

import pytest

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimConflict,
    EffectClaimState,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore


def _claim(*, digest: str = "a" * 64) -> EffectClaim:
    return EffectClaim(
        org_id="org_test",
        run_id="run_test",
        stage_id="stg_123e4567-e89b-42d3-a456-426614174000",
        revision=1,
        idempotency_key="test-effect",
        executor=EffectExecutorKind.BUILTIN,
        proposal_digest=digest,
        target_digest="b" * 64,
        target_ref="artifact://org_test/target",
        proposal_ref="artifact://org_test/proposal",
        actor=EffectActor.USER,
        decision_ledger_id="rtest.1",
    )


@pytest.mark.asyncio
async def test_claim_is_idempotent_and_conflicting_request_fails_closed() -> None:
    store = InMemoryEffectClaimStore()
    first = _claim()

    acquired = await store.claim(claim=first)
    replay = await store.claim(claim=first.model_copy())

    assert acquired.created is True
    assert replay.created is False
    assert replay.claim.claim_id == first.claim_id
    with pytest.raises(EffectClaimConflict):
        await store.claim(claim=_claim(digest="c" * 64))


@pytest.mark.asyncio
async def test_claim_update_is_monotonic_and_incomplete_listing_is_scoped() -> None:
    store = InMemoryEffectClaimStore()
    claim = _claim()
    await store.claim(claim=claim)

    assert await store.list_incomplete(org_id="org_test") == (claim,)
    completed = claim.model_copy(
        update={"state": EffectClaimState.COMPLETED, "outcome": EffectOutcome.APPLIED}
    )
    assert await store.update(claim=completed) == completed
    assert await store.list_incomplete(org_id="org_test") == ()
