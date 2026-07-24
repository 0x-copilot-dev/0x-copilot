"""Shared publication state for the in-memory artifact repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock


@dataclass(frozen=True, slots=True)
class InMemoryArtifactCandidateState:
    provenance_org_id: str | None
    candidate_since: datetime


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


__all__ = ("InMemoryArtifactPublicationCoordinator",)
