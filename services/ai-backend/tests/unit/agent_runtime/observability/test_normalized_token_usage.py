"""Tests for :class:`NormalizedTokenUsage` value-object semantics."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.observability.token_usage import NormalizedTokenUsage


class TestDefaults:
    def test_zero_defaults_everywhere(self) -> None:
        usage = NormalizedTokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cached_input_tokens == 0
        assert usage.cache_creation_input_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.audio_input_tokens == 0
        assert usage.audio_output_tokens == 0
        assert usage.total_tokens == 0


class TestTotalTokens:
    def test_total_is_sum_of_kinds(self) -> None:
        usage = NormalizedTokenUsage(
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=200,
            audio_input_tokens=10,
            audio_output_tokens=5,
        )
        # input already includes cached + cache_creation by contract
        assert usage.total_tokens == 100 + 50 + 200 + 10 + 5

    def test_cached_does_not_double_count(self) -> None:
        # cached is a SUBSET of input — total must not add it again.
        usage = NormalizedTokenUsage(input_tokens=1000, cached_input_tokens=800)
        assert usage.total_tokens == 1000

    def test_cache_creation_does_not_double_count(self) -> None:
        usage = NormalizedTokenUsage(
            input_tokens=1500, cache_creation_input_tokens=1000
        )
        assert usage.total_tokens == 1500


class TestMerge:
    def test_field_wise_max(self) -> None:
        a = NormalizedTokenUsage(input_tokens=100, output_tokens=200)
        b = NormalizedTokenUsage(input_tokens=150, output_tokens=180)
        merged = a.merge(b)
        assert merged.input_tokens == 150
        assert merged.output_tokens == 200

    def test_merge_is_commutative(self) -> None:
        a = NormalizedTokenUsage(input_tokens=10, output_tokens=20, reasoning_tokens=5)
        b = NormalizedTokenUsage(input_tokens=8, output_tokens=25, audio_input_tokens=3)
        assert a.merge(b) == b.merge(a)

    def test_merge_preserves_all_kinds(self) -> None:
        a = NormalizedTokenUsage(reasoning_tokens=100)
        b = NormalizedTokenUsage(audio_input_tokens=50, cache_creation_input_tokens=25)
        merged = a.merge(b)
        assert merged.reasoning_tokens == 100
        assert merged.audio_input_tokens == 50
        assert merged.cache_creation_input_tokens == 25


class TestImmutability:
    def test_frozen(self) -> None:
        usage = NormalizedTokenUsage(input_tokens=100)
        with pytest.raises(ValidationError):
            usage.input_tokens = 200  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTokenUsage(input_tokens=100, mystery_field=42)  # type: ignore[call-arg]


class TestValidation:
    def test_negative_input_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTokenUsage(input_tokens=-1)

    def test_negative_reasoning_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedTokenUsage(reasoning_tokens=-100)
