"""Durability and atomicity tests for the file-backed A5 effect-claim store."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimAcquisition,
    EffectClaimConflict,
    EffectClaimStorageError,
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


_STAGE_ID = "stg_123e4567-e89b-42d3-a456-426614174000"
_PROPOSAL_REF = f"proposal://{_STAGE_ID}/revisions/1"
_PROPOSAL_CONTENT_REF = "artifact://org_test/proposals/revision-1"


def _claim(
    *,
    digest: str = "a" * 64,
    proposal_content_ref: str | None = _PROPOSAL_CONTENT_REF,
) -> EffectClaim:
    return EffectClaim(
        org_id="org_test",
        run_id="run_test",
        stage_id=_STAGE_ID,
        revision=1,
        idempotency_key="test-effect",
        executor=EffectExecutorKind.BUILTIN,
        proposal_digest=digest,
        target_digest="b" * 64,
        target_ref="artifact://org_test/target",
        proposal_ref=_PROPOSAL_REF,
        proposal_content_ref=proposal_content_ref,
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
    with pytest.raises(EffectClaimConflict):
        await store.claim(
            claim=_claim(
                proposal_content_ref="artifact://org_test/proposals/revision-2"
            )
        )


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


async def test_new_file_claim_persists_canonical_and_content_references(
    tmp_path,
) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    claim = _claim()

    await store.claim(claim=claim)

    path = store._path_for(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["proposal_ref"] == _PROPOSAL_REF
    assert persisted["proposal_content_ref"] == _PROPOSAL_CONTENT_REF


async def test_old_overloaded_file_claim_normalizes_to_split_references(
    tmp_path,
) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    claim = _claim()
    legacy = claim.model_dump(mode="json")
    legacy.pop("proposal_content_ref")
    legacy["proposal_ref"] = _PROPOSAL_CONTENT_REF
    path = store._path_for(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = await store.get(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )

    assert loaded is not None
    assert loaded.proposal_ref == _PROPOSAL_REF
    assert loaded.proposal_content_ref == _PROPOSAL_CONTENT_REF
    replay = await store.claim(claim=claim)
    assert replay.created is False
    assert replay.claim == loaded


async def test_old_canonical_only_file_claim_remains_readable_but_not_claimable(
    tmp_path,
) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    claim = _claim()
    legacy = claim.model_dump(mode="json")
    legacy.pop("proposal_content_ref")
    path = store._path_for(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = await store.get(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )
    assert loaded is not None
    assert loaded.proposal_content_ref is None
    with pytest.raises(EffectClaimStorageError):
        await store.claim(claim=loaded)


async def test_unsafe_old_overloaded_file_claim_fails_closed(tmp_path) -> None:
    store = FileEffectClaimStore(root=tmp_path)
    claim = _claim()
    legacy = claim.model_dump(mode="json")
    legacy.pop("proposal_content_ref")
    legacy["proposal_ref"] = "file:///private/tmp/proposal.json"
    path = store._path_for(
        org_id=claim.org_id,
        executor=claim.executor,
        idempotency_key=claim.idempotency_key,
    )
    path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(EffectClaimStorageError):
        await store.get(
            org_id=claim.org_id,
            executor=claim.executor,
            idempotency_key=claim.idempotency_key,
        )
