"""Host-write-free mutation service for a durable workspace overlay."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceBaseEntry,
    WorkspaceEntryKind,
    WorkspaceMutationResult,
    WorkspaceOperation,
    content_ref_for_blob,
    mount_id_for_path,
    normalize_virtual_path,
    sha256_digest,
)
from agent_runtime.capabilities.workspace.errors import (
    WorkspaceEditError,
    WorkspaceIsDirectoryError,
    WorkspaceLimitError,
    WorkspaceNotFoundError,
    WorkspaceOverlayError,
)
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceBaseReadPort,
    WorkspaceOverlayStorePort,
)


class WorkspaceOverlayService:
    """Propose mutations into a run-scoped overlay, never into the base workspace.

    A2 owns the immutable bytes through ``ArtifactBlobStorePort``.  This service
    records only its digest and an opaque reference, then appends one metadata
    revision through the overlay store.  A4 stage binding is intentionally left
    for the next integration slice; ``stage_id`` fields remain available on the
    durable entry contract so that binding does not reshape persisted overlays.
    """

    MAX_ENTRIES = 1_000
    MAX_TOTAL_RESULT_BYTES = 250 * 1024 * 1024
    MAX_FILE_BYTES = 100 * 1024 * 1024

    def __init__(
        self,
        *,
        run_id: str,
        base_read: WorkspaceBaseReadPort,
        overlay_store: WorkspaceOverlayStorePort,
        blob_store: ArtifactBlobStorePort,
    ) -> None:
        self._run_id = run_id
        self._base_read = base_read
        self._overlay_store = overlay_store
        self._blob_store = blob_store

    async def manifest(self) -> OverlayManifest:
        """Return the current immutable run overlay manifest."""

        return await self._overlay_store.get_manifest(run_id=self._run_id)

    async def propose_create(
        self, virtual_path: str, content: bytes | str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create a new overlay file, failing if the merged target already exists."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if await self._merged_entry_exists(path, manifest):
            raise WorkspaceOverlayError("Workspace path already exists.")
        return await self._write(
            path=path,
            content=self._as_bytes(content),
            author=author,
            manifest=manifest,
            operation=WorkspaceOperation.CREATE,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
        )

    async def propose_replace(
        self, virtual_path: str, content: bytes | str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create or replace a file while preserving its original base precondition."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        existing = manifest.entry_at(path)
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.DIRECTORY:
            raise WorkspaceIsDirectoryError()
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.TOMBSTONE:
            # Recreating an existing base file retains its original compare-and-swap
            # guard.  Recreating an overlay-only file stays a create.
            operation = (
                WorkspaceOperation.CREATE
                if existing.baseline.existence is BaseExistence.MUST_NOT_EXIST
                else WorkspaceOperation.REPLACE
            )
            baseline = existing.baseline
        elif existing is not None:
            operation = existing.operation
            baseline = existing.baseline
        else:
            base = await self._base_read.stat(path)
            if base is None:
                operation = WorkspaceOperation.CREATE
                baseline = BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST)
            else:
                if base.entry_kind is WorkspaceEntryKind.DIRECTORY:
                    raise WorkspaceIsDirectoryError()
                operation = WorkspaceOperation.REPLACE
                baseline = await self._precondition_for_base(path, base)
        return await self._write(
            path=path,
            content=self._as_bytes(content),
            author=author,
            manifest=manifest,
            operation=operation,
            baseline=baseline,
        )

    async def propose_delete(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Place one explicit tombstone in the overlay; never infer descendants."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        existing = manifest.entry_at(path)
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.TOMBSTONE:
            raise WorkspaceNotFoundError()
        if (
            existing is not None
            and existing.baseline.existence is BaseExistence.MUST_NOT_EXIST
        ):
            # create → delete before a host apply has no durable target left.
            updated = await self._overlay_store.append_revision(
                run_id=self._run_id,
                expected_version=manifest.version,
                mutations=(
                    OverlayMutation(
                        kind=OverlayMutationKind.REMOVE,
                        virtual_path=path,
                    ),
                ),
            )
            return WorkspaceMutationResult(manifest=updated)

        if existing is not None:
            baseline = existing.baseline
        else:
            base = await self._base_read.stat(path)
            if base is None:
                raise WorkspaceNotFoundError()
            baseline = await self._precondition_for_base(path, base)
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.TOMBSTONE,
            operation=WorkspaceOperation.DELETE,
            baseline=baseline,
            stage_id=existing.stage_id if existing is not None else None,
            stage_revision=existing.stage_revision if existing is not None else None,
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def propose_move(
        self,
        source_virtual_path: str,
        destination_virtual_path: str,
        *,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        """Record an explicit same-mount move without altering either base path."""

        source = normalize_virtual_path(source_virtual_path)
        destination = normalize_virtual_path(destination_virtual_path)
        if source == destination or mount_id_for_path(source) != mount_id_for_path(
            destination
        ):
            raise WorkspaceOverlayError("Workspace move must stay within one mount.")
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if not await self._merged_entry_exists(source, manifest):
            raise WorkspaceNotFoundError()
        if await self._merged_entry_exists(destination, manifest):
            raise WorkspaceOverlayError("Workspace move destination already exists.")

        source_entry = manifest.entry_at(source)
        if source_entry is not None:
            baseline = source_entry.baseline
        else:
            base = await self._base_read.stat(source)
            if base is None:  # race-safe guard after the merged existence check
                raise WorkspaceNotFoundError()
            baseline = await self._precondition_for_base(source, base)

        tombstone = OverlayEntry(
            virtual_path=source,
            entry_kind=WorkspaceEntryKind.TOMBSTONE,
            operation=WorkspaceOperation.MOVE,
            baseline=baseline,
            stage_id=source_entry.stage_id if source_entry is not None else None,
            stage_revision=(
                source_entry.stage_revision if source_entry is not None else None
            ),
            author=author,
        )
        content = (
            {
                "content_ref": source_entry.content_ref,
                "content_digest": source_entry.content_digest,
                "byte_size": source_entry.byte_size,
            }
            if source_entry is not None
            and source_entry.entry_kind is WorkspaceEntryKind.FILE
            else {}
        )
        moved = OverlayEntry(
            virtual_path=destination,
            entry_kind=WorkspaceEntryKind.MOVE,
            operation=WorkspaceOperation.MOVE,
            source_virtual_path=source,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            stage_id=source_entry.stage_id if source_entry is not None else None,
            stage_revision=(
                source_entry.stage_revision if source_entry is not None else None
            ),
            author=author,
            **content,
        )
        updated = await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=manifest.version,
            mutations=(
                OverlayMutation(
                    kind=OverlayMutationKind.UPSERT,
                    virtual_path=source,
                    entry=tombstone,
                ),
                OverlayMutation(
                    kind=OverlayMutationKind.UPSERT,
                    virtual_path=destination,
                    entry=moved,
                ),
            ),
        )
        return WorkspaceMutationResult(
            entry=updated.entry_at(destination), manifest=updated
        )

    async def propose_mkdir(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create exactly one virtual directory; parent recursion is explicit."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if await self._merged_entry_exists(path, manifest):
            raise WorkspaceOverlayError("Workspace path already exists.")
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.DIRECTORY,
            operation=WorkspaceOperation.MKDIR,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def propose_edit(
        self,
        virtual_path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        """Apply a strict textual replacement against the merged virtual bytes."""

        from agent_runtime.capabilities.workspace.merged_backend import (
            MergedWorkspaceBackend,
        )

        path = normalize_virtual_path(virtual_path)
        if not old_string:
            raise WorkspaceEditError("Workspace edit requires a non-empty old string.")
        backend = MergedWorkspaceBackend(
            run_id=self._run_id,
            base_read=self._base_read,
            overlay_store=self._overlay_store,
            blob_store=self._blob_store,
            overlay_service=self,
        )
        try:
            current = (await backend.aread_bytes(path)).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceEditError(
                "Workspace edit requires a UTF-8 text file."
            ) from exc
        occurrences = current.count(old_string)
        if occurrences == 0:
            raise WorkspaceEditError("Workspace edit string was not found.")
        if occurrences > 1 and not replace_all:
            raise WorkspaceEditError("Workspace edit string is ambiguous.")
        updated = current.replace(old_string, new_string, -1 if replace_all else 1)
        return await self.propose_replace(path, updated, author=author)

    async def _write(
        self,
        *,
        path: str,
        content: bytes,
        author: str,
        manifest: OverlayManifest,
        operation: WorkspaceOperation,
        baseline: BasePrecondition,
    ) -> WorkspaceMutationResult:
        if len(content) > self.MAX_FILE_BYTES:
            raise WorkspaceLimitError("Workspace file exceeds the overlay file limit.")
        await self._check_limits(manifest=manifest, path=path, byte_size=len(content))
        written = await self._blob_store.put_stream(
            expected_digest=sha256_digest(content),
            chunks=self._single_chunk(content),
            byte_limit=self.MAX_FILE_BYTES,
        )
        current = manifest.entry_at(path)
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.FILE,
            operation=operation,
            content_ref=content_ref_for_blob(written.blob_key),
            content_digest=written.content_digest,
            byte_size=written.byte_size,
            baseline=baseline,
            stage_id=current.stage_id if current is not None else None,
            stage_revision=(current.stage_revision if current is not None else None),
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def _append(
        self, manifest: OverlayManifest, mutation: OverlayMutation
    ) -> WorkspaceMutationResult:
        updated = await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=manifest.version,
            mutations=(mutation,),
        )
        return WorkspaceMutationResult(
            entry=updated.entry_at(mutation.virtual_path), manifest=updated
        )

    async def bind_stage(
        self,
        *,
        virtual_paths: tuple[str, ...],
        stage_id: str,
        stage_revision: int,
        expected_manifest_version: int,
    ) -> OverlayManifest:
        """Bind exact current overlay entries to one A4 stage revision.

        The binding is a second optimistic manifest revision.  A stale caller
        cannot silently attach an approval surface to newer overlay content:
        the compare-and-append fails and the new content remains unbound/held.
        """

        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if manifest.version != expected_manifest_version:
            from agent_runtime.capabilities.workspace.errors import (  # noqa: PLC0415
                WorkspaceOverlayConflictError,
            )

            raise WorkspaceOverlayConflictError()
        mutations: list[OverlayMutation] = []
        for raw_path in virtual_paths:
            path = normalize_virtual_path(raw_path)
            entry = manifest.entry_at(path)
            if entry is None:
                raise WorkspaceNotFoundError()
            mutations.append(
                OverlayMutation(
                    kind=OverlayMutationKind.UPSERT,
                    virtual_path=path,
                    entry=entry.model_copy(
                        update={
                            "stage_id": stage_id,
                            "stage_revision": stage_revision,
                        }
                    ),
                )
            )
        return await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=manifest.version,
            mutations=tuple(mutations),
        )

    async def _precondition_for_base(
        self, path: str, entry: WorkspaceBaseEntry
    ) -> BasePrecondition:
        if entry.entry_kind is WorkspaceEntryKind.FILE:
            digest = entry.content_digest
            if digest is None:
                digest = sha256_digest(await self._read_base_bytes(path))
            return BasePrecondition(
                existence=BaseExistence.MUST_EXIST,
                entry_kind=WorkspaceEntryKind.FILE,
                opaque_generation=entry.opaque_generation,
                content_digest=digest,
                stable_file_id=entry.stable_file_id,
                byte_size=entry.byte_size,
                mtime_ns=entry.mtime_ns,
            )
        return BasePrecondition(
            existence=BaseExistence.MUST_EXIST,
            entry_kind=entry.entry_kind,
            opaque_generation=entry.opaque_generation,
            stable_file_id=entry.stable_file_id,
            byte_size=entry.byte_size,
            mtime_ns=entry.mtime_ns,
        )

    async def _merged_entry_exists(self, path: str, manifest: OverlayManifest) -> bool:
        entry = manifest.entry_at(path)
        if entry is not None:
            return entry.entry_kind is not WorkspaceEntryKind.TOMBSTONE
        return await self._base_read.stat(path) is not None

    async def _check_limits(
        self, *, manifest: OverlayManifest, path: str, byte_size: int
    ) -> None:
        current = manifest.entry_at(path)
        entry_count = len(manifest.entries) + (0 if current is not None else 1)
        if entry_count > self.MAX_ENTRIES:
            raise WorkspaceLimitError("Workspace overlay entry limit was exceeded.")
        referenced = sum(
            entry.byte_size or 0
            for entry in manifest.entries
            if entry.entry_kind is WorkspaceEntryKind.FILE
        )
        if current is not None and current.entry_kind is WorkspaceEntryKind.FILE:
            referenced -= current.byte_size or 0
        if referenced + byte_size > self.MAX_TOTAL_RESULT_BYTES:
            raise WorkspaceLimitError("Workspace overlay byte limit was exceeded.")

    async def _read_base_bytes(self, path: str) -> bytes:
        stream = await self._base_read.read(path)
        body = bytearray()
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise TypeError("workspace base read chunks must be bytes")
            body.extend(chunk)
            if len(body) > self.MAX_FILE_BYTES:
                raise WorkspaceLimitError(
                    "Workspace base file exceeds the overlay file limit."
                )
        return bytes(body)

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        yield content

    @staticmethod
    def _as_bytes(content: bytes | str) -> bytes:
        return content if isinstance(content, bytes) else content.encode("utf-8")


__all__ = ("WorkspaceOverlayService",)
