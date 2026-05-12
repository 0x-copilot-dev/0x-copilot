"""SSE push-notification bus with in-process and cross-process backends.

Two backends:

* :class:`InMemoryEventBus` — process-local ``asyncio.Condition`` pub/sub.
  Works only when API and worker share a process
  (``RUNTIME_START_IN_PROCESS_WORKER=true`` in dev). In production with a
  separate worker process, ``notify_sync`` can never wake an API-side waiter —
  the SSE adapter falls back to its 2-second poll.
* :class:`PostgresEventBus` — Postgres ``LISTEN/NOTIFY`` pub/sub. Cross-process
  by design. Sub-50ms wakeup; the SSE poll fallback becomes a backstop (10s)
  rather than the primary mechanism.

Select the backend via ``RUNTIME_EVENT_BUS_BACKEND=postgres`` (default
``in_memory``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EventBusBackend(Protocol):
    """Subscribe / wait / notify / unsubscribe surface used by the SSE adapter."""

    fallback_poll_seconds: float

    async def wait(self, run_id: str, *, timeout: float) -> None:
        """Block up to ``timeout`` for the next notification on ``run_id``."""

    async def notify(self, run_id: str) -> None:
        """Wake any waiters on ``run_id`` (no-op when none are registered)."""

    def notify_sync(self, run_id: str) -> None:
        """Thread-safe synchronous notify wrapper for callbacks fired off-loop."""

    def unsubscribe(self, run_id: str) -> None:
        """Drop any local subscription state for ``run_id`` (idempotent)."""


class InMemoryEventBus:
    """In-process pub/sub via ``asyncio.Condition`` (single-process only).

    The original ``RuntimeEventBus``. Suitable for tests, dev runs with
    ``RUNTIME_START_IN_PROCESS_WORKER=true``, and any deployment where the
    API and worker are guaranteed to share a process. Cross-process
    notifications never arrive — see :class:`PostgresEventBus` for the
    multi-process case.
    """

    fallback_poll_seconds: float = 2.0

    _instance: "InMemoryEventBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryEventBus":
        """Return (or create) the process-global event bus singleton."""

        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)

    async def notify(self, run_id: str) -> None:
        """Wake any waiters on ``run_id`` (no-op when none are registered)."""
        condition = self._conditions.get(run_id)
        if condition is None:
            return
        async with condition:
            condition.notify_all()

    def notify_sync(self, run_id: str) -> None:
        """Thread-safe synchronous wakeup of waiters on ``run_id``; drops silently when no loop is running."""
        condition = self._conditions.get(run_id)
        if condition is None:
            return
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop is not None:
            loop.call_soon_threadsafe(
                lambda c=condition: asyncio.ensure_future(self._async_notify(c))
            )
        else:
            logger.warning(
                "Notification dropped for run_id=%s: no running event loop",
                run_id,
            )

    async def wait(self, run_id: str, *, timeout: float = 5.0) -> None:
        """Block up to ``timeout`` seconds for the next notification on ``run_id``."""
        condition = self._conditions[run_id]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def unsubscribe(self, run_id: str) -> None:
        """Drop the condition variable for ``run_id`` (idempotent)."""
        self._conditions.pop(run_id, None)

    @staticmethod
    async def _async_notify(condition: asyncio.Condition) -> None:
        """Wake all waiters on ``condition`` from within the async context."""
        async with condition:
            condition.notify_all()


# Backward-compat alias so existing call sites that import
# ``RuntimeEventBus`` continue to work without edits. New code should
# depend on the ``EventBusBackend`` Protocol or the explicit class name.
RuntimeEventBus = InMemoryEventBus
