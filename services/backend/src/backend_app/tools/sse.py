"""Tools destination SSE stream — ``GET /v1/tools/stream`` (Phase 10 P10-A2).

Live updates for the Tools **per-tenant catalog feed** (tools-prd §4.10).
Mirrors the existing SSE convention exactly (cross-audit §5.2 + inbox /
home / project / routine SSE adapters):

* Single ``GET /v1/<resource>/stream`` route on the public plane.
* Typed ``event:`` / ``id:`` / ``data:`` frames.
* Monotonic ``sequence_no`` per ``(org_id, user_id)`` channel, used as
  the SSE ``id:`` field so browsers' EventSource implementation replays
  via ``Last-Event-ID`` on reconnect.
* ``?after_sequence=N`` query fallback for clients that cannot set
  ``Last-Event-ID``.
* 30-second heartbeats (``: keepalive\\n\\n`` comment frames) so corporate
  proxies don't close idle connections.

Tenant isolation: channel key is ``(org_id, user_id)`` from the **verified**
bearer; never from the request body or query path.

Wire shape (matches ``ToolStreamEnvelope`` in api-types/tools.ts):

::

    event: tool_event
    id: 42
    data: {"event_id":"...","sequence_no":42,"event_type":"tool.created",
           "tool":{...},"created_at":"2026-05-18T...+00:00"}

The bus is stashed on ``app.state.tools_activity_bus`` so the service
layer publishes after every state change (created / updated / deleted /
error_threshold) and after every invocation (batched to ~1Hz at the
service layer — P10-A2 stages the publish API; the actual batching is a
future tuning knob).

This file mirrors the pattern from :mod:`backend_app.inbox.sse` 1-for-1;
the only differences are the channel name (``tool_event``), the event
literal type (``ToolStreamEventType``), and the payload (carries a
``tool`` or ``invocation`` field).
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
    """Class-namespaced constants for the tools SSE stream."""

    class Sse:
        EVENT_NAME = "tool_event"
        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        WAIT_TIMEOUT_SECONDS = 5.0

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


ToolStreamEventType = Literal[
    "tool.created",
    "tool.updated",
    "tool.deleted",
    "tool.invoked",
    "tool.error_threshold",
    "tool.heartbeat",
]
"""Closed enum mirroring the TS contract."""


class ToolStreamEnvelope(BaseModel):
    """SSE event payload — locked to ``ToolStreamEnvelope`` in api-types.

    A schema mismatch fails validation at the framing boundary instead
    of silently desyncing the FE.
    """

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: ToolStreamEventType
    tool: dict[str, Any] | None = None
    invocation: dict[str, Any] | None = None
    created_at: datetime

    def serialise(self) -> str:
        return self.model_dump_json()


class InMemoryToolsActivityBus:
    """Process-local pub/sub for the tools SSE stream.

    Same shape as :class:`InMemoryInboxActivityBus`. Production swaps for
    Postgres LISTEN/NOTIFY or Redis pubsub when ai-backend (or another
    process) needs to publish.
    """

    _instance: "InMemoryToolsActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryToolsActivityBus":
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
        self._events: dict[tuple[str, str], deque[ToolStreamEnvelope]] = defaultdict(
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
        event_type: ToolStreamEventType,
        tool: dict[str, Any] | None = None,
        invocation: dict[str, Any] | None = None,
    ) -> ToolStreamEnvelope:
        """Append an event and wake any active subscriber.

        Validation rules:
          * ``tool.invoked`` requires ``invocation``.
          * ``tool.created`` / ``tool.updated`` / ``tool.deleted`` /
            ``tool.error_threshold`` require ``tool``.
          * ``tool.heartbeat`` carries neither.
        """

        if event_type == "tool.heartbeat":
            if tool is not None or invocation is not None:
                raise ValueError("heartbeat must not carry tool/invocation")
        elif event_type == "tool.invoked":
            if invocation is None:
                raise ValueError("tool.invoked requires invocation")
        else:
            if tool is None:
                raise ValueError(f"event_type={event_type!r} requires tool")

        key = (org_id, user_id)
        self._cursors[key] += 1
        envelope = ToolStreamEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            tool=tool,
            invocation=invocation,
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
    ) -> Iterable[ToolStreamEnvelope]:
        events = self._events.get((org_id, user_id))
        if events is None:
            return ()
        return tuple(e for e in events if e.sequence_no > after_sequence)

    def latest_sequence_no(self, *, org_id: str, user_id: str) -> int:
        return self._cursors.get((org_id, user_id), 0)

    def unsubscribe(self, *, org_id: str, user_id: str) -> None:
        self._conditions.pop((org_id, user_id), None)


ToolsActivityBus = InMemoryToolsActivityBus


class ToolsSseAdapter:
    """Adapt :class:`ToolStreamEnvelope` to SSE for the tools stream."""

    @classmethod
    async def stream(
        cls,
        *,
        bus: ToolsActivityBus,
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
    def format_event(cls, event: ToolStreamEnvelope) -> bytes:
        body = (
            f"event: {Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


class LastEventIdResolver:
    """Compute the effective ``after_sequence`` cursor from header + query.

    Resolution order (matches the SSE spec):

    1. ``Last-Event-ID`` header.
    2. ``?after_sequence=N`` query param.
    3. ``0`` — full replay.
    """

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


def register_tool_sse_routes(
    app: FastAPI, *, bus: ToolsActivityBus | None = None
) -> None:
    """Attach ``GET /v1/tools/stream`` to a backend FastAPI app.

    Idempotent — re-registration is safe in tests that build multiple apps.
    """

    resolved_bus = bus or ToolsActivityBus.get_default()
    app.state.tools_activity_bus = resolved_bus

    @app.get(
        "/v1/tools/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_tool_events(
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
            ToolsSseAdapter.stream(
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
    "Constants",
    "InMemoryToolsActivityBus",
    "LastEventIdResolver",
    "ToolStreamEnvelope",
    "ToolStreamEventType",
    "ToolsActivityBus",
    "ToolsSseAdapter",
    "register_tool_sse_routes",
]
