"""A2-backed exact-byte publication adapter for sandbox deliverables."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.artifacts.contracts import (
    ArtifactCreateRequest,
    ArtifactProvenance,
)
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxArtifactPublication,
)
from agent_runtime.capabilities.sandbox.ports import SandboxArtifactPublisherPort
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind


class ArtifactServiceSandboxPublisher(SandboxArtifactPublisherPort):
    """Publish a sandbox stream through A2's canonical artifact transaction.

    The adapter has verified caller identity injected at construction.  Neither
    the model nor the sandbox sends org/user IDs, an artifact ID, a blob key, or
    a host path across this boundary.
    """

    def __init__(self, *, service: ArtifactService, org_id: str, user_id: str) -> None:
        self._service = service
        self._org_id = org_id
        self._user_id = user_id

    async def publish(
        self,
        *,
        publication: SandboxArtifactPublication,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactRef:
        result = await self._service.publish_from_stream(
            org_id=self._org_id,
            user_id=self._user_id,
            request=ArtifactCreateRequest(
                run_id=publication.run_id,
                kind=ArtifactKind.FILE,
                title=publication.title,
                media_type=publication.media_type,
                suggested_filename=publication.suggested_filename,
                expected_digest=publication.content_digest,
                idempotency_key=publication.idempotency_key,
            ),
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.SYSTEM,
                source_ref=(
                    f"payload://sandbox/{publication.operation_id}/{publication.source_path.lstrip('/')}"
                ),
            ),
            chunks=chunks,
        )
        revision = result.record.current_revision.revision
        return ArtifactRef(
            artifact_id=revision.artifact_id,
            sha256=revision.content_digest,
            size_bytes=revision.byte_size,
        )


__all__ = ("ArtifactServiceSandboxPublisher",)
