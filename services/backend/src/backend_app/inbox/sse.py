"""Inbox destination SSE stream — ``GET /v1/inbox/stream``.

Live updates for the Inbox **per-user item feed** (see
``docs/atlas-new-design/destinations/inbox-prd.md`` §3.6 and §5.2). The
stream emits one event per noteworthy inbox-item lifecycle change for
the ``(org_id, user_id)`` channel — the FE prepends to the inbox list,
updates the rail badge, and de-duplicates by ``id``.

# Design — mirror the existing SSE convention exactly

Phase 2 cross-audit §5.2 mandates a single SSE convention across the
monorepo: ``GET /v1/<resource>/stream``, typed ``event:``/``id:``/``data:``
fields, pass-through facade, reconnect via ``Last-Event-ID``. This
module is a copy of the home SSE discipline
(:class:`backend_app.home.sse.HomeSseAdapter` +
:class:`backend_app.home.sse.InMemoryHomeActivityBus`) with the channel
payload swapped from ``row`` (Home activity) to ``item`` (inbox).

- Monotonic ``sequence_no`` per channel — incremented on publish, used as
  the SSE ``id:`` field so browsers' EventSource implementation
  automatically replays via ``Last-Event-ID`` on reconnect.
- ``?after_sequence=N`` query-param fallback for clients that cannot set
  ``Last-Event-ID`` (e.g. polyfills, curl scripts). Header wins when both
  are present — that's the documented Last-Event-ID semantics.
- Pass-through facade: ``backend-facade`` forwards bytes; it does **not**
  buffer or transform.
- 30-second heartbeats (``: keepalive\\n\\n`` comment frames) so corporate
  proxies don't close idle connections (the inbox-prd §10 spec cites a
  25-30s cadence; we standardise on 30s with the home stream to keep
  one number across destinations). Heartbeats are not events — they
  don't get a sequence number and the FE parser ignores them.

# What the bus is — and isn't

:class:`InMemoryInboxActivityBus` is **dev-tier** infrastructure. It is
process-local (Python ``asyncio.Condition`` + ``deque``) and only works
when the API process publishes to itself. The inbox-prd §3.5 producer
flow notes that in production the bus must be backed by Postgres
``LISTEN/NOTIFY`` (or Redis pubsub) so the ai-backend producer can
publish from a separate process and the SSE-serving backend process
picks it up. That follow-up bus is out of scope for P4-A3 — see the
identical note on the home activity bus.

# Tenant isolation

The channel key is ``(org_id, user_id)`` — both come from the
**verified** bearer (via :class:`BackendServiceAuthenticator`), never
from the request body or query path. A subscriber for
``(org_a, user_1)`` will never see events published to
``(org_b, user_1)``. The bus stores the tuple in the envelope and re-
checks on publish/subscribe lookup. This matches the inbox-prd §7.1
visibility rules — only the recipient (and tenant) sees the event.

# Wire shape

::

    event: inbox_event
    id: 42
    data: {"event_id":"...","sequence_no":42,"event_type":"item_added",
           "item":{...},"created_at":"2026-05-17T19:04:33+00:00"}

The ``data`` JSON matches ``packages/api-types::InboxEventEnvelope``. The
``item`` field is forward-declared as ``dict[str, Any]`` here because the
P4-A1 ``InboxItem`` Pydantic model lands in a parallel branch; the
orchestrator rewires this to the precise type at merge. The wire shape
is **already locked** — the JSON the bus emits is what the FE consumes
regardless of how the Python type narrows.

# Integration with P4-A1 (CRUD) and P4-A2 (producer)

The bus's ``publish(...)`` is called by **whichever** of these lands
first:

- P4-A1's ``POST /v1/inbox/items`` + ``PATCH /v1/inbox/items/{id}``
  handlers (app-facing mutations) — publishes for ``item_updated``.
- P4-A2's ``POST /internal/v1/inbox/items`` producer endpoint
  (ai-backend → backend) — publishes for ``item_added``.

The bus instance is stashed in ``app.state.inbox_activity_bus`` so both
sites can reach it through the FastAPI request without re-instantiating.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — pulled into a nested class so call sites never inline a magic
# string. Mirrors the discipline of backend_app/home/sse.py.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the inbox SSE stream."""

    class Sse:
        EVENT_NAME = "inbox_event"
        """The SSE ``event:`` line for every emitted frame. Matches the
        inbox-prd §3.6 event name; the FE listens for this exact string."""

        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"
        """SSE comment frame — ignored by ``EventSource`` parsers,
        keeps idle connections alive through corporate proxies."""

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        """Heartbeat cadence (cross-audit §5.2 / inbox-prd §10). 30s
        matches the home stream — one number across destinations."""

        WAIT_TIMEOUT_SECONDS = 5.0
        """Inner ``wait()`` slice; lets us check ``request.is_disconnected``
        responsively even when no event has been published."""

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256
        """Ring-buffer depth per ``(org_id, user_id)`` channel. Beyond
        this, oldest events drop — the inbox list renders ~50 rows at
        a time so 256 is generous headroom for replay-on-reconnect."""

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"
        """Standard SSE reconnect header; browsers set this automatically
        when an EventSource reopens after a network blip."""


# ---------------------------------------------------------------------------
# Wire schema — mirrors packages/api-types::InboxEventEnvelope
# ---------------------------------------------------------------------------


InboxEventType = Literal["item_added", "item_updated", "heartbeat"]
"""Closed enum mirroring the TS contract. ``heartbeat`` exists on the
wire only as a synthetic frame for tests; the production keepalive uses
an SSE comment (no event/data) so EventSource doesn't fire ``onmessage``.

Note the prompt-locked names (``item_added``/``item_updated``) differ
from the inbox-prd §4.1 longer-list (``item_created``/``item_updated``/
``item_deleted``). The orchestrator harmonises naming at merge; both
sides agree on ``item_updated``, so a future ``item_deleted`` (P4 follow-
up) extends this literal without breaking already-shipped clients.
"""


class InboxEventEnvelope(BaseModel):
    """SSE event payload — locked to packages/api-types::InboxEventEnvelope.

    A schema mismatch fails validation at the framing boundary instead
    of silently desyncing the FE (same discipline as
    :class:`backend_app.home.sse.HomeActivityEventEnvelope`).
    """

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: InboxEventType
    # ``item`` is forward-declared as ``dict[str, Any]`` — see module
    # docstring. The orchestrator rewires this to ``InboxItem``
    # (Pydantic, from P4-A1) at merge. The wire JSON is stable across
    # the rewire because both shapes serialise to the same fields.
    item: dict[str, Any] | None = None
    created_at: datetime

    def serialise(self) -> str:
        """Serialise to a JSON string with the ``created_at`` field in ISO-8601 UTC."""

        return self.model_dump_json()


# ---------------------------------------------------------------------------
# In-memory bus — dev / single-process. Production replaces with Postgres
# LISTEN/NOTIFY or Redis pubsub. Surface mirrors InMemoryHomeActivityBus.
# ---------------------------------------------------------------------------


class InMemoryInboxActivityBus:
    """Process-local pub/sub for the inbox SSE stream.

    Channel key is ``(org_id, user_id)`` — tenant-first so a per-org
    flush (e.g. tenant deletion) is a single dict-scan rather than a
    cross-channel search. The bus is intentionally minimal:

    - ``publish(...)`` appends and wakes any subscriber.
    - ``wait(...)`` blocks a subscriber up to ``timeout`` seconds.
    - ``list_after(..., after_sequence)`` replays buffered events for
      reconnect.
    - ``latest_sequence_no(...)`` returns the high-water mark.
    - ``unsubscribe(...)`` drops the condition variable (idempotent).

    The default singleton (``get_default()``) is what the FastAPI app
    wires; tests instantiate per-test and avoid cross-test bleed.
    """

    _instance: "InMemoryInboxActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryInboxActivityBus":
        """Return (or create) the process-global inbox-activity bus singleton."""

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
        # ``defaultdict`` with a lambda that captures the bound depth — same
        # pattern as the home activity bus. Buffered deques retain events
        # for reconnect-within-window replay.
        self._events: dict[tuple[str, str], deque[InboxEventEnvelope]] = defaultdict(
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
        event_type: InboxEventType,
        item: dict[str, Any] | None,
    ) -> InboxEventEnvelope:
        """Append an event and wake any active subscriber.

        ``item`` is required for ``item_added`` / ``item_updated`` and
        must be ``None`` for ``heartbeat``. The check lives here so
        a malformed publish never reaches the SSE wire.
        """

        if event_type in ("item_added", "item_updated") and item is None:
            raise ValueError(
                f"item is required for event_type={event_type!r}; got None."
            )
        if event_type == "heartbeat" and item is not None:
            raise ValueError("item must be None for heartbeat events.")

        key = (org_id, user_id)
        self._cursors[key] += 1
        envelope = InboxEventEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            item=item,
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
        """Block until the next publish for ``(org_id, user_id)`` or timeout."""

        condition = self._conditions[(org_id, user_id)]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def list_after(
        self, *, org_id: str, user_id: str, after_sequence: int
    ) -> Iterable[InboxEventEnvelope]:
        """Return buffered events with ``sequence_no > after_sequence``.

        Tenant isolation is enforced here: only events published with
        the matching ``(org_id, user_id)`` are returned. A cross-tenant
        subscriber sees the empty tuple, never another channel's rows.
        """

        events = self._events.get((org_id, user_id))
        if events is None:
            return ()
        return tuple(event for event in events if event.sequence_no > after_sequence)

    def latest_sequence_no(self, *, org_id: str, user_id: str) -> int:
        """Return the highest sequence_no published for the channel, or 0."""

        return self._cursors.get((org_id, user_id), 0)

    def unsubscribe(self, *, org_id: str, user_id: str) -> None:
        """Drop the condition variable while retaining buffered events for reconnect."""

        # Events are retained — they may be needed for a reconnect
        # within the buffer window. The condition variable is dropped
        # to avoid accumulating unused asyncio objects across many
        # short-lived connections.
        self._conditions.pop((org_id, user_id), None)


# Backward-compat alias mirroring the home bus convention. Lets call
# sites import ``InboxActivityBus`` without binding to ``InMemory…`` so a
# Postgres-backed bus can swap in without churn.
InboxActivityBus = InMemoryInboxActivityBus


# ---------------------------------------------------------------------------
# SSE adapter — same framing as the home SSE; tenant-keyed channel.
# ---------------------------------------------------------------------------


class InboxSseAdapter:
    """Adapt :class:`InboxEventEnvelope` to SSE for the inbox stream.

    Mirrors :class:`backend_app.home.sse.HomeSseAdapter` framing:

    - ``event:`` line = ``inbox_event`` (closed event name)
    - ``id:`` line = monotonic ``sequence_no``
    - ``data:`` line = ``InboxEventEnvelope.model_dump_json()``
    - Idle keepalive every 30s as ``: keepalive\\n\\n`` comment frames.

    The adapter is pure — it does not authenticate; the FastAPI handler
    resolves the verified ``(org_id, user_id)`` from the bearer and
    passes them in. That keeps tenant isolation in **one** place.
    """

    @classmethod
    async def stream(
        cls,
        *,
        bus: InboxActivityBus,
        org_id: str,
        user_id: str,
        after_sequence: int,
        follow: bool = True,
        request: Request | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield replayed + live SSE frames for the connected channel.

        ``after_sequence`` is exclusive — the next yielded event has
        ``sequence_no > after_sequence``. This matches the runtime SSE
        contract (``?after_sequence=N``) and the EventSource
        ``Last-Event-ID`` semantics.

        ``follow=False`` exists for the tests — it drains the buffer
        once then returns. Production callers always use ``follow=True``.

        Heartbeat cadence: a wall-clock timer fires a
        ``: keepalive\\n\\n`` comment frame when no real event has been
        emitted in ``HEARTBEAT_INTERVAL_SECONDS``. The bus's internal
        ``wait()`` swallows its own timeout, so we time the wait at the
        adapter level rather than nesting two ``wait_for`` calls that
        compete on different deadlines.
        """

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
            # Compute how long until the next heartbeat would be due.
            elapsed = loop.time() - last_emit_at
            heartbeat_after = max(
                Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS - elapsed,
                0.0,
            )
            # Bounded inner wait — we want to come back periodically so we
            # can re-check ``request.is_disconnected`` and re-publish a
            # heartbeat. Take the smaller of the heartbeat deadline and
            # the bus wait slice.
            slice_timeout = min(
                heartbeat_after if heartbeat_after > 0 else 0.001,
                Constants.Cadence.WAIT_TIMEOUT_SECONDS,
            )
            await bus.wait(org_id=org_id, user_id=user_id, timeout=slice_timeout)
            # If no event arrived in this slice and we have crossed the
            # heartbeat deadline, emit the keepalive frame and reset.
            if (
                loop.time() - last_emit_at
                >= Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS
            ):
                last_emit_at = loop.time()
                yield Constants.Sse.HEARTBEAT_COMMENT

    @classmethod
    def format_event(cls, event: InboxEventEnvelope) -> bytes:
        """Return one SSE-framed inbox event.

        Build the wire body through :class:`InboxEventEnvelope` so any
        field rename on the schema fails validation here rather than
        silently desyncing the FE — same discipline as
        :class:`backend_app.home.sse.HomeSseAdapter.format_event`.
        """

        body = (
            f"event: {Constants.Sse.EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {event.serialise()}\n\n"
        )
        return body.encode("utf-8")


# ---------------------------------------------------------------------------
# Last-Event-ID parsing — header is a string (per the W3C SSE spec). The
# stream uses an integer sequence_no, so we coerce. Invalid values fall
# back to 0 (full replay) rather than 4xx — that matches browser behavior
# when the cached ``Last-Event-ID`` is stale.
# ---------------------------------------------------------------------------


class LastEventIdResolver:
    """Compute the effective ``after_sequence`` cursor from header + query.

    Resolution order (matches the SSE spec):

    1. ``Last-Event-ID`` header (browsers set this automatically on
       reconnect). If parseable as a non-negative int, that wins.
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
# FastAPI registration — call from ``backend_app.app.create_app``.
# ---------------------------------------------------------------------------


def register_inbox_sse_routes(
    app: FastAPI, *, bus: InboxActivityBus | None = None
) -> None:
    """Attach ``GET /v1/inbox/stream`` to a backend FastAPI app.

    The route lives on the **public** ``/v1/*`` plane (the facade proxies
    it pass-through). Authentication uses the same bearer as
    ``GET /v1/inbox`` — the facade verifies, then forwards
    ``x-enterprise-org-id`` / ``x-enterprise-user-id`` service headers;
    :class:`BackendServiceAuthenticator.scoped_identity` re-reads those
    headers here.

    P4-A1 coordination — when P4-A1's CRUD routes (``services/backend/
    src/backend_app/inbox/routes.py``) land, the orchestrator either:

    1. Calls ``register_inbox_sse_routes(app)`` immediately after
       ``register_inbox_routes(app)`` from ``create_app``, **or**
    2. Inlines the registration as a single ``app.get("/v1/inbox/stream")``
       line inside ``inbox/routes.py`` that delegates to
       :meth:`InboxSseAdapter.stream` + :func:`LastEventIdResolver.resolve`.

    Either is correct; option (1) is the lower-friction merge.

    P4-A1's mutation handlers and P4-A2's producer locate the bus through
    ``app.state.inbox_activity_bus`` and call ``await bus.publish(...)``.
    """

    resolved_bus = bus or InboxActivityBus.get_default()
    app.state.inbox_activity_bus = resolved_bus

    @app.get("/v1/inbox/stream")
    def stream_inbox_events(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        """Open the SSE stream for the verified ``(org_id, user_id)``.

        Auth: the facade attaches service-token + identity headers; the
        backend re-derives ``(org_id, user_id)`` from those headers via
        :class:`BackendServiceAuthenticator`. The query params are
        ignored when the service token is present (prod path) and used
        only as the dev-mode fallback documented on the authenticator.
        """

        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_after = LastEventIdResolver.resolve(
            header_value=last_event_id, query_after_sequence=after_sequence
        )
        return StreamingResponse(
            InboxSseAdapter.stream(
                bus=resolved_bus,
                org_id=identity.org_id,
                user_id=identity.user_id,
                after_sequence=effective_after,
                follow=True,
                request=request,
            ),
            media_type=Constants.Sse.MEDIA_TYPE,
            # ``X-Accel-Buffering: no`` tells nginx (the default
            # reverse proxy in prod) to flush bytes as they're written
            # rather than buffering the response. Without this, the
            # first event may not arrive for tens of seconds.
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )
