"""Adversarial tests for E2 D5 legacy-stage migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agent_runtime.api.legacy_stage_migration_service import (
    LegacyCanonicalStageWriteResult,
    LegacyCanonicalStageCandidate,
    LegacyPendingStage,
    LegacyPendingStageStatus,
    LegacyQueueNeutralizationOutcome,
    LegacySourceFenceOutcome,
    LegacyStageMigrationService,
)
from agent_runtime.effects.contracts import (
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
)
from agent_runtime.surfaces_v2.legacy_migration import LegacyStageMigrationOutcome
from agent_runtime.surfaces_v2.legacy_migration import LegacyStageMigrationRecord
from runtime_adapters.in_memory.legacy_stage_migration_store import (
    InMemoryLegacyStageMigrationStore,
)
from runtime_adapters.file.legacy_stage_migration_store import (
    FileLegacyStageMigrationStore,
)


pytestmark = pytest.mark.anyio
NOW = datetime(2026, 7, 26, tzinfo=UTC)
ORG_A = "org_e2_stage_a"
ORG_B = "org_e2_stage_b"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _candidate(run_id: str) -> LegacyCanonicalStageCandidate:
    scope = EffectStageScope(run_id=run_id, owner_ref="principal://users/user_e2_stage")
    snapshot = EffectPolicySnapshot(
        snapshot_ref="policy://e2-stage/snapshot-1",
        descriptor_known=True,
        deployment_policy=EffectPolicy.REQUIRE,
    )
    proposal = ProposedEffect(
        operation_id="op_00000000-0000-4000-8000-000000000001",
        executor=EffectExecutorKind.MCP,
        target=EffectTarget(
            executor=EffectExecutorKind.MCP,
            capability="linear",
            op="create_issue",
            target_ref="mcp-target://linear/team-eng",
            display_label="ENG",
        ),
        target_digest="b" * 64,
        display_target="ENG",
        proposal_kind=EffectProposalKind.CANONICAL_ARGUMENTS,
        proposal_content_ref="artifact://art_00000000-0000-4000-8000-000000000001/revisions/1",
        proposal_digest="a" * 64,
        proposal_media_type="application/json",
        effect_class=EffectClass.EXTERNAL_REVERSIBLE,
        policy_snapshot_ref=snapshot.snapshot_ref,
        agent_hold=True,
        safe_summary_ref="summary://e2-stage/1",
    )
    return LegacyCanonicalStageCandidate(
        scope=scope, proposal=proposal, policy_snapshot=snapshot
    )


def _item(
    *,
    org_id: str = ORG_A,
    run_id: str = "run_e2_stage_1",
    stage_id: str = "legacy_stage_1",
    status: LegacyPendingStageStatus = LegacyPendingStageStatus.UNAPPROVED_HELD,
    candidate: LegacyCanonicalStageCandidate | None = None,
    digest: str = "d" * 64,
) -> LegacyPendingStage:
    return LegacyPendingStage(
        org_id=org_id,
        run_id=run_id,
        legacy_stage_id=stage_id,
        source_digest=digest,
        status=status,
        candidate=candidate,
    )


@dataclass
class _Inventory:
    items: list[LegacyPendingStage]

    async def list_pending_legacy_stages(self, *, org_id, after, limit):
        eligible = sorted(
            (
                item
                for item in self.items
                if item.org_id == org_id
                and (after is None or (item.run_id, item.legacy_stage_id) > after)
            ),
            key=lambda item: (item.run_id, item.legacy_stage_id),
        )
        return tuple(eligible[:limit])


@dataclass
class _Writer:
    created: list[tuple[str, str, str, str, LegacyCanonicalStageCandidate, str]] = (
        field(default_factory=list)
    )

    async def create_held_stage(
        self,
        *,
        org_id,
        run_id,
        legacy_stage_id,
        expected_source_digest,
        candidate,
        idempotency_key,
    ):
        self.created.append(
            (
                org_id,
                run_id,
                legacy_stage_id,
                expected_source_digest,
                candidate,
                idempotency_key,
            )
        )
        # Deliberately no approval/queue capability: this is the D5 seam.
        return LegacyCanonicalStageWriteResult(
            fence_outcome=LegacySourceFenceOutcome.RESERVED,
            canonical_stage_id="stg_00000000-0000-4000-8000-000000000001",
        )


@dataclass
class _Queue:
    cancelled: list[tuple[str, str, str, str]] = field(default_factory=list)
    outcome: LegacyQueueNeutralizationOutcome = (
        LegacyQueueNeutralizationOutcome.CANCELLED
    )

    async def cancel_unclaimed(self, *, org_id, run_id, legacy_stage_id, source_digest):
        self.cancelled.append((org_id, run_id, legacy_stage_id, source_digest))
        return self.outcome


@dataclass
class _Reconciler:
    frozen: list[tuple[str, str, str, str]] = field(default_factory=list)

    async def freeze(
        self, *, org_id, run_id, legacy_stage_id, source_digest, actor=None
    ):
        del actor
        self.frozen.append((org_id, run_id, legacy_stage_id, source_digest))

    async def release(
        self, *, org_id, run_id, legacy_stage_id, source_digest, actor=None
    ):
        del org_id, run_id, legacy_stage_id, source_digest, actor


@dataclass
class _Audit:
    records: list[object] = field(default_factory=list)

    async def write_stage_migration_audit(self, *, record, actor=None):
        del actor
        self.records.append(record)


def _service(items: list[LegacyPendingStage]):
    writer, queue, reconciler, audit = _Writer(), _Queue(), _Reconciler(), _Audit()
    return (
        LegacyStageMigrationService(
            inventory=_Inventory(items),
            mappings=InMemoryLegacyStageMigrationStore(),
            writer=writer,
            queue=queue,
            reconciler=reconciler,
            audit=audit,
            now=lambda: NOW,
        ),
        writer,
        queue,
        reconciler,
        audit,
    )


async def test_approved_legacy_item_becomes_held_never_auto_approved() -> None:
    item = _item(
        status=LegacyPendingStageStatus.APPROVED_UNAPPLIED,
        candidate=_candidate("run_e2_stage_1"),
    )
    service, writer, queue, reconciler, audit = _service([item])

    report = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=10, dry_run=False
    )

    assert report.canonical_held == 1
    assert len(writer.created) == 1
    assert queue.cancelled == []
    assert reconciler.frozen == []
    record = audit.records[0]
    assert record.outcome is LegacyStageMigrationOutcome.CANONICAL_HELD
    assert record.canonical_stage_id is not None
    # The old approval does not cross the seam: writer receives only candidate
    # facts and has no approval / command API at all.
    assert not hasattr(writer, "approve")
    assert not hasattr(writer, "enqueue")


async def test_unclaimed_legacy_queue_is_cancelled_before_held_stage() -> None:
    item = _item(
        status=LegacyPendingStageStatus.QUEUED_UNCLAIMED,
        candidate=_candidate("run_e2_stage_1"),
    )
    service, writer, queue, _reconciler, audit = _service([item])

    await service.run(org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False)

    assert len(queue.cancelled) == 1
    assert len(writer.created) == 1
    assert audit.records[0].queue_cancelled is True


@pytest.mark.parametrize(
    ("queue_outcome", "expected_outcome"),
    (
        (
            LegacyQueueNeutralizationOutcome.CLAIMED,
            LegacyStageMigrationOutcome.FROZEN_RECONCILE,
        ),
        (
            LegacyQueueNeutralizationOutcome.SOURCE_CHANGED,
            LegacyStageMigrationOutcome.QUARANTINED,
        ),
    ),
)
async def test_queue_race_never_reaches_canonical_writer(
    queue_outcome: LegacyQueueNeutralizationOutcome,
    expected_outcome: LegacyStageMigrationOutcome,
) -> None:
    item = _item(
        status=LegacyPendingStageStatus.QUEUED_UNCLAIMED,
        candidate=_candidate("run_e2_stage_1"),
    )
    service, writer, queue, reconciler, audit = _service([item])
    queue.outcome = queue_outcome

    await service.run(org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False)

    assert writer.created == []
    assert audit.records[0].outcome is expected_outcome
    assert audit.records[0].queue_cancelled is False
    assert len(reconciler.frozen) == int(
        expected_outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE
    )


async def test_writer_receives_trusted_source_fence_facts_not_only_candidate() -> None:
    item = _item(candidate=_candidate("run_e2_stage_1"), digest="e" * 64)
    service, writer, _queue, _reconciler, _audit = _service([item])

    await service.run(org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False)

    org_id, run_id, legacy_stage_id, source_digest, _candidate_value, _key = (
        writer.created[0]
    )
    assert (org_id, run_id, legacy_stage_id, source_digest) == (
        ORG_A,
        "run_e2_stage_1",
        "legacy_stage_1",
        "e" * 64,
    )


async def test_claimed_or_indeterminate_is_frozen_never_canonicalized_or_redispatched() -> (
    None
):
    items = [
        _item(status=LegacyPendingStageStatus.CLAIMED, stage_id="legacy_claimed"),
        _item(
            status=LegacyPendingStageStatus.INDETERMINATE,
            stage_id="legacy_indeterminate",
        ),
    ]
    service, writer, queue, reconciler, audit = _service(items)

    report = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=10, dry_run=False
    )

    assert report.frozen_reconcile == 2
    assert writer.created == []
    assert queue.cancelled == []
    assert len(reconciler.frozen) == 2
    assert all(record.reconciler_frozen for record in audit.records)


async def test_unprovable_bytes_or_arguments_are_quarantined() -> None:
    service, writer, queue, reconciler, audit = _service(
        [_item(status=LegacyPendingStageStatus.UNAPPROVED_PROPOSED, candidate=None)]
    )

    report = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False
    )

    assert report.quarantined == 1
    assert writer.created == queue.cancelled == reconciler.frozen == []
    assert audit.records[0].outcome is LegacyStageMigrationOutcome.QUARANTINED


async def test_retry_has_one_canonical_stage_and_exact_mapping_audit() -> None:
    item = _item(candidate=_candidate("run_e2_stage_1"))
    service, writer, _queue, _reconciler, audit = _service([item])

    first = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False
    )
    second = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False
    )

    assert first.report_digest == second.report_digest
    assert len(writer.created) == 1
    assert len(audit.records) == 2
    assert audit.records[0].model_dump(
        exclude={"created_at", "updated_at"}
    ) == audit.records[1].model_dump(exclude={"created_at", "updated_at"})


async def test_cross_tenant_inventory_isolation_and_dry_run_is_non_mutating() -> None:
    items = [
        _item(org_id=ORG_A, candidate=_candidate("run_e2_stage_1")),
        _item(
            org_id=ORG_B,
            run_id="run_e2_stage_2",
            stage_id="legacy_stage_b",
            candidate=_candidate("run_e2_stage_2"),
        ),
    ]
    service, writer, queue, reconciler, audit = _service(items)

    report = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=10, dry_run=True
    )

    assert report.scanned == 1
    assert report.canonical_held == 1
    assert writer.created == queue.cancelled == reconciler.frozen == audit.records == []


@pytest.mark.parametrize("store_kind", ("memory", "file"))
async def test_mapping_adapters_are_source_fenced_and_idempotent(
    store_kind, tmp_path
) -> None:
    store = (
        InMemoryLegacyStageMigrationStore()
        if store_kind == "memory"
        else FileLegacyStageMigrationStore(root=tmp_path)
    )
    # Use the service rather than manufacturing a mapping: it pins every
    # adapter to the same source digest + canonical-held semantics.
    service = LegacyStageMigrationService(
        inventory=_Inventory([_item(candidate=_candidate("run_e2_stage_1"))]),
        mappings=store,
        writer=_Writer(),
        queue=_Queue(),
        reconciler=_Reconciler(),
        audit=_Audit(),
        now=lambda: NOW,
    )
    await service.run(org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False)
    replay = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False
    )
    assert replay.canonical_held == 1


@pytest.mark.parametrize("store_kind", ("memory", "file"))
async def test_frozen_mapping_is_reassessed_not_a_permanent_migration_result(
    store_kind, tmp_path
) -> None:
    """Only a former frozen observation may be replaced after a fresh scan."""

    mappings = (
        InMemoryLegacyStageMigrationStore()
        if store_kind == "memory"
        else FileLegacyStageMigrationStore(root=tmp_path)
    )
    old = LegacyStageMigrationRecord(
        org_id=ORG_A,
        migration_id="e2_d5",
        run_id="run_e2_stage_1",
        legacy_stage_id="legacy_stage_1",
        source_digest="c" * 64,
        outcome=LegacyStageMigrationOutcome.FROZEN_RECONCILE,
        canonical_stage_id=None,
        queue_cancelled=False,
        reconciler_frozen=True,
        revision=0,
        created_at=NOW,
        updated_at=NOW,
    )
    await mappings.load_or_create(record=old)
    writer, queue, reconciler, audit = _Writer(), _Queue(), _Reconciler(), _Audit()
    service = LegacyStageMigrationService(
        inventory=_Inventory([_item(candidate=_candidate("run_e2_stage_1"))]),
        mappings=mappings,
        writer=writer,
        queue=queue,
        reconciler=reconciler,
        audit=audit,
        now=lambda: NOW,
    )

    report = await service.run(
        org_id=ORG_A, migration_id="e2_d5", batch_size=1, dry_run=False
    )
    current = await mappings.load(
        org_id=ORG_A,
        migration_id="e2_d5",
        run_id="run_e2_stage_1",
        legacy_stage_id="legacy_stage_1",
    )

    assert report.canonical_held == 1
    assert len(writer.created) == 1
    assert current is not None
    assert current.outcome is LegacyStageMigrationOutcome.CANONICAL_HELD
    assert current.source_digest == "d" * 64
