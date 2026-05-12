"""Unit tests for ``LiteLLMPricingSource`` (P12 Step 1).

Covers conversion correctness, rounding parity with ``CostCalculator``,
model-name canonicalisation, ``mode`` filtering, missing-field
handling, and the reasoning-field drop+log path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.calculator import CostCalculator
from agent_runtime.pricing.litellm_source import LiteLLMPricingSource
from agent_runtime.pricing.seed_loader import PricingSeedLoader


_EFFECTIVE_FROM = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


class _SyntheticDataMixin:
    """Build a tiny ``model_prices.json`` shape in a tmp path."""

    @staticmethod
    def write_data(tmp_path: Path, payload: dict[str, object]) -> Path:
        data_path = tmp_path / "model_prices.json"
        data_path.write_text(json.dumps(payload))
        return data_path

    @staticmethod
    def claude_row(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
            "cache_read_input_token_cost": 3e-07,
            "max_input_tokens": 1_000_000,
        }
        base.update(overrides)
        return base


class TestConversion(_SyntheticDataMixin):
    def test_converts_anthropic_row_to_micro_per_million(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "claude-sonnet-4-6": self.claude_row(),
            },
        )
        records = LiteLLMPricingSource.load_all(
            data_path=data_path, effective_from=_EFFECTIVE_FROM
        )
        assert len(records) == 1
        record = records[0]
        assert record.provider == "anthropic"
        assert record.model_name == "claude-sonnet-4-6"
        assert record.region == "global"
        # $3 / 1M = 3_000_000 micro_usd; $15 / 1M = 15_000_000 micro_usd;
        # cached $0.30 / 1M = 300_000 micro_usd.
        assert record.input_per_1m_micro_usd == 3_000_000
        assert record.output_per_1m_micro_usd == 15_000_000
        assert record.cached_input_per_1m_micro_usd == 300_000
        assert record.context_window_tokens == 1_000_000
        assert record.pricing_source == "litellm"
        assert record.pricing_version.startswith("litellm-")

    def test_missing_cache_read_yields_none(self, tmp_path: Path) -> None:
        row = self.claude_row()
        del row["cache_read_input_token_cost"]
        data_path = self.write_data(tmp_path, {"claude-haiku-4-5": row})
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert records[0].cached_input_per_1m_micro_usd is None

    def test_missing_max_input_tokens_falls_back_to_max_tokens(
        self, tmp_path: Path
    ) -> None:
        row = self.claude_row(max_input_tokens=None, max_tokens=200_000)
        del row["max_input_tokens"]
        data_path = self.write_data(tmp_path, {"some-model": row})
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert records[0].context_window_tokens == 200_000

    def test_explicit_effective_from_used(self, tmp_path: Path) -> None:
        data_path = self.write_data(tmp_path, {"claude-opus-4-7": self.claude_row()})
        records = LiteLLMPricingSource.load_all(
            data_path=data_path, effective_from=_EFFECTIVE_FROM
        )
        assert records[0].effective_from == _EFFECTIVE_FROM

    def test_explicit_pricing_version_used(self, tmp_path: Path) -> None:
        data_path = self.write_data(tmp_path, {"claude-opus-4-7": self.claude_row()})
        records = LiteLLMPricingSource.load_all(
            data_path=data_path,
            effective_from=_EFFECTIVE_FROM,
            pricing_version="litellm-v1.83.14",
        )
        assert records[0].pricing_version == "litellm-v1.83.14"


class TestModeFilter(_SyntheticDataMixin):
    @pytest.mark.parametrize(
        "skip_mode",
        [
            "embedding",
            "image_generation",
            "audio_transcription",
            "audio_speech",
            "moderation",
            "rerank",
        ],
    )
    def test_skipped_modes_excluded(self, tmp_path: Path, skip_mode: str) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "text-embedding-3-large": self.claude_row(
                    litellm_provider="openai", mode=skip_mode
                ),
                "claude-sonnet-4-6": self.claude_row(),
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        keys = {(r.provider, r.model_name) for r in records}
        assert ("anthropic", "claude-sonnet-4-6") in keys
        assert ("openai", "text-embedding-3-large") not in keys

    def test_unknown_mode_skipped(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "video-model": self.claude_row(mode="video_generation"),
                "claude-sonnet-4-6": self.claude_row(),
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert len(records) == 1
        assert records[0].model_name == "claude-sonnet-4-6"

    def test_completion_mode_kept(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "gpt-4-base": self.claude_row(
                    litellm_provider="openai", mode="completion"
                )
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert len(records) == 1

    def test_responses_mode_kept(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {"gpt-5": self.claude_row(litellm_provider="openai", mode="responses")},
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert len(records) == 1


class TestCanonicalisation(_SyntheticDataMixin):
    @pytest.mark.parametrize(
        ("key", "expected_model_name"),
        [
            ("claude-opus-4-7", "claude-opus-4-7"),
            ("anthropic/claude-opus-4-7", "claude-opus-4-7"),
            ("anthropic.claude-opus-4-7", "claude-opus-4-7"),
            ("us.anthropic.claude-opus-4-7", "claude-opus-4-7"),
            ("global.anthropic.claude-opus-4-7", "claude-opus-4-7"),
            ("eu.anthropic.claude-opus-4-7", "claude-opus-4-7"),
            ("au.anthropic.claude-opus-4-7", "claude-opus-4-7"),
            ("gpt-5.4-mini", "gpt-5.4-mini"),
            ("gemini-2.5-pro", "gemini-2.5-pro"),
        ],
    )
    def test_strip_region_and_provider_prefixes(
        self, tmp_path: Path, key: str, expected_model_name: str
    ) -> None:
        data_path = self.write_data(tmp_path, {key: self.claude_row()})
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert records[0].model_name == expected_model_name


class TestRoundingParity:
    def test_round_half_to_even_matches_calculator_for_cents(self) -> None:
        # Banker's rounding: a .5 boundary rounds to nearest even integer.
        # $1.5e-12 / token -> per_1m_micro_usd should be 1.5 -> rounds to 2.
        rate_int = LiteLLMPricingSource._usd_per_token_to_micro_per_million(1.5e-12)
        assert rate_int == 2
        # $4.5e-12 / token -> per_1m_micro_usd should be 4.5 -> rounds to 4.
        rate_int = LiteLLMPricingSource._usd_per_token_to_micro_per_million(4.5e-12)
        assert rate_int == 4

    def test_zero_token_cost_returns_zero(self) -> None:
        assert LiteLLMPricingSource._usd_per_token_to_micro_per_million(0.0) == 0

    def test_negative_token_cost_returns_zero(self) -> None:
        # Fail-soft: never let a malformed upstream row produce a negative rate.
        assert LiteLLMPricingSource._usd_per_token_to_micro_per_million(-1e-06) == 0

    @pytest.mark.parametrize(
        ("per_token_usd", "expected_per_1m_micro"),
        [
            (1e-06, 1_000_000),  # $1 / 1M
            (3e-06, 3_000_000),  # $3 / 1M
            (15e-06, 15_000_000),  # $15 / 1M
            (5e-07, 500_000),  # $0.50 / 1M
            (1e-07, 100_000),  # $0.10 / 1M
        ],
    )
    def test_known_values_round_trip_cleanly(
        self, per_token_usd: float, expected_per_1m_micro: int
    ) -> None:
        assert (
            LiteLLMPricingSource._usd_per_token_to_micro_per_million(per_token_usd)
            == expected_per_1m_micro
        )

    def test_calculator_consumes_litellm_record_consistently(self) -> None:
        # End-to-end parity: the integer rates the source produces feed the
        # calculator and yield the same micro-USD totals as if we'd
        # authored the YAML seed by hand. 1M input @ $3/1M -> 3_000_000.
        record = ModelPricingRecord(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            effective_from=_EFFECTIVE_FROM,
            input_per_1m_micro_usd=LiteLLMPricingSource._usd_per_token_to_micro_per_million(
                3e-06
            ),
            output_per_1m_micro_usd=LiteLLMPricingSource._usd_per_token_to_micro_per_million(
                1.5e-05
            ),
            cached_input_per_1m_micro_usd=LiteLLMPricingSource._usd_per_token_to_micro_per_million(
                3e-07
            ),
            pricing_version="litellm-2026-05-11",
        )
        cost = CostCalculator.compute(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_input_tokens=0,
            pricing=record,
        )
        assert cost == 3_000_000


class TestSkipPaths(_SyntheticDataMixin):
    def test_missing_provider_skipped(self, tmp_path: Path) -> None:
        row = self.claude_row()
        del row["litellm_provider"]
        data_path = self.write_data(tmp_path, {"model-without-provider": row})
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert records == ()

    def test_missing_input_cost_skipped(self, tmp_path: Path) -> None:
        row = self.claude_row()
        del row["input_cost_per_token"]
        data_path = self.write_data(tmp_path, {"claude-x": row})
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert records == ()

    def test_sample_spec_skipped(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "sample_spec": {"_comment": "demo row"},
                "claude-sonnet-4-6": self.claude_row(),
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        keys = {(r.provider, r.model_name) for r in records}
        assert keys == {("anthropic", "claude-sonnet-4-6")}

    def test_non_mapping_row_skipped(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "weird-row": ["not", "a", "mapping"],
                "claude-sonnet-4-6": self.claude_row(),
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        assert len(records) == 1


class TestReasoningFieldLogged(_SyntheticDataMixin):
    def test_reasoning_cost_is_dropped_but_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        row = self.claude_row(output_reasoning_token_cost=1.25e-05)
        data_path = self.write_data(tmp_path, {"claude-opus-4-7": row})
        with caplog.at_level("INFO", logger="agent_runtime.pricing.litellm_source"):
            records = LiteLLMPricingSource.load_all(data_path=data_path)
        # Record produced, but no reasoning column on ModelPricingRecord (out of scope).
        assert len(records) == 1
        # A log line was emitted naming the model so the future reasoning-billing PRD has data.
        reasoning_logs = [
            r for r in caplog.records if r.message == "pricing.reasoning_field_dropped"
        ]
        assert len(reasoning_logs) == 1
        assert getattr(reasoning_logs[0], "provider", None) == "anthropic"


class TestByKeyIndex(_SyntheticDataMixin):
    def test_by_key_returns_dict_keyed_by_triple(self, tmp_path: Path) -> None:
        data_path = self.write_data(
            tmp_path,
            {
                "claude-sonnet-4-6": self.claude_row(),
                "gpt-5": self.claude_row(litellm_provider="openai"),
            },
        )
        records = LiteLLMPricingSource.load_all(data_path=data_path)
        index = LiteLLMPricingSource.by_key(records)
        assert ("anthropic", "claude-sonnet-4-6", "global") in index
        assert ("openai", "gpt-5", "global") in index


class TestVendoredFile:
    """Integration-style sanity checks against the real vendored JSON."""

    def test_vendored_file_loads_without_error(self) -> None:
        records = LiteLLMPricingSource.load_all()
        # The vendored file has ~2700 entries; after filtering we keep
        # the billable subset which is still in the hundreds.
        assert len(records) > 100

    def test_vendored_file_produces_chat_models_for_each_seed_provider(self) -> None:
        records = LiteLLMPricingSource.load_all()
        providers = {r.provider for r in records}
        assert {"anthropic", "openai"}.issubset(providers)

    def test_vendored_anthropic_seeds_present(self) -> None:
        records = LiteLLMPricingSource.load_all()
        anthropic = {r.model_name for r in records if r.provider == "anthropic"}
        # Cross-reference with the YAML seed: every model in the seed
        # should also be in LiteLLM (otherwise we can't compare). If a
        # seed name vanishes from upstream, this test will flag it.
        seed_models = {
            r.model_name
            for r in PricingSeedLoader.load_all()
            if r.provider == "anthropic"
        }
        assert seed_models.issubset(anthropic), (
            f"YAML seeds reference Anthropic models not present in vendored "
            f"LiteLLM data: {seed_models - anthropic}"
        )
