"""Runtime worker process entrypoint."""

from __future__ import annotations

import asyncio

from agent_runtime.observability.http_logging import LoggingConfigurator
from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_worker.loop import RuntimeWorker
from agent_runtime.observability.db_statement_metrics import (
    DbStatementMetricsCollector,
    DbStatementMetricsCollectorEnv,
)
from runtime_worker.jobs.retention_sweeper import (
    RetentionSweeperLoop,
    RetentionSweeperLoopEnv,
)
from runtime_worker.usage_rollup_loop import (
    UsageRollupLoop,
    UsageRollupLoopEnv,
)


class RuntimeWorkerEntrypoint:
    """Entrypoint helpers for the runtime worker process."""

    @staticmethod
    async def amain() -> None:
        """Start the runtime worker loop."""

        settings = RuntimeSettings.load()
        LoggingConfigurator.configure(env=settings.environment.value)
        TelemetryBootstrap.configure(env=settings.environment.value)
        TelemetryBootstrap.instrument_httpx_clients()
        RuntimeSettings.configure_sdk_environment(settings)
        logger = LoggingConfigurator.get_logger("runtime_worker")

        async_ports = RuntimeAdapterFactory.from_settings(settings, role="worker")
        await async_ports.lifecycle.open()
        await async_ports.lifecycle.migrate()
        rollup_loop: UsageRollupLoop | None = None
        retention_loop: RetentionSweeperLoop | None = None
        statement_collector: DbStatementMetricsCollector | None = None
        try:
            worker = RuntimeWorker(
                persistence=async_ports.persistence,
                event_store=async_ports.event_store,
                queue=async_ports.queue,
                settings=settings,
                lock_seconds=settings.execution.worker_lock_seconds,
                draft_store=async_ports.draft_store,
                conversation_tool_ordinal_store=(
                    async_ports.conversation_tool_ordinal_store
                ),
            )
            logger.info(
                "worker_started",
                metadata={
                    "backend": async_ports.backend,
                    "worker_id": worker.worker_id,
                    "poll_interval_seconds": settings.execution.worker_poll_interval_seconds,
                },
            )
            if UsageRollupLoopEnv.env_bool(UsageRollupLoopEnv.ENABLED, default=True):
                rollup_loop = UsageRollupLoop(persistence=async_ports.persistence)
                await rollup_loop.start()
                logger.info(
                    "usage_rollup_loop_started",
                    metadata={
                        "interval_seconds": rollup_loop._interval,
                        "late_arrival_minutes": rollup_loop._late_arrival_minutes,
                    },
                )
            # C8: opt-in (default off) so existing deploys don't
            # start tombstoning rows on upgrade.
            if RetentionSweeperLoopEnv.env_bool(
                RetentionSweeperLoopEnv.ENABLED, default=False
            ):
                retention_loop = RetentionSweeperLoop(
                    persistence=async_ports.persistence
                )
                await retention_loop.start()
                logger.info(
                    "retention_sweeper_loop_started",
                    metadata={
                        "interval_seconds": retention_loop._interval,
                        "dry_run": retention_loop._dry_run,
                    },
                )
            # C11: opt-in (default off). Operator must have
            # ``pg_stat_statements`` installed; the scraper logs
            # once and exits if not.
            if DbStatementMetricsCollectorEnv.env_bool(
                DbStatementMetricsCollectorEnv.ENABLED, default=False
            ):

                async def _scrape_query(sql: str) -> list[dict]:
                    # ``DbStatementMetricsCollector`` is a Postgres-only opt-in
                    # diagnostic; ``postgres_store`` is None on every other
                    # backend. The enabling env flag is documented as
                    # Postgres-only, so reaching this branch on in-memory is a
                    # config error worth surfacing loudly.
                    pg_store = async_ports.postgres_store
                    if pg_store is None:
                        raise RuntimeError(
                            "DbStatementMetricsCollector requires "
                            "RUNTIME_STORE_BACKEND=postgres"
                        )
                    async with pg_store._role_connection("worker") as conn:
                        async with conn.cursor() as cur:
                            await cur.execute(sql)
                            rows = await cur.fetchall()
                    return list(rows)

                statement_collector = DbStatementMetricsCollector(
                    run_query=_scrape_query
                )
                await statement_collector.start()
                logger.info(
                    "db_statement_metrics_collector_started",
                    metadata={"interval_seconds": statement_collector._interval},
                )
            await worker.run_forever(
                poll_interval_seconds=settings.execution.worker_poll_interval_seconds,
            )
        finally:
            if statement_collector is not None:
                await statement_collector.stop()
            if retention_loop is not None:
                await retention_loop.stop()
            if rollup_loop is not None:
                await rollup_loop.stop()
            await async_ports.lifecycle.close()

    @staticmethod
    def main() -> None:
        """Run the async worker until interrupted."""

        try:
            asyncio.run(RuntimeWorkerEntrypoint.amain())
        except KeyboardInterrupt:
            LoggingConfigurator.get_logger("runtime_worker").info("worker_stopped")


if __name__ == "__main__":
    RuntimeWorkerEntrypoint.main()
