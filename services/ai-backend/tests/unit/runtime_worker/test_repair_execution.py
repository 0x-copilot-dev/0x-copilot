"""Adversarial coverage for executable, fail-closed D12 reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agent_runtime.effects.claims import EffectClaim, EffectClaimState
from agent_runtime.surfaces_v2.ledger_models import EffectActor, EffectExecutorKind
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme
from agent_runtime.surfaces_v2.repair_planning import build_repair_planning_snapshot
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairAction,
    RepairCandidateKind,
    RepairDecision,
    RepairDecisionState,
    RepairEffectState,
    RepairEvidenceState,
    RepairGraphCoverage,
    RepairLegalHoldState,
    RepairOwnerState,
    RepairReasonCode,
    RepairSnapshotRecord,
)
from runtime_adapters.in_memory.repair_planning_store import (
    InMemoryRepairPlanningSnapshotStore,
)
from runtime_api.schemas import RuntimeEffectReconcileCommand
from runtime_worker.jobs.repair_execution import (
    RepairExecutionEnv,
    RepairReconciliationExecutor,
    build_reconcile_command,
)
from runtime_worker.jobs.repair_planning import (
    EffectClaimRepairRevalidation,
    RepairExecutionResult,
    RepairPlanningRunner,
    RepairPlanningSourcePage,
)
from agent_runtime.effects.claims import EffectClaimScanCursor


pytestmark = pytest.mark.anyio

ORG = "org_repair_execution"
RUN = "run_repair_execution"
CLAIM = "clm_repair_execution"
STAGE = "stg_123e4567-e89b-42d3-a456-426614174000"
NOW = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _claim(**changes: object) -> EffectClaim:
    values: dict[str, object] = {
        "org_id": ORG,
        "run_id": RUN,
        "stage_id": STAGE,
        "revision": 1,
        "claim_id": CLAIM,
        "idempotency_key": "repair-execution-effect",
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
        "decision_ledger_id": "rrepair.001",
        "created_at": "2026-07-26T08:00:00+00:00",
        "updated_at": "2026-07-26T08:30:00+00:00",
    }
    values.update(changes)
    return EffectClaim.model_validate(values)


def _record(**changes: object) -> RepairSnapshotRecord:
    values: dict[str, object] = {
        "candidate_id": CLAIM,
        "tenant_id": ORG,
        "kind": RepairCandidateKind.EFFECT_RECONCILIATION,
        "reference_scheme": LifecycleReferenceScheme.WORKSPACE_TARGET.value,
        "graph_coverage": RepairGraphCoverage.COMPLETE,
        "legal_hold": RepairLegalHoldState.NONE,
        "evidence_state": RepairEvidenceState.VERIFIED,
        "evidence_id": "rev_repair_execution",
        "owner_state": RepairOwnerState.TERMINAL,
        "effect_state": RepairEffectState.CLAIMED,
        "reconcile_supported": True,
        "quiet_period_elapsed": True,
    }
    values.update(changes)
    return RepairSnapshotRecord.model_validate(values)


def _candidate() -> RepairDecision:
    return RepairDecision(
        candidate_id=CLAIM,
        state=RepairDecisionState.CANDIDATE,
        action=RepairAction.EFFECT_RECONCILE_CANDIDATE,
        reasons=(RepairReasonCode.VERIFIED_REPAIR_SIGNAL,),
    )


@dataclass
class _Collector:
    revalidation: EffectClaimRepairRevalidation | None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def revalidate_effect_claim(
        self, *, tenant_id: str, claim_id: str, now: datetime
    ) -> EffectClaimRepairRevalidation | None:
        assert now == NOW
        self.calls.append((tenant_id, claim_id))
        return self.revalidation


@dataclass
class _Queue:
    inserted: bool = True
    commands: list[RuntimeEffectReconcileCommand] = field(default_factory=list)
    fail: bool = False

    async def enqueue_effect_reconcile(
        self, command: RuntimeEffectReconcileCommand
    ) -> bool:
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.commands.append(command)
        return self.inserted


async def test_executor_freshly_revalidates_then_queues_a_body_free_command() -> None:
    claim = _claim()
    queue = _Queue()
    executor = RepairReconciliationExecutor(
        collector=_Collector(
            revalidation=EffectClaimRepairRevalidation(
                claim=claim,
                record=_record(),
            )
        ),
        queue=queue,
    )

    result = await executor.execute(tenant_id=ORG, decisions=(_candidate(),), now=NOW)

    assert result == RepairExecutionResult(queued=1)
    assert len(queue.commands) == 1
    command = queue.commands[0]
    assert command.command_id == build_reconcile_command(claim).command_id  # type: ignore[union-attr]
    assert command.model_dump(mode="json") == {
        "command_id": command.command_id,
        "org_id": ORG,
        "run_id": RUN,
        "claim_id": CLAIM,
        "trace_propagation": {},
        "created_at": "2026-07-26T08:30:00Z",
    }


async def test_executor_withholds_when_a_live_hold_appears_after_planning() -> None:
    queue = _Queue()
    executor = RepairReconciliationExecutor(
        collector=_Collector(
            revalidation=EffectClaimRepairRevalidation(
                claim=_claim(),
                record=_record(legal_hold=RepairLegalHoldState.ACTIVE),
            )
        ),
        queue=queue,
    )

    result = await executor.execute(tenant_id=ORG, decisions=(_candidate(),), now=NOW)

    assert result == RepairExecutionResult(withheld=1)
    assert queue.commands == []


async def test_executor_rejects_a_foreign_claim_at_the_queue_boundary() -> None:
    queue = _Queue()
    executor = RepairReconciliationExecutor(
        collector=_Collector(
            revalidation=EffectClaimRepairRevalidation(
                claim=_claim(org_id="org_foreign"),
                record=_record(),
            )
        ),
        queue=queue,
    )

    result = await executor.execute(tenant_id=ORG, decisions=(_candidate(),), now=NOW)

    assert result == RepairExecutionResult(withheld=1)
    assert queue.commands == []


async def test_executor_never_treats_a_cleanup_candidate_as_delete_authority() -> None:
    queue = _Queue()
    collector = _Collector(
        revalidation=EffectClaimRepairRevalidation(
            claim=_claim(),
            record=_record(),
        )
    )
    executor = RepairReconciliationExecutor(collector=collector, queue=queue)
    cleanup_candidate = RepairDecision(
        candidate_id=CLAIM,
        state=RepairDecisionState.CANDIDATE,
        action=RepairAction.ORPHAN_CLEANUP_CANDIDATE,
        reasons=(RepairReasonCode.VERIFIED_REPAIR_SIGNAL,),
    )

    result = await executor.execute(
        tenant_id=ORG,
        decisions=(cleanup_candidate,),
        now=NOW,
    )

    assert result == RepairExecutionResult(unsupported=1)
    assert collector.calls == []
    assert queue.commands == []


async def test_executor_does_not_advance_past_a_queue_failure() -> None:
    snapshot = build_repair_planning_snapshot(
        tenant_id=ORG,
        records=(_record(),),
        source_complete=True,
        as_of=NOW,
    )
    next_cursor = EffectClaimScanCursor(
        after_created_at=NOW,
        after_org_id=ORG,
        after_claim_id=CLAIM,
    )
    source_page = RepairPlanningSourcePage(
        snapshots=(snapshot,),
        expected_cursor=None,
        next_cursor=next_cursor,
        advance_cursor=True,
    )
    collector = _PlanningCollector(source_page=source_page)
    executor = _OutcomeExecutor(
        outcomes=[RepairExecutionResult(failed=1), RepairExecutionResult(queued=1)]
    )
    snapshots = InMemoryRepairPlanningSnapshotStore()
    runner = RepairPlanningRunner(
        collector=collector,  # type: ignore[arg-type]
        snapshots=snapshots,
        candidate_executor=executor,
        max_claims=10,
        page_size=10,
    )

    first = await runner.run_once(now=NOW)
    assert first.failed == 1
    assert await snapshots.load_effect_claim_scan_cursor() is None

    second = await runner.run_once(now=NOW)
    assert second.queued == 1
    assert second.failed == 0
    assert await snapshots.load_effect_claim_scan_cursor() == next_cursor
    assert executor.calls == 2


def test_recovery_command_is_deterministic_and_rejects_an_invalid_timestamp() -> None:
    claim = _claim()
    first = build_reconcile_command(claim)
    second = build_reconcile_command(claim)

    assert first == second
    assert build_reconcile_command(_claim(updated_at="not-a-timestamp")) is None


def test_repair_execution_flag_is_explicitly_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(RepairExecutionEnv.ENABLED, raising=False)
    assert RepairExecutionEnv.enabled() is False
    monkeypatch.setenv(RepairExecutionEnv.ENABLED, "true")
    assert RepairExecutionEnv.enabled() is True
    monkeypatch.setenv(RepairExecutionEnv.ENABLED, "false")
    assert RepairExecutionEnv.enabled() is False


@dataclass
class _PlanningCollector:
    source_page: RepairPlanningSourcePage

    async def collect(self, **_kwargs: object) -> RepairPlanningSourcePage:
        return self.source_page


@dataclass
class _OutcomeExecutor:
    outcomes: list[RepairExecutionResult]
    calls: int = 0

    async def execute(
        self,
        *,
        tenant_id: str,
        decisions: Sequence[RepairDecision],
        now: datetime,
    ) -> RepairExecutionResult:
        assert tenant_id == ORG
        assert decisions == (_candidate(),)
        assert now == NOW
        result = self.outcomes[self.calls]
        self.calls += 1
        return result
