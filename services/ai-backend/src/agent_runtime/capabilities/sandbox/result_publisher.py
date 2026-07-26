"""Artifact-backed publication for redaction-safe sandbox operation results.

The sandbox lifecycle produces bounded stdout/stderr previews.  This module is
the only bridge that persists those bytes: it uses A2's canonical artifact
service and returns the immutable revision reference.  It intentionally owns
no files, provider handle, workspace grant, or second result store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.artifacts.contracts import ArtifactCreateRequest, ArtifactProvenance
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind


class SandboxResultPublication(RuntimeContract):
    """Server-derived metadata for one immutable sandbox-result artifact."""

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


@runtime_checkable
class SandboxResultPublisherPort(Protocol):
    """Publish bounded result bytes and return one immutable artifact revision."""

    async def publish_result(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
    ) -> str:
        """Persist exact bytes through A2 and return ``artifact://…/revisions/N``."""
        ...


class ArtifactServiceSandboxResultPublisher(SandboxResultPublisherPort):
    """A2 adapter with verified caller identity supplied by runtime composition."""

    def __init__(self, *, service: ArtifactService, org_id: str, user_id: str) -> None:
        self._service = service
        self._org_id = org_id
        self._user_id = user_id

    async def publish_result(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
    ) -> str:
        mutation = await self._service.publish_from_stream(
            org_id=self._org_id,
            user_id=self._user_id,
            request=ArtifactCreateRequest(
                run_id=publication.run_id,
                kind=ArtifactKind.FILE,
                title="Sandbox result",
                media_type="application/json",
                suggested_filename="sandbox-result.json",
                expected_digest=publication.content_digest,
                idempotency_key=publication.idempotency_key,
            ),
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.SYSTEM,
                source_ref=f"payload://sandbox/{publication.operation_id}/result",
            ),
            chunks=chunks,
        )
        revision = mutation.record.current_revision.revision
        if (
            revision.content_digest != publication.content_digest
            or revision.byte_size != publication.byte_size
        ):
            raise ValueError("sandbox result artifact did not preserve exact bytes")
        ArtifactContentRefCodec.parse(revision.content_ref)
        return revision.content_ref


__all__ = (
    "ArtifactServiceSandboxResultPublisher",
    "SandboxResultPublication",
    "SandboxResultPublisherPort",
)
