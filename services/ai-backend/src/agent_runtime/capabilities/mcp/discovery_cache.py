"""Process-wide TTL + LRU cache for MCP server discovery results.

Memoizes :class:`LoadedMcpServer` records keyed by
``(server_name, org_id, user_id)`` so :class:`McpLoader.load_server` does
not pay the 3-round-trip ``connect + list_tools + list_resources`` cost
on every turn. Tool descriptors are server-defined and change on deploy
timescales, not session timescales, so a short TTL plus an explicit
invalidation hook on re-auth is the obvious fix.

Design invariants:

- One cache, one layer (the loader). Providers and clients stay
  stateless; invalidation goes through ``invalidate``.
- No module-level globals. The cache is constructed at FastAPI lifespan
  startup (and at worker dependency wiring) and threaded through to
  consumers. Tests get fresh cache instances.
- Defensive copies on every ``get``; never hand out shared refs to a
  frozen Pydantic record so accidental mutation downstream cannot
  poison the cache.
- Per-key async lock prevents the thundering-herd case: two concurrent
  callers for the same cold key run ``load()`` exactly once and both
  receive the loaded value.

API + worker run in **separate processes** in production. Each gets its
own cache; the warm-up cost is per-process. That trade-off is
deliberate — a shared cache would need a Redis-backed adapter (which
this contract supports — see ``McpDiscoveryCache.__doc__``).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from pydantic import Field, NonNegativeInt

from agent_runtime.capabilities.mcp.cards import LoadedMcpServer
from agent_runtime.execution.contracts import RuntimeContract


class McpDiscoveryCacheKey(RuntimeContract):
    """Composite key for a cached :class:`LoadedMcpServer` entry.

    ``user_id`` is keyed defensively because different OAuth scopes for
    the same server can yield different tool visibility. ``org_id`` is
    keyed for tenant isolation — a cache poisoned by one tenant must
    never serve another.
    """

    server_name: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class McpDiscoveryCacheStats(RuntimeContract):
    """Observability counters for a single cache instance."""

    hits: NonNegativeInt = 0
    misses: NonNegativeInt = 0
    evictions: NonNegativeInt = 0
    expired: NonNegativeInt = 0
    invalidations: NonNegativeInt = 0
    current_size: NonNegativeInt = 0


class McpDiscoveryCache:
    """Process-wide TTL + LRU cache for ``LoadedMcpServer`` records.

    Replaceable by a Redis-backed adapter without touching
    :class:`McpLoader`: the public surface is ``get`` / ``put`` /
    ``get_or_load`` / ``invalidate`` / ``stats``. ``McpLoader`` only
    constructs :class:`McpDiscoveryCacheKey` values and calls these
    methods, so an adapter that implements the same surface drops in.

    The in-process implementation uses an :class:`OrderedDict` for LRU
    eviction and a per-key :class:`asyncio.Lock` to serialise concurrent
    misses on the same key (thundering-herd protection).
    """

    _DEFAULT_TTL_SECONDS: float = 900.0
    _DEFAULT_MAX_ENTRIES: int = 1000

    def __init__(
        self,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            msg = "ttl_seconds must be positive"
            raise ValueError(msg)
        if max_entries <= 0:
            msg = "max_entries must be positive"
            raise ValueError(msg)
        self._ttl_seconds = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._clock = clock
        # ``OrderedDict`` of key → (expiry_monotonic, record). ``move_to_end``
        # marks recently-used entries on read so eviction picks the genuinely
        # least-recently-used key.
        self._entries: OrderedDict[
            McpDiscoveryCacheKey, tuple[float, LoadedMcpServer]
        ] = OrderedDict()
        # Per-key lazily-allocated locks. Removed when the entry expires or
        # is evicted so a long-lived cache doesn't accumulate dead locks.
        self._locks: dict[McpDiscoveryCacheKey, asyncio.Lock] = {}
        # Coarse guard around the OrderedDict + lock-table mutations so the
        # per-key fast path doesn't race with eviction or invalidation.
        self._mutex = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expired = 0
        self._invalidations = 0

    async def get(self, key: McpDiscoveryCacheKey) -> LoadedMcpServer | None:
        """Return a defensive copy of the cached record if fresh, else ``None``.

        Side effects on hit: move the entry to the MRU end and increment
        ``hits``. On miss (key absent or expired): drop the stale entry and
        increment ``misses`` / ``expired``.
        """
        async with self._mutex:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            expiry, record = entry
            if self._clock() >= expiry:
                # Stale — evict before we hand out a stale copy.
                self._entries.pop(key, None)
                self._locks.pop(key, None)
                self._expired += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return record.model_copy(deep=True)

    async def put(self, key: McpDiscoveryCacheKey, record: LoadedMcpServer) -> None:
        """Store a defensive copy of ``record`` under ``key`` with a fresh TTL.

        Enforces ``max_entries`` by evicting the LRU entry when full.
        """
        async with self._mutex:
            expiry = self._clock() + self._ttl_seconds
            stored = record.model_copy(deep=True)
            if key in self._entries:
                self._entries[key] = (expiry, stored)
                self._entries.move_to_end(key)
                return
            self._entries[key] = (expiry, stored)
            while len(self._entries) > self._max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                self._locks.pop(evicted_key, None)
                self._evictions += 1

    async def get_or_load(
        self,
        key: McpDiscoveryCacheKey,
        load: Callable[[], Awaitable[LoadedMcpServer | None]],
    ) -> LoadedMcpServer | None:
        """Cache-aside read with per-key async lock for thundering-herd safety.

        Flow:

        1. Try a fast-path ``get``. If hit, return immediately.
        2. Acquire the per-key lock so concurrent waiters serialise.
        3. Re-check the cache while holding the lock — another waiter
           may have populated it.
        4. Call ``load()``. If it returns ``None`` or raises, do NOT
           populate the cache; the next caller retries.
        5. On success: ``put`` then return a (fresh) defensive copy via
           ``get``.

        ``None`` and exceptions are intentionally not cached so a transient
        upstream failure (timeout, 5xx) does not pin a cold cache for the
        TTL duration.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached
        lock = await self._lock_for(key)
        async with lock:
            # Re-check: another waiter may have loaded and populated.
            cached = await self.get(key)
            if cached is not None:
                return cached
            loaded = await load()
            if loaded is None:
                return None
            await self.put(key, loaded)
            # Return a fresh defensive copy via the ``get`` path so
            # callers and the cache hold independent objects.
            return await self.get(key)

    async def invalidate(
        self,
        *,
        server_name: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        """Remove entries matching every provided field. ``None`` = wildcard.

        Used by:
          - ``auth_mcp`` middleware on successful re-auth (new scopes may
            change tool visibility, so cached descriptors must be busted).
          - Future: connector pause / uninstall flows.

        Returns the number of entries removed.
        """
        async with self._mutex:
            to_remove: list[McpDiscoveryCacheKey] = []
            for cached_key in self._entries:
                if server_name is not None and cached_key.server_name != server_name:
                    continue
                if org_id is not None and cached_key.org_id != org_id:
                    continue
                if user_id is not None and cached_key.user_id != user_id:
                    continue
                to_remove.append(cached_key)
            for cached_key in to_remove:
                self._entries.pop(cached_key, None)
                self._locks.pop(cached_key, None)
            self._invalidations += len(to_remove)
            return len(to_remove)

    def stats(self) -> McpDiscoveryCacheStats:
        """Return a snapshot of counters for observability."""
        return McpDiscoveryCacheStats(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            expired=self._expired,
            invalidations=self._invalidations,
            current_size=len(self._entries),
        )

    async def _lock_for(self, key: McpDiscoveryCacheKey) -> asyncio.Lock:
        """Return (and lazily allocate) the per-key async lock."""
        async with self._mutex:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock
