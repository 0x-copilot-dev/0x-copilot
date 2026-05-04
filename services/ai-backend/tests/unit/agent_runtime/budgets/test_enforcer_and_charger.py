"""B7 — preflight + post-charge against the in-memory adapter.

Covers:
- Allow / Warn / Deny classification.
- Most-restrictive aggregation (Deny > Warn > Allow).
- Reservation flow: two concurrent runs of $0.60 each against $1.00 budget
  → first reserves and Allows, second hits the inflated headroom and Denies.
- Idempotency: re-charging the same run_id is a NOOP.
- Soft enforcement still warns even when limit is exceeded.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from agent_runtime.budgets import BudgetCharger, BudgetEnforcer
from agent_runtime.budgets.estimator import BudgetEstimate
from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetScope,
    BudgetStatus,
    ChargeOutcome,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.async_runtime_api_store import (
    AsyncInMemoryRuntimeApiStore,
)


_NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _budget(
    *,
    org_id: str = "org_a",
    user_id: str | None = "user_1",
    scope: BudgetScope = BudgetScope.USER,
    period: BudgetPeriod = BudgetPeriod.DAY,
    enforcement: BudgetEnforcement = BudgetEnforcement.HARD,
    limit_micro_usd: int | None = 1_000_000,  # $1.00
    limit_tokens: int | None = None,
    status: BudgetStatus = BudgetStatus.ACTIVE,
) -> BudgetRecord:
    return BudgetRecord(
        org_id=org_id,
        user_id=user_id,
        scope=scope,
        period=period,
        enforcement=enforcement,
        limit_micro_usd=limit_micro_usd,
        limit_tokens=limit_tokens,
        status=status,
        created_by_user_id="user_1",
    )


def _estimate(
    *, micro_usd: int = 600_000, input_tokens: int = 500, output_tokens: int = 100
) -> BudgetEstimate:
    return BudgetEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micro_usd=micro_usd,
    )


@pytest.fixture
def store() -> InMemoryRuntimeApiStore:
    return InMemoryRuntimeApiStore()


@pytest.fixture
def persistence(store: InMemoryRuntimeApiStore) -> AsyncInMemoryRuntimeApiStore:
    return AsyncInMemoryRuntimeApiStore(store)


class TestPreflightDecisions:
    @pytest.mark.asyncio
    async def test_allow_when_no_budgets_configured(
        self, persistence: AsyncInMemoryRuntimeApiStore
    ) -> None:
        from agent_runtime.budgets import BudgetPreflightAllow

        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(),
            now=_NOW,
        )
        assert isinstance(decision, BudgetPreflightAllow)
        assert decision.reservations == ()

    @pytest.mark.asyncio
    async def test_allow_when_estimate_within_remaining(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        from agent_runtime.budgets import BudgetPreflightAllow

        store.create_budget(_budget(limit_micro_usd=1_000_000))
        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        assert isinstance(decision, BudgetPreflightAllow)
        # A reservation was placed against the budget.
        assert len(decision.reservations) == 1

    @pytest.mark.asyncio
    async def test_deny_when_hard_cap_would_be_exceeded(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        from agent_runtime.budgets import BudgetPreflightDeny

        store.create_budget(
            _budget(limit_micro_usd=500_000, enforcement=BudgetEnforcement.HARD)
        )
        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        assert isinstance(decision, BudgetPreflightDeny)
        assert decision.reason == "budget_exceeded"

    @pytest.mark.asyncio
    async def test_warn_when_soft_cap_would_be_exceeded(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        from agent_runtime.budgets import BudgetPreflightWarn

        store.create_budget(
            _budget(limit_micro_usd=500_000, enforcement=BudgetEnforcement.SOFT)
        )
        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        assert isinstance(decision, BudgetPreflightWarn)

    @pytest.mark.asyncio
    async def test_disabled_budget_short_circuits_to_allow(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        from agent_runtime.budgets import BudgetPreflightAllow

        store.create_budget(
            _budget(
                limit_micro_usd=1, status=BudgetStatus.DISABLED
            )  # would deny if active
        )
        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        assert isinstance(decision, BudgetPreflightAllow)


class TestConcurrentReservation:
    @pytest.mark.asyncio
    async def test_two_concurrent_runs_against_one_dollar_each_admit_one_each(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        # $1.00 budget remaining; each run estimates $0.60. Without
        # reservations, both would Allow. With reservations, the second
        # Denies because the first reservation is now part of "current spend".
        from agent_runtime.budgets import BudgetPreflightAllow, BudgetPreflightDeny

        store.create_budget(_budget(limit_micro_usd=1_000_000))
        enforcer = BudgetEnforcer(persistence)

        first = await enforcer.preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        second = await enforcer.preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-2",
            estimate=_estimate(micro_usd=600_000),
            now=_NOW,
        )
        assert isinstance(first, BudgetPreflightAllow)
        assert isinstance(second, BudgetPreflightDeny)


class TestCharger:
    @pytest.mark.asyncio
    async def test_charge_applies_observed_spend(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        budget = store.create_budget(_budget(limit_micro_usd=1_000_000))
        outcomes = await BudgetCharger(persistence).charge_run(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            observed_micro_usd=400_000,
            observed_tokens=600,
            now=_NOW,
        )
        assert outcomes[budget.id] is ChargeOutcome.APPLIED

    @pytest.mark.asyncio
    async def test_same_run_id_is_idempotent(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        budget = store.create_budget(_budget(limit_micro_usd=1_000_000))
        charger = BudgetCharger(persistence)
        first = await charger.charge_run(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            observed_micro_usd=400_000,
            observed_tokens=600,
            now=_NOW,
        )
        second = await charger.charge_run(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            observed_micro_usd=400_000,
            observed_tokens=600,
            now=_NOW,
        )
        assert first[budget.id] is ChargeOutcome.APPLIED
        assert second[budget.id] is ChargeOutcome.IDEMPOTENT_NOOP
        # Spend recorded once, not twice.
        state_key = (budget.id, date(2026, 5, 4).isoformat())
        assert store.budget_states[state_key].current_spend_micro_usd == 400_000

    @pytest.mark.asyncio
    async def test_consume_reservation_marks_it_consumed(
        self,
        store: InMemoryRuntimeApiStore,
        persistence: AsyncInMemoryRuntimeApiStore,
    ) -> None:
        budget = store.create_budget(_budget(limit_micro_usd=1_000_000))
        decision = await BudgetEnforcer(persistence).preflight(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            estimate=_estimate(micro_usd=400_000),
            now=_NOW,
        )
        from agent_runtime.budgets import BudgetPreflightAllow

        assert isinstance(decision, BudgetPreflightAllow)
        await BudgetCharger(persistence).charge_run(
            org_id="org_a",
            user_id="user_1",
            run_id="run-1",
            observed_micro_usd=400_000,
            observed_tokens=600,
            reservations=decision.reservations,
            now=_NOW,
        )
        assert all(
            r.consumed_at is not None for r in store.budget_reservations.values()
        )
        # Suppress unused-name warnings
        _ = budget
