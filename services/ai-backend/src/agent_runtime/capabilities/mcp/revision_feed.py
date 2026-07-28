"""Bounded, pull-driven MCP descriptor revision feed primitives.

This module deliberately owns no task, timer, or HTTP server.  A host may call
``McpRevisionFeedRunner.run_once`` after a verified request has made a subject
active.  That keeps desktop/offline operation local and prevents an idle
process from polling every tenant it has ever seen.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import json
import time
from typing import Final, Protocol

from pydantic import Field

from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolverPort,
)
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevisionClient,
    BackendMcpRevisionCursorExpired,
    BackendMcpRevisionFeed,
    BackendMcpRevisionUnavailable,
)
from agent_runtime.execution.contracts import RuntimeContract

_IDENTITY_MAX_LENGTH: Final = 256
MCP_REVISION_CURSOR_MAX_BYTES: Final = 1024


class McpRevisionSubject(RuntimeContract):
    """A subject derived from verified runtime identity, never feed input."""

    org_id: str = Field(min_length=1, max_length=_IDENTITY_MAX_LENGTH)
    user_id: str = Field(min_length=1, max_length=_IDENTITY_MAX_LENGTH)


class ActiveMcpRevisionSubjectRegistry:
    """Small LRU-free active-subject set with an inactivity expiry.

    At capacity a new subject is declined instead of evicting a currently active
    subject.  The request that authenticated the identity can retry after stale
    subjects expire; an attacker cannot use activity on one subject to evict a
    different tenant from the feed.
    """

    def __init__(
        self,
        *,
        max_subjects: int = 256,
        inactivity_ttl_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_subjects <= 0 or inactivity_ttl_seconds <= 0:
            raise ValueError("max_subjects and inactivity_ttl_seconds must be positive")
        self._max_subjects = max_subjects
        self._ttl = inactivity_ttl_seconds
        self._clock = clock
        self._active: dict[McpRevisionSubject, float] = {}
        self._guard = asyncio.Lock()

    def _prune_locked(self) -> None:
        now = self._clock()
        for subject, seen_at in tuple(self._active.items()):
            if now - seen_at >= self._ttl:
                self._active.pop(subject, None)

    async def touch_verified(self, subject: McpRevisionSubject) -> bool:
        """Record a verified subject; ``False`` means the hard cap is full."""

        async with self._guard:
            self._prune_locked()
            if subject not in self._active and len(self._active) >= self._max_subjects:
                return False
            self._active[subject] = self._clock()
            return True

    async def active_subjects(self) -> tuple[McpRevisionSubject, ...]:
        async with self._guard:
            self._prune_locked()
            return tuple(self._active)


class McpRevisionCursorStoreError(RuntimeError):
    """Cursor data was unsafe, corrupt, or could not be durably persisted."""


class McpRevisionCursorStorePort(Protocol):
    async def load(self, subject: McpRevisionSubject) -> str | None: ...

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None: ...

    async def clear(self, subject: McpRevisionSubject) -> None: ...


class InMemoryMcpRevisionCursorStore:
    """Test/dev cursor adapter. It never shares cursors across feed subjects."""

    def __init__(self) -> None:
        self._cursors: dict[McpRevisionSubject, str] = {}
        self._guard = asyncio.Lock()

    async def load(self, subject: McpRevisionSubject) -> str | None:
        async with self._guard:
            return self._cursors.get(subject)

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None:
        validate_mcp_revision_cursor(cursor)
        async with self._guard:
            self._cursors[subject] = cursor

    async def clear(self, subject: McpRevisionSubject) -> None:
        async with self._guard:
            self._cursors.pop(subject, None)


def validate_mcp_revision_cursor(cursor: str) -> None:
    if not cursor or len(cursor.encode("utf-8")) > MCP_REVISION_CURSOR_MAX_BYTES:
        raise ValueError("cursor must be non-empty and at most 1024 bytes")


class McpDescriptorCacheInvalidationPort(Protocol):
    async def invalidate_descriptor(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> None: ...

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None: ...


class McpCatalogGenerationAuthorityPort(Protocol):
    async def advance(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> int: ...

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None: ...


class _BoundedNoticeMemory:
    def __init__(self, max_notices: int) -> None:
        if max_notices <= 0:
            raise ValueError("max_notices must be positive")
        self._max_notices = max_notices
        self._notice_ids: OrderedDict[object, None] = OrderedDict()

    def seen(self, notice_key: object) -> bool:
        if notice_key in self._notice_ids:
            self._notice_ids.move_to_end(notice_key)
            return True
        self._notice_ids[notice_key] = None
        while len(self._notice_ids) > self._max_notices:
            self._notice_ids.popitem(last=False)
        return False

    def forget(self, notice_key: object) -> None:
        self._notice_ids.pop(notice_key, None)


class ProcessLocalMcpDescriptorCacheInvalidator:
    """Structural invalidation boundary; a host may observe generations by key."""

    def __init__(self, *, max_notices: int = 4096, max_entries: int = 1000) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._notices = _BoundedNoticeMemory(max_notices)
        self._max_entries = max_entries
        self._generations: OrderedDict[tuple[McpRevisionSubject, str], int] = (
            OrderedDict()
        )
        self._guard = asyncio.Lock()

    async def invalidate_descriptor(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> None:
        async with self._guard:
            if self._notices.seen((subject, notice_id)):
                return
            key = (subject, server_id)
            self._generations[key] = self._generations.get(key, 0) + 1
            self._generations.move_to_end(key)
            while len(self._generations) > self._max_entries:
                self._generations.popitem(last=False)

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None:
        async with self._guard:
            for key in tuple(self._generations):
                if key[0] == subject:
                    self._generations[key] += 1

    async def generation(self, *, subject: McpRevisionSubject, server_id: str) -> int:
        async with self._guard:
            return self._generations.get((subject, server_id), 0)


class ProcessLocalMcpCatalogGenerationAuthority:
    """The single process-local F3/catalog generation boundary for this slice."""

    def __init__(self, *, max_notices: int = 4096, max_entries: int = 1000) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._notices = _BoundedNoticeMemory(max_notices)
        self._max_entries = max_entries
        self._generations: OrderedDict[tuple[McpRevisionSubject, str], int] = (
            OrderedDict()
        )
        self._guard = asyncio.Lock()

    async def advance(
        self, *, subject: McpRevisionSubject, server_id: str, notice_id: str
    ) -> int:
        async with self._guard:
            key = (subject, server_id)
            if not self._notices.seen((subject, notice_id)):
                self._generations[key] = self._generations.get(key, 0) + 1
                self._generations.move_to_end(key)
                while len(self._generations) > self._max_entries:
                    self._generations.popitem(last=False)
            return self._generations.get(key, 0)

    async def flush_subject(self, *, subject: McpRevisionSubject) -> None:
        async with self._guard:
            for key in tuple(self._generations):
                if key[0] == subject:
                    self._generations[key] += 1

    async def generation(self, *, subject: McpRevisionSubject, server_id: str) -> int:
        async with self._guard:
            return self._generations.get((subject, server_id), 0)


class McpRevisionFeedCoordinator:
    """Applies each notice in resolver → descriptor → catalog → cursor order."""

    def __init__(
        self,
        *,
        resolver: McpDescriptorRevisionResolverPort,
        descriptors: McpDescriptorCacheInvalidationPort,
        catalog: McpCatalogGenerationAuthorityPort,
        cursors: McpRevisionCursorStorePort,
        max_dedupe_notices: int = 4096,
    ) -> None:
        self._resolver = resolver
        self._descriptors = descriptors
        self._catalog = catalog
        self._cursors = cursors
        self._dedupe = _BoundedNoticeMemory(max_dedupe_notices)
        self._guard = asyncio.Lock()

    async def apply_page(
        self, *, subject: McpRevisionSubject, feed: BackendMcpRevisionFeed
    ) -> None:
        # A single guard provides deterministic page ordering and lets a replay
        # after a failed cursor write skip already-successful invalidations.
        async with self._guard:
            for notice in feed.notices:
                notice_key = (subject, notice.notice_id)
                if self._dedupe.seen(notice_key):
                    continue
                try:
                    await self._resolver.apply_notice(
                        org_id=subject.org_id, user_id=subject.user_id, notice=notice
                    )
                    await self._descriptors.invalidate_descriptor(
                        subject=subject,
                        server_id=notice.server_id,
                        notice_id=notice.notice_id,
                    )
                    await self._catalog.advance(
                        subject=subject,
                        server_id=notice.server_id,
                        notice_id=notice.notice_id,
                    )
                except Exception:
                    # Do not record a partially applied notice as deduplicated.
                    self._dedupe.forget(notice_key)
                    raise
            if feed.next_cursor is not None:
                await self._cursors.save(subject, feed.next_cursor)

    async def reset_expired_subject(self, *, subject: McpRevisionSubject) -> None:
        """Flush exactly one subject before resetting its expired feed cursor."""

        async with self._guard:
            await self._resolver.invalidate_subject(
                org_id=subject.org_id, user_id=subject.user_id
            )
            await self._descriptors.flush_subject(subject=subject)
            await self._catalog.flush_subject(subject=subject)
            await self._cursors.clear(subject)


class McpRevisionFeedSubjectState(StrEnum):
    APPLIED = "applied"
    OFFLINE = "offline"
    CURSOR_EXPIRED = "cursor_expired"
    BOUND_EXCEEDED = "bound_exceeded"
    CURSOR_STALLED = "cursor_stalled"
    FAILED = "failed"


@dataclass(frozen=True)
class McpRevisionFeedSubjectResult:
    state: McpRevisionFeedSubjectState
    pages: int = 0
    notices: int = 0
    bytes_read: int = 0


@dataclass(frozen=True)
class McpRevisionFeedRunResult:
    subjects: int
    http_calls: int
    results: tuple[McpRevisionFeedSubjectResult, ...]
    retry_after_seconds: float | None = None


class McpRevisionFeedRunner:
    """One bounded pull pass. Hosts schedule it; this class never self-runs."""

    def __init__(
        self,
        *,
        client: BackendMcpRevisionClient,
        subjects: ActiveMcpRevisionSubjectRegistry,
        cursors: McpRevisionCursorStorePort,
        coordinator: McpRevisionFeedCoordinator,
        max_pages: int = 4,
        max_notices: int = 200,
        max_bytes: int = 64 * 1024,
        page_limit: int = 100,
        backoff_base_seconds: float = 1,
        backoff_max_seconds: float = 60,
        random: Callable[[], float] = __import__("random").random,
    ) -> None:
        if (
            max_pages <= 0
            or max_notices <= 0
            or max_bytes <= 0
            or not 1 <= page_limit <= 100
            or backoff_base_seconds <= 0
            or backoff_max_seconds < backoff_base_seconds
        ):
            raise ValueError("invalid revision feed bounds or backoff")
        self._client = client
        self._subjects = subjects
        self._cursors = cursors
        self._coordinator = coordinator
        self._max_pages = max_pages
        self._max_notices = max_notices
        self._max_bytes = max_bytes
        self._page_limit = page_limit
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self._random = random
        self._offline_attempts = 0
        self._diagnostics: dict[McpRevisionFeedSubjectState, int] = {
            state: 0 for state in McpRevisionFeedSubjectState
        }

    def diagnostics(self) -> dict[str, int]:
        """Low-cardinality outcome counters; never include org/user/server IDs."""

        return {state.value: count for state, count in self._diagnostics.items()}

    def _retry_after(self) -> float:
        ceiling = min(
            self._backoff_max,
            self._backoff_base * (2 ** max(0, self._offline_attempts - 1)),
        )
        return ceiling * (0.5 + (self._random() * 0.5))

    @staticmethod
    def _feed_bytes(feed: BackendMcpRevisionFeed) -> int:
        return len(
            json.dumps(feed.model_dump(mode="json"), separators=(",", ":")).encode()
        )

    async def run_once(self) -> McpRevisionFeedRunResult:
        active = await self._subjects.active_subjects()
        if not active:
            return McpRevisionFeedRunResult(subjects=0, http_calls=0, results=())
        calls = 0
        results: list[McpRevisionFeedSubjectResult] = []
        any_offline = False
        for subject in active:
            pages = notices = bytes_read = 0
            try:
                cursor = await self._cursors.load(subject)
                seen_cursors = {cursor} if cursor is not None else set()
                while pages < self._max_pages:
                    calls += 1
                    try:
                        feed = await self._client.feed(
                            org_id=subject.org_id,
                            user_id=subject.user_id,
                            after_cursor=cursor,
                            limit=self._page_limit,
                        )
                    except BackendMcpRevisionCursorExpired:
                        await self._coordinator.reset_expired_subject(subject=subject)
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.CURSOR_EXPIRED,
                            pages=pages + 1,
                            notices=notices,
                            bytes_read=bytes_read,
                        )
                        break
                    except BackendMcpRevisionUnavailable:
                        any_offline = True
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.OFFLINE,
                            pages,
                            notices,
                            bytes_read,
                        )
                        break
                    pages += 1
                    page_bytes = self._feed_bytes(feed)
                    if (
                        len(feed.notices) + notices > self._max_notices
                        or page_bytes + bytes_read > self._max_bytes
                    ):
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.BOUND_EXCEEDED,
                            pages,
                            notices,
                            bytes_read,
                        )
                        break
                    quiescent = (
                        not feed.notices
                        or len(feed.notices) < self._page_limit
                        or feed.next_cursor is None
                    )
                    if not quiescent and (
                        feed.next_cursor == cursor or feed.next_cursor in seen_cursors
                    ):
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.CURSOR_STALLED,
                            pages,
                            notices,
                            bytes_read,
                        )
                        break
                    await self._coordinator.apply_page(subject=subject, feed=feed)
                    notices += len(feed.notices)
                    bytes_read += page_bytes
                    if quiescent:
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.APPLIED,
                            pages,
                            notices,
                            bytes_read,
                        )
                        break
                    cursor = feed.next_cursor
                    if cursor is not None:
                        seen_cursors.add(cursor)
                else:
                    result = McpRevisionFeedSubjectResult(
                        McpRevisionFeedSubjectState.BOUND_EXCEEDED,
                        pages,
                        notices,
                        bytes_read,
                    )
            except Exception:
                result = McpRevisionFeedSubjectResult(
                    McpRevisionFeedSubjectState.FAILED, pages, notices, bytes_read
                )
            self._diagnostics[result.state] += 1
            results.append(result)
        if any_offline:
            self._offline_attempts += 1
            retry_after = self._retry_after()
        else:
            self._offline_attempts = 0
            retry_after = None
        return McpRevisionFeedRunResult(
            subjects=len(active),
            http_calls=calls,
            results=tuple(results),
            retry_after_seconds=retry_after,
        )
