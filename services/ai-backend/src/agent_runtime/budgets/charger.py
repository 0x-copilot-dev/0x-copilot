"""Post-run charge: CAS UPDATE on usage_budget_state, idempotent on run_id.

Called from ``_record_run_usage`` after the run-level row is written.
Best-effort: a charge failure must not break the run lifecycle. The
``IDEMPOTENT_NOOP`` outcome (matching ``last_charged_run_id``) means a
worker retry is hitting the same run a second time — perfectly normal,
no work needed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

from agent_runtime.budgets.period import BudgetPeriodCalculator
from agent_runtime.persistence.records import (
    BudgetReservationRecord,
    BudgetStatus,
    BudgetWithState,
    ChargeOutcome,
)


_LOG = logging.getLogger(__name__)
_MAX_CAS_RETRIES = 5


class BudgetCharger:
    """Apply observed run usage against every matching budget.

    Charges are summed across all budgets that match (org-level + the
    user's own budget both fire). Each budget's CAS update is independent;
    a transient failure on one doesn't poison the others.
    """

    def __init__(self, persistence: object) -> None:
        self._persistence = persistence

    async def charge_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        observed_micro_usd: int | None,
        observed_tokens: int,
        reservations: Sequence[BudgetReservationRecord] = (),
        now: datetime | None = None,
    ) -> dict[str, ChargeOutcome]:
        """Apply the observed spend; return the outcome per matching budget."""

        if now is None:
            now = datetime.now(timezone.utc)
        budgets = await self._persistence.lookup_budgets_for_run(
            org_id=org_id, user_id=user_id
        )
        outcomes: dict[str, ChargeOutcome] = {}
        for entry in budgets:
            if entry.budget.status is not BudgetStatus.ACTIVE:
                continue
            outcomes[entry.budget.id] = await self._charge_one(
                entry=entry,
                run_id=run_id,
                observed_micro_usd=observed_micro_usd,
                observed_tokens=observed_tokens,
                now=now,
            )
        # Best-effort: consume each reservation so the next reaper pass
        # doesn't mistake it for an abandoned reservation. A failure here
        # is fine — the reaper will purge it once expires_at passes.
        for reservation in reservations:
            try:
                await self._persistence.consume_budget_reservation(
                    reservation_id=reservation.reservation_id,
                    now=now,
                )
            except Exception:
                _LOG.warning(
                    "budget_reservation_consume_failed",
                    extra={
                        "metadata": {
                            "reservation_id": reservation.reservation_id,
                            "run_id": run_id,
                        }
                    },
                    exc_info=True,
                )
        return outcomes

    async def _charge_one(
        self,
        *,
        entry: BudgetWithState,
        run_id: str,
        observed_micro_usd: int | None,
        observed_tokens: int,
        now: datetime,
    ) -> ChargeOutcome:
        window = BudgetPeriodCalculator.window(entry.budget.period, now=now)
        delta_micro = observed_micro_usd or 0
        # Retry on row_version drift. A drift means a *different* run
        # charged in the meantime — the worker just re-reads and tries
        # again. Last-charged-run-id idempotency guard means our own
        # retry can never double-charge.
        for _attempt in range(_MAX_CAS_RETRIES):
            outcome = await self._persistence.charge_budget(
                budget_id=entry.budget.id,
                period_start=window.period_start,
                period_end=window.period_end,
                delta_micro_usd=delta_micro,
                delta_tokens=observed_tokens,
                run_id=run_id,
                now=now,
            )
            if outcome is not ChargeOutcome.EXHAUSTED_RETRIES:
                return outcome
        _LOG.warning(
            "budget_charge_exhausted_retries",
            extra={
                "metadata": {"budget_id": entry.budget.id, "run_id": run_id},
            },
        )
        return ChargeOutcome.EXHAUSTED_RETRIES
