"""Periodic worker that materializes due recurring todos via the backend.

Implements implementation-plan §11.1 (Todos recurrence). The worker is a
thin scheduler that polls every ``RECURRENCE_TICK_SECONDS`` (default 60s)
and asks ``backend`` to materialize all ``todo_series`` rows whose
``next_materialize_at <= now``. The backend owns the storage transaction,
the ``FOR UPDATE SKIP LOCKED`` claim, and the
``(series_id, due_date)`` UNIQUE-constraint that makes the materialization
idempotent — re-running the materializer twice creates only one row per
due date.

The rule evaluator (``RecurrenceRuleEvaluator``) is a pure-Python class
held here so it can be unit-tested without a backend. Three rule kinds
are supported (matching the implementation-plan §11.1 spec field):

* ``rrule`` — RFC 5545 subset: ``FREQ`` (``DAILY`` / ``WEEKLY``),
  ``BYDAY`` (``MO``…``SU``), and ``INTERVAL`` (positive int).
* ``every_N_days:<N>`` — every ``N`` days from the previous due date.
* ``every_weekday`` — Mon-Fri, skipping weekends.

Cross-service contract: no direct Postgres access from this worker. All
writes go through the backend's ``/internal/v1/todos/series/materialize-due``
endpoint with the standard service-token + identity headers (see
``agent_runtime/api/user_policies_resolver.py`` for the canonical pattern
this mirrors). Network errors are swallowed and logged; the next tick
retries — duplicate materialization is impossible thanks to the UNIQUE
constraint.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — env + headers
# ---------------------------------------------------------------------------


class _Env:
    """Environment variable names used by the materializer."""

    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"
    TICK_SECONDS = "RECURRENCE_TICK_SECONDS"
    ENABLED = "RECURRENCE_MATERIALIZER_ENABLED"
    DEFAULT_TICK_SECONDS = 60.0


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Rule evaluator — pure, unit-testable
# ---------------------------------------------------------------------------


class RecurrenceRuleError(ValueError):
    """Raised when a recurrence rule + spec cannot be parsed."""


class _Weekday:
    """Two-letter RFC 5545 weekday codes mapped to ``date.weekday()`` (Mon=0)."""

    CODES: dict[str, int] = {
        "MO": 0,
        "TU": 1,
        "WE": 2,
        "TH": 3,
        "FR": 4,
        "SA": 5,
        "SU": 6,
    }


class RecurrenceRuleEvaluator:
    """Compute the next due date for a recurrence rule + spec.

    Stateless. Three rule kinds, each with a fixed spec grammar
    (see module docstring). ``next_due`` is the only public method —
    it returns the **next** due date strictly after ``previous_due``.
    """

    RULE_RRULE = "rrule"
    RULE_EVERY_N_DAYS = "every_N_days"
    RULE_EVERY_WEEKDAY = "every_weekday"
    SUPPORTED_RULES: tuple[str, ...] = (
        RULE_RRULE,
        RULE_EVERY_N_DAYS,
        RULE_EVERY_WEEKDAY,
    )

    _MAX_SCAN_DAYS = 366  # Safety bound for BYDAY scans (one full year).

    def next_due(self, *, rule: str, spec: str, previous_due: date) -> date:
        """Return the next due date strictly after ``previous_due``.

        Raises ``RecurrenceRuleError`` for any unsupported / malformed
        rule + spec combination. Callers are expected to validate
        upstream — the evaluator is the second wall.
        """
        if rule not in self.SUPPORTED_RULES:
            raise RecurrenceRuleError(f"unsupported rule: {rule}")
        if rule == self.RULE_EVERY_WEEKDAY:
            return self._next_weekday(previous_due)
        if rule == self.RULE_EVERY_N_DAYS:
            return self._next_every_n_days(spec=spec, previous_due=previous_due)
        return self._next_rrule(spec=spec, previous_due=previous_due)

    # ---- rule kinds -------------------------------------------------------

    def _next_weekday(self, previous_due: date) -> date:
        candidate = previous_due + timedelta(days=1)
        # Skip Saturday(5) and Sunday(6).
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)
        return candidate

    def _next_every_n_days(self, *, spec: str, previous_due: date) -> date:
        # Spec form: ``every_N_days:<positive int>``.
        prefix = "every_N_days:"
        if not spec.startswith(prefix):
            raise RecurrenceRuleError(
                f"every_N_days spec must start with '{prefix}', got '{spec}'"
            )
        tail = spec[len(prefix) :].strip()
        try:
            n = int(tail)
        except ValueError as exc:
            raise RecurrenceRuleError(
                f"every_N_days spec must be a positive int, got '{tail}'"
            ) from exc
        if n <= 0:
            raise RecurrenceRuleError(f"every_N_days spec must be > 0, got {n}")
        return previous_due + timedelta(days=n)

    def _next_rrule(self, *, spec: str, previous_due: date) -> date:
        parsed = self._parse_rrule_spec(spec)
        freq = parsed["FREQ"]
        interval = parsed["INTERVAL"]
        byday = parsed["BYDAY"]

        if freq == "DAILY":
            # BYDAY is meaningless with DAILY in our subset; ignore.
            return previous_due + timedelta(days=interval)

        if freq != "WEEKLY":
            raise RecurrenceRuleError(
                f"rrule FREQ must be DAILY or WEEKLY, got '{freq}'"
            )

        if not byday:
            # Plain WEEKLY: same weekday as previous_due, +interval weeks.
            return previous_due + timedelta(days=7 * interval)

        # WEEKLY + BYDAY: scan forward day-by-day; pick the first day whose
        # weekday is in BYDAY AND whose week-offset is a multiple of
        # ``interval`` relative to ``previous_due``'s ISO week.
        prev_year, prev_week, _ = previous_due.isocalendar()
        prev_week_start = previous_due - timedelta(days=previous_due.weekday())
        target_weekdays = {_Weekday.CODES[code] for code in byday}
        for offset in range(1, self._MAX_SCAN_DAYS + 1):
            candidate = previous_due + timedelta(days=offset)
            if candidate.weekday() not in target_weekdays:
                continue
            candidate_week_start = candidate - timedelta(days=candidate.weekday())
            week_delta_days = (candidate_week_start - prev_week_start).days
            if week_delta_days % (7 * interval) != 0:
                continue
            return candidate
        raise RecurrenceRuleError(
            f"no rrule match within {self._MAX_SCAN_DAYS} days for spec '{spec}'"
        )

    # ---- rrule spec parsing -----------------------------------------------

    def _parse_rrule_spec(self, spec: str) -> dict[str, object]:
        """Parse the RFC 5545 subset we support; return ``{FREQ, INTERVAL, BYDAY}``."""
        parts = [piece for piece in spec.split(";") if piece]
        out: dict[str, object] = {"INTERVAL": 1, "BYDAY": ()}
        for piece in parts:
            if "=" not in piece:
                raise RecurrenceRuleError(
                    f"malformed rrule fragment '{piece}' in spec '{spec}'"
                )
            key, value = piece.split("=", 1)
            key = key.strip().upper()
            value = value.strip().upper()
            if key == "FREQ":
                out["FREQ"] = value
            elif key == "INTERVAL":
                try:
                    interval = int(value)
                except ValueError as exc:
                    raise RecurrenceRuleError(
                        f"rrule INTERVAL must be int, got '{value}'"
                    ) from exc
                if interval <= 0:
                    raise RecurrenceRuleError(
                        f"rrule INTERVAL must be > 0, got {interval}"
                    )
                out["INTERVAL"] = interval
            elif key == "BYDAY":
                codes = tuple(code.strip() for code in value.split(",") if code.strip())
                for code in codes:
                    if code not in _Weekday.CODES:
                        raise RecurrenceRuleError(
                            f"unknown BYDAY code '{code}' in spec '{spec}'"
                        )
                out["BYDAY"] = codes
            else:
                # Unsupported part: refuse (don't silently ignore — caller
                # is using a feature we don't model).
                raise RecurrenceRuleError(
                    f"unsupported rrule key '{key}' in spec '{spec}'"
                )
        if "FREQ" not in out:
            raise RecurrenceRuleError(f"rrule spec missing required FREQ: '{spec}'")
        return out


# ---------------------------------------------------------------------------
# Backend client port + implementations
# ---------------------------------------------------------------------------


class MaterializeOutcome(BaseModel):
    """Result of a single materialize-due call to the backend."""

    model_config = ConfigDict(frozen=True)
    materialized: int = Field(default=0, ge=0)
    skipped_duplicates: int = Field(default=0, ge=0)
    series_processed: int = Field(default=0, ge=0)

    @field_validator("materialized", "skipped_duplicates", "series_processed")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("counts must be non-negative")
        return value


@runtime_checkable
class TodoRecurrenceBackendClient(Protocol):
    """Port for the materialize-due RPC against ``backend``.

    Implementations must return a ``MaterializeOutcome`` (never raise) when
    the backend lane is not configured or the fetch fails — the next tick
    will retry. The UNIQUE constraint on ``(series_id, due_date)`` means
    "retry vs duplicate" is decided by the backend, not the worker.
    """

    async def materialize_due(self, *, now: datetime) -> MaterializeOutcome:
        """Trigger a server-side claim + materialize pass for all due series."""


class HttpTodoRecurrenceBackendClient:
    """Production client that POSTs ``/internal/v1/todos/series/materialize-due``.

    The injected ``httpx.AsyncClient`` lifecycle is the caller's
    responsibility. Network and HTTP errors are swallowed; the worker
    re-polls on the next tick.
    """

    PATH = "/internal/v1/todos/series/materialize-due"

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        backend_url: str,
        service_token: str,
    ) -> None:
        self._client = http_client
        self._backend_url = backend_url.rstrip("/")
        self._service_token = service_token

    async def materialize_due(self, *, now: datetime) -> MaterializeOutcome:
        try:
            response = await self._client.post(
                f"{self._backend_url}{self.PATH}",
                json={"now": now.astimezone(timezone.utc).isoformat()},
                headers={
                    _Headers.SERVICE_TOKEN: self._service_token,
                    # Materialization is a system-level operation: the
                    # per-row ``tenant_id`` lives on each ``todo_series``
                    # row. The org/user headers are present for audit
                    # ("actor=system") and to satisfy backend's auth gate
                    # (see CLAUDE.md auth rules — service-token requires
                    # both x-enterprise-org-id and x-enterprise-user-id).
                    _Headers.ORG: "system",
                    _Headers.USER: "system",
                },
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "todo_recurrence.fetch_failed",
                extra={
                    "metadata": {"error_class": exc.__class__.__name__},
                },
            )
            return MaterializeOutcome()
        if response.status_code >= 400:
            _LOGGER.warning(
                "todo_recurrence.fetch_non_2xx",
                extra={
                    "metadata": {"status_code": response.status_code},
                },
            )
            return MaterializeOutcome()
        try:
            body = response.json()
        except ValueError:
            return MaterializeOutcome()
        if not isinstance(body, dict):
            return MaterializeOutcome()
        try:
            return MaterializeOutcome(**body)
        except Exception:
            _LOGGER.warning(
                "todo_recurrence.bad_response_shape",
                extra={"metadata": {"keys": sorted(body.keys())}},
            )
            return MaterializeOutcome()


class NullTodoRecurrenceBackendClient:
    """No-op client used when the trusted-backend lane is not configured."""

    async def materialize_due(self, *, now: datetime) -> MaterializeOutcome:
        """Return an empty outcome unconditionally."""
        return MaterializeOutcome()


class TodoRecurrenceBackendClientFactory:
    """Select the appropriate client from environment configuration."""

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> TodoRecurrenceBackendClient:
        """Return an HTTP client when env is set, else the null client."""
        backend_url = os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token or http_client is None:
            return NullTodoRecurrenceBackendClient()
        return HttpTodoRecurrenceBackendClient(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


class TodoRecurrenceMaterializerEnv:
    """Env-var helpers (mirrors ``RetentionSweeperLoopEnv``)."""

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


class TodoRecurrenceMaterializerLoop:
    """Polls every ``tick_seconds`` (default 60s) and asks backend to materialize."""

    def __init__(
        self,
        *,
        client: TodoRecurrenceBackendClient,
        tick_seconds: float | None = None,
        clock: object | None = None,
    ) -> None:
        self._client = client
        self._tick = (
            tick_seconds
            if tick_seconds is not None
            else TodoRecurrenceMaterializerEnv.env_float(
                _Env.TICK_SECONDS, _Env.DEFAULT_TICK_SECONDS
            )
        )
        # ``clock`` is any callable returning ``datetime`` — injected for tests.
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background loop; idempotent if already running."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name="todo-recurrence-materializer"
        )

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it to finish."""
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
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
                return
            except TimeoutError:
                pass
            try:
                await self.tick_once()
            except Exception:
                _LOGGER.warning("todo_recurrence.tick_failed", exc_info=True)

    async def tick_once(self) -> MaterializeOutcome:
        """Run one materialize-due pass.

        Idempotency lives on the backend's UNIQUE ``(series_id, due_date)``
        constraint. Calling ``tick_once`` twice in a row with the same
        clock will at most materialize each due series once — the second
        call's outcome reports ``skipped_duplicates`` for the rows the
        UNIQUE constraint rejected.
        """
        outcome = await self._client.materialize_due(now=self._clock())
        _LOGGER.info(
            "todo_recurrence.tick",
            extra={
                "metadata": {
                    "materialized": outcome.materialized,
                    "skipped_duplicates": outcome.skipped_duplicates,
                    "series_processed": outcome.series_processed,
                }
            },
        )
        return outcome
