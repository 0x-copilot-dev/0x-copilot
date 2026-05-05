"""OTel meters for backend DB pool health (C4).

Mirrors ``services/ai-backend/.../pool_metrics.py`` so dashboards and alerts
share one contract. Backend uses the synchronous ``psycopg_pool.ConnectionPool``;
``get_stats()`` exposes the same shape as the async pool, so the gauges are
populated identically.
"""

from __future__ import annotations

from contextlib import contextmanager
from time import monotonic
from typing import TYPE_CHECKING, Any, Iterator

from opentelemetry import metrics


if TYPE_CHECKING:  # pragma: no cover -- typing-only import
    from psycopg_pool import ConnectionPool


_METER_NAME = "backend_app.db.pool"


class PoolMetrics:
    """Per-store OTel meter wrapper for the backend's sync pool.

    One instance is created per ``PostgresConnectionPool`` so the
    ``service`` and ``role`` attributes are applied uniformly. Gauges are
    observable callbacks bound to the pool reference; histograms and
    counters are recorded inline.
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
        self._pool: ConnectionPool | None = None
        self._gauges_registered = False

    def bind_pool(self, pool: ConnectionPool) -> None:
        """Register pool-stats observable gauges. Idempotent per instance."""

        if self._gauges_registered:
            return
        self._pool = pool
        self._meter.create_observable_gauge(
            name="db_pool_size",
            callbacks=[lambda options: self._observe_pool_stat("pool_size")],
            description="Current configured size of the connection pool.",
        )
        self._meter.create_observable_gauge(
            name="db_pool_in_use",
            callbacks=[lambda options: self._observe_pool_stat("pool_size_used")],
            description="Connections currently checked out of the pool.",
        )
        self._meter.create_observable_gauge(
            name="db_pool_waiting",
            callbacks=[lambda options: self._observe_pool_stat("requests_waiting")],
            description="Pending pool acquire() requests blocked on capacity.",
        )
        self._gauges_registered = True

    def _observe_pool_stat(self, stat_key: str) -> list[metrics.Observation]:
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
            metrics.Observation(int(value), attributes=self._base_attributes()),
        ]

    @contextmanager
    def time_acquire(self) -> Iterator[None]:
        """Context manager that records pool acquire latency."""

        start = monotonic()
        try:
            yield
        finally:
            elapsed = monotonic() - start
            self._acquire_histogram.record(elapsed, attributes=self._base_attributes())

    def record_optimistic_retry(self, *, table: str, outcome: str) -> None:
        self._optimistic_retry_counter.add(
            1,
            attributes={
                **self._base_attributes(),
                self._ATTR_KEY_TABLE: table,
                self._ATTR_KEY_OUTCOME: outcome,
            },
        )

    def record_atomic_upsert(self, *, table: str, outcome: str) -> None:
        self._atomic_upsert_counter.add(
            1,
            attributes={
                **self._base_attributes(),
                self._ATTR_KEY_TABLE: table,
                self._ATTR_KEY_OUTCOME: outcome,
            },
        )

    def _base_attributes(self) -> dict[str, Any]:
        return {
            self._ATTR_KEY_SERVICE: self._service,
            self._ATTR_KEY_ROLE: self._role,
        }
