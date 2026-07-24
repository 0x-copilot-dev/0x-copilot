"""Hermetic optimistic-concurrency implementation of the workspace overlay store."""

from __future__ import annotations

import asyncio

from agent_runtime.capabilities.workspace.contracts import (
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError


class InMemoryWorkspaceOverlayStore:
    """Process-local durable-model adapter used by focused overlay domain tests."""

    def __init__(self) -> None:
        self._manifests: dict[str, OverlayManifest] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        lock = await self._lock_for(run_id)
        async with lock:
            return self._manifests.get(run_id, OverlayManifest(run_id=run_id))

    async def append_revision(
        self,
        *,
        run_id: str,
        expected_version: int,
        mutations: tuple[OverlayMutation, ...] | list[OverlayMutation],
    ) -> OverlayManifest:
        lock = await self._lock_for(run_id)
        async with lock:
            current = self._manifests.get(run_id, OverlayManifest(run_id=run_id))
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
            self._manifests[run_id] = updated
            return updated

    async def compact(self, *, run_id: str) -> OverlayManifest:
        """The immutable in-memory representation is already compact."""

        return await self.get_manifest(run_id=run_id)

    async def _lock_for(self, run_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(run_id, asyncio.Lock())


__all__ = ("InMemoryWorkspaceOverlayStore",)
