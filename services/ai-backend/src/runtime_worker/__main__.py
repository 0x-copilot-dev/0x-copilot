"""Runtime worker process entrypoint."""

from __future__ import annotations

import asyncio
import logging

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_worker.loop import RuntimeWorker


async def amain() -> None:
    """Start the runtime worker loop."""

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logger = logging.getLogger("runtime_worker")
    settings = RuntimeSettings.load()
    ports = RuntimeAdapterFactory.from_settings(settings)
    worker = RuntimeWorker(
        persistence=ports.persistence,
        event_store=ports.event_store,
        queue=ports.queue,
        settings=settings,
        lock_seconds=settings.execution.worker_lock_seconds,
    )
    logger.info(
        "runtime worker started backend=%s worker_id=%s poll_interval=%s",
        ports.backend,
        worker.worker_id,
        settings.execution.worker_poll_interval_seconds,
    )
    await worker.run_forever(
        poll_interval_seconds=settings.execution.worker_poll_interval_seconds,
    )


def main() -> None:
    """Run the async worker until interrupted."""

    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        logging.getLogger("runtime_worker").info("runtime worker stopped")


if __name__ == "__main__":
    main()
