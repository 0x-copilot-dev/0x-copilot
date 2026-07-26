"""Runtime worker process entrypoint."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from agent_runtime.api.user_policies_resolver import UserPoliciesResolverFactory
from agent_runtime.capabilities.desktop.workspace_attestation import (
    DesktopWorkspaceAttestationRegistry,
)
from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.observability.http_logging import LoggingConfigurator
from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.repair_planning import (
    build_audit_export_verification_store,
    build_effect_claim_store,
    build_repair_legal_hold_lookup,
    build_repair_planning_snapshot_store,
)
from runtime_adapters.artifact_cleanup_schedule import (
    build_artifact_cleanup_schedule_store,
)
from agent_runtime.api.artifact_repository import ArtifactServiceComposition
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
from runtime_worker.jobs.repair_planning import (
    EffectClaimRepairSnapshotCollector,
    RepairPlanningLoop,
    RepairPlanningLoopEnv,
    RepairPlanningRunner,
)
from runtime_worker.jobs.repair_execution import (
    RepairExecutionEnv,
    RepairReconciliationExecutor,
)
from runtime_worker.jobs.artifact_cleanup_execution import (
    ArtifactCleanupExecutionEnv,
    ArtifactCleanupExecutionLoop,
    ArtifactCleanupExecutionRunner,
)
from runtime_worker.jobs.audit_export_verification import (
    AuditExportVerificationSampler,
    AuditExportVerificationSamplingEnv,
    AuditExportVerificationSamplingLoop,
    AuditExportVerificationSamplingRunner,
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
        artifact_cleanup_execution_loop: ArtifactCleanupExecutionLoop | None = None
        repair_planning_loop: RepairPlanningLoop | None = None
        audit_export_verification_loop: AuditExportVerificationSamplingLoop | None = (
            None
        )
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
            # Compose the claim source once for this worker process.  D12 reads
            # that exact instance when it is enabled; in-memory therefore has
            # the same semantic view as the effect worker, while file/Postgres
            # retain their restart-safe durable implementation.
            effect_claim_store = (
                build_effect_claim_store(
                    settings=settings,
                    persistence=async_ports.persistence,
                )
                if (
                    settings.execution.surfaces_v2
                    and settings.execution.artifact_effects_v2
                )
                else None
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
                artifact_service=ArtifactServiceComposition.build(async_ports),
                artifact_blob_store=async_ports.artifact_blob_store,
                artifact_reference_store=async_ports.artifact_reference_provider,
                effect_claim_store=effect_claim_store,
                workspace_attestation_registry=(
                    DesktopWorkspaceAttestationRegistry.from_environment()
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
            artifact_cleanup_execution_enabled = ArtifactCleanupExecutionEnv.enabled()
            if artifact_cleanup_execution_enabled and (
                not settings.execution.artifact_effects_v2
                or async_ports.artifact_lifecycle_jobs is None
            ):
                raise RuntimeError(
                    "ARTIFACT_CLEANUP_EXECUTION_ENABLED requires "
                    "ARTIFACT_EFFECTS_V2 and a configured artifact repository."
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
                    persistence=async_ports.persistence,
                    # Dedicated cleanup owns this retention kind when its
                    # explicit flag is on. Generic retention stays unchanged
                    # otherwise, avoiding duplicate scheduling/audit rows.
                    artifact_effects_v2=(
                        async_ports.artifact_effects_v2
                        and not artifact_cleanup_execution_enabled
                    ),
                )
                await retention_loop.start()
                logger.info(
                    "retention_sweeper_loop_started",
                    metadata={
                        "interval_seconds": retention_loop._interval,
                        "dry_run": retention_loop._dry_run,
                    },
                )
            if artifact_cleanup_execution_enabled:
                max_orgs = ArtifactCleanupExecutionEnv.env_int(
                    ArtifactCleanupExecutionEnv.MAX_ORGS,
                    default=ArtifactCleanupExecutionEnv.DEFAULT_MAX_ORGS,
                    maximum=500,
                )
                limit_per_org = ArtifactCleanupExecutionEnv.env_int(
                    ArtifactCleanupExecutionEnv.LIMIT_PER_ORG,
                    default=ArtifactCleanupExecutionEnv.DEFAULT_LIMIT_PER_ORG,
                    maximum=500,
                )
                lease_seconds = ArtifactCleanupExecutionEnv.env_float(
                    ArtifactCleanupExecutionEnv.LEASE_SECONDS,
                    default=ArtifactCleanupExecutionEnv.DEFAULT_LEASE_SECONDS,
                )
                artifact_cleanup_execution_loop = ArtifactCleanupExecutionLoop(
                    runner=ArtifactCleanupExecutionRunner(
                        persistence=async_ports.persistence,  # type: ignore[arg-type]
                        schedule=build_artifact_cleanup_schedule_store(
                            settings=settings,
                            persistence=async_ports.persistence,
                        ),
                        max_orgs=max_orgs,
                        limit_per_org=limit_per_org,
                        lease_seconds=lease_seconds,
                    )
                )
                await artifact_cleanup_execution_loop.start()
                logger.info(
                    "artifact_cleanup_execution_loop_started",
                    metadata={
                        "interval_seconds": artifact_cleanup_execution_loop._interval,
                        "max_orgs": max_orgs,
                        "limit_per_org": limit_per_org,
                        "lease_seconds": lease_seconds,
                    },
                )
            # D12 planning is opt-in.  Execution is a second explicit switch:
            # it consumes only persisted candidate rows, freshly revalidates
            # them, and can enqueue only body-free reconciliation commands.
            # Physical artifact cleanup has its own opt-in scheduler above;
            # both lanes still execute only through hold/reference-locked
            # lifecycle jobs, never through repair planning.
            repair_planning_enabled = RepairPlanningLoopEnv.env_bool(
                RepairPlanningLoopEnv.ENABLED, default=False
            )
            repair_execution_enabled = RepairExecutionEnv.enabled()
            if repair_execution_enabled and not repair_planning_enabled:
                raise RuntimeError(
                    "REPAIR_EXECUTION_ENABLED requires REPAIR_PLANNING_ENABLED."
                )
            if repair_planning_enabled:
                if effect_claim_store is None:
                    raise RuntimeError(
                        "REPAIR_PLANNING_ENABLED requires SURFACES_V2 and "
                        "ARTIFACT_EFFECTS_V2."
                    )
                max_claims = RepairPlanningLoopEnv.env_int(
                    RepairPlanningLoopEnv.MAX_CLAIMS,
                    RepairPlanningLoopEnv.DEFAULT_MAX_CLAIMS,
                    maximum=500,
                )
                page_size = RepairPlanningLoopEnv.env_int(
                    RepairPlanningLoopEnv.PAGE_SIZE,
                    RepairPlanningLoopEnv.DEFAULT_PAGE_SIZE,
                    maximum=500,
                )
                max_events_per_run = RepairPlanningLoopEnv.env_int(
                    RepairPlanningLoopEnv.MAX_EVENTS_PER_RUN,
                    RepairPlanningLoopEnv.DEFAULT_MAX_EVENTS_PER_RUN,
                    maximum=10_000,
                )
                quiet_seconds = RepairPlanningLoopEnv.env_int(
                    RepairPlanningLoopEnv.QUIET_SECONDS,
                    RepairPlanningLoopEnv.DEFAULT_QUIET_SECONDS,
                    maximum=604_800,
                )
                repair_collector = EffectClaimRepairSnapshotCollector(
                    persistence=async_ports.persistence,
                    event_store=async_ports.event_store,
                    claims=effect_claim_store,
                    legal_holds=build_repair_legal_hold_lookup(
                        settings=settings,
                        persistence=async_ports.persistence,
                    ),
                    supported_reconcile_executors=(
                        worker.repair_reconcile_supported_executors
                    ),
                    max_events_per_run=max_events_per_run,
                    quiet_period=timedelta(seconds=quiet_seconds),
                )
                repair_planning_loop = RepairPlanningLoop(
                    runner=RepairPlanningRunner(
                        collector=repair_collector,
                        snapshots=build_repair_planning_snapshot_store(
                            settings=settings,
                            persistence=async_ports.persistence,
                        ),
                        candidate_executor=(
                            RepairReconciliationExecutor(
                                collector=repair_collector,
                                queue=async_ports.queue,
                            )
                            if repair_execution_enabled
                            else None
                        ),
                        max_claims=max_claims,
                        page_size=page_size,
                    )
                )
                await repair_planning_loop.start()
                logger.info(
                    "repair_planning_loop_started",
                    metadata={
                        "interval_seconds": repair_planning_loop._interval,
                        "max_claims": max_claims,
                        "page_size": page_size,
                        "repair_execution_enabled": repair_execution_enabled,
                    },
                )
            # D7/D12 audit-export verification is a bounded, read-only sampler.
            # It has no effect/repair executor, queue, approval, or artifact
            # dependency. A missing production signing key becomes an honest
            # persisted unavailable outcome rather than a fake success.
            if AuditExportVerificationSamplingEnv.env_bool(
                AuditExportVerificationSamplingEnv.ENABLED,
                default=False,
            ):
                max_samples = AuditExportVerificationSamplingEnv.env_int(
                    AuditExportVerificationSamplingEnv.MAX_SAMPLES,
                    AuditExportVerificationSamplingEnv.DEFAULT_MAX_SAMPLES,
                    maximum=500,
                )
                lease_seconds = AuditExportVerificationSamplingEnv.env_int(
                    AuditExportVerificationSamplingEnv.LEASE_SECONDS,
                    AuditExportVerificationSamplingEnv.DEFAULT_LEASE_SECONDS,
                    maximum=3600,
                )
                audit_export_verification_loop = AuditExportVerificationSamplingLoop(
                    runner=AuditExportVerificationSamplingRunner(
                        store=build_audit_export_verification_store(
                            settings=settings,
                            persistence=async_ports.persistence,
                        ),
                        sampler=AuditExportVerificationSampler(
                            event_store=async_ports.event_store,
                        ),
                        persistence=async_ports.persistence,
                        worker_id=f"{worker.worker_id}-audit-export-verification",
                        max_samples=max_samples,
                        lease_seconds=lease_seconds,
                    )
                )
                await audit_export_verification_loop.start()
                logger.info(
                    "audit_export_verification_sampling_loop_started",
                    metadata={
                        "interval_seconds": audit_export_verification_loop._interval,
                        "max_samples": max_samples,
                        "lease_seconds": lease_seconds,
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
            if artifact_cleanup_execution_loop is not None:
                await artifact_cleanup_execution_loop.stop()
            if audit_export_verification_loop is not None:
                await audit_export_verification_loop.stop()
            if repair_planning_loop is not None:
                await repair_planning_loop.stop()
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
