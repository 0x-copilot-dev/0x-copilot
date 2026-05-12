"""Background loop that periodically upserts daily usage rollups for user, org, connector, subagent, and purpose dimensions."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from agent_runtime.api.ports import PersistencePort
from agent_runtime.api.usage_service import UsageQueryService


_LOGGER = logging.getLogger(__name__)


class UsageRollupLoopEnv:
    """Env-var key names and default values for the usage rollup loop."""

    INTERVAL_SECONDS = "USAGE_ROLLUP_INTERVAL_SECONDS"
    LATE_ARRIVAL_MINUTES = "USAGE_LATE_ARRIVAL_WINDOW_MINUTES"
    BACKFILL_DAYS = "USAGE_ROLLUP_BACKFILL_DAYS"
    ENABLED = "USAGE_ROLLUP_LOOP_ENABLED"

    DEFAULT_INTERVAL_SECONDS = 600.0
    DEFAULT_LATE_ARRIVAL_MINUTES = 30
    # On first start the loop backfills the last 30 days once before settling
    # into the every-N-minutes refresh of the trailing 2 days. This makes the
    # endpoints' cold-start fallback path a transient corner instead of the
    # steady state.
    DEFAULT_BACKFILL_DAYS = 30

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        """Read ``name`` from the environment as a float, returning ``default`` on miss or parse error."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        """Read ``name`` from the environment as an int, returning ``default`` on miss or parse error."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        """Read ``name`` from the environment as a boolean, returning ``default`` on miss."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


class UsageRollupLoop:
    """Periodic loop that runs usage rollup refreshes on a configurable interval; lifecycle managed by the worker process."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        interval_seconds: float | None = None,
        late_arrival_minutes: int | None = None,
        backfill_days: int | None = None,
    ) -> None:
        """Initialise the loop with configurable interval, late-arrival window, and initial backfill span."""
        self._persistence = persistence
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else UsageRollupLoopEnv.env_float(
                UsageRollupLoopEnv.INTERVAL_SECONDS,
                UsageRollupLoopEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._late_arrival_minutes = (
            late_arrival_minutes
            if late_arrival_minutes is not None
            else UsageRollupLoopEnv.env_int(
                UsageRollupLoopEnv.LATE_ARRIVAL_MINUTES,
                UsageRollupLoopEnv.DEFAULT_LATE_ARRIVAL_MINUTES,
            )
        )
        self._backfill_days = (
            backfill_days
            if backfill_days is not None
            else UsageRollupLoopEnv.env_int(
                UsageRollupLoopEnv.BACKFILL_DAYS,
                UsageRollupLoopEnv.DEFAULT_BACKFILL_DAYS,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Kick off the loop. Returns immediately."""

        if self._task is not None:
            return
        await self.refresh(span_days=self._backfill_days)
        self._task = asyncio.create_task(self._run(), name="usage-rollup-loop")

    async def stop(self) -> None:
        """Signal the loop to exit and wait for it."""

        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        """Wait for the next tick or a stop signal, then trigger a refresh; repeat until stopped."""
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return  # stop requested
            except TimeoutError:
                pass
            try:
                await self.refresh(span_days=2)
            except Exception:
                _LOGGER.warning("usage_rollup_refresh_failed", exc_info=True)

    async def refresh(self, *, span_days: int) -> None:
        """Upsert daily usage rows for user, org, connector, subagent, and purpose over the trailing ``span_days`` UTC days."""

        now = datetime.now(timezone.utc)
        end = now
        start = end - timedelta(days=max(span_days, 1))
        run_rows = await self._persistence.query_run_usage_for_range(
            org_id=None,
            user_id=None,
            start=start,
            end=end,
        )
        refreshed_at = now
        user_rows = UsageQueryService.rollup_user_rows(
            run_rows, refreshed_at=refreshed_at
        )
        org_rows = UsageQueryService.rollup_org_rows(
            run_rows, refreshed_at=refreshed_at
        )
        for row in user_rows:
            try:
                await self._persistence.upsert_user_daily_usage(row)
            except Exception:
                _LOGGER.warning(
                    "usage_rollup_user_upsert_failed",
                    extra={
                        "metadata": {
                            "org_id": row.org_id,
                            "user_id": row.user_id,
                            "day": row.day.date().isoformat(),
                        }
                    },
                    exc_info=True,
                )
        for row in org_rows:
            try:
                await self._persistence.upsert_org_daily_usage(row)
            except Exception:
                _LOGGER.warning(
                    "usage_rollup_org_upsert_failed",
                    extra={
                        "metadata": {
                            "org_id": row.org_id,
                            "day": row.day.date().isoformat(),
                        }
                    },
                    exc_info=True,
                )

        # Per-connector rollup reads from ``runtime_model_call_usage``
        # (per-LLM-call) because one run typically spans multiple connectors
        # and the run-level rollup cannot be split by connector.
        try:
            call_rows = await self._persistence.query_model_call_usage_for_range(
                org_id=None, start=start, end=end
            )
        except Exception:
            _LOGGER.warning(
                "usage_rollup_connector_scan_failed",
                exc_info=True,
            )
            call_rows = ()
        run_user_lookup = {row.run_id: row.user_id for row in run_rows}
        connector_rows = UsageQueryService.rollup_connector_rows(
            call_rows,
            run_user_lookup=run_user_lookup,
            refreshed_at=refreshed_at,
        )
        for row in connector_rows:
            try:
                await self._persistence.upsert_connector_daily_usage(row)
            except Exception:
                _LOGGER.warning(
                    "usage_rollup_connector_upsert_failed",
                    extra={
                        "metadata": {
                            "org_id": row.org_id,
                            "day": row.day.date().isoformat(),
                            "connector_slug": row.connector_slug,
                            "model_name": row.model_name,
                        }
                    },
                    exc_info=True,
                )

        # Subagent rollup: aggregates org × day × subagent_slug × model.
        # Reuses the per-call scan to avoid a second table scan.
        subagent_rows = UsageQueryService.rollup_subagent_rows(
            call_rows,
            refreshed_at=refreshed_at,
        )
        for row in subagent_rows:
            try:
                await self._persistence.upsert_subagent_daily_usage(row)
            except Exception:
                _LOGGER.warning(
                    "usage_rollup_subagent_upsert_failed",
                    extra={
                        "metadata": {
                            "org_id": row.org_id,
                            "day": row.day.date().isoformat(),
                            "subagent_slug": row.subagent_slug,
                            "model_name": row.model_name,
                        }
                    },
                    exc_info=True,
                )

        # Purpose rollup: aggregates org × day × purpose × model.
        # Reuses the same per-call scan.
        purpose_rows = UsageQueryService.rollup_purpose_rows(
            call_rows,
            refreshed_at=refreshed_at,
        )
        for row in purpose_rows:
            try:
                await self._persistence.upsert_purpose_daily_usage(row)
            except Exception:
                _LOGGER.warning(
                    "usage_rollup_purpose_upsert_failed",
                    extra={
                        "metadata": {
                            "org_id": row.org_id,
                            "day": row.day.date().isoformat(),
                            "purpose": row.purpose,
                            "model_name": row.model_name,
                        }
                    },
                    exc_info=True,
                )
