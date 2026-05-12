"""``pg_stat_statements`` scraper and slow-query OTel hook.

``DbStatementMetricsCollector`` periodically reads ``pg_stat_statements`` and
exports per-digest counters to OTel. Query text is never exported; only the
SHA-256 digest of the normalised statement. ``SlowQueryTracer`` emits an OTel
span when a query duration crosses ``RUNTIME_DB_SLOW_QUERY_MS`` (default 500 ms);
spans carry only the digest, duration, and service/role. Both surfaces fail-soft
when ``pg_stat_statements`` is unavailable or OTel is not bootstrapped.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any


_LOGGER = logging.getLogger("ai_backend.observability.db_statements")
_METER_NAME = "ai_backend.db"


_NORMALIZE_LITERAL = re.compile(r"\$\d+")
_NORMALIZE_WS = re.compile(r"\s+")


class DbStatementDigest:
    """Stateless digest helper.

    Normalizes parameterized SQL (``$1``, ``$2``, …) to ``$N`` and
    collapses whitespace before SHA-256-hashing. The result is short
    enough to be a metric label without unbounded cardinality.
    """

    @staticmethod
    def for_statement(query: str) -> str:
        normalized = _NORMALIZE_LITERAL.sub("$N", query)
        normalized = _NORMALIZE_WS.sub(" ", normalized).strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class DbStatementMetricsCollectorEnv:
    """Env-var keys + defaults."""

    INTERVAL_SECONDS = "RUNTIME_DB_STATEMENT_SCRAPE_INTERVAL_SECONDS"
    ENABLED = "RUNTIME_DB_STATEMENT_SCRAPE_ENABLED"

    DEFAULT_INTERVAL_SECONDS = 60.0

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


class DbStatementMetricsCollector:
    """Periodic scraper for ``pg_stat_statements``.

    Run inside the worker process so the API process doesn't pay the
    every-minute SELECT. Disabled by default (``RUNTIME_DB_STATEMENT_SCRAPE_ENABLED=true``
    to opt in) so existing deploys don't suddenly scrape an extension
    they may not have installed.
    """

    def __init__(
        self,
        *,
        run_query: Any,
        interval_seconds: float | None = None,
    ) -> None:
        # ``run_query`` is an async callable returning a list of rows.
        # Decoupled so we don't have to drag the whole store into this
        # module just for one SELECT — the worker passes a tiny adapter
        # that issues the SQL on its existing pool.
        self._run_query = run_query
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else DbStatementMetricsCollectorEnv.env_float(
                DbStatementMetricsCollectorEnv.INTERVAL_SECONDS,
                DbStatementMetricsCollectorEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._meter = self._build_meter()
        self._extension_warning_emitted = False

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:  # pragma: no cover - optional dep
            return None
        try:
            return otel_metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - defensive
            return None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name="db-statement-metrics-collector"
        )

    async def stop(self) -> None:
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
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                await self.scrape_once()
            except Exception:
                _LOGGER.warning("db_statement_scrape_failed", exc_info=True)

    async def scrape_once(self) -> int:
        """One SELECT against ``pg_stat_statements``.

        Returns the number of digests recorded so callers can log it.
        """

        try:
            rows = await self._run_query(
                """
                SELECT
                    queryid::text       AS query_id,
                    calls               AS calls,
                    total_exec_time     AS total_exec_time_ms,
                    rows                AS rows,
                    query               AS query_text
                  FROM pg_stat_statements
                 ORDER BY total_exec_time DESC
                 LIMIT 200
                """
            )
        except Exception as exc:
            self._maybe_log_extension_warning(exc)
            return 0
        if not self._meter:
            return len(rows)
        for row in rows:
            digest = DbStatementDigest.for_statement(str(row.get("query_text", "")))
            attrs = {"digest": digest}
            self._record_counter(
                "db_statement_calls_total", int(row.get("calls", 0)), attrs
            )
            self._record_counter(
                "db_statement_total_time_seconds",
                float(row.get("total_exec_time_ms", 0.0)) / 1000.0,
                attrs,
            )
            self._record_counter(
                "db_statement_rows_total", int(row.get("rows", 0)), attrs
            )
        return len(rows)

    def _record_counter(self, name: str, value: float, attrs: dict[str, str]) -> None:
        try:
            counter = self._meter.create_counter(name)
            counter.add(value, attrs)
        except Exception:  # pragma: no cover - defensive
            return

    def _maybe_log_extension_warning(self, exc: BaseException) -> None:
        if self._extension_warning_emitted:
            return
        self._extension_warning_emitted = True
        _LOGGER.warning(
            "db_statement_scrape_disabled",
            extra={
                "metadata": {
                    "reason": "pg_stat_statements_unavailable",
                    "exc_type": type(exc).__name__,
                }
            },
        )


class SlowQueryTracerEnv:
    THRESHOLD_MS = "RUNTIME_DB_SLOW_QUERY_MS"
    DEFAULT_THRESHOLD_MS = 500


class SlowQueryTracer:
    """Emit an OTel span when a query exceeds the threshold.

    Spans NEVER carry query text — only the digest + duration. The
    threshold defaults to 500 ms; operators tune via
    ``RUNTIME_DB_SLOW_QUERY_MS``.

    Designed to wrap ``conn.execute`` — see ``observe(query, duration_ms)``
    for the surface a custom cursor wrapper or psycopg event hook would
    call.
    """

    def __init__(self, *, threshold_ms: int | None = None) -> None:
        if threshold_ms is None:
            raw = os.environ.get(
                SlowQueryTracerEnv.THRESHOLD_MS,
                str(SlowQueryTracerEnv.DEFAULT_THRESHOLD_MS),
            )
            try:
                threshold_ms = int(raw)
            except ValueError:
                threshold_ms = SlowQueryTracerEnv.DEFAULT_THRESHOLD_MS
        self._threshold_ms = threshold_ms
        self._tracer = self._build_tracer()

    @property
    def threshold_ms(self) -> int:
        return self._threshold_ms

    @staticmethod
    def _build_tracer() -> Any:
        try:
            from opentelemetry import trace as otel_trace
        except ImportError:  # pragma: no cover - optional dep
            return None
        try:
            return otel_trace.get_tracer(_METER_NAME)
        except Exception:  # pragma: no cover - defensive
            return None

    def observe(self, *, query: str, duration_ms: float) -> bool:
        """Emit a span when ``duration_ms`` exceeds the threshold.

        Returns True when a span fired so tests can assert without
        having to drive an OTel SDK. Production callers ignore the
        return value.
        """

        if duration_ms < self._threshold_ms:
            return False
        if self._tracer is None:
            return True
        try:
            with self._tracer.start_as_current_span("db.slow_query") as span:
                span.set_attribute(
                    "db.statement.digest", DbStatementDigest.for_statement(query)
                )
                span.set_attribute("db.statement.duration_ms", duration_ms)
        except Exception:  # pragma: no cover - defensive
            return True
        return True

    def time_block(self, query: str) -> "_SlowQueryTimer":
        """Helper context manager used by callers that wrap one statement."""

        return _SlowQueryTimer(self, query)


class _SlowQueryTimer:
    def __init__(self, tracer: SlowQueryTracer, query: str) -> None:
        self._tracer = tracer
        self._query = query
        self._started_at = 0.0

    def __enter__(self) -> "_SlowQueryTimer":
        self._started_at = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        duration_ms = (time.monotonic() - self._started_at) * 1000.0
        self._tracer.observe(query=self._query, duration_ms=duration_ms)

    async def __aenter__(self) -> "_SlowQueryTimer":
        self._started_at = time.monotonic()
        return self

    async def __aexit__(self, *args: object) -> None:
        duration_ms = (time.monotonic() - self._started_at) * 1000.0
        self._tracer.observe(query=self._query, duration_ms=duration_ms)
