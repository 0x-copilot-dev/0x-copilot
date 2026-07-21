"""Unit tests for ``LitellmRateSource`` (pricing slice 1: litellm library).

Hermetic: rate data is either injected (``model_cost=...``) or read from
``litellm.model_cost`` which is bundled offline (no network). ``litellm.__version__``
is pinned by ``requirements.txt`` (1.93.0), so the real-catalog assertions are
deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_runtime.pricing.calculator import CostCalculator
from agent_runtime.pricing.litellm_source import LitellmRateSource


_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

# Minimal fake ``model_cost`` slice for hermetic, injection-based tests.
_FAKE_MODEL_COST: dict[str, dict[str, object]] = {
    "gpt-5": {
        "input_cost_per_token": 1.25e-06,
        "output_cost_per_token": 1e-05,
        "cache_read_input_token_cost": 1.25e-07,
        "max_input_tokens": 272_000,
        "mode": "chat",
        "litellm_provider": "openai",
    },
    # Only reachable via the ``gemini/<model>`` prefixed candidate key.
    "gemini/gemini-2.5-flash": {
        "input_cost_per_token": 3e-07,
        "output_cost_per_token": 2.5e-06,
        "cache_read_input_token_cost": 3e-08,
        "max_input_tokens": 1_048_576,
        "litellm_provider": "gemini",
    },
    # Only reachable via ``openrouter/<vendor/model>`` prefixed candidate key.
    "openrouter/some-vendor/some-model": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 8e-06,
        "max_input_tokens": 128_000,
        "litellm_provider": "openrouter",
    },
    # Embedding-style row: input rate but no output rate -> unpriced.
    "text-embed-only": {
        "input_cost_per_token": 1e-07,
        "mode": "embedding",
    },
}


def _fake_source(*, overrides_path: Path | None = None) -> LitellmRateSource:
    return LitellmRateSource(
        overrides_path=overrides_path,
        model_cost=_FAKE_MODEL_COST,
        litellm_version="1.93.0",
    )


class TestLitellmRateFromInjectedCatalog:
    async def test_bare_key_priced_to_micro_usd_per_million(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        assert record is not None
        # 1.25e-06 USD/token * 1e12 = 1_250_000 micro-USD / 1M tokens.
        assert record.input_per_1m_micro_usd == 1_250_000
        assert record.output_per_1m_micro_usd == 10_000_000
        assert record.cached_input_per_1m_micro_usd == 125_000
        assert record.context_window_tokens == 272_000
        assert record.pricing_source == "litellm"

    async def test_cost_calculator_matches_litellm_rate(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        assert record is not None
        # 800 fresh input @1.25/1M = 1000; 500 output @10/1M = 5000;
        # 200 cached @0.125/1M = 25. Total 6025 micro-USD (integer).
        cost = CostCalculator.compute(
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=200,
            pricing=record,
        )
        assert cost == 6_025
        assert isinstance(cost, int)

    async def test_prefixed_gemini_key_resolves(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="gemini",
            model_name="gemini-2.5-flash",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.input_per_1m_micro_usd == 300_000
        assert record.output_per_1m_micro_usd == 2_500_000

    async def test_google_slug_normalizes_to_gemini(self) -> None:
        # ``google`` is an accepted alias of the canonical ``gemini`` slug.
        record = await _fake_source().lookup_pricing(
            provider="google",
            model_name="gemini-2.5-flash",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.input_per_1m_micro_usd == 300_000

    async def test_openrouter_prefixed_vendor_model_resolves(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="openrouter",
            model_name="some-vendor/some-model",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.input_per_1m_micro_usd == 2_000_000

    async def test_row_without_output_rate_is_unpriced(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="openai", model_name="text-embed-only", region="global", at=_AT
        )
        assert record is None

    async def test_unknown_model_returns_none_no_exception(self) -> None:
        # The safety contract: a model litellm lacks + no override -> None
        # (explicit unpriced), never a raised exception that fails the run.
        record = await _fake_source().lookup_pricing(
            provider="openai",
            model_name="does-not-exist-anywhere",
            region="global",
            at=_AT,
        )
        assert record is None

    async def test_pricing_id_and_version_derived_from_litellm_version(self) -> None:
        record = await _fake_source().lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        assert record is not None
        assert record.pricing_version == "litellm:1.93.0"
        assert record.id == "litellm:1.93.0:openai:gpt-5:global"

    async def test_pricing_id_stable_across_lookups(self) -> None:
        # Immutability: same coordinates + pinned version -> same snapshot id.
        source = _fake_source()
        first = await source.lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        second = await source.lookup_pricing(
            provider="openai",
            model_name="gpt-5",
            region="global",
            at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        assert first is not None and second is not None
        assert first.id == second.id
        assert first.pricing_version == second.pricing_version


class TestOverrideBackstop:
    def _write_override(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "pricing_overrides.yaml"
        path.write_text(body)
        return path

    async def test_override_wins_over_litellm(self, tmp_path: Path) -> None:
        # gpt-5 is present in the fake litellm catalog; the override must win.
        path = self._write_override(
            tmp_path,
            """
            overrides_version: "test-ov"
            overrides:
              - provider: openai
                model_name: gpt-5
                region: global
                input_per_1m_micro_usd: 99_000_000
                output_per_1m_micro_usd: 99_000_000
                reason: "override wins over litellm"
            """,
        )
        record = await _fake_source(overrides_path=path).lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        assert record is not None
        assert record.pricing_source == "override"
        assert record.input_per_1m_micro_usd == 99_000_000

    async def test_override_provider_normalized(self, tmp_path: Path) -> None:
        # An override authored with ``google`` still matches a ``gemini`` lookup.
        path = self._write_override(
            tmp_path,
            """
            overrides_version: "test-ov"
            overrides:
              - provider: google
                model_name: gemini-x
                region: global
                input_per_1m_micro_usd: 111_000
                output_per_1m_micro_usd: 222_000
                reason: "authored with google slug"
            """,
        )
        record = await _fake_source(overrides_path=path).lookup_pricing(
            provider="gemini", model_name="gemini-x", region="global", at=_AT
        )
        assert record is not None
        assert record.pricing_source == "override"
        assert record.input_per_1m_micro_usd == 111_000


class TestRealLitellmCatalog:
    """Against the bundled ``litellm.model_cost`` (offline, version-pinned)."""

    async def test_gpt5_priced_from_real_catalog(self) -> None:
        source = LitellmRateSource(litellm_version="1.93.0")
        record = await source.lookup_pricing(
            provider="openai", model_name="gpt-5", region="global", at=_AT
        )
        assert record is not None
        assert record.input_per_1m_micro_usd == 1_250_000
        assert record.output_per_1m_micro_usd == 10_000_000
        assert record.cached_input_per_1m_micro_usd == 125_000

    async def test_claude_opus_priced_from_real_catalog(self) -> None:
        source = LitellmRateSource(litellm_version="1.93.0")
        record = await source.lookup_pricing(
            provider="anthropic",
            model_name="claude-opus-4-8",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.input_per_1m_micro_usd == 5_000_000
        assert record.output_per_1m_micro_usd == 25_000_000

    async def test_gemini_3_flash_override_wins_over_missing_litellm(self) -> None:
        # gemini-3-flash is absent from litellm 1.93.0; the shipped override
        # backstop (config/pricing_overrides.yaml) supplies the rate.
        source = LitellmRateSource(litellm_version="1.93.0")
        record = await source.lookup_pricing(
            provider="gemini",
            model_name="gemini-3-flash",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.pricing_source == "override"
        assert record.input_per_1m_micro_usd == 300_000
        assert record.output_per_1m_micro_usd == 2_500_000

    async def test_gemini_3_flash_override_matches_google_slug(self) -> None:
        source = LitellmRateSource(litellm_version="1.93.0")
        record = await source.lookup_pricing(
            provider="google",
            model_name="gemini-3-flash",
            region="global",
            at=_AT,
        )
        assert record is not None
        assert record.pricing_source == "override"
