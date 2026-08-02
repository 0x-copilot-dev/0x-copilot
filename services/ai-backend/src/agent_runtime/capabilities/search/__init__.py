"""Local page extraction for the built-in ``web_search`` tool.

A search snippet is not an answer: it is one to three sentences chosen by a
general-purpose ranker for a human scanning a results page, and it habitually
stops right before the part the agent needs. This package closes that gap
without a hosted search API — it fetches the pages the engine just pointed at,
extracts their main content on the user's own machine, and returns a window of
each page anchored on the text the engine matched.

The design is in ``docs/plan/local-search-extraction/PRD.md``. The three
decisions that shape the code:

* extraction runs **inside** ``web_search`` — a second tool would cost a second
  model round-trip to decide to call it;
* there is **no ranker** — the engine already told us which part of the page
  matched, so locating the snippet in the extracted article beats re-ranking it
  with an embedding model, at zero added latency and zero added dependency;
* the cache is **per-workspace, on disk, short-TTL** — a follow-up question
  about the page just read is the common case, and the desktop app restarts
  often.

Nothing here may make ``web_search`` worse than it was. Every stage reports its
failure as a value, and a total failure of this package returns exactly the
snippets the tool returned before it existed.
"""

from __future__ import annotations

from agent_runtime.capabilities.search.cache import WorkspacePageCache
from agent_runtime.capabilities.search.contracts import (
    EnrichedSource,
    ExtractedArticle,
    FetchedPage,
    PageCacheBudgets,
    PassageOrigin,
    SearchCandidate,
    SearchContentBudget,
    SearchFetchBudgets,
    SourceStatus,
    TransportResponse,
    WindowedPassage,
)
from agent_runtime.capabilities.search.enrichment import (
    SearchEnrichment,
    SearchEnrichmentEnvironment,
)
from agent_runtime.capabilities.search.extraction import ArticleExtractor
from agent_runtime.capabilities.search.fetching import (
    CLIENT_IDENTITY,
    FetchTargetPolicy,
    HttpxPageTransport,
    PageFetcher,
    PageTransportPort,
    RobotsPolicy,
)
from agent_runtime.capabilities.search.windowing import (
    FoldedText,
    SnippetLocator,
    SnippetWindow,
)

__all__ = (
    "CLIENT_IDENTITY",
    "ArticleExtractor",
    "EnrichedSource",
    "ExtractedArticle",
    "FetchTargetPolicy",
    "FetchedPage",
    "FoldedText",
    "HttpxPageTransport",
    "PageCacheBudgets",
    "PageFetcher",
    "PageTransportPort",
    "PassageOrigin",
    "RobotsPolicy",
    "SearchCandidate",
    "SearchContentBudget",
    "SearchEnrichment",
    "SearchEnrichmentEnvironment",
    "SearchFetchBudgets",
    "SnippetLocator",
    "SnippetWindow",
    "SourceStatus",
    "TransportResponse",
    "WindowedPassage",
    "WorkspacePageCache",
)
