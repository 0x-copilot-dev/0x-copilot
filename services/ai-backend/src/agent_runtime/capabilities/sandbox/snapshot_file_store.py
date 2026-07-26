"""C1/A2-backed immutable snapshot selection and exact-byte file access.

This module is deliberately a composition boundary.  It can resolve an exact
A2 artifact revision using a verified worker-owned identity and stream only
the content reference that it has already authorized.  It never accepts a
workspace path, a desktop grant, a local path, or credentials for the sandbox.

C1 overlay snapshots are resolved through a retained manifest version and the
requested virtual path.  No resolver reads C1's mutable current view as a
fallback, and all resulting content remains A2 digest-pinned bytes.
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
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceEntryKind,
    WorkspaceOverlayVersionRef,
    blob_key_from_content_ref,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from agent_runtime.capabilities.workspace.ports import WorkspaceOverlayStorePort
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
        for entry in plan.entries:
            if entry.source.kind is not SandboxSnapshotSourceKind.OVERLAY:
                continue
            try:
                overlay = WorkspaceOverlayVersionRef.parse(entry.source.source_ref)
            except ValueError as exc:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                    "An authorized immutable sandbox snapshot is unavailable.",
                ) from exc
            if overlay.run_id != identity.run_id:
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

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        """Stream exactly one overlay blob authorized by resolution."""


@dataclass(frozen=True)
class _AuthorizedBlob:
    content_digest: str
    size_bytes: int


@dataclass
class ArtifactRevisionSandboxSnapshotFileStore(SandboxSnapshotFileStorePort):
    """A2-backed store for exact artifact revisions in one verified user scope.

    This is intentionally created per verified identity by a worker composition
    root.  Artifact ids do not contain tenant or run ownership, so a
    process-global unscoped artifact resolver would be an authorization bypass.
    """

    identity: SandboxSnapshotIdentity
    metadata_store: ArtifactMetadataStorePort
    blob_store: ArtifactBlobStorePort
    _authorized_blobs: dict[str, _AuthorizedBlob] = field(
        default_factory=dict, init=False, repr=False
    )

    async def resolve(
        self, *, source: SandboxSnapshotSource, virtual_path: str
    ) -> SandboxResolvedSnapshotSource | None:
        """Resolve one exact artifact revision; refuse every non-A2 source."""

        del virtual_path
        if source.kind is not SandboxSnapshotSourceKind.ARTIFACT:
            return None
        parsed = ArtifactContentRefCodec.parse(source.source_ref)
        record = await self.metadata_store.get_artifact(
            org_id=self.identity.org_id,
            user_id=self.identity.user_id,
            artifact_id=parsed.artifact_id,
        )
        if (
            record is None
            or record.artifact.org_id != self.identity.org_id
            or record.artifact.user_id != self.identity.user_id
            or record.artifact.run_id != self.identity.run_id
        ):
            return None
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


@dataclass
class WorkspaceOverlaySandboxSnapshotFileResolver(
    VersionedOverlaySnapshotFileResolverPort
):
    """C1 retained-manifest resolver backed by A2 content-addressed blobs.

    The resolver is created for one verified run identity.  It accepts only an
    overlay URI for that run, retrieves exactly the requested retained version,
    and authorizes the resolved blob for a later one-shot-style stream.  C1
    metadata never supplies bytes or a host path.
    """

    identity: SandboxSnapshotIdentity
    overlay_store: WorkspaceOverlayStorePort
    blob_store: ArtifactBlobStorePort
    _authorized_blobs: dict[str, _AuthorizedBlob] = field(
        default_factory=dict, init=False, repr=False
    )

    async def resolve_overlay_file(
        self,
        *,
        overlay_ref: str,
        virtual_path: str,
    ) -> SandboxResolvedSnapshotSource | None:
        try:
            overlay = WorkspaceOverlayVersionRef.parse(overlay_ref)
        except ValueError:
            return None
        if overlay.run_id != self.identity.run_id:
            return None
        try:
            manifest = await self.overlay_store.get_manifest_version(
                run_id=overlay.run_id, version=overlay.version
            )
        except WorkspaceOverlayConflictError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc
        if (
            manifest is None
            or manifest.run_id != overlay.run_id
            or manifest.version != overlay.version
        ):
            return None
        entry = manifest.entry_at(virtual_path)
        if (
            entry is None
            or entry.entry_kind is not WorkspaceEntryKind.FILE
            or entry.content_ref is None
            or entry.content_digest is None
            or entry.byte_size is None
        ):
            return None
        try:
            blob_key = blob_key_from_content_ref(entry.content_ref)
        except ValueError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match its immutable overlay.",
            ) from exc
        if blob_key != entry.content_digest:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match its immutable overlay.",
            )
        try:
            stat = await self.blob_store.stat(blob_key)
        except Exception as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc
        if stat.blob_key != blob_key or stat.byte_size != entry.byte_size:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match its immutable overlay.",
            )
        self._authorized_blobs[entry.content_ref] = _AuthorizedBlob(
            content_digest=entry.content_digest,
            size_bytes=entry.byte_size,
        )
        return SandboxResolvedSnapshotSource(
            kind=SandboxSnapshotSourceKind.OVERLAY,
            source_ref=overlay_ref,
            content_ref=entry.content_ref,
            content_digest=entry.content_digest,
            size_bytes=entry.byte_size,
        )

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
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
                            "Sandbox snapshot content did not match its immutable overlay.",
                        )
                    digest.update(chunk)
                    total += len(chunk)
                    if total > authorized.size_bytes:
                        raise SandboxError(
                            SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                            "Sandbox snapshot content did not match its immutable overlay.",
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
                    "Sandbox snapshot content did not match its immutable overlay.",
                )

        return _verified_stream()


@dataclass
class C1A2SandboxSnapshotFileStore(SandboxSnapshotFileStorePort):
    """Compose verified A2 revisions and C1 retained overlays for one run."""

    artifacts: ArtifactRevisionSandboxSnapshotFileStore
    overlays: VersionedOverlaySnapshotFileResolverPort
    _artifact_content_refs: set[str] = field(
        default_factory=set, init=False, repr=False
    )

    async def resolve(
        self, *, source: SandboxSnapshotSource, virtual_path: str
    ) -> SandboxResolvedSnapshotSource | None:
        if source.kind is SandboxSnapshotSourceKind.ARTIFACT:
            resolved = await self.artifacts.resolve(
                source=source, virtual_path=virtual_path
            )
            if resolved is not None:
                self._artifact_content_refs.add(resolved.content_ref)
            return resolved
        if source.kind is SandboxSnapshotSourceKind.OVERLAY:
            return await self.overlays.resolve_overlay_file(
                overlay_ref=source.source_ref, virtual_path=virtual_path
            )
        return None

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        if content_ref in self._artifact_content_refs:
            return await self.artifacts.open(content_ref=content_ref)
        return await self.overlays.open(content_ref=content_ref)


__all__ = (
    "ArtifactRevisionSandboxSnapshotFileStore",
    "C1A2SandboxSnapshotFileStore",
    "SandboxSnapshotIdentity",
    "SandboxSnapshotPlanAuthorityPort",
    "TrustedSandboxSnapshotPlanProvider",
    "VersionedOverlaySnapshotFileResolverPort",
    "WorkspaceOverlaySandboxSnapshotFileResolver",
)
