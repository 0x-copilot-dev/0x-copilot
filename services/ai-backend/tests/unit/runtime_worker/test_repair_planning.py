"""Adversarial tests for the bounded, planning-only D12 worker job."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimScanCursor,
    EffectClaimState,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.surfaces_v2.ledger_models import EffectActor, EffectExecutorKind
from agent_runtime.surfaces_v2.repair_planning import (
    RepairPlanningSnapshotStore,
    build_repair_planning_snapshot,
)
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
from runtime_adapters.file.repair_planning_store import FileRepairPlanningSnapshotStore
from runtime_adapters.in_memory.repair_planning_store import (
    InMemoryRepairPlanningSnapshotStore,
)
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)
from runtime_worker.jobs.repair_planning import (
    EffectClaimRepairSnapshotCollector,
    RepairPlanningRunner,
)


pytestmark = pytest.mark.anyio

ORG = "org_repair_planning"
USER = "user_repair_planning"
RUN = "run_repair_planning"
CONVERSATION = "conv_repair_planning"
TRACE = "trace_repair_planning"
STAGE = "stg_123e4567-e89b-42d3-a456-426614174000"
NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run(**changes: object) -> RunRecord:
    values: dict[str, object] = {
        "run_id": RUN,
        "conversation_id": CONVERSATION,
        "org_id": ORG,
        "user_id": USER,
        "user_message_id": "msg_repair_planning",
        "trace_id": TRACE,
        "status": AgentRunStatus.COMPLETED,
        "model_provider": "openai",
        "model_name": "gpt-5.4-mini",
        "runtime_context": AgentRuntimeContext(
            user_id=USER,
            org_id=ORG,
            roles=["employee"],
            run_id=RUN,
            trace_id=TRACE,
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
        "latest_sequence_no": 1,
    }
    values.update(changes)
    return RunRecord.model_validate(values)


def _claim(**changes: object) -> EffectClaim:
    values: dict[str, object] = {
        "org_id": ORG,
        "run_id": RUN,
        "stage_id": STAGE,
        "revision": 1,
        "claim_id": "clm_repair_planning",
        "idempotency_key": "repair-planning-claim",
        "executor": EffectExecutorKind.WORKSPACE,
        "proposal_digest": "a" * 64,
        "target_digest": "b" * 64,
        "state": EffectClaimState.CLAIMED,
        "target_ref": "workspace-target://grant_1/path_token_1",
        "proposal_ref": f"proposal://{STAGE}/revisions/1",
        "proposal_content_ref": (
            "artifact://art_018f47a6-7b2c-7b10-8f21-12345678b002/revisions/1"
        ),
        "actor": EffectActor.USER,
        "decision_ledger_id": "rrepair.1",
        "created_at": (NOW - timedelta(minutes=10)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=5)).isoformat(),
    }
    values.update(changes)
    return EffectClaim.model_validate(values)


def _event(
    claim: EffectClaim,
    *,
    sequence_no: int = 1,
    run_id: str = RUN,
    conversation_id: str = CONVERSATION,
    payload: dict[str, object] | None = None,
) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        run_id=run_id,
        conversation_id=conversation_id,
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.EFFECT_CLAIMED,
        trace_id=TRACE,
        sequence_no=sequence_no,
        activity_kind=RuntimeActivityKind.EVENT,
        payload=(
            payload
            if payload is not None
            else {
                "v": 1,
                "stage_id": claim.stage_id,
                "revision": claim.revision,
                "claim_id": claim.claim_id,
                "executor": claim.executor.value,
                "attempt": claim.attempt,
            }
        ),
    )


@dataclass
class _Persistence:
    run: RunRecord | None
    requested: list[tuple[str, str]] = field(default_factory=list)

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        self.requested.append((org_id, run_id))
        return self.run


@dataclass
class _Events:
    events: Sequence[RuntimeEventEnvelope]
    requested: list[tuple[str, str, int]] = field(default_factory=list)
    dangerous_calls: int = 0

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[RuntimeEventEnvelope]:
        self.requested.append((org_id, run_id, after_sequence))
        return self.events

    async def append_event(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.dangerous_calls += 1
        raise AssertionError("repair planning must not append an event")


@dataclass
class _Claims:
    rows: Sequence[EffectClaim]
    requested_limits: list[int] = field(default_factory=list)
    scan_cursors: list[object | None] = field(default_factory=list)
    dangerous_calls: int = 0

    async def list_incomplete(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        assert org_id is None
        self.requested_limits.append(limit)
        return self.rows[:limit]

    async def list_incomplete_after(
        self, *, cursor: EffectClaimScanCursor | None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        self.scan_cursors.append(cursor)
        self.requested_limits.append(limit)
        ordered = sorted(self.rows, key=_claim_scan_key)
        if cursor is not None:
            after = (
                cursor.after_created_at,
                cursor.after_org_id,
                cursor.after_claim_id,
            )
            ordered = [claim for claim in ordered if _claim_scan_key(claim) > after]
        return tuple(ordered[:limit])

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        for claim in self.rows:
            if claim.org_id == org_id and claim.claim_id == claim_id:
                return claim
        return None

    async def update(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.dangerous_calls += 1
        raise AssertionError("repair planning must not update a claim")


@dataclass
class _Holds:
    state: RepairLegalHoldState
    calls: int = 0

    async def resolve(
        self, *, org_id: str, user_id: str, conversation_id: str
    ) -> RepairLegalHoldState:
        assert (org_id, user_id, conversation_id) == (ORG, USER, CONVERSATION)
        self.calls += 1
        return self.state


def _collector(
    *,
    claim: EffectClaim | None = None,
    run: RunRecord | None = None,
    events: Sequence[RuntimeEventEnvelope] | None = None,
    hold: RepairLegalHoldState = RepairLegalHoldState.NONE,
    claims: Sequence[EffectClaim] | None = None,
) -> tuple[EffectClaimRepairSnapshotCollector, _Claims, _Events]:
    selected = claim or _claim()
    selected_claims = tuple(claims if claims is not None else (selected,))
    default_events = tuple(
        _event(item, sequence_no=index)
        for index, item in enumerate(selected_claims, start=1)
    )
    effective_events = events if events is not None else default_events
    effective_run = (
        run if run is not None else _run(latest_sequence_no=len(effective_events))
    )
    claim_port = _Claims(rows=selected_claims)
    event_port = _Events(events=effective_events)
    return (
        EffectClaimRepairSnapshotCollector(
            persistence=_Persistence(run=effective_run),
            event_store=event_port,
            claims=claim_port,
            legal_holds=_Holds(state=hold),
            supported_reconcile_executors=frozenset({EffectExecutorKind.WORKSPACE}),
            max_events_per_run=10,
            quiet_period=timedelta(seconds=30),
        ),
        claim_port,
        event_port,
    )


def _claim_scan_key(claim: EffectClaim) -> tuple[datetime, str, str]:
    created_at = datetime.fromisoformat(claim.created_at)
    assert created_at.tzinfo is not None
    return (created_at.astimezone(UTC), claim.org_id, claim.claim_id)


async def test_terminal_trusted_claim_persists_candidate_without_an_effect() -> None:
    collector, claims, events = _collector()
    source_page = await collector.collect(limit=10, now=NOW)
    snapshots = source_page.snapshots
    store = InMemoryRepairPlanningSnapshotStore()
    result = await RepairPlanningRunner(
        collector=collector,
        snapshots=store,
        max_claims=10,
        page_size=10,
    ).run_once(now=NOW)

    assert len(snapshots) == 1
    assert result.candidates == 1
    assert result.withheld == result.failed == 0
    outcomes = await store.list_outcomes(
        tenant_id=ORG,
        snapshot_id=snapshots[0].snapshot_id,
    )
    assert [outcome.state for outcome in outcomes] == [RepairDecisionState.CANDIDATE]
    assert outcomes[0].candidate_id == _claim().claim_id
    assert claims.dangerous_calls == 0
    assert events.dangerous_calls == 0


async def test_execution_revalidation_reloads_a_live_hold_before_dispatch() -> None:
    claim = _claim()
    holds = _Holds(state=RepairLegalHoldState.NONE)
    claims = _Claims(rows=(claim,))
    events = _Events(events=(_event(claim),))
    collector = EffectClaimRepairSnapshotCollector(
        persistence=_Persistence(run=_run()),
        event_store=events,
        claims=claims,
        legal_holds=holds,
        supported_reconcile_executors=frozenset({EffectExecutorKind.WORKSPACE}),
        max_events_per_run=10,
        quiet_period=timedelta(seconds=30),
    )

    initial = await collector.collect(limit=10, now=NOW)
    initial_record = initial.snapshots[0].records[0]
    assert (
        RepairPlanner()
        .plan(
            RepairPlanningRequest(
                tenant_id=ORG,
                snapshot_id="initial_snapshot",
                as_of=NOW,
                records=(initial_record,),
            )
        )
        .decisions[0]
        .state
        is RepairDecisionState.CANDIDATE
    )

    holds.state = RepairLegalHoldState.ACTIVE
    revalidated = await collector.revalidate_effect_claim(
        tenant_id=ORG,
        claim_id=claim.claim_id,
        now=NOW,
    )

    assert revalidated is not None
    fresh = RepairPlanner.decide_record(revalidated.record)
    assert fresh.state is RepairDecisionState.WITHHELD
    assert "live_legal_hold" in {reason.value for reason in fresh.reasons}
    assert holds.calls == 2


async def test_execution_revalidation_withholds_when_the_reference_graph_changes() -> (
    None
):
    claim = _claim()
    holds = _Holds(state=RepairLegalHoldState.NONE)
    claims = _Claims(rows=(claim,))
    events = _Events(events=(_event(claim),))
    collector = EffectClaimRepairSnapshotCollector(
        persistence=_Persistence(run=_run()),
        event_store=events,
        claims=claims,
        legal_holds=holds,
        supported_reconcile_executors=frozenset({EffectExecutorKind.WORKSPACE}),
        max_events_per_run=10,
        quiet_period=timedelta(seconds=30),
    )

    events.events = (_event(claim, sequence_no=2),)
    revalidated = await collector.revalidate_effect_claim(
        tenant_id=ORG,
        claim_id=claim.claim_id,
        now=NOW,
    )

    assert revalidated is not None
    fresh = RepairPlanner.decide_record(revalidated.record)
    assert fresh.state is RepairDecisionState.WITHHELD
    assert "incomplete_graph" in {reason.value for reason in fresh.reasons}


@pytest.mark.parametrize(
    ("mutator", "expected_reason"),
    [
        (
            lambda claim: _collector(
                claim=claim,
                hold=RepairLegalHoldState.ACTIVE,
            ),
            "live_legal_hold",
        ),
        (
            lambda claim: _collector(
                claim=claim,
                run=_run(org_id="org_foreign"),
            ),
            "incomplete_graph",
        ),
        (
            lambda claim: _collector(
                claim=claim,
                events=(_event(claim, sequence_no=2),),
            ),
            "incomplete_graph",
        ),
        (
            lambda claim: _collector(
                claim=claim,
                events=(
                    _event(
                        claim,
                        payload={
                            "v": 1,
                            "stage_id": claim.stage_id,
                            "revision": claim.revision + 1,
                            "claim_id": claim.claim_id,
                            "executor": claim.executor.value,
                            "attempt": claim.attempt,
                        },
                    ),
                ),
            ),
            "missing_evidence",
        ),
        (
            lambda claim: _collector(
                claim=_claim(target_ref="future-target://opaque"),
            ),
            "unknown_reference_scheme",
        ),
    ],
)
async def test_incomplete_unknown_or_cross_tenant_facts_are_withheld(
    mutator,
    expected_reason: str,
) -> None:
    collector, _claims, _events = mutator(_claim())
    source_page = await collector.collect(limit=10, now=NOW)
    snapshots = source_page.snapshots
    snapshot = snapshots[0]
    store = InMemoryRepairPlanningSnapshotStore()
    runner = RepairPlanningRunner(
        collector=collector,
        snapshots=store,
        max_claims=10,
        page_size=10,
    )

    result = await runner.run_once(now=NOW)
    outcomes = await store.list_outcomes(
        tenant_id=ORG,
        snapshot_id=snapshot.snapshot_id,
    )

    assert result.candidates == 0
    assert result.withheld == 1
    assert outcomes[0].state is RepairDecisionState.WITHHELD
    assert expected_reason in {reason.value for reason in outcomes[0].reasons}
    rendered = str(outcomes[0].model_dump(mode="json"))
    assert "/" not in rendered
    assert "future-target" not in rendered
    assert "future-target" not in snapshot.model_dump_json()


async def test_bounded_claim_page_advances_without_treating_a_page_as_incomplete() -> (
    None
):
    first = _claim(claim_id="clm_repair_first", idempotency_key="repair-first")
    second = _claim(claim_id="clm_repair_second", idempotency_key="repair-second")
    collector, _claims, _events = _collector(claims=(first, second))
    source_page = await collector.collect(limit=1, now=NOW)
    snapshot = source_page.snapshots[0]

    assert snapshot.source_complete is True
    assert snapshot.records[0].graph_coverage is RepairGraphCoverage.COMPLETE
    assert source_page.advance_cursor is True
    assert source_page.next_cursor is not None
    store = InMemoryRepairPlanningSnapshotStore()
    result = await RepairPlanningRunner(
        collector=collector,
        snapshots=store,
        max_claims=1,
        page_size=1,
    ).run_once(now=NOW)
    outcomes = await store.list_outcomes(
        tenant_id=ORG,
        snapshot_id=snapshot.snapshot_id,
    )

    assert result.candidates == 1
    assert result.withheld == 0
    assert outcomes[0].state is RepairDecisionState.CANDIDATE


@pytest.mark.parametrize("backend", ["in_memory", "file"])
async def test_restart_resumes_keyset_claim_page_then_resets_after_exhaustion(
    backend: Literal["in_memory", "file"], tmp_path: Path
) -> None:
    first = _claim(claim_id="clm_repair_first", idempotency_key="repair-first")
    second = _claim(claim_id="clm_repair_second", idempotency_key="repair-second")
    collector, claims, events = _collector(claims=(first, second))
    first_page = await collector.collect(limit=1, now=NOW)
    assert first_page.next_cursor is not None
    assert first_page.snapshots[0].records[0].candidate_id == first.claim_id
    store: RepairPlanningSnapshotStore
    if backend == "file":
        store = FileRepairPlanningSnapshotStore(root=tmp_path)
    else:
        store = InMemoryRepairPlanningSnapshotStore()
    runner = RepairPlanningRunner(
        collector=collector,
        snapshots=store,
        max_claims=1,
        page_size=1,
    )

    first_cycle = await runner.run_once(now=NOW)
    assert first_cycle.candidates == 1
    assert await store.load_effect_claim_scan_cursor() == first_page.next_cursor
    if backend == "file":
        # Re-open durable state to prove the scan position is not process-local.
        store = FileRepairPlanningSnapshotStore(root=tmp_path)
        runner = RepairPlanningRunner(
            collector=collector,
            snapshots=store,
            max_claims=1,
            page_size=1,
        )

    second_cycle = await runner.run_once(now=NOW + timedelta(minutes=1))
    assert second_cycle.candidates == 1
    assert await store.load_effect_claim_scan_cursor() is None
    assert claims.scan_cursors[-1] == first_page.next_cursor
    assert events.dangerous_calls == claims.dangerous_calls == 0


def _snapshot_record(candidate_id: str) -> RepairSnapshotRecord:
    return RepairSnapshotRecord(
        candidate_id=candidate_id,
        tenant_id=ORG,
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


@pytest.mark.parametrize("backend", ["in_memory", "file"])
async def test_repeated_snapshot_facts_resume_original_as_of_and_cursor(
    backend: Literal["in_memory", "file"], tmp_path: Path
) -> None:
    first = build_repair_planning_snapshot(
        tenant_id=ORG,
        records=(_snapshot_record("candidate-a"), _snapshot_record("candidate-b")),
        source_complete=True,
        as_of=NOW,
    )
    repeated = build_repair_planning_snapshot(
        tenant_id=ORG,
        records=first.records,
        source_complete=True,
        as_of=NOW + timedelta(minutes=5),
    )
    assert repeated.snapshot_id == first.snapshot_id
    assert repeated.snapshot_digest == first.snapshot_digest
    assert repeated.as_of != first.as_of
    store: RepairPlanningSnapshotStore
    if backend == "file":
        store = FileRepairPlanningSnapshotStore(root=tmp_path)
    else:
        store = InMemoryRepairPlanningSnapshotStore()

    await store.load_or_create(snapshot=first)
    first_page = _plan(first, limit=1)
    assert await store.advance(
        tenant_id=ORG,
        snapshot_id=first.snapshot_id,
        expected_after_candidate_id=None,
        plan=first_page,
    )
    if backend == "file":
        # A new adapter instance proves the durable backend replays state,
        # rather than merely relying on the process-local object cache.
        store = FileRepairPlanningSnapshotStore(root=tmp_path)

    resumed = await store.load_or_create(snapshot=repeated)
    assert resumed.snapshot.as_of == first.as_of
    assert resumed.after_candidate_id == first_page.next_cursor.after_candidate_id
    assert resumed.completed is False
    second_page = _plan(repeated, cursor=resumed.cursor(), limit=1)
    assert await store.advance(
        tenant_id=ORG,
        snapshot_id=first.snapshot_id,
        expected_after_candidate_id=resumed.after_candidate_id,
        plan=second_page,
    )
    final = await store.load(tenant_id=ORG, snapshot_id=first.snapshot_id)
    assert final is not None and final.completed is True
    assert [
        item.candidate_id
        for item in await store.list_outcomes(
            tenant_id=ORG, snapshot_id=first.snapshot_id
        )
    ] == ["candidate-a", "candidate-b"]


async def test_runner_repeated_poll_is_idempotent_when_only_as_of_changes() -> None:
    collector, _claims, events = _collector()
    store = InMemoryRepairPlanningSnapshotStore()
    runner = RepairPlanningRunner(
        collector=collector,
        snapshots=store,
        max_claims=10,
        page_size=1,
    )

    first = await runner.run_once(now=NOW)
    source_page = await collector.collect(limit=10, now=NOW)
    snapshot_id = source_page.snapshots[0].snapshot_id
    persisted_after_first = await store.load(tenant_id=ORG, snapshot_id=snapshot_id)
    assert persisted_after_first is not None
    repeated = await runner.run_once(now=NOW + timedelta(minutes=1))
    persisted_after_repeat = await store.load(tenant_id=ORG, snapshot_id=snapshot_id)
    assert persisted_after_repeat is not None
    outcomes = await store.list_outcomes(
        tenant_id=ORG,
        snapshot_id=snapshot_id,
    )

    assert first.candidates == 1
    assert repeated == type(repeated)(snapshots=1)
    assert persisted_after_repeat.snapshot.as_of == persisted_after_first.snapshot.as_of
    assert persisted_after_repeat.snapshot.as_of == NOW
    assert len(outcomes) == 1
    assert events.dangerous_calls == 0
