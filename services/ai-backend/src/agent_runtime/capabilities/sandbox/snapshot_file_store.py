"""Artifact-backed immutable file snapshots and trusted snapshot-plan selection.

This module is deliberately a composition boundary.  It can resolve an exact
A2 artifact revision using a verified worker-owned identity and stream only
the content reference that it has already authorized.  It never accepts a
workspace path, a desktop grant, a local path, or credentials for the sandbox.

C1 overlay snapshots are intentionally not approximated here.  The C1 store
currently exposes only its latest manifest and the D3 snapshot input does not
identify an entry inside a multi-file overlay.  ``VersionedOverlay...Port``
documents the exact immutable input needed before a composed overlay resolver
can be attached.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import hashlib
from typing import Protocol, runtime_checkable

from pydantic import Field, ValidationError

from agent_runtime.artifacts.ports import (
    ArtifactBlobStorePort,
    ArtifactMetadataStorePort,
)
from agent_runtime.capabilities.sandbox.contracts import SandboxError, SandboxErrorCode
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxResolvedSnapshotSource,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotPlan,
    SandboxSnapshotPlanProvider,
    SandboxSnapshotSource,
    SandboxSnapshotSourceKind,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import ArtifactContentRefCodec

_BLOB_REF_PREFIX = "artifact-blob://sha256/"


class SandboxSnapshotIdentity(RuntimeContract):
    """Verified identity used by worker composition, never model-supplied args."""

    run_id: str = Field(min_length=1, max_length=255)
    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)


@runtime_checkable
class SandboxSnapshotPlanAuthorityPort(Protocol):
    """Authoritative per-run selection store owned by worker composition.

    The authority must derive identity from the verified run, and must return
    only a previously authorized reference-only plan.  It deliberately has no
    command, local-path, broker-grant, or secret argument.
    """

    async def load_plan(
        self, *, identity: SandboxSnapshotIdentity
    ) -> SandboxSnapshotPlan | None:
        """Return the exact plan authorized for ``identity``, if one exists."""


@dataclass(frozen=True)
class TrustedSandboxSnapshotPlanProvider(SandboxSnapshotPlanProvider):
    """Adapt a worker-owned plan authority to the model-tool provider port.

    Missing identity or a missing authoritative selection fails closed.  There
    is intentionally no in-memory/default-plan fallback.
    """

    authority: SandboxSnapshotPlanAuthorityPort

    async def snapshot_for(
        self,
        *,
        run_id: str,
        org_id: str | None,
        user_id: str | None,
    ) -> SandboxSnapshotPlan:
        try:
            identity = SandboxSnapshotIdentity(
                run_id=run_id,
                org_id=org_id,
                user_id=user_id,
            )
        except ValidationError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc
        plan = await self.authority.load_plan(identity=identity)
        if plan is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            )
        return plan


@runtime_checkable
class VersionedOverlaySnapshotFileResolverPort(Protocol):
    """Required C1 input for future immutable overlay file resolution.

    ``overlay_ref`` must select a retained manifest version and
    ``virtual_path`` must select one canonical entry within that manifest.  It
    returns only digest-pinned artifact blob metadata.  C1 does not currently
    expose this historical-manifest lookup, so no concrete implementation is
    supplied here.
    """

    async def resolve_overlay_file(
        self,
        *,
        overlay_ref: str,
        virtual_path: str,
    ) -> SandboxResolvedSnapshotSource | None:
        """Resolve exactly one canonical file in an immutable overlay version."""


@dataclass(frozen=True)
class _AuthorizedBlob:
    content_digest: str
    size_bytes: int


@dataclass
class ArtifactRevisionSandboxSnapshotFileStore(SandboxSnapshotFileStorePort):
    """A2-backed store for exact artifact revisions in one verified user scope.

    This is intentionally created per verified identity by a worker composition
    root.  Artifact ids do not contain tenant ownership, so a process-global
    unscoped artifact resolver would be an authorization bypass.
    """

    identity: SandboxSnapshotIdentity
    metadata_store: ArtifactMetadataStorePort
    blob_store: ArtifactBlobStorePort
    _authorized_blobs: dict[str, _AuthorizedBlob] = field(
        default_factory=dict, init=False, repr=False
    )

    async def resolve(
        self, *, source: SandboxSnapshotSource
    ) -> SandboxResolvedSnapshotSource | None:
        """Resolve one exact artifact revision; refuse every non-A2 source."""

        if source.kind is not SandboxSnapshotSourceKind.ARTIFACT:
            return None
        parsed = ArtifactContentRefCodec.parse(source.source_ref)
        stored = await self.metadata_store.get_revision(
            org_id=self.identity.org_id,
            user_id=self.identity.user_id,
            artifact_id=parsed.artifact_id,
            revision=parsed.revision,
        )
        if stored is None:
            return None
        revision = stored.revision
        if (
            revision.content_ref != source.source_ref
            or revision.content_digest != stored.blob_key
        ):
            return None
        try:
            stat = await self.blob_store.stat(stored.blob_key)
        except Exception as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc
        if (
            stat.blob_key != revision.content_digest
            or stat.byte_size != revision.byte_size
        ):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match its immutable revision.",
            )
        content_ref = f"{_BLOB_REF_PREFIX}{revision.content_digest}"
        self._authorized_blobs[content_ref] = _AuthorizedBlob(
            content_digest=revision.content_digest,
            size_bytes=revision.byte_size,
        )
        return SandboxResolvedSnapshotSource(
            kind=SandboxSnapshotSourceKind.ARTIFACT,
            source_ref=source.source_ref,
            content_ref=content_ref,
            content_digest=revision.content_digest,
            size_bytes=revision.byte_size,
        )

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        """Open only a blob previously authorized by :meth:`resolve`.

        The returned stream is re-hashed and re-counted, so a storage race or
        a broken blob adapter cannot silently upload bytes other than the
        immutable manifest describes.
        """

        authorized = self._authorized_blobs.get(content_ref)
        if authorized is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content was not authorized for this operation.",
            )
        try:
            stream = await self.blob_store.open_stream(authorized.content_digest)
        except Exception as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc

        async def _verified_stream() -> AsyncIterator[bytes]:
            digest = hashlib.sha256()
            total = 0
            try:
                async for chunk in stream:
                    if not isinstance(chunk, bytes):
                        raise SandboxError(
                            SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                            "Sandbox snapshot content did not match its immutable revision.",
                        )
                    digest.update(chunk)
                    total += len(chunk)
                    if total > authorized.size_bytes:
                        raise SandboxError(
                            SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                            "Sandbox snapshot content did not match its immutable revision.",
                        )
                    yield chunk
            except SandboxError:
                raise
            except Exception as exc:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                    "An authorized immutable sandbox snapshot is unavailable.",
                ) from exc
            if (
                total != authorized.size_bytes
                or digest.hexdigest() != authorized.content_digest
            ):
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content did not match its immutable revision.",
                )

        return _verified_stream()


__all__ = (
    "ArtifactRevisionSandboxSnapshotFileStore",
    "SandboxSnapshotIdentity",
    "SandboxSnapshotPlanAuthorityPort",
    "TrustedSandboxSnapshotPlanProvider",
    "VersionedOverlaySnapshotFileResolverPort",
)
