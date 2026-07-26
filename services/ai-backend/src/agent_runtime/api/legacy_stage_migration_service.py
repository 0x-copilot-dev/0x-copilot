"""Fail-closed E2 D5 migration for pending legacy stages and approvals.

This service is deliberately an administrative migration worker, not a legacy
execution bridge.  It can create a canonical *held* stage from independently
verified source facts, cancel an unclaimed old command, or freeze an uncertain
legacy command for observation.  It never copies an approval, returns an old
command to a worker, or calls an executor.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agent_runtime.effects.contracts import (
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyStageMigrationOutcome,
    LegacyStageMigrationRecord,
    LegacyStageMigrationStore,
)


class LegacyPendingStageStatus(StrEnum):
    """The only D5 source states a migration adapter may report."""

    COMPATIBILITY_ONLY = "compatibility_only"
    UNAPPROVED_PROPOSED = "unapproved_proposed"
    UNAPPROVED_HELD = "unapproved_held"
    APPROVED_UNAPPLIED = "approved_unapplied"
    QUEUED_UNCLAIMED = "queued_unclaimed"
    CLAIMED = "claimed"
    INDETERMINATE = "indeterminate"
    QUARANTINED = "quarantined"


class LegacyQueueNeutralizationOutcome(StrEnum):
    """Digest-CAS result for an old queue command.

    ``cancelled`` and ``already_cancelled`` are the sole outcomes that permit
    creating a canonical stage.  A claimed command is ambiguous and freezes;
    a changed source is never guessed at and quarantines.
    """

    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    CLAIMED = "claimed"
    SOURCE_CHANGED = "source_changed"


class LegacySourceFenceOutcome(StrEnum):
    """Outcome of the writer's last-moment source verification/reservation."""

    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    STAGED = "staged"
    SOURCE_CHANGED = "source_changed"


class LegacyCanonicalStageWriteResult(RuntimeContract):
    """Result of the writer's effect fence and held-stage creation.

    A writer returns ``source_changed`` rather than throwing for an expected
    compare-and-swap loss.  That lets the migration durably quarantine the
    source and audit the refusal, while a genuine infrastructure failure still
    fails the control-plane call without creating a mapping.
    """

    fence_outcome: LegacySourceFenceOutcome
    canonical_stage_id: str | None = None

    def is_created(self) -> bool:
        """Whether the source was reserved and a held stage is available."""

        return (
            self.fence_outcome
            in {
                LegacySourceFenceOutcome.RESERVED,
                LegacySourceFenceOutcome.ALREADY_RESERVED,
                LegacySourceFenceOutcome.STAGED,
            }
            and self.canonical_stage_id is not None
        )


class LegacyCanonicalStageCandidate(RuntimeContract):
    """Exact canonical material independently proven by a source adapter.

    ``ProposedEffect`` contains only immutable refs and digests.  The source
    adapter must refuse to construct this object unless it has proved the old
    proposal bytes, canonical arguments/target, and all relevant digests.  An
    old approval is intentionally absent from this model.
    """

    scope: EffectStageScope
    proposal: ProposedEffect
    policy_snapshot: EffectPolicySnapshot


class LegacyPendingStage(RuntimeContract):
    """One tenant-scoped old pending item, sorted by run and stage identity."""

    org_id: str
    run_id: str
    legacy_stage_id: str
    source_digest: str
    status: LegacyPendingStageStatus
    candidate: LegacyCanonicalStageCandidate | None = None


class LegacyStageMigrationReport(RuntimeContract):
    """Content-free result of one bounded D5 scan."""

    org_id: str
    migration_id: str
    dry_run: bool
    scanned: int
    compatibility_only: int
    canonical_held: int
    frozen_reconcile: int
    quarantined: int
    queue_commands_cancelled: int
    report_digest: str


class LegacyStageMigrationActor(RuntimeContract):
    """Authenticated sealed-job identity recorded with every real mutation."""

    operator_ref: str
    migration_job_id: str


class LegacyStageMigrationError(RuntimeError):
    """Safe administrative migration failure."""


@runtime_checkable
class LegacyPendingStageInventoryPort(Protocol):
    """Stable tenant-scoped D5 input; it has no execution method."""

    async def list_pending_legacy_stages(
        self, *, org_id: str, after: tuple[str, str] | None, limit: int
    ) -> Sequence[LegacyPendingStage]:
        """Return an ordered bounded page in ``(run_id, legacy_stage_id)`` order."""


@runtime_checkable
class CanonicalHeldStageWriter(Protocol):
    """Create/replay an unapproved canonical stage using a stable migration key."""

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
        """Atomically verify/reserve the exact source and return a held stage.

        The writer is an authority boundary: accepting an inventory candidate is
        insufficient.  It must re-check the trusted source digest and reserve
        that exact old stage before emitting any canonical event.
        """

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        """Record that the staged canonical work has its durable mapping."""


@runtime_checkable
class LegacyStageSourceFence(Protocol):
    """Atomic source verifier/reserver used inside the canonical writer.

    Inventory is advisory.  This port is the authority which locks/rechecks
    the real legacy record immediately before canonical event creation.
    """

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
        """Return a digest-CAS reservation outcome, never a best-effort bool."""

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        """Advance a materialization only after its mapping is durable."""

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        """Terminally release stale reserved source facts without dispatching."""


@runtime_checkable
class LegacyQueueNeutralizer(Protocol):
    """Cancel only an unclaimed legacy command; no claim/dispatch API exists."""

    async def cancel_unclaimed(
        self, *, org_id: str, run_id: str, legacy_stage_id: str, source_digest: str
    ) -> LegacyQueueNeutralizationOutcome:
        """CAS-cancel/replay exact old work or report an unsafe race honestly."""


@runtime_checkable
class LegacyFrozenReconciler(Protocol):
    """Persist observation-only reconciliation for an uncertain legacy command."""

    async def freeze(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        actor: LegacyStageMigrationActor | None = None,
    ) -> None:
        """Freeze for terminal observation only; it must never redispatch."""

    async def release(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        actor: LegacyStageMigrationActor | None = None,
    ) -> None:
        """Close a frozen checkpoint only after a fresh scan proves a new state."""


@runtime_checkable
class LegacyStageMigrationAuditPort(Protocol):
    """Append a redacted, idempotent mapping audit record."""

    async def write_stage_migration_audit(
        self,
        *,
        record: LegacyStageMigrationRecord,
        actor: LegacyStageMigrationActor | None = None,
    ) -> None:
        """Persist one exact mapping audit record without source bodies or approvals."""


@dataclass(frozen=True)
class LegacyStageMigrationService:
    """Checkpointed D5 migration with one explicit fresh-approval boundary."""

    inventory: LegacyPendingStageInventoryPort
    mappings: LegacyStageMigrationStore
    writer: CanonicalHeldStageWriter
    queue: LegacyQueueNeutralizer
    reconciler: LegacyFrozenReconciler
    audit: LegacyStageMigrationAuditPort
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    _MAX_PAGE = 100

    async def run(
        self,
        *,
        org_id: str,
        migration_id: str,
        batch_size: int,
        dry_run: bool,
        actor: LegacyStageMigrationActor | None = None,
    ) -> LegacyStageMigrationReport:
        if not 1 <= batch_size <= self._MAX_PAGE:
            raise LegacyStageMigrationError("invalid migration batch")
        page = tuple(
            await self.inventory.list_pending_legacy_stages(
                org_id=org_id, after=None, limit=batch_size
            )
        )
        self._validate_page(org_id=org_id, page=page, limit=batch_size)
        outcomes: list[LegacyStageMigrationRecord] = []
        for item in page:
            if dry_run:
                outcomes.append(
                    self._planned_record(item=item, migration_id=migration_id)
                )
                continue
            record = await self._migrate_one(
                item=item, migration_id=migration_id, actor=actor
            )
            await self.audit.write_stage_migration_audit(record=record, actor=actor)
            outcomes.append(record)
        return self._report(
            org_id=org_id,
            migration_id=migration_id,
            dry_run=dry_run,
            records=tuple(outcomes),
        )

    async def _migrate_one(
        self,
        *,
        item: LegacyPendingStage,
        migration_id: str,
        actor: LegacyStageMigrationActor | None,
    ) -> LegacyStageMigrationRecord:
        outcome = self._outcome_for(item)
        # Frozen work is intentionally checkpointed separately from immutable
        # mapping records.  The next scan must re-evaluate it rather than
        # replaying a permanent frozen result forever.
        if outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE:
            return await self._frozen_record(
                item=item, migration_id=migration_id, actor=actor
            )
        release = getattr(self.reconciler, "release", None)
        if callable(release):
            await release(
                org_id=item.org_id,
                run_id=item.run_id,
                legacy_stage_id=item.legacy_stage_id,
                source_digest=item.source_digest,
                actor=actor,
            )
        existing = await self.mappings.load(
            org_id=item.org_id,
            migration_id=migration_id,
            run_id=item.run_id,
            legacy_stage_id=item.legacy_stage_id,
        )
        replacing_frozen = (
            existing is not None
            and existing.outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE
        )
        if existing is not None:
            if replacing_frozen:
                # A frozen checkpoint is a durable observation task, never a
                # forever-binding mapping. Continue with the fresh inventory
                # facts; the store below permits replacing only this outcome.
                existing = None
            elif existing.source_digest != item.source_digest:
                raise LegacyStageMigrationError("legacy stage source changed")
            elif (
                existing.outcome is LegacyStageMigrationOutcome.CANONICAL_HELD
                and existing.canonical_stage_id is not None
            ):
                mark_mapped = getattr(self.writer, "mark_mapped", None)
                if callable(mark_mapped):
                    await mark_mapped(
                        org_id=item.org_id,
                        run_id=item.run_id,
                        legacy_stage_id=item.legacy_stage_id,
                        expected_source_digest=item.source_digest,
                        canonical_stage_id=existing.canonical_stage_id,
                    )
            if existing is not None:
                return existing

        queue_cancelled = False
        reconciler_frozen = False
        canonical_stage_id: str | None = None
        if item.status is LegacyPendingStageStatus.QUEUED_UNCLAIMED:
            # Neutralize old work before a canonical stage exists.  A failure
            # leaves no mapping and therefore no safe migration result.
            queue_outcome = await self.queue.cancel_unclaimed(
                org_id=item.org_id,
                run_id=item.run_id,
                legacy_stage_id=item.legacy_stage_id,
                source_digest=item.source_digest,
            )
            if queue_outcome in {
                LegacyQueueNeutralizationOutcome.CANCELLED,
                LegacyQueueNeutralizationOutcome.ALREADY_CANCELLED,
            }:
                queue_cancelled = True
            elif queue_outcome is LegacyQueueNeutralizationOutcome.CLAIMED:
                outcome = LegacyStageMigrationOutcome.FROZEN_RECONCILE
            elif queue_outcome is LegacyQueueNeutralizationOutcome.SOURCE_CHANGED:
                outcome = LegacyStageMigrationOutcome.QUARANTINED
            else:  # pragma: no cover - enum exhaustiveness guard
                raise LegacyStageMigrationError("legacy queue outcome is invalid")
        if outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE:
            return await self._frozen_record(
                item=item, migration_id=migration_id, actor=actor
            )
        if outcome is LegacyStageMigrationOutcome.CANONICAL_HELD:
            if item.candidate is None:
                raise LegacyStageMigrationError("unproven canonical material")
            if item.candidate.scope.run_id != item.run_id:
                raise LegacyStageMigrationError("canonical scope does not match source")
            if not item.candidate.proposal.agent_hold:
                # A source adapter may prove bytes/arguments, but it may not
                # preserve an old permission by accidentally selecting an
                # allow-always posture.  Every migrated stage starts held.
                raise LegacyStageMigrationError("canonical migration must be held")
            write_result = await self.writer.create_held_stage(
                org_id=item.org_id,
                run_id=item.run_id,
                legacy_stage_id=item.legacy_stage_id,
                expected_source_digest=item.source_digest,
                candidate=item.candidate,
                idempotency_key=self._idempotency_key(
                    item=item, migration_id=migration_id
                ),
            )
            if write_result.is_created():
                canonical_stage_id = write_result.canonical_stage_id
            elif write_result.fence_outcome is LegacySourceFenceOutcome.SOURCE_CHANGED:
                outcome = LegacyStageMigrationOutcome.QUARANTINED
            else:  # pragma: no cover - result model makes this unreachable
                raise LegacyStageMigrationError("canonical source fence is invalid")
        record = LegacyStageMigrationRecord(
            org_id=item.org_id,
            migration_id=migration_id,
            run_id=item.run_id,
            legacy_stage_id=item.legacy_stage_id,
            source_digest=item.source_digest,
            outcome=outcome,
            canonical_stage_id=canonical_stage_id,
            queue_cancelled=queue_cancelled,
            reconciler_frozen=reconciler_frozen,
            revision=0,
            created_at=self.now(),
            updated_at=self.now(),
        )
        persisted = (
            await self.mappings.replace_frozen(record=record)
            if replacing_frozen
            else await self.mappings.load_or_create(record=record)
        )
        if (
            persisted.outcome is LegacyStageMigrationOutcome.CANONICAL_HELD
            and persisted.canonical_stage_id is not None
        ):
            mark_mapped = getattr(self.writer, "mark_mapped", None)
            if callable(mark_mapped):
                await mark_mapped(
                    org_id=item.org_id,
                    run_id=item.run_id,
                    legacy_stage_id=item.legacy_stage_id,
                    expected_source_digest=item.source_digest,
                    canonical_stage_id=persisted.canonical_stage_id,
                )
        return persisted

    async def _frozen_record(
        self,
        *,
        item: LegacyPendingStage,
        migration_id: str,
        actor: LegacyStageMigrationActor | None,
    ) -> LegacyStageMigrationRecord:
        await self.reconciler.freeze(
            org_id=item.org_id,
            run_id=item.run_id,
            legacy_stage_id=item.legacy_stage_id,
            source_digest=item.source_digest,
            actor=actor,
        )
        return LegacyStageMigrationRecord(
            org_id=item.org_id,
            migration_id=migration_id,
            run_id=item.run_id,
            legacy_stage_id=item.legacy_stage_id,
            source_digest=item.source_digest,
            outcome=LegacyStageMigrationOutcome.FROZEN_RECONCILE,
            canonical_stage_id=None,
            queue_cancelled=False,
            reconciler_frozen=True,
            revision=0,
            created_at=self.now(),
            updated_at=self.now(),
        )

    def _planned_record(
        self, *, item: LegacyPendingStage, migration_id: str
    ) -> LegacyStageMigrationRecord:
        outcome = self._outcome_for(item)
        return LegacyStageMigrationRecord(
            org_id=item.org_id,
            migration_id=migration_id,
            run_id=item.run_id,
            legacy_stage_id=item.legacy_stage_id,
            source_digest=item.source_digest,
            outcome=outcome,
            canonical_stage_id=(
                "stg_planned"
                if outcome is LegacyStageMigrationOutcome.CANONICAL_HELD
                else None
            ),
            queue_cancelled=item.status is LegacyPendingStageStatus.QUEUED_UNCLAIMED,
            reconciler_frozen=outcome is LegacyStageMigrationOutcome.FROZEN_RECONCILE,
            revision=0,
            created_at=self.now(),
            updated_at=self.now(),
        )

    @staticmethod
    def _outcome_for(item: LegacyPendingStage) -> LegacyStageMigrationOutcome:
        if item.status is LegacyPendingStageStatus.COMPATIBILITY_ONLY:
            return LegacyStageMigrationOutcome.COMPATIBILITY_ONLY
        if item.status in {
            LegacyPendingStageStatus.CLAIMED,
            LegacyPendingStageStatus.INDETERMINATE,
        }:
            return LegacyStageMigrationOutcome.FROZEN_RECONCILE
        if (
            item.status
            in {
                LegacyPendingStageStatus.UNAPPROVED_PROPOSED,
                LegacyPendingStageStatus.UNAPPROVED_HELD,
                LegacyPendingStageStatus.APPROVED_UNAPPLIED,
                LegacyPendingStageStatus.QUEUED_UNCLAIMED,
            }
            and item.candidate is not None
        ):
            # APPROVED_UNAPPLIED intentionally lands here too: the old
            # approval is not represented by candidate or writer APIs.
            return LegacyStageMigrationOutcome.CANONICAL_HELD
        return LegacyStageMigrationOutcome.QUARANTINED

    @staticmethod
    def _validate_page(
        *, org_id: str, page: Sequence[LegacyPendingStage], limit: int
    ) -> None:
        keys = tuple((item.run_id, item.legacy_stage_id) for item in page)
        if (
            len(page) > limit
            or tuple(sorted(keys)) != keys
            or len(set(keys)) != len(keys)
            or any(item.org_id != org_id for item in page)
        ):
            raise LegacyStageMigrationError("legacy stage inventory is invalid")

    @staticmethod
    def _idempotency_key(*, item: LegacyPendingStage, migration_id: str) -> str:
        material = f"{migration_id}\0{item.org_id}\0{item.run_id}\0{item.legacy_stage_id}\0{item.source_digest}"
        return f"e2stage_{hashlib.sha256(material.encode()).hexdigest()}"

    @staticmethod
    def _report(
        *,
        org_id: str,
        migration_id: str,
        dry_run: bool,
        records: tuple[LegacyStageMigrationRecord, ...],
    ) -> LegacyStageMigrationReport:
        def count(outcome: LegacyStageMigrationOutcome) -> int:
            return sum(record.outcome is outcome for record in records)

        body = {
            "org_id": org_id,
            "migration_id": migration_id,
            "dry_run": dry_run,
            "records": [
                {
                    "run_id": record.run_id,
                    "legacy_stage_id": record.legacy_stage_id,
                    "source_digest": record.source_digest,
                    "outcome": record.outcome.value,
                    "canonical_stage_id": record.canonical_stage_id,
                    "queue_cancelled": record.queue_cancelled,
                    "reconciler_frozen": record.reconciler_frozen,
                }
                for record in records
            ],
        }
        return LegacyStageMigrationReport(
            org_id=org_id,
            migration_id=migration_id,
            dry_run=dry_run,
            scanned=len(records),
            compatibility_only=count(LegacyStageMigrationOutcome.COMPATIBILITY_ONLY),
            canonical_held=count(LegacyStageMigrationOutcome.CANONICAL_HELD),
            frozen_reconcile=count(LegacyStageMigrationOutcome.FROZEN_RECONCILE),
            quarantined=count(LegacyStageMigrationOutcome.QUARANTINED),
            queue_commands_cancelled=sum(record.queue_cancelled for record in records),
            report_digest=canonical_json_sha256(body),
        )


__all__ = [
    "CanonicalHeldStageWriter",
    "LegacyCanonicalStageWriteResult",
    "LegacyCanonicalStageCandidate",
    "LegacyFrozenReconciler",
    "LegacyPendingStage",
    "LegacyPendingStageInventoryPort",
    "LegacyPendingStageStatus",
    "LegacyQueueNeutralizationOutcome",
    "LegacySourceFenceOutcome",
    "LegacyStageSourceFence",
    "LegacyQueueNeutralizer",
    "LegacyStageMigrationAuditPort",
    "LegacyStageMigrationActor",
    "LegacyStageMigrationError",
    "LegacyStageMigrationReport",
    "LegacyStageMigrationService",
]
