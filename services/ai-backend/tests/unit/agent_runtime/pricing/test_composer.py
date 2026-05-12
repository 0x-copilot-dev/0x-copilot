"""Unit tests for ``PricingComposer`` (P12 Step 2)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.composer import PricingComposer, PricingComposerError


_EFFECTIVE_FROM = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


class _YamlMixin:
    """Write override + seed YAMLs in tmp paths."""

    @staticmethod
    def write_override(tmp_path: Path, body: str) -> Path:
        path = tmp_path / "pricing_overrides.yaml"
        path.write_text(body)
        return path

    @staticmethod
    def write_litellm_data(tmp_path: Path, body: str) -> Path:
        path = tmp_path / "model_prices.json"
        path.write_text(body)
        return path


_TWO_PROVIDER_LITELLM = """
{
  "claude-sonnet-4-6": {
    "litellm_provider": "anthropic",
    "mode": "chat",
    "input_cost_per_token": 3e-06,
    "output_cost_per_token": 1.5e-05,
    "cache_read_input_token_cost": 3e-07,
    "max_input_tokens": 1000000
  },
  "gpt-5": {
    "litellm_provider": "openai",
    "mode": "chat",
    "input_cost_per_token": 1.25e-06,
    "output_cost_per_token": 1e-05,
    "max_input_tokens": 272000
  }
}
"""


class TestPrimarySourceSelection(_YamlMixin):
    def test_unknown_primary_source_raises(self) -> None:
        with pytest.raises(PricingComposerError, match="unknown primary_source"):
            PricingComposer.load(primary_source="bogus")  # type: ignore[arg-type]

    def test_primary_litellm_uses_vendored_data(self, tmp_path: Path) -> None:
        data_path = self.write_litellm_data(tmp_path, _TWO_PROVIDER_LITELLM)
        records = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=data_path,
            overrides_path=tmp_path / "no_overrides.yaml",
            effective_from=_EFFECTIVE_FROM,
        )
        keys = {(r.provider, r.model_name) for r in records}
        assert keys == {("anthropic", "claude-sonnet-4-6"), ("openai", "gpt-5")}

    def test_primary_yaml_uses_seed_loader(self, tmp_path: Path) -> None:
        # Tests run against the shipped seed dir by default; constrain to
        # a tmp seed_dir with a single file to assert the YAML branch
        # really reads from seed_loader, not LiteLLM.
        seed_dir = tmp_path / "seeds"
        seed_dir.mkdir()
        (seed_dir / "test-2026-q1.yaml").write_text(
            """
            pricing_version: "test-2026-q1.v1"
            provider: anthropic
            prices:
              - model_name: claude-test
                effective_from: 2026-01-01T00:00:00Z
                input_per_1m_micro_usd: 7_000_000
                output_per_1m_micro_usd: 21_000_000
            """
        )
        records = PricingComposer.load(
            primary_source="yaml",
            seed_dir=seed_dir,
            overrides_path=tmp_path / "no_overrides.yaml",
            effective_from=_EFFECTIVE_FROM,
        )
        keys = {(r.provider, r.model_name) for r in records}
        assert keys == {("anthropic", "claude-test")}


class TestMergeBehavior(_YamlMixin):
    def test_override_replaces_primary_on_key_collision(self, tmp_path: Path) -> None:
        # LiteLLM says $1.25/1M for gpt-5; override pins to $12.5/1M.
        # Merged output should carry the override value.
        litellm_path = self.write_litellm_data(tmp_path, _TWO_PROVIDER_LITELLM)
        override_path = self.write_override(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-5
                input_per_1m_micro_usd: 12_500_000
                output_per_1m_micro_usd: 50_000_000
                reason: "Pin to seed value"
            """,
        )
        records = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=litellm_path,
            overrides_path=override_path,
            effective_from=_EFFECTIVE_FROM,
        )
        gpt5 = next(r for r in records if r.model_name == "gpt-5")
        assert gpt5.input_per_1m_micro_usd == 12_500_000
        assert gpt5.pricing_source == "override"

    def test_primary_passes_through_when_no_override(self, tmp_path: Path) -> None:
        litellm_path = self.write_litellm_data(tmp_path, _TWO_PROVIDER_LITELLM)
        records = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=litellm_path,
            overrides_path=tmp_path / "no_overrides.yaml",
            effective_from=_EFFECTIVE_FROM,
        )
        anthropic = next(r for r in records if r.provider == "anthropic")
        assert anthropic.pricing_source == "litellm"

    def test_override_without_primary_match_is_still_emitted(
        self, tmp_path: Path
    ) -> None:
        # gemini-2.5-pro is in the override but NOT in our two-provider LiteLLM fixture.
        # It still ends up in the merged set — that's the "LiteLLM doesn't ship this model"
        # case (e.g. internal fine-tunes, brand-new releases).
        litellm_path = self.write_litellm_data(tmp_path, _TWO_PROVIDER_LITELLM)
        override_path = self.write_override(
            tmp_path,
            """
            overrides:
              - provider: google
                model_name: gemini-2.5-pro
                input_per_1m_micro_usd: 1_250_000
                output_per_1m_micro_usd: 5_000_000
                reason: "Not in LiteLLM yet"
            """,
        )
        records = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=litellm_path,
            overrides_path=override_path,
            effective_from=_EFFECTIVE_FROM,
        )
        gemini = next(
            r
            for r in records
            if r.provider == "google" and r.model_name == "gemini-2.5-pro"
        )
        assert gemini.pricing_source == "override"

    def test_merge_is_stable_under_repeated_load(self, tmp_path: Path) -> None:
        # Re-running with the same inputs and same effective_from must
        # produce byte-equivalent records. The composer is stateless;
        # this just guards against accidental mutation across reads.
        litellm_path = self.write_litellm_data(tmp_path, _TWO_PROVIDER_LITELLM)
        override_path = self.write_override(
            tmp_path,
            """
            overrides:
              - provider: openai
                model_name: gpt-5
                input_per_1m_micro_usd: 12_500_000
                output_per_1m_micro_usd: 50_000_000
                reason: "stability check"
            """,
        )
        first = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=litellm_path,
            overrides_path=override_path,
            effective_from=_EFFECTIVE_FROM,
        )
        second = PricingComposer.load(
            primary_source="litellm",
            litellm_data_path=litellm_path,
            overrides_path=override_path,
            effective_from=_EFFECTIVE_FROM,
        )
        # Compare value-equality (ignore IDs / created_at which are per-instance).
        first_view = {
            (r.provider, r.model_name, r.region): (
                r.input_per_1m_micro_usd,
                r.output_per_1m_micro_usd,
                r.cached_input_per_1m_micro_usd,
                r.context_window_tokens,
                r.pricing_source,
                r.pricing_version,
            )
            for r in first
        }
        second_view = {
            (r.provider, r.model_name, r.region): (
                r.input_per_1m_micro_usd,
                r.output_per_1m_micro_usd,
                r.cached_input_per_1m_micro_usd,
                r.context_window_tokens,
                r.pricing_source,
                r.pricing_version,
            )
            for r in second
        }
        assert first_view == second_view


class TestMigrationCutoverIntegration:
    """End-to-end against the shipped vendored JSON + migration override file.

    Pins the behaviour-preservation invariant the PRD §5 requires: for
    every (provider, model_name, region) currently in the YAML seeds,
    the composer output values must match the seed values byte-identically
    (because the migration override file carries the seed values for
    every divergent or missing key).
    """

    def test_every_yaml_seed_key_still_billing_at_seed_values(self) -> None:
        from agent_runtime.pricing.seed_loader import PricingSeedLoader

        seed_records = PricingSeedLoader.load_all()
        composed = PricingComposer.load(
            primary_source="litellm",
            effective_from=_EFFECTIVE_FROM,
        )
        composed_index: dict[tuple[str, str, str], ModelPricingRecord] = {
            (r.provider, r.model_name, r.region): r for r in composed
        }
        for seed in seed_records:
            key = (seed.provider, seed.model_name, seed.region)
            assert key in composed_index, (
                f"YAML seed key {key} disappeared from composed output"
            )
            composed_row = composed_index[key]
            assert composed_row.input_per_1m_micro_usd == seed.input_per_1m_micro_usd, (
                f"{key}: input rate diverged"
            )
            assert (
                composed_row.output_per_1m_micro_usd == seed.output_per_1m_micro_usd
            ), f"{key}: output rate diverged"
            assert (
                composed_row.cached_input_per_1m_micro_usd
                == seed.cached_input_per_1m_micro_usd
            ), f"{key}: cached rate diverged"
