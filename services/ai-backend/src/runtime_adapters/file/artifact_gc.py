"""Cross-process coordinated file artifact garbage collection."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timezone

from agent_runtime.artifacts.contracts import ArtifactGcCandidate
from runtime_adapters._artifact_repository import (
    ArtifactGcCandidateScope,
    ArtifactQuarantineReapResult,
)
from runtime_adapters.artifact_lifecycle import ORPHAN_PUBLICATION_RECOVERY_ORG_ID
from runtime_adapters.artifact_references import FileArtifactReferenceStore
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)


class FileArtifactGarbageCollector:
    """Authoritatively recheck ledgers and atomically quarantine bytes."""

    ORPHAN_RECOVERY_ORG_ID = ORPHAN_PUBLICATION_RECOVERY_ORG_ID

    def __init__(
        self,
        layout: FileStoreLayout,
        coordinator: FileArtifactPublicationCoordinator,
        reference_store: FileArtifactReferenceStore,
    ) -> None:
        if reference_store.coordinator is not coordinator:
            raise ValueError("artifact adapters must share one publication coordinator")
        self.layout = layout
        self.coordinator = coordinator
        self.reference_store = reference_store
        self._hold_revalidator: (
            Callable[[tuple[ArtifactGcCandidateScope, ...]], bool] | None
        ) = None

    def set_hold_revalidator(
        self,
        revalidator: Callable[[tuple[ArtifactGcCandidateScope, ...]], bool],
    ) -> None:
        """Install the runtime-owned live-hold checker at composition time."""

        self._hold_revalidator = revalidator

    def has_active_hold_locked(self, *, blob_key: str) -> bool:
        revalidator = self._hold_revalidator
        if revalidator is None:
            return False
        scopes = self.coordinator.candidate_scopes_locked(blob_key=blob_key)
        # A legacy candidate without persisted ownership cannot be reconciled
        # against a hold added after its metadata disappeared.  The safe
        # recovery is to withhold rather than make a deletion guess.
        return not scopes or bool(revalidator(scopes))

    def has_active_hold(self, *, blob_key: str) -> bool:
        with self.coordinator.locked():
            return self.has_active_hold_locked(blob_key=blob_key)

    def has_pending_publications(self) -> bool:
        """Report only durable manifest work, never by walking object shards."""

        with self.coordinator.locked():
            return any(
                state.provenance_org_id is None
                for state in self.coordinator.candidates.values()
            )

    async def collect_if_unreferenced(
        self,
        *,
        org_id: str,
        candidate: ArtifactGcCandidate,
        grace_before: datetime,
    ) -> bool:
        with self.coordinator.locked():
            if candidate.unreferenced_since > grace_before:
                return False
            durable_candidate = self.coordinator.candidates.get(candidate.blob_key)
            if (
                durable_candidate is None
                or durable_candidate.candidate_since != candidate.unreferenced_since
                or durable_candidate.candidate_since > grace_before
            ):
                return False
            if self.reference_store.has_reference_locked(blob_key=candidate.blob_key):
                return False
            if self.has_active_hold_locked(blob_key=candidate.blob_key):
                return False
            quarantine = self.coordinator.quarantine_path(candidate.blob_key)
            if quarantine.exists():
                return candidate.blob_key in self.coordinator.quarantine
            active = self.layout.object_path(candidate.blob_key)
            FileStoreLayout.ensure_dir(quarantine.parent)
            try:
                os.replace(active, quarantine)
            except FileNotFoundError:
                return False
            self.coordinator._fsync_directory(active.parent)
            self.coordinator._fsync_directory(quarantine.parent)
            self.coordinator.mark_quarantined_locked(
                blob_key=candidate.blob_key,
                quarantined_at=datetime.now(timezone.utc),
            )
            return True

    async def reap_quarantine(
        self,
        *,
        older_than: datetime,
        limit: int,
        provenance_org_id: str | None = None,
    ) -> ArtifactQuarantineReapResult:
        reaped: list[str] = []
        restored: list[str] = []
        withheld: list[str] = []
        with self.coordinator.locked():
            ordered = sorted(
                self.coordinator.quarantine.items(),
                key=lambda item: (item[1].quarantined_at, item[0]),
            )
            attempted = 0
            for blob_key, state in ordered:
                if attempted >= limit:
                    break
                candidate = self.coordinator.candidates.get(blob_key)
                if provenance_org_id is not None and (
                    candidate is None
                    or candidate.provenance_org_id != provenance_org_id
                ):
                    continue
                if state.quarantined_at >= older_than:
                    continue
                attempted += 1
                if self.reference_store.has_reference_locked(blob_key=blob_key):
                    self.coordinator.restore_locked(blob_key)
                    self.coordinator.cancel_candidate_locked(blob_key)
                    restored.append(blob_key)
                    continue
                if self.has_active_hold_locked(blob_key=blob_key):
                    withheld.append(blob_key)
                    continue
                quarantine = self.coordinator.quarantine_path(blob_key)
                reaping = self.coordinator.reaping_path(blob_key)
                if not quarantine.exists():
                    if self.layout.object_path(blob_key).exists():
                        self.coordinator.cancel_candidate_locked(blob_key)
                        restored.append(blob_key)
                    else:
                        self.coordinator.clear_reaped_locked(blob_key)
                        reaped.append(blob_key)
                    continue
                FileStoreLayout.ensure_dir(reaping.parent)
                os.replace(quarantine, reaping)
                self.coordinator._fsync_directory(quarantine.parent)
                self.coordinator._fsync_directory(reaping.parent)
                reaping.unlink()
                self.coordinator._fsync_directory(reaping.parent)
                integrity = (
                    self.layout.objects_dir
                    / ".integrity"
                    / blob_key[:2]
                    / f"{blob_key}.json"
                )
                try:
                    integrity.unlink()
                    self.coordinator._fsync_directory(integrity.parent)
                except FileNotFoundError:
                    pass
                self.coordinator.clear_reaped_locked(blob_key)
                reaped.append(blob_key)
        return ArtifactQuarantineReapResult(
            reaped_blob_keys=tuple(reaped),
            restored_blob_keys=tuple(restored),
            withheld_blob_keys=tuple(withheld),
        )


__all__ = ("FileArtifactGarbageCollector",)
