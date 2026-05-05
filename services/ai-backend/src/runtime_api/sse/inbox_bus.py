"""Per-user inbox event bus for SSE push (PR 1.4.1).

This is the in-process counterpart to :class:`RuntimeEventBus` (which
fans run-scoped events). The inbox bus fans approval-assignment +
approval-resolution events to the recipient user's session, whose
identity is *not* a participant in the source run's conversation.

Why a separate bus, instead of a slot inside the existing run stream?
The recipient is not authorized to subscribe to the source run's events
— the conversation belongs to a different user. Routing inbox events
through a per-user channel keeps the visibility contract clean.

Schema:

  - ``inbox_events`` (in-memory only for v1 — multi-replica adds Redis
    pub/sub behind the same port without changing the contract).
  - ``inbox_event_cursors(user_id pk, latest_sequence_no)`` for replay.

The bus exposes:

  - ``publish(user_id, envelope)`` — append + wake subscribers.
  - ``wait(user_id, timeout)`` — block a subscriber until next publish
    or timeout.
  - ``list_after(user_id, after_sequence)`` — replay for SSE reconnect.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

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


class InboxEventBus:
    """In-process per-user inbox bus.

    Bounded by ``max_buffer_per_user`` events per user — older events
    drop. Per-user retention is bounded by time too (default 7 days);
    callers wanting longer retention should hit the REST inbox endpoint
    instead. The bus is at-least-once; the FE reducer is idempotent on
    ``(approval_id, sequence_no)``.

    The default singleton (`get_default()`) is what production wires.
    Tests instantiate per-test and avoid cross-test bleed.
    """

    _instance: "InboxEventBus | None" = None
    DEFAULT_MAX_BUFFER_PER_USER = 256

    @classmethod
    def get_default(cls) -> "InboxEventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default_for_tests(cls) -> None:
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
        events = self._events.get(user_id)
        if events is None:
            return ()
        return tuple(event for event in events if event.sequence_no > after_sequence)

    def latest_sequence_no(self, *, user_id: str) -> int:
        return self._cursors.get(user_id, 0)

    def unsubscribe(self, *, user_id: str) -> None:
        # Keep events around — they may be needed for a reconnect
        # within the buffer window — but drop the condition variable
        # so we don't accumulate unused conditions.
        self._conditions.pop(user_id, None)
