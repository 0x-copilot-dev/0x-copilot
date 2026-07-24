"""Coordinated in-memory artifact garbage collection."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.artifacts.contracts import ArtifactGcCandidate
from runtime_adapters._artifact_repository import ArtifactQuarantineReapResult
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactQuarantineState,
    InMemoryArtifactPublicationCoordinator,
)


class InMemoryArtifactGarbageCollector:
    """Final-check references and move bytes to a restorable map."""

    def __init__(
        self,
        coordinator: InMemoryArtifactPublicationCoordinator,
        metadata_store: InMemoryArtifactMetadataStore,
        reference_store: InMemoryArtifactReferenceStore,
    ) -> None:
        if (
            metadata_store.coordinator is not coordinator
            or reference_store.coordinator is not coordinator
        ):
            raise ValueError("artifact adapters must share one publication coordinator")
        self.coordinator = coordinator
        self.metadata_store = metadata_store
        self.reference_store = reference_store

    async def collect_if_unreferenced(
        self,
        *,
        org_id: str,
        candidate: ArtifactGcCandidate,
        grace_before: datetime,
    ) -> bool:
        with self.coordinator.lock:
            if candidate.unreferenced_since > grace_before:
                return False
            durable_candidate = self.coordinator.candidates.get(candidate.blob_key)
            if (
                durable_candidate is None
                or durable_candidate.candidate_since != candidate.unreferenced_since
                or durable_candidate.candidate_since > grace_before
            ):
                return False
            if self.metadata_store.has_revision_reference_locked(
                blob_key=candidate.blob_key
            ):
                return False
            if self.reference_store.has_reference_locked(blob_key=candidate.blob_key):
                return False
            body = self.coordinator.blobs.pop(candidate.blob_key, None)
            if body is None:
                return candidate.blob_key in self.coordinator.quarantine
            created_at = self.coordinator.created_at.pop(candidate.blob_key)
            self.coordinator.quarantine[candidate.blob_key] = (
                InMemoryArtifactQuarantineState(
                    body=body,
                    created_at=created_at,
                    quarantined_at=datetime.now(timezone.utc),
                )
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
        with self.coordinator.lock:
            ordered = sorted(
                self.coordinator.quarantine.items(),
                key=lambda item: (item[1].quarantined_at, item[0]),
            )
            for blob_key, state in ordered:
                if len(reaped) + len(restored) >= limit:
                    break
                if state.quarantined_at >= older_than:
                    continue
                if self.metadata_store.has_revision_reference_locked(
                    blob_key=blob_key
                ) or self.reference_store.has_reference_locked(blob_key=blob_key):
                    self.coordinator.restore_locked(blob_key)
                    self.coordinator.cancel_candidate_locked(blob_key)
                    restored.append(blob_key)
                    continue
                self.coordinator.quarantine.pop(blob_key, None)
                self.coordinator.candidates.pop(blob_key, None)
                reaped.append(blob_key)
        return ArtifactQuarantineReapResult(
            reaped_blob_keys=tuple(reaped),
            restored_blob_keys=tuple(restored),
        )


__all__ = ("InMemoryArtifactGarbageCollector",)
