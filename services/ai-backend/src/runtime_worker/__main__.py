"""Runtime worker process entrypoint."""

from __future__ import annotations

import asyncio

from agent_runtime.observability.http_logging import LoggingConfigurator
from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_worker.loop import RuntimeWorker


_ASYNC_BACKENDS = frozenset({"in_memory_async", "postgres"})


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

        if settings.store.backend in _ASYNC_BACKENDS:
            async_ports = RuntimeAdapterFactory.async_from_settings(settings)
            await async_ports.store.open()
            await async_ports.store.migrate()
            try:
                worker = RuntimeWorker(
                    persistence=async_ports.persistence,
                    event_store=async_ports.event_store,
                    queue=async_ports.queue,
                    settings=settings,
                    lock_seconds=settings.execution.worker_lock_seconds,
                )
                logger.info(
                    "worker_started",
                    metadata={
                        "backend": async_ports.backend,
                        "worker_id": worker.worker_id,
                        "poll_interval_seconds": settings.execution.worker_poll_interval_seconds,
                    },
                )
                await worker.run_forever(
                    poll_interval_seconds=settings.execution.worker_poll_interval_seconds,
                )
            finally:
                await async_ports.store.close()
            return

        ports = RuntimeAdapterFactory.from_settings(settings)
        worker = RuntimeWorker(
            persistence=ports.persistence,
            event_store=ports.event_store,
            queue=ports.queue,
            settings=settings,
            lock_seconds=settings.execution.worker_lock_seconds,
        )
        logger.info(
            "worker_started",
            metadata={
                "backend": ports.backend,
                "worker_id": worker.worker_id,
                "poll_interval_seconds": settings.execution.worker_poll_interval_seconds,
            },
        )
        await worker.run_forever(
            poll_interval_seconds=settings.execution.worker_poll_interval_seconds,
        )

    @staticmethod
    def main() -> None:
        """Run the async worker until interrupted."""

        try:
            asyncio.run(RuntimeWorkerEntrypoint.amain())
        except KeyboardInterrupt:
            LoggingConfigurator.get_logger("runtime_worker").info("worker_stopped")


if __name__ == "__main__":
    RuntimeWorkerEntrypoint.main()
