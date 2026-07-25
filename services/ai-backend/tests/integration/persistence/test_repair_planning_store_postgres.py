"""Live Postgres parity checks for the D12 planning-only snapshot adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_runtime.surfaces_v2.repair_planning import build_repair_planning_snapshot
from agent_runtime.effects.claims import EffectClaimScanCursor
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairCandidateKind,
    RepairDecisionState,
    RepairEffectState,
    RepairEvidenceState,
    RepairGraphCoverage,
    RepairLegalHoldState,
    RepairOwnerState,
    RepairPlanner,
    RepairPlanningRequest,
    RepairSnapshotRecord,
)
from runtime_adapters.postgres.repair_planning_store import (
    PostgresRepairPlanningSnapshotStore,
)
from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("REPAIR_PLANNING_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set REPAIR_PLANNING_LIVE_TEST_DATABASE_URL to a disposable Postgres "
            "database to exercise the D12 durable planning-state adapter."
        ),
    ),
]


@pytest.fixture
def database_url() -> str:
    return os.environ["REPAIR_PLANNING_LIVE_TEST_DATABASE_URL"]


@pytest.fixture
async def runtime_store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=1,
        pool_max_size=4,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        yield store
    finally:
        await store.close()


def _record(*, tenant_id: str, candidate_id: str) -> RepairSnapshotRecord:
    return RepairSnapshotRecord(
        candidate_id=candidate_id,
        tenant_id=tenant_id,
        kind=RepairCandidateKind.EFFECT_RECONCILIATION,
        reference_scheme="workspace-target",
        graph_coverage=RepairGraphCoverage.COMPLETE,
        legal_hold=RepairLegalHoldState.NONE,
        evidence_state=RepairEvidenceState.VERIFIED,
        evidence_id=f"evidence_{candidate_id}",
        owner_state=RepairOwnerState.TERMINAL,
        effect_state=RepairEffectState.CLAIMED,
        reconcile_supported=True,
        quiet_period_elapsed=True,
    )


def _plan(snapshot, *, cursor=None, limit: int = 1):  # noqa: ANN001
    return RepairPlanner().plan(
        RepairPlanningRequest(
            tenant_id=snapshot.tenant_id,
            snapshot_id=snapshot.snapshot_id,
            as_of=snapshot.as_of,
            records=snapshot.records,
            cursor=cursor,
            limit=limit,
        )
    )


async def test_postgres_snapshot_replay_preserves_cursor_and_original_as_of(
    runtime_store: PostgresRuntimeApiStore,
) -> None:
    tenant_id = f"org_repair_planning_{uuid4().hex}"
    as_of = datetime(2026, 7, 25, 12, tzinfo=UTC)
    original = build_repair_planning_snapshot(
        tenant_id=tenant_id,
        records=(
            _record(tenant_id=tenant_id, candidate_id="candidate-a"),
            _record(tenant_id=tenant_id, candidate_id="candidate-b"),
        ),
        source_complete=True,
        as_of=as_of,
    )
    repeated = build_repair_planning_snapshot(
        tenant_id=tenant_id,
        records=original.records,
        source_complete=True,
        as_of=as_of + timedelta(minutes=5),
    )
    first_store = PostgresRepairPlanningSnapshotStore(store=runtime_store)
    first_state = await first_store.load_or_create(snapshot=original)
    first_page = _plan(first_state.snapshot, limit=1)

    assert await first_store.advance(
        tenant_id=tenant_id,
        snapshot_id=original.snapshot_id,
        expected_after_candidate_id=None,
        plan=first_page,
    )

    # A new adapter instance models a worker restart. A repeat poll with only
    # a new observation timestamp must resume, not conflict or duplicate.
    restarted = PostgresRepairPlanningSnapshotStore(store=runtime_store)
    resumed = await restarted.load_or_create(snapshot=repeated)
    assert resumed.snapshot.as_of == original.as_of
    assert resumed.after_candidate_id == first_page.next_cursor.after_candidate_id
    second_page = _plan(resumed.snapshot, cursor=resumed.cursor(), limit=1)
    assert await restarted.advance(
        tenant_id=tenant_id,
        snapshot_id=original.snapshot_id,
        expected_after_candidate_id=resumed.after_candidate_id,
        plan=second_page,
    )
    assert not await restarted.advance(
        tenant_id=tenant_id,
        snapshot_id=original.snapshot_id,
        expected_after_candidate_id=resumed.after_candidate_id,
        plan=second_page,
    )
    outcomes = await restarted.list_outcomes(
        tenant_id=tenant_id,
        snapshot_id=original.snapshot_id,
    )
    assert [item.state for item in outcomes] == [
        RepairDecisionState.CANDIDATE,
        RepairDecisionState.CANDIDATE,
    ]

    scan_cursor = EffectClaimScanCursor(
        after_created_at=as_of,
        after_org_id=tenant_id,
        after_claim_id="clm_repair_scan",
    )
    assert await first_store.advance_effect_claim_scan_cursor(
        expected=None,
        next_cursor=scan_cursor,
    )
    assert await restarted.load_effect_claim_scan_cursor() == scan_cursor
    assert not await restarted.advance_effect_claim_scan_cursor(
        expected=None,
        next_cursor=None,
    )
    assert await restarted.advance_effect_claim_scan_cursor(
        expected=scan_cursor,
        next_cursor=None,
    )
