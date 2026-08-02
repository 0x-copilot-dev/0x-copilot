from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agent_runtime.capabilities.search.contracts import SourceStatus
from agent_runtime.capabilities.search.extraction import ArticleExtractor


class FakeExtractorModuleMixin:
    """Install a stand-in for ``trafilatura`` under a name nothing else imports."""

    MODULE_NAME = "tests_fake_trafilatura"
    URL = "https://example.test/article"
    HTML = "<html><body><p>anything</p></body></html>"
    ARTICLE = "Real article body. " * 40

    def _install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        extract: object,
    ) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = []
        module = ModuleType(self.MODULE_NAME)

        def _extract(html: str, **kwargs: object) -> object:
            calls.append({"html": html, **kwargs})
            return extract(html, **kwargs) if callable(extract) else extract

        module.extract = _extract  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, self.MODULE_NAME, module)
        return calls

    def _extractor(self, **kwargs: object) -> ArticleExtractor:
        return ArticleExtractor(module_name=self.MODULE_NAME, **kwargs)  # type: ignore[arg-type]


class TestArticleExtractorAvailability(FakeExtractorModuleMixin):
    def test_missing_module_reports_unavailable_rather_than_raising(self) -> None:
        # The dependency is not pinned yet. Until it is, every search has to
        # keep working — the absence is a rung of the fallback chain, not a bug.
        extractor = ArticleExtractor(module_name="tests_module_that_does_not_exist")

        result = extractor.extract(html=self.HTML, url=self.URL)

        assert extractor.available is False
        assert result.failure is SourceStatus.EXTRACTOR_UNAVAILABLE
        assert result.text == ""

    def test_import_is_attempted_only_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[str] = []

        def _import(name: str) -> ModuleType:
            attempts.append(name)
            raise ImportError(name)

        monkeypatch.setattr(
            "agent_runtime.capabilities.search.extraction.importlib.import_module",
            _import,
        )
        extractor = ArticleExtractor(module_name="tests_module_that_does_not_exist")

        extractor.extract(html=self.HTML, url=self.URL)
        extractor.extract(html=self.HTML, url=self.URL)

        assert len(attempts) == 1

    def test_present_module_reports_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install(monkeypatch, self.ARTICLE)

        assert self._extractor().available is True


class TestArticleExtractorOutcomes(FakeExtractorModuleMixin):
    def test_extracts_the_article_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._install(monkeypatch, self.ARTICLE)

        result = self._extractor().extract(html=self.HTML, url=self.URL)

        assert result.failure is None
        assert result.text == self.ARTICLE
        assert result.usable is True
        assert calls == [
            {
                "html": self.HTML,
                "url": self.URL,
                **ArticleExtractor.Values.EXTRACT_KWARGS,
            }
        ]

    def test_extractor_exception_becomes_a_typed_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(_html: str, **_kwargs: object) -> str:
            raise RuntimeError("malformed markup")

        self._install(monkeypatch, _explode)

        result = self._extractor().extract(html=self.HTML, url=self.URL)

        assert result.failure is SourceStatus.EXTRACTION_FAILED
        assert result.usable is False

    def test_no_main_content_becomes_extraction_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install(monkeypatch, None)

        result = self._extractor().extract(html=self.HTML, url=self.URL)

        assert result.failure is SourceStatus.EXTRACTION_EMPTY

    def test_a_nav_bar_sized_result_is_not_treated_as_an_article(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Extraction "succeeding" with a cookie banner is worse than the engine
        # snippet, so it degrades to the same fallback as a failure.
        self._install(monkeypatch, "Accept cookies. Home. About.")

        result = self._extractor().extract(html=self.HTML, url=self.URL)

        assert result.failure is SourceStatus.EXTRACTION_EMPTY

    def test_minimum_article_length_is_injectable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._install(monkeypatch, "Short but wanted.")

        result = self._extractor(min_article_chars=5).extract(
            html=self.HTML, url=self.URL
        )

        assert result.failure is None
        assert result.text == "Short but wanted."
