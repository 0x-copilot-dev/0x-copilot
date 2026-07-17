"""``GET /v1/team/stream`` — Team destination SSE.

Mirrors the cross-audit §5.2 convention: monotonic ``sequence_no`` per
``(org_id, user_id)``, ``Last-Event-ID`` reconnect, 30s heartbeats,
pass-through facade. The bus is a thin process-local pub/sub —
production replaces with Redis pubsub via the same Protocol shape (the
``TeamActivityBus`` typedef makes the swap drop-in; see
``backend_app.inbox.sse`` for the same discipline).

Wire envelope is locked at ``packages/api-types/src/team.ts``
(``TeamStreamEnvelope``); the JSON the bus emits must match.
"""

from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------------------
# Constants — mirrors backend_app.inbox.sse.Constants for symmetry.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced SSE constants — mirrors inbox/home convention."""

    class Sse:
        EVENT_NAME = "team_event"
        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        WAIT_TIMEOUT_SECONDS = 5.0

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


TeamStreamEventType = Literal[
    "team.presence_changed",
    "team.role_changed",
    "team.invited",
    "team.joined",
    "team.offboarded",
    "heartbeat",
]


class TeamStreamEnvelope(BaseModel):
    """SSE event payload — locked to api-types/team.ts ``TeamStreamEnvelope``.

    ``person`` carries the post-mutation projection so the FE can patch
    its in-memory list without a round-trip. ``offboarding`` is only
    present on ``team.offboarded``; ``project_id`` is the optional
    admin-scope filter (sub-PRD §3.1 wire docstring).
    """

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: TeamStreamEventType
    person: dict[str, Any] | None = None
    offboarding: dict[str, Any] | None = None
    project_id: str | None = None
    created_at: datetime

    def serialise(self) -> str:
        return self.model_dump_json()


class InMemoryTeamActivityBus:
    """Process-local pub/sub for the Team SSE stream.

    The channel key is ``(tenant_id, user_id)``. Tenant isolation is
    enforced on every publish/subscribe lookup; a subscriber on
    ``(org_a, u1)`` never sees ``(org_b, u1)``'s events.
    """

    _instance: "InMemoryTeamActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryTeamActivityBus":
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
        self._events: dict[tuple[str, str], deque[TeamStreamEnvelope]] = defaultdict(
            lambda: deque(maxlen=self._max_buffer)
        )
        self._cursors: dict[tuple[str, str], int] = defaultdict(int)
        self._conditions: dict[tuple[str, str], asyncio.Condition] = defaultdict(
            asyncio.Condition
        )

    async def publish(
        self,
        *,
        tenant_id: str,
        user_id: str,
        event_type: TeamStreamEventType,
        person: dict[str, Any] | None = None,
        offboarding: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> TeamStreamEnvelope:
        """Append an event and wake any active subscriber.

        ``person`` is required for every non-heartbeat frame so the FE
        can patch its in-memory row; the check lives here so a
        malformed publish never reaches the SSE wire.
        """

        if event_type != "heartbeat" and person is None:
            raise ValueError(
                f"person is required for event_type={event_type!r}; got None."
            )
        key = (tenant_id, user_id)
        self._cursors[key] += 1
        envelope = TeamStreamEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            person=person,
            offboarding=offboarding,
            project_id=project_id,
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
        tenant_id: str,
        user_id: str,
        timeout: float = Constants.Cadence.WAIT_TIMEOUT_SECONDS,
    ) -> None:
        condition = self._conditions[(tenant_id, user_id)]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def list_after(
        self, *, tenant_id: str, user_id: str, after_sequence: int
    ) -> Iterable[TeamStreamEnvelope]:
        events = self._events.get((tenant_id, user_id))
        if events is None:
            return ()
        return tuple(e for e in events if e.sequence_no > after_sequence)

    def latest_sequence_no(self, *, tenant_id: str, user_id: str) -> int:
        return self._cursors.get((tenant_id, user_id), 0)

    def unsubscribe(self, *, tenant_id: str, user_id: str) -> None:
        self._conditions.pop((tenant_id, user_id), None)


TeamActivityBus = InMemoryTeamActivityBus


class TeamSseAdapter:
    """Adapt :class:`TeamStreamEnvelope` to SSE for the Team stream."""

    @classmethod
    async def stream(
        cls,
        *,
        bus: TeamActivityBus,
        tenant_id: str,
        user_id: str,
        after_sequence: int,
        follow: bool = True,
        request: Request | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield replayed + live SSE frames for the connected channel."""

        latest = after_sequence
        loop = asyncio.get_event_loop()
        last_emit = loop.time()
        while True:
            for event in bus.list_after(
                tenant_id=tenant_id, user_id=user_id, after_sequence=latest
            ):
                latest = max(latest, event.sequence_no)
                last_emit = loop.time()
                yield cls.format_event(event)
            if not follow:
                return
            if request is not None and await request.is_disconnected():
                bus.unsubscribe(tenant_id=tenant_id, user_id=user_id)
                return
            elapsed = loop.time() - last_emit
            heartbeat_after = max(
                Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS - elapsed, 0.0
            )
            slice_timeout = min(
                heartbeat_after if heartbeat_after > 0 else 0.001,
                Constants.Cadence.WAIT_TIMEOUT_SECONDS,
            )
            await bus.wait(tenant_id=tenant_id, user_id=user_id, timeout=slice_timeout)
            if loop.time() - last_emit >= Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS:
                last_emit = loop.time()
                yield Constants.Sse.HEARTBEAT_COMMENT

    @classmethod
    def format_event(cls, event: TeamStreamEnvelope) -> bytes:
        body = (
            f"event: {Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


class LastEventIdResolver:
    """Compute the effective ``after_sequence`` from header + query.

    Mirrors the inbox resolver; Last-Event-ID wins when parseable.
    """

    @classmethod
    def resolve(cls, *, header_value: str | None, query_after_sequence: int) -> int:
        if header_value is not None:
            try:
                parsed = int(header_value.strip())
                if parsed >= 0:
                    return parsed
            except (ValueError, TypeError):
                pass
        return max(query_after_sequence, 0)


def register_team_sse_routes(
    app: FastAPI, *, bus: TeamActivityBus | None = None
) -> None:
    """Attach ``GET /v1/team/stream`` to a backend FastAPI app."""

    resolved_bus = bus or TeamActivityBus.get_default()
    app.state.team_activity_bus = resolved_bus

    @app.get(
        "/v1/team/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_team_events(
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
            TeamSseAdapter.stream(
                bus=resolved_bus,
                tenant_id=identity.org_id,
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
    "InMemoryTeamActivityBus",
    "LastEventIdResolver",
    "TeamActivityBus",
    "TeamSseAdapter",
    "TeamStreamEnvelope",
    "TeamStreamEventType",
    "register_team_sse_routes",
]
