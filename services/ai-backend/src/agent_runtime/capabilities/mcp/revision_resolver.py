"""Bounded, generation-safe revision bootstrap cache for MCP discovery."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevision,
    BackendMcpRevisionClient,
    BackendMcpRevisionNotFound,
    BackendMcpRevisionNotice,
    BackendMcpRevisionUnavailable,
)


class RevisionResolveState(StrEnum):
    FRESH = "fresh"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RevisionResolveResult:
    state: RevisionResolveState
    revision: BackendMcpRevision | None = None


class McpDescriptorRevisionResolverPort(Protocol):
    """Trusted revision bootstrap and invalidation boundary for MCP loaders."""

    async def register(
        self, *, org_id: str, user_id: str, server_name: str, server_id: str
    ) -> None: ...

    async def resolve(
        self, *, org_id: str, user_id: str, server_name: str
    ) -> RevisionResolveResult: ...

    async def invalidate(
        self, *, org_id: str, user_id: str, server_name: str
    ) -> None: ...

    async def apply_notice(self, notice: BackendMcpRevisionNotice) -> None: ...


@dataclass
class _Entry:
    server_id: str
    revision: BackendMcpRevision | None = None
    not_found: bool = False
    checked: float | None = None
    generation: int = 0
    users: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class McpDescriptorRevisionResolver:
    """Keep only body-free revisions, with one fetch per registered server key.

    Generation is checked after every await.  A server remap or invalidation can
    therefore never admit an older in-flight response into the new mapping.
    """

    def __init__(
        self,
        client: BackendMcpRevisionClient,
        *,
        ttl_seconds: float = 30,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self._client = client
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str, str], _Entry] = OrderedDict()
        self._guard = asyncio.Lock()

    @staticmethod
    def _invalidate_entry(entry: _Entry) -> None:
        entry.generation += 1
        entry.revision = None
        entry.not_found = False
        entry.checked = None

    def _trim_locked(self) -> None:
        while len(self._entries) > self._max:
            key = next(
                (key for key, entry in self._entries.items() if entry.users == 0),
                None,
            )
            if key is None:
                return
            self._entries.pop(key)

    def _evict_for_insert_locked(self) -> bool:
        """Make room for one idle entry without evicting an active cohort."""

        while len(self._entries) >= self._max:
            key = next(
                (key for key, entry in self._entries.items() if entry.users == 0),
                None,
            )
            if key is None:
                return False
            self._entries.pop(key)
        return True

    async def register(
        self, *, org_id: str, user_id: str, server_name: str, server_id: str
    ) -> None:
        key = (org_id, user_id, server_name)
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                if not self._evict_for_insert_locked():
                    # All current entries are in flight.  Do not evict one and
                    # create a second single-flight cohort for the same key.
                    return
                self._entries[key] = _Entry(server_id=server_id)
            elif entry.server_id != server_id:
                entry.server_id = server_id
                self._invalidate_entry(entry)
            self._entries.move_to_end(key)

    async def invalidate(self, *, org_id: str, user_id: str, server_name: str) -> None:
        key = (org_id, user_id, server_name)
        async with self._guard:
            if entry := self._entries.get(key):
                self._invalidate_entry(entry)
                self._entries.move_to_end(key)

    async def apply_notice(self, notice: BackendMcpRevisionNotice) -> None:
        """Invalidate matching mappings without assuming an order for revisions.

        Revisions are opaque strings.  Equality is the only meaningful relation:
        a notice saying the cached revision is still current is a no-op; every
        other notice (including duplicates and out-of-order deliveries) makes
        the next resolve fetch the authoritative exact revision.
        """

        async with self._guard:
            for key, entry in list(self._entries.items()):
                if entry.server_id != notice.server_id:
                    continue
                if entry.revision is None:
                    self._invalidate_entry(entry)
                elif (
                    entry.revision.profile_id != notice.profile_id
                    or entry.revision.subject_scope_hash != notice.subject_scope_hash
                    or entry.revision.revision != notice.new_revision
                ):
                    self._invalidate_entry(entry)
                self._entries.move_to_end(key)

    async def resolve(
        self, *, org_id: str, user_id: str, server_name: str
    ) -> RevisionResolveResult:
        key = (org_id, user_id, server_name)
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                return RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
            entry.users += 1
            self._entries.move_to_end(key)
        try:
            async with entry.lock:
                async with self._guard:
                    if self._entries.get(key) is not entry:
                        return RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
                    if (
                        entry.checked is not None
                        and self._clock() - entry.checked < self._ttl
                    ):
                        return RevisionResolveResult(
                            (
                                RevisionResolveState.NOT_FOUND
                                if entry.not_found
                                else RevisionResolveState.FRESH
                            ),
                            entry.revision,
                        )
                    generation = entry.generation
                    server_id = entry.server_id
                try:
                    revision = await self._client.get_exact(
                        org_id=org_id, user_id=user_id, server_id=server_id
                    )
                    result = RevisionResolveResult(RevisionResolveState.FRESH, revision)
                except BackendMcpRevisionNotFound:
                    revision = None
                    result = RevisionResolveResult(RevisionResolveState.NOT_FOUND)
                except BackendMcpRevisionUnavailable:
                    revision = None
                    result = RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
                except Exception:
                    revision = None
                    result = RevisionResolveResult(RevisionResolveState.UNAVAILABLE)

                async with self._guard:
                    if (
                        self._entries.get(key) is not entry
                        or entry.generation != generation
                        or entry.server_id != server_id
                    ):
                        return RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
                    if result.state is not RevisionResolveState.UNAVAILABLE:
                        entry.revision = revision
                        entry.not_found = result.state is RevisionResolveState.NOT_FOUND
                        entry.checked = self._clock()
                    return result
        finally:
            async with self._guard:
                entry.users -= 1
                self._trim_locked()
