"""Main-content extraction, with absence of the extractor as a normal outcome.

``trafilatura`` is the extractor the PRD names, and it is **not yet a pinned
dependency of this service**. That is not a temporary embarrassment to be
papered over with a hard import: the whole pipeline is built so that a missing
extractor is one more rung of the fallback chain, indistinguishable from a page
that would not parse. Until the dependency lands, every call here reports
``EXTRACTOR_UNAVAILABLE`` and the search returns exactly the snippets it
returned before this module existed (PRD AC1).

The import is lazy for a second reason: a deployment that never fetches a page
must not pay for the extractor's import at process start.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

from agent_runtime.capabilities.search.contracts import ExtractedArticle, SourceStatus

_LOGGER = logging.getLogger(__name__)


class ArticleExtractor:
    """Extract an article's main text from raw HTML, or report why it could not."""

    class Values:
        MODULE = "trafilatura"
        #: Kept in one place because it is the one thing here that a library
        #: upgrade can break, and it cannot be exercised until the dependency
        #: is pinned. Any incompatibility surfaces as ``EXTRACTION_FAILED``
        #: (a snippet fallback), never as a broken search.
        EXTRACT_KWARGS = {"include_comments": False, "include_tables": True}
        #: Below this, "extraction" returned a nav bar or a cookie banner, and
        #: the engine snippet is the better answer.
        MIN_ARTICLE_CHARS = 200

    class Log:
        UNAVAILABLE = (
            "search.extractor_unavailable module=%s; "
            "web_search falls back to engine snippets"
        )
        FAILED = "search.extraction_failed url=%s"

    def __init__(
        self,
        *,
        module_name: str | None = None,
        min_article_chars: int | None = None,
    ) -> None:
        """Bind the extractor module name and the minimum useful article length."""

        self._module_name = module_name or self.Values.MODULE
        self._min_article_chars = (
            min_article_chars
            if min_article_chars is not None
            else self.Values.MIN_ARTICLE_CHARS
        )
        self._module: ModuleType | None = None
        self._resolved = False

    @property
    def available(self) -> bool:
        """Return whether the extractor module can be imported at all."""

        return self._resolve() is not None

    def extract(self, *, html: str, url: str) -> ExtractedArticle:
        """Return the article text for ``html``, or the reason there is none."""

        module = self._resolve()
        if module is None:
            return ExtractedArticle(url=url, failure=SourceStatus.EXTRACTOR_UNAVAILABLE)
        try:
            text = module.extract(html, url=url, **self.Values.EXTRACT_KWARGS)
        except Exception:  # noqa: BLE001 - a bad page must not end the search
            _LOGGER.warning(self.Log.FAILED, url, exc_info=True)
            return ExtractedArticle(url=url, failure=SourceStatus.EXTRACTION_FAILED)
        if not isinstance(text, str) or len(text.strip()) < self._min_article_chars:
            return ExtractedArticle(url=url, failure=SourceStatus.EXTRACTION_EMPTY)
        return ExtractedArticle(url=url, text=text)

    def _resolve(self) -> ModuleType | None:
        """Import the extractor once per instance; ``None`` when it is absent."""

        if self._resolved:
            return self._module
        self._resolved = True
        try:
            self._module = importlib.import_module(self._module_name)
        except ImportError:
            _LOGGER.info(self.Log.UNAVAILABLE, self._module_name)
            self._module = None
        return self._module


__all__ = ("ArticleExtractor",)
