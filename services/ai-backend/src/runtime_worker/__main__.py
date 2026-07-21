"""Runtime worker process entrypoint."""

from __future__ import annotations

import asyncio

from agent_runtime.api.user_policies_resolver import UserPoliciesResolverFactory
from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.observability.http_logging import LoggingConfigurator
from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker
from agent_runtime.observability.db_statement_metrics import (
    DbStatementMetricsCollector,
    DbStatementMetricsCollectorEnv,
)
from runtime_worker.jobs.retention_backfill import (
    RetentionBackfillJob,
    RetentionBackfillJobEnv,
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
        # The file backend is single-writer and single-process: its queue claim
        # is an in-process asyncio lock that cannot coordinate with a separate
        # OS process, so a standalone worker against it would double-claim runs
        # that the in-process worker (in runtime_api) is already draining. Fail
        # fast rather than silently corrupt the single-writer invariant. The
        # file store runs only under single_user_desktop, which executes runs
        # in-process — never via this entrypoint.
        if async_ports.backend == "file":
            raise SystemExit(
                "The standalone runtime worker does not support "
                "RUNTIME_STORE_BACKEND=file. The file store is single-writer and "
                "single-process (single_user_desktop); runs execute via the "
                "in-process worker in runtime_api, not this process."
            )
        await async_ports.lifecycle.open()
        await async_ports.lifecycle.migrate()
        rollup_loop: UsageRollupLoop | None = None
        retention_loop: RetentionSweeperLoop | None = None
        statement_collector: DbStatementMetricsCollector | None = None
        try:
            # One MCP discovery cache per worker process — shared by every
            # run/approval handler this worker spins up. API and worker run
            # as separate processes in production; each builds its own
            # cache (per-process warm-up trade-off documented in the cache
            # docstring).
            mcp_discovery_cache = (
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            )
            # BYOK: run/approval handlers re-fetch per-user provider keys at
            # claim time (queue payloads never carry them). Null when the
            # backend lane env is not configured — runs then use env keys.
            user_policies_resolver = UserPoliciesResolverFactory.default(
                http_client=BackendHttpPool.get()
            )
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
                citation_store=async_ports.citation_store,
                mcp_discovery_cache=mcp_discovery_cache,
                user_policies_resolver=user_policies_resolver,
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
            # Opt-in (default off) so existing deploys are unaffected on upgrade.
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
            # Opt-in (default off). Requires ``pg_stat_statements`` installed;
            # the scraper logs once and exits if not.
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
            # Opt-in (default off). Stamps ``retention_until`` on historical rows
            # that pre-date retention policy enforcement. Runs once at startup.
            if RetentionBackfillJobEnv.env_bool(
                RetentionBackfillJobEnv.ENABLED, default=False
            ):
                backfill_job = RetentionBackfillJob(
                    persistence=async_ports.persistence,
                )
                backfill_counts = await backfill_job.run()
                logger.info(
                    "retention_backfill_complete",
                    metadata={"rows_stamped": sum(backfill_counts.values())},
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
            # Idempotent — closes the pooled backend client used by the
            # BYOK policy resolver (and any capability HTTP callers).
            await BackendHttpPool.aclose()

    @staticmethod
    def main() -> None:
        """Run the async worker until interrupted."""

        try:
            asyncio.run(RuntimeWorkerEntrypoint.amain())
        except KeyboardInterrupt:
            LoggingConfigurator.get_logger("runtime_worker").info("worker_stopped")


if __name__ == "__main__":
    RuntimeWorkerEntrypoint.main()
