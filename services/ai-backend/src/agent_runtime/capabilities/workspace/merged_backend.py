"""Merged virtual workspace reads plus overlay-only mutation entry points."""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Sequence

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.workspace.contracts import (
    OverlayEntry,
    OverlayManifest,
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
    WorkspaceEntryKind,
    WorkspaceMutationResult,
    blob_key_from_content_ref,
    normalize_virtual_path,
)
from agent_runtime.capabilities.workspace.errors import (
    WorkspaceIsDirectoryError,
    WorkspaceNotFoundError,
)
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceBaseReadPort,
    WorkspaceOverlayStorePort,
)


class MergedWorkspaceBackend:
    """One run's read-your-writes workspace facade.

    The only authority passed in is ``WorkspaceBaseReadPort``.  Every mutation
    delegates to ``WorkspaceOverlayService``; there is intentionally no host
    mutation client, callback, or generic executor in this object graph.
    """

    def __init__(
        self,
        *,
        run_id: str,
        base_read: WorkspaceBaseReadPort,
        overlay_store: WorkspaceOverlayStorePort,
        blob_store: ArtifactBlobStorePort,
        overlay_service: WorkspaceOverlayService,
    ) -> None:
        self._run_id = run_id
        self._base_read = base_read
        self._overlay_store = overlay_store
        self._blob_store = blob_store
        self._overlay_service = overlay_service

    async def astat(self, virtual_path: str) -> WorkspaceBaseEntry | None:
        path = normalize_virtual_path(virtual_path, allow_mount_root=True)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        return await self._stat(path, manifest, ignore_tombstone_for=None)

    async def aread_bytes(self, virtual_path: str) -> bytes:
        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        return await self._read_bytes(path, manifest, ignore_tombstone_for=None)

    async def alist(self, virtual_path: str) -> tuple[WorkspaceBaseEntry, ...]:
        path = normalize_virtual_path(virtual_path, allow_mount_root=True)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        base_entries = await self._base_read.list(path)
        merged: dict[str, WorkspaceBaseEntry] = {}
        for entry in base_entries:
            if not self._is_hidden(entry.virtual_path, manifest):
                merged[entry.virtual_path] = entry
        for entry in manifest.entries:
            if posixpath.dirname(entry.virtual_path) != path:
                continue
            if entry.entry_kind is WorkspaceEntryKind.TOMBSTONE:
                merged.pop(entry.virtual_path, None)
                continue
            merged[entry.virtual_path] = await self._entry_as_base(entry, manifest)
        return tuple(merged[path] for path in sorted(merged))

    async def aglob(self, pattern: str) -> tuple[WorkspaceBaseEntry, ...]:
        """Merge base and overlay matches using standard POSIX glob semantics."""

        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        matches: dict[str, WorkspaceBaseEntry] = {}
        for entry in await self._base_read.glob(pattern):
            if not self._is_hidden(entry.virtual_path, manifest):
                matches[entry.virtual_path] = entry
        for entry in manifest.entries:
            if entry.entry_kind is WorkspaceEntryKind.TOMBSTONE:
                matches.pop(entry.virtual_path, None)
            elif fnmatch.fnmatchcase(entry.virtual_path, pattern):
                matches[entry.virtual_path] = await self._entry_as_base(entry, manifest)
        return tuple(matches[path] for path in sorted(matches))

    async def agrep(
        self, query: str, paths: Sequence[str] | None = None
    ) -> tuple[WorkspaceBaseMatch, ...]:
        """Merge base matches with line-oriented overlay content matches."""

        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        matches = {
            (match.virtual_path, match.line_number): match
            for match in await self._base_read.grep(query, paths)
            if not self._is_hidden(match.virtual_path, manifest)
        }
        scoped_paths = (
            {normalize_virtual_path(path) for path in paths} if paths else None
        )
        for entry in manifest.entries:
            if entry.entry_kind is not WorkspaceEntryKind.FILE:
                continue
            if scoped_paths is not None and entry.virtual_path not in scoped_paths:
                continue
            try:
                text = (
                    await self._read_bytes(
                        entry.virtual_path, manifest, ignore_tombstone_for=None
                    )
                ).decode("utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches[(entry.virtual_path, line_number)] = WorkspaceBaseMatch(
                        virtual_path=entry.virtual_path,
                        line_number=line_number,
                        line_text=line,
                    )
        return tuple(matches[key] for key in sorted(matches))

    async def awrite(
        self, virtual_path: str, content: bytes | str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Stage a create/replace only in the durable overlay."""

        return await self._overlay_service.propose_replace(
            virtual_path, content, author=author
        )

    async def aedit(
        self,
        virtual_path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        """Stage a strict textual edit only in the durable overlay."""

        return await self._overlay_service.propose_edit(
            virtual_path,
            old_string,
            new_string,
            replace_all=replace_all,
            author=author,
        )

    async def adelete(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        return await self._overlay_service.propose_delete(virtual_path, author=author)

    async def amove(
        self,
        source_virtual_path: str,
        destination_virtual_path: str,
        *,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        return await self._overlay_service.propose_move(
            source_virtual_path, destination_virtual_path, author=author
        )

    async def amkdir(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        return await self._overlay_service.propose_mkdir(virtual_path, author=author)

    async def _stat(
        self,
        path: str,
        manifest: OverlayManifest,
        *,
        ignore_tombstone_for: str | None,
    ) -> WorkspaceBaseEntry | None:
        entry = manifest.entry_at(path)
        if entry is not None:
            if (
                entry.entry_kind is WorkspaceEntryKind.TOMBSTONE
                and path != ignore_tombstone_for
            ):
                return None
            if entry.entry_kind is not WorkspaceEntryKind.TOMBSTONE:
                return await self._entry_as_base(entry, manifest)
        if self._is_hidden(path, manifest, ignore_tombstone_for=ignore_tombstone_for):
            return None
        return await self._base_read.stat(path)

    async def _read_bytes(
        self,
        path: str,
        manifest: OverlayManifest,
        *,
        ignore_tombstone_for: str | None,
    ) -> bytes:
        entry = manifest.entry_at(path)
        if entry is not None:
            if (
                entry.entry_kind is WorkspaceEntryKind.TOMBSTONE
                and path != ignore_tombstone_for
            ):
                raise WorkspaceNotFoundError()
            if entry.entry_kind is WorkspaceEntryKind.FILE:
                return await self._read_overlay_file(entry)
            if entry.entry_kind is WorkspaceEntryKind.DIRECTORY:
                raise WorkspaceIsDirectoryError()
            if entry.entry_kind is WorkspaceEntryKind.MOVE:
                if entry.content_ref is not None:
                    return await self._read_overlay_file(entry)
                # A move references the source in the base/overlay view; source's
                # tombstone is bypassed only for this internal content lookup.
                if entry.source_virtual_path is None:  # defensive contract guard
                    raise WorkspaceNotFoundError()
                return await self._read_bytes(
                    entry.source_virtual_path,
                    manifest,
                    ignore_tombstone_for=entry.source_virtual_path,
                )
        if self._is_hidden(path, manifest, ignore_tombstone_for=ignore_tombstone_for):
            raise WorkspaceNotFoundError()
        base_entry = await self._base_read.stat(path)
        if base_entry is None:
            raise WorkspaceNotFoundError()
        if base_entry.entry_kind is not WorkspaceEntryKind.FILE:
            raise WorkspaceIsDirectoryError()
        stream = await self._base_read.read(path)
        body = bytearray()
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise TypeError("workspace base read chunks must be bytes")
            body.extend(chunk)
        return bytes(body)

    async def _read_overlay_file(self, entry: OverlayEntry) -> bytes:
        if entry.content_ref is None:  # defensive contract guard
            raise WorkspaceNotFoundError()
        stream = await self._blob_store.open_stream(
            blob_key_from_content_ref(entry.content_ref)
        )
        body = bytearray()
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise TypeError("workspace overlay content chunks must be bytes")
            body.extend(chunk)
        return bytes(body)

    async def _entry_as_base(
        self, entry: OverlayEntry, manifest: OverlayManifest
    ) -> WorkspaceBaseEntry:
        if entry.entry_kind is WorkspaceEntryKind.MOVE:
            if entry.content_ref is not None:
                return WorkspaceBaseEntry(
                    virtual_path=entry.virtual_path,
                    entry_kind=WorkspaceEntryKind.FILE,
                    content_digest=entry.content_digest,
                    byte_size=entry.byte_size,
                )
            if entry.source_virtual_path is None:
                raise WorkspaceNotFoundError()
            source = await self._stat(
                entry.source_virtual_path,
                manifest,
                ignore_tombstone_for=entry.source_virtual_path,
            )
            if source is None:
                raise WorkspaceNotFoundError()
            return source.model_copy(update={"virtual_path": entry.virtual_path})
        return WorkspaceBaseEntry(
            virtual_path=entry.virtual_path,
            entry_kind=entry.entry_kind,
            content_digest=entry.content_digest,
            byte_size=entry.byte_size,
        )

    @staticmethod
    def _is_hidden(
        path: str,
        manifest: OverlayManifest,
        *,
        ignore_tombstone_for: str | None = None,
    ) -> bool:
        candidate = path
        while candidate.startswith("/workspace/") and candidate != "/workspace":
            entry = manifest.entry_at(candidate)
            if (
                entry is not None
                and entry.entry_kind is WorkspaceEntryKind.TOMBSTONE
                and candidate != ignore_tombstone_for
            ):
                return True
            candidate = posixpath.dirname(candidate)
        return False


__all__ = ("MergedWorkspaceBackend",)
