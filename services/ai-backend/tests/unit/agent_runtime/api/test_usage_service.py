"""Unit tests for B4's UsageQueryService — period parsing + rollup math."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.persistence.records import RuntimeRunUsageRecord


def _run(
    *,
    org_id: str = "org_a",
    user_id: str = "user_1",
    conversation_id: str = "conv_1",
    run_id: str = "run-1",
    model_provider: str = "openai",
    model_name: str = "gpt-5.4-mini",
    completed_at: datetime,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cached_input_tokens: int = 0,
    total_tokens: int | None = None,
    cost_micro_usd: int | None = None,
    pii_purged_at: datetime | None = None,
) -> RuntimeRunUsageRecord:
    return RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        model_provider=model_provider,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=total_tokens
        if total_tokens is not None
        else input_tokens + output_tokens,
        chunk_count=1,
        duration_ms=1000,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        status="completed",
        cost_micro_usd=cost_micro_usd,
        pii_purged_at=pii_purged_at,
    )


class TestParsePeriod:
    def test_today_window_is_midnight_utc_to_now(self) -> None:
        now = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)
        start, end = UsageQueryService.parse_period("today", now=now)
        assert start == datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc)
        assert end == now

    def test_7d_window_inclusive_of_today(self) -> None:
        now = datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)
        start, end = UsageQueryService.parse_period("7d", now=now)
        # 7 days inclusive: today + 6 days back.
        assert start.date() == date(2026, 4, 28)
        assert start.time() == time.min
        assert end == now

    def test_30d_window(self) -> None:
        now = datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc)
        start, end = UsageQueryService.parse_period("30d", now=now)
        assert (end.date() - start.date()).days == 29

    def test_month_window_starts_at_first(self) -> None:
        now = datetime(2026, 5, 17, 9, 12, tzinfo=timezone.utc)
        start, end = UsageQueryService.parse_period("month", now=now)
        assert start == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
        assert end == now

    def test_unsupported_period_raises(self) -> None:
        try:
            UsageQueryService.parse_period("year")  # type: ignore[arg-type]
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_naive_now_treated_as_utc(self) -> None:
        # Defensive: callers should pass aware datetimes, but tests cover
        # the naive→UTC fallback so production logs don't blow up on a
        # legacy caller.
        naive = datetime(2026, 5, 4, 14, 30)
        start, end = UsageQueryService.parse_period("today", now=naive)
        assert start.tzinfo is timezone.utc
        assert end.tzinfo is timezone.utc


class TestRollupArithmetic:
    def test_rollup_sums_per_user_per_model_per_day(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(completed_at=completed, input_tokens=10, output_tokens=20),
            _run(
                completed_at=completed.replace(hour=14),
                input_tokens=5,
                output_tokens=7,
            ),
            _run(
                completed_at=completed,
                user_id="user_2",
                input_tokens=1,
                output_tokens=2,
            ),
        )
        rollups = UsageQueryService.rollup_user_rows(
            rows, refreshed_at=datetime.now(timezone.utc)
        )
        # 2 distinct (user, model, day) buckets — user_1 sums to 15+27, user_2 to 1+2.
        assert len(rollups) == 2
        by_user = {row.user_id: row for row in rollups}
        assert by_user["user_1"].input_tokens == 15
        assert by_user["user_1"].output_tokens == 27
        assert by_user["user_1"].runs_count == 2
        assert by_user["user_2"].runs_count == 1

    def test_pii_purged_excluded_from_user_rollup(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(
                completed_at=completed,
                input_tokens=10,
                output_tokens=20,
                pii_purged_at=datetime.now(timezone.utc),
            ),
        )
        rollups = UsageQueryService.rollup_user_rows(
            rows, refreshed_at=datetime.now(timezone.utc)
        )
        assert rollups == ()

    def test_pii_purged_included_in_org_rollup(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(
                completed_at=completed,
                input_tokens=10,
                output_tokens=20,
                pii_purged_at=datetime.now(timezone.utc),
            ),
        )
        rollups = UsageQueryService.rollup_org_rows(
            rows, refreshed_at=datetime.now(timezone.utc)
        )
        assert len(rollups) == 1
        assert rollups[0].input_tokens == 10
        assert rollups[0].distinct_users == 1

    def test_org_rollup_distinct_users(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(completed_at=completed, user_id="u1"),
            _run(completed_at=completed, user_id="u2"),
            _run(completed_at=completed, user_id="u1"),
        )
        rollups = UsageQueryService.rollup_org_rows(
            rows, refreshed_at=datetime.now(timezone.utc)
        )
        assert len(rollups) == 1
        assert rollups[0].distinct_users == 2
        assert rollups[0].runs_count == 3

    def test_rollup_idempotent(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(completed_at=completed, input_tokens=10, output_tokens=20),
            _run(
                completed_at=completed.replace(hour=14),
                input_tokens=5,
                output_tokens=7,
            ),
        )
        refreshed = datetime.now(timezone.utc)
        first = UsageQueryService.rollup_user_rows(rows, refreshed_at=refreshed)
        second = UsageQueryService.rollup_user_rows(rows, refreshed_at=refreshed)
        assert [r.model_dump() for r in first] == [r.model_dump() for r in second]

    def test_cost_aggregates_only_when_priced(self) -> None:
        completed = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
        rows = (
            _run(completed_at=completed, cost_micro_usd=100),
            _run(
                completed_at=completed,
                user_id="u2",
                cost_micro_usd=None,
            ),
        )
        rollups = UsageQueryService.rollup_user_rows(
            rows, refreshed_at=datetime.now(timezone.utc)
        )
        by_user = {row.user_id: row for row in rollups}
        assert by_user["user_1"].cost_micro_usd == 100
        # Unpriced rows leave the bucket cost None.
        assert by_user["u2"].cost_micro_usd is None


class TestDaysBetween:
    def test_inclusive(self) -> None:
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        end = datetime(2026, 5, 3, tzinfo=timezone.utc)
        assert UsageQueryService.days_between(start, end) == (
            date(2026, 5, 1),
            date(2026, 5, 2),
            date(2026, 5, 3),
        )

    def test_empty_when_inverted(self) -> None:
        start = datetime(2026, 5, 3, tzinfo=timezone.utc)
        end = datetime(2026, 5, 1, tzinfo=timezone.utc)
        assert UsageQueryService.days_between(start, end) == ()
