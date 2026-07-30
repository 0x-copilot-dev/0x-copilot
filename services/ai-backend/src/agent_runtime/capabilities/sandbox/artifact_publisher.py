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

    Everything published here is :attr:`ArtifactKind.FILE`, whatever its media
    type says — deliberately, and unlike the model-facing ``publish_artifact``
    tool, whose ``_ArtifactMediaPolicy`` refuses ``file`` for a media type a
    structured renderer owns.  The asymmetry is the point:

    * There, ``kind`` and ``media_type`` are two halves of one statement by one
      author, so the policy only makes them agree.  Here the media type is a
      label put on a path *before* the command ran, and the bytes at that path
      are whatever an untrusted process wrote; nothing reads them.  ``file`` is
      the honest claim — bytes came out of a sandbox, at this digest and size.
    * ``kind`` is a byte ceiling, not only a renderer switch: A2 writes blobs
      under ``ArtifactLimits.for_kind`` — 250 MiB for ``file``, 100 MiB for
      ``dataset``, 10 MiB for ``code`` — while the sandbox's own deliverable
      ceiling defaults to 512 MiB.  Reclassifying a 12 MB bundle as ``code``
      would refuse it, and ``SandboxLifecycleCoordinator`` turns one failed
      publication into a failed collection for the whole operation, so the
      other deliverables would be lost with it.
    * This adapter cannot tell a user-facing deliverable from an internal byte
      container.  ``SandboxPatchCollector`` publishes patch-entry bytes through
      the same port, and those are reviewed as a patch, never opened as a tab.

    The place that knows a sandbox file is a report meant to be read as a table
    is the caller that asked for it — ``SandboxDeliverable`` — not this
    transport.  No production caller declares deliverables yet
    (``SandboxLifecycleOperationRunner`` passes none), so that is where the kind
    belongs when one does, rather than being guessed from a label here.
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
                # Never derived from ``media_type``; see the class docstring.
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
