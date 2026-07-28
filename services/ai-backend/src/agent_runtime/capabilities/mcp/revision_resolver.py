"""Bounded, single-flight trusted revision bootstrap registry."""

from __future__ import annotations
import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevision,
    BackendMcpRevisionClient,
)


class RevisionResolveState(StrEnum):
    FRESH = "fresh"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RevisionResolveResult:
    state: RevisionResolveState
    revision: BackendMcpRevision | None = None


@dataclass
class _Entry:
    server_id: str
    revision: BackendMcpRevision | None
    checked: float
    lock: asyncio.Lock


class McpDescriptorRevisionResolver:
    def __init__(
        self,
        client: BackendMcpRevisionClient,
        *,
        ttl_seconds: float = 30,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str, str], _Entry] = OrderedDict()
        self._guard = asyncio.Lock()

    async def register(
        self, *, org_id: str, user_id: str, server_name: str, server_id: str
    ) -> None:
        key = (org_id, user_id, server_name)
        async with self._guard:
            old = self._entries.get(key)
            self._entries[key] = _Entry(
                server_id,
                old.revision if old and old.server_id == server_id else None,
                old.checked if old and old.server_id == server_id else 0,
                old.lock if old else asyncio.Lock(),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    async def resolve(
        self, *, org_id: str, user_id: str, server_name: str
    ) -> RevisionResolveResult:
        key = (org_id, user_id, server_name)
        async with self._guard:
            entry = self._entries.get(key)
            self._entries.move_to_end(key) if entry else None
        if entry is None:
            return RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
        if entry.revision and self._clock() - entry.checked < self._ttl:
            return RevisionResolveResult(RevisionResolveState.FRESH, entry.revision)
        async with entry.lock:
            if entry.revision and self._clock() - entry.checked < self._ttl:
                return RevisionResolveResult(RevisionResolveState.FRESH, entry.revision)
            try:
                revision = await self._client.get_exact(
                    org_id=org_id, user_id=user_id, server_id=entry.server_id
                )
            except Exception:
                return RevisionResolveResult(RevisionResolveState.UNAVAILABLE)
            entry.revision = revision
            entry.checked = self._clock()
            return (
                RevisionResolveResult(RevisionResolveState.FRESH, revision)
                if revision
                else RevisionResolveResult(RevisionResolveState.NOT_FOUND)
            )
