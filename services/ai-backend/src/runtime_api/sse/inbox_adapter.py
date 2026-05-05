"""Per-user inbox SSE adapter (PR 1.4.1).

Mirrors :class:`RuntimeSseAdapter` shape — same ``?after_sequence=N``
reconnect contract, same ``event:``/``id:``/``data:`` framing — but
keys subscriptions by ``user_id`` instead of ``run_id``. The recipient
of a forwarded approval subscribes here even when they aren't a
participant in any active run.

Wire format is intentionally compatible with the run-stream SSE so the
FE can use one parser for both.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from agent_runtime.api.constants import Values
from runtime_api.sse.inbox_bus import InboxEventBus, InboxEventEnvelope


class InboxSseAdapter:
    """Adapt inbox-bus envelopes to SSE for the per-user channel."""

    MEDIA_TYPE = "text/event-stream"
    FALLBACK_POLL_SECONDS = 5.0
    HEARTBEAT_INTERVAL_SECONDS = 25.0
    """Send a heartbeat (``: keepalive\\n\\n``) every N seconds even when
    nothing was published, so intermediaries (proxies, load balancers)
    don't kill the connection. The 25s default matches the runtime SSE.
    """

    @classmethod
    async def stream(
        cls,
        *,
        bus: InboxEventBus,
        user_id: str,
        after_sequence: int,
        follow: bool = True,
    ) -> AsyncIterator[str]:
        """Yield replayed + live events for the connected user."""

        latest_sequence = after_sequence
        while True:
            for event in bus.list_after(
                user_id=user_id, after_sequence=latest_sequence
            ):
                latest_sequence = max(latest_sequence, event.sequence_no)
                yield cls.format_event(event)
            if not follow:
                return
            try:
                await asyncio.wait_for(
                    bus.wait(user_id=user_id, timeout=cls.FALLBACK_POLL_SECONDS),
                    timeout=cls.HEARTBEAT_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                # Connection-keepalive comment frame; ignored by FE
                # parsers, prevents idle proxy disconnect.
                yield ": keepalive\n\n"

    @classmethod
    def format_event(cls, event: InboxEventEnvelope) -> str:
        payload = {
            "event_type": event.event_type,
            "approval_id": event.approval_id,
            "status": event.status,
            "org_id": event.org_id,
            "conversation_id": event.conversation_id,
            "actor_user_id": event.actor_user_id,
            "emitted_at": event.emitted_at.astimezone(timezone.utc).isoformat(),
            "sequence_no": event.sequence_no,
        }
        return (
            f"event: {Values.SSE_EVENT_NAME}\n"
            f"id: {event.sequence_no}\n"
            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
        )

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)
