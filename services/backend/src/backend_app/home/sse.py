"""Home destination SSE stream — ``GET /v1/home/stream``.

Live updates for the Home **agent activity feed** (see
``docs/atlas-new-design/destinations/home-prd.md`` §3.5 and §5.2). The
stream emits one event per noteworthy agent activity for the
``(org_id, user_id)`` channel — the FE prepends to
``<HomeAgentActivityFeed>`` and caps at 15 most-recent rows.

# Design — mirror the existing SSE convention exactly

Phase 2 cross-audit §5.2 mandates a single SSE convention across the
monorepo: ``GET /v1/<resource>/stream``, typed ``event:``/``id:``/``data:``
fields, pass-through facade, reconnect via ``Last-Event-ID``. This
module is a copy of the inbox SSE discipline
(:class:`runtime_api.sse.inbox_adapter.InboxSseAdapter` +
:class:`runtime_api.sse.inbox_bus.InMemoryInboxBus`) with the channel
key swapped from ``user_id`` to ``(org_id, user_id)``.

- Monotonic ``sequence_no`` per channel — incremented on publish, used as
  the SSE ``id:`` field so browsers' EventSource implementation
  automatically replays via ``Last-Event-ID`` on reconnect.
- ``?after_sequence=N`` query-param fallback for clients that cannot set
  ``Last-Event-ID`` (e.g. polyfills, curl scripts). Header wins when both
  are present — that's the documented Last-Event-ID semantics.
- Pass-through facade: ``backend-facade`` forwards bytes; it does **not**
  buffer or transform.
- 30-second heartbeats (``: keepalive\\n\\n`` comment frames) so corporate
  proxies don't close idle connections. Heartbeats are not events — they
  don't get a sequence number and the FE parser ignores them.

# What the bus is — and isn't

:class:`InMemoryHomeActivityBus` is **dev-tier** infrastructure. It is
process-local (Python ``asyncio.Condition`` + ``deque``) and only works
when the API process publishes to itself. The home-prd notes that in
production the bus must be backed by Postgres ``LISTEN/NOTIFY`` (or
Redis pubsub) so the aggregator process can publish from anywhere and
the SSE-serving process picks it up. That follow-up bus is out of
scope for P2-A2 — see ``InboxEventBus``'s identical comment.

# Tenant isolation

The channel key is ``(org_id, user_id)`` — both come from the
**verified** bearer (via :class:`BackendServiceAuthenticator`), never
from the request body or query path. A subscriber for
``(org_a, user_1)`` will never see events published to
``(org_b, user_1)``. The bus stores the tuple in the envelope and re-
checks on publish/subscribe lookup.

# Wire shape

::

    event: home_activity
    id: 42
    data: {"event_id":"...","sequence_no":42,"event_type":"activity_added",
           "row":{...},"created_at":"2026-05-17T19:04:33+00:00"}

The ``data`` JSON matches ``packages/api-types::HomeActivityEvent``. The
``row`` field is forward-declared as ``dict[str, Any]`` here because the
P2-A1 ``HomeActivityRow`` Pydantic model lands in a parallel branch; the
orchestrator rewires this to the precise type at merge. The wire shape
is **already locked** — the JSON the bus emits is what the FE consumes
regardless of how the Python type narrows.
"""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — pulled into a nested class so call sites never inline a magic
# string. Mirrors the discipline of agent_runtime/api/constants.py.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the home SSE stream."""

    class Sse:
        EVENT_NAME = "home_activity"
        """The SSE ``event:`` line for every emitted frame. Matches the
        home-prd §3.5 event name; the FE listens for this exact string."""

        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"
        """SSE comment frame — ignored by ``EventSource`` parsers,
        keeps idle connections alive through corporate proxies."""

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        """Heartbeat cadence (home-prd §3.5 / cross-audit §5.2)."""

        WAIT_TIMEOUT_SECONDS = 5.0
        """Inner ``wait()`` slice; lets us check ``request.is_disconnected``
        responsively even when no event has been published."""

    class Bus:
        DEFAULT_MAX_BUFFER_PER_CHANNEL = 256
        """Ring-buffer depth per ``(org_id, user_id)`` channel. Beyond
        this, oldest events drop — the home feed only renders 15 rows
        so 256 is generous headroom for replay-on-reconnect."""

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"
        """Standard SSE reconnect header; browsers set this automatically
        when an EventSource reopens after a network blip."""


# ---------------------------------------------------------------------------
# Wire schema — mirrors packages/api-types::HomeActivityEvent
# ---------------------------------------------------------------------------


HomeActivityEventType = Literal[
    # Phase 2 legacy — kept for backward compat with the FE's existing
    # ``home_activity`` listener. ``activity_added`` / ``activity_updated``
    # remain the canonical "noteworthy agent activity" envelope.
    "activity_added",
    "activity_updated",
    "heartbeat",
    # Phase 9 morning-briefing envelopes (home-prd §3.6 + sub-PRD). Each
    # corresponds to a section that re-fetches when its kind fires:
    # * ``triage_updated``      — TriageCounts shape changed
    # * ``timeline_appended``   — TodayTimeline gained a new entry
    # * ``whats_new_appended``  — WhatsNewDigest gained a new row
    # * ``activity_appended``   — LiveActivityRail row (alias for the
    #                             legacy ``activity_added`` so a Phase 9
    #                             FE can listen on a single kind name)
    "triage_updated",
    "timeline_appended",
    "whats_new_appended",
    "activity_appended",
]
"""Closed enum mirroring the wire contract. ``heartbeat`` exists on the
wire only as a synthetic frame for tests; the production keepalive uses
an SSE comment (no event/data) so EventSource doesn't fire ``onmessage``.
"""


class HomeActivityEventEnvelope(BaseModel):
    """SSE event payload — locked to packages/api-types::HomeActivityEvent.

    A schema mismatch fails validation at the framing boundary instead
    of silently desyncing the FE (same discipline as
    :class:`InboxEventEnvelopeSchema`).
    """

    event_id: str = Field(min_length=1)
    sequence_no: int = Field(ge=1)
    event_type: HomeActivityEventType
    # ``row`` is forward-declared as ``dict[str, Any]`` — see module
    # docstring. The orchestrator rewires this to ``HomeActivityRow``
    # (Pydantic) at merge with P2-A1. The wire JSON is stable across
    # the rewire because both shapes serialise to the same fields.
    row: dict[str, Any] | None = None
    created_at: datetime

    def serialise(self) -> str:
        """Serialise to a JSON string with the ``created_at`` field in ISO-8601 UTC."""

        return self.model_dump_json()


# ---------------------------------------------------------------------------
# In-memory bus — dev / single-process. Production replaces with Postgres
# LISTEN/NOTIFY or Redis pubsub. Surface mirrors InMemoryInboxBus.
# ---------------------------------------------------------------------------


class InMemoryHomeActivityBus:
    """Process-local pub/sub for the home activity SSE stream.

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

    _instance: "InMemoryHomeActivityBus | None" = None

    @classmethod
    def get_default(cls) -> "InMemoryHomeActivityBus":
        """Return (or create) the process-global home-activity bus singleton."""

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
        # pattern as the inbox bus. Buffered deques retain events for
        # reconnect-within-window replay.
        self._events: dict[tuple[str, str], deque[HomeActivityEventEnvelope]] = (
            defaultdict(lambda: deque(maxlen=self._max_buffer))
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
        event_type: HomeActivityEventType,
        row: dict[str, Any] | None,
    ) -> HomeActivityEventEnvelope:
        """Append an event and wake any active subscriber.

        ``row`` is required for ``activity_added`` / ``activity_updated``
        and must be ``None`` for ``heartbeat``. The check lives here so
        a malformed publish never reaches the SSE wire.
        """

        # Validation rules per kind:
        # * activity_added / activity_updated / activity_appended /
        #   timeline_appended / whats_new_appended — ``row`` REQUIRED
        #   (carries the section payload).
        # * triage_updated — ``row`` OPTIONAL (clients re-fetch
        #   /v1/home; the envelope is just a re-fetch trigger).
        # * heartbeat — ``row`` MUST be None.
        _ROW_REQUIRED = (
            "activity_added",
            "activity_updated",
            "activity_appended",
            "timeline_appended",
            "whats_new_appended",
        )
        if event_type in _ROW_REQUIRED and row is None:
            raise ValueError(
                f"row is required for event_type={event_type!r}; got None."
            )
        if event_type == "heartbeat" and row is not None:
            raise ValueError("row must be None for heartbeat events.")

        key = (org_id, user_id)
        self._cursors[key] += 1
        envelope = HomeActivityEventEnvelope(
            event_id=str(uuid.uuid4()),
            sequence_no=self._cursors[key],
            event_type=event_type,
            row=row,
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
    ) -> Iterable[HomeActivityEventEnvelope]:
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


# Backward-compat alias mirroring the inbox bus convention. Lets call
# sites import ``HomeActivityBus`` without binding to ``InMemory…`` so a
# Postgres-backed bus can swap in without churn.
HomeActivityBus = InMemoryHomeActivityBus


# ---------------------------------------------------------------------------
# SSE adapter — same framing as the inbox SSE; tenant-keyed channel.
# ---------------------------------------------------------------------------


class HomeSseAdapter:
    """Adapt :class:`HomeActivityEventEnvelope` to SSE for the home stream.

    Mirrors :class:`runtime_api.sse.inbox_adapter.InboxSseAdapter` framing:

    - ``event:`` line = ``home_activity`` (closed event name)
    - ``id:`` line = monotonic ``sequence_no``
    - ``data:`` line = ``HomeActivityEventEnvelope.model_dump_json()``
    - Idle keepalive every 30s as ``: keepalive\\n\\n`` comment frames.

    The adapter is pure — it does not authenticate; the FastAPI handler
    resolves the verified ``(org_id, user_id)`` from the bearer and
    passes them in. That keeps tenant isolation in **one** place.
    """

    @classmethod
    async def stream(
        cls,
        *,
        bus: HomeActivityBus,
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
    def format_event(cls, event: HomeActivityEventEnvelope) -> bytes:
        """Return one SSE-framed home activity event.

        Build the wire body through :class:`HomeActivityEventEnvelope` so
        any field rename on the schema fails validation here rather than
        silently desyncing the FE — same discipline as
        :class:`InboxSseAdapter.format_event`.
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


def register_home_sse_routes(
    app: FastAPI, *, bus: HomeActivityBus | None = None
) -> None:
    """Attach ``GET /v1/home/stream`` to a backend FastAPI app.

    The route lives on the **public** ``/v1/*`` plane (the facade proxies
    it pass-through). Authentication uses the same bearer as
    ``GET /v1/home`` — the facade verifies, then forwards
    ``x-enterprise-org-id`` / ``x-enterprise-user-id`` service headers;
    :class:`BackendServiceAuthenticator.scoped_identity` re-reads those
    headers here.

    P2-A1 coordination — when P2-A1's ``services/backend/src/backend_app/
    home/routes.py`` lands first, the orchestrator can either:

    1. Call ``register_home_sse_routes(app)`` immediately after
       ``register_home_routes(app)`` from ``create_app``, **or**
    2. Inline the registration as a single ``app.get("/v1/home/stream")``
       line inside ``home/routes.py`` that delegates to
       :meth:`HomeSseAdapter.stream` + :func:`LastEventIdResolver.resolve`.

    Either is correct; option (1) is the lower-friction merge.
    """

    resolved_bus = bus or HomeActivityBus.get_default()
    app.state.home_activity_bus = resolved_bus

    @app.get(
        "/v1/home/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def stream_home_activity(
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
            HomeSseAdapter.stream(
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
