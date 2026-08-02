"""Typed contracts for the local page extraction that runs inside ``web_search``.

Every stage of the pipeline reports its own failure as a value rather than an
exception, because the pipeline's defining property (AC1 in
``docs/plan/local-search-extraction/PRD.md``) is that a failure of the new path
is a *fallback*, never an error: whatever breaks, the tool still returns the
DuckDuckGo snippets it returned before extraction existed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt


class PassageOrigin(StrEnum):
    """Which rung of the fallback chain produced a result's model-visible text."""

    #: The engine's snippet was located in the extracted article and a window
    #: was cut around it — the common case, and the reason the feature exists.
    WINDOW = "window"
    #: The snippet could not be located (the engine rewrote or truncated it), so
    #: the article's lead is returned instead, capped to the same width.
    LEAD = "lead"
    #: Nothing was extracted; this is exactly what the tool returned before.
    SNIPPET = "snippet"


class SourceStatus(StrEnum):
    """Why a source did or did not yield extracted article text."""

    EXTRACTED = "extracted"
    CACHED = "cached"
    UNSUPPORTED_URL = "unsupported_url"
    ROBOTS_DISALLOWED = "robots_disallowed"
    FETCH_FAILED = "fetch_failed"
    FETCH_TIMEOUT = "fetch_timeout"
    NOT_HTML = "not_html"
    EXTRACTOR_UNAVAILABLE = "extractor_unavailable"
    EXTRACTION_FAILED = "extraction_failed"
    EXTRACTION_EMPTY = "extraction_empty"
    BUDGET_EXHAUSTED = "budget_exhausted"


class SearchCandidate(BaseModel):
    """One DuckDuckGo text result, in the keys this tool has always returned.

    ``RawKeys`` and ``ResultKeys`` are the two spellings of the same three
    fields — the engine's (``body`` / ``href``) and ours (``snippet`` /
    ``link``). They were previously written as inline literals at the one call
    site; they are named here because the fallback path and the enriched path
    must produce the *same* keys or AC1 is not a fallback but a second shape.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    link: str = ""
    snippet: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    class RawKeys:
        TITLE = "title"
        LINK = "href"
        SNIPPET = "body"

    class ResultKeys:
        TITLE = "title"
        LINK = "link"
        SNIPPET = "snippet"

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any]) -> "SearchCandidate":
        """Read one engine result, coercing every absent/odd field to ``""``."""

        return cls(
            title=cls._text(raw.get(cls.RawKeys.TITLE)),
            link=cls._text(raw.get(cls.RawKeys.LINK)),
            snippet=cls._text(raw.get(cls.RawKeys.SNIPPET)),
            raw=dict(raw),
        )

    @staticmethod
    def _text(value: object) -> str:
        """Return ``value`` when it is a string, else ``""`` (engine output is untrusted)."""

        return value if isinstance(value, str) else ""

    def as_result(self, passage: str | None = None) -> dict[str, str]:
        """Render the model-visible row, optionally replacing the snippet text.

        Key order matches the pre-extraction implementation because the model
        sees this dict serialized, and a reordered dict is a different prompt.
        """

        return {
            self.ResultKeys.SNIPPET: self.snippet if passage is None else passage,
            self.ResultKeys.TITLE: self.title,
            self.ResultKeys.LINK: self.link,
        }


class TransportResponse(BaseModel):
    """What a page transport returns: a status, a content type, and bounded text."""

    model_config = ConfigDict(extra="forbid")

    status_code: int
    content_type: str = ""
    text: str = ""
    #: ``True`` when the body hit ``max_page_bytes`` and the tail was dropped.
    truncated: bool = False


class FetchedPage(BaseModel):
    """One fetch attempt. ``failure`` is ``None`` only when ``html`` is usable."""

    model_config = ConfigDict(extra="forbid")

    url: str
    html: str = ""
    failure: SourceStatus | None = None


class ExtractedArticle(BaseModel):
    """One extraction attempt. ``failure`` is ``None`` only when ``text`` is usable."""

    model_config = ConfigDict(extra="forbid")

    url: str
    text: str = ""
    failure: SourceStatus | None = None
    #: ``True`` when the text came from the on-disk cache rather than a fetch.
    cached: bool = False

    @property
    def usable(self) -> bool:
        """Return whether this article can be windowed."""

        return self.failure is None and bool(self.text)


class WindowedPassage(BaseModel):
    """The model-visible text chosen for one source, and which rung produced it."""

    model_config = ConfigDict(extra="forbid")

    text: str
    origin: PassageOrigin


class EnrichedSource(BaseModel):
    """One search result after the fallback chain has chosen its passage."""

    model_config = ConfigDict(extra="forbid")

    candidate: SearchCandidate
    passage: WindowedPassage
    status: SourceStatus
    article_text: str = ""

    class ArtifactKeys:
        EXTRACTED_TEXT = "extracted_text"
        EXTRACTION_STATUS = "extraction_status"
        PASSAGE_ORIGIN = "passage_origin"

    def content_row(self) -> dict[str, str]:
        """Render the row that reaches ``ToolMessage.content`` (prompt tokens)."""

        return self.candidate.as_result(self.passage.text)

    def artifact_row(self) -> dict[str, Any]:
        """Render the row that reaches ``ToolMessage.artifact`` (never the prompt).

        The engine's raw result is carried through unchanged and the full
        extracted article is added beside it. This is where the artifact stops
        being the rename-duplicate of ``content`` described in PRD §1a: it now
        holds material ``content`` deliberately does not — the rest of the page.
        """

        return {
            **self.candidate.raw,
            self.ArtifactKeys.EXTRACTED_TEXT: self.article_text,
            self.ArtifactKeys.EXTRACTION_STATUS: self.status.value,
            self.ArtifactKeys.PASSAGE_ORIGIN: self.passage.origin.value,
        }


class SearchFetchBudgets(BaseModel):
    """Latency bounds for the fetch stage (PRD AC6).

    Every value is a constructor argument with a default rather than a settings
    read: the hyperparameter surface is owned elsewhere, and a tunable that has
    no home yet is better as an injectable default than as a second config
    vocabulary.
    """

    model_config = ConfigDict(extra="forbid")

    #: Hard per-fetch deadline. A page slower than this is abandoned and falls
    #: back to its snippet rather than stalling the turn.
    per_fetch_timeout_seconds: PositiveFloat = 5.0
    #: Whole-search wall clock. Fetches still running when it expires are
    #: abandoned; their sources fall back to their snippets.
    wall_clock_seconds: PositiveFloat = 12.0
    #: Politeness + latency cap: at most this many origins are fetched at once.
    max_parallel_fetches: PositiveInt = 4
    #: Body cap. A page larger than this is truncated, not streamed to disk.
    max_page_bytes: PositiveInt = 2_000_000


class SearchContentBudget(BaseModel):
    """Token bound on what reaches ``ToolMessage.content`` (PRD AC2).

    Enforced in characters against a fixed chars-per-token ratio. The ratio is
    the tokenizer-free approximation the runtime already uses for its
    second-tier estimate (``budgets/token_counter.py``); an exact tokenizer here
    would cost a model-specific dependency on the tool path to refine a bound
    that only has to hold, not be tight.

    The bound covers the *whole* row — title and link as well as the passage —
    because all three reach the prompt.
    """

    model_config = ConfigDict(extra="forbid")

    content_token_budget: PositiveInt = 1_200
    chars_per_token: PositiveInt = 4
    #: Upper bound on one window, applied even when the budget would allow more.
    window_chars: PositiveInt = 1_200
    #: Upper bound on an article lead when the snippet could not be located.
    lead_chars: PositiveInt = 900
    #: Below this width a window cannot reliably contain the answer, so the
    #: source is left on its snippet instead of spending tokens on a stub.
    min_window_chars: PositiveInt = 200

    def total_chars(self) -> int:
        """Return the character budget for the whole ``content`` payload."""

        return self.content_token_budget * self.chars_per_token

    def estimated_tokens(self, rows: Sequence[Mapping[str, str]]) -> int:
        """Return the estimated prompt-token cost of rendered content rows."""

        return self.row_chars(rows) // self.chars_per_token

    @staticmethod
    def row_chars(rows: Sequence[Mapping[str, str]]) -> int:
        """Return the total character count across every field of every row."""

        return sum(len(value) for row in rows for value in row.values())

    def window_chars_for(self, *, remaining_chars: int, source_count: int) -> int:
        """Split ``remaining_chars`` evenly and clamp to the per-window ceiling.

        Returns ``0`` when the share is too small to be worth spending, which
        the caller reads as "leave these sources on their snippets".
        """

        if source_count <= 0:
            return 0
        share = min(self.window_chars, remaining_chars // source_count)
        return share if share >= self.min_window_chars else 0


class PageCacheBudgets(BaseModel):
    """Freshness and size bounds for the on-disk page cache (PRD §8 decision 3).

    An unbounded on-disk cache on a user's laptop is a bug, not a feature, so
    the size bound is part of the contract rather than an operational concern.
    """

    model_config = ConfigDict(extra="forbid")

    #: Short enough that news stays fresh; long enough that the common case —
    #: a follow-up question about the page just read — is a hit.
    ttl_seconds: PositiveInt = 86_400
    max_total_bytes: PositiveInt = 64 * 1024 * 1024


__all__ = (
    "EnrichedSource",
    "ExtractedArticle",
    "FetchedPage",
    "PageCacheBudgets",
    "PassageOrigin",
    "SearchCandidate",
    "SearchContentBudget",
    "SearchFetchBudgets",
    "SourceStatus",
    "TransportResponse",
    "WindowedPassage",
)
