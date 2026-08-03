"""Adversarial production-port tests for E2 D5 source and queue fences."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.legacy_stage_migration_runtime import (
    _DeterministicStageIds,
    LegacyCanonicalStageEvidence,
    RuntimeCanonicalHeldStageWriter,
    RuntimeLegacyFrozenReconciler,
    RuntimeLegacyStageSourceFence,
    legacy_stage_source_digest,
)
from agent_runtime.api.legacy_stage_migration_service import LegacyStageMigrationActor
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.api.legacy_stage_migration_service import (
    LegacyCanonicalStageCandidate,
    LegacyQueueNeutralizationOutcome,
    LegacySourceFenceOutcome,
)
from agent_runtime.effects.contracts import (
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.persistence.records import OutboxStatus
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
)
from agent_runtime.surfaces_v2.staging import StagedWriteFold
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.legacy_stage_migration_control import (
    InMemoryLegacyStageQueueControl,
    InMemoryLegacyStageReservationStore,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.file.legacy_stage_migration_control import (
    FileLegacyStageQueueControl,
    FileLegacyStageReservationStore,
)
from runtime_adapters.repair_planning import build_legacy_stage_migration_service
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
)
from agent_runtime.surfaces_v2.legacy_stage_materialization import (
    LegacyStageMaterializationState,
    LegacyStageReconciliationRecord,
    LegacyStageReconciliationState,
)


pytestmark = pytest.mark.anyio
ORG = "org_e2_runtime"
USER = "user_e2_runtime"
RUN = "run_e2_runtime"
STAGE = "legacy_e2_stage"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
        run_id=RUN,
        conversation_id="conv_e2_runtime",
        org_id=ORG,
        user_id=USER,
        user_message_id="msg_e2_runtime",
        trace_id="trace_e2_runtime",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=USER,
            org_id=ORG,
            run_id=RUN,
            trace_id="trace_e2_runtime",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _legacy_event(*, sequence_no: int = 1, op: str = "create_issue") -> object:
    return SimpleNamespace(
        event_id=f"legacy_event_{sequence_no}",
        event_type=RuntimeApiEventType.WRITE_STAGED,
        sequence_no=sequence_no,
        payload={
            "stage_id": STAGE,
            "surface_id": "surface_e2_runtime",
            "target": {"connector": "linear", "op": op},
            "proposal_ref": "draft://draft_e2_runtime/v1",
        },
    )


def _legacy_revision_event(*, sequence_no: int = 2) -> object:
    return SimpleNamespace(
        event_id=f"legacy_revision_{sequence_no}",
        event_type=RuntimeApiEventType.REVISION_ADDED,
        sequence_no=sequence_no,
        payload={
            "stage_id": STAGE,
            "rev": 2,
            "proposal_ref": "draft://draft_e2_runtime/v2",
            "diff_ref": "diff://draft_e2_runtime/v2",
            "author": "user",
        },
    )


def _candidate() -> LegacyCanonicalStageCandidate:
    snapshot = EffectPolicySnapshot(
        snapshot_ref="policy://e2-runtime/snapshot-1",
        descriptor_known=True,
        deployment_policy=EffectPolicy.REQUIRE,
    )
    return LegacyCanonicalStageCandidate(
        scope=EffectStageScope(run_id=RUN, owner_ref=f"principal://users/{USER}"),
        proposal=ProposedEffect(
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
            proposal_content_ref=(
                "artifact://art_00000000-0000-4000-8000-000000000001/revisions/1"
            ),
            proposal_digest="a" * 64,
            proposal_media_type="application/json",
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            policy_snapshot_ref=snapshot.snapshot_ref,
            agent_hold=True,
            safe_summary_ref="summary://e2-runtime/1",
        ),
        policy_snapshot=snapshot,
    )


async def test_effect_fence_rejects_stale_or_forged_inventory_before_writer() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    state = StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    expected = legacy_stage_source_digest(run_id=RUN, state=state)
    fence = RuntimeLegacyStageSourceFence(
        reservations=InMemoryLegacyStageReservationStore(store=store),
    )
    writer = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=fence,
    )

    # The old stage changes after inventory but before the writer's effect
    # fence.  The writer must not append ``effect.staged`` or touch a queue.
    store.events_by_run[RUN] = [_legacy_event(op="delete_issue")]
    result = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key="e2stage_runtime_stale",
    )

    assert result.fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED
    assert result.canonical_stage_id is None
    assert [
        getattr(event.event_type, "value", event.event_type)
        for event in store.events_by_run[RUN]
    ] == ["write.staged"]
    assert store.effect_commit_commands == []


async def test_effect_fence_reserves_then_creates_one_held_stage_without_execution() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_state = StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    expected = legacy_stage_source_digest(run_id=RUN, state=source_state)
    fence = RuntimeLegacyStageSourceFence(
        reservations=InMemoryLegacyStageReservationStore(store=store),
    )
    writer = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=fence,
    )

    first = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key="e2stage_runtime_exact",
    )
    replay = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key="e2stage_runtime_exact",
    )

    assert first.fence_outcome is LegacySourceFenceOutcome.RESERVED
    assert replay.fence_outcome is LegacySourceFenceOutcome.STAGED
    assert first.canonical_stage_id == replay.canonical_stage_id
    assert [
        getattr(event.event_type, "value", event.event_type)
        for event in store.events_by_run[RUN]
    ] == [
        "write.staged",
        "effect.staged",
    ]
    assert store.effect_commit_commands == []


async def test_queue_cas_cancels_unclaimed_and_a_worker_can_never_claim_it() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_digest = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    store._queue_payloads["cmd_e2"] = {
        "org_id": ORG,
        "run_id": RUN,
        "command_type": "stage_commit_requested",
        "stage_id": STAGE,
    }
    store._queue_order.append("cmd_e2")
    store._queue_statuses["cmd_e2"] = OutboxStatus.PENDING
    store._queue_available_at["cmd_e2"] = datetime.now(UTC)
    control = InMemoryLegacyStageQueueControl(store=store)

    outcome = await control.cancel_unclaimed(
        org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
    )

    assert outcome.value == "cancelled"
    assert (
        await store.claim_next(worker_id="worker_e2", lock_expires_at=datetime.now(UTC))
        is None
    )


async def test_queue_cas_reports_claimed_without_neutralizing_it() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_digest = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    store._queue_payloads["cmd_e2"] = {
        "org_id": ORG,
        "run_id": RUN,
        "command_type": "stage_commit_requested",
        "stage_id": STAGE,
    }
    store._queue_statuses["cmd_e2"] = OutboxStatus.CLAIMED
    control = InMemoryLegacyStageQueueControl(store=store)

    outcome = await control.cancel_unclaimed(
        org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
    )

    assert outcome.value == "claimed"
    assert store._queue_statuses["cmd_e2"] is OutboxStatus.CLAIMED


async def test_queue_cas_refuses_source_drift_and_leaves_old_command_pending() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    store._queue_payloads["cmd_e2"] = {
        "org_id": ORG,
        "run_id": RUN,
        "command_type": "stage_commit_requested",
        "stage_id": STAGE,
    }
    store._queue_statuses["cmd_e2"] = OutboxStatus.PENDING
    store.events_by_run[RUN] = [_legacy_event(op="delete_issue")]
    control = InMemoryLegacyStageQueueControl(store=store)

    outcome = await control.cancel_unclaimed(
        org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=expected
    )

    assert outcome.value == "source_changed"
    assert store._queue_statuses["cmd_e2"] is OutboxStatus.PENDING


async def test_post_reservation_source_mutation_cannot_append_stale_canonical_stage() -> (
    None
):
    """The append-time fence, not just reservation, rejects the race window."""

    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    fence = RuntimeLegacyStageSourceFence(
        reservations=InMemoryLegacyStageReservationStore(store=store),
    )

    class _MutatesAfterReserve:
        async def append_event(self, event):
            store.events_by_run[RUN].append(_legacy_revision_event(sequence_no=2))
            return await store.append_event(event)

        async def list_events_after(self, **kwargs):
            return await store.list_events_after(**kwargs)

    writer = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(
            persistence=store,
            event_store=_MutatesAfterReserve(),
        ),
        fence=fence,
    )

    result = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key="e2stage_post_reservation_race",
    )

    assert result.fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED
    assert [event.event_type.value for event in store.events_by_run[RUN]] == [
        "write.staged",
        "revision.added",
    ]
    assert store.effect_commit_commands == []


async def test_crash_after_reservation_recovers_exact_held_stage_without_wedging() -> (
    None
):
    """A restarted worker completes the same deterministic stage iff proof holds."""

    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    key = "migration-crash-after-reservation"
    stage_id = _DeterministicStageIds(key=key).new_stage_id()
    before_crash = InMemoryLegacyStageReservationStore(store=store)
    assert (
        await before_crash.verify_and_reserve(
            org_id=ORG,
            run_id=RUN,
            legacy_stage_id=STAGE,
            expected_source_digest=expected,
            idempotency_key=key,
            canonical_stage_id=stage_id,
        )
        is LegacySourceFenceOutcome.RESERVED
    )

    restarted = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=RuntimeLegacyStageSourceFence(
            reservations=InMemoryLegacyStageReservationStore(store=store)
        ),
    )
    result = await restarted.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key=key,
    )

    assert result.canonical_stage_id == stage_id
    assert [event.event_type.value for event in store.events_by_run[RUN]] == [
        "write.staged",
        "effect.staged",
    ]
    assert store.effect_commit_commands == []


async def test_queue_cas_neutralizes_every_duplicate_or_freezes_on_any_claim() -> None:
    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_digest = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    for index, queue_status in enumerate((OutboxStatus.PENDING, OutboxStatus.RETRY)):
        command_id = f"cmd_e2_duplicate_{index}"
        store._queue_payloads[command_id] = {
            "org_id": ORG,
            "run_id": RUN,
            "command_type": "stage_commit_requested",
            "stage_id": STAGE,
        }
        store._queue_order.append(command_id)
        store._queue_statuses[command_id] = queue_status
        store._queue_available_at[command_id] = datetime.now(UTC)
    control = InMemoryLegacyStageQueueControl(store=store)

    assert (
        await control.cancel_unclaimed(
            org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
        )
        is LegacyQueueNeutralizationOutcome.CANCELLED
    )
    assert set(store._queue_statuses.values()) == {OutboxStatus.CANCELLED}

    store._queue_statuses["cmd_e2_duplicate_1"] = OutboxStatus.CLAIMED
    assert (
        await control.cancel_unclaimed(
            org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
        )
        is LegacyQueueNeutralizationOutcome.CLAIMED
    )
    assert store._queue_statuses["cmd_e2_duplicate_1"] is OutboxStatus.CLAIMED


async def test_file_queue_cas_neutralizes_all_duplicates_or_freezes_without_a_partial_cancel(
    tmp_path,
) -> None:
    """The desktop queue has the same all-or-freeze duplicate semantics."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_digest = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    for index, queue_status in enumerate((OutboxStatus.PENDING, OutboxStatus.RETRY)):
        command_id = f"cmd_e2_file_duplicate_{index}"
        store._queue_payloads[command_id] = {
            "org_id": ORG,
            "run_id": RUN,
            "command_type": "stage_commit_requested",
            "stage_id": STAGE,
        }
        store._queue_order.append(command_id)
        store._queue_statuses[command_id] = queue_status
    control = FileLegacyStageQueueControl(store=store)

    assert (
        await control.cancel_unclaimed(
            org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
        )
        is LegacyQueueNeutralizationOutcome.CANCELLED
    )
    assert set(store._queue_statuses.values()) == {OutboxStatus.CANCELLED}

    store._queue_statuses["cmd_e2_file_duplicate_0"] = OutboxStatus.PENDING
    store._queue_statuses["cmd_e2_file_duplicate_1"] = OutboxStatus.CLAIMED
    assert (
        await control.cancel_unclaimed(
            org_id=ORG, run_id=RUN, legacy_stage_id=STAGE, source_digest=source_digest
        )
        is LegacyQueueNeutralizationOutcome.CLAIMED
    )
    assert store._queue_statuses["cmd_e2_file_duplicate_0"] is OutboxStatus.PENDING
    assert store._queue_statuses["cmd_e2_file_duplicate_1"] is OutboxStatus.CLAIMED


async def test_production_composition_canonicalizes_only_full_fact_evidence() -> None:
    """The real builder uses durable evidence rather than a no-op resolver."""

    store = InMemoryRuntimeApiStore()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    source_digest = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    candidate = _candidate()
    proof = {
        "org_id": ORG,
        "run_id": RUN,
        "legacy_stage_id": STAGE,
        "source_digest": source_digest,
        "proposal_bytes_digest": candidate.proposal.proposal_digest,
        "canonical_arguments_digest": candidate.proposal.proposal_digest,
        "target_snapshot_digest": candidate.proposal.target_digest,
        "candidate": candidate.model_dump(mode="json"),
    }
    evidence = LegacyCanonicalStageEvidence(
        candidate=candidate,
        proposal_bytes_digest=candidate.proposal.proposal_digest,
        canonical_arguments_digest=candidate.proposal.proposal_digest,
        target_snapshot_digest=candidate.proposal.target_digest,
        proof_digest=canonical_json_sha256(proof),
    )
    control = InMemoryLegacyStageReservationStore(store=store)
    await control.put_candidate_evidence(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest=source_digest,
        evidence=evidence,
    )
    settings = SimpleNamespace(store=SimpleNamespace(backend="memory"))
    service = build_legacy_stage_migration_service(
        settings=settings,
        persistence=store,
        event_store=store,
    )

    report = await service.run(
        org_id=ORG,
        migration_id="e2_full_fact",
        batch_size=10,
        dry_run=False,
        actor=LegacyStageMigrationActor(
            operator_ref=f"principal://users/{USER}",
            migration_job_id="job_e2_full_fact",
        ),
    )

    assert report.canonical_held == 1
    assert [event.event_type.value for event in store.events_by_run[RUN]] == [
        "write.staged",
        "effect.staged",
    ]
    assert store.effect_commit_commands == []
    assert store.audit_log[-1][1]["actor"]["migration_job_id"] == "job_e2_full_fact"
    assert (
        store._e2_legacy_stage_materializations[(ORG, RUN, STAGE)].state
        is LegacyStageMaterializationState.MAPPED
    )


async def test_file_adapter_rejects_post_reservation_mutation_after_restart(
    tmp_path,
) -> None:
    """The file fence survives a fresh control object and refuses stale append."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    initial = FileLegacyStageReservationStore(store=store, root=tmp_path)
    # Simulate a process boundary after reservation, followed by a source edit.
    key = "e2stage_file_restart_race"
    assert (
        await initial.verify_and_reserve(
            org_id=ORG,
            run_id=RUN,
            legacy_stage_id=STAGE,
            expected_source_digest=expected,
            idempotency_key=key,
            canonical_stage_id=_DeterministicStageIds(key=key).new_stage_id(),
        )
        is LegacySourceFenceOutcome.RESERVED
    )
    restarted_control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    store.events_by_run[RUN].append(_legacy_revision_event())
    restarted = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=RuntimeLegacyStageSourceFence(reservations=restarted_control),
    )

    result = await restarted.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key=key,
    )

    assert result.fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED
    assert [event.event_type.value for event in store.events_by_run[RUN]] == [
        "write.staged",
        "revision.added",
    ]


async def test_file_append_gate_rechecks_after_a_real_concurrent_source_mutation(
    tmp_path,
) -> None:
    """The desktop adapter serializes a source mutation before the fenced append."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    control = FileLegacyStageReservationStore(store=store, root=tmp_path)

    class _MutatingEventStore:
        async def append_event(self, event):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=ORG,
                    run_id=RUN,
                    conversation_id=_run().conversation_id,
                    trace_id="trace_e2_file_mutation",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.REVISION_ADDED,
                    payload={
                        "stage_id": STAGE,
                        "rev": 3,
                        "proposal_ref": "draft://draft_e2_runtime/v3",
                        "diff_ref": "diff://draft_e2_runtime/v3",
                        "author": "user",
                    },
                )
            )
            return await store.append_event(event)

        async def list_events_after(self, **kwargs):
            return await store.list_events_after(**kwargs)

    writer = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(
            persistence=store,
            event_store=_MutatingEventStore(),
        ),
        fence=RuntimeLegacyStageSourceFence(reservations=control),
    )

    result = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key="e2stage_file_real_mutation",
    )

    assert result.fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED
    assert [event.event_type.value for event in store.events_by_run[RUN]] == [
        "write.staged",
        "revision.added",
    ]
    assert store.effect_commit_commands == []


async def test_file_recovers_crash_after_event_before_state_transition(
    tmp_path,
) -> None:
    """The fsynced event lets a restarted file fence finish RESERVED → STAGED."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    key = "e2stage_file_crash_after_append"
    control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    writer = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=RuntimeLegacyStageSourceFence(reservations=control),
    )
    first = await writer.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key=key,
    )
    path = control._path(ORG, RUN, STAGE)  # noqa: SLF001
    staged = control._read(path)  # noqa: SLF001
    control._write(  # noqa: SLF001
        path,
        staged.model_copy(update={"state": LegacyStageMaterializationState.RESERVED}),
    )
    restarted_control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    restarted = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=RuntimeLegacyStageSourceFence(reservations=restarted_control),
    )

    recovered = await restarted.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key=key,
    )

    assert recovered.fence_outcome is LegacySourceFenceOutcome.STAGED
    assert recovered.canonical_stage_id == first.canonical_stage_id
    assert restarted_control._read(path).state is LegacyStageMaterializationState.STAGED  # noqa: SLF001
    assert store.effect_commit_commands == []


async def test_file_recovers_crash_after_reservation_before_append(tmp_path) -> None:
    """A durable RESERVED record never wedges a restarted desktop worker."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    store.runs[RUN] = _run()
    store.events_by_run[RUN] = [_legacy_event()]
    expected = legacy_stage_source_digest(
        run_id=RUN, state=StagedWriteFold.fold(store.events_by_run[RUN])[STAGE]
    )
    key = "e2stage_file_crash_before_append"
    stage_id = _DeterministicStageIds(key=key).new_stage_id()
    before_crash = FileLegacyStageReservationStore(store=store, root=tmp_path)
    assert (
        await before_crash.verify_and_reserve(
            org_id=ORG,
            run_id=RUN,
            legacy_stage_id=STAGE,
            expected_source_digest=expected,
            idempotency_key=key,
            canonical_stage_id=stage_id,
        )
        is LegacySourceFenceOutcome.RESERVED
    )

    restarted_control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    restarted = RuntimeCanonicalHeldStageWriter(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        fence=RuntimeLegacyStageSourceFence(reservations=restarted_control),
    )
    recovered = await restarted.create_held_stage(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        expected_source_digest=expected,
        candidate=_candidate(),
        idempotency_key=key,
    )

    assert recovered.fence_outcome is LegacySourceFenceOutcome.ALREADY_RESERVED
    assert recovered.canonical_stage_id == stage_id
    assert (
        restarted_control._read(  # noqa: SLF001
            restarted_control._path(ORG, RUN, STAGE)  # noqa: SLF001
        ).state
        is LegacyStageMaterializationState.STAGED
    )
    assert store.effect_commit_commands == []


async def test_reconciliation_reassesses_and_releases_after_restart_without_dispatch() -> (
    None
):
    """Claimed work is a durable inert task, not a forever-frozen mapping."""

    store = InMemoryRuntimeApiStore()
    control = InMemoryLegacyStageReservationStore(store=store)
    actor = LegacyStageMigrationActor(
        operator_ref=f"principal://users/{USER}",
        migration_job_id="job_e2_reconcile",
    )
    first = RuntimeLegacyFrozenReconciler(audit=store, checkpoints=control)
    await first.freeze(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest="c" * 64,
        actor=actor,
    )
    restarted = RuntimeLegacyFrozenReconciler(audit=store, checkpoints=control)
    await restarted.freeze(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest="c" * 64,
        actor=actor,
    )
    await restarted.release(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest="d" * 64,
        actor=actor,
    )

    checkpoint = store._e2_legacy_stage_reconciliations[(ORG, RUN, STAGE)]
    assert checkpoint.state is LegacyStageReconciliationState.RELEASED
    assert checkpoint.checkpoint_revision == 2
    assert checkpoint.source_digest == "d" * 64
    assert store.effect_commit_commands == []
    assert store.stage_commit_commands == []


async def test_file_reconciliation_checkpoint_survives_restart_and_records_release(
    tmp_path,
) -> None:
    """Operator recovery is durable and remains structurally non-dispatching."""

    store = FileRuntimeApiStore(tmp_path)
    await store.open()
    actor = LegacyStageMigrationActor(
        operator_ref=f"principal://users/{USER}",
        migration_job_id="job_e2_file_reconcile",
    )
    first_control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    first = RuntimeLegacyFrozenReconciler(audit=store, checkpoints=first_control)
    await first.freeze(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest="e" * 64,
        actor=actor,
    )

    restarted_control = FileLegacyStageReservationStore(store=store, root=tmp_path)
    restarted = RuntimeLegacyFrozenReconciler(
        audit=store, checkpoints=restarted_control
    )
    await restarted.release(
        org_id=ORG,
        run_id=RUN,
        legacy_stage_id=STAGE,
        source_digest="f" * 64,
        actor=actor,
    )

    material = "\0".join((ORG, RUN, STAGE)).encode()
    path = restarted_control._reconciliation_dir / (  # noqa: SLF001
        f"{hashlib.sha256(material).hexdigest()}.json"
    )
    checkpoint = LegacyStageReconciliationRecord.model_validate(
        json.loads(path.read_text(encoding="utf-8"))["record"]
    )
    assert checkpoint.state is LegacyStageReconciliationState.RELEASED
    assert checkpoint.checkpoint_revision == 1
    assert store.effect_commit_commands == []
    assert store.stage_commit_commands == []
