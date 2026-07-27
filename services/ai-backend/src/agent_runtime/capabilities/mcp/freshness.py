"""Revision-aware freshness control for MCP descriptor discovery.

The existing :class:`McpDiscoveryCache` owns process-local TTL, LRU, and
thundering-herd behavior.  This module adds the control-plane information that
the cache deliberately does not know about:

* which org/user subject authorized a descriptor view;
* which opaque control-plane revision produced that view; and
* the maximum age at which a descriptor may still be reused.

It is an opt-in wrapper rather than a second descriptor cache.  Records remain
stored in ``McpDiscoveryCache`` and every lookup uses its existing
``(server_name, org_id, user_id)`` key.  Missing revision metadata fails closed:
an entry inserted by a legacy caller is never silently treated as revision
compatible.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, field_validator

from agent_runtime.capabilities.mcp.cards import LoadedMcpServer
from agent_runtime.capabilities.mcp.discovery_cache import (
    McpDiscoveryCache,
    McpDiscoveryCacheKey,
)
from agent_runtime.execution.contracts import RuntimeContract


class McpDescriptorRevision(RuntimeContract):
    """Opaque revision emitted by the MCP control plane.

    The runtime compares revisions for equality only.  It must not infer
    ordering from provider-specific values such as ETags, hashes, or database
    sequence identifiers.
    """

    value: str = Field(min_length=1, max_length=512)

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "descriptor revision must not be blank"
            raise ValueError(msg)
        return normalized


class McpDescriptorSubject(RuntimeContract):
    """The complete authorization subject for one descriptor view."""

    org_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class McpDescriptorFreshnessRequest(RuntimeContract):
    """Revision expectation for one subject-scoped MCP server."""

    server_name: str = Field(min_length=1)
    subject: McpDescriptorSubject
    revision: McpDescriptorRevision

    def cache_key(self) -> McpDiscoveryCacheKey:
        """Project the request to the existing cache's isolation key."""
        return McpDiscoveryCacheKey(
            server_name=self.server_name,
            org_id=self.subject.org_id,
            user_id=self.subject.user_id,
        )


class McpDescriptorFreshnessState(StrEnum):
    """Deterministic reasons a cached descriptor may or may not be reused."""

    FRESH = "fresh"
    NOT_TRACKED = "not_tracked"
    REVISION_CHANGED = "revision_changed"
    MAX_STALENESS_EXCEEDED = "max_staleness_exceeded"
    VALUE_EVICTED = "value_evicted"


class McpDescriptorFreshnessDecision(RuntimeContract):
    """Content-free decision produced before descriptor reuse or reload."""

    state: McpDescriptorFreshnessState
    requested_revision: McpDescriptorRevision
    cached_revision: McpDescriptorRevision | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    max_staleness_seconds: float = Field(gt=0)

    @property
    def reuse_allowed(self) -> bool:
        """Whether a cached descriptor is eligible for this request."""
        return self.state is McpDescriptorFreshnessState.FRESH


class McpDescriptorCacheResult(RuntimeContract):
    """Result of a revision-aware cache read or cache-aside load."""

    decision: McpDescriptorFreshnessDecision
    record: LoadedMcpServer | None = None
    loaded: bool = False


class McpDescriptorInvalidationResult(RuntimeContract):
    """Counts returned by an explicit subject-scoped invalidation hook."""

    cached_records_removed: int = Field(ge=0)
    revision_records_removed: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class _RevisionRecord:
    revision: McpDescriptorRevision
    admitted_at: float


@dataclass(slots=True)
class _KeyLock:
    lock: asyncio.Lock
    users: int = 0


class RevisionAwareMcpDiscoveryCache:
    """Subject-scoped revision and bounded-staleness guard.

    ``max_staleness_seconds`` is an upper bound independent of the underlying
    cache TTL.  The effective lifetime is therefore the smaller of the two:
    the wrapper rejects an over-age record even if the base TTL is longer, and
    reports ``VALUE_EVICTED`` if the base cache expires or evicts it first.

    Revision state is process-local, matching ``McpDiscoveryCache``.  A later
    shared-cache adapter can persist both values behind this same contract.
    """

    def __init__(
        self,
        cache: McpDiscoveryCache,
        *,
        max_staleness_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_staleness_seconds <= 0:
            msg = "max_staleness_seconds must be positive"
            raise ValueError(msg)
        self._cache = cache
        self._max_staleness_seconds = float(max_staleness_seconds)
        self._clock = clock
        self._revisions: dict[McpDiscoveryCacheKey, _RevisionRecord] = {}
        self._key_locks: dict[McpDiscoveryCacheKey, _KeyLock] = {}
        self._state_lock = asyncio.Lock()

    async def get(
        self,
        request: McpDescriptorFreshnessRequest,
    ) -> McpDescriptorCacheResult:
        """Return a record only when subject, revision, and age all match."""
        key = request.cache_key()
        async with self._lock_for(key):
            return await self._get_locked(key=key, request=request)

    async def put(
        self,
        request: McpDescriptorFreshnessRequest,
        record: LoadedMcpServer,
    ) -> None:
        """Admit a descriptor under the request's exact subject and revision."""
        key = request.cache_key()
        async with self._lock_for(key):
            await self._put_locked(key=key, request=request, record=record)

    async def get_or_load(
        self,
        request: McpDescriptorFreshnessRequest,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> McpDescriptorCacheResult:
        """Load once per exact subject/server key when reuse is not allowed.

        ``None`` and exceptions are not admitted.  The key lock spans the load
        so concurrent callers for the same subject and revision share the
        result while different subjects and servers may still load in parallel.
        """
        key = request.cache_key()
        async with self._lock_for(key):
            cached = await self._get_locked(key=key, request=request)
            if cached.record is not None:
                return cached

            loaded = await load()
            if loaded is None:
                return cached

            await self._put_locked(key=key, request=request, record=loaded)
            admitted = await self._cache.get(key)
            if admitted is None:  # Defensive: an adapter may reject admission.
                async with self._state_lock:
                    self._revisions.pop(key, None)
                return McpDescriptorCacheResult(
                    decision=self._decision(
                        request=request,
                        state=McpDescriptorFreshnessState.VALUE_EVICTED,
                    ),
                )
            return McpDescriptorCacheResult(
                decision=cached.decision,
                record=admitted,
                loaded=True,
            )

    async def invalidate_subject(
        self,
        subject: McpDescriptorSubject,
        *,
        server_name: str | None = None,
    ) -> McpDescriptorInvalidationResult:
        """Invalidate only one org/user subject, optionally one server.

        Both subject fields are mandatory by construction.  This intentionally
        does not expose the wildcard org/user surface of the base cache.
        """
        cached_removed = await self._cache.invalidate(
            server_name=server_name,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        async with self._state_lock:
            revision_keys = tuple(
                key
                for key in self._revisions
                if key.org_id == subject.org_id
                and key.user_id == subject.user_id
                and (server_name is None or key.server_name == server_name)
            )
            for key in revision_keys:
                self._revisions.pop(key, None)
        return McpDescriptorInvalidationResult(
            cached_records_removed=cached_removed,
            revision_records_removed=len(revision_keys),
        )

    async def _get_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        request: McpDescriptorFreshnessRequest,
    ) -> McpDescriptorCacheResult:
        async with self._state_lock:
            revision_record = self._revisions.get(key)

        if revision_record is None:
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.NOT_TRACKED,
                ),
            )

        age_seconds = max(0.0, self._clock() - revision_record.admitted_at)
        if revision_record.revision != request.revision:
            decision = self._decision(
                request=request,
                state=McpDescriptorFreshnessState.REVISION_CHANGED,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            )
            await self._evict_exact(key)
            return McpDescriptorCacheResult(decision=decision)

        if age_seconds >= self._max_staleness_seconds:
            decision = self._decision(
                request=request,
                state=McpDescriptorFreshnessState.MAX_STALENESS_EXCEEDED,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            )
            await self._evict_exact(key)
            return McpDescriptorCacheResult(decision=decision)

        record = await self._cache.get(key)
        if record is None:
            async with self._state_lock:
                self._revisions.pop(key, None)
            return McpDescriptorCacheResult(
                decision=self._decision(
                    request=request,
                    state=McpDescriptorFreshnessState.VALUE_EVICTED,
                    cached_revision=revision_record.revision,
                    age_seconds=age_seconds,
                ),
            )

        return McpDescriptorCacheResult(
            decision=self._decision(
                request=request,
                state=McpDescriptorFreshnessState.FRESH,
                cached_revision=revision_record.revision,
                age_seconds=age_seconds,
            ),
            record=record,
        )

    async def _put_locked(
        self,
        *,
        key: McpDiscoveryCacheKey,
        request: McpDescriptorFreshnessRequest,
        record: LoadedMcpServer,
    ) -> None:
        await self._cache.put(key, record)
        async with self._state_lock:
            self._revisions[key] = _RevisionRecord(
                revision=request.revision,
                admitted_at=self._clock(),
            )

    async def _evict_exact(self, key: McpDiscoveryCacheKey) -> None:
        await self._cache.invalidate(
            server_name=key.server_name,
            org_id=key.org_id,
            user_id=key.user_id,
        )
        async with self._state_lock:
            self._revisions.pop(key, None)

    def _decision(
        self,
        *,
        request: McpDescriptorFreshnessRequest,
        state: McpDescriptorFreshnessState,
        cached_revision: McpDescriptorRevision | None = None,
        age_seconds: float | None = None,
    ) -> McpDescriptorFreshnessDecision:
        return McpDescriptorFreshnessDecision(
            state=state,
            requested_revision=request.revision,
            cached_revision=cached_revision,
            age_seconds=age_seconds,
            max_staleness_seconds=self._max_staleness_seconds,
        )

    @asynccontextmanager
    async def _lock_for(
        self,
        key: McpDiscoveryCacheKey,
    ) -> AsyncIterator[None]:
        """Acquire a temporary per-key lock without an unbounded lock table."""
        async with self._state_lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = _KeyLock(lock=asyncio.Lock())
                self._key_locks[key] = key_lock
            key_lock.users += 1
        acquired = False
        try:
            await key_lock.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                key_lock.lock.release()
            async with self._state_lock:
                key_lock.users -= 1
                if key_lock.users == 0:
                    self._key_locks.pop(key, None)
