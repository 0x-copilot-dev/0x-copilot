"""Unit tests for the seed_pricing planning + apply library (P12 Step 2).

The script at ``scripts/usage/seed_pricing.py`` is a thin CLI shell
over :mod:`agent_runtime.pricing.upsert_planner`. These tests target
the library directly so they don't have to subprocess the CLI or load
the script via importlib.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.records import ModelPricingRecord
from agent_runtime.pricing.upsert_planner import (
    Disposition,
    PlannedAction,
    apply_actions,
    plan_actions,
    records_equivalent,
    summary_counts,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore


_EFFECTIVE_FROM = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _record(
    *,
    model_name: str = "claude-x",
    input_per_1m: int = 3_000_000,
    output_per_1m: int = 15_000_000,
    cached: int | None = 300_000,
    context: int | None = 1_000_000,
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
        context_window_tokens=context,
        pricing_source=pricing_source,
        pricing_version=pricing_version,
    )


class TestRecordsEquivalent:
    def test_identical_records_are_equivalent(self) -> None:
        assert records_equivalent(_record(), _record())

    def test_differing_rate_breaks_equivalence(self) -> None:
        assert not records_equivalent(
            _record(input_per_1m=3_000_000), _record(input_per_1m=3_100_000)
        )

    def test_differing_pricing_source_breaks_equivalence(self) -> None:
        assert not records_equivalent(
            _record(pricing_source="litellm"), _record(pricing_source="override")
        )

    def test_differing_pricing_version_breaks_equivalence(self) -> None:
        assert not records_equivalent(
            _record(pricing_version="litellm-2026-05-11"),
            _record(pricing_version="litellm-2026-05-12"),
        )

    def test_effective_from_does_not_affect_equivalence(self) -> None:
        # Re-ingesting the same values at a later timestamp is a no-op.
        assert records_equivalent(
            _record(effective_from=_EFFECTIVE_FROM),
            _record(effective_from=_EFFECTIVE_FROM + timedelta(minutes=5)),
        )


class TestPlanActions:
    @pytest.mark.asyncio
    async def test_no_existing_row_yields_insert(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        plan = await plan_actions(persistence, [_record()])
        assert len(plan) == 1
        assert plan[0].disposition == Disposition.INSERT_NEW
        assert plan[0].existing is None

    @pytest.mark.asyncio
    async def test_matching_existing_row_yields_no_change(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(_record())
        plan = await plan_actions(persistence, [_record()])
        assert plan[0].disposition == Disposition.NO_CHANGE

    @pytest.mark.asyncio
    async def test_differing_values_yield_close_and_insert(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(input_per_1m=3_000_000, effective_from=_EFFECTIVE_FROM)
        )
        plan = await plan_actions(
            persistence,
            [_record(input_per_1m=5_000_000, effective_from=_EFFECTIVE_FROM)],
        )
        assert plan[0].disposition == Disposition.CLOSE_AND_INSERT
        # New effective_from must be strictly greater than the existing
        # one (Postgres partial unique index requirement); composer's
        # minute-floored stamp collided exactly, so it was bumped.
        assert plan[0].record.effective_from > _EFFECTIVE_FROM

    @pytest.mark.asyncio
    async def test_differing_values_with_later_effective_from_not_bumped(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(input_per_1m=3_000_000, effective_from=_EFFECTIVE_FROM)
        )
        later = _EFFECTIVE_FROM + timedelta(hours=1)
        plan = await plan_actions(
            persistence,
            [_record(input_per_1m=5_000_000, effective_from=later)],
        )
        assert plan[0].disposition == Disposition.CLOSE_AND_INSERT
        assert plan[0].record.effective_from == later  # no bump needed


class TestApplyActions:
    @pytest.mark.asyncio
    async def test_apply_inserts_new_rows(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        plan = await plan_actions(persistence, [_record(model_name="claude-x")])
        await apply_actions(persistence, plan)
        existing = await persistence.lookup_pricing(
            provider="anthropic",
            model_name="claude-x",
            region="global",
            at=_EFFECTIVE_FROM + timedelta(minutes=1),
        )
        assert existing is not None
        assert existing.input_per_1m_micro_usd == 3_000_000

    @pytest.mark.asyncio
    async def test_apply_skips_no_change_actions(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(_record())
        plan = await plan_actions(persistence, [_record()])
        before_count = len(persistence.pricing_rows)
        await apply_actions(persistence, plan)
        assert len(persistence.pricing_rows) == before_count

    @pytest.mark.asyncio
    async def test_apply_close_and_insert_closes_prior_row(self) -> None:
        persistence = InMemoryRuntimeApiStore()
        await persistence.upsert_pricing(
            _record(input_per_1m=3_000_000, effective_from=_EFFECTIVE_FROM)
        )
        plan = await plan_actions(
            persistence,
            [_record(input_per_1m=5_000_000, effective_from=_EFFECTIVE_FROM)],
        )
        await apply_actions(persistence, plan)
        active_rows = [
            row
            for row in persistence.pricing_rows
            if row.effective_until is None
            and row.provider == "anthropic"
            and row.model_name == "claude-x"
        ]
        assert len(active_rows) == 1
        assert active_rows[0].input_per_1m_micro_usd == 5_000_000

    @pytest.mark.asyncio
    async def test_idempotent_when_run_twice(self) -> None:
        # Two consecutive applies with the same composed output must
        # not duplicate rows or churn the catalog.
        persistence = InMemoryRuntimeApiStore()
        first_plan = await plan_actions(persistence, [_record()])
        await apply_actions(persistence, first_plan)
        before = list(persistence.pricing_rows)
        second_plan = await plan_actions(persistence, [_record()])
        await apply_actions(persistence, second_plan)
        # Second plan should be all-no-change; no new rows.
        assert len(persistence.pricing_rows) == len(before)
        assert all(
            action.disposition == Disposition.NO_CHANGE for action in second_plan
        )


class TestSummaryCounts:
    def test_counts_mixed_plan(self) -> None:
        plan = [
            PlannedAction(
                record=_record(),
                existing=None,
                disposition=Disposition.INSERT_NEW,
            ),
            PlannedAction(
                record=_record(),
                existing=_record(),
                disposition=Disposition.NO_CHANGE,
            ),
            PlannedAction(
                record=_record(input_per_1m=5_000_000),
                existing=_record(),
                disposition=Disposition.CLOSE_AND_INSERT,
            ),
        ]
        counts = summary_counts(plan)
        assert counts[Disposition.INSERT_NEW] == 1
        assert counts[Disposition.NO_CHANGE] == 1
        assert counts[Disposition.CLOSE_AND_INSERT] == 1
