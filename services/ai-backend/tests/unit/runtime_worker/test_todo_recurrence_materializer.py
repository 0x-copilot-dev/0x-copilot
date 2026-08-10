"""Tests for ``runtime_worker.jobs.todo_recurrence_materializer``.

Coverage:

* ``RecurrenceRuleEvaluator`` — rrule (DAILY/WEEKLY ± BYDAY ± INTERVAL),
  ``every_N_days:N``, ``every_weekday``. Includes malformed-spec paths.
* ``TodoRecurrenceMaterializerLoop`` — happy-path tick, **idempotency**
  (re-running the loop twice on the same series materializes only one
  row per due date — enforced by the backend's UNIQUE constraint, which
  surfaces as ``skipped_duplicates`` in the outcome).
* ``HttpTodoRecurrenceBackendClient`` — service-token + identity headers
  always present; 5xx / network errors return an empty outcome (the
  next tick retries).
* Series deletion semantics: tombstoning the series stops future
  materializations but keeps past instances — verified via the backend
  client contract (no claim returned for a deleted series).

P3-A1 dependency: this worker depends on the backend's
``/internal/v1/todos/series/materialize-due`` endpoint + the
``(series_id, due_date)`` UNIQUE constraint on ``todos``. P3-A1 owns the
schema + the route; we mock both via a fake ``TodoRecurrenceBackendClient``
so these tests stand alone.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

from runtime_worker.jobs.todo_recurrence_materializer import (
    HttpTodoRecurrenceBackendClient,
    MaterializeOutcome,
    NullTodoRecurrenceBackendClient,
    RecurrenceRuleError,
    RecurrenceRuleEvaluator,
    TodoRecurrenceBackendClient,
    TodoRecurrenceMaterializerLoop,
)


# ---------------------------------------------------------------------------
# Rule evaluator
# ---------------------------------------------------------------------------


class TestRecurrenceRuleEvaluatorEveryWeekday:
    def test_friday_to_next_monday_skips_weekend(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-15 is a Friday.
        assert evaluator.next_due(
            rule="every_weekday", spec="", previous_due=date(2026, 5, 15)
        ) == date(2026, 5, 18)

    def test_saturday_to_monday(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-16 is a Saturday.
        assert evaluator.next_due(
            rule="every_weekday", spec="", previous_due=date(2026, 5, 16)
        ) == date(2026, 5, 18)

    def test_monday_to_tuesday(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-18 is a Monday.
        assert evaluator.next_due(
            rule="every_weekday", spec="", previous_due=date(2026, 5, 18)
        ) == date(2026, 5, 19)


class TestRecurrenceRuleEvaluatorEveryNDays:
    def test_every_3_days(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        assert evaluator.next_due(
            rule="every_N_days",
            spec="every_N_days:3",
            previous_due=date(2026, 5, 1),
        ) == date(2026, 5, 4)

    def test_every_1_day(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        assert evaluator.next_due(
            rule="every_N_days",
            spec="every_N_days:1",
            previous_due=date(2026, 5, 1),
        ) == date(2026, 5, 2)

    def test_malformed_prefix_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="every_N_days",
                spec="three_days:3",
                previous_due=date(2026, 5, 1),
            )

    def test_non_numeric_tail_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="every_N_days",
                spec="every_N_days:three",
                previous_due=date(2026, 5, 1),
            )

    def test_zero_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="every_N_days",
                spec="every_N_days:0",
                previous_due=date(2026, 5, 1),
            )


class TestRecurrenceRuleEvaluatorRRule:
    def test_freq_daily(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        assert evaluator.next_due(
            rule="rrule", spec="FREQ=DAILY", previous_due=date(2026, 5, 1)
        ) == date(2026, 5, 2)

    def test_freq_daily_with_interval(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        assert evaluator.next_due(
            rule="rrule",
            spec="FREQ=DAILY;INTERVAL=4",
            previous_due=date(2026, 5, 1),
        ) == date(2026, 5, 5)

    def test_freq_weekly_no_byday(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-18 is a Monday; +1 week = 2026-05-25.
        assert evaluator.next_due(
            rule="rrule",
            spec="FREQ=WEEKLY",
            previous_due=date(2026, 5, 18),
        ) == date(2026, 5, 25)

    def test_freq_weekly_byday_mwf_from_monday(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-18 = Mon → next BYDAY in MO/WE/FR is Wed 2026-05-20.
        assert evaluator.next_due(
            rule="rrule",
            spec="FREQ=WEEKLY;BYDAY=MO,WE,FR",
            previous_due=date(2026, 5, 18),
        ) == date(2026, 5, 20)

    def test_freq_weekly_byday_mwf_from_friday_wraps_to_next_monday(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # 2026-05-22 = Fri → next BYDAY in MO/WE/FR is Mon 2026-05-25.
        assert evaluator.next_due(
            rule="rrule",
            spec="FREQ=WEEKLY;BYDAY=MO,WE,FR",
            previous_due=date(2026, 5, 22),
        ) == date(2026, 5, 25)

    def test_freq_weekly_byday_with_interval_2(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        # Every other week, Tuesday only. From Tue 2026-05-19 → Tue 2026-06-02.
        assert evaluator.next_due(
            rule="rrule",
            spec="FREQ=WEEKLY;BYDAY=TU;INTERVAL=2",
            previous_due=date(2026, 5, 19),
        ) == date(2026, 6, 2)

    def test_missing_freq_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="rrule",
                spec="INTERVAL=2",
                previous_due=date(2026, 5, 1),
            )

    def test_unknown_byday_code_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="rrule",
                spec="FREQ=WEEKLY;BYDAY=MO,XX",
                previous_due=date(2026, 5, 1),
            )

    def test_unsupported_rrule_part_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="rrule",
                spec="FREQ=WEEKLY;COUNT=5",
                previous_due=date(2026, 5, 1),
            )

    def test_unsupported_freq_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="rrule",
                spec="FREQ=MONTHLY",
                previous_due=date(2026, 5, 1),
            )

    def test_unsupported_rule_kind_rejected(self) -> None:
        evaluator = RecurrenceRuleEvaluator()
        with pytest.raises(RecurrenceRuleError):
            evaluator.next_due(
                rule="cron", spec="0 9 * * *", previous_due=date(2026, 5, 1)
            )


# ---------------------------------------------------------------------------
# Loop — happy path + IDEMPOTENCY
# ---------------------------------------------------------------------------


class _FakeBackendClient(TodoRecurrenceBackendClient):
    """In-memory stand-in for the backend that models the UNIQUE constraint.

    A "series" here is just a ``(series_id, list of due_dates)`` plan. The
    fake mimics the backend's claim + UNIQUE-constraint behaviour: every
    time ``materialize_due`` is called, each due date the series owes is
    materialized **at most once** — re-calls report ``skipped_duplicates``
    for already-materialized rows.
    """

    def __init__(
        self,
        *,
        plan: dict[str, list[date]],
        tombstoned_series: set[str] | None = None,
    ) -> None:
        self._plan = plan
        self._tombstoned = tombstoned_series or set()
        self.materialized: dict[str, set[date]] = {sid: set() for sid in plan}
        self.calls: list[datetime] = []

    async def materialize_due(self, *, now: datetime) -> MaterializeOutcome:
        self.calls.append(now)
        materialized = 0
        skipped = 0
        series_processed = 0
        for series_id, due_dates in self._plan.items():
            if series_id in self._tombstoned:
                # Tombstoned series: no claims, no materialization.
                continue
            series_processed += 1
            for due in due_dates:
                # Only consider "due" entries (server-side filter).
                if (
                    datetime(
                        due.year,
                        due.month,
                        due.day,
                        tzinfo=timezone.utc,
                    )
                    > now
                ):
                    continue
                if due in self.materialized[series_id]:
                    skipped += 1
                    continue
                self.materialized[series_id].add(due)
                materialized += 1
        return MaterializeOutcome(
            materialized=materialized,
            skipped_duplicates=skipped,
            series_processed=series_processed,
        )


class TestTodoRecurrenceMaterializerLoop:
    @pytest.mark.asyncio
    async def test_happy_path_materializes_due_rows(self) -> None:
        client = _FakeBackendClient(
            plan={"series-a": [date(2026, 5, 17), date(2026, 5, 18)]}
        )
        loop = TodoRecurrenceMaterializerLoop(
            client=client,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        assert outcome.materialized == 2
        assert outcome.skipped_duplicates == 0
        assert outcome.series_processed == 1
        assert client.materialized["series-a"] == {
            date(2026, 5, 17),
            date(2026, 5, 18),
        }

    @pytest.mark.asyncio
    async def test_idempotency_second_tick_skips_already_materialized(self) -> None:
        """Critical invariant — UNIQUE(series_id, due_date)."""
        client = _FakeBackendClient(
            plan={"series-a": [date(2026, 5, 17), date(2026, 5, 18)]}
        )
        loop = TodoRecurrenceMaterializerLoop(
            client=client,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        first = await loop.tick_once()
        second = await loop.tick_once()
        # First tick materializes both due rows.
        assert first.materialized == 2
        assert first.skipped_duplicates == 0
        # Second tick at the same wall-clock instant: nothing new
        # materialized; both rows reported as duplicate skips. This is
        # the UNIQUE(series_id, due_date) invariant — running the
        # materializer twice creates only one row per due date.
        assert second.materialized == 0
        assert second.skipped_duplicates == 2
        # And the fake's "materialized" set still has exactly the two
        # entries from the first call — proves no second insert occurred.
        assert client.materialized["series-a"] == {
            date(2026, 5, 17),
            date(2026, 5, 18),
        }

    @pytest.mark.asyncio
    async def test_future_dates_not_yet_due_are_not_materialized(self) -> None:
        client = _FakeBackendClient(
            plan={"series-a": [date(2026, 5, 17), date(2026, 5, 25)]}
        )
        loop = TodoRecurrenceMaterializerLoop(
            client=client,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        # Only the past-due row is materialized.
        assert outcome.materialized == 1
        assert client.materialized["series-a"] == {date(2026, 5, 17)}

    @pytest.mark.asyncio
    async def test_tombstoned_series_yields_nothing(self) -> None:
        """Series deletion: tombstones future materializations.

        Past instances (already in ``client.materialized``) are NOT
        removed — that's the spec ("keeps already-materialized
        instances"). New materializations are skipped because the
        series is tombstoned.
        """
        client = _FakeBackendClient(
            plan={
                "series-live": [date(2026, 5, 17)],
                "series-tombstoned": [date(2026, 5, 17), date(2026, 5, 18)],
            },
            tombstoned_series={"series-tombstoned"},
        )
        # Pre-populate a "historical" materialization on the tombstoned
        # series — proves the worker doesn't undo past instances.
        client.materialized["series-tombstoned"].add(date(2026, 5, 1))

        loop = TodoRecurrenceMaterializerLoop(
            client=client,
            tick_seconds=60.0,
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        outcome = await loop.tick_once()
        # Only the live series materialized.
        assert outcome.materialized == 1
        assert outcome.series_processed == 1  # tombstoned not counted
        assert client.materialized["series-live"] == {date(2026, 5, 17)}
        # Tombstoned series: past instance retained, no new ones.
        assert client.materialized["series-tombstoned"] == {date(2026, 5, 1)}

    @pytest.mark.asyncio
    async def test_start_stop_is_idempotent(self) -> None:
        client = _FakeBackendClient(plan={"series-a": []})
        loop = TodoRecurrenceMaterializerLoop(
            client=client,
            tick_seconds=0.01,
            clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        )
        await loop.start()
        await loop.start()  # second start is a no-op
        await loop.stop()
        # Second stop on an already-stopped loop must not raise.
        await loop.stop()


# ---------------------------------------------------------------------------
# HTTP client — header + error handling
# ---------------------------------------------------------------------------


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Records every request the client makes; replies with the next queued response."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(500)
        return self._responses.pop(0)


class TestHttpTodoRecurrenceBackendClient:
    @pytest.mark.asyncio
    async def test_sends_service_token_and_identity_headers(self) -> None:
        transport = _CapturingTransport(
            [
                httpx.Response(
                    200,
                    json={
                        "materialized": 2,
                        "skipped_duplicates": 1,
                        "series_processed": 3,
                    },
                )
            ]
        )
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpTodoRecurrenceBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.materialize_due(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
            )
        assert outcome == MaterializeOutcome(
            materialized=2, skipped_duplicates=1, series_processed=3
        )
        request = transport.requests[0]
        # CLAUDE.md auth rule: service-token caller MUST send both
        # x-enterprise-org-id and x-enterprise-user-id.
        assert request.headers["x-enterprise-service-token"] == "secret-token"
        assert request.headers["x-enterprise-org-id"] == "system"
        assert request.headers["x-enterprise-user-id"] == "system"
        assert request.url.path == "/internal/v1/todos/series/materialize-due"

    @pytest.mark.asyncio
    async def test_5xx_returns_empty_outcome(self) -> None:
        transport = _CapturingTransport([httpx.Response(503)])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpTodoRecurrenceBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.materialize_due(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
            )
        assert outcome == MaterializeOutcome()

    @pytest.mark.asyncio
    async def test_non_dict_body_returns_empty_outcome(self) -> None:
        transport = _CapturingTransport([httpx.Response(200, json=["unexpected"])])
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = HttpTodoRecurrenceBackendClient(
                http_client=http_client,
                backend_url="http://backend:8100",
                service_token="secret-token",
            )
            outcome = await client.materialize_due(
                now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
            )
        assert outcome == MaterializeOutcome()


class TestNullTodoRecurrenceBackendClient:
    @pytest.mark.asyncio
    async def test_returns_empty_outcome(self) -> None:
        client = NullTodoRecurrenceBackendClient()
        outcome = await client.materialize_due(
            now=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
        )
        assert outcome == MaterializeOutcome()
