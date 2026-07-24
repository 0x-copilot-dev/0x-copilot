"""Runtime composition adapters for the canonical Artifact Repository.

This module adapts existing run/message/event read ports to the artifact
domain. It never constructs storage and never dereferences filesystem paths.
The runtime adapter factory owns metadata/blob construction; this layer only
combines those injected ports into ``ArtifactService``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from agent_runtime.api.ports import PersistencePort
from agent_runtime.artifacts import (
    ArtifactBlobStorePort,
    ArtifactMetadataStorePort,
    ArtifactNotFoundError,
    ArtifactScope,
    ArtifactService,
    ArtifactSourceDescriptor,
)
from agent_runtime.artifacts.contracts import validate_artifact_source_ref


class _ArtifactMessageByIdPort(Protocol):
    """Exact, scoped message lookup implemented by each runtime store."""

    async def get_message_by_id(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        message_id: str,
    ) -> object | None: ...


@dataclass(frozen=True)
class ArtifactSourceSnapshot:
    """One immutable byte snapshot returned by an indexed source lookup."""

    source_ref: str
    content: bytes
    byte_size: int
    content_digest: str
    media_type: str
    title: str

    @classmethod
    def from_bytes(
        cls,
        *,
        source_ref: str,
        content: bytes,
        media_type: str,
        title: str,
    ) -> ArtifactSourceSnapshot:
        return cls(
            source_ref=source_ref,
            content=content,
            byte_size=len(content),
            content_digest=hashlib.sha256(content).hexdigest(),
            media_type=media_type,
            title=title,
        )


@runtime_checkable
class ArtifactSourceLookupPort(Protocol):
    """O(1) or indexed lookup for promotable source bytes."""

    async def get_message_snapshot(
        self,
        *,
        scope: ArtifactScope,
        message_id: str,
    ) -> ArtifactSourceSnapshot | None: ...


class RuntimeArtifactSourceLookup:
    """Adapt a runtime store's exact message query to source snapshots."""

    def __init__(self, messages: _ArtifactMessageByIdPort) -> None:
        self._messages = messages

    async def get_message_snapshot(
        self,
        *,
        scope: ArtifactScope,
        message_id: str,
    ) -> ArtifactSourceSnapshot | None:
        message = await self._messages.get_message_by_id(
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
            run_id=scope.run_id,
            message_id=message_id,
        )
        if message is None:
            return None
        content = getattr(message, "content_text", None)
        if not isinstance(content, str) or not content:
            return None
        content_format = getattr(message, "content_format", "")
        media_type = (
            "text/markdown; charset=utf-8"
            if content_format in {"markdown", "md"}
            else "text/plain; charset=utf-8"
        )
        return ArtifactSourceSnapshot.from_bytes(
            source_ref=f"message://{message_id}",
            content=content.encode("utf-8"),
            media_type=media_type,
            title="Conversation message",
        )


class RuntimeArtifactRunScopeResolver:
    """Resolve a run only when both tenant and owner match."""

    def __init__(self, persistence: PersistencePort) -> None:
        self._persistence = persistence

    async def resolve_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> ArtifactScope | None:
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            return None
        return ArtifactScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            trace_id=run.trace_id,
        )


class RuntimeArtifactSourceResolver:
    """Resolve supported logical sources without scanning run history.

    A2 supports ``message://`` through an exact indexed store lookup.
    ``operation://`` and ``payload://`` validate but return scoped not-found
    until their stores expose equivalent indexed immutable-snapshot queries.
    They never fall back to replaying a run or reading a server-local path.
    """

    CHUNK_BYTES = 64 * 1024

    def __init__(self, lookup: ArtifactSourceLookupPort) -> None:
        self._lookup = lookup

    async def resolve_source(
        self,
        *,
        scope: ArtifactScope,
        source_ref: str,
    ) -> ArtifactSourceDescriptor | None:
        snapshot = await self._resolve(scope=scope, source_ref=source_ref)
        if snapshot is None:
            return None
        return ArtifactSourceDescriptor(
            source_ref=snapshot.source_ref,
            byte_size=snapshot.byte_size,
            content_digest=snapshot.content_digest,
            media_type=snapshot.media_type,
            title=snapshot.title,
        )

    async def open_source(
        self,
        *,
        scope: ArtifactScope,
        source: ArtifactSourceDescriptor,
    ) -> AsyncIterator[bytes]:
        snapshot = await self._resolve(scope=scope, source_ref=source.source_ref)
        if snapshot is None:
            raise ArtifactNotFoundError()
        return self._stream_content(snapshot.content)

    async def _resolve(
        self,
        *,
        scope: ArtifactScope,
        source_ref: str,
    ) -> ArtifactSourceSnapshot | None:
        try:
            canonical = validate_artifact_source_ref(source_ref)
        except ValueError:
            return None
        if canonical.startswith("message://"):
            return await self._lookup.get_message_snapshot(
                scope=scope,
                message_id=canonical.removeprefix("message://"),
            )
        # No event replay: these schemes stay typed-not-found until an indexed,
        # immutable source query is available through ArtifactSourceLookupPort.
        return None

    @classmethod
    async def _stream_content(cls, content: bytes) -> AsyncIterator[bytes]:
        for offset in range(0, len(content), cls.CHUNK_BYTES):
            yield content[offset : offset + cls.CHUNK_BYTES]


class ArtifactServiceComposition:
    """Build the domain service from a storage-owned runtime port bundle."""

    @classmethod
    def build(cls, ports: object) -> ArtifactService | None:
        metadata = cast(
            ArtifactMetadataStorePort | None,
            getattr(ports, "artifact_metadata_store", None),
        )
        blobs = cast(
            ArtifactBlobStorePort | None,
            getattr(ports, "artifact_blob_store", None),
        )
        persistence = cast(
            PersistencePort | None,
            getattr(ports, "persistence", None),
        )
        source_lookup = cast(
            ArtifactSourceLookupPort | None,
            getattr(ports, "artifact_source_lookup", None),
        )
        if (
            metadata is None
            or blobs is None
            or persistence is None
            or source_lookup is None
        ):
            return None
        return ArtifactService(
            metadata=metadata,
            blobs=blobs,
            run_scopes=RuntimeArtifactRunScopeResolver(persistence),
            sources=RuntimeArtifactSourceResolver(source_lookup),
        )


__all__ = (
    "ArtifactServiceComposition",
    "ArtifactSourceLookupPort",
    "ArtifactSourceSnapshot",
    "RuntimeArtifactRunScopeResolver",
    "RuntimeArtifactSourceLookup",
    "RuntimeArtifactSourceResolver",
)
