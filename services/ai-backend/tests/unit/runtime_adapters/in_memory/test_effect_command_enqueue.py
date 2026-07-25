"""In-memory round trips for the A5 effect worker command envelopes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerResult
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    RuntimeEffectCommitCommand,
    RuntimeEffectReconcileCommand,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _commit_command() -> RuntimeEffectCommitCommand:
    return RuntimeEffectCommitCommand(
        command_id="effect-commit-1",
        org_id="org_acme",
        user_id="user_sarah",
        conversation_id="conv_1",
        run_id="run_1",
        stage_id="stg_00000000-0000-4000-8000-000000000001",
        revision=2,
        decision_ledger_id="r123.7",
        proposal_digest="a" * 64,
        target_digest="b" * 64,
        idempotency_key="effect-commit-1",
    )


def _reconcile_command() -> RuntimeEffectReconcileCommand:
    return RuntimeEffectReconcileCommand(
        command_id="effect-reconcile-1",
        org_id="org_acme",
        run_id="run_1",
        claim_id="clm_abc123",
    )


def _payload_without_queue_metadata(
    claim_payload: dict[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in claim_payload.items()
        if key != "command_type" and not (key == "approval_id" and value is None)
    }


class TestEffectCommandRoundtrip:
    async def test_commit_then_reconcile_round_trip_through_the_runtime_queue(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        commit = _commit_command()
        reconcile = _reconcile_command()

        await store.enqueue_effect_commit(commit)
        await store.enqueue_effect_reconcile(reconcile)

        assert store.effect_commit_commands == [commit]
        assert store.effect_reconcile_commands == [reconcile]

        claimed_commit = await store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert claimed_commit is not None
        assert claimed_commit.command_type == (
            PersistenceValues.EventType.EFFECT_COMMIT_REQUESTED
        )
        assert (
            RuntimeEffectCommitCommand.model_validate(
                _payload_without_queue_metadata(claimed_commit.payload)
            )
            == commit
        )

        await store.mark_complete(
            result=RuntimeWorkerResult(command_id=commit.command_id, succeeded=True)
        )
        claimed_reconcile = await store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert claimed_reconcile is not None
        assert claimed_reconcile.command_type == (
            PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED
        )
        assert (
            RuntimeEffectReconcileCommand.model_validate(
                _payload_without_queue_metadata(claimed_reconcile.payload)
            )
            == reconcile
        )
        assert {
            "user_id",
            "conversation_id",
            "stage_id",
            "proposal_ref",
            "target_ref",
        }.isdisjoint(claimed_reconcile.payload)
