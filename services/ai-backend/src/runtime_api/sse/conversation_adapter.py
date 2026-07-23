"""Store-tailed SSE adapter for the Chats live-refresh stream (PRD-09 D4).

Unlike :class:`InboxSseAdapter`, which reads an in-memory pub/sub bus, this
adapter is a STORE TAIL: on each poll it re-queries the caller's newest
conversation slice through :meth:`ConversationQueryService.list_conversation_changes`
and emits an envelope for every row newer than the connection's watermark. The
in-memory bus "works only when API and worker share a process" — a publish from
a separate worker process never reaches API-side subscribers — so a bus would be
correct in dev and silently dead in production. The tail works identically in
both topologies with no new table and no bus, because every ``updated_at`` bump
moves a changed row to the top of the newest-first slice the tail polls.

Same ``event:``/``id:``/``data:`` framing and 25s heartbeat as the run/inbox
streams. The reconnect cursor is the D3 keyset ``(updated_at, conversation_id)``
carried on the SSE ``id:`` line and accepted back as ``?after=<cursor>`` — one
codec serves pagination, reconnect-resume, and the tail, and unlike a per-process
sequence counter it survives an API restart (a deliberate, documented divergence
from the run/inbox streams' ``?after_sequence=N``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent_runtime.api.conversation_query_service import ConversationQueryService


class ConversationSseAdapter:
    """Adapt the conversation store tail to an SSE channel for one caller scope."""

    MEDIA_TYPE = "text/event-stream"
    HEARTBEAT_INTERVAL_SECONDS = 25.0
    """Emit a heartbeat comment frame every N seconds even when nothing
    changed, so intermediaries don't kill an idle connection. Matches the
    run/inbox streams."""

    POLL_INTERVAL_SECONDS = 2.0
    """How often the tail re-queries the store. Bounded, topology-agnostic;
    O(page) per tick, not O(all conversations)."""

    SSE_EVENT_NAME = "conversation_changed"

    @classmethod
    async def stream(
        cls,
        *,
        query_service: ConversationQueryService,
        org_id: str,
        user_id: str,
        after: str | None,
        follow: bool = True,
        heartbeat_interval_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield ``conversation_changed`` frames newer than ``after`` for the caller.

        The watermark advances to the newest emitted row's keyset so a
        consumer never re-sees a row and a reconnect resumes cleanly. When
        ``follow`` is False the tail runs exactly one pass (test seam).
        """

        heartbeat = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else cls.HEARTBEAT_INTERVAL_SECONDS
        )
        poll = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else cls.POLL_INTERVAL_SECONDS
        )
        watermark = after
        idle_seconds = 0.0
        while True:
            envelopes = await query_service.list_conversation_changes(
                org_id=org_id,
                user_id=user_id,
                after=watermark,
            )
            for envelope in envelopes:
                watermark = envelope.cursor
                idle_seconds = 0.0
                yield cls.format_event(envelope.model_dump_json(), envelope.cursor)
            if not follow:
                return
            await asyncio.sleep(poll)
            idle_seconds += poll
            if idle_seconds >= heartbeat:
                idle_seconds = 0.0
                # Connection-keepalive comment frame; ignored by FE parsers.
                yield ": keepalive\n\n"

    @classmethod
    def format_event(cls, data_json: str, cursor: str) -> str:
        """Return one SSE-framed conversation-change event."""

        return f"event: {cls.SSE_EVENT_NAME}\nid: {cursor}\ndata: {data_json}\n\n"


__all__ = ["ConversationSseAdapter"]
