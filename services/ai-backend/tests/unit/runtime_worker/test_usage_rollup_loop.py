"""Unit tests for B4's usage rollup loop."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.records import RuntimeRunUsageRecord
from runtime_adapters.in_memory import (
    AsyncInMemoryRuntimeApiStore,
    InMemoryRuntimeApiStore,
)
from runtime_worker.usage_rollup_loop import UsageRollupLoop, UsageRollupLoopEnv


def _seed_run(
    store: InMemoryRuntimeApiStore,
    *,
    run_id: str,
    org_id: str = "org_a",
    user_id: str = "user_1",
    completed_at: datetime,
    input_tokens: int = 100,
    output_tokens: int = 200,
) -> None:
    store.run_usage[run_id] = RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id=user_id,
        conversation_id="conv-1",
        run_id=run_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=0,
        total_tokens=input_tokens + output_tokens,
        chunk_count=1,
        duration_ms=1000,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        status="completed",
    )


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_writes_user_and_org_rollups(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        completed = datetime.now(timezone.utc) - timedelta(hours=2)
        _seed_run(
            sync_store,
            run_id="r1",
            user_id="u1",
            completed_at=completed,
            input_tokens=10,
            output_tokens=20,
        )
        _seed_run(
            sync_store,
            run_id="r2",
            user_id="u2",
            completed_at=completed,
            input_tokens=5,
            output_tokens=7,
        )

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        # Two distinct users → two user-rollup rows.
        assert len(sync_store.user_daily_usage) == 2
        # One org × one model × one day → one org-rollup row.
        assert len(sync_store.org_daily_usage) == 1
        org_rows = list(sync_store.org_daily_usage.values())
        assert org_rows[0].distinct_users == 2
        assert org_rows[0].runs_count == 2
        assert org_rows[0].input_tokens == 15
        assert org_rows[0].output_tokens == 27

    @pytest.mark.asyncio
    async def test_refresh_idempotent(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        _seed_run(sync_store, run_id="r1", completed_at=completed)

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        def _strip_refreshed(rows: dict[object, object]) -> dict[object, object]:
            # ``refreshed_at`` advances per call; the rest of the row is
            # what we expect to be byte-stable across re-runs.
            return {
                key: {
                    field: value
                    for field, value in row.model_dump().items()
                    if field != "refreshed_at"
                }
                for key, row in rows.items()  # type: ignore[attr-defined]
            }

        first_user = _strip_refreshed(sync_store.user_daily_usage)
        first_org = _strip_refreshed(sync_store.org_daily_usage)
        await loop.refresh(span_days=2)
        assert _strip_refreshed(sync_store.user_daily_usage) == first_user
        assert _strip_refreshed(sync_store.org_daily_usage) == first_org

    @pytest.mark.asyncio
    async def test_refresh_skips_runs_outside_window(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        old = datetime.now(timezone.utc) - timedelta(days=10)
        _seed_run(sync_store, run_id="old", completed_at=old)

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        # The 10-day-old run is outside the 2-day refresh window.
        assert sync_store.user_daily_usage == {}
        assert sync_store.org_daily_usage == {}


# --- env-var parsers (UsageRollupLoopEnv) ----------------------------------


class TestUsageRollupLoopEnv:
    @pytest.fixture(autouse=True)
    def _isolate_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[pytest.MonkeyPatch]:
        for name in (
            UsageRollupLoopEnv.INTERVAL_SECONDS,
            UsageRollupLoopEnv.LATE_ARRIVAL_MINUTES,
            UsageRollupLoopEnv.BACKFILL_DAYS,
            UsageRollupLoopEnv.ENABLED,
        ):
            monkeypatch.delenv(name, raising=False)
        yield monkeypatch

    def test_env_float_returns_default_when_unset(self) -> None:
        assert (
            UsageRollupLoopEnv.env_float(UsageRollupLoopEnv.INTERVAL_SECONDS, 600.0)
            == 600.0
        )

    def test_env_float_returns_default_when_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.INTERVAL_SECONDS, "   ")
        assert (
            UsageRollupLoopEnv.env_float(UsageRollupLoopEnv.INTERVAL_SECONDS, 42.0)
            == 42.0
        )

    def test_env_float_returns_default_when_unparseable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.INTERVAL_SECONDS, "not-a-number")
        assert (
            UsageRollupLoopEnv.env_float(UsageRollupLoopEnv.INTERVAL_SECONDS, 7.5)
            == 7.5
        )

    def test_env_float_parses_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.INTERVAL_SECONDS, "12.5")
        assert (
            UsageRollupLoopEnv.env_float(UsageRollupLoopEnv.INTERVAL_SECONDS, 600.0)
            == 12.5
        )

    def test_env_int_returns_default_when_unset(self) -> None:
        assert (
            UsageRollupLoopEnv.env_int(UsageRollupLoopEnv.LATE_ARRIVAL_MINUTES, 30)
            == 30
        )

    def test_env_int_returns_default_when_unparseable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.LATE_ARRIVAL_MINUTES, "many")
        assert (
            UsageRollupLoopEnv.env_int(UsageRollupLoopEnv.LATE_ARRIVAL_MINUTES, 30)
            == 30
        )

    def test_env_int_parses_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.BACKFILL_DAYS, "7")
        assert UsageRollupLoopEnv.env_int(UsageRollupLoopEnv.BACKFILL_DAYS, 30) == 7

    def test_env_bool_returns_default_when_unset(self) -> None:
        assert (
            UsageRollupLoopEnv.env_bool(UsageRollupLoopEnv.ENABLED, default=True)
            is True
        )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", True),
            ("true", True),
            ("True", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("nope", False),
        ],
    )
    def test_env_bool_parses_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ) -> None:
        monkeypatch.setenv(UsageRollupLoopEnv.ENABLED, raw)
        assert (
            UsageRollupLoopEnv.env_bool(UsageRollupLoopEnv.ENABLED, default=False)
            is expected
        )


# --- start / stop / refresh-failure lifecycle ------------------------------


class _RaisingPersistence:
    """Persistence stub whose ``query_run_usage_for_range`` always raises.

    Used to exercise the loop's "best-effort: log and continue" path
    without dragging the in-memory store into a state that would also
    raise on construction.
    """

    async def query_run_usage_for_range(
        self, *, org_id: object, user_id: object, start: object, end: object
    ) -> None:
        del org_id, user_id, start, end
        raise RuntimeError("simulated persistence outage")


class TestUsageRollupLoopLifecycle:
    @pytest.mark.asyncio
    async def test_start_then_stop_returns_cleanly(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        loop = UsageRollupLoop(
            persistence=async_store,
            interval_seconds=0.05,
            backfill_days=1,
        )
        await loop.start()
        # Yield control so the background task can wait at least once.
        await asyncio.sleep(0.0)
        await loop.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        loop = UsageRollupLoop(
            persistence=async_store,
            interval_seconds=0.05,
            backfill_days=1,
        )
        await loop.start()
        await loop.start()  # second call short-circuits — no second task spawned
        await loop.stop()

    @pytest.mark.asyncio
    async def test_stop_when_never_started_is_a_no_op(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        loop = UsageRollupLoop(persistence=async_store)
        await loop.stop()  # safe: no task to await

    @pytest.mark.asyncio
    async def test_run_loop_logs_and_survives_refresh_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        loop = UsageRollupLoop(
            persistence=_RaisingPersistence(),
            interval_seconds=0.02,
            backfill_days=1,
        )
        with caplog.at_level("WARNING", logger="runtime_worker.usage_rollup_loop"):
            # ``start`` runs the initial backfill — which raises through
            # ``refresh()``. The exception escapes ``start`` because the
            # initial refresh is awaited directly (not inside the catch).
            with pytest.raises(RuntimeError):
                await loop.start()

        # Now exercise the periodic-tick branch where failures are caught
        # and logged. We invoke ``_run`` directly with a primed stop so it
        # ticks once, raises, logs, and then exits via the stop signal.
        loop_with_running_task = UsageRollupLoop(
            persistence=_RaisingPersistence(),
            interval_seconds=0.02,
            backfill_days=1,
        )
        # Schedule stop after a single tick so ``_run`` returns deterministically.

        async def _stop_soon() -> None:
            await asyncio.sleep(0.05)
            loop_with_running_task._stop.set()  # noqa: SLF001 — exercising private branch

        with caplog.at_level("WARNING", logger="runtime_worker.usage_rollup_loop"):
            await asyncio.gather(loop_with_running_task._run(), _stop_soon())  # noqa: SLF001
        assert any(
            "usage_rollup_refresh_failed" in record.getMessage()
            for record in caplog.records
        )


# --- per-row UPSERT failure paths (best-effort logging) --------------------


class _SelectiveFailureStore(AsyncInMemoryRuntimeApiStore):
    """In-memory store that raises on a chosen UPSERT method.

    Wraps the real async store so the rest of the refresh path runs
    normally; only the named upsert raises. Lets a single test
    exercise the per-target ``except Exception: log+continue`` branch.
    """

    def __init__(self, sync_store: InMemoryRuntimeApiStore, *, raise_on: str) -> None:
        super().__init__(sync_store)
        self._raise_on = raise_on

    async def upsert_user_daily_usage(self, row: object) -> None:  # type: ignore[override]
        if self._raise_on == "user":
            raise RuntimeError("user-rollup upsert failure")
        await super().upsert_user_daily_usage(row)  # type: ignore[arg-type]

    async def upsert_org_daily_usage(self, row: object) -> None:  # type: ignore[override]
        if self._raise_on == "org":
            raise RuntimeError("org-rollup upsert failure")
        await super().upsert_org_daily_usage(row)  # type: ignore[arg-type]

    async def upsert_connector_daily_usage(self, row: object) -> None:  # type: ignore[override]
        if self._raise_on == "connector":
            raise RuntimeError("connector-rollup upsert failure")
        await super().upsert_connector_daily_usage(row)  # type: ignore[arg-type]

    async def query_model_call_usage_for_range(  # type: ignore[override]
        self, *, org_id: object, start: object, end: object
    ) -> tuple[object, ...]:
        if self._raise_on == "connector_scan":
            raise RuntimeError("connector-scan failure")
        return await super().query_model_call_usage_for_range(
            org_id=org_id, start=start, end=end
        )


class TestRefreshUpsertFailures:
    @pytest.mark.asyncio
    async def test_user_upsert_failure_is_logged_and_other_targets_continue(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sync_store = InMemoryRuntimeApiStore()
        store = _SelectiveFailureStore(sync_store, raise_on="user")
        _seed_run(
            sync_store,
            run_id="r1",
            completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        loop = UsageRollupLoop(persistence=store)
        with caplog.at_level("WARNING", logger="runtime_worker.usage_rollup_loop"):
            await loop.refresh(span_days=2)
        assert any(
            "usage_rollup_user_upsert_failed" in record.getMessage()
            for record in caplog.records
        )
        # Org rollup still landed even though the user rollup failed.
        assert sync_store.org_daily_usage

    @pytest.mark.asyncio
    async def test_org_upsert_failure_is_logged_and_user_target_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sync_store = InMemoryRuntimeApiStore()
        store = _SelectiveFailureStore(sync_store, raise_on="org")
        _seed_run(
            sync_store,
            run_id="r1",
            completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        loop = UsageRollupLoop(persistence=store)
        with caplog.at_level("WARNING", logger="runtime_worker.usage_rollup_loop"):
            await loop.refresh(span_days=2)
        assert any(
            "usage_rollup_org_upsert_failed" in record.getMessage()
            for record in caplog.records
        )
        # User rollup still landed.
        assert sync_store.user_daily_usage

    @pytest.mark.asyncio
    async def test_connector_scan_failure_is_logged_and_skips_connector_rollup(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sync_store = InMemoryRuntimeApiStore()
        store = _SelectiveFailureStore(sync_store, raise_on="connector_scan")
        _seed_run(
            sync_store,
            run_id="r1",
            completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        loop = UsageRollupLoop(persistence=store)
        with caplog.at_level("WARNING", logger="runtime_worker.usage_rollup_loop"):
            await loop.refresh(span_days=2)
        assert any(
            "usage_rollup_connector_scan_failed" in record.getMessage()
            for record in caplog.records
        )
        # User and org rollups still landed; connector rollup is empty.
        assert sync_store.user_daily_usage
        assert sync_store.connector_daily_usage == {}
