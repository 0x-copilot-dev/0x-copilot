from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agent_runtime.capabilities.search.contracts import (
    EnrichedSource,
    ExtractedArticle,
    FetchedPage,
    PassageOrigin,
    SearchContentBudget,
    SearchFetchBudgets,
    SourceStatus,
)
from agent_runtime.capabilities.search.enrichment import (
    SearchEnrichment,
    SearchEnrichmentEnvironment,
)


class ScriptedFetcher:
    """Returns a scripted page per URL and records the batch it was asked for."""

    def __init__(self, pages: dict[str, FetchedPage]) -> None:
        self._pages = pages
        self.requested: list[tuple[str, ...]] = []

    def fetch(self, urls: Sequence[str], *, deadline: float) -> dict[str, FetchedPage]:
        self.requested.append(tuple(urls))
        return {
            url: self._pages.get(
                url, FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)
            )
            for url in urls
        }


class ScriptedExtractor:
    """Returns a scripted article per URL.

    ``available`` is part of the seam, not decoration: the pipeline skips
    fetching entirely when the extractor cannot run, so a double that omitted it
    would exercise a path production never takes.
    """

    def __init__(
        self,
        articles: dict[str, ExtractedArticle],
        *,
        available: bool = True,
    ) -> None:
        self._articles = articles
        self.available = available

    def extract(self, *, html: str, url: str) -> ExtractedArticle:
        return self._articles.get(
            url, ExtractedArticle(url=url, failure=SourceStatus.EXTRACTION_EMPTY)
        )


class RecordingCache:
    """In-memory stand-in for the on-disk cache."""

    def __init__(self, seed: dict[str, str] | None = None) -> None:
        self.entries = dict(seed or {})
        self.written: list[str] = []

    def get(self, url: str) -> str | None:
        return self.entries.get(url)

    def put(self, url: str, text: str) -> None:
        self.entries[url] = text
        self.written.append(url)


class ExplodingWindow:
    """A windowing stage that always raises, to pin the per-source fallback."""

    def build(self, **_kwargs: object) -> object:
        raise RuntimeError("windowing bug")


class EnrichmentMixin:
    ANSWER = (
        "The amendment took effect on 1 April 2026, and the previous wording "
        'read "reasonable endeavours" until that date.'
    )
    FILLER = "An unrelated paragraph that is not the answer. "
    URL_A = "https://example.test/a"
    URL_B = "https://example.test/b"
    SNIPPET_A = "The amendment took effect on 1 April 2026"
    SNIPPET_B = "A second result about the same topic"

    def article(self, answer: str | None = None) -> str:
        return self.FILLER * 20 + (answer or self.ANSWER) + " " + self.FILLER * 20

    def raw(self, *urls: str) -> list[dict[str, Any]]:
        snippets = {self.URL_A: self.SNIPPET_A, self.URL_B: self.SNIPPET_B}
        return [
            {
                "title": f"Title for {url}",
                "href": url,
                "body": snippets.get(url, "engine snippet"),
                "extra": "artifact-only",
            }
            for url in urls
        ]

    def _enrichment(self, **kwargs: object) -> SearchEnrichment:
        options: dict[str, Any] = {
            "fetcher": ScriptedFetcher({}),
            "extractor": ScriptedExtractor({}),
        }
        options.update(kwargs)
        return SearchEnrichment(**options)

    def _fetched(self, *urls: str) -> ScriptedFetcher:
        return ScriptedFetcher(
            {url: FetchedPage(url=url, html=f"<html>{url}</html>") for url in urls}
        )

    def _extracted(self, **articles: str) -> ScriptedExtractor:
        return ScriptedExtractor(
            {
                url: ExtractedArticle(url=url, text=text)
                for url, text in articles.items()
            }
        )


class TestFallbackChain(EnrichmentMixin):
    def test_located_snippet_yields_a_window_of_the_real_page(self) -> None:
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, _artifact = enrichment.enrich(self.raw(self.URL_A))

        assert self.ANSWER in content[0]["snippet"]
        assert len(content[0]["snippet"]) > len(self.SNIPPET_A)

    def test_unlocatable_snippet_yields_the_article_lead(self) -> None:
        # The engine rewrote the snippet, so it cannot be found on the page.
        raw = self.raw(self.URL_A)
        raw[0]["body"] = "A paraphrase the page does not literally contain"
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, artifact = enrichment.enrich(raw)

        assert content[0]["snippet"].startswith(self.FILLER[:20])
        assert artifact[0]["passage_origin"] == PassageOrigin.LEAD.value

    def test_failed_extraction_falls_back_to_the_engine_snippet(self) -> None:
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=ScriptedExtractor(
                {
                    self.URL_A: ExtractedArticle(
                        url=self.URL_A, failure=SourceStatus.EXTRACTOR_UNAVAILABLE
                    )
                }
            ),
        )

        content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert content[0]["snippet"] == self.SNIPPET_A
        assert artifact[0]["extraction_status"] == (
            SourceStatus.EXTRACTOR_UNAVAILABLE.value
        )

    def test_failed_fetch_falls_back_to_the_engine_snippet(self) -> None:
        enrichment = self._enrichment(
            fetcher=ScriptedFetcher(
                {
                    self.URL_A: FetchedPage(
                        url=self.URL_A, failure=SourceStatus.FETCH_TIMEOUT
                    )
                }
            ),
        )

        content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert content[0]["snippet"] == self.SNIPPET_A
        assert artifact[0]["extraction_status"] == SourceStatus.FETCH_TIMEOUT.value

    def test_a_windowing_bug_costs_one_source_not_the_search(self) -> None:
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
            window=ExplodingWindow(),
        )

        content, _artifact = enrichment.enrich(self.raw(self.URL_A))

        assert content[0]["snippet"] == self.SNIPPET_A

    def test_one_failing_source_does_not_degrade_its_neighbour(self) -> None:
        enrichment = self._enrichment(
            fetcher=ScriptedFetcher(
                {
                    self.URL_A: FetchedPage(url=self.URL_A, html="<html>a</html>"),
                    self.URL_B: FetchedPage(
                        url=self.URL_B, failure=SourceStatus.FETCH_FAILED
                    ),
                }
            ),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, _artifact = enrichment.enrich(self.raw(self.URL_A, self.URL_B))

        assert self.ANSWER in content[0]["snippet"]
        assert content[1]["snippet"] == self.SNIPPET_B


class TestNeverWorseThanToday(EnrichmentMixin):
    """AC1: total failure of the new path returns exactly the old output."""

    def test_every_fetch_failing_returns_the_engine_snippets_verbatim(self) -> None:
        raw = self.raw(self.URL_A, self.URL_B)
        enrichment = self._enrichment(
            fetcher=ScriptedFetcher(
                {
                    url: FetchedPage(url=url, failure=SourceStatus.FETCH_FAILED)
                    for url in (self.URL_A, self.URL_B)
                }
            )
        )

        content, _artifact = enrichment.enrich(raw)

        assert content == [
            {
                "snippet": result["body"],
                "title": result["title"],
                "link": result["href"],
            }
            for result in raw
        ]

    def test_a_result_with_no_link_is_returned_unchanged(self) -> None:
        raw = [{"title": "No link", "body": "Only a snippet"}]

        content, _artifact = self._enrichment().enrich(raw)

        assert content == [
            {"snippet": "Only a snippet", "title": "No link", "link": ""}
        ]

    def test_engine_fields_of_the_wrong_type_do_not_raise(self) -> None:
        raw = [{"title": None, "href": 42, "body": ["not", "a", "string"]}]

        content, _artifact = self._enrichment().enrich(raw)

        assert content == [{"snippet": "", "title": "", "link": ""}]

    def test_no_results_returns_empty_halves(self) -> None:
        assert self._enrichment().enrich([]) == ([], [])


class TestContentAndArtifactSplit(EnrichmentMixin):
    def test_artifact_carries_the_full_article_and_content_does_not(self) -> None:
        article = self.article()
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: article}),
        )

        content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert artifact[0]["extracted_text"] == article
        assert len(content[0]["snippet"]) < len(article)

    def test_artifact_keeps_every_engine_field(self) -> None:
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        _content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert artifact[0]["extra"] == "artifact-only"
        assert artifact[0]["href"] == self.URL_A

    def test_content_rows_keep_the_established_key_set(self) -> None:
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, _artifact = enrichment.enrich(self.raw(self.URL_A))

        assert list(content[0]) == ["snippet", "title", "link"]


class TestContentTokenBudget(EnrichmentMixin):
    """AC2: what reaches the prompt is bounded, and asserted rather than hoped."""

    def _four_source_enrichment(
        self, budget: SearchContentBudget
    ) -> tuple[list[str], SearchEnrichment]:
        urls = [f"https://example.test/{index}" for index in range(4)]
        return (
            urls,
            SearchEnrichment(
                fetcher=self._fetched(*urls),
                extractor=ScriptedExtractor(
                    {
                        url: ExtractedArticle(url=url, text=self.article())
                        for url in urls
                    }
                ),
                content_budget=budget,
            ),
        )

    def test_content_never_exceeds_the_configured_token_budget(self) -> None:
        budget = SearchContentBudget(content_token_budget=400)
        urls, enrichment = self._four_source_enrichment(budget)
        raw = [
            {"title": f"Title {url}", "href": url, "body": self.SNIPPET_A}
            for url in urls
        ]

        content, _artifact = enrichment.enrich(raw)

        assert budget.estimated_tokens(content) <= budget.content_token_budget

    def test_a_generous_budget_still_respects_the_window_ceiling(self) -> None:
        budget = SearchContentBudget(content_token_budget=100_000, window_chars=300)
        urls, enrichment = self._four_source_enrichment(budget)
        raw = [
            {"title": f"Title {url}", "href": url, "body": self.SNIPPET_A}
            for url in urls
        ]

        content, _artifact = enrichment.enrich(raw)

        assert all(len(row["snippet"]) <= 300 for row in content)

    def test_a_budget_too_small_to_window_leaves_every_source_on_its_snippet(
        self,
    ) -> None:
        # Shrinking a snippet row to fund a window would make the tool worse
        # than it is today for that source, which AC1 forbids. So the windows
        # are what give way, not the snippets.
        budget = SearchContentBudget(content_token_budget=1)
        urls, enrichment = self._four_source_enrichment(budget)
        raw = [
            {"title": f"Title {url}", "href": url, "body": self.SNIPPET_A}
            for url in urls
        ]

        content, artifact = enrichment.enrich(raw)

        assert [row["snippet"] for row in content] == [self.SNIPPET_A] * 4
        assert artifact[0]["extraction_status"] == SourceStatus.BUDGET_EXHAUSTED.value

    def test_snippet_only_sources_are_charged_before_windows_are_sized(self) -> None:
        budget = SearchContentBudget(content_token_budget=400)
        long_snippet = "A very long engine snippet. " * 20
        enrichment = SearchEnrichment(
            fetcher=ScriptedFetcher(
                {
                    self.URL_A: FetchedPage(url=self.URL_A, html="<html>a</html>"),
                    self.URL_B: FetchedPage(
                        url=self.URL_B, failure=SourceStatus.FETCH_FAILED
                    ),
                }
            ),
            extractor=self._extracted(**{self.URL_A: self.article()}),
            content_budget=budget,
        )
        raw = self.raw(self.URL_A, self.URL_B)
        raw[1]["body"] = long_snippet

        content, _artifact = enrichment.enrich(raw)

        assert content[1]["snippet"] == long_snippet
        assert budget.estimated_tokens(content) <= budget.content_token_budget


class TestPageCacheIntegration(EnrichmentMixin):
    def test_a_cache_hit_skips_the_fetch_entirely(self) -> None:
        fetcher = self._fetched(self.URL_A)
        enrichment = self._enrichment(
            fetcher=fetcher,
            extractor=self._extracted(**{self.URL_A: "never used " * 40}),
            cache=RecordingCache({self.URL_A: self.article()}),
        )

        content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert fetcher.requested == [()]
        assert self.ANSWER in content[0]["snippet"]
        assert artifact[0]["extraction_status"] == SourceStatus.CACHED.value

    def test_a_successful_extraction_is_written_to_the_cache(self) -> None:
        cache = RecordingCache()
        enrichment = self._enrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
            cache=cache,
        )

        enrichment.enrich(self.raw(self.URL_A))

        assert cache.written == [self.URL_A]

    def test_a_failed_extraction_is_not_cached(self) -> None:
        # Caching a failure would pin a transient network blip in place for the
        # whole TTL.
        cache = RecordingCache()
        enrichment = self._enrichment(
            fetcher=ScriptedFetcher(
                {
                    self.URL_A: FetchedPage(
                        url=self.URL_A, failure=SourceStatus.FETCH_FAILED
                    )
                }
            ),
            cache=cache,
        )

        enrichment.enrich(self.raw(self.URL_A))

        assert cache.written == []


class TestDeduplicationAndBudgetPlumbing(EnrichmentMixin):
    def test_the_same_url_twice_is_fetched_once(self) -> None:
        fetcher = self._fetched(self.URL_A)
        enrichment = self._enrichment(
            fetcher=fetcher,
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, _artifact = enrichment.enrich(self.raw(self.URL_A, self.URL_A))

        assert fetcher.requested == [(self.URL_A,)]
        assert len(content) == 2

    def test_the_wall_clock_budget_reaches_the_fetcher_as_a_deadline(self) -> None:
        seen: list[float] = []

        class DeadlineRecordingFetcher(ScriptedFetcher):
            def fetch(self, urls, *, deadline):  # type: ignore[no-untyped-def]
                seen.append(deadline)
                return super().fetch(urls, deadline=deadline)

        enrichment = SearchEnrichment(
            fetcher=DeadlineRecordingFetcher({}),
            extractor=ScriptedExtractor({}),
            fetch_budgets=SearchFetchBudgets(wall_clock_seconds=7.0),
            clock=lambda: 100.0,
        )

        enrichment.enrich(self.raw(self.URL_A))

        assert seen == [107.0]


class TestDesktopOnlyActivation:
    DESKTOP_ENV = {
        "RUNTIME_STORE_BACKEND": "file",
        "RUNTIME_FILE_STORE_ROOT": "/tmp/copilot-store",
    }

    def test_no_workspace_root_means_no_extraction_path_at_all(self) -> None:
        assert SearchEnrichment.for_environment({}) is None

    def test_a_non_file_store_backend_means_no_extraction_path(self) -> None:
        environ = {
            "RUNTIME_STORE_BACKEND": "postgres",
            "RUNTIME_FILE_STORE_ROOT": "/tmp/copilot-store",
        }

        assert SearchEnrichment.for_environment(environ) is None

    def test_a_file_backend_without_a_root_means_no_extraction_path(self) -> None:
        assert (
            SearchEnrichment.for_environment({"RUNTIME_STORE_BACKEND": "file"}) is None
        )

    def test_a_desktop_workspace_builds_the_pipeline(self) -> None:
        assert isinstance(
            SearchEnrichment.for_environment(self.DESKTOP_ENV), SearchEnrichment
        )

    def test_the_cache_lives_under_the_workspace_directory(self) -> None:
        directory = SearchEnrichmentEnvironment.cache_directory(self.DESKTOP_ENV)

        assert directory == Path("/tmp/copilot-store/cache/web-pages")


class TestEnrichedSourceRendering:
    def test_status_and_origin_are_reported_on_the_artifact_row(self) -> None:
        source = EnrichedSource(
            candidate={"title": "T", "link": "L", "snippet": "S", "raw": {"k": "v"}},
            passage={"text": "windowed", "origin": PassageOrigin.WINDOW},
            status=SourceStatus.EXTRACTED,
            article_text="full text",
        )

        assert source.artifact_row() == {
            "k": "v",
            "extracted_text": "full text",
            "extraction_status": "extracted",
            "passage_origin": "window",
        }
        assert source.content_row() == {
            "snippet": "windowed",
            "title": "T",
            "link": "L",
        }


class TestDefaultComposition(EnrichmentMixin):
    def test_the_default_pipeline_windows_without_any_injected_stage(self) -> None:
        # Only the two network-touching stages are injected: the windowing,
        # budgeting and rendering the model actually sees are the real ones.
        enrichment = SearchEnrichment(
            fetcher=self._fetched(self.URL_A),
            extractor=self._extracted(**{self.URL_A: self.article()}),
        )

        content, artifact = enrichment.enrich(self.raw(self.URL_A))

        assert artifact[0]["passage_origin"] == PassageOrigin.WINDOW.value
        assert self.ANSWER in content[0]["snippet"]


class TestNoExtractorMeansNoFetch(EnrichmentMixin):
    """ "Inert" was true of the output and false of the latency.

    `trafilatura` is not installed, which is the shipped state today, so every
    fetched page was discarded with `extractor_unavailable`. The pipeline
    discovered that AFTER fetching: on desktop each `web_search` issued up to
    four page GETs plus up to four robots.txt GETs, under a 12s wall clock, to
    return byte-identical output. Availability is now checked before the fetch.
    """

    def test_an_unavailable_extractor_issues_no_requests(self) -> None:
        fetcher = ScriptedFetcher({})
        enrichment = self._enrichment(
            fetcher=fetcher,
            extractor=ScriptedExtractor({}, available=False),
        )

        enrichment.enrich(self.raw(self.URL_A, self.URL_B))

        assert fetcher.requested == []

    def test_the_model_visible_content_is_the_snippet_fallback(self) -> None:
        raw = self.raw(self.URL_A, self.URL_B)
        available_content, _ = self._enrichment(
            fetcher=ScriptedFetcher({}),
            extractor=ScriptedExtractor({}, available=True),
        ).enrich(raw)
        skipped_content, skipped_artifact = self._enrichment(
            fetcher=ScriptedFetcher({}),
            extractor=ScriptedExtractor({}, available=False),
        ).enrich(raw)

        # AC1: skipping the fetch changes the COST, never the answer.
        assert skipped_content == available_content
        # The artifact still differs, and should: the reason genuinely is
        # "no extractor" rather than "the fetch failed", and flattening the two
        # would make a missing dependency look like a network problem.
        assert {source["extraction_status"] for source in skipped_artifact} == {
            "extractor_unavailable"
        }
