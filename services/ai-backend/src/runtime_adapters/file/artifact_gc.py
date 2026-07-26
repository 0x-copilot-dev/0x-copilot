"""Cross-process coordinated file artifact garbage collection."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from agent_runtime.artifacts.contracts import ArtifactGcCandidate
from runtime_adapters._artifact_repository import ArtifactQuarantineReapResult
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
    ) -> ArtifactQuarantineReapResult:
        reaped: list[str] = []
        restored: list[str] = []
        with self.coordinator.locked():
            ordered = sorted(
                self.coordinator.quarantine.items(),
                key=lambda item: (item[1].quarantined_at, item[0]),
            )
            for blob_key, state in ordered:
                if len(reaped) + len(restored) >= limit:
                    break
                if state.quarantined_at >= older_than:
                    continue
                if self.reference_store.has_reference_locked(blob_key=blob_key):
                    self.coordinator.restore_locked(blob_key)
                    self.coordinator.cancel_candidate_locked(blob_key)
                    restored.append(blob_key)
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
        )


__all__ = ("FileArtifactGarbageCollector",)
