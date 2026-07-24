"""Dependency-inverted ports for artifact metadata, bytes, and references."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactBlobStat,
    ArtifactBlobWriteResult,
    ArtifactCreateCommand,
    ArtifactGcCandidate,
    ArtifactLedgerEvent,
    ArtifactListPage,
    ArtifactListQuery,
    ArtifactMutationResult,
    ArtifactReferenceSnapshot,
    ArtifactScope,
    ArtifactSoftDeleteCommand,
    ArtifactSourceDescriptor,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)


@runtime_checkable
class ArtifactMetadataStorePort(Protocol):
    """Canonical metadata/revision store plus the existing runtime outbox seam."""

    async def create_artifact(
        self, command: ArtifactCreateCommand
    ) -> ArtifactMutationResult:
        """Atomically persist revision 1, idempotency, and ledger commands."""

    async def append_revision(
        self, command: ArtifactAppendCommand
    ) -> ArtifactMutationResult:
        """Compare-and-append one immutable revision and its ledger command."""

    async def get_artifact(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        """Return a caller-owned artifact or ``None`` without disclosing scope."""

    async def get_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
        include_deleted: bool = False,
    ) -> ArtifactStoredRevision | None:
        """Return one immutable revision in caller scope."""

    async def list_artifacts(self, query: ArtifactListQuery) -> ArtifactListPage:
        """Return authoritative metadata ordered by updated_at DESC, id ASC."""

    async def soft_delete(
        self, command: ArtifactSoftDeleteCommand
    ) -> ArtifactStoredRecord | None:
        """Tombstone metadata without synchronously deleting shared bytes."""

    async def list_unreferenced_content(
        self,
        *,
        org_id: str,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]:
        """Return grace-expired blob candidates; never delete them here."""

    async def list_live_blob_keys(self, *, org_id: str) -> frozenset[str]:
        """Return every blob referenced by artifact revisions, including tombstones."""


@runtime_checkable
class ArtifactBlobStorePort(Protocol):
    """Bounded streaming content-addressed blob persistence."""

    async def put_stream(
        self,
        *,
        expected_digest: str | None,
        chunks: AsyncIterator[bytes],
        byte_limit: int,
    ) -> ArtifactBlobWriteResult:
        """Hash and atomically publish a stream, removing temporary state on failure."""

    async def open_stream(
        self,
        blob_key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open an inclusive byte range without materializing the whole body."""

    async def stat(self, blob_key: str) -> ArtifactBlobStat:
        """Return size and range capability for one internal blob key."""

    async def delete_if_unreferenced(
        self,
        blob_key: str,
        *,
        live_blob_keys: frozenset[str],
    ) -> bool:
        """Delete only when ``blob_key`` is absent from the supplied live snapshot."""

    async def list_candidates(
        self, *, older_than: datetime, limit: int
    ) -> Sequence[ArtifactGcCandidate]:
        """Return grace-expired physical objects for reference evaluation."""


@runtime_checkable
class ArtifactRunScopeResolverPort(Protocol):
    """Authorize a run and return its immutable tenant/conversation context."""

    async def resolve_run(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> ArtifactScope | None:
        """Return ``None`` for missing, foreign, or deleted run scope."""


@runtime_checkable
class ArtifactSourceResolverPort(Protocol):
    """Resolve promotable message/result/payload refs inside verified run scope."""

    async def resolve_source(
        self, *, scope: ArtifactScope, source_ref: str
    ) -> ArtifactSourceDescriptor | None:
        """Authorize and describe a server-owned source reference."""

    async def open_source(
        self, *, scope: ArtifactScope, source: ArtifactSourceDescriptor
    ) -> AsyncIterator[bytes]:
        """Stream the already-authorized source bytes."""


@runtime_checkable
class ArtifactLedgerPublisherPort(Protocol):
    """Drain artifact commands through the existing runtime event transport."""

    async def publish(self, event: ArtifactLedgerEvent) -> None:
        """Idempotently append one command and complete its runtime outbox row."""


@runtime_checkable
class ArtifactReferenceProviderPort(Protocol):
    """One reference source used by retention-safe blob garbage collection."""

    async def snapshot(self, *, org_id: str) -> ArtifactReferenceSnapshot:
        """Return the provider's exact live reference set for one tenant."""


__all__ = (
    "ArtifactBlobStorePort",
    "ArtifactLedgerPublisherPort",
    "ArtifactMetadataStorePort",
    "ArtifactReferenceProviderPort",
    "ArtifactRunScopeResolverPort",
    "ArtifactSourceResolverPort",
)
