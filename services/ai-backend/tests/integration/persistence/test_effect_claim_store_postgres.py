"""Live Postgres contract tests for the A5 durable effect-claim adapter.

The effect coordinator must obtain this claim before it calls an external
executor.  These tests intentionally exercise the real unique key and row
lock rather than emulating them in a fake cursor.  They are destructive only
in their own randomly-scoped tenant rows and are skipped unless a disposable
cluster is provided.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
from runtime_adapters.postgres import (
    PostgresEffectClaimStore,
    PostgresRuntimeApiStore,
)


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("EFFECT_CLAIM_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set EFFECT_CLAIM_LIVE_TEST_DATABASE_URL to a disposable Postgres "
            "database to exercise the durable effect-claim adapter."
        ),
    ),
]

_BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def database_url() -> str:
    return os.environ["EFFECT_CLAIM_LIVE_TEST_DATABASE_URL"]


@pytest.fixture
async def runtime_store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=1,
        pool_max_size=8,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        yield store
    finally:
        await store.close()


@pytest.fixture
def claims(runtime_store: PostgresRuntimeApiStore) -> PostgresEffectClaimStore:
    return PostgresEffectClaimStore(runtime_store)


def _claim(
    *,
    org_id: str,
    idempotency_key: str,
    digest: str = "a" * 64,
    offset: int = 0,
) -> EffectClaim:
    timestamp = (_BASE_TIME + timedelta(seconds=offset)).isoformat()
    return EffectClaim(
        org_id=org_id,
        run_id=f"run_{uuid4().hex}",
        stage_id="stg_123e4567-e89b-42d3-a456-426614174000",
        revision=1,
        idempotency_key=idempotency_key,
        executor=EffectExecutorKind.BUILTIN,
        proposal_digest=digest,
        target_digest="b" * 64,
        target_ref=f"artifact://{org_id}/target",
        proposal_ref=f"artifact://{org_id}/proposal",
        actor=EffectActor.USER,
        decision_ledger_id="rtest.1",
        created_at=timestamp,
        updated_at=timestamp,
    )


class TestPostgresEffectClaimStore:
    async def test_atomic_idempotent_claim_returns_one_durable_winner(
        self, claims: PostgresEffectClaimStore
    ) -> None:
        org_id = f"org_effect_claim_{uuid4().hex}"
        proposed = _claim(org_id=org_id, idempotency_key="atomic-effect")

        acquired = await asyncio.gather(
            *(claims.claim(claim=proposed.model_copy()) for _ in range(12))
        )

        assert sum(item.created for item in acquired) == 1
        assert {item.claim.claim_id for item in acquired} == {proposed.claim_id}
        loaded = await claims.get(
            org_id=org_id,
            executor=EffectExecutorKind.BUILTIN,
            idempotency_key="atomic-effect",
        )
        assert loaded == proposed

    async def test_changed_request_under_same_key_is_a_conflict(
        self, claims: PostgresEffectClaimStore
    ) -> None:
        org_id = f"org_effect_claim_{uuid4().hex}"
        await claims.claim(
            claim=_claim(org_id=org_id, idempotency_key="conflicting-effect")
        )

        with pytest.raises(EffectClaimConflict):
            await claims.claim(
                claim=_claim(
                    org_id=org_id,
                    idempotency_key="conflicting-effect",
                    digest="c" * 64,
                )
            )

    async def test_update_and_incomplete_listing_follow_claim_contract(
        self, claims: PostgresEffectClaimStore
    ) -> None:
        org_id = f"org_effect_claim_{uuid4().hex}"
        completed = _claim(
            org_id=org_id,
            idempotency_key="completed-effect",
            offset=1,
        )
        unresolved = _claim(
            org_id=org_id,
            idempotency_key="unresolved-effect",
            offset=2,
        )
        await claims.claim(claim=completed)
        await claims.claim(claim=unresolved)

        completed_result = completed.model_copy(
            update={
                "state": EffectClaimState.COMPLETED,
                "outcome": EffectOutcome.APPLIED,
                "updated_at": (_BASE_TIME + timedelta(seconds=3)).isoformat(),
            }
        )
        stored_completed = await claims.update(claim=completed_result)
        assert stored_completed == completed_result

        indeterminate = unresolved.model_copy(
            update={
                "state": EffectClaimState.INDETERMINATE,
                "outcome": EffectOutcome.INDETERMINATE,
                "updated_at": (_BASE_TIME + timedelta(seconds=4)).isoformat(),
            }
        )
        stored_indeterminate = await claims.update(claim=indeterminate)
        assert stored_indeterminate == indeterminate

        incomplete = await claims.list_incomplete(org_id=org_id)
        assert incomplete == (indeterminate,)
        assert (
            await claims.get_by_claim_id(org_id=org_id, claim_id=completed.claim_id)
            == completed_result
        )
        assert await claims.list_incomplete(org_id=org_id, limit=0) == ()
