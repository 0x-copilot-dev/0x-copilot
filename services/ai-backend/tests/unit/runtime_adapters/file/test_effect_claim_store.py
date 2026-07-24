"""Durability and atomicity tests for the file-backed A5 effect-claim store."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimAcquisition,
    EffectClaimConflict,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectExecutorKind,
)
from runtime_adapters.file.effect_claim_store import FileEffectClaimStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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


async def test_claim_is_idempotent_after_durable_create(tmp_path) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    first = _claim()

    acquired = await store.claim(claim=first)
    replay = await store.claim(claim=first.model_copy())

    assert acquired.created is True
    assert replay.created is False
    assert replay.claim == first


async def test_changed_digest_reusing_key_fails_closed(tmp_path) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    await store.claim(claim=_claim())

    with pytest.raises(EffectClaimConflict):
        await store.claim(claim=_claim(digest="c" * 64))


async def test_concurrent_adapter_instances_have_one_claim_winner(tmp_path) -> None:
    barrier = threading.Barrier(16)

    def claim_from_a_separate_caller() -> EffectClaimAcquisition:
        barrier.wait()
        return asyncio.run(FileEffectClaimStore(root=tmp_path).claim(claim=_claim()))

    claims = await asyncio.gather(
        *(asyncio.to_thread(claim_from_a_separate_caller) for _ in range(16))
    )

    assert sum(acquired.created for acquired in claims) == 1
    assert {acquired.claim.claim_id for acquired in claims} == {
        claims[0].claim.claim_id
    }


async def test_claim_survives_restart_with_same_identity(tmp_path) -> None:
    first = FileEffectClaimStore(root=tmp_path)
    original = _claim()
    assert (await first.claim(claim=original)).created is True

    restarted = FileEffectClaimStore(root=tmp_path)
    replay = await restarted.claim(claim=original.model_copy())
    loaded = await restarted.get(
        org_id=original.org_id,
        executor=original.executor,
        idempotency_key=original.idempotency_key,
    )

    assert replay.created is False
    assert replay.claim == original
    assert loaded == original
