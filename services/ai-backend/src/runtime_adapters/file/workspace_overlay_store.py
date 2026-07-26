"""File-backed optimistic workspace overlay manifests for desktop runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from agent_runtime.capabilities.workspace.contracts import (
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileWorkspaceOverlayStore:
    """Persist current and immutable historical manifests with CAS semantics."""

    _SUBDIR: ClassVar[str] = "workspace-overlays"
    _LOCK_FILENAME: ClassVar[str] = ".overlays.lock"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600
    _VERSIONS_SUBDIR: ClassVar[str] = "versions"

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._root = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._root.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._versions_root = self._root / self._VERSIONS_SUBDIR
        self._versions_root.mkdir(mode=self._DIR_MODE, exist_ok=True)
        self._lock_path = self._root / self._LOCK_FILENAME
        self._lock = asyncio.Lock()

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        async with self._lock:
            with self._exclusive_lock():
                return self._read(run_id)

    async def get_manifest_version(
        self, *, run_id: str, version: int
    ) -> OverlayManifest | None:
        """Read an immutable version file without falling back to current state."""

        if version < 1:
            return None
        async with self._lock:
            with self._exclusive_lock():
                return self._read_version(run_id, version)

    async def append_revision(
        self,
        *,
        run_id: str,
        expected_version: int,
        mutations: tuple[OverlayMutation, ...] | list[OverlayMutation],
    ) -> OverlayManifest:
        async with self._lock:
            with self._exclusive_lock():
                current = self._read(run_id)
                if current.version != expected_version:
                    raise WorkspaceOverlayConflictError()
                next_version = current.version + 1
                entries = {entry.virtual_path: entry for entry in current.entries}
                for mutation in mutations:
                    if mutation.kind is OverlayMutationKind.REMOVE:
                        entries.pop(mutation.virtual_path, None)
                    elif mutation.entry is not None:
                        entries[mutation.virtual_path] = mutation.entry.model_copy(
                            update={"overlay_revision": next_version}
                        )
                updated = OverlayManifest(
                    run_id=run_id,
                    version=next_version,
                    entries=tuple(entries[path] for path in sorted(entries)),
                )
                self._write(run_id, updated)
                # The current pointer commits first.  A crash before the
                # immutable version file exists leaves a snapshot unavailable,
                # never a mutable-latest substitute.  Historical files are
                # create-only and cannot be altered by a later overlay edit.
                self._write_version(run_id, updated)
                return updated

    async def compact(self, *, run_id: str) -> OverlayManifest:
        async with self._lock:
            with self._exclusive_lock():
                manifest = self._read(run_id)
                self._write(run_id, manifest)
                if manifest.version > 0:
                    self._write_version(run_id, manifest)
                return manifest

    def _read(self, run_id: str) -> OverlayManifest:
        path = self._path(run_id)
        if not path.exists():
            return OverlayManifest(run_id=run_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            manifest = OverlayManifest.model_validate(raw)
        except Exception as exc:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay storage is unavailable."
            ) from exc
        if manifest.run_id != run_id:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay scope does not match."
            )
        return manifest

    def _read_version(self, run_id: str, version: int) -> OverlayManifest | None:
        path = self._version_path(run_id, version)
        if not path.exists():
            return None
        manifest = self._read_path(path, run_id=run_id)
        if manifest.version != version:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay version does not match its immutable record."
            )
        return manifest

    def _write(self, run_id: str, manifest: OverlayManifest) -> None:
        path = self._path(run_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        encoded = json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_root()
        finally:
            temporary.unlink(missing_ok=True)

    def _write_version(self, run_id: str, manifest: OverlayManifest) -> None:
        """Create one immutable manifest record, accepting only an exact retry."""

        path = self._version_path(run_id, manifest.version)
        path.parent.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        encoded = self._encoded(manifest)
        try:
            fd = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
        except FileExistsError:
            existing = self._read_version(run_id, manifest.version)
            if existing != manifest:
                raise WorkspaceOverlayConflictError(
                    "Workspace overlay version is already immutable."
                )
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(path.parent)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    def _path(self, run_id: str) -> Path:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.json"

    def _version_path(self, run_id: str, version: int) -> Path:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._versions_root / digest / f"{version}.json"

    @staticmethod
    def _encoded(manifest: OverlayManifest) -> str:
        return json.dumps(
            manifest.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @staticmethod
    def _read_path(path: Path, *, run_id: str) -> OverlayManifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            manifest = OverlayManifest.model_validate(raw)
        except Exception as exc:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay storage is unavailable."
            ) from exc
        if manifest.run_id != run_id:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay scope does not match."
            )
        return manifest

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            fd = os.open(
                self._lock_path,
                os.O_CREAT | os.O_RDWR,
                self._FILE_MODE,
            )
            acquired = False
            try:
                acquire_exclusive(fd)
                acquired = True
                yield
            finally:
                if acquired:
                    release_exclusive(fd)
                os.close(fd)
        except OSError as exc:
            raise WorkspaceOverlayConflictError(
                "Workspace overlay storage is unavailable."
            ) from exc

    def _fsync_root(self) -> None:
        self._fsync_directory(self._root)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # The manifest file itself was already fsynced. Some platforms do
            # not permit directory fsync; atomic replace remains fail-closed.
            return


__all__ = ("FileWorkspaceOverlayStore",)
