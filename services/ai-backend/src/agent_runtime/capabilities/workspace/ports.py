"""Dependency-inverted ports for the pure workspace overlay domain."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

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


__all__ = ("WorkspaceBaseReadPort", "WorkspaceOverlayStorePort")
