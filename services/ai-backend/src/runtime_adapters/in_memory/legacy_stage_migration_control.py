"""In-memory control ports for E2 D5 migration integration tests/dev."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agent_runtime.api.legacy_stage_migration_runtime import (
    LegacyQueueInventoryState,
    legacy_stage_source_digest,
)
from agent_runtime.api.legacy_stage_migration_service import (
    LegacyQueueNeutralizationOutcome,
    LegacySourceFenceOutcome,
)
from agent_runtime.persistence.records import OutboxStatus
from agent_runtime.surfaces_v2.legacy_stage_materialization import (
    LegacyStageMaterializationRecord,
    LegacyStageMaterializationState,
    LegacyStageReconciliationRecord,
    LegacyStageReconciliationState,
)
from agent_runtime.api.legacy_stage_migration_runtime import (
    LegacyCanonicalStageEvidence,
)
from agent_runtime.surfaces_v2.staging import StagedWriteFold


class InMemoryLegacyStageReservationStore:
    """Atomic source reservation semantic twin for the memory backend."""

    def __init__(self, *, store: object) -> None:
        self._store = store
        if not hasattr(store, "_e2_legacy_stage_materializations"):
            setattr(store, "_e2_legacy_stage_materializations", {})
        if not hasattr(store, "_e2_legacy_stage_materialization_lock"):
            setattr(store, "_e2_legacy_stage_materialization_lock", asyncio.Lock())
        if not hasattr(store, "_e2_legacy_stage_evidence"):
            setattr(store, "_e2_legacy_stage_evidence", {})
        if not hasattr(store, "_e2_legacy_stage_reconciliations"):
            setattr(store, "_e2_legacy_stage_reconciliations", {})

    async def verify_and_reserve(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        idempotency_key: str,
        canonical_stage_id: str,
    ) -> LegacySourceFenceOutcome:
        key = (org_id, run_id, legacy_stage_id)
        async with self._store._e2_legacy_stage_materialization_lock:  # noqa: SLF001
            run = getattr(self._store, "runs").get(run_id)
            if run is None or getattr(run, "org_id", None) != org_id:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
            state = StagedWriteFold.fold(
                getattr(self._store, "events_by_run").get(run_id, ())
            ).get(legacy_stage_id)
            if state is None:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
            source_digest = legacy_stage_source_digest(run_id=run_id, state=state)
            if source_digest != expected_source_digest:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
            records = self._store._e2_legacy_stage_materializations  # noqa: SLF001
            existing = records.get(key)
            if existing is None:
                records[key] = LegacyStageMaterializationRecord(
                    org_id=org_id,
                    run_id=run_id,
                    legacy_stage_id=legacy_stage_id,
                    source_digest=source_digest,
                    idempotency_key=idempotency_key,
                    canonical_stage_id=canonical_stage_id,
                    state=LegacyStageMaterializationState.RESERVED,
                    revision=0,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                return LegacySourceFenceOutcome.RESERVED
            if (
                existing.source_digest == source_digest
                and existing.idempotency_key == idempotency_key
                and existing.canonical_stage_id == canonical_stage_id
            ):
                if existing.state is LegacyStageMaterializationState.RESERVED:
                    return LegacySourceFenceOutcome.ALREADY_RESERVED
                if existing.state in {
                    LegacyStageMaterializationState.STAGED,
                    LegacyStageMaterializationState.MAPPED,
                }:
                    return LegacySourceFenceOutcome.STAGED
            return LegacySourceFenceOutcome.SOURCE_CHANGED

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        key = (org_id, run_id, legacy_stage_id)
        async with self._store._e2_legacy_stage_materialization_lock:  # noqa: SLF001
            record = self._store._e2_legacy_stage_materializations.get(key)  # noqa: SLF001
            if (
                record is None
                or record.source_digest != expected_source_digest
                or record.canonical_stage_id != canonical_stage_id
                or record.state
                not in {
                    LegacyStageMaterializationState.STAGED,
                    LegacyStageMaterializationState.MAPPED,
                }
            ):
                raise RuntimeError("legacy materialization cannot be mapped")
            if record.state is LegacyStageMaterializationState.MAPPED:
                return
            self._store._e2_legacy_stage_materializations[key] = record.model_copy(  # noqa: SLF001
                update={
                    "state": LegacyStageMaterializationState.MAPPED,
                    "revision": record.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        key = (org_id, run_id, legacy_stage_id)
        async with self._store._e2_legacy_stage_materialization_lock:  # noqa: SLF001
            record = self._store._e2_legacy_stage_materializations.get(key)  # noqa: SLF001
            if record is None or record.source_digest != expected_source_digest:
                return
            if record.state is LegacyStageMaterializationState.STAGED:
                return
            self._store._e2_legacy_stage_materializations[key] = record.model_copy(  # noqa: SLF001
                update={
                    "state": LegacyStageMaterializationState.QUARANTINED,
                    "revision": record.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )

    async def load_candidate_evidence(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
    ) -> LegacyCanonicalStageEvidence | None:
        return self._store._e2_legacy_stage_evidence.get(  # noqa: SLF001
            (org_id, run_id, legacy_stage_id, source_digest)
        )

    async def put_candidate_evidence(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        evidence: LegacyCanonicalStageEvidence,
    ) -> None:
        """Trusted-import test seam; production importers use the same facts."""

        self._store._e2_legacy_stage_evidence[  # noqa: SLF001
            (org_id, run_id, legacy_stage_id, source_digest)
        ] = evidence

    async def checkpoint_reconciliation(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        state: LegacyStageReconciliationState,
        operator_ref: str,
        migration_job_id: str,
    ) -> LegacyStageReconciliationRecord:
        key = (org_id, run_id, legacy_stage_id)
        now = datetime.now(UTC)
        async with self._store._e2_legacy_stage_materialization_lock:  # noqa: SLF001
            records = self._store._e2_legacy_stage_reconciliations  # noqa: SLF001
            existing = records.get(key)
            if existing is None:
                result = LegacyStageReconciliationRecord(
                    org_id=org_id,
                    run_id=run_id,
                    legacy_stage_id=legacy_stage_id,
                    source_digest=source_digest,
                    state=state,
                    checkpoint_revision=0,
                    operator_ref=operator_ref,
                    migration_job_id=migration_job_id,
                    reassessed_at=now,
                    terminal_at=(
                        now
                        if state is not LegacyStageReconciliationState.FROZEN
                        else None
                    ),
                )
            else:
                result = existing.model_copy(
                    update={
                        "source_digest": source_digest,
                        "state": state,
                        "checkpoint_revision": existing.checkpoint_revision + 1,
                        "operator_ref": operator_ref,
                        "migration_job_id": migration_job_id,
                        "reassessed_at": now,
                        "terminal_at": (
                            now
                            if state is not LegacyStageReconciliationState.FROZEN
                            else None
                        ),
                    }
                )
            records[key] = result
            return result


class InMemoryLegacyStageQueueControl:
    """Typed CAS over the actual in-memory runtime outbox."""

    def __init__(self, *, store: object) -> None:
        self._store = store
        self._lock = asyncio.Lock()

    async def state_for_stage(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> str:
        async with self._lock:
            statuses = self._matching_statuses(
                org_id=org_id, run_id=run_id, legacy_stage_id=legacy_stage_id
            )
            if OutboxStatus.CLAIMED in statuses:
                return LegacyQueueInventoryState.CLAIMED
            if any(
                status in {OutboxStatus.PENDING, OutboxStatus.RETRY}
                for status in statuses
            ):
                return LegacyQueueInventoryState.UNCLAIMED
            return LegacyQueueInventoryState.NONE

    async def cancel_unclaimed(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
    ) -> LegacyQueueNeutralizationOutcome:
        async with self._lock:
            if not self._source_matches(
                org_id=org_id,
                run_id=run_id,
                legacy_stage_id=legacy_stage_id,
                expected_source_digest=source_digest,
            ):
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            command_ids = self._matching_commands(
                org_id=org_id, run_id=run_id, legacy_stage_id=legacy_stage_id
            )
            if not command_ids:
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            statuses = getattr(self._store, "_queue_statuses")
            matching_statuses = tuple(
                statuses.get(command_id) for command_id in command_ids
            )
            if OutboxStatus.CLAIMED in matching_statuses:
                return LegacyQueueNeutralizationOutcome.CLAIMED
            active = tuple(
                command_id
                for command_id, status in zip(
                    command_ids, matching_statuses, strict=True
                )
                if status in {OutboxStatus.PENDING, OutboxStatus.RETRY}
            )
            if not active:
                if all(
                    status is OutboxStatus.CANCELLED for status in matching_statuses
                ):
                    return LegacyQueueNeutralizationOutcome.ALREADY_CANCELLED
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            claims = getattr(self._store, "_queue_claims", None)
            for command_id in active:
                statuses[command_id] = OutboxStatus.CANCELLED
                if isinstance(claims, dict):
                    claims.pop(command_id, None)
            return LegacyQueueNeutralizationOutcome.CANCELLED

    def _matching_statuses(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> tuple[OutboxStatus | None, ...]:
        statuses = getattr(self._store, "_queue_statuses")
        return tuple(
            statuses.get(command_id)
            for command_id in self._matching_commands(
                org_id=org_id, run_id=run_id, legacy_stage_id=legacy_stage_id
            )
        )

    def _matching_commands(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> tuple[str, ...]:
        payloads = getattr(self._store, "_queue_payloads")
        return tuple(
            command_id
            for command_id, payload in payloads.items()
            if (
                payload.get("org_id") == org_id
                and payload.get("run_id") == run_id
                and payload.get("command_type") == "stage_commit_requested"
                and payload.get("stage_id") == legacy_stage_id
            )
        )

    def _source_matches(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> bool:
        run = getattr(self._store, "runs").get(run_id)
        if run is None or getattr(run, "org_id", None) != org_id:
            return False
        state = StagedWriteFold.fold(
            getattr(self._store, "events_by_run").get(run_id, ())
        ).get(legacy_stage_id)
        return (
            state is not None
            and legacy_stage_source_digest(run_id=run_id, state=state)
            == expected_source_digest
        )


__all__ = [
    "InMemoryLegacyStageQueueControl",
    "InMemoryLegacyStageReservationStore",
]
