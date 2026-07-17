"""Memory destination SSE stream — ``GET /v1/memory/stream``.

Live updates for the Memory **per-user feed** (sub-PRD §4.2). The stream
emits one event per noteworthy lifecycle change for the
``(org_id, user_id)`` channel — the FE updates the catalog, lifts the
proposal toast, and de-dupes by ``event_id``.

Mirrors the canonical SSE convention (cross-audit §5.2 + the inbox /
home / connectors streams):

* ``GET /v1/<resource>/stream`` route shape.
* Monotonic ``sequence_no`` per ``(org_id, user_id)`` channel; reconnect
  via ``Last-Event-ID`` (browsers) or ``?after_sequence=N`` (curl).
* 30-second heartbeat comment frames so corporate proxies don't close
  idle connections.

In-memory bus is process-local; production bus is Postgres
``LISTEN/NOTIFY`` (out of scope for P12-A3 — same as the home + inbox
buses).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class _Constants:
    class Sse:
        EVENT_NAME = "memory_event"
        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        WAIT_TIMEOUT_SECONDS = 5.0

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


# ---------------------------------------------------------------------------
# Wire schema (mirrors packages/api-types/src/memory.ts::MemoryStreamEnvelope)
# ---------------------------------------------------------------------------


MemoryEventType = Literal[
    "memory.created",
    "memory.updated",
    "memory.deleted",
    "memory.proposal_appended",
    "memory.proposal_decided",
    "heartbeat",
]


class MemoryEventEnvelope(BaseModel):
    """SSE event payload — locked to packages/api-types::MemoryStreamEnvelope."""

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: MemoryEventType
    item: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None
    deleted_id: str | None = None
    created_at: datetime

    def serialise(self) -> str:
        return self.model_dump_json()


# ---------------------------------------------------------------------------
# In-memory bus
# ---------------------------------------------------------------------------


class InMemoryMemoryActivityBus:
    """Process-local pub/sub for the memory SSE stream.

    Channel key is ``(org_id, user_id)``. ``user`` events fan out only to
    the owner; ``workspace`` events would fan out to every tenant member
    — that's the bus consumer's job (the service publishes once per
    intended recipient channel). For P12-A3 we publish once per the
    actor's channel; broader tenant fan-out is the deployment composer's
    job (Postgres LISTEN/NOTIFY tenant-wide channel — same pattern as
    home).
    """

    _instance: "InMemoryMemoryActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryMemoryActivityBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default_for_tests(cls) -> None:
        cls._instance = None

    def __init__(self, *, max_buffer_per_channel: int | None = None) -> None:
        self._max_buffer = (
            max_buffer_per_channel or _Constants.Bus.DEFAULT_MAX_BUFFER_PER_CHANNEL
        )
        self._events: dict[tuple[str, str], deque[MemoryEventEnvelope]] = defaultdict(
            lambda: deque(maxlen=self._max_buffer)
        )
        self._cursors: dict[tuple[str, str], int] = defaultdict(int)
        self._conditions: dict[tuple[str, str], asyncio.Condition] = defaultdict(
            asyncio.Condition
        )

    async def publish(
        self,
        *,
        org_id: str,
        user_id: str,
        event_type: MemoryEventType,
        item: Any | None = None,
        proposal: Any | None = None,
        deleted_id: str | None = None,
    ) -> MemoryEventEnvelope:
        """Append an envelope + wake any active subscriber.

        ``item`` / ``proposal`` are Pydantic records (or dicts) — the bus
        normalises them to dicts so the wire JSON matches the api-types
        contract regardless of caller shape.
        """

        key = (org_id, user_id)
        self._cursors[key] += 1
        envelope = MemoryEventEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            item=_normalise(item),
            proposal=_normalise(proposal),
            deleted_id=deleted_id,
            created_at=datetime.now(timezone.utc),
        )
        self._events[key].append(envelope)
        condition = self._conditions.get(key)
        if condition is not None:
            async with condition:
                condition.notify_all()
        return envelope

    async def wait(
        self,
        *,
        org_id: str,
        user_id: str,
        timeout: float = _Constants.Cadence.WAIT_TIMEOUT_SECONDS,
    ) -> None:
        condition = self._conditions[(org_id, user_id)]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def list_after(
        self, *, org_id: str, user_id: str, after_sequence: int
    ) -> Iterable[MemoryEventEnvelope]:
        events = self._events.get((org_id, user_id))
        if events is None:
            return ()
        return tuple(e for e in events if e.sequence_no > after_sequence)

    def latest_sequence_no(self, *, org_id: str, user_id: str) -> int:
        return self._cursors.get((org_id, user_id), 0)

    def unsubscribe(self, *, org_id: str, user_id: str) -> None:
        self._conditions.pop((org_id, user_id), None)


MemoryActivityBus = InMemoryMemoryActivityBus


def _normalise(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return None


# ---------------------------------------------------------------------------
# SSE adapter
# ---------------------------------------------------------------------------


class MemorySseAdapter:
    """Adapt :class:`MemoryEventEnvelope` to SSE."""

    @classmethod
    async def stream(
        cls,
        *,
        bus: MemoryActivityBus,
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
                _Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS - elapsed, 0.0
            )
            slice_timeout = min(
                heartbeat_after if heartbeat_after > 0 else 0.001,
                _Constants.Cadence.WAIT_TIMEOUT_SECONDS,
            )
            await bus.wait(org_id=org_id, user_id=user_id, timeout=slice_timeout)
            if (
                loop.time() - last_emit_at
                >= _Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS
            ):
                last_emit_at = loop.time()
                yield _Constants.Sse.HEARTBEAT_COMMENT

    @classmethod
    def format_event(cls, event: MemoryEventEnvelope) -> bytes:
        body = (
            f"event: {_Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


class _LastEventIdResolver:
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


def register_memory_sse_routes(
    app: FastAPI, *, bus: MemoryActivityBus | None = None
) -> None:
    """Attach ``GET /v1/memory/stream`` to a backend FastAPI app."""

    resolved_bus = bus or MemoryActivityBus.get_default()
    app.state.memory_activity_bus = resolved_bus

    @app.get(
        "/v1/memory/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_memory_events(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias=_Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_after = _LastEventIdResolver.resolve(
            header_value=last_event_id, query_after_sequence=after_sequence
        )
        return StreamingResponse(
            MemorySseAdapter.stream(
                bus=resolved_bus,
                org_id=identity.org_id,
                user_id=identity.user_id,
                after_sequence=effective_after,
                follow=True,
                request=request,
            ),
            media_type=_Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )


__all__ = [
    "InMemoryMemoryActivityBus",
    "MemoryActivityBus",
    "MemoryEventEnvelope",
    "MemoryEventType",
    "MemorySseAdapter",
    "register_memory_sse_routes",
]
