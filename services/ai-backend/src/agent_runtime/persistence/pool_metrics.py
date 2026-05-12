"""OTel meters for DB pool health and atomic-write outcomes.

The runtime exports OpenTelemetry metrics over OTLP; there is no in-process
Prometheus surface. These helpers keep meter creation and instrument names
in one place so dashboards and alerts have a stable contract.

Metric names match the C4 spec for forward compatibility with a future
Prometheus exposition layer:
- ``db_pool_size`` / ``db_pool_in_use`` / ``db_pool_waiting`` -- gauges
  populated from ``psycopg_pool.AsyncConnectionPool.get_stats()``.
- ``db_pool_acquire_seconds`` -- histogram of pool acquisition latency.
- ``db_optimistic_retry_total`` -- counter for CAS retry outcomes.
- ``db_atomic_upsert_total`` -- counter for upsert outcomes (insert,
  update, conflict_rejected).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import monotonic
from typing import TYPE_CHECKING, Any, AsyncIterator

from opentelemetry import metrics


if TYPE_CHECKING:  # pragma: no cover -- typing-only import
    from psycopg_pool import AsyncConnectionPool


_METER_NAME = "agent_runtime.persistence.pool"


class PoolMetrics:
    """Per-store OTel meter wrapper.

    One instance is created per ``PostgresRuntimeApiStore`` so the
    ``service`` and ``role`` attributes can be applied uniformly without
    touching every call site. Gauges are observable callbacks bound to the
    store's pool reference; histograms and counters are recorded inline.
    """

    _ATTR_KEY_SERVICE = "service"
    _ATTR_KEY_ROLE = "role"
    _ATTR_KEY_TABLE = "table"
    _ATTR_KEY_OUTCOME = "outcome"

    def __init__(self, *, service: str, role: str) -> None:
        self._service = service
        self._role = role
        self._meter = metrics.get_meter(_METER_NAME)
        self._acquire_histogram = self._meter.create_histogram(
            name="db_pool_acquire_seconds",
            unit="s",
            description="Wait time to acquire a connection from the pool.",
        )
        self._optimistic_retry_counter = self._meter.create_counter(
            name="db_optimistic_retry_total",
            description=(
                "Optimistic-lock retry outcomes. "
                "outcome=success|exhausted; table identifies the CAS target."
            ),
        )
        self._atomic_upsert_counter = self._meter.create_counter(
            name="db_atomic_upsert_total",
            description=(
                "Atomic-upsert outcomes for ON CONFLICT DO UPDATE statements. "
                "outcome=insert|update|conflict_rejected."
            ),
        )
        self._pool: AsyncConnectionPool | None = None
        self._gauges_registered = False

    def bind_pool(self, pool: AsyncConnectionPool) -> None:
        """Register pool-stats observable gauges. Idempotent per instance."""

        if self._gauges_registered:
            return
        self._pool = pool

        def _observe_size(_options: object) -> list[metrics.Observation]:
            return self._observe_pool_stat("pool_size", "db_pool_size")

        def _observe_in_use(_options: object) -> list[metrics.Observation]:
            return self._observe_pool_stat("pool_size") and []

        def _observe_waiting(_options: object) -> list[metrics.Observation]:
            return self._observe_pool_stat("requests_waiting", "db_pool_waiting")

        # Observable gauges call back per scrape; keep them simple and fast.
        self._meter.create_observable_gauge(
            name="db_pool_size",
            callbacks=[
                lambda options: self._observe_pool_stat("pool_size", "db_pool_size")
            ],
            description="Current configured size of the connection pool.",
        )
        self._meter.create_observable_gauge(
            name="db_pool_in_use",
            callbacks=[
                lambda options: self._observe_pool_stat(
                    "pool_size_used", "db_pool_in_use"
                )
            ],
            description="Connections currently checked out of the pool.",
        )
        self._meter.create_observable_gauge(
            name="db_pool_waiting",
            callbacks=[
                lambda options: self._observe_pool_stat(
                    "requests_waiting", "db_pool_waiting"
                )
            ],
            description="Pending pool acquire() requests blocked on capacity.",
        )
        self._gauges_registered = True

    def _observe_pool_stat(
        self, stat_key: str, *_unused_metric_names: str
    ) -> list[metrics.Observation]:
        """Return one OTel observation for a single pool stat key, or an empty list on failure."""
        if self._pool is None:
            return []
        try:
            stats = self._pool.get_stats()
        except Exception:
            # Stats access failures must never break the export pipeline.
            return []
        value = stats.get(stat_key)
        if value is None:
            return []
        return [
            metrics.Observation(
                int(value),
                attributes=self._base_attributes(),
            )
        ]

    @asynccontextmanager
    async def time_acquire(self) -> AsyncIterator[None]:
        """Context manager that records pool acquire latency."""

        start = monotonic()
        try:
            yield
        finally:
            elapsed = monotonic() - start
            self._acquire_histogram.record(elapsed, attributes=self._base_attributes())

    def record_optimistic_retry(self, *, table: str, outcome: str) -> None:
        """Increment the optimistic-lock retry counter for ``table`` with ``outcome``."""
        self._optimistic_retry_counter.add(
            1,
            attributes={
                **self._base_attributes(),
                self._ATTR_KEY_TABLE: table,
                self._ATTR_KEY_OUTCOME: outcome,
            },
        )

    def record_atomic_upsert(self, *, table: str, outcome: str) -> None:
        """Increment the atomic-upsert outcome counter for ``table``."""
        self._atomic_upsert_counter.add(
            1,
            attributes={
                **self._base_attributes(),
                self._ATTR_KEY_TABLE: table,
                self._ATTR_KEY_OUTCOME: outcome,
            },
        )

    def _base_attributes(self) -> dict[str, Any]:
        """Return the ``service`` and ``role`` label set shared by all instruments."""
        return {
            self._ATTR_KEY_SERVICE: self._service,
            self._ATTR_KEY_ROLE: self._role,
        }
