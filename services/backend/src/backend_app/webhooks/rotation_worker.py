"""Webhook secret rotation worker — P11-A3.

Async loop that claims due rotating webhooks once per minute (default),
rotates the secret in the vault, advances ``rotates_at`` by 90 days,
and emits an audit row. connectors-prd §9.2.

Claim semantics live in the store (``FOR UPDATE SKIP LOCKED`` in
production; in-memory mimic in dev). The worker is intentionally
single-purpose: NO retries on transient claim failures, NO surfacing
of plaintexts beyond the canonical audit channel.

Lifecycle:

    worker = WebhookRotationWorker(service=svc)
    task = asyncio.create_task(worker.run())
    ...
    worker.stop()
    await task

The ``stop()`` cooperatively cancels the loop at the next tick. The
worker is idempotent — re-running after a crash claims any rows whose
``rotates_at`` is still in the past; the FOR UPDATE SKIP LOCKED gate
ensures concurrent workers don't double-rotate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from backend_app.webhooks.service import WebhooksService


_LOGGER = logging.getLogger("backend.webhooks.rotation_worker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookRotationWorker:
    """Polls the webhook store every ``interval_s`` and rotates due rows."""

    def __init__(
        self,
        *,
        service: WebhooksService,
        interval_s: float = 60.0,
        batch_size: int = 50,
        clock=_now,
    ) -> None:
        self._service = service
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._clock = clock
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Run the rotation loop until :meth:`stop` is called.

        On each tick we drain the due queue in one batch then sleep
        for ``interval_s``. We do NOT log plaintext or
        ciphertext — only the count and the audit row carries the
        before/after metadata (the audit hash chain in production
        signs the row before any downstream sees it).
        """

        while not self._stop_event.is_set():
            try:
                rotated = self.tick()
                if rotated:
                    _LOGGER.info(
                        "webhook_rotation_tick", extra={"rotated_count": len(rotated)}
                    )
            except Exception:  # noqa: BLE001 — defensive; loop must not die
                _LOGGER.exception("webhook_rotation_tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval_s
                )
            except asyncio.TimeoutError:
                continue

    def tick(self) -> list:
        """One iteration — claim + rotate due rows. Returns rotation summaries.

        Synchronous on purpose: the store path is sync (Postgres
        transactions in production; dict mutation in tests), and the
        rotation_worker only runs as a long-lived background task.
        Splitting it out as a separate method makes unit-testing
        idempotency trivial (run tick() twice; the second call returns
        an empty list because the first advanced ``rotates_at``).
        """

        return self._service.rotate_due(now=self._clock(), limit=self._batch_size)

    def stop(self) -> None:
        """Cooperatively signal the loop to exit."""

        self._stop_event.set()


__all__ = ["WebhookRotationWorker"]
