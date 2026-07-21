"""Projects destination SSE stream — ``GET /v1/projects/stream``.

Live updates for the Projects rail / list (PRD-H FR-H.2). The stream
emits one event per noteworthy project lifecycle change for the caller's
**tenant** channel — the FE (``apps/frontend/src/features/projects/
ProjectsRoute.tsx``) merges the envelope into its list (add-to-rail on
``project_member_added``, drop on ``project_deleted``, re-fetch/merge on
``project_updated`` / ``project_archived`` / ``project_activated``).

# Design — mirror the existing SSE convention exactly

Cross-audit §5.2 mandates a single SSE convention across the monorepo:
``GET /v1/<resource>/stream``, typed ``event:``/``id:``/``data:`` fields,
pass-through facade, reconnect via ``Last-Event-ID`` /
``?after_sequence=N``. This module is the projects sibling of
:mod:`backend_app.inbox.sse` — the same run-stream discipline (persisted
per-channel ring buffer, monotonic ``sequence_no``, heartbeat comment
frames) with two deliberate differences:

* **Channel key is the tenant alone** (``tenant_id`` / ``org_id``), not
  ``(org_id, user_id)``. Projects are a tenant-shared surface — every
  member of a tenant sees the same project mutations (FR-H.1: "Auth +
  tenant scoping identical to the REST routes"). Per-recipient ACL
  fan-out (hiding a project a viewer can't see) is a follow-up; the
  tenant wall is the load-bearing isolation boundary here.
* **``publish`` is synchronous.** The projects mutation handlers
  (create / update / delete / member / star) are plain ``def`` FastAPI
  routes; a sync ``publish`` lets them emit without every handler
  becoming ``async``. The SSE read loop polls the ring buffer on a
  bounded slice, so a freshly-appended envelope is picked up within one
  ``WAIT_TIMEOUT_SECONDS`` slice. Live latency of <1s is immaterial for
  this dev-tier bus; production replaces it with Postgres LISTEN/NOTIFY
  (see the identical note on the inbox / home buses).

# Tenant isolation

The channel key is ``tenant_id`` — derived from the **verified** bearer
(via :class:`BackendServiceAuthenticator`), never from the request body.
A subscriber for ``org_a`` will never see events published to ``org_b``:
:meth:`InMemoryProjectActivityBus.list_after` filters on the channel key
and a cross-tenant subscriber gets the empty tuple.

# Wire shape

::

    event: project_event
    id: 42
    data: {"sequence_no":42,"event_type":"project_updated",
           "project_id":"prj_...","payload":{...},
           "emitted_at":"2026-07-21T19:04:33+00:00"}

The ``data`` JSON matches ``packages/api-types::ProjectStreamEnvelope``
and the FE ``isProjectStreamEnvelope`` structural guard
(``apps/frontend/src/api/projectsApi.ts``).
"""

from __future__ import annotations

import asyncio
import logging
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


# ---------------------------------------------------------------------------
# Constants — class-namespaced so call sites never inline a magic string.
# Mirrors backend_app/inbox/sse.py.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the projects SSE stream."""

    class Sse:
        EVENT_NAME = "project_event"
        """The SSE ``event:`` line for every emitted frame. Matches the FE
        ``SSE_EVENT_NAME`` in ``projectsApi.ts``."""

        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"
        """SSE comment frame — ignored by ``EventSource`` parsers, keeps
        idle connections alive through corporate proxies."""

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        """Heartbeat cadence (cross-audit §5.2) — one number across
        destinations (matches inbox / home)."""

        WAIT_TIMEOUT_SECONDS = 1.0
        """Poll slice for the read loop. A sync ``publish`` is picked up
        within one slice; also bounds ``request.is_disconnected`` checks."""

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256
        """Ring-buffer depth per tenant channel. Beyond this, oldest
        events drop — the projects list renders ~50 rows so 256 is
        generous headroom for replay-on-reconnect."""

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


# ---------------------------------------------------------------------------
# Wire schema — mirrors packages/api-types::ProjectStreamEnvelope
# ---------------------------------------------------------------------------


ProjectStreamEventType = Literal[
    "project_created",
    "project_updated",
    "project_archived",
    "project_activated",
    "project_deleted",
    "project_member_added",
    "project_member_removed",
    "project_member_role_changed",
    "project_ownership_transferred",
    "project_starred",
    "project_unstarred",
]
"""Closed enum mirroring the TS ``ProjectStreamEventType`` union. The FE
guard only asserts ``event_type`` is a string (open-union forwards-
compat), so extending this literal never breaks already-shipped clients.
"""


class ProjectStreamEnvelope(BaseModel):
    """SSE event payload — locked to packages/api-types::ProjectStreamEnvelope.

    A schema mismatch fails validation at the framing boundary instead of
    silently desyncing the FE (same discipline as
    :class:`backend_app.inbox.sse.InboxEventEnvelope`).
    """

    sequence_no: int = Field(ge=1)
    event_type: ProjectStreamEventType
    project_id: str = Field(min_length=1)
    payload: dict[str, Any]
    emitted_at: datetime

    def serialise(self) -> str:
        """Serialise to a JSON string with ``emitted_at`` in ISO-8601 UTC."""

        return self.model_dump_json()


# ---------------------------------------------------------------------------
# In-memory bus — dev / single-process. Production replaces with Postgres
# LISTEN/NOTIFY or Redis pubsub. Channel key is the tenant alone.
# ---------------------------------------------------------------------------


class InMemoryProjectActivityBus:
    """Process-local pub/sub for the projects SSE stream.

    Channel key is ``tenant_id`` — the whole tenant shares one ordered
    stream. ``publish`` is synchronous (see module docstring) and returns
    the framed :class:`ProjectStreamEnvelope`.
    """

    _instance: "InMemoryProjectActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryProjectActivityBus":
        """Return (or create) the process-global projects-activity bus."""

        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default_for_tests(cls) -> None:
        """Clear the singleton so each test starts with a fresh bus."""

        cls._instance = None

    def __init__(self, *, max_buffer_per_channel: int | None = None) -> None:
        self._max_buffer = (
            max_buffer_per_channel or Constants.Bus.DEFAULT_MAX_BUFFER_PER_CHANNEL
        )
        self._events: dict[str, deque[ProjectStreamEnvelope]] = defaultdict(
            lambda: deque(maxlen=self._max_buffer)
        )
        self._cursors: dict[str, int] = defaultdict(int)

    def publish(
        self,
        *,
        tenant_id: str,
        event_type: ProjectStreamEventType,
        project_id: str,
        payload: dict[str, Any],
    ) -> ProjectStreamEnvelope:
        """Append an event to the tenant channel and return the envelope.

        Synchronous by design — the projects mutation handlers are plain
        ``def`` routes. The SSE read loop polls the ring buffer and picks
        the appended envelope up on its next slice.
        """

        self._cursors[tenant_id] += 1
        envelope = ProjectStreamEnvelope(
            sequence_no=self._cursors[tenant_id],
            event_type=event_type,
            project_id=project_id,
            payload=payload,
            emitted_at=datetime.now(timezone.utc),
        )
        self._events[tenant_id].append(envelope)
        return envelope

    def list_after(
        self, *, tenant_id: str, after_sequence: int
    ) -> Iterable[ProjectStreamEnvelope]:
        """Return buffered events with ``sequence_no > after_sequence``.

        Tenant isolation is enforced here: only events published on the
        matching ``tenant_id`` channel are returned. A cross-tenant
        subscriber sees the empty tuple, never another tenant's rows.
        """

        events = self._events.get(tenant_id)
        if events is None:
            return ()
        return tuple(event for event in events if event.sequence_no > after_sequence)

    def latest_sequence_no(self, *, tenant_id: str) -> int:
        """Return the highest sequence_no published for the channel, or 0."""

        return self._cursors.get(tenant_id, 0)


# Backward-compat alias — lets call sites import ``ProjectActivityBus``
# without binding to ``InMemory…`` so a Postgres-backed bus can swap in
# without churn (mirrors the inbox / home bus convention).
ProjectActivityBus = InMemoryProjectActivityBus


# ---------------------------------------------------------------------------
# SSE adapter — same framing as the inbox SSE; tenant-keyed channel.
# ---------------------------------------------------------------------------


class ProjectSseAdapter:
    """Adapt :class:`ProjectStreamEnvelope` to SSE for the projects stream.

    Framing mirrors :class:`backend_app.inbox.sse.InboxSseAdapter`:

    - ``event:`` line = ``project_event`` (closed event name)
    - ``id:`` line = monotonic ``sequence_no``
    - ``data:`` line = ``ProjectStreamEnvelope.model_dump_json()``
    - Idle keepalive every 30s as ``: keepalive\\n\\n`` comment frames.

    The adapter is pure — it does not authenticate; the FastAPI handler
    resolves the verified ``tenant_id`` from the bearer and passes it in.
    That keeps tenant isolation in **one** place.
    """

    @classmethod
    async def stream(
        cls,
        *,
        bus: ProjectActivityBus,
        tenant_id: str,
        after_sequence: int,
        follow: bool = True,
        request: Request | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield replayed + live SSE frames for the connected tenant channel.

        ``after_sequence`` is exclusive — the next yielded event has
        ``sequence_no > after_sequence`` (matches the runtime SSE contract
        and the EventSource ``Last-Event-ID`` semantics).

        ``follow=False`` drains the buffer once then returns (tests).
        Production callers always use ``follow=True``.
        """

        latest_sequence = after_sequence
        loop = asyncio.get_event_loop()
        last_emit_at = loop.time()
        while True:
            for event in bus.list_after(
                tenant_id=tenant_id, after_sequence=latest_sequence
            ):
                latest_sequence = max(latest_sequence, event.sequence_no)
                last_emit_at = loop.time()
                yield cls.format_event(event)
            if not follow:
                return
            if request is not None and await request.is_disconnected():
                return
            await asyncio.sleep(Constants.Cadence.WAIT_TIMEOUT_SECONDS)
            if (
                loop.time() - last_emit_at
                >= Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS
            ):
                last_emit_at = loop.time()
                yield Constants.Sse.HEARTBEAT_COMMENT

    @classmethod
    def format_event(cls, event: ProjectStreamEnvelope) -> bytes:
        """Return one SSE-framed project event.

        Build the wire body through :class:`ProjectStreamEnvelope` so any
        field rename on the schema fails validation here rather than
        silently desyncing the FE.
        """

        body = (
            f"event: {Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


# ---------------------------------------------------------------------------
# Last-Event-ID parsing — header is a string (W3C SSE spec); the stream
# uses an integer sequence_no. Invalid values fall back to 0 (full replay)
# rather than 4xx — matches browser behaviour with a stale cached id.
# ---------------------------------------------------------------------------


class LastEventIdResolver:
    """Compute the effective ``after_sequence`` cursor from header + query.

    Resolution order (matches the SSE spec):

    1. ``Last-Event-ID`` header (browsers set this on reconnect).
    2. ``?after_sequence=N`` query param (manual / polyfill clients).
    3. ``0`` — full replay of the buffer.
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


# ---------------------------------------------------------------------------
# FastAPI registration — called from within ``register_projects_routes``.
# ---------------------------------------------------------------------------


def register_projects_sse_route(app: FastAPI, *, bus: ProjectActivityBus) -> None:
    """Attach ``GET /v1/projects/stream`` to a backend FastAPI app.

    The route lives on the **public** ``/v1/*`` plane (the facade proxies
    it pass-through). Authentication uses the same bearer as
    ``GET /v1/projects`` — the facade verifies, then forwards
    ``x-enterprise-org-id`` / ``x-enterprise-user-id`` service headers;
    :class:`BackendServiceAuthenticator.scoped_identity` re-reads those
    headers (dev fallback: the ``org_id`` / ``user_id`` query params).
    """

    @app.get(
        "/v1/projects/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_project_events(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        """Open the SSE stream for the verified tenant channel."""

        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_after = LastEventIdResolver.resolve(
            header_value=last_event_id, query_after_sequence=after_sequence
        )
        return StreamingResponse(
            ProjectSseAdapter.stream(
                bus=bus,
                tenant_id=identity.org_id,
                after_sequence=effective_after,
                follow=True,
                request=request,
            ),
            media_type=Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )


__all__ = [
    "Constants",
    "InMemoryProjectActivityBus",
    "LastEventIdResolver",
    "ProjectActivityBus",
    "ProjectSseAdapter",
    "ProjectStreamEnvelope",
    "ProjectStreamEventType",
    "register_projects_sse_route",
]
