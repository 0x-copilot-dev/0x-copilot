"""Immutable Artifact-backed draft-send effect material.

This module is the convergence boundary between the legacy ``/drafts`` virtual
path and the universal effect protocol.  It deliberately stores no draft body:
the proposal names one exact Artifact revision, while a small canonical target
descriptor names the connector operation and its non-body metadata.  The A5
worker re-opens both immutable references immediately before dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from agent_runtime.artifacts import (
    ArtifactBlobStorePort,
    ArtifactNotFoundError,
    ArtifactService,
)
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftPathBinding,
)
from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    EffectStageIdCodec,
    OperationIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import Sha256Hex
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)


_TARGET_REF_PREFIX = "draft-send-target://sha256/"
_TARGET_MAX_BYTES = 64 * 1024
_DRAFT_MAX_BYTES = 20 * 1024 * 1024
_DRAFT_OPERATION_DOMAIN = b"0x-copilot/artifact-draft-send-operation/v1\x00"
_DRAFT_STAGE_DOMAIN = b"0x-copilot/artifact-draft-send-stage/v1\x00"


class ArtifactDraftSendTarget(RuntimeContract):
    """Canonical non-body facts required to send one draft revision.

    ``target_metadata`` is deliberately separate from the draft Artifact.  It
    is part of the digest-pinned target, never copied into the Artifact or a
    legacy draft row, and is re-canonicalised by the worker before dispatch.
    """

    connector: str = Field(min_length=1, max_length=255)
    op: str = Field(min_length=1, max_length=255)
    title: str = Field(default="", max_length=240)
    target_metadata: JsonObject = Field(default_factory=dict)

    @field_validator("connector", "op")
    @classmethod
    def _stable_identifier(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("draft-send target identifiers must be stable")
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    @property
    def digest(self) -> str:
        return sha256_hex(self.canonical_bytes())


class ArtifactDraftRevision(RuntimeContract):
    """One server-authorised immutable Artifact revision for a virtual draft."""

    artifact_id: str
    revision: int = Field(ge=1)
    content_ref: str
    content_digest: Sha256Hex
    media_type: str = Field(min_length=1, max_length=255)
    title: str = Field(default="", max_length=240)

    @field_validator("content_ref")
    @classmethod
    def _content_ref_is_exact_artifact_revision(cls, value: str) -> str:
        ArtifactContentRefCodec.parse(value)
        return value

    @model_validator(mode="after")
    def _content_ref_matches_revision(self) -> "ArtifactDraftRevision":
        parsed = ArtifactContentRefCodec.parse(self.content_ref)
        if parsed.artifact_id != self.artifact_id or parsed.revision != self.revision:
            raise ValueError("content_ref must identify this artifact revision")
        return self


@runtime_checkable
class ArtifactDraftRevisionResolverPort(Protocol):
    """Resolve a virtual draft only through its canonical Artifact revision."""

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        run_id: str,
        draft_id: str,
    ) -> ArtifactDraftRevision | None:
        """Return the current scoped Artifact revision or ``None``."""


@dataclass(frozen=True)
class ArtifactDraftRevisionResolver(ArtifactDraftRevisionResolverPort):
    """Authorise a deterministic draft binding against Artifact metadata."""

    artifacts: ArtifactService

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        run_id: str,
        draft_id: str,
    ) -> ArtifactDraftRevision | None:
        binding = ArtifactDraftPathBinding(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            draft_id=draft_id,
        )
        try:
            record = await self.artifacts.get_metadata(
                org_id=org_id,
                user_id=user_id,
                artifact_id=binding.artifact_id,
            )
        except ArtifactNotFoundError:
            return None
        artifact = record.artifact
        revision = record.current_revision.revision
        if (
            artifact.conversation_id != conversation_id
            or artifact.run_id != run_id
            or revision.source_ref != binding.source_ref
        ):
            return None
        return ArtifactDraftRevision(
            artifact_id=artifact.artifact_id,
            revision=revision.revision,
            content_ref=revision.content_ref,
            content_digest=revision.content_digest,
            media_type=artifact.media_type,
            title=artifact.title,
        )


class ArtifactDraftSendTargetRefCodec:
    """Strict content-addressed reference for non-body draft-send target facts."""

    @classmethod
    def format(cls, digest: str) -> str:
        _validate_digest(digest)
        return f"{_TARGET_REF_PREFIX}{digest}"

    @classmethod
    def parse(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(_TARGET_REF_PREFIX):
            raise ValueError("draft-send target reference is invalid")
        digest = value.removeprefix(_TARGET_REF_PREFIX)
        _validate_digest(digest)
        return digest


@runtime_checkable
class ArtifactDraftSendTargetStorePort(Protocol):
    """Persist/read small target material without ever accepting draft bytes."""

    async def persist(self, *, target: ArtifactDraftSendTarget) -> str:
        """Return a digest-pinned target reference."""

    async def resolve(
        self, *, reference: str, digest: str
    ) -> ArtifactDraftSendTarget | None:
        """Return exactly one retained canonical target or ``None``."""

    def open_reference(self, *, reference: str) -> AsyncIterator[bytes]:
        """Open one target reference for coordinator digest revalidation."""


@dataclass(frozen=True)
class ArtifactDraftSendTargetStore(ArtifactDraftSendTargetStorePort):
    """Content-addressed target descriptor storage over A2's portable ports."""

    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    org_id: str
    user_id: str

    async def persist(self, *, target: ArtifactDraftSendTarget) -> str:
        body = target.canonical_bytes()
        digest = sha256_hex(body)
        reference = ArtifactDraftSendTargetRefCodec.format(digest)
        if await self._has_active_reference(reference=reference, digest=digest):
            return reference
        stored = await self.blobs.put_stream(
            expected_digest=digest,
            chunks=_one_chunk(body),
            byte_limit=_TARGET_MAX_BYTES,
        )
        if stored.blob_key != digest or stored.content_digest != digest:
            raise ValueError("draft-send target material changed during storage")
        seed = f"{self.org_id}\0effect\0{reference}\0{digest}".encode()
        try:
            await self.references.acquire(
                ArtifactReferenceEdge(
                    org_id=self.org_id,
                    edge_id=f"effect-{hashlib.sha256(seed).hexdigest()}",
                    user_id=self.user_id,
                    blob_key=digest,
                    reference_kind=ArtifactReferenceKind.EFFECT,
                    reference_id=reference,
                    created_at=datetime.now(timezone.utc),
                )
            )
        except ValueError:
            # A concurrent identical retry may have acquired the deterministic
            # edge between our read and write. It is idempotent only when the
            # retained reference still names these exact target bytes.
            if not await self._has_active_reference(reference=reference, digest=digest):
                raise
        return reference

    async def resolve(
        self, *, reference: str, digest: str
    ) -> ArtifactDraftSendTarget | None:
        try:
            if ArtifactDraftSendTargetRefCodec.parse(reference) != digest:
                return None
            _validate_digest(digest)
            if not await self._has_active_reference(reference=reference, digest=digest):
                return None
            body = await _read_bounded(
                await self.blobs.open_stream(digest), limit=_TARGET_MAX_BYTES
            )
            if body is None or sha256_hex(body) != digest:
                return None
            target = ArtifactDraftSendTarget.model_validate_json(body)
            return target if target.canonical_bytes() == body else None
        except (CanonicalJsonError, ValueError, TypeError):
            return None
        except Exception:
            return None

    def open_reference(self, *, reference: str) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            digest = ArtifactDraftSendTargetRefCodec.parse(reference)
            target = await self.resolve(reference=reference, digest=digest)
            if target is not None:
                yield target.canonical_bytes()

        return _stream()

    async def _has_active_reference(self, *, reference: str, digest: str) -> bool:
        edges = await self.references.list_edges(
            org_id=self.org_id, user_id=self.user_id
        )
        return any(
            edge.reference_kind is ArtifactReferenceKind.EFFECT
            and edge.reference_id == reference
            and edge.blob_key == digest
            and edge.released_at is None
            for edge in edges
        )


@dataclass(frozen=True)
class ArtifactDraftMcpEffectMaterialResolver:
    """Reconstruct connector arguments from the exact approved Artifact revision."""

    artifacts: ArtifactService
    targets: ArtifactDraftSendTargetStorePort
    org_id: str
    user_id: str
    conversation_id: str
    run_id: str

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> McpEffectMaterial | None:
        try:
            parsed_ref = ArtifactContentRefCodec.parse(request.proposal_content_ref)
            target = await self.targets.resolve(
                reference=request.target_ref,
                digest=request.target_digest,
            )
            if target is None:
                return None
            record, stored, stream = await self.artifacts.stream_revision(
                org_id=self.org_id,
                user_id=self.user_id,
                artifact_id=parsed_ref.artifact_id,
                revision=parsed_ref.revision,
            )
            if (
                record.artifact.conversation_id != self.conversation_id
                or record.artifact.run_id != self.run_id
                or stored.revision.content_ref != request.proposal_content_ref
                or stored.revision.content_digest != request.proposal_digest
                or stored.revision.byte_size > _DRAFT_MAX_BYTES
            ):
                return None
            body = await _read_bounded(stream, limit=_DRAFT_MAX_BYTES)
            if body is None or sha256_hex(body) != request.proposal_digest:
                return None
            text = body.decode("utf-8")
            arguments: JsonObject = {"body": text}
            if target.title:
                arguments["title"] = target.title
            if target.target_metadata:
                arguments["target_metadata"] = dict(target.target_metadata)
            return McpEffectMaterial(
                target_connector=target.connector,
                target_op=target.op,
                arguments=arguments,
                target_ref=request.target_ref,
                target_digest=request.target_digest,
                proposal_ref=request.proposal_ref,
                proposal_content_ref=request.proposal_content_ref,
                proposal_digest=request.proposal_digest,
                arguments_digest=sha256_hex(canonical_json_bytes(arguments)),
            )
        except (ArtifactNotFoundError, UnicodeDecodeError, ValueError, TypeError):
            return None
        except Exception:
            return None

    def open_artifact_reference(self, *, reference: str) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            try:
                parsed = ArtifactContentRefCodec.parse(reference)
                record, stored, body = await self.artifacts.stream_revision(
                    org_id=self.org_id,
                    user_id=self.user_id,
                    artifact_id=parsed.artifact_id,
                    revision=parsed.revision,
                )
                if (
                    record.artifact.conversation_id != self.conversation_id
                    or record.artifact.run_id != self.run_id
                    or stored.revision.content_ref != reference
                    or stored.revision.byte_size > _DRAFT_MAX_BYTES
                ):
                    return
                async for chunk in body:
                    yield chunk
            except Exception:
                return

        return _stream()


def draft_send_operation_id(
    *, artifact: ArtifactDraftRevision, target_digest: str
) -> str:
    """Derive a stable A1-valid operation id for idempotent draft-send staging."""

    _validate_digest(target_digest)
    material = (
        f"{artifact.content_ref}\0{artifact.content_digest}\0{target_digest}"
    ).encode()
    raw = bytearray(hashlib.sha256(_DRAFT_OPERATION_DOMAIN + material).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return OperationIdCodec.format(UUID(bytes=bytes(raw)))


def draft_send_stage_id(*, artifact: ArtifactDraftRevision, target_digest: str) -> str:
    """Derive a stable stage id so an identical stage retry replays safely."""

    _validate_digest(target_digest)
    material = (
        f"{artifact.content_ref}\0{artifact.content_digest}\0{target_digest}"
    ).encode()
    raw = bytearray(hashlib.sha256(_DRAFT_STAGE_DOMAIN + material).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return EffectStageIdCodec.format(UUID(bytes=bytes(raw)))


async def _one_chunk(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _read_bounded(stream: AsyncIterator[bytes], *, limit: int) -> bytes | None:
    content = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(content) + len(chunk) > limit:
            return None
        content.extend(chunk)
    return bytes(content)


def _validate_digest(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("draft-send digest is invalid")


__all__ = (
    "ArtifactDraftMcpEffectMaterialResolver",
    "ArtifactDraftRevision",
    "ArtifactDraftRevisionResolver",
    "ArtifactDraftRevisionResolverPort",
    "ArtifactDraftSendTarget",
    "ArtifactDraftSendTargetRefCodec",
    "ArtifactDraftSendTargetStore",
    "ArtifactDraftSendTargetStorePort",
    "draft_send_operation_id",
    "draft_send_stage_id",
)
