"""Persisted shape of an assistant turn: the run's ledger, folded into parts.

Both terminal paths write the assistant message — the ordinary completion in
``handlers/run.py`` and the resume-after-approval completion in
``handlers/approval.py`` — so the fold is composed here once and injected,
rather than copied into each handler. A second copy of a projection rule is
exactly what produced the bug this module exists to fix.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from agent_runtime.api.ports import EventStorePort
from agent_runtime.presentation.turn_parts import TurnPartsProjection
from runtime_api.schemas import RuntimeApiEventType


class AssistantTurnContent:
    """Project a run's sealed ledger into ordered ``MessageRecord.content`` blocks."""

    class Keys:
        SEQUENCE_NO = "sequence_no"
        EVENT_TYPE = "event_type"
        PAYLOAD = "payload"
        MESSAGE = "message"

    def __init__(self, event_store: EventStorePort) -> None:
        self._event_store = event_store
        self._log = logging.getLogger(__name__)

    async def blocks(
        self,
        *,
        org_id: str,
        run_id: str,
        final_text: str,
    ) -> tuple[dict[str, object], ...]:
        """The turn's ordered parts, ready to persist.

        Read once, on the terminal path — not per tool call. The ledger is
        already the durable, ordered, sealed record of the turn; this projects
        it into the shape the transcript renders, so a completed run reloads as
        what it was rather than as its last sentence. ``content_text`` remains
        the final assistant text for previews and the next turn's context.

        The ``final_response`` event has not been appended yet when the message
        is written, so the text about to be emitted is supplied as the event it
        is about to become. That keeps ONE reconcile rule — the fold's — instead
        of a second copy of it here.

        Failure is non-fatal by design: a turn that cannot be folded still
        persists with its ``content_text``, which is the pre-existing behaviour.
        A transcript that loses its interleaving is a degraded transcript; a run
        that fails to record its answer is a lost one.
        """
        try:
            events = await self._event_store.list_events_after(
                org_id=org_id, run_id=run_id, after_sequence=0
            )
            folded: list[Mapping[str, object]] = [
                event.model_dump(mode="json") for event in events
            ]
            latest_seq = max((event.sequence_no for event in events), default=0)
            folded.append(
                {
                    self.Keys.SEQUENCE_NO: latest_seq + 1,
                    self.Keys.EVENT_TYPE: RuntimeApiEventType.FINAL_RESPONSE.value,
                    self.Keys.PAYLOAD: {self.Keys.MESSAGE: final_text},
                }
            )
            return TurnPartsProjection.content_blocks(folded)
        except Exception:
            self._log.warning(
                "[turn-parts] fold failed for run=%s; persisting content_text only",
                run_id,
                exc_info=True,
            )
            return ()
