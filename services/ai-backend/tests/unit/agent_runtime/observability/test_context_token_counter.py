"""Unit tests for the occupancy-ledger segment token counter (PRD-04).

Two contracts are under test and they are the reason the module exists:

1. :meth:`ContextTokenCounter.count` is **total**. No input and no injected port
   can make it raise, because it runs on the model-call hot path and occupancy
   is best-effort observability (design §6.4).
2. :meth:`ContextTokenCounter.count_digested` memoizes on ``(model, digest)``
   with bounded LRU eviction (design §3.4), so the resident prompt surface is
   tokenized once per process rather than once per model call.

All offline. The real ``LitellmTokenCounter`` runs under the offline guardrail
(bundled cost map, HF download disabled) exactly as
``tests/unit/agent_runtime/budgets/test_token_counter.py`` establishes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    DigestTokenCache,
    DigestTokenCacheKey,
    DigestTokenCacheStats,
    DigestTokenCount,
    TokenCounterSource,
)


class FakeCounter:
    """Deterministic ``TokenCounterPort`` whose answer the test dictates.

    ``answer`` is returned verbatim (including deliberately invalid values such
    as ``None``, a negative int, or a non-int) so the fallback chain's rejection
    rules can be exercised. ``raises`` makes the tier blow up instead.
    """

    def __init__(self, *, answer: object = 0, raises: BaseException | None = None):
        self.answer = answer
        self.raises = raises
        self.calls: list[tuple[str, str]] = []

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        content = "".join(
            str(message.get("content", ""))
            for message in messages  # type: ignore[union-attr]
        )
        self.calls.append((model, content))
        if self.raises is not None:
            raise self.raises
        return self.answer  # type: ignore[return-value]


class ExplodingCounter:
    """A port that raises on every call, standing in for a broken tokenizer."""

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        del model, messages
        raise RuntimeError("tokenizer exploded")


class CounterBuilderMixin:
    """Construction helpers shared by the counting tests."""

    MODEL = "gpt-5.4-mini"
    OTHER_MODEL = "claude-3-5-sonnet-20240620"
    TEXT = "x" * 40

    def isolated_cache(self, *, max_entries: int = 8) -> DigestTokenCache:
        """A cache this test owns outright — never the process-wide one."""

        return DigestTokenCache(max_entries=max_entries)

    def counter(
        self,
        *,
        tokenizer: object | None = None,
        heuristic: object | None = None,
        cache: DigestTokenCache | None = None,
    ) -> ContextTokenCounter:
        return ContextTokenCounter(
            tokenizer=tokenizer,  # type: ignore[arg-type]
            heuristic=heuristic,  # type: ignore[arg-type]
            cache=cache or self.isolated_cache(),
        )


class TestTokenCounterSource:
    def test_values_are_the_persisted_wire_strings(self) -> None:
        assert TokenCounterSource.TOKENIZER == "tokenizer"
        assert TokenCounterSource.HEURISTIC == "heuristic"
        assert TokenCounterSource.PROXY == "proxy"

    def test_enum_is_closed_to_the_three_tiers(self) -> None:
        assert {member.value for member in TokenCounterSource} == {
            "tokenizer",
            "heuristic",
            "proxy",
        }


class TestFallbackChain(CounterBuilderMixin):
    def test_tokenizer_tier_wins_when_it_answers(self) -> None:
        heuristic = FakeCounter(answer=999)
        counter = self.counter(tokenizer=FakeCounter(answer=17), heuristic=heuristic)

        assert counter.count(self.TEXT, model=self.MODEL) == (
            17,
            TokenCounterSource.TOKENIZER,
        )
        assert heuristic.calls == []

    def test_zero_from_the_tokenizer_is_a_valid_answer(self) -> None:
        # Zero must not be confused with "no answer" — an empty-after-
        # normalization segment legitimately costs nothing.
        counter = self.counter(
            tokenizer=FakeCounter(answer=0),
            heuristic=FakeCounter(answer=123),
        )

        assert counter.count(self.TEXT, model=self.MODEL) == (
            0,
            TokenCounterSource.TOKENIZER,
        )

    def test_falls_to_heuristic_when_the_tokenizer_raises(self) -> None:
        counter = self.counter(
            tokenizer=ExplodingCounter(),
            heuristic=FakeCounter(answer=11),
        )

        assert counter.count(self.TEXT, model=self.MODEL) == (
            11,
            TokenCounterSource.HEURISTIC,
        )

    def test_falls_to_heuristic_when_the_tokenizer_returns_none(self) -> None:
        counter = self.counter(
            tokenizer=FakeCounter(answer=None),
            heuristic=FakeCounter(answer=7),
        )

        assert counter.count(self.TEXT, model=self.MODEL) == (
            7,
            TokenCounterSource.HEURISTIC,
        )

    @pytest.mark.parametrize("bad_answer", [-1, "12", 3.5, True, None])
    def test_rejects_nonsensical_tier_answers(self, bad_answer: object) -> None:
        counter = self.counter(
            tokenizer=FakeCounter(answer=bad_answer),
            heuristic=FakeCounter(answer=5),
        )

        assert counter.count(self.TEXT, model=self.MODEL) == (
            5,
            TokenCounterSource.HEURISTIC,
        )

    def test_proxy_tier_when_the_whole_injected_chain_blows_up(self) -> None:
        counter = self.counter(
            tokenizer=ExplodingCounter(),
            heuristic=ExplodingCounter(),
        )

        assert counter.count(self.TEXT, model=self.MODEL) == (
            len(self.TEXT) // 4,
            TokenCounterSource.PROXY,
        )

    def test_never_raises_for_any_pathological_input(self) -> None:
        counter = self.counter(
            tokenizer=ExplodingCounter(),
            heuristic=ExplodingCounter(),
        )

        for text in ("", "\x00\x00", "🙂" * 100, "a" * 100_000):
            tokens, source = counter.count(text, model=self.MODEL)
            assert tokens >= 0
            assert isinstance(source, TokenCounterSource)

    def test_empty_text_short_circuits_without_touching_any_tier(self) -> None:
        tokenizer = FakeCounter(answer=4)
        counter = self.counter(tokenizer=tokenizer, heuristic=FakeCounter(answer=4))

        assert counter.count("", model=self.MODEL) == (0, TokenCounterSource.HEURISTIC)
        assert tokenizer.calls == []

    def test_non_string_input_is_zero_rather_than_a_type_error(self) -> None:
        counter = self.counter(
            tokenizer=FakeCounter(answer=4),
            heuristic=FakeCounter(answer=4),
        )

        assert counter.count(None, model=self.MODEL) == (  # type: ignore[arg-type]
            0,
            TokenCounterSource.HEURISTIC,
        )

    def test_segment_text_reaches_the_port_as_one_message(self) -> None:
        tokenizer = FakeCounter(answer=3)
        self.counter(tokenizer=tokenizer, heuristic=FakeCounter(answer=1)).count(
            "segment body",
            model=self.MODEL,
        )

        assert tokenizer.calls == [(self.MODEL, "segment body")]


class TestRealCounterChain(CounterBuilderMixin):
    """The default (unfaked) chain, exercised offline against real litellm."""

    def test_default_chain_uses_the_real_tokenizer(self) -> None:
        counter = ContextTokenCounter(cache=self.isolated_cache())

        tokens, source = counter.count(
            "The publish_artifact description charges rent on every call.",
            model=self.MODEL,
        )

        assert source is TokenCounterSource.TOKENIZER
        assert tokens > 0

    def test_unknown_model_degrades_instead_of_raising(self) -> None:
        counter = ContextTokenCounter(cache=self.isolated_cache())

        tokens, source = counter.count(self.TEXT, model="not-a-real-provider/nope")

        # litellm may or may not resolve an encoder for an unknown slug; either
        # way the call must return a usable number and never propagate.
        assert tokens > 0
        assert source in (
            TokenCounterSource.TOKENIZER,
            TokenCounterSource.HEURISTIC,
        )


class TestMeasuredCountingBias(CounterBuilderMixin):
    """The design's §9 "reconciliation bound", which was never written down.

    §9 proposes asserting ``|unattributed_delta| / provider_input_tokens < 0.05``
    per provider. No such test shipped, so nothing bounded how far
    ``estimated_input_tokens`` could sit from the truth — and the answer turned
    out to be *further than the proposed tolerance*, for a reason that has
    nothing to do with tokenizer accuracy. These tests pin the two measured facts
    behind that, so neither can move without a failing assertion.
    """

    # 11 system fragments + 30 tool schemas + 40 message parts, the shape of the
    # design's own §11 reference measurements.
    SEGMENT_COUNT = 81
    MAX_ENVELOPE_TOKENS_PER_SEGMENT = 8

    PROVIDER_SLUGS = (
        "gpt-4o-mini",
        "claude-sonnet-4-5-20250929",
        "claude-3-5-sonnet-20241022",
        "gemini/gemini-2.0-flash",
    )

    def segments(self) -> tuple[str, ...]:
        return (
            *(f"system fragment {i}: " + ("policy text. " * 60) for i in range(11)),
            *(
                f'{{"name":"tool_{i}","description":"' + ("does a thing. " * 40) + '"}'
                for i in range(30)
            ),
            *(f"message part {i}: " + ("conversation text. " * 25) for i in range(40)),
        )

    def test_the_per_segment_envelope_bias_stays_bounded(self) -> None:
        """Σ per-segment must exceed a one-shot count by no more than the envelope.

        Every segment is wrapped in its own synthetic message, so each pays
        litellm's per-message envelope. The bias is therefore proportional to
        *segment count*, not to tokenizer error: on this 81-segment request it is
        roughly +6%, which already exceeds §9's ±5% tolerance before any real
        drift. Pinning it keeps a future change that adds segments (or stops
        rolling the assembly joiners up) from quietly doubling the residual.
        """

        segments = self.segments()
        assert len(segments) == self.SEGMENT_COUNT
        counter = ContextTokenCounter(cache=self.isolated_cache(max_entries=256))

        per_segment = sum(
            counter.count(text, model=self.PROVIDER_SLUGS[0])[0] for text in segments
        )
        one_shot, _ = counter.count("".join(segments), model=self.PROVIDER_SLUGS[0])

        overcount = per_segment - one_shot
        assert overcount > 0, "the envelope biases high, not low"
        assert overcount <= self.SEGMENT_COUNT * self.MAX_ENVELOPE_TOKENS_PER_SEGMENT

    def test_the_offline_chain_is_one_encoder_for_every_provider(self) -> None:
        """``TOKENIZER`` is not a per-provider claim, and the enum says so.

        The offline guardrail disables the HuggingFace tokenizer downloads so
        counting is deterministic and network-free, which means every provider
        slug resolves to the same tiktoken encoder. Asserting the equality keeps
        the documentation honest in both directions: if a future litellm bump
        *does* bundle provider tokenizers, this fails and the enum's docstring —
        plus ``DigestTokenCacheKey``'s justification for keying on model — has to
        be revisited rather than silently becoming true.
        """

        counter = ContextTokenCounter(cache=self.isolated_cache(max_entries=64))
        text = "The publish_artifact description charges rent on every call."

        counts = {
            model: counter.count(text, model=model) for model in self.PROVIDER_SLUGS
        }

        assert {tokens for tokens, _ in counts.values()} == {
            counts[self.PROVIDER_SLUGS[0]][0]
        }, f"per-provider tokenizers would disagree: {counts}"
        assert all(
            source is TokenCounterSource.TOKENIZER for _, source in counts.values()
        )


class TestDigestMemoization(CounterBuilderMixin):
    def test_second_call_with_the_same_digest_is_a_cache_hit(self) -> None:
        cache = self.isolated_cache()
        tokenizer = FakeCounter(answer=42)
        counter = self.counter(tokenizer=tokenizer, cache=cache)

        first = counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")
        second = counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")

        assert first == second == (42, TokenCounterSource.TOKENIZER)
        assert len(tokenizer.calls) == 1
        stats = cache.stats()
        assert (stats.hits, stats.misses, stats.current_size) == (1, 1, 1)

    def test_a_different_digest_is_a_miss(self) -> None:
        tokenizer = FakeCounter(answer=8)
        counter = self.counter(tokenizer=tokenizer)

        counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")
        counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-b")

        assert len(tokenizer.calls) == 2

    def test_the_same_digest_under_a_different_model_is_a_miss(self) -> None:
        # Tokenizers genuinely disagree; collapsing models would mis-attribute
        # occupancy on any deployment routing more than one model.
        tokenizer = FakeCounter(answer=8)
        counter = self.counter(tokenizer=tokenizer)

        counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")
        counter.count_digested(self.TEXT, model=self.OTHER_MODEL, digest="sha-a")

        assert len(tokenizer.calls) == 2

    def test_blank_digest_bypasses_the_cache_entirely(self) -> None:
        cache = self.isolated_cache()
        tokenizer = FakeCounter(answer=6)
        counter = self.counter(tokenizer=tokenizer, cache=cache)

        counter.count_digested(self.TEXT, model=self.MODEL, digest="")
        counter.count_digested(self.TEXT, model=self.MODEL, digest="")

        assert len(tokenizer.calls) == 2
        assert cache.stats().current_size == 0

    def test_counter_source_survives_the_round_trip(self) -> None:
        # A degraded PROXY count must not silently become TOKENIZER on the
        # cached read — that would hide the §6.4 fail-open signal.
        counter = self.counter(
            tokenizer=ExplodingCounter(),
            heuristic=ExplodingCounter(),
        )

        first = counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")
        second = counter.count_digested(self.TEXT, model=self.MODEL, digest="sha-a")

        assert first == second == (len(self.TEXT) // 4, TokenCounterSource.PROXY)

    def test_memoization_never_raises_even_when_every_tier_fails(self) -> None:
        counter = self.counter(
            tokenizer=ExplodingCounter(),
            heuristic=ExplodingCounter(),
        )

        assert counter.count_digested("", model=self.MODEL, digest="sha-a") == (
            0,
            TokenCounterSource.HEURISTIC,
        )


class TestDigestTokenCache(CounterBuilderMixin):
    def key(self, digest: str, *, model: str = "m") -> DigestTokenCacheKey:
        return DigestTokenCacheKey(model=model, digest=digest)

    def value(self, tokens: int) -> DigestTokenCount:
        return DigestTokenCount(
            estimated_tokens=tokens,
            counter_source=TokenCounterSource.TOKENIZER,
        )

    def test_evicts_the_least_recently_used_entry_at_the_cap(self) -> None:
        cache = DigestTokenCache(max_entries=2)
        cache.put(self.key("a"), self.value(1))
        cache.put(self.key("b"), self.value(2))
        cache.put(self.key("c"), self.value(3))

        assert cache.get(self.key("a")) is None
        assert cache.get(self.key("b")) is not None
        assert cache.get(self.key("c")) is not None
        assert cache.stats().evictions == 1

    def test_reading_an_entry_protects_it_from_the_next_eviction(self) -> None:
        cache = DigestTokenCache(max_entries=2)
        cache.put(self.key("a"), self.value(1))
        cache.put(self.key("b"), self.value(2))

        cache.get(self.key("a"))  # "a" becomes most-recently-used
        cache.put(self.key("c"), self.value(3))

        assert cache.get(self.key("a")) is not None
        assert cache.get(self.key("b")) is None

    def test_eviction_is_driven_by_the_counter_not_the_process_cache(self) -> None:
        cache = DigestTokenCache(max_entries=2)
        counter = self.counter(tokenizer=FakeCounter(answer=5), cache=cache)

        for digest in ("d1", "d2", "d3"):
            counter.count_digested(self.TEXT, model=self.MODEL, digest=digest)

        assert cache.stats().current_size == 2
        assert cache.stats().evictions == 1

    def test_reput_of_an_existing_key_does_not_grow_the_cache(self) -> None:
        cache = DigestTokenCache(max_entries=2)
        cache.put(self.key("a"), self.value(1))
        cache.put(self.key("a"), self.value(9))

        assert cache.stats().current_size == 1
        cached = cache.get(self.key("a"))
        assert cached is not None and cached.estimated_tokens == 9

    def test_clear_empties_entries_and_counters(self) -> None:
        cache = DigestTokenCache(max_entries=4)
        cache.put(self.key("a"), self.value(1))
        cache.get(self.key("a"))
        cache.get(self.key("zzz"))

        cache.clear()

        assert cache.stats() == DigestTokenCacheStats(
            hits=0,
            misses=0,
            evictions=0,
            current_size=0,
        )
        assert cache.get(self.key("a")) is None

    def test_rejects_a_non_positive_cap(self) -> None:
        with pytest.raises(ValueError):
            DigestTokenCache(max_entries=0)

    def test_default_cap_is_the_documented_bound(self) -> None:
        assert DigestTokenCache.DEFAULT_MAX_ENTRIES == 2048


class TestSharedCache(CounterBuilderMixin):
    def teardown_method(self) -> None:
        # Never leave process-wide state behind for the next test module.
        DigestTokenCache.reset_shared()

    def test_shared_returns_one_process_wide_instance(self) -> None:
        assert DigestTokenCache.shared() is DigestTokenCache.shared()

    def test_default_constructed_counters_share_that_instance(self) -> None:
        left = ContextTokenCounter(tokenizer=FakeCounter(answer=3))
        right = ContextTokenCounter(tokenizer=FakeCounter(answer=99))

        assert left.cache is right.cache is DigestTokenCache.shared()

        first = left.count_digested(self.TEXT, model=self.MODEL, digest="shared")
        second = right.count_digested(self.TEXT, model=self.MODEL, digest="shared")

        # ``right`` never tokenizes: the process-wide memo already had the bytes.
        assert first == second == (3, TokenCounterSource.TOKENIZER)

    def test_clearing_the_shared_cache_forces_a_recount(self) -> None:
        tokenizer = FakeCounter(answer=3)
        counter = ContextTokenCounter(tokenizer=tokenizer)

        counter.count_digested(self.TEXT, model=self.MODEL, digest="shared")
        DigestTokenCache.shared().clear()
        counter.count_digested(self.TEXT, model=self.MODEL, digest="shared")

        assert len(tokenizer.calls) == 2

    def test_reset_shared_installs_a_fresh_instance(self) -> None:
        original = DigestTokenCache.shared()
        DigestTokenCache.reset_shared()

        assert DigestTokenCache.shared() is not original


class TestTheMemoKeyCanNeverRaise(CounterBuilderMixin):
    """A memoization key must not be able to cost a count (design §6.4).

    ``count_digested`` builds a :class:`DigestTokenCacheKey`, and that is
    Pydantic construction on the model-call path. The bound it enforces on
    ``model`` (200) holds today only because ``ModelRouteEntry.model_name``
    happens to carry the same literal — the one identifier in that contract that
    is not 255 — and nothing links the two. If the key were ever to raise, the
    failure would not degrade one segment: ``ContextSegment.measure`` sits inside
    the recorder's *per-class* guard, so every system, message and
    response-format segment for that call would be discarded while the tool block
    survived on its own fallback, and the shortfall would land in
    ``unattributed_delta`` looking exactly like tokenizer drift.

    An unmemoized count is a correct answer that costs one tokenizer call. That
    is the required degradation, and these tests are what stop the guard being
    removed as "dead".
    """

    ILLEGAL_KEY_INPUTS: tuple[tuple[str, str], ...] = (
        ("", "a" * 64),
        ("m" * 201, "a" * 64),
        ("gpt-5", "a" * 201),
    )

    @pytest.mark.parametrize(("model", "digest"), ILLEGAL_KEY_INPUTS)
    def test_an_unkeyable_pair_still_returns_a_count(
        self, model: str, digest: str
    ) -> None:
        tokenizer = FakeCounter(answer=11)
        counter = self.counter(tokenizer=tokenizer)

        assert counter.count_digested(self.TEXT, model=model, digest=digest) == (
            11,
            TokenCounterSource.TOKENIZER,
        )

    @pytest.mark.parametrize(("model", "digest"), ILLEGAL_KEY_INPUTS)
    def test_an_unkeyable_pair_is_counted_uncached_rather_than_stored(
        self, model: str, digest: str
    ) -> None:
        tokenizer = FakeCounter(answer=11)
        cache = self.isolated_cache()
        counter = self.counter(tokenizer=tokenizer, cache=cache)

        counter.count_digested(self.TEXT, model=model, digest=digest)
        counter.count_digested(self.TEXT, model=model, digest=digest)

        # Both calls reached the tokenizer, and nothing unkeyable was stored.
        assert len(tokenizer.calls) == 2
        assert cache.stats().current_size == 0

    def test_a_legal_pair_is_still_memoized_alongside_illegal_ones(self) -> None:
        tokenizer = FakeCounter(answer=7)
        cache = self.isolated_cache()
        counter = self.counter(tokenizer=tokenizer, cache=cache)

        counter.count_digested(self.TEXT, model="", digest="a" * 64)
        counter.count_digested(self.TEXT, model=self.MODEL, digest="a" * 64)
        counter.count_digested(self.TEXT, model=self.MODEL, digest="a" * 64)

        assert cache.stats().current_size == 1
        assert cache.stats().hits == 1

    def test_a_cache_that_refuses_to_store_never_costs_the_count(self) -> None:
        class RefusingCache(DigestTokenCache):
            def put(self, key: object, value: object) -> None:  # type: ignore[override]
                del key, value
                raise RuntimeError("cache backend unavailable")

        counter = self.counter(
            tokenizer=FakeCounter(answer=5), cache=RefusingCache(max_entries=4)
        )

        assert counter.count_digested(self.TEXT, model=self.MODEL, digest="d") == (
            5,
            TokenCounterSource.TOKENIZER,
        )
