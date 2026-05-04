"""B7 — UTC window math for budget periods."""

from __future__ import annotations

from datetime import date, datetime, timezone

from agent_runtime.budgets.period import BudgetPeriodCalculator
from agent_runtime.persistence.records import BudgetPeriod


class TestDayWindow:
    def test_day_window_starts_and_ends_on_same_day(self) -> None:
        now = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)
        window = BudgetPeriodCalculator.window(BudgetPeriod.DAY, now=now)
        assert window.period_start == date(2026, 5, 4)
        assert window.period_end == date(2026, 5, 4)

    def test_day_window_rolls_at_utc_midnight(self) -> None:
        # 23:59 May 4 → window is May 4. One minute later is May 5.
        before = datetime(2026, 5, 4, 23, 59, tzinfo=timezone.utc)
        after = datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc)
        assert BudgetPeriodCalculator.window(
            BudgetPeriod.DAY, now=before
        ).period_start == date(2026, 5, 4)
        assert BudgetPeriodCalculator.window(
            BudgetPeriod.DAY, now=after
        ).period_start == date(2026, 5, 5)


class TestMonthWindow:
    def test_month_window_starts_at_first_of_month(self) -> None:
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        window = BudgetPeriodCalculator.window(BudgetPeriod.MONTH, now=now)
        assert window.period_start == date(2026, 5, 1)
        assert window.period_end == date(2026, 5, 31)

    def test_month_window_handles_february_in_leap_year(self) -> None:
        now = datetime(2028, 2, 15, tzinfo=timezone.utc)
        window = BudgetPeriodCalculator.window(BudgetPeriod.MONTH, now=now)
        assert window.period_start == date(2028, 2, 1)
        assert window.period_end == date(2028, 2, 29)

    def test_naive_now_is_treated_as_utc(self) -> None:
        # Defensive: a naive datetime must not silently change the window.
        naive = datetime(2026, 5, 4, 14, 30)  # no tzinfo
        window = BudgetPeriodCalculator.window(BudgetPeriod.DAY, now=naive)
        assert window.period_start == date(2026, 5, 4)
