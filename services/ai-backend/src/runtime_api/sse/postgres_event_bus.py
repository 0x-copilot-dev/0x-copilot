"""Postgres ``LISTEN/NOTIFY`` cross-process SSE event bus.

Worker appends an event → Postgres adapter fires
``NOTIFY runtime_events_v1, '<run_id>:<seq>'``. The API process holds one
dedicated ``LISTEN`` connection; its background ``listen_loop`` task reads
notifications and wakes the per-run ``asyncio.Event`` registered by the SSE
adapter. The SSE adapter wakes within milliseconds — the 10-second poll
fallback is a backstop for missed notifications during reconnect.

Connection ownership:

* One dedicated psycopg ``AsyncConnection`` is held for the process lifetime.
  ``LISTEN`` is connection-bound; recycling the connection breaks the subscription.
* NOTIFY emissions come from regular pool connections (NOTIFY inside a
  transaction delivers when the transaction commits).
* On listener-side drop, ``listen_loop`` reconnects with exponential backoff
  (capped at ``MAX_BACKOFF_SECONDS``) and re-issues ``LISTEN``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Versioned channel name so a future schema change can use a parallel
# ``runtime_events_v2`` without disrupting in-flight clients.
CHANNEL = "runtime_events_v1"

# Reconnect backoff caps so a flapping DB doesn't busy-loop the listener.
INITIAL_BACKOFF_SECONDS = 0.5
MAX_BACKOFF_SECONDS = 30.0


def _format_payload(run_id: str, sequence_no: int) -> str:
    """Serialize a NOTIFY payload as ``<run_id>:<sequence_no>``.

    Postgres NOTIFY payloads are limited to 8000 bytes; ``<uuid>:<int>``
    is well under that. Format is parsed by :meth:`PostgresEventBus._dispatch`.
    """

    return f"{run_id}:{sequence_no}"


class PostgresEventBus:
    """Cross-process SSE bus over Postgres ``LISTEN/NOTIFY``.

    Construction holds a connection factory rather than the connection
    itself so the listener can be reconnected on drop. The factory is
    typically ``lambda: AsyncConnection.connect(dsn, autocommit=True)`` —
    autocommit is required so ``LISTEN`` takes effect immediately.

    Use as::

        bus = PostgresEventBus(connection_factory=...)
        await bus.start()        # spawns the listen_loop task
        try:
            ...
        finally:
            await bus.stop()
    """

    fallback_poll_seconds: float = 10.0

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Awaitable[object]],
        channel: str = CHANNEL,
    ) -> None:
        self._connection_factory = connection_factory
        self._channel = channel
        self._listeners: dict[str, asyncio.Event] = {}
        self._listen_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._connection: object | None = None

    async def start(self) -> None:
        """Spawn the background listen loop. Idempotent."""

        if self._listen_task is not None and not self._listen_task.done():
            return
        self._stop_event.clear()
        self._listen_task = asyncio.create_task(
            self._listen_loop(), name="postgres-event-bus-listen"
        )

    async def stop(self) -> None:
        """Signal the loop to exit and await it. Idempotent."""

        self._stop_event.set()
        task = self._listen_task
        self._listen_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._close_connection()

    async def wait(self, run_id: str, *, timeout: float) -> None:
        """Block up to ``timeout`` seconds for a notification on ``run_id``.

        Multiple concurrent waiters on the same run_id share one
        ``asyncio.Event``; ``set()`` wakes all of them. After return, the
        event is cleared so the next call sees a fresh wait.
        """

        event = self._listeners.get(run_id)
        if event is None:
            event = asyncio.Event()
            self._listeners[run_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            # Clear so the next wait returns only on a *new* notification.
            event.clear()

    async def notify(self, run_id: str) -> None:
        """Wake local waiters on ``run_id``.

        This is the in-process fast path; the cross-process path is the
        ``NOTIFY`` fired by the Postgres adapter inside ``append_event``.
        Both wake the same ``asyncio.Event`` so the SSE adapter does not
        care which path delivered the wakeup.
        """

        event = self._listeners.get(run_id)
        if event is not None:
            event.set()

    def notify_sync(self, run_id: str) -> None:
        """Cross-thread synchronous wakeup of local waiters on ``run_id``.

        The Postgres bus expects most wakeups to come from the cross-
        process ``LISTEN/NOTIFY`` path, but this method preserves the
        legacy in-process fast path (e.g. when ``RUNTIME_START_IN_PROCESS_
        WORKER=true``) so a producer on the same process still wakes
        immediately.
        """

        event = self._listeners.get(run_id)
        if event is None:
            return
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop is not None:
            loop.call_soon_threadsafe(event.set)
        else:
            # No running loop — log and drop. SSE adapter's poll fallback
            # picks the event up within ``fallback_poll_seconds``.
            logger.warning(
                "Notification dropped for run_id=%s: no running event loop",
                run_id,
            )

    def unsubscribe(self, run_id: str) -> None:
        self._listeners.pop(run_id, None)

    def _dispatch(self, payload: str) -> None:
        """Parse a NOTIFY payload and wake the matching local listener."""

        run_id, sep, _seq = payload.partition(":")
        if not sep or not run_id:
            logger.warning(
                "Dropping malformed runtime_events notification: %r", payload
            )
            return
        event = self._listeners.get(run_id)
        if event is not None:
            event.set()

    async def _listen_loop(self) -> None:
        """Connect, LISTEN, dispatch notifications until ``stop`` is set."""

        backoff = INITIAL_BACKOFF_SECONDS
        while not self._stop_event.is_set():
            try:
                await self._open_connection()
                conn = self._connection
                if conn is None:  # pragma: no cover — defensive
                    raise RuntimeError("listener connection is None after open")
                await conn.execute(f"LISTEN {self._channel}")  # type: ignore[union-attr]
                # Reset backoff after a successful connect.
                backoff = INITIAL_BACKOFF_SECONDS
                logger.info("PostgresEventBus listening on channel=%r", self._channel)
                async for notify in conn.notifies():  # type: ignore[union-attr]
                    if self._stop_event.is_set():
                        break
                    payload = getattr(notify, "payload", "")
                    self._dispatch(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "PostgresEventBus listen loop error; reconnecting in %.1fs",
                    backoff,
                )
                await self._close_connection()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    # Stop signalled during backoff — exit cleanly.
                    return
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, MAX_BACKOFF_SECONDS)

    async def _open_connection(self) -> None:
        """Open the listener connection if not already open."""
        if self._connection is not None:
            return
        self._connection = await self._connection_factory()

    async def _close_connection(self) -> None:
        """Close and discard the listener connection, swallowing errors to allow clean teardown."""
        conn = self._connection
        self._connection = None
        if conn is None:
            return
        close = getattr(conn, "close", None)
        if close is None:
            return
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            logger.exception("PostgresEventBus failed to close listener connection")
