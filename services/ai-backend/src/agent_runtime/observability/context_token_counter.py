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

**Known bias, deliberately not corrected — and larger than "slightly".**
``TokenCounterPort`` counts a *message list*, so a segment is wrapped in one
synthetic message and inherits litellm's per-message envelope. Measured against
the installed litellm, that envelope is **7 tokens per segment**, and a request
shaped like the design's own reference measurements (11 system fragments, 30
tool schemas, 40 message parts = 81 segments) therefore over-counts by **610
tokens, +5.9%**, against counting the identical text once. Two consequences a
reader has to know:

1. ``unattributed_delta`` is expected *negative* in steady state, and its
   magnitude scales with **segment count**, not with drift. A call that splits
   into more segments has a larger residual for no other reason.
2. That structural bias alone exceeds the ±5% reconciliation tolerance the
   design's §9 test plan proposes, so the tolerance cannot be read as a
   statement about tokenizer accuracy until the envelope is netted out.

It is left uncorrected because §3.3 forbids scaling segments toward the provider
total and because the correction would change what every injected counter fake
means. ``test_context_token_counter`` pins the measured bias so it cannot grow
silently, which is the honest middle ground: a known, bounded, checked artifact
beats an unknown one.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from enum import StrEnum
import logging
from threading import RLock
from typing import ClassVar, Final

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.budgets.token_counter import (
    CharHeuristicTokenCounter,
    LitellmTokenCounter,
    TokenCounterPort,
)
from agent_runtime.execution.contracts import RuntimeContract


_LOGGER = logging.getLogger(__name__)


class TokenCounterSource(StrEnum):
    """Which tier of the fallback chain produced a segment's token count.

    Persisted on every :class:`ContextSegment` so a reader can tell a real BPE
    count from a character-division guess rather than treating all estimates as
    equally trustworthy. ``PROXY`` on a segment is the §6.4 fail-open signature —
    something in the counting chain misbehaved and the ledger chose a worse
    number over a failed run.

    **``TOKENIZER`` does not mean "the provider's own tokenizer".** It means the
    count came through ``litellm.token_counter``, which this service calls under
    :func:`~agent_runtime.pricing.litellm_runtime.apply_offline_litellm_config`
    so counting is deterministic and network-free. That guardrail disables the
    HuggingFace tokenizer downloads, and the measured consequence is that
    ``gpt-4o-mini``, ``claude-sonnet-4-5``, ``claude-3-5-sonnet`` and
    ``gemini-2.0-flash`` all return the *same* count for the same text: one
    offline tiktoken encoder serves every provider. The tier is therefore
    "token-shaped and stable", which is far better than ``len // 4`` and is not
    provider-authoritative. ``provider_input_tokens`` is the only authoritative
    number in this ledger, and ``unattributed_delta`` is where the difference
    between the two is meant to stay visible (§3.3).
    """

    TOKENIZER = "tokenizer"
    HEURISTIC = "heuristic"
    PROXY = "proxy"


class DigestTokenCacheKey(RuntimeContract):
    """Identity of a memoized count: the bytes, and the model that counted them.

    ``model`` is part of the key so a count is never served across models. Under
    today's offline guardrail the two would in fact agree — one tiktoken encoder
    serves every provider slug, see :class:`TokenCounterSource` — so this is
    forward-safety rather than a correction: the moment a deployment injects a
    tokenizer that *is* provider-specific, a model-agnostic key would start
    serving one provider's count for another's request, silently and with no
    signal on the segment. Keying on it costs one dict entry per model.

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

    1. :class:`LitellmTokenCounter` — ``litellm.token_counter``, which this
       service calls under ``apply_offline_litellm_config``. Reported as
       ``TOKENIZER``. **Not** the provider's own tokenizer: the offline guardrail
       disables litellm's HuggingFace downloads, so one tiktoken encoder serves
       every provider slug and openai / anthropic / gemini models return the same
       count for the same text. See :class:`TokenCounterSource`, which documents
       the measured evidence, and ``test_context_token_counter``, which pins it.
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

        A blank digest — or any ``(model, digest)`` pair that cannot form a memo
        key — degrades to an uncached :meth:`count` rather than keying every
        distinct segment under the same empty string. See :meth:`_cache_key`.

        The tokenizer runs *outside* the cache lock, so two threads racing a cold
        key may both compute. That is deliberate: duplicate work on a cold key is
        cheaper than serialising every counting caller in the process behind one
        slow tokenizer call, and the results are identical.
        """

        key = self._cache_key(model=model, digest=digest)
        if key is None:
            return self.count(text, model=model)

        cached = self._cache.get(key)
        if cached is not None:
            return cached.estimated_tokens, cached.counter_source

        tokens, source = self.count(text, model=model)
        self._memoize(key, tokens=tokens, source=source)
        return tokens, source

    @staticmethod
    def _cache_key(*, model: str, digest: str) -> DigestTokenCacheKey | None:
        """Build the memo key for these inputs, or ``None`` when none is possible.

        **A memoization key must never be able to raise.** This is Pydantic
        construction, and it runs on the model-call path from
        ``ContextSegment.measure`` — inside the recorder's *per-class* guard,
        which means a ``ValidationError`` here does not degrade one segment, it
        discards every system, message and response-format segment for that call
        while the tool block survives (``ToolSchemaLedger._count`` has a fallback
        of its own). The snapshot then reports an ``estimated_input_tokens`` far
        below what was sent and buries the shortfall in ``unattributed_delta``,
        where it is indistinguishable from tokenizer drift. That is the same
        confidently-wrong failure the restated label bound produced, from the
        same cause: a bound restated instead of derived.

        The bound in question is :class:`DigestTokenCacheKey`'s ``max_length=200``
        on ``model``, which today holds only because ``ModelRouteEntry.model_name``
        happens to be ``max_length=200`` as well — the one identifier in that
        contract that is not 255. Nothing links the two literals, so a widened
        route field would silently arm this path. Guarding is the fix rather than
        re-deriving the literal, because an unmemoized count is a *correct*
        answer that costs one tokenizer call, while a raise is not an answer at
        all: the cache exists to save work, and it must never be able to cost
        correctness.
        """

        if not digest:
            return None
        try:
            return DigestTokenCacheKey(model=model, digest=digest)
        except Exception:  # noqa: BLE001 — an unkeyable count is simply uncached
            _LOGGER.debug(
                "Could not build a digest token-cache key; counting this "
                "segment without memoization.",
                exc_info=True,
            )
            return None

    def _memoize(
        self,
        key: DigestTokenCacheKey,
        *,
        tokens: int,
        source: TokenCounterSource,
    ) -> None:
        """Store one count, treating an unstorable one as merely uncached.

        Guarded for the same reason :meth:`_cache_key` is: :class:`DigestTokenCount`
        is Pydantic construction on the model-call path, and the whole value of
        this cache is saving work — never at the price of the count already in
        hand, which the caller returns either way.
        """

        try:
            self._cache.put(
                key,
                DigestTokenCount(estimated_tokens=tokens, counter_source=source),
            )
        except Exception:  # noqa: BLE001 — an unstorable count is simply uncached
            _LOGGER.debug(
                "Could not memoize a segment token count; the count itself stands.",
                exc_info=True,
            )

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
