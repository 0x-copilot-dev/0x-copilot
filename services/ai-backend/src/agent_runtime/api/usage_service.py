"""Usage query service: shared period parsing + rollup math (B4).

Used by both ``UsageApiRoutes`` (synchronous reads) and the rollup loop
(``runtime_worker/usage_rollup_loop.py``). Period semantics live in one
place so ``today`` means the same thing in the API response window and
in the rollup table refresh window.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from agent_runtime.persistence.records import (
    RuntimeRunUsageRecord,
    UsageDailyOrgRow,
    UsageDailyUserRow,
)


UsagePeriod = Literal["today", "7d", "30d", "month"]


class UsageQueryService:
    """Period-to-window translation + rollup arithmetic helpers.

    Stateless: every method is a classmethod or staticmethod. Lives in
    ``agent_runtime.api`` so both HTTP routes and the rollup worker can
    import it without crossing into runtime_worker (which would create a
    layering inversion: api shouldn't depend on worker).
    """

    @classmethod
    def parse_period(
        cls, period: UsagePeriod, *, now: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return ``(start, end)`` UTC range for ``period``.

        ``start`` is inclusive, ``end`` is exclusive. ``now`` is overridable
        for tests so a frozen-time fixture produces deterministic output.
        """

        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        end = now
        if period == "today":
            start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        elif period == "7d":
            start = datetime.combine(
                (now - timedelta(days=6)).date(), time.min, tzinfo=timezone.utc
            )
        elif period == "30d":
            start = datetime.combine(
                (now - timedelta(days=29)).date(), time.min, tzinfo=timezone.utc
            )
        elif period == "month":
            start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        else:
            raise ValueError(f"unsupported usage period: {period}")
        return start, end

    @classmethod
    def days_between(cls, start: datetime, end: datetime) -> tuple[date, ...]:
        """Return every UTC date covered by ``[start, end]`` inclusive."""

        if start.date() > end.date():
            return ()
        days: list[date] = []
        cursor = start.date()
        while cursor <= end.date():
            days.append(cursor)
            cursor += timedelta(days=1)
        return tuple(days)

    @classmethod
    def rollup_user_rows(
        cls,
        rows: Iterable[RuntimeRunUsageRecord],
        *,
        refreshed_at: datetime,
    ) -> tuple[UsageDailyUserRow, ...]:
        """Aggregate per-run rows into per-user-per-model-per-day rollups.

        Excludes rows with ``pii_purged_at IS NOT NULL`` from per-user
        aggregates so a deleted user's history doesn't leak into their
        own /usage view (B4 spec §3.2 unit-test bullet).
        """

        buckets: dict[
            tuple[str, str, date, str, str],
            _RollupBucket,
        ] = defaultdict(_RollupBucket)
        for row in rows:
            if row.pii_purged_at is not None:
                continue
            day = row.completed_at.date()
            key = (
                row.org_id,
                row.user_id,
                day,
                row.model_provider,
                row.model_name,
            )
            buckets[key].add(row)
        return tuple(
            bucket.to_user_row(
                org_id=key[0],
                user_id=key[1],
                day=key[2],
                model_provider=key[3],
                model_name=key[4],
                refreshed_at=refreshed_at,
            )
            for key, bucket in buckets.items()
        )

    @classmethod
    def rollup_org_rows(
        cls,
        rows: Iterable[RuntimeRunUsageRecord],
        *,
        refreshed_at: datetime,
    ) -> tuple[UsageDailyOrgRow, ...]:
        """Aggregate per-run rows into per-org-per-model-per-day rollups.

        Per-org includes PII-purged rows in totals (so billing aggregates
        survive deletion); the per-user table excludes them.
        """

        buckets: dict[
            tuple[str, date, str, str],
            _OrgRollupBucket,
        ] = defaultdict(_OrgRollupBucket)
        for row in rows:
            day = row.completed_at.date()
            key = (row.org_id, day, row.model_provider, row.model_name)
            buckets[key].add(row)
        return tuple(
            bucket.to_org_row(
                org_id=key[0],
                day=key[1],
                model_provider=key[2],
                model_name=key[3],
                refreshed_at=refreshed_at,
            )
            for key, bucket in buckets.items()
        )

    @classmethod
    def sum_totals(
        cls,
        rows: Sequence[Mapping[str, object]],
        *,
        keys: tuple[str, ...] = (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "total_tokens",
        ),
    ) -> dict[str, int]:
        """Sum named integer columns across a sequence of rollup rows."""

        totals = {key: 0 for key in keys}
        for row in rows:
            for key in keys:
                value = row.get(key)
                if isinstance(value, int):
                    totals[key] += value
        return totals


class _RollupBucket:
    """Mutable accumulator for per-user-per-model-per-day rollups."""

    def __init__(self) -> None:
        self.runs_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_input_tokens = 0
        self.total_tokens = 0
        self.cost_micro_usd: int | None = None

    def add(self, row: RuntimeRunUsageRecord) -> None:
        self.runs_count += 1
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.cached_input_tokens += row.cached_input_tokens
        self.total_tokens += row.total_tokens
        if row.cost_micro_usd is not None:
            self.cost_micro_usd = (self.cost_micro_usd or 0) + row.cost_micro_usd

    def to_user_row(
        self,
        *,
        org_id: str,
        user_id: str,
        day: date,
        model_provider: str,
        model_name: str,
        refreshed_at: datetime,
    ) -> UsageDailyUserRow:
        return UsageDailyUserRow(
            org_id=org_id,
            user_id=user_id,
            day=datetime.combine(day, time.min, tzinfo=timezone.utc),
            model_provider=model_provider,
            model_name=model_name,
            runs_count=self.runs_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=self.total_tokens,
            cost_micro_usd=self.cost_micro_usd,
            refreshed_at=refreshed_at,
        )


class _OrgRollupBucket(_RollupBucket):
    """Mutable accumulator that also tracks distinct user count."""

    def __init__(self) -> None:
        super().__init__()
        self._user_ids: set[str] = set()

    def add(self, row: RuntimeRunUsageRecord) -> None:  # type: ignore[override]
        super().add(row)
        self._user_ids.add(row.user_id)

    def to_org_row(
        self,
        *,
        org_id: str,
        day: date,
        model_provider: str,
        model_name: str,
        refreshed_at: datetime,
    ) -> UsageDailyOrgRow:
        return UsageDailyOrgRow(
            org_id=org_id,
            day=datetime.combine(day, time.min, tzinfo=timezone.utc),
            model_provider=model_provider,
            model_name=model_name,
            runs_count=self.runs_count,
            distinct_users=len(self._user_ids),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            total_tokens=self.total_tokens,
            cost_micro_usd=self.cost_micro_usd,
            refreshed_at=refreshed_at,
        )
