"""Pre-run budget preflight: Allow / Warn / Deny + reservation.

The enforcer runs at the top of ``RuntimeRunHandler.handle()``. It:

  1. Looks up active budgets for ``(org_id, user_id)``.
  2. For each budget, computes remaining headroom against current spend
     PLUS existing-but-unconsumed reservations.
  3. Decides:
     - estimate <= remaining → Allow + reserve.
     - estimate > remaining and enforcement = soft → Warn + reserve.
     - estimate > remaining and enforcement = hard → Deny (no reservation).
  4. The most restrictive decision across all matching budgets wins
     (Deny > Warn > Allow). All matched budgets get a reservation when
     the final outcome is Allow or Warn.

Single-tenant deploys with no budgets configured short-circuit on the
empty ``lookup_budgets_for_run`` result — zero added latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union

from agent_runtime.budgets.estimator import BudgetEstimate
from agent_runtime.budgets.period import BudgetPeriodCalculator, BudgetWindow
from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetRecord,
    BudgetReservationRecord,
    BudgetStatus,
    BudgetWithState,
)


@dataclass(frozen=True)
class BudgetPreflightAllow:
    """Budget did not block; the (optional) reservations are recorded."""

    reservations: tuple[BudgetReservationRecord, ...] = ()


@dataclass(frozen=True)
class BudgetPreflightWarn:
    """Budget would be exceeded under soft enforcement; run proceeds."""

    budget: BudgetRecord
    current_micro_usd: int
    current_tokens: int
    estimated_micro_usd: int | None
    estimated_tokens: int
    reservations: tuple[BudgetReservationRecord, ...] = ()


@dataclass(frozen=True)
class BudgetPreflightDeny:
    """Hard cap would be exceeded; the run is rejected before the LLM call."""

    budget: BudgetRecord
    current_micro_usd: int
    current_tokens: int
    estimated_micro_usd: int | None
    estimated_tokens: int
    reason: str = "budget_exceeded"


BudgetDecision = Union[BudgetPreflightAllow, BudgetPreflightWarn, BudgetPreflightDeny]


class BudgetEnforcer:
    """Run preflight against all budgets matching ``(org_id, user_id)``.

    The enforcer is constructed once per worker. ``preflight`` is called
    on the handler hot path; the cost is one DB round-trip when budgets
    exist for the tenant, zero otherwise.
    """

    def __init__(self, persistence: object) -> None:
        self._persistence = persistence

    async def preflight(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        estimate: BudgetEstimate,
        now: datetime | None = None,
    ) -> BudgetDecision:
        if now is None:
            now = datetime.now(timezone.utc)
        budgets = await self._persistence.lookup_budgets_for_run(
            org_id=org_id, user_id=user_id
        )
        active = tuple(
            entry for entry in budgets if entry.budget.status is BudgetStatus.ACTIVE
        )
        if not active:
            return BudgetPreflightAllow()

        # First pass: classify each budget independently.
        decisions: list[BudgetDecision] = []
        for entry in active:
            window = BudgetPeriodCalculator.window(entry.budget.period, now=now)
            current_micro, current_tokens = self._current_spend(entry)
            est_micro = estimate.cost_micro_usd or 0
            est_tokens = estimate.input_tokens + estimate.output_tokens
            within_micro = self._within_micro_limit(
                budget=entry.budget,
                current_micro=current_micro,
                est_micro=est_micro,
            )
            within_tokens = self._within_token_limit(
                budget=entry.budget,
                current_tokens=current_tokens,
                est_tokens=est_tokens,
            )
            if within_micro and within_tokens:
                decisions.append(
                    await self._reserve_and_allow(
                        entry=entry,
                        window=window,
                        run_id=run_id,
                        est_micro=est_micro,
                        est_tokens=est_tokens,
                        now=now,
                    )
                )
                continue
            if entry.budget.enforcement is BudgetEnforcement.SOFT:
                decisions.append(
                    BudgetPreflightWarn(
                        budget=entry.budget,
                        current_micro_usd=current_micro,
                        current_tokens=current_tokens,
                        estimated_micro_usd=estimate.cost_micro_usd,
                        estimated_tokens=est_tokens,
                        reservations=(),
                    )
                )
                continue
            decisions.append(
                BudgetPreflightDeny(
                    budget=entry.budget,
                    current_micro_usd=current_micro,
                    current_tokens=current_tokens,
                    estimated_micro_usd=estimate.cost_micro_usd,
                    estimated_tokens=est_tokens,
                )
            )

        # Second pass: most restrictive wins (Deny > Warn > Allow).
        return self._aggregate(decisions)

    @classmethod
    def _aggregate(cls, decisions: list[BudgetDecision]) -> BudgetDecision:
        deny = next((d for d in decisions if isinstance(d, BudgetPreflightDeny)), None)
        if deny is not None:
            return deny
        warn = next((d for d in decisions if isinstance(d, BudgetPreflightWarn)), None)
        if warn is not None:
            # Carry forward the reservations of the Allow decisions so the
            # post-run charger can consume them.
            allow_reservations = tuple(
                r
                for d in decisions
                if isinstance(d, BudgetPreflightAllow)
                for r in d.reservations
            )
            return BudgetPreflightWarn(
                budget=warn.budget,
                current_micro_usd=warn.current_micro_usd,
                current_tokens=warn.current_tokens,
                estimated_micro_usd=warn.estimated_micro_usd,
                estimated_tokens=warn.estimated_tokens,
                reservations=allow_reservations,
            )
        allow_reservations = tuple(
            r
            for d in decisions
            if isinstance(d, BudgetPreflightAllow)
            for r in d.reservations
        )
        return BudgetPreflightAllow(reservations=allow_reservations)

    async def _reserve_and_allow(
        self,
        *,
        entry: BudgetWithState,
        window: BudgetWindow,
        run_id: str,
        est_micro: int,
        est_tokens: int,
        now: datetime,
    ) -> BudgetPreflightAllow:
        reservation = await self._persistence.reserve_budget(
            budget_id=entry.budget.id,
            period_start=window.period_start,
            run_id=run_id,
            reserved_micro_usd=est_micro,
            reserved_tokens=est_tokens,
            now=now,
        )
        if reservation is None:
            # Idempotent retry: the run already had a reservation; that's
            # an Allow under our atomicity contract (the prior attempt
            # made room for itself).
            return BudgetPreflightAllow()
        return BudgetPreflightAllow(reservations=(reservation,))

    @staticmethod
    def _current_spend(entry: BudgetWithState) -> tuple[int, int]:
        """Return (current_spend_micro_usd, current_spend_tokens) including reservations.

        The persistence port is expected to inflate
        ``current_spend_micro_usd`` / ``current_spend_tokens`` on the
        ``BudgetStateRecord`` it returns to include active (unconsumed)
        reservations. This puts the headroom math in one place — the
        port — instead of duplicating it here and in the SQL.
        """

        state = entry.state
        if state is None:
            return 0, 0
        return state.current_spend_micro_usd, state.current_spend_tokens

    @staticmethod
    def _within_micro_limit(
        *, budget: BudgetRecord, current_micro: int, est_micro: int
    ) -> bool:
        if budget.limit_micro_usd is None:
            return True
        return current_micro + est_micro <= budget.limit_micro_usd

    @staticmethod
    def _within_token_limit(
        *, budget: BudgetRecord, current_tokens: int, est_tokens: int
    ) -> bool:
        if budget.limit_tokens is None:
            return True
        return current_tokens + est_tokens <= budget.limit_tokens
