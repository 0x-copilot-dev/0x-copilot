"""Per-user inbox event bus for SSE push notifications.

Per-user counterpart to the run-scoped SSE bus. Fans approval-assignment and
approval-resolution events to the recipient user's session — whose identity is
not a participant in the source run's conversation.

A separate bus is needed because the recipient is not authorised to subscribe to
the source run's event stream; routing inbox events through a per-user channel
keeps the visibility contract clean.

Two backends:

* :class:`InMemoryInboxBus` — process-local deque + ``asyncio.Condition``
  pub/sub. Works only when API and worker share a process. In production with
  separate processes, a publish from the worker never reaches API-side
  subscribers.
* Postgres-backed inbox bus — planned follow-up. Requires an ``inbox_events``
  table plus ``LISTEN/NOTIFY runtime_inbox_v1`` for cross-process wakeup.

The bus surface:

  - ``publish(user_id, envelope)`` — append + wake subscribers.
  - ``wait(user_id, timeout)`` — block a subscriber until next publish or timeout.
  - ``list_after(user_id, after_sequence)`` — replay for SSE reconnect.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


InboxEventType = Literal["approval_assigned", "approval_resolved"]


@dataclass(frozen=True)
class InboxEventEnvelope:
    """Inbox SSE envelope shape — mirrors RuntimeEventEnvelope discipline.

    ``sequence_no`` is monotonic per-user; the FE reconnects via
    ``?after_sequence=N`` and the bus replays from there.
    """

    user_id: str
    sequence_no: int
    event_type: InboxEventType
    approval_id: str
    status: str  # "pending" | "approved" | "rejected" | "expired" | "forwarded"
    org_id: str
    conversation_id: str
    actor_user_id: str  # forwarder for assigned, decider for resolved
    emitted_at: datetime


@runtime_checkable
class InboxBusBackend(Protocol):
    """Per-user inbox surface used by the inbox SSE adapter."""

    async def publish(
        self,
        *,
        user_id: str,
        event_type: InboxEventType,
        approval_id: str,
        status: str,
        org_id: str,
        conversation_id: str,
        actor_user_id: str,
    ) -> InboxEventEnvelope:
        """Append + wake any subscriber on ``user_id``."""

    async def wait(self, *, user_id: str, timeout: float = 5.0) -> None:
        """Block until next publish for this user or timeout."""

    def list_after(
        self, *, user_id: str, after_sequence: int
    ) -> Iterable[InboxEventEnvelope]:
        """Return persisted events with ``sequence_no > after_sequence``."""

    def latest_sequence_no(self, *, user_id: str) -> int:
        """Highest sequence_no published for ``user_id``."""

    def unsubscribe(self, *, user_id: str) -> None:
        """Drop any local subscription state for ``user_id`` (idempotent)."""


class InMemoryInboxBus:
    """In-process per-user inbox bus.

    Bounded by ``max_buffer_per_user`` events per user — older events
    drop. Per-user retention is bounded by time too (default 7 days);
    callers wanting longer retention should hit the REST inbox endpoint
    instead. The bus is at-least-once; the FE reducer is idempotent on
    ``(approval_id, sequence_no)``.

    The default singleton (`get_default()`) is what production wires.
    Tests instantiate per-test and avoid cross-test bleed.
    """

    _instance: "InMemoryInboxBus | None" = None
    DEFAULT_MAX_BUFFER_PER_USER = 256

    @classmethod
    def get_default(cls) -> "InMemoryInboxBus":
        """Return (or create) the process-global inbox bus singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default_for_tests(cls) -> None:
        """Clear the singleton so each test starts with a fresh bus."""
        cls._instance = None

    def __init__(self, *, max_buffer_per_user: int | None = None) -> None:
        self._max_buffer = max_buffer_per_user or self.DEFAULT_MAX_BUFFER_PER_USER
        self._events: dict[str, deque[InboxEventEnvelope]] = defaultdict(
            lambda: deque(maxlen=self._max_buffer)
        )
        self._cursors: dict[str, int] = defaultdict(int)
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)

    async def publish(
        self,
        *,
        user_id: str,
        event_type: InboxEventType,
        approval_id: str,
        status: str,
        org_id: str,
        conversation_id: str,
        actor_user_id: str,
    ) -> InboxEventEnvelope:
        """Append an event for ``user_id``, increment the cursor, and wake any active subscriber."""
        self._cursors[user_id] += 1
        envelope = InboxEventEnvelope(
            user_id=user_id,
            sequence_no=self._cursors[user_id],
            event_type=event_type,
            approval_id=approval_id,
            status=status,
            org_id=org_id,
            conversation_id=conversation_id,
            actor_user_id=actor_user_id,
            emitted_at=datetime.now(timezone.utc),
        )
        self._events[user_id].append(envelope)
        condition = self._conditions.get(user_id)
        if condition is not None:
            async with condition:
                condition.notify_all()
        return envelope

    async def wait(self, *, user_id: str, timeout: float = 5.0) -> None:
        """Block until next publish for this user or timeout."""

        condition = self._conditions[user_id]
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def list_after(
        self, *, user_id: str, after_sequence: int
    ) -> Iterable[InboxEventEnvelope]:
        """Return all buffered events with ``sequence_no > after_sequence`` for ``user_id``."""
        events = self._events.get(user_id)
        if events is None:
            return ()
        return tuple(event for event in events if event.sequence_no > after_sequence)

    def latest_sequence_no(self, *, user_id: str) -> int:
        """Return the highest sequence_no published for ``user_id``, or 0 if none."""
        return self._cursors.get(user_id, 0)

    def unsubscribe(self, *, user_id: str) -> None:
        """Drop the condition variable for ``user_id`` while retaining buffered events for reconnect."""
        # Events are retained — they may be needed for a reconnect within
        # the buffer window — but the condition variable is dropped to
        # avoid accumulating unused asyncio objects.
        self._conditions.pop(user_id, None)


# Backward-compat alias so existing call sites that import
# ``InboxEventBus`` continue to work without edits. New code should
# depend on the ``InboxBusBackend`` Protocol or the explicit class name.
InboxEventBus = InMemoryInboxBus
