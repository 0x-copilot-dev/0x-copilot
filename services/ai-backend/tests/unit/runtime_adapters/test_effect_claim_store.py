from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimConflict,
    EffectClaimScanCursor,
    EffectClaimState,
    EffectClaimStorageError,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore


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
    with pytest.raises(EffectClaimConflict):
        await store.claim(
            claim=_claim(
                proposal_content_ref="artifact://org_test/proposals/revision-2"
            )
        )


@pytest.mark.asyncio
async def test_new_claim_requires_immutable_content_reference() -> None:
    store = InMemoryEffectClaimStore()

    with pytest.raises(EffectClaimStorageError):
        await store.claim(claim=_claim(proposal_content_ref=None))


@pytest.mark.parametrize(
    "proposal_content_ref",
    (
        "/private/tmp/proposal.json",
        "file:///private/tmp/proposal.json",
        "filesystem://local/private/tmp/proposal.json",
        "data:text/plain,proposal",
        "https://untrusted.example/proposal.json",
        _PROPOSAL_REF,
        "artifact://org_test/../proposal",
        "artifact://org_test/%2e%2e/proposal",
        "artifact://org_test/%252e%252e/proposal",
        "artifact://org_test/%2525252525252525252e%2525252525252525252e/proposal",
        "artifact://org_test/..%5cproposal",
        "artifact://org_test/%2Fetc/passwd",
        "artifact://org_test/proposal?path=../other",
        "artifact://org_test/proposal#../other",
    ),
)
def test_claim_content_reference_rejects_paths_and_traversal(
    proposal_content_ref: str,
) -> None:
    with pytest.raises(ValidationError):
        _claim(proposal_content_ref=proposal_content_ref)


def test_legacy_overloaded_claim_input_normalizes_before_persistence() -> None:
    legacy = _claim().model_dump(mode="json")
    legacy.pop("proposal_content_ref")
    legacy["proposal_ref"] = _PROPOSAL_CONTENT_REF

    normalized = EffectClaim.model_validate(legacy)

    assert normalized.proposal_ref == _PROPOSAL_REF
    assert normalized.proposal_content_ref == _PROPOSAL_CONTENT_REF


def test_explicit_new_claim_shape_requires_canonical_proposal_identity() -> None:
    legacy = _claim().model_dump(mode="json")
    legacy["proposal_ref"] = _PROPOSAL_CONTENT_REF

    with pytest.raises(ValidationError):
        EffectClaim.model_validate(legacy)


def test_completed_rowset_claim_requires_exact_outcome_coverage() -> None:
    claimed = _claim().model_copy(update={"row_keys": ("row-a", "row-b")})
    payload = claimed.model_dump(mode="json")
    payload.update(
        {
            "state": "completed",
            "outcome": "partial",
            "row_results": [{"row_key": "row-a", "outcome": "failed"}],
        }
    )

    with pytest.raises(ValidationError):
        EffectClaim.model_validate(payload)


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


@pytest.mark.asyncio
async def test_global_incomplete_keyset_scan_orders_across_tenants() -> None:
    store = InMemoryEffectClaimStore()
    created = datetime(2026, 7, 25, tzinfo=UTC)
    first = _claim().model_copy(
        update={
            "org_id": "org_a",
            "claim_id": "clm_scan_a",
            "idempotency_key": "scan-a",
            "created_at": created.isoformat(),
        }
    )
    second = _claim().model_copy(
        update={
            "org_id": "org_b",
            "claim_id": "clm_scan_b",
            "idempotency_key": "scan-b",
            "created_at": created.isoformat(),
        }
    )
    third = _claim().model_copy(
        update={
            "org_id": "org_a",
            "claim_id": "clm_scan_c",
            "idempotency_key": "scan-c",
            "created_at": (created + timedelta(seconds=1)).isoformat(),
        }
    )
    for claim in (third, second, first):
        await store.claim(claim=claim)

    first_page = await store.list_incomplete_after(cursor=None, limit=2)
    assert [claim.claim_id for claim in first_page] == ["clm_scan_a", "clm_scan_b"]
    cursor = EffectClaimScanCursor(
        after_created_at=created,
        after_org_id="org_b",
        after_claim_id="clm_scan_b",
    )
    second_page = await store.list_incomplete_after(cursor=cursor, limit=2)

    assert [claim.claim_id for claim in second_page] == ["clm_scan_c"]
