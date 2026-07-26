"""Production composition primitives for the E2 D5 legacy-stage migration.

The policy service in :mod:`legacy_stage_migration_service` intentionally owns
classification only.  This module supplies the runtime-facing, still
non-executing ports: inventory from the append-only run ledger, an authoritative
source fence, a held-stage writer, a queue neutralizer, and a frozen-only
reconciler.  None of these objects has an effect executor or an effect-commit
outbox.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import hashlib
from typing import Protocol, runtime_checkable
from uuid import UUID

from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.legacy_stage_migration_service import (
    CanonicalHeldStageWriter,
    LegacyCanonicalStageCandidate,
    LegacyCanonicalStageWriteResult,
    LegacyFrozenReconciler,
    LegacyPendingStage,
    LegacyPendingStageInventoryPort,
    LegacyPendingStageStatus,
    LegacyQueueNeutralizationOutcome,
    LegacyQueueNeutralizer,
    LegacySourceFenceOutcome,
    LegacyStageMigrationAuditPort,
    LegacyStageMigrationActor,
    LegacyStageMigrationError,
    LegacyStageSourceFence,
)
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageStatus
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec
from agent_runtime.surfaces_v2.legacy_stage_materialization import (
    MATERIALIZATION_FENCE_METADATA_KEY,
    LegacyStageReconciliationState,
    LegacyStageMaterializationRejected,
)
from agent_runtime.surfaces_v2.ledger_models import EffectActor
from agent_runtime.surfaces_v2.staging import StagedWriteFold, StagedWriteStatus


class LegacyQueueInventoryState(str):
    """Internal queue observation values; not a public wire contract."""

    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"
    NONE = "none"


class LegacyCanonicalStageEvidence(RuntimeContract):
    """Full-fact importer proof; no approval or executable command is stored."""

    candidate: LegacyCanonicalStageCandidate
    proposal_bytes_digest: str
    canonical_arguments_digest: str
    target_snapshot_digest: str
    proof_digest: str


@runtime_checkable
class LegacyStageQueueInspector(Protocol):
    """Read only the legacy command lifecycle for one stage."""

    async def state_for_stage(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> str:
        """Return ``unclaimed``, ``claimed``, or ``none`` for an exact stage."""


@runtime_checkable
class LegacyStageReservationStore(Protocol):
    """Atomic source verifier/reserver separate from the final mapping row."""

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
        """Read/re-fold and reserve one exact legacy source in one adapter fence."""

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        """Persist the final ``staged → mapped`` transition."""

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        """Release stale source proof into a terminal, non-dispatching state."""


@runtime_checkable
class LegacyStageCandidateResolver(Protocol):
    """Return canonical material only when every source fact is proven."""

    async def resolve(
        self,
        *,
        org_id: str,
        run: object,
        legacy_stage_id: str,
        source_digest: str,
        events: Sequence[object],
    ) -> LegacyCanonicalStageCandidate | None:
        """Never infer content, arguments, or a digest from partial legacy data."""


@runtime_checkable
class LegacyStageCandidateEvidenceStore(Protocol):
    """Reads only durable, importer-produced full-fact candidate evidence."""

    async def load_candidate_evidence(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
    ) -> object | None:
        """Return a safe evidence record or ``None``; never infer missing data."""


@runtime_checkable
class LegacyStageReconciliationStore(Protocol):
    """Durable, no-dispatch checkpoints for ambiguous old commands."""

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
    ) -> object:
        """Advance an observation-only checkpoint or its terminal transition."""


@dataclass(frozen=True)
class DurableLegacyStageCandidateResolver(LegacyStageCandidateResolver):
    """Canonicalizes only a cryptographically complete imported evidence row."""

    evidence: LegacyStageCandidateEvidenceStore

    async def resolve(
        self,
        *,
        org_id: str,
        run: object,
        legacy_stage_id: str,
        source_digest: str,
        events: Sequence[object],
    ) -> LegacyCanonicalStageCandidate | None:
        del events
        run_id = getattr(run, "run_id", None)
        if not isinstance(run_id, str) or getattr(run, "org_id", None) != org_id:
            return None
        evidence = await self.evidence.load_candidate_evidence(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            source_digest=source_digest,
        )
        if evidence is None:
            return None
        candidate = getattr(evidence, "candidate", None)
        if not isinstance(candidate, LegacyCanonicalStageCandidate):
            return None
        proof = {
            "org_id": org_id,
            "run_id": run_id,
            "legacy_stage_id": legacy_stage_id,
            "source_digest": source_digest,
            "proposal_bytes_digest": getattr(evidence, "proposal_bytes_digest", None),
            "canonical_arguments_digest": getattr(
                evidence, "canonical_arguments_digest", None
            ),
            "target_snapshot_digest": getattr(evidence, "target_snapshot_digest", None),
            "candidate": candidate.model_dump(mode="json"),
        }
        if (
            proof["proposal_bytes_digest"] != candidate.proposal.proposal_digest
            or proof["canonical_arguments_digest"] != candidate.proposal.proposal_digest
            or proof["target_snapshot_digest"] != candidate.proposal.target_digest
            or getattr(evidence, "proof_digest", None) != canonical_json_sha256(proof)
            or candidate.scope.run_id != run_id
            or not candidate.proposal.agent_hold
        ):
            return None
        return candidate


class NoLegacyStageCandidateResolver:
    """Safe production default until a legacy record has full canonical facts.

    Existing v2 staged-write events deliberately contain draft references, not
    the immutable target/argument bundle required by universal effects.  This
    resolver therefore reports those rows to the control plane as quarantined,
    which is an observable audited outcome rather than a hidden no-op.
    """

    async def resolve(
        self,
        *,
        org_id: str,
        run: object,
        legacy_stage_id: str,
        source_digest: str,
        events: Sequence[object],
    ) -> LegacyCanonicalStageCandidate | None:
        del org_id, run, legacy_stage_id, source_digest, events
        return None


def legacy_stage_source_digest(*, run_id: str, state: object) -> str:
    """Derive the same complete-state digest used by the E2 evidence scan."""

    state_dump = getattr(state, "model_dump", None)
    if not callable(state_dump):
        raise LegacyStageMigrationError("legacy stage source is invalid")
    return canonical_json_sha256(
        {
            "run_id": run_id,
            "stage_id": getattr(state, "stage_id", ""),
            "folded_state_digest": canonical_json_sha256(state_dump(mode="json")),
            "status": getattr(getattr(state, "status", None), "value", ""),
        }
    )


@dataclass(frozen=True)
class RuntimeLegacyPendingStageInventory(LegacyPendingStageInventoryPort):
    """Tenant-scoped inventory over the existing append-only runtime ledger."""

    persistence: object
    event_store: object
    queue: LegacyStageQueueInspector
    candidates: LegacyStageCandidateResolver = NoLegacyStageCandidateResolver()
    max_events_per_run: int = 50_000

    async def list_pending_legacy_stages(
        self, *, org_id: str, after: tuple[str, str] | None, limit: int
    ) -> Sequence[LegacyPendingStage]:
        if limit < 1:
            return ()
        list_runs = getattr(self.persistence, "list_runs_for_migration", None)
        list_events = getattr(self.event_store, "list_events_after", None)
        if not callable(list_runs) or not callable(list_events):
            raise LegacyStageMigrationError("legacy migration inventory is unavailable")

        # An inventory page is bounded by *items*, not runs.  Each run is
        # independently tenant-checked before its event stream is folded.
        run_after = after[0] if after is not None else None
        runs = tuple(
            await list_runs(org_id=org_id, after_run_id=run_after, limit=max(limit, 1))
        )
        items: list[LegacyPendingStage] = []
        for run in runs:
            run_id = getattr(run, "run_id", None)
            if not isinstance(run_id, str) or getattr(run, "org_id", None) != org_id:
                raise LegacyStageMigrationError("legacy migration inventory is invalid")
            events = tuple(
                await list_events(org_id=org_id, run_id=run_id, after_sequence=0)
            )
            if len(events) > self.max_events_per_run:
                raise LegacyStageMigrationError(
                    "legacy migration inventory is too large"
                )
            states = StagedWriteFold.fold(events)
            for legacy_stage_id, state in sorted(states.items()):
                key = (run_id, legacy_stage_id)
                if after is not None and key <= after:
                    continue
                source_digest = legacy_stage_source_digest(run_id=run_id, state=state)
                candidate = await self.candidates.resolve(
                    org_id=org_id,
                    run=run,
                    legacy_stage_id=legacy_stage_id,
                    source_digest=source_digest,
                    events=events,
                )
                status = await self._status_for(
                    org_id=org_id,
                    run_id=run_id,
                    stage_id=legacy_stage_id,
                    state_status=state.status,
                )
                items.append(
                    LegacyPendingStage(
                        org_id=org_id,
                        run_id=run_id,
                        legacy_stage_id=legacy_stage_id,
                        source_digest=source_digest,
                        status=status,
                        candidate=candidate,
                    )
                )
                if len(items) == limit:
                    return tuple(items)
        return tuple(items)

    async def _status_for(
        self,
        *,
        org_id: str,
        run_id: str,
        stage_id: str,
        state_status: StagedWriteStatus,
    ) -> LegacyPendingStageStatus:
        if state_status in {
            StagedWriteStatus.REJECTED,
            StagedWriteStatus.APPLIED,
            StagedWriteStatus.PARTIALLY_APPLIED,
        }:
            return LegacyPendingStageStatus.COMPATIBILITY_ONLY
        if state_status is StagedWriteStatus.CORRUPT:
            return LegacyPendingStageStatus.INDETERMINATE
        if state_status in {
            StagedWriteStatus.APPROVED,
            StagedWriteStatus.APPLY_PENDING,
        }:
            queue_state = await self.queue.state_for_stage(
                org_id=org_id, run_id=run_id, legacy_stage_id=stage_id
            )
            if queue_state == LegacyQueueInventoryState.UNCLAIMED:
                return LegacyPendingStageStatus.QUEUED_UNCLAIMED
            if queue_state == LegacyQueueInventoryState.CLAIMED:
                return LegacyPendingStageStatus.CLAIMED
            return LegacyPendingStageStatus.APPROVED_UNAPPLIED
        return LegacyPendingStageStatus.UNAPPROVED_HELD


@dataclass(frozen=True)
class RuntimeLegacyStageSourceFence(LegacyStageSourceFence):
    """Delegate to one adapter-atomic source verification/reservation fence."""

    reservations: LegacyStageReservationStore

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
        return await self.reservations.verify_and_reserve(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            idempotency_key=idempotency_key,
            canonical_stage_id=canonical_stage_id,
        )

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        await self.reservations.mark_mapped(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            canonical_stage_id=canonical_stage_id,
        )

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        await self.reservations.quarantine(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
        )


@dataclass(frozen=True)
class _DeterministicStageIds:
    """Stable UUID4-compatible stage identity for retry-safe migration writes."""

    key: str

    def new_stage_id(self) -> str:
        raw = bytearray(hashlib.sha256(self.key.encode("utf-8")).digest()[:16])
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        return EffectStageIdCodec.format(UUID(bytes=bytes(raw)))


class _NoCommitOutbox:
    """Structural assertion that a migration-created stage cannot execute."""

    async def enqueue_after_decision(self, command: object) -> None:
        del command
        raise AssertionError("legacy migration stages never enqueue an effect commit")


@dataclass(frozen=True)
class RuntimeCanonicalHeldStageWriter(CanonicalHeldStageWriter):
    """Fence first, then create exactly one held canonical effect stage."""

    persistence: object
    event_producer: RuntimeEventProducer
    fence: LegacyStageSourceFence

    async def create_held_stage(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        candidate: LegacyCanonicalStageCandidate,
        idempotency_key: str,
    ) -> LegacyCanonicalStageWriteResult:
        # This is intentionally the first stateful operation.  An advisory
        # inventory object, a forged tenant/run, or a changed folded source
        # therefore never reaches EffectStager.
        stage_ids = _DeterministicStageIds(key=idempotency_key)
        stage_id = stage_ids.new_stage_id()
        fence_outcome = await self.fence.verify_and_reserve(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            idempotency_key=idempotency_key,
            canonical_stage_id=stage_id,
        )
        if fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED:
            return LegacyCanonicalStageWriteResult(fence_outcome=fence_outcome)

        get_run = getattr(self.persistence, "get_run", None)
        if not callable(get_run):
            raise LegacyStageMigrationError("legacy migration runtime is unavailable")
        run = await get_run(org_id=org_id, run_id=run_id)
        if run is None or getattr(run, "org_id", None) != org_id:
            return LegacyCanonicalStageWriteResult(
                fence_outcome=LegacySourceFenceOutcome.SOURCE_CHANGED
            )
        owner_ref = f"principal://users/{getattr(run, 'user_id', '')}"
        if candidate.scope.run_id != run_id or candidate.scope.owner_ref != owner_ref:
            return LegacyCanonicalStageWriteResult(
                fence_outcome=LegacySourceFenceOutcome.SOURCE_CHANGED
            )
        if not candidate.proposal.agent_hold:
            raise LegacyStageMigrationError(
                "legacy migration writer requires held proposal"
            )

        stager = EffectStager(
            ledger=RuntimeEffectLedger(
                event_producer=self.event_producer,
                run=run,
                owner_ref=owner_ref,
                append_metadata={
                    MATERIALIZATION_FENCE_METADATA_KEY: {
                        "org_id": org_id,
                        "run_id": run_id,
                        "legacy_stage_id": legacy_stage_id,
                        "source_digest": expected_source_digest,
                        "idempotency_key": idempotency_key,
                        "canonical_stage_id": stage_id,
                    }
                },
            ),
            outbox=_NoCommitOutbox(),
            stage_ids=stage_ids,
        )
        if fence_outcome is LegacySourceFenceOutcome.STAGED:
            # A crash can land after the reservation and stage append.  Do not
            # call append again (presentation metadata is intentionally not an
            # idempotency input); replay the deterministic stage instead.
            existing = await stager.get_state(
                scope=candidate.scope,
                stage_id=stage_id,
            )
            if existing.status is not EffectStageStatus.HELD:
                raise LegacyStageMigrationError("reserved legacy stage is not held")
            return LegacyCanonicalStageWriteResult(
                fence_outcome=fence_outcome,
                canonical_stage_id=existing.stage_id,
            )
        try:
            state = await stager.stage(
                scope=candidate.scope,
                proposed_effect=candidate.proposal,
                policy_snapshot=candidate.policy_snapshot,
                actor=EffectActorIdentity(
                    actor=EffectActor.SYSTEM,
                    principal_ref="principal://system/e2-legacy-stage-migration",
                ),
                idempotency_key=idempotency_key,
            )
        except LegacyStageMaterializationRejected:
            await self.fence.quarantine(
                org_id=org_id,
                run_id=run_id,
                legacy_stage_id=legacy_stage_id,
                expected_source_digest=expected_source_digest,
            )
            return LegacyCanonicalStageWriteResult(
                fence_outcome=LegacySourceFenceOutcome.SOURCE_CHANGED
            )
        if state.status is not EffectStageStatus.HELD:
            raise LegacyStageMigrationError("legacy migration stage was not held")
        return LegacyCanonicalStageWriteResult(
            fence_outcome=fence_outcome,
            canonical_stage_id=state.stage_id,
        )

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        await self.fence.mark_mapped(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            canonical_stage_id=canonical_stage_id,
        )


@dataclass(frozen=True)
class RuntimeLegacyQueueNeutralizer(LegacyQueueNeutralizer):
    """Call the backend's atomic source-digest + queue compare-and-set."""

    cancel_cas: Callable[..., Awaitable[LegacyQueueNeutralizationOutcome]]

    async def cancel_unclaimed(
        self, *, org_id: str, run_id: str, legacy_stage_id: str, source_digest: str
    ) -> LegacyQueueNeutralizationOutcome:
        # A reservation is deliberately not taken here: a claimed race must
        # freeze rather than strand an old command behind a canonical stage.
        return await self.cancel_cas(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            source_digest=source_digest,
        )


@dataclass(frozen=True)
class RuntimeLegacyFrozenReconciler(LegacyFrozenReconciler):
    """Checkpointed reconciliation which owns no worker queue or executor."""

    audit: object
    checkpoints: LegacyStageReconciliationStore

    async def freeze(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        actor: LegacyStageMigrationActor | None = None,
    ) -> None:
        write_audit = getattr(self.audit, "write_audit_log", None)
        if not callable(write_audit):
            raise LegacyStageMigrationError(
                "legacy reconciliation audit is unavailable"
            )
        operator_ref = (
            actor.operator_ref
            if actor is not None
            else "principal://system/e2-migration-test"
        )
        job_id = actor.migration_job_id if actor is not None else "e2-migration-test"
        checkpoint = await self.checkpoints.checkpoint_reconciliation(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            source_digest=source_digest,
            state=LegacyStageReconciliationState.FROZEN,
            operator_ref=operator_ref,
            migration_job_id=job_id,
        )
        await write_audit(
            event_type="e2_legacy_stage_frozen",
            record={
                "org_id": org_id,
                "run_id": run_id,
                "legacy_stage_id": legacy_stage_id,
                "source_digest": source_digest,
                "checkpoint": getattr(checkpoint, "model_dump", lambda **_: checkpoint)(
                    mode="json"
                ),
                "mode": "observation_only",
            },
        )

    async def release(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        actor: LegacyStageMigrationActor | None = None,
    ) -> None:
        await self.checkpoints.checkpoint_reconciliation(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            source_digest=source_digest,
            state=LegacyStageReconciliationState.RELEASED,
            operator_ref=(
                actor.operator_ref
                if actor is not None
                else "principal://system/e2-migration-test"
            ),
            migration_job_id=(
                actor.migration_job_id if actor is not None else "e2-migration-test"
            ),
        )


@dataclass(frozen=True)
class RuntimeLegacyStageMigrationAudit(LegacyStageMigrationAuditPort):
    """Write only redacted mapping facts through the existing audit port."""

    audit: object

    async def write_stage_migration_audit(
        self, *, record: object, actor: LegacyStageMigrationActor | None = None
    ) -> None:
        write_audit = getattr(self.audit, "write_audit_log", None)
        if not callable(write_audit):
            raise LegacyStageMigrationError("legacy migration audit is unavailable")
        dump = getattr(record, "model_dump", None)
        if not callable(dump):
            raise LegacyStageMigrationError("legacy migration audit is invalid")
        await write_audit(
            event_type="e2_legacy_stage_migration_recorded",
            record={
                "migration": dump(mode="json"),
                "actor": actor.model_dump(mode="json") if actor is not None else None,
            },
        )


__all__ = [
    "LegacyQueueInventoryState",
    "DurableLegacyStageCandidateResolver",
    "LegacyStageCandidateEvidenceStore",
    "LegacyStageReconciliationStore",
    "LegacyStageCandidateResolver",
    "LegacyStageQueueInspector",
    "LegacyStageReservationStore",
    "NoLegacyStageCandidateResolver",
    "RuntimeCanonicalHeldStageWriter",
    "RuntimeLegacyFrozenReconciler",
    "RuntimeLegacyPendingStageInventory",
    "RuntimeLegacyQueueNeutralizer",
    "RuntimeLegacyStageMigrationAudit",
    "RuntimeLegacyStageSourceFence",
    "legacy_stage_source_digest",
]
