"""Bounded retry helper for optimistic-lock CAS misses (C3).

The runtime worker calls ``update_run_status`` and similar at hot paths where
two competing writers (e.g. main run handler vs. cancel handler) can
legitimately race. Optimistic-lock CAS on ``agent_runs.row_version`` makes
those races detectable: the loser raises
:class:`ConcurrentRunUpdateError` instead of silently overwriting.

Use ``with_optimistic_retry`` to wrap an idempotent re-read-then-rewrite
operation. The helper applies bounded exponential backoff with jitter, then
re-raises the last error if attempts are exhausted.

Usage::

    run = await with_optimistic_retry(
        lambda: store.update_run_status(run_id=run_id, status=status),
    )

The supplied callable MUST re-read the run (or item) it intends to update on
each invocation; ``update_run_status`` already does its own SELECT inside the
same transaction, so wrapping the bare call is correct.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agent_runtime.persistence.errors import (
    ConcurrentMemoryItemUpdateError,
    ConcurrentRunUpdateError,
    PersistenceError,
)


_T = TypeVar("_T")

_DEFAULT_RETRYABLE: tuple[type[PersistenceError], ...] = (
    ConcurrentRunUpdateError,
    ConcurrentMemoryItemUpdateError,
)


async def with_optimistic_retry(
    operation: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.05,
    retryable: tuple[type[PersistenceError], ...] = _DEFAULT_RETRYABLE,
) -> _T:
    """Invoke ``operation`` with bounded retries on optimistic-lock conflicts.

    On each failed attempt sleeps ``base_delay_seconds * 2**attempt`` plus
    a small uniform jitter so two retrying writers don't lockstep. Each
    retry outcome is reported as ``db_optimistic_retry_total{outcome}`` via
    OTel so dashboards can alert on sustained churn.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    # Lazy import so this module stays importable in environments without
    # the OTel SDK (notably some test fixtures).
    from opentelemetry import metrics

    meter = metrics.get_meter("agent_runtime.persistence.pool")
    counter = meter.create_counter(
        name="db_optimistic_retry_total",
        description=(
            "Optimistic-lock retry outcomes. "
            "outcome=success|exhausted; table identifies the CAS target."
        ),
    )

    last_error: PersistenceError | None = None
    for attempt in range(max_attempts):
        try:
            result = await operation()
            if attempt > 0:
                counter.add(
                    1,
                    attributes={
                        "table": _table_for_error(last_error),
                        "outcome": "success",
                    },
                )
            return result
        except retryable as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            delay = base_delay_seconds * (2**attempt) + random.uniform(
                0, base_delay_seconds
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    counter.add(
        1,
        attributes={
            "table": _table_for_error(last_error),
            "outcome": "exhausted",
        },
    )
    raise last_error


def _table_for_error(error: PersistenceError | None) -> str:
    """Return the table name label for an OTel attribute, or ``"unknown"`` when unrecognised."""
    from agent_runtime.persistence.errors import (
        ConcurrentMemoryItemUpdateError,
        ConcurrentRunUpdateError,
    )

    if isinstance(error, ConcurrentRunUpdateError):
        return "agent_runs"
    if isinstance(error, ConcurrentMemoryItemUpdateError):
        return "runtime_memory_items"
    return "unknown"
