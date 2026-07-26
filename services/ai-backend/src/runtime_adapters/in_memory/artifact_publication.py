"""Shared publication state for the in-memory artifact repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from runtime_adapters._artifact_repository import ArtifactGcCandidateScope


@dataclass(frozen=True, slots=True)
class InMemoryArtifactCandidateState:
    provenance_org_id: str | None
    candidate_since: datetime
    scopes: tuple[ArtifactGcCandidateScope, ...] = ()


@dataclass(frozen=True, slots=True)
class InMemoryArtifactQuarantineState:
    body: bytes
    created_at: datetime
    quarantined_at: datetime


class InMemoryArtifactPublicationCoordinator:
    """One lock and byte inventory shared by publication, references, and GC."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.blobs: dict[str, bytes] = {}
        self.created_at: dict[str, datetime] = {}
        self.candidates: dict[str, InMemoryArtifactCandidateState] = {}
        self.quarantine: dict[str, InMemoryArtifactQuarantineState] = {}

    def restore_locked(self, blob_key: str) -> bool:
        """Restore a GC-quarantined digest while ``lock`` is held."""

        quarantined = self.quarantine.pop(blob_key, None)
        if quarantined is None:
            return False
        self.blobs[blob_key] = quarantined.body
        self.created_at[blob_key] = quarantined.created_at
        return True

    def cancel_candidate_locked(self, blob_key: str) -> None:
        self.candidates.pop(blob_key, None)

    def record_candidate_locked(
        self,
        *,
        blob_key: str,
        provenance_org_id: str | None,
        candidate_since: datetime,
        scopes: tuple[ArtifactGcCandidateScope, ...] = (),
    ) -> None:
        """Retain the oldest eligibility clock and every trustworthy owner.

        A digest can be shared by multiple tenant artifacts.  Keeping the
        union means a late hold in *any* owning scope blocks physical
        reclamation even after those logical rows have been purged.
        """

        current = self.candidates.get(blob_key)
        merged_scopes = tuple(
            sorted(
                {
                    *(current.scopes if current is not None else ()),
                    *scopes,
                },
                key=lambda scope: (
                    scope.org_id,
                    scope.user_id or "",
                    scope.conversation_id or "",
                ),
            )
        )
        next_state = InMemoryArtifactCandidateState(
            provenance_org_id=(
                current.provenance_org_id
                if current is not None and current.provenance_org_id is not None
                else provenance_org_id
            ),
            candidate_since=(
                min(current.candidate_since, candidate_since)
                if current is not None
                else candidate_since
            ),
            scopes=merged_scopes,
        )
        self.candidates[blob_key] = next_state

    def candidate_scopes_locked(
        self, *, blob_key: str
    ) -> tuple[ArtifactGcCandidateScope, ...]:
        candidate = self.candidates.get(blob_key)
        return candidate.scopes if candidate is not None else ()


__all__ = ("InMemoryArtifactPublicationCoordinator",)
