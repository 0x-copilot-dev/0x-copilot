"""Connectors destination SSE stream — ``GET /v1/connectors/stream``.

Live updates for the Connectors **per-tenant feed** (connectors-prd §4.9
+ §1.6 status taxonomy). Mirrors :mod:`backend_app.inbox.sse` exactly —
the only thing that differs is the wire payload (``connector`` instead
of ``item``) and the closed event-name enum.

See ``backend_app.inbox.sse`` for the rationale behind the framing
choices (monotonic ``sequence_no``, ``Last-Event-ID`` resume, 30s
heartbeats, in-memory bus dev-tier). This module re-uses the same
discipline without re-deriving it: single source of truth lives in the
inbox stream's module docstring; this one calls out only what differs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes

logger = logging.getLogger(__name__)


class Constants:
    """Class-namespaced constants — mirrors :class:`InboxSseAdapter`."""

    class Sse:
        EVENT_NAME = "connector_event"
        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        WAIT_TIMEOUT_SECONDS = 5.0

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


# Closed enum mirroring the TS contract.
ConnectorEventType = Literal[
    "connector.created",
    "connector.status_changed",
    "connector.scope_changed",
    "connector.error_threshold",
    "heartbeat",
]


class ConnectorEventEnvelope(BaseModel):
    """SSE event payload — locked to packages/api-types::ConnectorStreamEnvelope."""

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: ConnectorEventType
    connector: dict[str, Any] | None = None
    created_at: datetime

    def serialise(self) -> str:
        return self.model_dump_json()


class InMemoryConnectorActivityBus:
    """Process-local pub/sub for the connectors SSE stream.

    Channel key is ``(org_id, user_id)`` — the connectors stream is
    user-scoped (the owner sees status changes on their own connectors;
    admin compliance reads use a separate paged audit endpoint, not SSE).
    Same surface as :class:`InMemoryInboxActivityBus`.
    """

    _instance: "InMemoryConnectorActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryConnectorActivityBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default_for_tests(cls) -> None:
        cls._instance = None

    def __init__(self, *, max_buffer_per_channel: int | None = None) -> None:
        self._max_buffer = (
            max_buffer_per_channel or Constants.Bus.DEFAULT_MAX_BUFFER_PER_CHANNEL
        )
        self._events: dict[tuple[str, str], deque[ConnectorEventEnvelope]] = (
            defaultdict(lambda: deque(maxlen=self._max_buffer))
        )
        self._cursors: dict[tuple[str, str], int] = defaultdict(int)
        self._conditions: dict[tuple[str, str], asyncio.Condition] = defaultdict(
            asyncio.Condition
        )
        # PRD-I I2 — serving loop bound ONCE at composition (app lifespan
        # startup). ``None`` = unbound = legacy poll-slice semantics.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Strong refs to in-flight notify tasks (fire-and-forget tasks
        # are GC-eligible without one — asyncio docs' standard pattern).
        self._notify_tasks: set[asyncio.Task[None]] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the serving event loop for immediate waiter wakeup (I2).

        Called ONCE from the app lifespan startup — the lifespan owns the
        loop, so binding happens at composition time, never by sniffing
        for a running loop at publish time. When bound,
        :meth:`publish_nowait` wakes async waiters via
        ``loop.call_soon_threadsafe`` instead of leaving them to the
        ≤5 s poll slice. When unbound (unit tests, non-server contexts)
        behavior is byte-identical to the legacy poll-slice pickup.
        """

        self._loop = loop

    def unbind_loop(self) -> None:
        """Drop the loop binding (app lifespan shutdown).

        The bus is a process-global singleton that can outlive one app's
        loop (tests build many apps); unbinding restores legacy
        semantics rather than leaving a dangling closed-loop reference.
        """

        self._loop = None

    @property
    def loop_bound(self) -> bool:
        """True when a serving loop is bound (composition observability)."""

        return self._loop is not None

    async def publish(
        self,
        *,
        org_id: str,
        user_id: str,
        event_type: ConnectorEventType,
        connector: dict[str, Any] | None,
    ) -> ConnectorEventEnvelope:
        envelope = self._append(
            org_id=org_id,
            user_id=user_id,
            event_type=event_type,
            connector=connector,
        )
        condition = self._conditions.get((org_id, user_id))
        if condition is not None:
            async with condition:
                condition.notify_all()
        return envelope

    def publish_nowait(
        self,
        *,
        org_id: str,
        user_id: str,
        event_type: ConnectorEventType,
        connector: dict[str, Any] | None,
    ) -> ConnectorEventEnvelope:
        """Synchronous publish for callers outside the event loop.

        The MCP mutation handlers are plain ``def`` routes (threadpool),
        so the connectors write-through path cannot ``await``. This
        appends to the ring buffer, then — when a serving loop is bound
        (see :meth:`bind_loop`) — schedules an immediate waiter wakeup
        on that loop via ``call_soon_threadsafe`` (PRD-I I2). When
        unbound, waiters are NOT notified and the SSE read loop's
        ≤``WAIT_TIMEOUT_SECONDS`` poll slice picks the envelope up —
        the original synchronous-by-design semantics (same rationale as
        the :mod:`backend_app.projects.sse` bus publish).

        Failure discipline: wakeup scheduling must never fail the
        publish (the envelope is already appended and durable in the
        ring buffer); a dead/closed loop degrades to poll-slice pickup.
        """

        envelope = self._append(
            org_id=org_id,
            user_id=user_id,
            event_type=event_type,
            connector=connector,
        )
        self._schedule_wakeup(org_id=org_id, user_id=user_id)
        return envelope

    def _schedule_wakeup(self, *, org_id: str, user_id: str) -> None:
        """Wake the channel's waiters on the bound loop, if any.

        Thread-safe: callable from threadpool workers. The condition
        lookup happens on the LOOP thread (inside the scheduled
        callback), where waiters register — so there is no cross-thread
        race on the conditions dict.
        """

        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._notify_channel_on_loop, (org_id, user_id))
        except RuntimeError:
            # Loop already closed (singleton outlived an app instance
            # that never unbound). The envelope is appended; waiters
            # fall back to the poll slice — never fail the publish.
            logger.debug(
                "connector bus wakeup skipped: bound loop is closed "
                "(org_id=%s user_id=%s)",
                org_id,
                user_id,
            )

    def _notify_channel_on_loop(self, key: tuple[str, str]) -> None:
        """Loop-thread callback: notify exactly this channel's waiters."""

        condition = self._conditions.get(key)
        if condition is None:
            # No subscriber ever waited on this channel — nothing to
            # wake; the ring buffer replay serves any later subscriber.
            return
        task = asyncio.ensure_future(self._notify(condition))
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)

    @staticmethod
    async def _notify(condition: asyncio.Condition) -> None:
        async with condition:
            condition.notify_all()

    def _append(
        self,
        *,
        org_id: str,
        user_id: str,
        event_type: ConnectorEventType,
        connector: dict[str, Any] | None,
    ) -> ConnectorEventEnvelope:
        if event_type != "heartbeat" and connector is None:
            raise ValueError(
                f"connector is required for event_type={event_type!r}; got None."
            )
        if event_type == "heartbeat" and connector is not None:
            raise ValueError("connector must be None for heartbeat events.")
        key = (org_id, user_id)
        self._cursors[key] += 1
        envelope = ConnectorEventEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            connector=connector,
            created_at=datetime.now(timezone.utc),
        )
        self._events[key].append(envelope)
        return envelope

    async def wait(
        self,
        *,
        org_id: str,
        user_id: str,
        timeout: float = Constants.Cadence.WAIT_TIMEOUT_SECONDS,
    ) -> None:
        condition = self._conditions[(org_id, user_id)]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def list_after(
        self, *, org_id: str, user_id: str, after_sequence: int
    ) -> Iterable[ConnectorEventEnvelope]:
        events = self._events.get((org_id, user_id))
        if events is None:
            return ()
        return tuple(event for event in events if event.sequence_no > after_sequence)

    def latest_sequence_no(self, *, org_id: str, user_id: str) -> int:
        return self._cursors.get((org_id, user_id), 0)

    def unsubscribe(self, *, org_id: str, user_id: str) -> None:
        self._conditions.pop((org_id, user_id), None)


# Backward-compat alias (matches the inbox/home naming convention).
ConnectorActivityBus = InMemoryConnectorActivityBus


class ConnectorSseAdapter:
    """Adapt :class:`ConnectorEventEnvelope` to SSE bytes."""

    @classmethod
    async def stream(
        cls,
        *,
        bus: ConnectorActivityBus,
        org_id: str,
        user_id: str,
        after_sequence: int,
        follow: bool = True,
        request: Request | None = None,
    ) -> AsyncIterator[bytes]:
        latest_sequence = after_sequence
        loop = asyncio.get_event_loop()
        last_emit_at = loop.time()
        while True:
            for event in bus.list_after(
                org_id=org_id, user_id=user_id, after_sequence=latest_sequence
            ):
                latest_sequence = max(latest_sequence, event.sequence_no)
                last_emit_at = loop.time()
                yield cls.format_event(event)
            if not follow:
                return
            if request is not None and await request.is_disconnected():
                bus.unsubscribe(org_id=org_id, user_id=user_id)
                return
            elapsed = loop.time() - last_emit_at
            heartbeat_after = max(
                Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS - elapsed,
                0.0,
            )
            slice_timeout = min(
                heartbeat_after if heartbeat_after > 0 else 0.001,
                Constants.Cadence.WAIT_TIMEOUT_SECONDS,
            )
            await bus.wait(org_id=org_id, user_id=user_id, timeout=slice_timeout)
            if (
                loop.time() - last_emit_at
                >= Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS
            ):
                last_emit_at = loop.time()
                yield Constants.Sse.HEARTBEAT_COMMENT

    @classmethod
    def format_event(cls, event: ConnectorEventEnvelope) -> bytes:
        body = (
            f"event: {Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


class LastEventIdResolver:
    """Compute the effective ``after_sequence`` from header + query."""

    @classmethod
    def resolve(cls, *, header_value: str | None, query_after_sequence: int) -> int:
        if header_value is not None:
            parsed = cls._parse_non_negative_int(header_value)
            if parsed is not None:
                return parsed
        return max(query_after_sequence, 0)

    @staticmethod
    def _parse_non_negative_int(raw: str) -> int | None:
        candidate = raw.strip()
        if not candidate:
            return None
        try:
            value = int(candidate)
        except ValueError:
            return None
        if value < 0:
            return None
        return value


def register_connector_sse_routes(
    app: FastAPI, *, bus: ConnectorActivityBus | None = None
) -> None:
    """Attach ``GET /v1/connectors/stream`` to a backend FastAPI app."""

    resolved_bus = bus or ConnectorActivityBus.get_default()
    app.state.connector_activity_bus = resolved_bus

    @app.get(
        "/v1/connectors/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_connector_events(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_after = LastEventIdResolver.resolve(
            header_value=last_event_id, query_after_sequence=after_sequence
        )
        return StreamingResponse(
            ConnectorSseAdapter.stream(
                bus=resolved_bus,
                org_id=identity.org_id,
                user_id=identity.user_id,
                after_sequence=effective_after,
                follow=True,
                request=request,
            ),
            media_type=Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )


__all__ = [
    "ConnectorActivityBus",
    "ConnectorEventEnvelope",
    "ConnectorEventType",
    "ConnectorSseAdapter",
    "Constants",
    "InMemoryConnectorActivityBus",
    "LastEventIdResolver",
    "register_connector_sse_routes",
]
