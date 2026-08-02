"""The extraction pipeline that runs *inside* ``web_search``.

There is no second ``fetch_page`` tool, on purpose: a second tool costs a second
model round-trip to decide to call it — more latency and more tokens for the
same bytes. Every search silently gets better and the agent's contract is
unchanged (PRD §8 decision 1).

The pipeline is one pass::

    engine results → cache lookup → parallel fetch → extract → window → budget

and every stage reports failure as a value, so the chain degrades rung by rung
to the engine snippet the tool has always returned (PRD AC1).

Desktop only. The activation signal is the presence of a real workspace
directory on this machine — which is also where the cache has to live — so there
is no separate feature flag to drift from it. Off the desktop there is no
workspace root, nothing is fetched, and ``web_search`` behaves exactly as it did
before this module existed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_runtime.capabilities.search.cache import WorkspacePageCache
from agent_runtime.capabilities.search.contracts import (
    EnrichedSource,
    ExtractedArticle,
    PassageOrigin,
    SearchCandidate,
    SearchContentBudget,
    SearchFetchBudgets,
    SourceStatus,
    WindowedPassage,
)
from agent_runtime.capabilities.search.extraction import ArticleExtractor
from agent_runtime.capabilities.search.fetching import PageFetcher
from agent_runtime.capabilities.search.windowing import SnippetWindow

_LOGGER = logging.getLogger(__name__)


class SearchEnrichmentEnvironment:
    """Resolve the desktop workspace directory the cache and the feature need.

    The same two variables the file-store gate in
    ``runtime_worker.dependencies`` reads, for the same reason: they are the
    signal that this process is running beside a real user on a real machine
    with a real directory to write to. Reading them here rather than adding a
    setting keeps one activation vocabulary instead of two.
    """

    STORE_BACKEND = "RUNTIME_STORE_BACKEND"
    FILE_STORE_ROOT = "RUNTIME_FILE_STORE_ROOT"
    FILE_BACKEND = "file"
    CACHE_SEGMENTS = ("cache", "web-pages")

    @classmethod
    def cache_directory(cls, environ: Mapping[str, str]) -> Path | None:
        """Return the workspace cache directory, or ``None`` when not on desktop."""

        backend = environ.get(cls.STORE_BACKEND, "").strip().lower()
        root = environ.get(cls.FILE_STORE_ROOT, "").strip()
        if backend != cls.FILE_BACKEND or not root:
            return None
        return Path(root).expanduser().joinpath(*cls.CACHE_SEGMENTS)


class SearchEnrichment:
    """Turn engine results into snippet-anchored windows of the real pages."""

    class Log:
        ENRICHED = (
            "search.enriched query_sources=%d windowed=%d lead=%d snippet=%d "
            "content_tokens=%d"
        )
        WINDOW_FAILED = "search.window_failed url=%s"

    def __init__(
        self,
        *,
        cache: WorkspacePageCache | None = None,
        fetcher: PageFetcher | None = None,
        extractor: ArticleExtractor | None = None,
        window: SnippetWindow | None = None,
        content_budget: SearchContentBudget | None = None,
        fetch_budgets: SearchFetchBudgets | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Compose the pipeline; every collaborator is injectable for tests."""

        self._cache = cache
        self._fetch_budgets = fetch_budgets or SearchFetchBudgets()
        self._fetcher = fetcher or PageFetcher(budgets=self._fetch_budgets)
        self._extractor = extractor or ArticleExtractor()
        self._window = window or SnippetWindow()
        self._budget = content_budget or SearchContentBudget()
        self._clock = clock

    @classmethod
    def for_environment(
        cls, environ: Mapping[str, str], **overrides: Any
    ) -> "SearchEnrichment | None":
        """Build the pipeline for a desktop workspace, or ``None`` elsewhere."""

        directory = SearchEnrichmentEnvironment.cache_directory(environ)
        if directory is None:
            return None
        return cls(cache=WorkspacePageCache(directory=directory), **overrides)

    def enrich(
        self, raw_results: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        """Return the ``(content, artifact)`` pair for one search.

        ``content`` carries only the windows — that is the token cost. The full
        extracted article per source goes to ``artifact``, which never enters
        the prompt but is what makes "read more from source 2" free later.
        """

        candidates = [SearchCandidate.from_raw(raw) for raw in raw_results]
        deadline = self._clock() + self._fetch_budgets.wall_clock_seconds
        articles = self._articles(candidates, deadline=deadline)
        sources = self._sources(candidates, articles)
        content = [source.content_row() for source in sources]
        self._log(sources, content)
        return content, [source.artifact_row() for source in sources]

    def _articles(
        self, candidates: Sequence[SearchCandidate], *, deadline: float
    ) -> dict[str, ExtractedArticle]:
        """Resolve each distinct URL to an article, via the cache then the network."""

        articles: dict[str, ExtractedArticle] = {}
        if not self._extractor.available:
            # Nothing downstream can use a fetched page, so fetching one is pure
            # cost. This mattered: with `trafilatura` absent — the shipped state
            # today — every web_search issued up to four page GETs plus up to
            # four robots.txt GETs under a 12s wall clock, and then returned
            # byte-identical output. "Inert" was true of the RESULT and false of
            # the latency. Checked before the fetch, not discovered after it.
            return {
                candidate.link: ExtractedArticle(
                    url=candidate.link, failure=SourceStatus.EXTRACTOR_UNAVAILABLE
                )
                for candidate in candidates
                if candidate.link
            }
        outstanding: list[str] = []
        for candidate in candidates:
            url = candidate.link
            if not url or url in articles or url in outstanding:
                continue
            cached = self._cache.get(url) if self._cache is not None else None
            if cached:
                articles[url] = ExtractedArticle(url=url, text=cached, cached=True)
                continue
            outstanding.append(url)
        for url, page in self._fetcher.fetch(outstanding, deadline=deadline).items():
            if page.failure is not None:
                articles[url] = ExtractedArticle(url=url, failure=page.failure)
                continue
            article = self._extractor.extract(html=page.html, url=url)
            articles[url] = article
            if article.usable and self._cache is not None:
                self._cache.put(url, article.text)
        return articles

    def _sources(
        self,
        candidates: Sequence[SearchCandidate],
        articles: Mapping[str, ExtractedArticle],
    ) -> list[EnrichedSource]:
        """Choose each source's passage under the shared content budget."""

        resolved = [
            (candidate, self._article_of(candidate, articles))
            for candidate in candidates
        ]
        extractable = {
            index for index, (_, article) in enumerate(resolved) if article.usable
        }
        width = self._budget.window_chars_for(
            remaining_chars=self._remaining_chars(resolved, extractable),
            source_count=len(extractable),
        )
        return [
            self._source(candidate, article, width)
            if index in extractable and width > 0
            else self._snippet_source(candidate, article)
            for index, (candidate, article) in enumerate(resolved)
        ]

    def _remaining_chars(
        self,
        resolved: Sequence[tuple[SearchCandidate, ExtractedArticle]],
        extractable: set[int],
    ) -> int:
        """Return the characters left for windows after the fixed costs.

        Title, link and every snippet-fallback passage are charged first and are
        never trimmed. Trimming them is what AC1 forbids: a snippet row *is*
        today's output, so shortening one to fund a window would make the tool
        worse than it was for that source.
        """

        fixed = sum(
            len(candidate.title)
            + len(candidate.link)
            + (0 if index in extractable else len(candidate.snippet))
            for index, (candidate, _article) in enumerate(resolved)
        )
        return self._budget.total_chars() - fixed

    @staticmethod
    def _article_of(
        candidate: SearchCandidate, articles: Mapping[str, ExtractedArticle]
    ) -> ExtractedArticle:
        """Return the article for ``candidate``, or a typed 'nothing fetched' one."""

        return articles.get(
            candidate.link,
            ExtractedArticle(url=candidate.link, failure=SourceStatus.UNSUPPORTED_URL),
        )

    def _source(
        self, candidate: SearchCandidate, article: ExtractedArticle, width: int
    ) -> EnrichedSource:
        """Window one extracted article, falling back to its snippet on any error."""

        try:
            passage = self._window.build(
                article=article.text,
                snippet=candidate.snippet,
                window_chars=width,
                lead_chars=min(self._budget.lead_chars, width),
            )
        except Exception:  # noqa: BLE001 - a windowing bug costs one source, not the search
            _LOGGER.warning(self.Log.WINDOW_FAILED, candidate.link, exc_info=True)
            return self._snippet_source(candidate, article)
        return EnrichedSource(
            candidate=candidate,
            # Belt and braces: the width arithmetic already bounds this, so a
            # cut here would mean a windowing bug — and AC2 is a bound that has
            # to hold even then.
            passage=WindowedPassage(text=passage.text[:width], origin=passage.origin),
            status=SourceStatus.CACHED if article.cached else SourceStatus.EXTRACTED,
            article_text=article.text,
        )

    @staticmethod
    def _snippet_source(
        candidate: SearchCandidate, article: ExtractedArticle
    ) -> EnrichedSource:
        """Return the AC1 floor for one source: exactly the engine's own snippet."""

        return EnrichedSource(
            candidate=candidate,
            passage=WindowedPassage(
                text=candidate.snippet, origin=PassageOrigin.SNIPPET
            ),
            status=article.failure or SourceStatus.BUDGET_EXHAUSTED,
            article_text=article.text,
        )

    def _log(
        self, sources: Sequence[EnrichedSource], content: Sequence[Mapping[str, str]]
    ) -> None:
        """Emit the one line that makes AC2/AC4 measurable from a real run."""

        origins = [source.passage.origin for source in sources]
        _LOGGER.info(
            self.Log.ENRICHED,
            len(sources),
            origins.count(PassageOrigin.WINDOW),
            origins.count(PassageOrigin.LEAD),
            origins.count(PassageOrigin.SNIPPET),
            self._budget.estimated_tokens(content),
        )


__all__ = ("SearchEnrichment", "SearchEnrichmentEnvironment")
