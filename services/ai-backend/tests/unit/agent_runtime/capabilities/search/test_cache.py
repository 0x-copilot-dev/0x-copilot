from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.capabilities.search.cache import WorkspacePageCache
from agent_runtime.capabilities.search.contracts import PageCacheBudgets


class MovableClock:
    """A wall clock the test advances, so TTL is asserted rather than slept on."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CacheMixin:
    URL = "https://example.test/article?q=1"
    OTHER_URL = "https://example.test/other"
    TEXT = "The extracted article body. " * 10

    def _cache(
        self,
        directory: Path,
        *,
        clock: MovableClock | None = None,
        budgets: PageCacheBudgets | None = None,
    ) -> tuple[WorkspacePageCache, MovableClock]:
        moving = clock or MovableClock()
        cache = WorkspacePageCache(
            directory=directory / "web-pages", budgets=budgets, clock=moving
        )
        return cache, moving


class TestWorkspacePageCache(CacheMixin):
    def test_round_trips_extracted_text(self, tmp_path: Path) -> None:
        cache, _clock = self._cache(tmp_path)

        cache.put(self.URL, self.TEXT)

        assert cache.get(self.URL) == self.TEXT

    def test_unknown_url_is_a_miss(self, tmp_path: Path) -> None:
        cache, _clock = self._cache(tmp_path)

        assert cache.get(self.URL) is None

    def test_url_is_not_used_as_a_path_segment(self, tmp_path: Path) -> None:
        # A URL is untrusted input; the record name must not be able to carry a
        # separator or a traversal segment.
        cache, _clock = self._cache(tmp_path)

        cache.put("https://example.test/../../escape?x=/y", self.TEXT)

        names = [entry.name for entry in (tmp_path / "web-pages").iterdir()]
        assert len(names) == 1
        assert "/" not in names[0] and ".." not in names[0]

    def test_record_past_its_ttl_is_a_miss_and_is_deleted(self, tmp_path: Path) -> None:
        budgets = PageCacheBudgets(ttl_seconds=100)
        cache, clock = self._cache(tmp_path, budgets=budgets)
        cache.put(self.URL, self.TEXT)

        clock.advance(101)

        assert cache.get(self.URL) is None
        assert list((tmp_path / "web-pages").iterdir()) == []

    def test_record_within_its_ttl_is_a_hit(self, tmp_path: Path) -> None:
        budgets = PageCacheBudgets(ttl_seconds=100)
        cache, clock = self._cache(tmp_path, budgets=budgets)
        cache.put(self.URL, self.TEXT)

        clock.advance(99)

        assert cache.get(self.URL) == self.TEXT

    def test_corrupt_record_is_a_miss_rather_than_an_error(
        self, tmp_path: Path
    ) -> None:
        cache, _clock = self._cache(tmp_path)
        cache.put(self.URL, self.TEXT)
        record = next((tmp_path / "web-pages").iterdir())
        record.write_text("{not json", encoding="utf-8")

        assert cache.get(self.URL) is None

    def test_record_missing_its_timestamp_is_discarded(self, tmp_path: Path) -> None:
        cache, _clock = self._cache(tmp_path)
        cache.put(self.URL, self.TEXT)
        record = next((tmp_path / "web-pages").iterdir())
        record.write_text(json.dumps({"text": "orphan"}), encoding="utf-8")

        assert cache.get(self.URL) is None
        assert list((tmp_path / "web-pages").iterdir()) == []

    def test_empty_text_is_not_stored(self, tmp_path: Path) -> None:
        cache, _clock = self._cache(tmp_path)

        cache.put(self.URL, "")

        assert cache.get(self.URL) is None

    def test_unwritable_directory_degrades_to_a_no_op(self, tmp_path: Path) -> None:
        # The parent is a FILE, so mkdir cannot succeed. A cache that can fail a
        # search is worse than no cache.
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("", encoding="utf-8")
        cache = WorkspacePageCache(directory=blocked / "web-pages")

        cache.put(self.URL, self.TEXT)

        assert cache.get(self.URL) is None


class TestWorkspacePageCacheEviction(CacheMixin):
    BIG_TEXT = "x" * 4_000

    def test_evicts_least_recently_used_records_over_the_size_bound(
        self, tmp_path: Path
    ) -> None:
        budgets = PageCacheBudgets(max_total_bytes=9_000)
        cache, clock = self._cache(tmp_path, budgets=budgets)

        cache.put("https://example.test/1", self.BIG_TEXT)
        clock.advance(10)
        cache.put("https://example.test/2", self.BIG_TEXT)
        clock.advance(10)
        cache.put("https://example.test/3", self.BIG_TEXT)

        assert cache.get("https://example.test/1") is None
        assert cache.get("https://example.test/3") == self.BIG_TEXT

    def test_reading_a_record_keeps_it_from_being_evicted(self, tmp_path: Path) -> None:
        # Recency, not age: the page the user keeps asking about must survive.
        budgets = PageCacheBudgets(max_total_bytes=9_000)
        cache, clock = self._cache(tmp_path, budgets=budgets)
        cache.put("https://example.test/1", self.BIG_TEXT)
        clock.advance(10)
        cache.put("https://example.test/2", self.BIG_TEXT)

        clock.advance(10)
        assert cache.get("https://example.test/1") == self.BIG_TEXT
        clock.advance(10)
        cache.put("https://example.test/3", self.BIG_TEXT)

        assert cache.get("https://example.test/1") == self.BIG_TEXT
        assert cache.get("https://example.test/2") is None

    def test_records_within_the_bound_are_kept(self, tmp_path: Path) -> None:
        cache, _clock = self._cache(tmp_path)

        cache.put(self.URL, self.TEXT)
        cache.put(self.OTHER_URL, self.TEXT)

        assert cache.get(self.URL) == self.TEXT
        assert cache.get(self.OTHER_URL) == self.TEXT
