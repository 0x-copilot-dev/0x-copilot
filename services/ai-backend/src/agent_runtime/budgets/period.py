"""Pure UTC window calculation for budget periods.

UTC midnight for ``day``; first-of-month UTC for ``month``. ``end`` is
exclusive in the sense that a charge with ``completed_at == end`` rolls
into the next period — but the SQL uses ``BETWEEN start AND end`` with
DATE columns, so end is represented as the inclusive last day.

Both forms are returned (datetime + date) because the charge layer
queries ``period_start DATE`` and the API exposes ``period_start /
period_end`` as datetimes.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from agent_runtime.persistence.records import BudgetPeriod


@dataclass(frozen=True)
class BudgetWindow:
    """Inclusive start, inclusive end (DATE) plus exclusive end datetime."""

    period_start: date
    period_end: date
    period_end_exclusive: datetime  # one micro past the last second of period_end


class BudgetPeriodCalculator:
    """Map ``(period, now)`` to the current budget window in UTC."""

    @classmethod
    def window(
        cls,
        period: BudgetPeriod,
        *,
        now: datetime | None = None,
    ) -> BudgetWindow:
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if period is BudgetPeriod.DAY:
            start = now.date()
            end = start
        elif period is BudgetPeriod.MONTH:
            start = date(now.year, now.month, 1)
            last_day = calendar.monthrange(now.year, now.month)[1]
            end = date(now.year, now.month, last_day)
        else:  # pragma: no cover - StrEnum is exhaustive
            raise ValueError(f"unsupported budget period: {period}")
        end_exclusive = datetime(
            end.year, end.month, end.day, tzinfo=timezone.utc
        ) + timedelta(days=1)
        return BudgetWindow(
            period_start=start,
            period_end=end,
            period_end_exclusive=end_exclusive,
        )
