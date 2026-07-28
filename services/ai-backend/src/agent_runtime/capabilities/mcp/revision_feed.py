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
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
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
_CURSOR_MAX_BYTES: Final = 1024


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
        _validate_cursor(cursor)
        async with self._guard:
            self._cursors[subject] = cursor

    async def clear(self, subject: McpRevisionSubject) -> None:
        async with self._guard:
            self._cursors.pop(subject, None)


class DesktopFilesystemMcpRevisionCursorStore:
    """Desktop-only durable cursor adapter rooted at ``RUNTIME_FILE_STORE_ROOT``.

    The directory contains opaque SHA-256 filenames, never tenant/user strings.
    Each write is staged in the same directory, fsynced, atomically replaced,
    then the directory is fsynced.  Bad bytes are a hard error rather than a
    reason to silently rewind a feed.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_bytes: int = _CURSOR_MAX_BYTES,
    ) -> None:
        configured_root = (
            root if root is not None else os.environ.get("RUNTIME_FILE_STORE_ROOT")
        )
        if not configured_root:
            raise ValueError(
                "RUNTIME_FILE_STORE_ROOT is required for filesystem cursors"
            )
        if max_bytes <= 0 or max_bytes > _CURSOR_MAX_BYTES:
            raise ValueError("max_bytes must be between 1 and 1024")
        self._root = Path(configured_root).absolute()
        self._max_bytes = max_bytes
        self._guard = asyncio.Lock()

    def _ensure_root(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = os.lstat(self._root)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise McpRevisionCursorStoreError("cursor root is not a real directory")
        if os.path.realpath(self._root) != str(self._root):
            raise McpRevisionCursorStoreError("cursor root traverses a symlink")
        os.chmod(self._root, 0o700)

    def _path_for(self, subject: McpRevisionSubject) -> Path:
        digest = hashlib.sha256(
            f"{subject.org_id}\0{subject.user_id}".encode("utf-8")
        ).hexdigest()
        return self._root / f"mcp-revision-{digest}.cursor"

    def _validate_path(self, path: Path) -> None:
        if path.parent != self._root or path.name != path.name.replace("/", ""):
            raise McpRevisionCursorStoreError("unsafe cursor path")
        try:
            file_stat = os.lstat(path)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise McpRevisionCursorStoreError("cursor path is not a regular file")
        if file_stat.st_mode & 0o077:
            raise McpRevisionCursorStoreError(
                "cursor file permissions are not restrictive"
            )

    async def load(self, subject: McpRevisionSubject) -> str | None:
        async with self._guard:
            self._ensure_root()
            path = self._path_for(subject)
            self._validate_path(path)
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not safely open cursor"
                ) from exc
            try:
                file_stat = os.fstat(fd)
                if (
                    not stat.S_ISREG(file_stat.st_mode)
                    or file_stat.st_size > self._max_bytes
                ):
                    raise McpRevisionCursorStoreError(
                        "cursor file is invalid or too large"
                    )
                raw = os.read(fd, self._max_bytes + 1)
            finally:
                os.close(fd)
            if len(raw) > self._max_bytes:
                raise McpRevisionCursorStoreError("cursor file is too large")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise McpRevisionCursorStoreError("cursor file is corrupt") from exc
            if not isinstance(payload, dict) or set(payload) != {"cursor"}:
                raise McpRevisionCursorStoreError("cursor file has an invalid schema")
            cursor = payload["cursor"]
            if not isinstance(cursor, str):
                raise McpRevisionCursorStoreError("cursor is not a string")
            _validate_cursor(cursor)
            return cursor

    async def save(self, subject: McpRevisionSubject, cursor: str) -> None:
        _validate_cursor(cursor)
        encoded = json.dumps({"cursor": cursor}, separators=(",", ":")).encode("utf-8")
        if len(encoded) > self._max_bytes:
            raise McpRevisionCursorStoreError("cursor is too large")
        async with self._guard:
            self._ensure_root()
            path = self._path_for(subject)
            self._validate_path(path)
            temporary = self._root / f".{path.name}.{secrets.token_hex(16)}.tmp"
            fd = -1
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                written = 0
                while written < len(encoded):
                    written += os.write(fd, encoded[written:])
                os.fsync(fd)
                os.close(fd)
                fd = -1
                os.replace(temporary, path)
                dir_fd = os.open(self._root, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                raise McpRevisionCursorStoreError(
                    "could not durably save cursor"
                ) from exc
            finally:
                if fd >= 0:
                    os.close(fd)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    async def clear(self, subject: McpRevisionSubject) -> None:
        async with self._guard:
            self._ensure_root()
            path = self._path_for(subject)
            self._validate_path(path)
            try:
                path.unlink()
            except FileNotFoundError:
                return
            dir_fd = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def _validate_cursor(cursor: str) -> None:
    if not cursor or len(cursor.encode("utf-8")) > _CURSOR_MAX_BYTES:
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
                    await self._coordinator.apply_page(subject=subject, feed=feed)
                    notices += len(feed.notices)
                    bytes_read += page_bytes
                    if feed.next_cursor is None:
                        result = McpRevisionFeedSubjectResult(
                            McpRevisionFeedSubjectState.APPLIED,
                            pages,
                            notices,
                            bytes_read,
                        )
                        break
                    cursor = feed.next_cursor
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
