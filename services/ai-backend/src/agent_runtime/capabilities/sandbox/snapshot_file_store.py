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
import asyncio
import hashlib
import os
from pathlib import Path
import stat
import tempfile
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


@dataclass(frozen=True)
class _SealedSnapshotContent:
    """One fully verified, worker-local staging object.

    This is deliberately not another logical content authority: callers cannot
    name it, it has no URI, and it is reachable only from the in-memory
    authorization map created while resolving a C1/A2 source.  It exists so
    the provider never receives a byte before the entire selected source has
    passed its declared digest and size checks.
    """

    path: Path
    content_digest: str
    size_bytes: int


class SealedSandboxSnapshotFileStore(SandboxSnapshotFileStorePort):
    """Pre-verify an immutable source before the coordinator can open it.

    A plain blob stream can report a digest mismatch only *after* yielding its
    first bytes.  Passing that stream through to ``DeepAgentSandboxRuntime``
    would allow an online provider to receive unverified content.  This adapter
    consumes a bounded source into a private, atomic file under the trusted
    file-store root, verifies the complete body, and only then exposes an
    iterator.  ``open`` re-verifies the sealed file before it returns its
    iterator, so a local disk race also cannot turn into a provider upload.

    The temporary files are an implementation-detail spill buffer, not a
    second artifact/result store: no model-visible reference can address them,
    and restart loses the in-memory authorization map that makes them readable.
    """

    _MODE = 0o600

    def __init__(
        self,
        *,
        source: SandboxSnapshotFileStorePort,
        root: Path,
        max_entry_bytes: int,
    ) -> None:
        if max_entry_bytes < 1:
            raise ValueError("sandbox sealed snapshot limit must be positive")
        self._source = source
        self._root = root.resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._max_entry_bytes = max_entry_bytes
        self._sealed: dict[str, _SealedSnapshotContent] = {}
        self._lock = asyncio.Lock()

    async def resolve(
        self, *, source: SandboxSnapshotSource, virtual_path: str
    ) -> SandboxResolvedSnapshotSource | None:
        resolved = await self._source.resolve(source=source, virtual_path=virtual_path)
        if resolved is None:
            return None
        await self._seal(resolved)
        return resolved

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        """Return bytes only after re-verifying the entire sealed object."""

        async with self._lock:
            sealed = self._sealed.get(content_ref)
            if sealed is None:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                    "Sandbox snapshot content was not authorized for this operation.",
                )
            content = await asyncio.to_thread(self._read_verified, sealed)

        async def _stream() -> AsyncIterator[bytes]:
            if content:
                yield content

        return _stream()

    async def _seal(self, resolved: SandboxResolvedSnapshotSource) -> None:
        if resolved.size_bytes > self._max_entry_bytes:
            raise SandboxError(
                SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED,
                "Sandbox snapshot content exceeds the per-file ceiling.",
            )
        async with self._lock:
            prior = self._sealed.get(resolved.content_ref)
            if prior is not None:
                if (
                    prior.content_digest != resolved.content_digest
                    or prior.size_bytes != resolved.size_bytes
                ):
                    raise SandboxError(
                        SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                        "Sandbox snapshot content did not match its immutable source.",
                    )
                # The sealed file is re-verified below before any later open.
                self._read_verified(prior)
                return

            temporary_path: Path | None = None
            try:
                descriptor, raw_path = tempfile.mkstemp(
                    prefix="snapshot-",
                    suffix=".tmp",
                    dir=self._root,
                )
                temporary_path = Path(raw_path)
                os.chmod(temporary_path, self._MODE)
                digest = hashlib.sha256()
                total = 0
                with os.fdopen(descriptor, "wb") as handle:
                    # Acquire the source only after ``fdopen`` owns the
                    # descriptor.  A source-open failure must still close the
                    # worker-private temporary file before it is unlinked.
                    stream = await self._source.open(content_ref=resolved.content_ref)
                    async for chunk in stream:
                        if not isinstance(chunk, bytes):
                            raise SandboxError(
                                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                                "Sandbox snapshot content did not match its immutable source.",
                            )
                        total += len(chunk)
                        if total > resolved.size_bytes or total > self._max_entry_bytes:
                            raise SandboxError(
                                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                                "Sandbox snapshot content did not match its immutable source.",
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if (
                    total != resolved.size_bytes
                    or digest.hexdigest() != resolved.content_digest
                ):
                    raise SandboxError(
                        SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                        "Sandbox snapshot content did not match its immutable source.",
                    )
                target = self._sealed_path(resolved.content_ref)
                os.replace(temporary_path, target)
                temporary_path = None
                self._fsync_directory()
                self._sealed[resolved.content_ref] = _SealedSnapshotContent(
                    path=target,
                    content_digest=resolved.content_digest,
                    size_bytes=resolved.size_bytes,
                )
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalize storage details
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                    "An authorized immutable sandbox snapshot is unavailable.",
                ) from exc
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        pass

    def _read_verified(self, sealed: _SealedSnapshotContent) -> bytes:
        """Read one private regular file fully before yielding it to a provider."""

        mode = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            mode |= os.O_NOFOLLOW
        try:
            descriptor = os.open(sealed.path, mode)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("sealed sandbox snapshot is not a regular file")
                chunks: list[bytes] = []
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = os.read(descriptor, 64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > sealed.size_bytes or total > self._max_entry_bytes:
                        raise OSError("sealed sandbox snapshot exceeds its limit")
                    digest.update(chunk)
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED,
                "An authorized immutable sandbox snapshot is unavailable.",
            ) from exc
        if total != sealed.size_bytes or digest.hexdigest() != sealed.content_digest:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH,
                "Sandbox snapshot content did not match its immutable source.",
            )
        return b"".join(chunks)

    def _sealed_path(self, content_ref: str) -> Path:
        key = hashlib.sha256(content_ref.encode("utf-8")).hexdigest()
        return self._root / f"{key}.sealed"

    def _fsync_directory(self) -> None:
        descriptor = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


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
    "SealedSandboxSnapshotFileStore",
    "TrustedSandboxSnapshotPlanProvider",
    "VersionedOverlaySnapshotFileResolverPort",
    "WorkspaceOverlaySandboxSnapshotFileResolver",
)
