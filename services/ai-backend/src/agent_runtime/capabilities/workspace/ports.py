"""Dependency-inverted ports for the pure workspace overlay domain."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from weakref import finalize

from agent_runtime.capabilities.workspace.contracts import (
    OverlayManifest,
    OverlayMutation,
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
)


@runtime_checkable
class WorkspaceBaseReadPort(Protocol):
    """Read-only view of the granted base workspace.

    This intentionally contains no mutation method.  The overlay domain can
    therefore never acquire a host-write capability through this dependency.
    """

    async def stat(self, virtual_path: str) -> WorkspaceBaseEntry | None:
        """Return metadata for a base path, or ``None`` when it is absent."""

    async def read(
        self,
        virtual_path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open a bounded byte stream from the base workspace."""

    async def list(self, virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        """List direct children of a base directory."""

    async def glob(self, pattern: str) -> Sequence[WorkspaceBaseEntry]:
        """Return bounded base matches for a virtual glob pattern."""

    async def grep(
        self, query: str, paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        """Return bounded literal search hits from base content."""


@runtime_checkable
class WorkspaceOverlayStorePort(Protocol):
    """Optimistically versioned durable metadata storage for overlay entries."""

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        """Read the current immutable manifest for a run."""

    async def get_manifest_version(
        self, *, run_id: str, version: int
    ) -> OverlayManifest | None:
        """Read one retained immutable version, never a current-view fallback."""

    async def append_revision(
        self,
        *,
        run_id: str,
        expected_version: int,
        mutations: Sequence[OverlayMutation],
    ) -> OverlayManifest:
        """Atomically apply mutations only when the current version matches."""

    async def compact(self, *, run_id: str) -> OverlayManifest:
        """Optionally compact storage without changing the logical manifest."""


@dataclass(frozen=True)
class _OverlayReadMessage:
    run_id: str
    result: asyncio.Future[OverlayManifest]


_STOP = object()


class WorkspaceOverlayReadPort:
    """Opaque read-only route to a mutable overlay store.

    ``MergedWorkspaceBackend`` is model-visible, so it must not retain an
    object that exposes ``append_revision``.  The composition root binds the
    mutable store into an actor; the resulting port carries only a request
    queue and supports the read operation the merged backend needs.
    """

    __slots__ = ("_queue", "_lease", "__weakref__")

    def __new__(cls) -> WorkspaceOverlayReadPort:
        del cls
        raise TypeError("workspace overlay read ports are created by composition")

    @classmethod
    def bind(cls, store: WorkspaceOverlayStorePort) -> WorkspaceOverlayReadPort:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[_OverlayReadMessage | object] = asyncio.Queue()
        loop.create_task(
            _serve_overlay_reads(queue=queue, store=store),
            name="workspace-overlay-read-port",
        )
        port = object.__new__(cls)
        port._queue = queue
        port._lease = finalize(port, _close_queue, queue)
        return port

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        result: asyncio.Future[OverlayManifest] = (
            asyncio.get_running_loop().create_future()
        )
        await self._queue.put(_OverlayReadMessage(run_id=run_id, result=result))
        return await result


async def _serve_overlay_reads(
    *,
    queue: asyncio.Queue[_OverlayReadMessage | object],
    store: WorkspaceOverlayStorePort,
) -> None:
    while True:
        message = await queue.get()
        if message is _STOP:
            queue.task_done()
            return
        assert isinstance(message, _OverlayReadMessage)
        try:
            if not message.result.done():
                message.result.set_result(
                    await store.get_manifest(run_id=message.run_id)
                )
        except BaseException as exc:
            if not message.result.done():
                message.result.set_exception(exc)
        finally:
            queue.task_done()


def _close_queue(queue: asyncio.Queue[_OverlayReadMessage | object]) -> None:
    queue.put_nowait(_STOP)


__all__ = (
    "WorkspaceBaseReadPort",
    "WorkspaceOverlayReadPort",
    "WorkspaceOverlayStorePort",
)
