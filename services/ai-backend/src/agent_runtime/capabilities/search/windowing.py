"""Snippet-anchored windowing: cut the part of the article the engine matched.

This is the whole of the relevance stage, and it uses no model. DuckDuckGo has
already told us which part of the page matched the query — that is what a
snippet *is* — so the job is to locate that text inside the extracted article
and take a window around it, not to re-rank passages with an embedding model
(PRD §8 decision 2).

Locating is a fold-and-search, not an exact match, because an engine snippet is
rarely a byte-identical substring of the page: whitespace is collapsed, curly
quotes are straightened or not, and fragments are joined with an ellipsis. The
fold is therefore reversible — every folded character keeps the source offset it
came from — so a match found in folded coordinates cuts a window out of the
*original* text, with its punctuation and spacing intact.
"""

from __future__ import annotations

import re

from agent_runtime.capabilities.search.contracts import PassageOrigin, WindowedPassage


class FoldedText:
    """A comparison-form rendering of some text plus a map back to its offsets.

    ``offsets[i]`` is the index in the source string that produced ``text[i]``,
    which is what lets a match in folded coordinates become a cut in source
    coordinates. A fold that lost that map would force the window to be cut out
    of the folded text, and the model would be shown de-punctuated, case-flattened
    prose that no longer matches the page it cites.
    """

    class Values:
        SPACE = " "
        #: Characters folded 1:1 so a snippet that straightened its quotes (or
        #: kept them curly) still matches the page. Only same-length
        #: substitutions are allowed here: a 1:N fold would desynchronise the
        #: offset map and silently cut the window at the wrong place.
        SUBSTITUTIONS = {
            "’": "'",
            "‘": "'",
            "“": '"',
            "”": '"',
            "–": "-",
            "—": "-",
            " ": " ",
            "​": " ",
        }

    def __init__(self, text: str, offsets: tuple[int, ...]) -> None:
        self.text = text
        self.offsets = offsets

    @classmethod
    def fold(cls, source: str) -> "FoldedText":
        """Lowercase, collapse whitespace, and normalise quotes/dashes."""

        chars: list[str] = []
        offsets: list[int] = []
        previous_was_space = True
        for index, char in enumerate(source):
            mapped = cls.Values.SUBSTITUTIONS.get(char, char)
            if mapped.isspace():
                if previous_was_space:
                    continue
                chars.append(cls.Values.SPACE)
                offsets.append(index)
                previous_was_space = True
                continue
            lowered = mapped.lower()
            # Some codepoints lowercase to more than one character (e.g. "İ").
            # Taking the multi-character form would shift every later offset by
            # one and cut the window somewhere else entirely, so the original is
            # kept instead — the cost is one unmatched character, not a wrong cut.
            chars.append(lowered if len(lowered) == 1 else mapped)
            offsets.append(index)
            previous_was_space = False
        return cls("".join(chars), tuple(offsets))

    def source_span(self, start: int, length: int) -> tuple[int, int]:
        """Map a folded ``[start, start+length)`` span back to source offsets."""

        if not self.offsets or length <= 0:
            return (0, 0)
        first = self.offsets[start]
        last = self.offsets[min(start + length - 1, len(self.offsets) - 1)]
        return (first, last + 1)


class SnippetLocator:
    """Find where an engine snippet occurs inside an extracted article.

    The probe ladder is deliberately cheapest-first and stops at the first hit:
    the whole snippet, then its ellipsis-separated fragments longest-first, then
    shrinking word-prefixes of the longest fragment. The ladder exists because
    engines truncate and re-join snippets; a single exact-match attempt would
    send most real pages down the lead fallback for no reason.
    """

    class Values:
        #: Both spellings of the join an engine puts between snippet fragments.
        FRAGMENT_SEPARATOR = re.compile(r"…|\.\.\.")
        SPACE = " "
        #: Fewer words than this matches too much of a page to be meaningful.
        MIN_PROBE_WORDS = 4
        MAX_PROBE_WORDS = 12

    def locate(
        self, *, folded_article: str, folded_snippet: str
    ) -> tuple[int, int] | None:
        """Return ``(start, length)`` in folded coordinates, or ``None``."""

        for probe in self._probes(folded_snippet):
            found = folded_article.find(probe)
            if found >= 0:
                return (found, len(probe))
        return None

    def _probes(self, folded_snippet: str) -> tuple[str, ...]:
        """Return the ordered probe ladder for one folded snippet."""

        snippet = folded_snippet.strip()
        if not snippet:
            return ()
        probes: list[str] = [snippet]
        fragments = sorted(
            (
                fragment.strip()
                for fragment in self.Values.FRAGMENT_SEPARATOR.split(snippet)
            ),
            key=len,
            reverse=True,
        )
        probes.extend(
            fragment for fragment in fragments if fragment and fragment != snippet
        )
        longest = fragments[0] if fragments else snippet
        probes.extend(self._word_prefixes(longest))
        # Order is load-bearing (longest probe wins) so de-duplicate in place.
        seen: set[str] = set()
        return tuple(
            probe for probe in probes if not (probe in seen or seen.add(probe))
        )

    def _word_prefixes(self, fragment: str) -> tuple[str, ...]:
        """Return shrinking word-prefixes of ``fragment``, longest first."""

        words = [word for word in fragment.split(self.Values.SPACE) if word]
        longest = min(self.Values.MAX_PROBE_WORDS, len(words))
        return tuple(
            self.Values.SPACE.join(words[:count])
            for count in range(longest, self.Values.MIN_PROBE_WORDS - 1, -1)
        )


class SnippetWindow:
    """Cut the model-visible passage out of an extracted article.

    The fallback chain lives here, in one place, so every caller degrades the
    same way: located snippet → window around it; not located → article lead;
    no article → the engine snippet (which the caller supplies, because that rung
    needs no article at all).
    """

    class Values:
        #: Marks a cut edge so the model can tell a fragment from a full page.
        TRUNCATION_MARK = "…"
        #: How far to look for a word boundary before giving up and cutting mid-word.
        SNAP_SEARCH_CHARS = 160
        NEWLINE = "\n"

    def __init__(self, locator: SnippetLocator | None = None) -> None:
        self._locator = locator or SnippetLocator()

    def build(
        self,
        *,
        article: str,
        snippet: str,
        window_chars: int,
        lead_chars: int,
    ) -> WindowedPassage:
        """Return the window around ``snippet``, or the article lead."""

        folded_article = FoldedText.fold(article)
        located = self._locator.locate(
            folded_article=folded_article.text,
            folded_snippet=FoldedText.fold(snippet).text,
        )
        if located is None:
            return WindowedPassage(
                text=self._lead(article, lead_chars),
                origin=PassageOrigin.LEAD,
            )
        start, end = folded_article.source_span(*located)
        return WindowedPassage(
            text=self._around(article, start=start, end=end, window_chars=window_chars),
            origin=PassageOrigin.WINDOW,
        )

    def _lead(self, article: str, lead_chars: int) -> str:
        """Return the head of the article, capped and snapped to a word boundary."""

        return self._cut(article, start=0, end=self._body_chars(lead_chars))

    def _around(self, article: str, *, start: int, end: int, window_chars: int) -> str:
        """Return a window of ``window_chars`` centred on ``[start, end)``."""

        body = self._body_chars(window_chars)
        match_length = max(end - start, 0)
        padding = max((body - match_length) // 2, 0)
        cut_start = max(start - padding, 0)
        cut_end = min(cut_start + body, len(article))
        # Re-anchor when the match sits near the end of the article: without
        # this the window would be shorter than the budget allows for no reason.
        cut_start = max(min(cut_start, cut_end - body), 0)
        return self._cut(article, start=cut_start, end=cut_end)

    def _body_chars(self, width: int) -> int:
        """Reserve room for the truncation marks so the result honours ``width``."""

        return max(width - 2 * len(self.Values.TRUNCATION_MARK), 1)

    def _cut(self, article: str, *, start: int, end: int) -> str:
        """Slice ``article``, snapped to word boundaries and marked when cut."""

        end = min(end, len(article))
        snapped_start = self._snap_start(article, start)
        snapped_end = self._snap_end(article, end, floor=snapped_start)
        body = article[snapped_start:snapped_end].strip()
        prefix = self.Values.TRUNCATION_MARK if snapped_start > 0 else ""
        suffix = self.Values.TRUNCATION_MARK if snapped_end < len(article) else ""
        return f"{prefix}{body}{suffix}"

    def _snap_start(self, article: str, start: int) -> int:
        """Move ``start`` forward to the next word boundary, if one is near."""

        if start <= 0:
            return 0
        limit = min(start + self.Values.SNAP_SEARCH_CHARS, len(article))
        for index in range(start, limit):
            if article[index].isspace():
                return index + 1
        return start

    def _snap_end(self, article: str, end: int, *, floor: int) -> int:
        """Move ``end`` back to the previous word boundary, if one is near."""

        if end >= len(article):
            return len(article)
        limit = max(end - self.Values.SNAP_SEARCH_CHARS, floor)
        for index in range(end, limit, -1):
            if article[index - 1].isspace():
                return index - 1
        return end


__all__ = ("FoldedText", "SnippetLocator", "SnippetWindow")
