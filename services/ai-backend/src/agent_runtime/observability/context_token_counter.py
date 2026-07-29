"""Segment-level token counting for the Context Occupancy Ledger (PRD-04).

The ledger decomposes one provider request into segments (§4.5 of the solution
design) and needs a token count *per segment*. Two constraints shape this module
and nothing else about it is interesting:

**It must never raise.** Occupancy is best-effort observability measured on the
model-call hot path (§6.4). A tokenizer that blows up on a pathological string
must degrade to a worse number, never take the run down. Every tier here is
wrapped, and the last tier is pure arithmetic over ``len(str)`` that has no
failure mode at all.

**It must not cost O(segments) tokenizer calls per model call.** A naive
implementation would tokenize the same 650-token ``publish_artifact`` description
on every single call of every run. §3.4 avoids that by exploiting digests the
system already computes — ``content_digest`` for system fragments,
``tool_schema_revision`` for the tool block — so identical bytes are counted once
per process and then served from a bounded LRU. Steady-state cost is therefore
proportional to *new* content, not to total context.

The counting chain itself is the one already used by the pre-run budget
preflight (``runtime_worker/handlers/run.py``): ``litellm.token_counter`` →
char/4 heuristic → last-resort char/4. This module reuses
:class:`~agent_runtime.budgets.token_counter.TokenCounterPort` rather than
inventing a parallel protocol, so a single fake satisfies both call sites in
tests and a future counter implementation lands in one place.

**Known bias, deliberately not corrected.** ``TokenCounterPort`` counts a
*message list*, so a segment is wrapped in one synthetic message and inherits
that provider's per-message envelope (roughly 3–4 tokens). Summed across ~30
segments that biases ``estimated_input_tokens`` slightly high, which shows up as
a small negative ``unattributed_delta``. That is the correct outcome: §3.3
forbids scaling segments to match the provider total, and the delta field exists
precisely so drift of this kind stays visible instead of being smeared away.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from enum import StrEnum
from threading import RLock
from typing import ClassVar, Final

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.budgets.token_counter import (
    CharHeuristicTokenCounter,
    LitellmTokenCounter,
    TokenCounterPort,
)
from agent_runtime.execution.contracts import RuntimeContract


class TokenCounterSource(StrEnum):
    """Which tier of the fallback chain produced a segment's token count.

    Persisted on every :class:`ContextSegment` so a reader can tell an
    authoritative tokenizer count from a degraded approximation rather than
    treating all estimates as equally trustworthy. ``PROXY`` on a segment is the
    §6.4 fail-open signature — something in the counting chain misbehaved and the
    ledger chose a worse number over a failed run.
    """

    TOKENIZER = "tokenizer"
    HEURISTIC = "heuristic"
    PROXY = "proxy"


class DigestTokenCacheKey(RuntimeContract):
    """Identity of a memoized count: the bytes, and the model that counted them.

    ``model`` is part of the key because tokenizers genuinely disagree — the same
    tool description is a different number of tokens under o200k_base than under
    Anthropic's tokenizer, and collapsing them would silently mis-attribute
    occupancy on any deployment that routes more than one model.

    ``digest`` is supplied by the caller, not computed here. That is the point of
    §3.4: system fragments already carry ``content_digest`` and the tool block
    already carries ``tool_schema_revision``, so the ledger reuses a hash the
    system paid for rather than re-hashing every segment on every call.
    """

    model: str = Field(min_length=1, max_length=200)
    digest: str = Field(min_length=1, max_length=200)


class DigestTokenCount(RuntimeContract):
    """A memoized count together with the tier that produced it.

    The tier is cached alongside the number so a cache hit reports the same
    ``counter_source`` the original miss did. Caching the count but recomputing
    (or defaulting) the source would make ``PROXY`` disappear from every segment
    after the first, hiding exactly the degradation §6.4 wants surfaced.
    """

    estimated_tokens: NonNegativeInt
    counter_source: TokenCounterSource


class DigestTokenCacheStats(RuntimeContract):
    """Counters for one cache instance, for tests and future metering."""

    hits: NonNegativeInt = 0
    misses: NonNegativeInt = 0
    evictions: NonNegativeInt = 0
    current_size: NonNegativeInt = 0


class DigestTokenCache:
    """Bounded, lock-guarded LRU of ``(model, digest) → count``.

    Process-wide by default via :meth:`shared`, because the whole value of §3.4
    is that the second run in a process does not re-tokenize the first run's
    resident prompt surface. Tests take an instance of their own, or call
    :meth:`clear` on the shared one — the cache is deliberately never implicit
    state that a test cannot reach.

    Guarded by :class:`threading.RLock` rather than :class:`asyncio.Lock`: the
    counting API is synchronous CPU work invoked from async middleware, and a
    worker may also drive it from a thread executor. The lock covers only the
    ``OrderedDict`` mutations — never the tokenizer call — so one slow count
    cannot stall every other segment in the process.
    """

    DEFAULT_MAX_ENTRIES: Final[int] = 2048

    _shared_instance: ClassVar[DigestTokenCache | None] = None
    _shared_guard: ClassVar[RLock] = RLock()

    def __init__(self, *, max_entries: PositiveInt = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries <= 0:
            msg = "max_entries must be positive"
            raise ValueError(msg)
        self._max_entries = int(max_entries)
        # key → count. ``move_to_end`` on read makes eviction pick the genuinely
        # least-recently-used entry, so a long-lived resident prompt surface is
        # not evicted by a burst of one-shot message digests.
        self._entries: OrderedDict[DigestTokenCacheKey, DigestTokenCount] = (
            OrderedDict()
        )
        self._lock = RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @classmethod
    def shared(cls) -> DigestTokenCache:
        """Return the process-wide cache, constructing it on first use."""

        with cls._shared_guard:
            if cls._shared_instance is None:
                cls._shared_instance = cls()
            return cls._shared_instance

    @classmethod
    def reset_shared(cls) -> None:
        """Drop the process-wide cache entirely. Test seam only."""

        with cls._shared_guard:
            cls._shared_instance = None

    def get(self, key: DigestTokenCacheKey) -> DigestTokenCount | None:
        """Return the memoized count for ``key``, marking it most-recently-used."""

        with self._lock:
            cached = self._entries.get(key)
            if cached is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return cached

    def put(self, key: DigestTokenCacheKey, value: DigestTokenCount) -> None:
        """Store ``value`` under ``key``, evicting the LRU entry when full."""

        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        """Empty the cache and its counters. Test seam and future admin hook."""

        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> DigestTokenCacheStats:
        """Return a point-in-time snapshot of this cache's counters."""

        with self._lock:
            return DigestTokenCacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                current_size=len(self._entries),
            )


class ContextTokenCounter:
    """Count one segment's tokens through a total, never-raising fallback chain.

    Tiers, in order, each used only when the previous yields no usable count:

    1. :class:`LitellmTokenCounter` — the provider's real tokenizer where litellm
       bundles one (openai / anthropic / gemini), an offline tiktoken
       approximation otherwise. Reported as ``TOKENIZER``.
    2. :class:`CharHeuristicTokenCounter` — ``len // 4`` over the same text.
       Reported as ``HEURISTIC``.
    3. Inline ``len(text) // 4`` computed here. Reported as ``PROXY``, which is
       the §6.4 marker that the injected chain failed rather than merely missed.

    Tier 3 exists separately from tier 2 even though the arithmetic matches: tier
    2 is an injected port that a caller may replace (and that may therefore raise
    or return ``None``), while tier 3 is unconditional arithmetic that cannot.
    Keeping them distinct is what lets the snapshot distinguish "we deliberately
    used the heuristic" from "everything above this broke".

    Both ports are constructed lazily-by-default here rather than required of the
    caller, so the middleware in PRD-05 can build a counter with no wiring while
    tests inject deterministic fakes.
    """

    _CHARS_PER_TOKEN: Final[int] = 4

    class _MessageKeys:
        """Message-dict keys, single-sourced for the synthetic wrapper message."""

        ROLE: Final[str] = "role"
        CONTENT: Final[str] = "content"

    _SYNTHETIC_ROLE: Final[str] = "user"

    def __init__(
        self,
        *,
        tokenizer: TokenCounterPort | None = None,
        heuristic: TokenCounterPort | None = None,
        cache: DigestTokenCache | None = None,
    ) -> None:
        self._tokenizer: TokenCounterPort = tokenizer or LitellmTokenCounter()
        self._heuristic: TokenCounterPort = heuristic or CharHeuristicTokenCounter()
        self._cache: DigestTokenCache = cache or DigestTokenCache.shared()

    @property
    def cache(self) -> DigestTokenCache:
        """The memoization cache this counter reads and writes."""

        return self._cache

    def count(self, text: str, *, model: str) -> tuple[int, TokenCounterSource]:
        """Count ``text`` under ``model``; never raises, always returns a count.

        Empty (or non-``str``) input short-circuits to ``(0, HEURISTIC)`` rather
        than paying a tokenizer call to learn that an empty segment costs the
        message envelope. A zero-byte contributor must report zero tokens, or
        every optional segment would look like it charges rent.
        """

        if not isinstance(text, str) or not text:
            return 0, TokenCounterSource.HEURISTIC

        messages = self._as_messages(text)
        counted = self._safe_count(self._tokenizer, model=model, messages=messages)
        if counted is not None:
            return counted, TokenCounterSource.TOKENIZER

        heuristic = self._safe_count(self._heuristic, model=model, messages=messages)
        if heuristic is not None:
            return heuristic, TokenCounterSource.HEURISTIC

        # Last resort. Pure arithmetic over a ``str`` that we have already
        # type-checked — there is no exception path left below this line, which
        # is what makes the "never raises" contract literally true.
        return len(text) // self._CHARS_PER_TOKEN, TokenCounterSource.PROXY

    def count_digested(
        self,
        text: str,
        *,
        model: str,
        digest: str,
    ) -> tuple[int, TokenCounterSource]:
        """Memoized :meth:`count`, keyed on ``(model, digest)`` per §3.4.

        ``digest`` is trusted to identify ``text`` — callers pass a hash the
        system already computed over exactly these bytes (``content_digest``,
        ``tool_schema_revision``). A caller that passes a digest not derived from
        ``text`` gets a wrong-but-harmless count; it cannot corrupt anything
        beyond its own occupancy row, and the alternative (re-hashing every
        segment on every model call) is the cost §3.4 exists to avoid.

        A blank digest degrades to an uncached :meth:`count` rather than keying
        every distinct segment under the same empty string.

        The tokenizer runs *outside* the cache lock, so two threads racing a cold
        key may both compute. That is deliberate: duplicate work on a cold key is
        cheaper than serialising every counting caller in the process behind one
        slow tokenizer call, and the results are identical.
        """

        if not digest:
            return self.count(text, model=model)

        key = DigestTokenCacheKey(model=model, digest=digest)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.estimated_tokens, cached.counter_source

        tokens, source = self.count(text, model=model)
        self._cache.put(
            key,
            DigestTokenCount(estimated_tokens=tokens, counter_source=source),
        )
        return tokens, source

    def _as_messages(self, text: str) -> Sequence[Mapping[str, str]]:
        """Wrap raw segment text in the single message shape the port expects."""

        return (
            {
                self._MessageKeys.ROLE: self._SYNTHETIC_ROLE,
                self._MessageKeys.CONTENT: text,
            },
        )

    @staticmethod
    def _safe_count(
        counter: TokenCounterPort,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
    ) -> int | None:
        """Call a counter, absorbing any failure into ``None`` so the chain continues.

        Both a raised exception and a nonsensical return (``None``, negative, a
        non-``int`` from a misbehaving fake or a future port implementation) are
        treated identically as "this tier has no answer". Zero is a *valid*
        answer and is kept — an empty-after-normalization segment legitimately
        costs nothing.
        """

        try:
            counted = counter.count(model=model, messages=messages)
        except Exception:  # noqa: BLE001 — the chain is the error handling
            return None
        if not isinstance(counted, int) or isinstance(counted, bool) or counted < 0:
            return None
        return counted


__all__ = (
    "ContextTokenCounter",
    "DigestTokenCache",
    "DigestTokenCacheKey",
    "DigestTokenCacheStats",
    "DigestTokenCount",
    "TokenCounterSource",
)
