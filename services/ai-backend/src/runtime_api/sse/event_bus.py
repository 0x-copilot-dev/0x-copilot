"""Async event notification bus for SSE push instead of poll."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class RuntimeEventBus:
    """Lightweight pub/sub that wakes SSE listeners when events are appended.

    In-process: uses asyncio.Condition so the SSE adapter wakes immediately
    when the worker appends an event, instead of polling every 250ms.
    """

    _instance: RuntimeEventBus | None = None

    @classmethod
    def get_default(cls) -> RuntimeEventBus:
        """Return (or create) the process-global event bus singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)

    async def notify(self, run_id: str) -> None:
        condition = self._conditions.get(run_id)
        if condition is None:
            return
        async with condition:
            condition.notify_all()

    def notify_sync(self, run_id: str) -> None:
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
        condition = self._conditions[run_id]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def unsubscribe(self, run_id: str) -> None:
        self._conditions.pop(run_id, None)

    @staticmethod
    async def _async_notify(condition: asyncio.Condition) -> None:
        async with condition:
            condition.notify_all()
