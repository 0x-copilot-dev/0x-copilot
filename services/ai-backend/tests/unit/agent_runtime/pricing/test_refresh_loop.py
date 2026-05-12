"""Unit tests for ``PricingRefreshLoop`` (P12 Step 3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agent_runtime.deployment.profile import (
    DeploymentFeatureToggles,
    DeploymentProfile,
)
from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.refresh_loop import (
    PricingRefreshLoop,
    _max_fractional_change,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore


_EFFECTIVE_FROM = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _toggles(*, pricing_primary_source: str = "litellm") -> DeploymentFeatureToggles:
    return DeploymentFeatureToggles(
        allow_embedded_provider_keys=True,
        allow_self_signup=True,
        allow_vendor_telemetry=True,
        default_retention_days=365,
        dev_auth_bypass_allowed=True,
        enforce_rls=False,
        require_field_level_encryption=False,
        require_kms_token_vault=False,
        siem_export_required=False,
        pricing_primary_source=pricing_primary_source,
    )


def _profile(*, pricing_primary_source: str = "litellm") -> DeploymentProfile:
    return DeploymentProfile(
        name="development",
        toggles=_toggles(pricing_primary_source=pricing_primary_source),
    )


def _record(
    *,
    model_name: str = "claude-x",
    input_per_1m: int = 3_000_000,
    output_per_1m: int = 15_000_000,
    cached: int | None = 300_000,
    pricing_source: str = "litellm",
    pricing_version: str = "litellm-2026-05-11",
    effective_from: datetime = _EFFECTIVE_FROM,
) -> ModelPricingRecord:
    return ModelPricingRecord(
        provider="anthropic",
        model_name=model_name,
        region="global",
        effective_from=effective_from,
        input_per_1m_micro_usd=input_per_1m,
        output_per_1m_micro_usd=output_per_1m,
        cached_input_per_1m_micro_usd=cached,
        context_window_tokens=1_000_000,
        pricing_source=pricing_source,
        pricing_version=pricing_version,
    )


def _write_litellm_fixture(tmp_path: Path, rows: dict[str, dict[str, object]]) -> Path:
    path = tmp_path / "model_prices.json"
    path.write_text(json.dumps(rows))
    return path


def _write_empty_overrides(tmp_path: Path) -> Path:
    path = tmp_path / "pricing_overrides.yaml"
    path.write_text("overrides: []\n")
    return path


class TestAirGappedSkip:
    @pytest.mark.asyncio
    async def test_yaml_primary_skips_refresh(self, tmp_path: Path) -> None:
        # Air-gapped deploy — there's no upstream to refresh against.
        # The loop should no-op and emit a skip log line.
        loop = PricingRefreshLoop(
            persistence=InMemoryRuntimeApiStore(),
            deployment_profile=_profile(pricing_primary_source="yaml"),
            litellm_data_path=_write_litellm_fixture(tmp_path, {}),
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        assert outcomes == ()


class TestNoChange:
    @pytest.mark.asyncio
    async def test_matching_row_is_silently_skipped(self, tmp_path: Path) -> None:
        persistence = InMemoryRuntimeApiStore()
        # Seed the persistence with a row that matches what LiteLLM ships.
        await persistence.upsert_pricing(_record(model_name="claude-sonnet-4-6"))
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-sonnet-4-6": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3e-06,
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        # The row exists and values match, so the planner returns NO_CHANGE.
        # The refresh loop maps that to a "dry_run" outcome (nothing changed,
        # nothing written, no upstream-changed log noise).
        rate_outcomes = [
            o for o in outcomes if o.record.model_name == "claude-sonnet-4-6"
        ]
        assert len(rate_outcomes) == 1
        assert rate_outcomes[0].action_taken == "dry_run"
        assert rate_outcomes[0].max_fractional_change == Decimal(0)


class TestNewModelInsertion:
    @pytest.mark.asyncio
    async def test_new_model_inserted_when_auto_apply_true(
        self, tmp_path: Path
    ) -> None:
        persistence = InMemoryRuntimeApiStore()
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-new-model": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 2e-06,
                    "output_cost_per_token": 1e-05,
                    "max_input_tokens": 500_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        outcome = next(o for o in outcomes if o.record.model_name == "claude-new-model")
        assert outcome.action_taken == "inserted_new"
        # Persistence should now have the new row.
        active = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-new-model",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(hours=2),
        )
        assert active is not None
        assert active.input_per_1m_micro_usd == 2_000_000

    @pytest.mark.asyncio
    async def test_new_model_dry_run_when_auto_apply_false(
        self, tmp_path: Path
    ) -> None:
        persistence = InMemoryRuntimeApiStore()
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-new-model": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 2e-06,
                    "output_cost_per_token": 1e-05,
                    "max_input_tokens": 500_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=False,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        outcome = next(o for o in outcomes if o.record.model_name == "claude-new-model")
        assert outcome.action_taken == "dry_run"
        # No write performed.
        active = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-new-model",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(hours=2),
        )
        assert active is None


class TestRateChange:
    @pytest.mark.asyncio
    async def test_within_sanity_applied_when_auto_apply_true(
        self, tmp_path: Path
    ) -> None:
        persistence = InMemoryRuntimeApiStore()
        # Seed at $3/1M input
        await persistence.upsert_pricing(
            _record(model_name="claude-sonnet-4-6", input_per_1m=3_000_000)
        )
        # Upstream says $3.30/1M — a 10% change, well under the 25% threshold.
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-sonnet-4-6": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3.3e-06,
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        outcome = next(
            o for o in outcomes if o.record.model_name == "claude-sonnet-4-6"
        )
        assert outcome.action_taken == "applied"
        # Active row should now have the new rate.
        active = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(hours=2),
        )
        assert active is not None
        assert active.input_per_1m_micro_usd == 3_300_000

    @pytest.mark.asyncio
    async def test_within_sanity_dry_run_when_auto_apply_false(
        self, tmp_path: Path
    ) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(model_name="claude-sonnet-4-6", input_per_1m=3_000_000)
        )
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-sonnet-4-6": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3.3e-06,
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=False,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        outcome = next(
            o for o in outcomes if o.record.model_name == "claude-sonnet-4-6"
        )
        assert outcome.action_taken == "dry_run"
        # Active row preserved at the original value.
        active = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(hours=2),
        )
        assert active is not None
        assert active.input_per_1m_micro_usd == 3_000_000


class TestSanityGuard:
    @pytest.mark.asyncio
    async def test_change_exceeding_threshold_refuses_to_apply(
        self, tmp_path: Path
    ) -> None:
        # Even with auto_apply=True a > threshold change is refused.
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(model_name="claude-sonnet-4-6", input_per_1m=3_000_000)
        )
        # Upstream says $0.30/1M — a 90% drop. Far beyond sanity.
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-sonnet-4-6": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3e-07,  # 90% lower than active
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            sanity_threshold=Decimal("0.25"),
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        outcome = next(
            o for o in outcomes if o.record.model_name == "claude-sonnet-4-6"
        )
        assert outcome.action_taken == "refused_sanity"
        # Active row preserved despite auto_apply=True.
        active = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(hours=2),
        )
        assert active is not None
        assert active.input_per_1m_micro_usd == 3_000_000

    @pytest.mark.asyncio
    async def test_sanity_does_not_block_other_records(self, tmp_path: Path) -> None:
        # One record exceeds sanity, another is within. The within-sanity
        # record applies normally; the over-sanity one is refused.
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(model_name="claude-stable", input_per_1m=3_000_000)
        )
        await persistence.upsert_pricing(
            _record(model_name="claude-erratic", input_per_1m=3_000_000)
        )
        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-stable": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3.3e-06,  # +10%
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                },
                "claude-erratic": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3e-07,  # -90%
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                },
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            sanity_threshold=Decimal("0.25"),
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        outcomes = await loop.refresh(effective_from=_EFFECTIVE_FROM)
        stable = next(o for o in outcomes if o.record.model_name == "claude-stable")
        erratic = next(o for o in outcomes if o.record.model_name == "claude-erratic")
        assert stable.action_taken == "applied"
        assert erratic.action_taken == "refused_sanity"


class TestMaxFractionalChange:
    def test_returns_largest_change_across_rate_fields(self) -> None:
        existing = _record(input_per_1m=1000, output_per_1m=2000, cached=200)
        new = _record(input_per_1m=1100, output_per_1m=2400, cached=200)
        # input: 10%, output: 20%, cached: 0% → max 20%
        result = _max_fractional_change(existing=existing, new=new)
        assert result is not None
        assert result == Decimal("0.2")

    def test_returns_none_when_no_meaningful_comparison(self) -> None:
        existing = _record(input_per_1m=0, output_per_1m=0, cached=None)
        new = _record(input_per_1m=5_000_000, output_per_1m=10_000_000, cached=None)
        # Existing is all-zero / None — no fraction computable.
        assert _max_fractional_change(existing=existing, new=new) is None

    def test_skips_none_cached_pairs(self) -> None:
        existing = _record(input_per_1m=1000, output_per_1m=2000, cached=None)
        new = _record(input_per_1m=1000, output_per_1m=2000, cached=500_000)
        # cached pair has None on one side; rates didn't change.
        result = _max_fractional_change(existing=existing, new=new)
        assert result == Decimal(0)


class TestHistoryUntouched:
    @pytest.mark.asyncio
    async def test_old_row_is_closed_not_mutated_on_apply(self, tmp_path: Path) -> None:
        # When refresh applies a change, the prior row is CLOSED via
        # effective_until — never deleted, never rewritten. Historical
        # cost rows that reference its id still resolve correctly.
        persistence = InMemoryRuntimeApiStore()
        old = _record(
            model_name="claude-sonnet-4-6",
            input_per_1m=3_000_000,
            effective_from=_EFFECTIVE_FROM - timedelta(days=30),
        )
        await persistence.upsert_pricing(old)
        old_id = persistence.pricing_rows[0].id

        data_path = _write_litellm_fixture(
            tmp_path,
            {
                "claude-sonnet-4-6": {
                    "litellm_provider": "anthropic",
                    "mode": "chat",
                    "input_cost_per_token": 3.3e-06,  # +10%, within sanity
                    "output_cost_per_token": 1.5e-05,
                    "cache_read_input_token_cost": 3e-07,
                    "max_input_tokens": 1_000_000,
                }
            },
        )
        loop = PricingRefreshLoop(
            persistence=persistence,
            deployment_profile=_profile(),
            auto_apply=True,
            litellm_data_path=data_path,
            overrides_path=_write_empty_overrides(tmp_path),
        )
        await loop.refresh()
        # Old row exists, retains its id + values, but is now closed.
        closed = next(row for row in persistence.pricing_rows if row.id == old_id)
        assert closed.input_per_1m_micro_usd == 3_000_000  # unchanged
        assert closed.effective_until is not None  # closed


class TestEnvConfig:
    def test_env_bool_parses_truthy_values(self, monkeypatch) -> None:
        from agent_runtime.pricing.refresh_loop import PricingRefreshLoopEnv

        for raw in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("PRICING_REFRESH_AUTO_APPLY", raw)
            assert (
                PricingRefreshLoopEnv.env_bool(
                    PricingRefreshLoopEnv.AUTO_APPLY, default=False
                )
                is True
            )
        for raw in ("false", "0", "no", "off"):
            monkeypatch.setenv("PRICING_REFRESH_AUTO_APPLY", raw)
            assert (
                PricingRefreshLoopEnv.env_bool(
                    PricingRefreshLoopEnv.AUTO_APPLY, default=True
                )
                is False
            )

    def test_env_bool_unset_returns_default(self, monkeypatch) -> None:
        from agent_runtime.pricing.refresh_loop import PricingRefreshLoopEnv

        monkeypatch.delenv("PRICING_REFRESH_AUTO_APPLY", raising=False)
        assert (
            PricingRefreshLoopEnv.env_bool(
                PricingRefreshLoopEnv.AUTO_APPLY, default=True
            )
            is True
        )
        assert (
            PricingRefreshLoopEnv.env_bool(
                PricingRefreshLoopEnv.AUTO_APPLY, default=False
            )
            is False
        )

    def test_env_bool_empty_string_returns_default(self, monkeypatch) -> None:
        from agent_runtime.pricing.refresh_loop import PricingRefreshLoopEnv

        monkeypatch.setenv("PRICING_REFRESH_AUTO_APPLY", "")
        assert (
            PricingRefreshLoopEnv.env_bool(
                PricingRefreshLoopEnv.AUTO_APPLY, default=True
            )
            is True
        )
