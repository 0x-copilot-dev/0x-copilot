"""Adapter-level artifact deletion, retention, and quarantine job composition."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from agent_runtime.artifacts.contracts import ArtifactGcCandidate
from runtime_adapters._artifact_repository import (
    ArtifactQuarantineReapResult,
    ArtifactQuarantineReaper,
    ArtifactRetentionPurgeResult,
    ArtifactRetentionPurger,
    ArtifactRetentionScope,
)


_DEFAULT_METADATA_RETENTION_GRACE = timedelta(days=30)
_DEFAULT_CANDIDATE_GRACE = timedelta(days=1)
_DEFAULT_QUARANTINE_GRACE = timedelta(days=1)
_DEFAULT_SWEEP_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ArtifactDeletionInventory:
    """Scoped deletion inventory without content or cross-tenant metadata."""

    artifact_rows: int = 0
    revision_rows: int = 0
    idempotency_rows: int = 0
    reference_edge_rows: int = 0
    gc_candidate_rows: int = 0
    quarantined_digest_rows: int = 0
    reaping_digest_rows: int = 0
    artifact_ids: tuple[str, ...] = ()
    blob_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactLifecycleEvidence:
    """Durable evidence retained when a lifecycle job tombstones artifacts."""

    evidence_id: str
    scope: ArtifactRetentionScope
    reason: str
    created_at: datetime
    tombstoned_artifact_ids: tuple[str, ...]
    inventory_before: ArtifactDeletionInventory


@dataclass(frozen=True, slots=True)
class ArtifactLifecycleTombstoneResult:
    evidence: ArtifactLifecycleEvidence
    inventory_after: ArtifactDeletionInventory


@dataclass(frozen=True, slots=True)
class ArtifactRetentionJobResult:
    purge: ArtifactRetentionPurgeResult
    quarantined_blob_keys: tuple[str, ...]
    reap: ArtifactQuarantineReapResult


@dataclass(frozen=True, slots=True)
class ArtifactLifecycleSchedule:
    """Conservative phases used by the existing runtime retention sweeper."""

    metadata_retention_grace: timedelta = _DEFAULT_METADATA_RETENTION_GRACE
    candidate_grace: timedelta = _DEFAULT_CANDIDATE_GRACE
    quarantine_grace: timedelta = _DEFAULT_QUARANTINE_GRACE
    limit: int = _DEFAULT_SWEEP_LIMIT


@runtime_checkable
class ArtifactLifecycleStorePort(Protocol):
    """Metadata-owned lifecycle operations used by deletion jobs."""

    async def deletion_inventory(
        self,
        *,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory: ...

    async def tombstone_for_lifecycle(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
    ) -> ArtifactLifecycleTombstoneResult: ...

    async def get_lifecycle_evidence(
        self,
        *,
        org_id: str,
        evidence_id: str,
    ) -> ArtifactLifecycleEvidence | None: ...

    async def list_lifecycle_org_ids(self) -> Sequence[str]: ...

    async def list_unreferenced_content(
        self,
        *,
        org_id: str,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]: ...


class ArtifactLifecycleJobs:
    """Actual lifecycle job seam, composed only while ``ARTIFACT_EFFECTS_V2`` is on."""

    def __init__(
        self,
        *,
        store: ArtifactLifecycleStorePort,
        retention_purger: ArtifactRetentionPurger,
        garbage_collector: object,
        quarantine_reaper: ArtifactQuarantineReaper,
        schedule: ArtifactLifecycleSchedule | None = None,
    ) -> None:
        self.store = store
        self.retention_purger = retention_purger
        self.garbage_collector = garbage_collector
        self.quarantine_reaper = quarantine_reaper
        self.schedule = schedule or ArtifactLifecycleSchedule()

    @staticmethod
    def _evidence_id(kind: str, *scope_parts: str) -> str:
        digest = hashlib.sha256(
            "\0".join((kind, *scope_parts)).encode("utf-8")
        ).hexdigest()
        return f"artifact_lifecycle_{digest}"

    @classmethod
    def conversation_evidence_id(
        cls,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        deleted_at: datetime | None = None,
    ) -> str:
        return cls._evidence_id(
            "conversation",
            org_id,
            user_id,
            conversation_id,
            deleted_at.isoformat() if deleted_at is not None else "legacy",
        )

    @classmethod
    def user_evidence_id(
        cls, *, org_id: str, user_id: str, deleted_at: datetime | None = None
    ) -> str:
        return cls._evidence_id(
            "user",
            org_id,
            user_id,
            deleted_at.isoformat() if deleted_at is not None else "legacy",
        )

    @classmethod
    def org_evidence_id(cls, *, org_id: str, deleted_at: datetime | None = None) -> str:
        return cls._evidence_id(
            "org",
            org_id,
            deleted_at.isoformat() if deleted_at is not None else "legacy",
        )

    async def list_org_ids(self) -> Sequence[str]:
        org_ids = set(await self.store.list_lifecycle_org_ids())
        pending = getattr(self.garbage_collector, "has_pending_publications", None)
        recovery_org_id = getattr(
            self.garbage_collector,
            "ORPHAN_RECOVERY_ORG_ID",
            None,
        )
        if callable(pending) and isinstance(recovery_org_id, str) and pending():
            # The physical manifest deliberately has no trusted tenant before
            # metadata commits.  A synthetic worker-only scope gives the
            # normal retention loop a chance to resolve it without exposing a
            # cross-tenant artifact to a product caller.
            org_ids.add(recovery_org_id)
        return tuple(sorted(org_ids))

    async def on_conversation_deleted(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        deleted_at: datetime,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        """Live hook called by persistence after conversation deletion."""

        return await self.tombstone_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            deleted_at=deleted_at,
            evidence_id=self.conversation_evidence_id(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                deleted_at=deleted_at,
            ),
            reason="conversation_deleted",
            protected_conversation_ids=protected_conversation_ids,
        )

    async def on_user_deleted(
        self,
        *,
        org_id: str,
        user_id: str,
        deleted_at: datetime,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        """Live hook called by persistence after user-history deletion."""

        return await self.tombstone_user(
            org_id=org_id,
            user_id=user_id,
            deleted_at=deleted_at,
            evidence_id=self.user_evidence_id(
                org_id=org_id, user_id=user_id, deleted_at=deleted_at
            ),
            reason="user_history_deleted",
            protected_conversation_ids=protected_conversation_ids,
        )

    async def on_org_deleted(
        self,
        *,
        org_id: str,
        deleted_at: datetime,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        """Executable org-erasure hook for the trusted account lifecycle."""

        return await self.tombstone_org(
            org_id=org_id,
            deleted_at=deleted_at,
            evidence_id=self.org_evidence_id(org_id=org_id, deleted_at=deleted_at),
            reason="org_deleted",
            protected_conversation_ids=protected_conversation_ids,
        )

    async def tombstone_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        return await self.store.tombstone_for_lifecycle(
            scope=ArtifactRetentionScope(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                protected_conversation_ids=tuple(
                    sorted(set(protected_conversation_ids))
                ),
            ),
            deleted_at=deleted_at,
            evidence_id=evidence_id,
            reason=reason,
        )

    async def tombstone_user(
        self,
        *,
        org_id: str,
        user_id: str,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        return await self.store.tombstone_for_lifecycle(
            scope=ArtifactRetentionScope(
                org_id=org_id,
                user_id=user_id,
                protected_conversation_ids=tuple(
                    sorted(set(protected_conversation_ids))
                ),
            ),
            deleted_at=deleted_at,
            evidence_id=evidence_id,
            reason=reason,
        )

    async def tombstone_org(
        self,
        *,
        org_id: str,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactLifecycleTombstoneResult:
        return await self.store.tombstone_for_lifecycle(
            scope=ArtifactRetentionScope(
                org_id=org_id,
                protected_conversation_ids=tuple(
                    sorted(set(protected_conversation_ids))
                ),
            ),
            deleted_at=deleted_at,
            evidence_id=evidence_id,
            reason=reason,
        )

    async def run_retention(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_before: datetime,
        candidate_grace_before: datetime,
        quarantine_older_than: datetime,
        limit: int,
    ) -> ArtifactRetentionJobResult:
        discover = getattr(
            self.garbage_collector, "discover_orphaned_publications", None
        )
        physical_candidates: Sequence[ArtifactGcCandidate] = ()
        if callable(discover):
            physical_candidates = await discover(
                provenance_org_id=scope.org_id,
                older_than=candidate_grace_before,
                limit=limit,
            )
        purge = await self.retention_purger.purge_tombstones(
            scope=scope,
            deleted_before=deleted_before,
            limit=limit,
        )
        durable_candidates = await self.store.list_unreferenced_content(
            org_id=scope.org_id,
            older_than=candidate_grace_before,
            limit=limit,
        )
        candidates = {
            candidate.blob_key: candidate
            for candidate in (
                *physical_candidates,
                *purge.eligible_candidates,
                *durable_candidates,
            )
        }
        quarantined: list[str] = []
        for candidate in sorted(
            candidates.values(),
            key=lambda value: (value.unreferenced_since, value.blob_key),
        )[:limit]:
            collected = await self.garbage_collector.collect_if_unreferenced(
                org_id=scope.org_id,
                candidate=candidate,
                grace_before=candidate_grace_before,
            )
            if collected:
                quarantined.append(candidate.blob_key)
        reap = await self.quarantine_reaper.reap_quarantine(
            older_than=quarantine_older_than,
            limit=limit,
        )
        return ArtifactRetentionJobResult(
            purge=purge,
            quarantined_blob_keys=tuple(quarantined),
            reap=reap,
        )

    async def run_scheduled_retention(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int | None = None,
        protected_conversation_ids: tuple[str, ...] = (),
    ) -> ArtifactRetentionJobResult:
        """Run all three durable phases for one org from the live sweeper."""

        schedule = self.schedule
        return await self.run_retention(
            scope=ArtifactRetentionScope(
                org_id=org_id,
                protected_conversation_ids=tuple(
                    sorted(set(protected_conversation_ids))
                ),
            ),
            deleted_before=now - schedule.metadata_retention_grace,
            candidate_grace_before=now - schedule.candidate_grace,
            quarantine_older_than=now - schedule.quarantine_grace,
            limit=max(1, limit or schedule.limit),
        )


__all__ = (
    "ArtifactDeletionInventory",
    "ArtifactLifecycleEvidence",
    "ArtifactLifecycleJobs",
    "ArtifactLifecycleSchedule",
    "ArtifactLifecycleStorePort",
    "ArtifactLifecycleTombstoneResult",
    "ArtifactRetentionJobResult",
)
