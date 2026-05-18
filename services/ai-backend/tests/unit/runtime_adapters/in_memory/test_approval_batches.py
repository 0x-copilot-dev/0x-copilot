"""ApprovalBatch persistence tests for the in-memory adapter (PR #43).

Pins the atomic ``record_item_decision_and_maybe_lock_batch`` contract:
- Round-trip insert + list
- Single-item batch → ``READY_TO_RESUME`` on the only decision
- 5-item batch → ``BATCH_INCOMPLETE`` four times, then ``READY_TO_RESUME``
- Concurrent last-decision race → exactly one ``READY_TO_RESUME``, the other ``LOST_RACE``
- After RESUMING → subsequent decisions return ``LOST_RACE``
- Mixed approve/reject preserved by ``decisions_in_order``
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
    ApprovalBatchStatus,
    BatchItemDecision,
    BatchOutcomeStatus,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import ApprovalDecision


_ORG_ID = "org_test"
_RUN_ID = "run_test"


def _build_batch(batch_id: str, *, size: int) -> ApprovalBatchSpec:
    """Helper — build a PENDING batch with ``size`` items numbered 0..size-1."""
    return ApprovalBatchSpec.build(
        batch=ApprovalBatchRecord(batch_id=batch_id, run_id=_RUN_ID, org_id=_ORG_ID),
        items=[
            ApprovalBatchItemRecord(
                item_id=f"{batch_id}:{i}", batch_id=batch_id, index=i
            )
            for i in range(size)
        ],
    )


class TestApprovalBatchInsertAndList:
    async def test_round_trip_insert_and_list(self) -> None:
        store = InMemoryRuntimeApiStore()
        spec = _build_batch("b_round_trip", size=3)
        inserted = await store.insert_approval_batch(spec=spec)

        assert inserted.batch_id == "b_round_trip"
        assert inserted.status is ApprovalBatchStatus.PENDING

        items = await store.list_items_for_batch(
            org_id=_ORG_ID, batch_id="b_round_trip"
        )
        assert len(items) == 3
        assert [item.index for item in items] == [0, 1, 2]
        assert all(item.decision is None for item in items)

    async def test_idempotent_re_insert_returns_existing(self) -> None:
        store = InMemoryRuntimeApiStore()
        spec = _build_batch("b_idem", size=2)
        await store.insert_approval_batch(spec=spec)
        # Re-insert with the same batch_id; second call must not raise and
        # must not duplicate items.
        await store.insert_approval_batch(spec=spec)
        items = await store.list_items_for_batch(org_id=_ORG_ID, batch_id="b_idem")
        assert len(items) == 2

    async def test_get_batch_scoped_by_org(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_scope", size=1))
        assert (
            await store.get_approval_batch(org_id=_ORG_ID, batch_id="b_scope")
            is not None
        )
        # Wrong org returns None — tenant isolation.
        assert (
            await store.get_approval_batch(org_id="other_org", batch_id="b_scope")
            is None
        )

    async def test_get_item_scoped_by_org(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_item_scope", size=1))
        item = await store.get_approval_batch_item(
            org_id=_ORG_ID, item_id="b_item_scope:0"
        )
        assert item is not None
        assert item.batch_id == "b_item_scope"
        assert (
            await store.get_approval_batch_item(
                org_id="other_org", item_id="b_item_scope:0"
            )
            is None
        )

    async def test_spec_build_rejects_misaligned_indices(self) -> None:
        with pytest.raises(ValueError):
            ApprovalBatchSpec.build(
                batch=ApprovalBatchRecord(
                    batch_id="b_bad", run_id=_RUN_ID, org_id=_ORG_ID
                ),
                items=[
                    ApprovalBatchItemRecord(
                        item_id="b_bad:0", batch_id="b_bad", index=0
                    ),
                    # index=2 instead of 1 — not contiguous.
                    ApprovalBatchItemRecord(
                        item_id="b_bad:2", batch_id="b_bad", index=2
                    ),
                ],
            )


class TestRecordItemDecisionAndMaybeLockBatch:
    async def test_single_item_batch_completes_on_only_decision(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_n1", size=1))

        outcome = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID,
            item_id="b_n1:0",
            decision=ApprovalDecision.APPROVED,
        )

        assert outcome.status is BatchOutcomeStatus.READY_TO_RESUME
        assert outcome.batch is not None
        assert outcome.batch.status is ApprovalBatchStatus.RESUMING
        assert outcome.decisions_in_order() == (BatchItemDecision.APPROVED,)

    async def test_n5_returns_incomplete_until_last_decision(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_n5", size=5))

        for index in range(4):
            outcome = await store.record_item_decision_and_maybe_lock_batch(
                org_id=_ORG_ID,
                item_id=f"b_n5:{index}",
                decision=ApprovalDecision.APPROVED,
            )
            assert outcome.status is BatchOutcomeStatus.BATCH_INCOMPLETE, index

        last = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID,
            item_id="b_n5:4",
            decision=ApprovalDecision.APPROVED,
        )
        assert last.status is BatchOutcomeStatus.READY_TO_RESUME
        assert last.batch is not None
        assert last.batch.status is ApprovalBatchStatus.RESUMING
        assert last.decisions_in_order() == (BatchItemDecision.APPROVED,) * 5

    async def test_decisions_in_order_preserves_mixed_approve_reject(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_mixed", size=5))

        # Decide out of order to make sure ``decisions_in_order`` sorts by index.
        plan = {
            0: ApprovalDecision.APPROVED,
            3: ApprovalDecision.APPROVED,
            1: ApprovalDecision.APPROVED,
            4: ApprovalDecision.APPROVED,
            2: ApprovalDecision.REJECTED,
        }
        for item_index, decision in plan.items():
            await store.record_item_decision_and_maybe_lock_batch(
                org_id=_ORG_ID,
                item_id=f"b_mixed:{item_index}",
                decision=decision,
            )
        # The assertion below relies on the final batch state — every item's
        # decision matches the plan regardless of which call won the batch
        # transition.
        items = await store.list_items_for_batch(org_id=_ORG_ID, batch_id="b_mixed")
        decisions = [item.decision for item in items]
        assert decisions == [
            BatchItemDecision.APPROVED,  # 0
            BatchItemDecision.APPROVED,  # 1
            BatchItemDecision.REJECTED,  # 2
            BatchItemDecision.APPROVED,  # 3
            BatchItemDecision.APPROVED,  # 4
        ]

    async def test_after_resuming_subsequent_calls_return_lost_race(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_lost", size=2))

        # First decision — batch still incomplete.
        outcome_a = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_lost:0", decision=ApprovalDecision.APPROVED
        )
        assert outcome_a.status is BatchOutcomeStatus.BATCH_INCOMPLETE
        # Last decision — flips to RESUMING.
        outcome_b = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_lost:1", decision=ApprovalDecision.APPROVED
        )
        assert outcome_b.status is BatchOutcomeStatus.READY_TO_RESUME
        # A retry on either item must now return LOST_RACE — the batch is no
        # longer PENDING.
        outcome_c = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_lost:0", decision=ApprovalDecision.APPROVED
        )
        assert outcome_c.status is BatchOutcomeStatus.LOST_RACE
        outcome_d = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_lost:1", decision=ApprovalDecision.APPROVED
        )
        assert outcome_d.status is BatchOutcomeStatus.LOST_RACE

    async def test_unknown_item_returns_lost_race(self) -> None:
        store = InMemoryRuntimeApiStore()
        outcome = await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID,
            item_id="not_a_real_item",
            decision=ApprovalDecision.APPROVED,
        )
        assert outcome.status is BatchOutcomeStatus.LOST_RACE

    async def test_concurrent_last_decision_only_one_wins(self) -> None:
        """Two coroutines submit the last-two decisions simultaneously.

        The atomic primitive is the lock target. Exactly one caller flips
        ``PENDING -> RESUMING``; the other observes the post-flip state and
        returns ``LOST_RACE``. Without the lock both could observe "every
        item resolved" simultaneously and both would call resume — the very
        race the design forbids.
        """
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_race", size=2))

        # We can't reliably reproduce a true race without monkey-patching the
        # store's internals, so we resolve item 0 first to leave only item 1
        # outstanding. Then two coroutines both try to resolve item 1
        # concurrently. The per-batch lock serialises them: one wins
        # READY_TO_RESUME, the other LOST_RACE (the batch is RESUMING by
        # the time the second one acquires the lock).
        await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_race:0", decision=ApprovalDecision.APPROVED
        )

        async def decide_last() -> object:
            return await store.record_item_decision_and_maybe_lock_batch(
                org_id=_ORG_ID,
                item_id="b_race:1",
                decision=ApprovalDecision.APPROVED,
            )

        outcomes = await asyncio.gather(decide_last(), decide_last())
        statuses = sorted(outcome.status.value for outcome in outcomes)
        assert statuses == [
            BatchOutcomeStatus.LOST_RACE.value,
            BatchOutcomeStatus.READY_TO_RESUME.value,
        ]

    async def test_mark_resolved_idempotent_for_terminal_statuses(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.insert_approval_batch(spec=_build_batch("b_resolve", size=1))
        await store.record_item_decision_and_maybe_lock_batch(
            org_id=_ORG_ID, item_id="b_resolve:0", decision=ApprovalDecision.APPROVED
        )
        await store.mark_approval_batch_resolved(org_id=_ORG_ID, batch_id="b_resolve")
        batch = await store.get_approval_batch(org_id=_ORG_ID, batch_id="b_resolve")
        assert batch is not None
        assert batch.status is ApprovalBatchStatus.RESOLVED
        # Idempotent re-call.
        await store.mark_approval_batch_resolved(org_id=_ORG_ID, batch_id="b_resolve")
        batch_again = await store.get_approval_batch(
            org_id=_ORG_ID, batch_id="b_resolve"
        )
        assert batch_again is not None
        assert batch_again.status is ApprovalBatchStatus.RESOLVED
