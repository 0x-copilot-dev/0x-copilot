"""Artifact-backed publication for redaction-safe sandbox operation results.

The sandbox lifecycle produces bounded stdout/stderr previews.  This module is
the only bridge that persists those bytes: it uses A2's canonical artifact
service and returns the immutable revision reference.  It intentionally owns
no files, provider handle, workspace grant, or second result store.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.artifacts.contracts import ArtifactCreateRequest, ArtifactProvenance
from agent_runtime.artifacts.service import ArtifactService
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind


class SandboxResultPublication(RuntimeContract):
    """Server-derived metadata for one immutable sandbox outcome artifact."""

    run_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    document_kind: Literal["result", "patch"] = "result"
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


@runtime_checkable
class SandboxResultPublisherPort(Protocol):
    """Publish bounded result/patch bytes and return an immutable artifact revision."""

    async def publish_result(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
    ) -> str:
        """Persist exact bytes through A2 and return ``artifact://…/revisions/N``."""
        ...

    async def publish_patch(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
    ) -> str:
        """Persist one complete patch manifest through the same A2 authority."""
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
        if publication.document_kind != "result":
            raise ValueError(
                "sandbox result publication must have document_kind=result"
            )
        return await self._publish(
            publication=publication,
            chunks=chunks,
            title="Sandbox result",
            filename="sandbox-result.json",
            source_suffix="result",
        )

    async def publish_patch(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
    ) -> str:
        if publication.document_kind != "patch":
            raise ValueError("sandbox patch publication must have document_kind=patch")
        return await self._publish(
            publication=publication,
            chunks=chunks,
            title="Sandbox patch proposal",
            filename="sandbox-patch.json",
            source_suffix="patch",
        )

    async def _publish(
        self,
        *,
        publication: SandboxResultPublication,
        chunks: AsyncIterator[bytes],
        title: str,
        filename: str,
        source_suffix: str,
    ) -> str:
        mutation = await self._service.publish_from_stream(
            org_id=self._org_id,
            user_id=self._user_id,
            request=ArtifactCreateRequest(
                run_id=publication.run_id,
                # ``FILE`` for the same reason as every other sandbox
                # publication (see ``ArtifactServiceSandboxPublisher``), and
                # unambiguously so here: this is a machine-readable envelope the
                # model dereferences by ``result_ref``, not authored content, and
                # ``application/json`` has no single owning renderer anyway —
                # ``_ArtifactMediaPolicy`` counts it as both dataset and code.
                kind=ArtifactKind.FILE,
                title=title,
                media_type="application/json",
                suggested_filename=filename,
                expected_digest=publication.content_digest,
                idempotency_key=publication.idempotency_key,
            ),
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.SYSTEM,
                source_ref=(
                    f"payload://sandbox/{publication.operation_id}/{source_suffix}"
                ),
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
