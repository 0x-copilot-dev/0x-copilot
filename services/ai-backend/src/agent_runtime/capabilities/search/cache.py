"""Per-workspace on-disk cache of extracted article text (PRD §8 decision 3).

Scope was the decision, not the mechanism. Per-run re-fetches the same URL every
turn, and the common case is a follow-up question about the page just read.
Per-conversation does not survive a restart, which the desktop app does often.
So: the workspace's own directory, keyed by URL, with a short TTL and an LRU
size bound.

What is cached is the **extracted article**, not the raw HTML. It is an order of
magnitude smaller, so the size bound buys far more pages, and a hit skips both
the fetch and the extraction. Only successful extractions are stored — caching a
failure would pin a transient network blip in place for the whole TTL.

Two clocks, deliberately:

* ``fetched_at`` inside the record answers *freshness* (TTL);
* the file's mtime, touched on every read, answers *recency* (LRU eviction).

One clock cannot do both — reusing mtime for freshness would make a popular page
immortal, and reusing ``fetched_at`` for recency would evict the page being read
most.

Every operation is best-effort. A cache that can fail a search is worse than no
cache, so every filesystem error degrades to a miss or a no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from agent_runtime.capabilities.search.contracts import PageCacheBudgets

_LOGGER = logging.getLogger(__name__)


class WorkspacePageCache:
    """URL-keyed store of extracted article text under one workspace directory."""

    class Keys:
        URL = "url"
        FETCHED_AT = "fetched_at"
        TEXT = "text"

    class Values:
        SUFFIX = ".json"
        ENCODING = "utf-8"
        TEMP_SUFFIX = ".tmp"
        #: The OS user boundary is the tenant boundary on the desktop profile,
        #: matching the file store's own 0o700/0o600 posture.
        DIR_MODE = 0o700
        FILE_MODE = 0o600

    class Log:
        READ_FAILED = "search.cache_read_failed key=%s"
        WRITE_FAILED = "search.cache_write_failed key=%s"
        EVICT_FAILED = "search.cache_evict_failed dir=%s"

    def __init__(
        self,
        *,
        directory: Path,
        budgets: PageCacheBudgets | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind the cache directory, its bounds, and the wall clock used for TTL."""

        self._directory = directory
        self._budgets = budgets or PageCacheBudgets()
        self._clock = clock

    def get(self, url: str) -> str | None:
        """Return fresh cached article text for ``url``, else ``None``.

        A record past its TTL is deleted on the way past: expiry is the only
        pass over the directory that is guaranteed to happen, so it is also the
        cheapest place to reclaim the space.
        """

        path = self._path(url)
        try:
            record = json.loads(path.read_text(encoding=self.Values.ENCODING))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            _LOGGER.info(self.Log.READ_FAILED, path.name, exc_info=True)
            return None
        fetched_at = record.get(self.Keys.FETCHED_AT)
        text = record.get(self.Keys.TEXT)
        if not isinstance(fetched_at, (int, float)) or not isinstance(text, str):
            self._discard(path)
            return None
        if self._clock() - fetched_at > self._budgets.ttl_seconds:
            self._discard(path)
            return None
        self._touch(path)
        return text

    def put(self, url: str, text: str) -> None:
        """Store ``text`` for ``url`` and evict down to the size bound."""

        if not text:
            return
        path = self._path(url)
        record = {
            self.Keys.URL: url,
            self.Keys.FETCHED_AT: self._clock(),
            self.Keys.TEXT: text,
        }
        temporary = path.with_suffix(self.Values.TEMP_SUFFIX)
        try:
            self._directory.mkdir(
                parents=True, exist_ok=True, mode=self.Values.DIR_MODE
            )
            temporary.write_text(json.dumps(record), encoding=self.Values.ENCODING)
            os.chmod(temporary, self.Values.FILE_MODE)
            # Replace rather than write in place: a reader either sees the whole
            # previous record or the whole new one, never a half-written file.
            os.replace(temporary, path)
        except OSError:
            _LOGGER.info(self.Log.WRITE_FAILED, path.name, exc_info=True)
            self._discard(temporary)
            return
        # A write is a use. Stamping it through the same clock a read stamps
        # through is what keeps the LRU ordering coherent: leaving the mtime the
        # OS assigned would mix two clocks, and the eviction pass would compare
        # written records against read records on different scales.
        self._touch(path)
        self.evict()

    def evict(self) -> None:
        """Delete least-recently-used records until the size bound holds."""

        try:
            records = sorted(
                (
                    (entry.stat().st_mtime, entry.stat().st_size, entry)
                    for entry in self._directory.glob(f"*{self.Values.SUFFIX}")
                ),
                key=lambda item: item[0],
            )
        except OSError:
            _LOGGER.info(self.Log.EVICT_FAILED, self._directory, exc_info=True)
            return
        total = sum(size for _mtime, size, _entry in records)
        for _mtime, size, entry in records:
            if total <= self._budgets.max_total_bytes:
                return
            self._discard(entry)
            total -= size

    def _path(self, url: str) -> Path:
        """Return the record path for ``url``.

        SHA-256 rather than any reversible encoding: a URL is untrusted input,
        and a digest cannot contain a separator, a ``..`` segment, or a
        reserved name. Nothing ever needs to read the URL back out of the path —
        the record itself carries it.
        """

        digest = hashlib.sha256(url.encode(self.Values.ENCODING)).hexdigest()
        return self._directory / f"{digest}{self.Values.SUFFIX}"

    def _touch(self, path: Path) -> None:
        """Mark a record as most-recently-used for the LRU pass."""

        try:
            now = self._clock()
            os.utime(path, (now, now))
        except OSError:
            return

    def _discard(self, path: Path) -> None:
        """Delete a record, tolerating a concurrent delete."""

        try:
            path.unlink(missing_ok=True)
        except OSError:
            return


__all__ = ("WorkspacePageCache",)
