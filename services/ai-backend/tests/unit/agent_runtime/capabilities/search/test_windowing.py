from __future__ import annotations

from agent_runtime.capabilities.search.contracts import PassageOrigin
from agent_runtime.capabilities.search.windowing import (
    FoldedText,
    SnippetLocator,
    SnippetWindow,
)


class ArticleMixin:
    """One synthetic article whose answer sits well below the fold."""

    ANSWER = (
        "The amendment took effect on 1 April 2026, and the old wording read "
        '"reasonable endeavours" before it was replaced.'
    )
    NAVIGATION = "Skip to content. Home About Contact. "
    FILLER = "An unrelated paragraph about something else entirely. "

    def article(self, *, lead_repeats: int = 8, tail_repeats: int = 8) -> str:
        return (
            self.NAVIGATION
            + self.FILLER * lead_repeats
            + self.ANSWER
            + " "
            + self.FILLER * tail_repeats
        )

    @staticmethod
    def window() -> SnippetWindow:
        return SnippetWindow()


class TestFoldedText:
    def test_fold_collapses_whitespace_and_lowercases(self) -> None:
        folded = FoldedText.fold("  The\n\n  Quick   Brown  ")

        assert folded.text == "the quick brown "

    def test_fold_normalises_quotes_and_dashes(self) -> None:
        folded = FoldedText.fold("“best endeavours” — it’s changed")

        assert folded.text == '"best endeavours" - it\'s changed'

    def test_offsets_map_a_folded_span_back_to_the_source(self) -> None:
        source = "Hello   there,   “World”."
        folded = FoldedText.fold(source)
        start = folded.text.index('"world"')

        span = folded.source_span(start, len('"world"'))

        assert source[span[0] : span[1]] == "“World”"

    def test_offsets_stay_aligned_for_multi_character_lowercase(self) -> None:
        # "İ".lower() is two characters; taking it would shift every later
        # offset by one and cut the window in the wrong place.
        source = "İstanbul rules apply here"
        folded = FoldedText.fold(source)
        start = folded.text.index("stanbul")

        span = folded.source_span(start, len("stanbul"))

        assert source[span[0] : span[1]] == "stanbul"


class TestSnippetLocator(ArticleMixin):
    def test_locates_an_exact_snippet(self) -> None:
        article = FoldedText.fold(self.article())
        snippet = FoldedText.fold(self.ANSWER)

        located = SnippetLocator().locate(
            folded_article=article.text, folded_snippet=snippet.text
        )

        assert located is not None
        start, length = located
        assert article.text[start : start + length] == snippet.text

    def test_locates_an_ellipsis_joined_snippet_by_its_longest_fragment(self) -> None:
        article = FoldedText.fold(self.article())
        snippet = FoldedText.fold(
            "Something the engine invented … the old wording read "
            '"reasonable endeavours" before it was replaced.'
        )

        located = SnippetLocator().locate(
            folded_article=article.text, folded_snippet=snippet.text
        )

        assert located is not None

    def test_locates_an_engine_truncated_snippet_by_word_prefix(self) -> None:
        article = FoldedText.fold(self.article())
        # The engine kept the head of the sentence and appended words that are
        # nowhere on the page, so only a prefix probe can match.
        snippet = FoldedText.fold(
            "The amendment took effect on 1 April 2026, and the old wording "
            "read something the page never says at all"
        )

        located = SnippetLocator().locate(
            folded_article=article.text, folded_snippet=snippet.text
        )

        assert located is not None

    def test_returns_none_for_an_unrelated_snippet(self) -> None:
        article = FoldedText.fold(self.article())
        snippet = FoldedText.fold("A completely different page about penguins")

        assert (
            SnippetLocator().locate(
                folded_article=article.text, folded_snippet=snippet.text
            )
            is None
        )

    def test_returns_none_for_an_empty_snippet(self) -> None:
        assert (
            SnippetLocator().locate(folded_article="anything", folded_snippet="   ")
            is None
        )


class TestSnippetWindow(ArticleMixin):
    def test_window_contains_the_answer_the_snippet_pointed_at(self) -> None:
        passage = self.window().build(
            article=self.article(),
            snippet=self.ANSWER,
            window_chars=400,
            lead_chars=200,
        )

        assert passage.origin is PassageOrigin.WINDOW
        assert self.ANSWER in passage.text

    def test_window_honours_its_character_budget(self) -> None:
        passage = self.window().build(
            article=self.article(lead_repeats=40, tail_repeats=40),
            snippet=self.ANSWER,
            window_chars=300,
            lead_chars=200,
        )

        assert len(passage.text) <= 300

    def test_window_marks_both_cut_edges(self) -> None:
        passage = self.window().build(
            article=self.article(lead_repeats=40, tail_repeats=40),
            snippet=self.ANSWER,
            window_chars=300,
            lead_chars=200,
        )

        assert passage.text.startswith(SnippetWindow.Values.TRUNCATION_MARK)
        assert passage.text.endswith(SnippetWindow.Values.TRUNCATION_MARK)

    def test_window_preserves_the_source_punctuation_and_case(self) -> None:
        # The fold is only a search index. A window cut out of the folded text
        # would show the model de-quoted, lowercased prose.
        passage = self.window().build(
            article=self.article(),
            snippet="the amendment took effect on 1 april 2026",
            window_chars=400,
            lead_chars=200,
        )

        assert '"reasonable endeavours"' in passage.text

    def test_unlocatable_snippet_falls_back_to_the_article_lead(self) -> None:
        passage = self.window().build(
            article=self.article(),
            snippet="nothing on this page says any of these words",
            window_chars=400,
            lead_chars=180,
        )

        assert passage.origin is PassageOrigin.LEAD
        assert passage.text.startswith(self.NAVIGATION.strip()[:10])
        assert len(passage.text) <= 180

    def test_lead_of_a_short_article_is_not_marked_as_truncated(self) -> None:
        passage = self.window().build(
            article="A short page with no filler.",
            snippet="unrelated",
            window_chars=400,
            lead_chars=400,
        )

        assert passage.origin is PassageOrigin.LEAD
        assert passage.text == "A short page with no filler."

    def test_window_at_the_end_of_the_article_still_fills_its_budget(self) -> None:
        article = self.NAVIGATION + self.FILLER * 20 + self.ANSWER

        passage = self.window().build(
            article=article, snippet=self.ANSWER, window_chars=300, lead_chars=200
        )

        assert passage.origin is PassageOrigin.WINDOW
        assert self.ANSWER in passage.text
        assert len(passage.text) > 250
        assert not passage.text.endswith(SnippetWindow.Values.TRUNCATION_MARK)
