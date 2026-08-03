"""Pure assistant-turn projection: run events -> an ORDERED list of parts.

A turn is ``text -> tools -> text -> tools -> text``, not "one text blob and one
reasoning blob". The transcript used to be folded bucket-by-KIND on both sides
of the wire, which destroyed two things at once:

* text the model emitted BEFORE a tool call, because the terminal
  ``final_response`` replaced the single text accumulator outright;
* the position of every mid-turn card, because the whole turn carried one
  anchor (its first token) and so every tool / approval / subagent card sorted
  after it.

The ledger was never wrong -- every frame carries a monotonic ``sequence_no``
and the terminal event seals ``[1..N]``. The order was discarded in the fold.
This module is the Python half of the single fold rule; the TypeScript twin is
``packages/chat-surface/src/destinations/run/chatProjection.ts`` and the
differential corpus asserts both agree at EVERY replay prefix.

The worker persists the output of this fold into ``MessageRecord.content`` so a
completed turn reloads in the shape it rendered -- ``content_text`` stays what
it honestly is, the final assistant text used for previews and the next turn's
model context.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class TurnPartKind(StrEnum):
    """The two kinds of streamed prose a turn can contain.

    Cards (tools, approvals, subagent fleets) are NOT parts here: they already
    have their own projections and their own ``sequence_no``. Interleaving is
    performed by the renderer over one shared seq order, so duplicating card
    state into this fold would create a second source of truth for it.
    """

    TEXT = "text"
    REASONING = "reasoning"


class TurnPartStatus(StrEnum):
    RUNNING = "running"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class TurnPart:
    """One contiguous span of prose, anchored at the seq it opened at."""

    kind: TurnPartKind
    text: str
    #: ``sequence_no`` of the event that OPENED this part -- its anchor. Not the
    #: latest delta's seq: the anchor must not drift forward as tokens arrive,
    #: or a card dispatched mid-part would sort ahead of prose that preceded it.
    seq: int
    status: TurnPartStatus
    started_at_ms: int | None = None
    updated_at_ms: int | None = None

    def to_content_block(self) -> dict[str, object]:
        """Wire shape for ``MessageRecord.content`` (and the client's parts)."""
        block: dict[str, object] = {
            TurnPartsProjection.Keys.Block.TYPE: str(self.kind),
            TurnPartsProjection.Keys.Block.TEXT: self.text,
            TurnPartsProjection.Keys.Block.SEQ: self.seq,
            TurnPartsProjection.Keys.Block.STATUS: {
                TurnPartsProjection.Keys.Block.TYPE: str(self.status)
            },
        }
        if self.started_at_ms is not None:
            block[TurnPartsProjection.Keys.Block.STARTED_AT_MS] = self.started_at_ms
        if self.updated_at_ms is not None:
            block[TurnPartsProjection.Keys.Block.UPDATED_AT_MS] = self.updated_at_ms
        return block


@dataclass(slots=True)
class _DraftPart:
    """A part under construction. Mutable by design; the fold is the only writer."""

    kind: TurnPartKind
    text: str
    seq: int
    started_at_ms: int | None
    updated_at_ms: int | None
    closed: bool


class TurnPartsProjection:
    """Fold run events into the ordered parts of one assistant turn."""

    class Keys:
        class Event:
            EVENT_ID = "event_id"
            SEQUENCE_NO = "sequence_no"
            EVENT_TYPE = "event_type"
            SUBAGENT_ID = "subagent_id"
            PAYLOAD = "payload"
            SUMMARY = "summary"
            CREATED_AT = "created_at"
            CREATED_AT_MS = "created_at_ms"

        class Payload:
            #: Resolution order. ``text`` is read first purely for historical
            #: tolerance -- no runtime event has ever carried it. The worker
            #: writes ``message`` (what ``RuntimeTextPayload`` declares) and the
            #: streamed chunk arrives as ``delta``.
            TEXT_KEYS = ("text", "message", "delta")
            SUMMARY = "summary"

        class Block:
            TYPE = "type"
            TEXT = "text"
            SEQ = "seq"
            STATUS = "status"
            STARTED_AT_MS = "startedAtMs"
            UPDATED_AT_MS = "updatedAtMs"

    class EventType:
        MODEL_DELTA = "model_delta"
        REASONING_SUMMARY_DELTA = "reasoning_summary_delta"
        REASONING_SUMMARY = "reasoning_summary"
        FINAL_RESPONSE = "final_response"

    #: Events that END an open part.
    #:
    #: Deliberately a closed set rather than "any non-delta event": closing on
    #: an incidental frame (a heartbeat, ``run_started``, a todo snapshot) would
    #: split a sentence -- or worse, a GFM table -- across two parts that each
    #: parse as half a document. These are exactly the events that render as
    #: their own item in the transcript, so a break here is one the user sees.
    PART_BREAKING_EVENT_TYPES: frozenset[str] = frozenset(
        {
            "tool_call_started",
            "tool_call_completed",
            "tool_result",
            "approval_requested",
            "approval_resolved",
            "mcp_auth_required",
            "subagent_started",
            "subagent_completed",
            "subagent_fleet_started",
            "subagent_fleet_finished",
        }
    )

    @classmethod
    def fold(cls, events: Iterable[Mapping[str, object]]) -> tuple[TurnPart, ...]:
        """Project events into the turn's ordered parts.

        Accepts the same persisted event shape served by replay/SSE. Returns an
        empty tuple until the main agent has produced text or reasoning.
        """
        drafts: list[_DraftPart] = []
        # Index rather than a reference so closing/reopening stays unambiguous
        # (and to mirror the TS twin, where a nullable binding defeats control
        # flow analysis through the closures that mutate it).
        open_index = -1
        finalized = False
        seen: set[str] = set()
        # True when a card has landed since the last prose arrived. The terminal
        # text may only settle INTO the tail text part when this is False —
        # otherwise a run that ends right after a tool call would reconcile its
        # answer into the sentence the model spoke BEFORE the call, which is the
        # original overwrite bug wearing a smaller hat.
        card_since_prose = False

        for event in cls._ordered(events):
            if event.get(cls.Keys.Event.SUBAGENT_ID) is not None:
                # Subagent streams belong to the Agents tab, never the reply.
                continue
            event_id = event.get(cls.Keys.Event.EVENT_ID)
            if isinstance(event_id, str):
                if event_id in seen:
                    continue
                seen.add(event_id)

            event_type = str(event.get(cls.Keys.Event.EVENT_TYPE) or "")
            if event_type in cls.PART_BREAKING_EVENT_TYPES:
                # The model stopped talking and acted. Whatever was open ended
                # here; the next delta opens a NEW part, which is what lets text
                # render on both sides of the card this event produces.
                open_index = cls._close(drafts, open_index)
                card_since_prose = True
                continue

            if event_type == cls.EventType.MODEL_DELTA:
                delta = cls._payload_text(event)
                if delta:
                    open_index = cls._append(
                        drafts, open_index, TurnPartKind.TEXT, delta, event
                    )
                    card_since_prose = False
            elif event_type == cls.EventType.REASONING_SUMMARY_DELTA:
                delta = cls._payload_text(event)
                if delta:
                    open_index = cls._append(
                        drafts, open_index, TurnPartKind.REASONING, delta, event
                    )
                    card_since_prose = False
            elif event_type == cls.EventType.REASONING_SUMMARY:
                open_index = cls._apply_reasoning_cap(drafts, open_index, event)
            elif event_type == cls.EventType.FINAL_RESPONSE:
                finalized = True
                open_index = cls._close(drafts, open_index)
                cls._reconcile_final(drafts, event, may_reconcile=not card_since_prose)

        return tuple(
            TurnPart(
                kind=draft.kind,
                text=draft.text,
                seq=draft.seq,
                status=(
                    TurnPartStatus.COMPLETE
                    if draft.closed or finalized
                    else TurnPartStatus.RUNNING
                ),
                started_at_ms=draft.started_at_ms,
                updated_at_ms=draft.updated_at_ms,
            )
            for draft in drafts
            if draft.text
        )

    @classmethod
    def content_blocks(
        cls, events: Iterable[Mapping[str, object]]
    ) -> tuple[dict[str, object], ...]:
        """The fold, in the wire shape persisted to ``MessageRecord.content``."""
        return tuple(part.to_content_block() for part in cls.fold(events))

    @classmethod
    def _ordered(
        cls, events: Iterable[Mapping[str, object]]
    ) -> Sequence[Mapping[str, object]]:
        """Ascending ``sequence_no``.

        The stream is append-only in arrival order, which is seq order in
        practice -- but a replay tail stitched onto a live subscription can
        arrive out of order, and the whole projection rests on this being the
        true total order.
        """
        return sorted(
            (event for event in events if isinstance(event, Mapping)),
            key=lambda event: cls._int(event.get(cls.Keys.Event.SEQUENCE_NO)),
        )

    @classmethod
    def _close(cls, drafts: list[_DraftPart], open_index: int) -> int:
        if open_index != -1:
            drafts[open_index].closed = True
        return -1

    @classmethod
    def _append(
        cls,
        drafts: list[_DraftPart],
        open_index: int,
        kind: TurnPartKind,
        delta: str,
        event: Mapping[str, object],
    ) -> int:
        at = cls._created_at_ms(event)
        if open_index == -1 or drafts[open_index].kind is not kind:
            # A different kind was open (or nothing was) -- the previous part
            # ended. A delta with no matching open part OPENS A NEW ONE: this is
            # the whole fix. Text after a tool call is its own part, not an
            # append to the part sitting above the tool card.
            cls._close(drafts, open_index)
            drafts.append(
                _DraftPart(
                    kind=kind,
                    text=delta,
                    seq=cls._int(event.get(cls.Keys.Event.SEQUENCE_NO)),
                    started_at_ms=at,
                    updated_at_ms=at,
                    closed=False,
                )
            )
            return len(drafts) - 1
        drafts[open_index].text += delta
        if at is not None:
            drafts[open_index].updated_at_ms = at
        return open_index

    @classmethod
    def _apply_reasoning_cap(
        cls,
        drafts: list[_DraftPart],
        open_index: int,
        event: Mapping[str, object],
    ) -> int:
        """Apply the provider's explicit reasoning close marker.

        It carries the CUMULATIVE summary, so it REPLACES the text of the part
        that is OPEN. The old client fold wrote it into the FIRST reasoning part
        of the turn, which destroyed span #1 the moment span #2 capped.
        """
        text = cls._reasoning_summary_text(event)
        if open_index != -1 and drafts[open_index].kind is TurnPartKind.REASONING:
            draft = drafts[open_index]
            if text:
                draft.text = text
            draft.updated_at_ms = cls._created_at_ms(event) or draft.updated_at_ms
            return cls._close(drafts, open_index)
        if not text:
            return open_index
        at = cls._created_at_ms(event)
        drafts.append(
            _DraftPart(
                kind=TurnPartKind.REASONING,
                text=text,
                seq=cls._int(event.get(cls.Keys.Event.SEQUENCE_NO)),
                started_at_ms=at,
                updated_at_ms=at,
                closed=True,
            )
        )
        return open_index

    @classmethod
    def _reconcile_final(
        cls,
        drafts: list[_DraftPart],
        event: Mapping[str, object],
        *,
        may_reconcile: bool,
    ) -> None:
        """Settle the terminal text into the LAST text part, never the whole turn.

        ``final_response`` carries the last assistant turn's text. Assigning it
        to a single accumulator is what deleted every sentence the model spoke
        before it acted.

        ``may_reconcile`` is False once a card has landed since the last prose:
        the terminal text then belongs to a turn segment that has not streamed
        yet, so it opens its OWN part. Without that guard, a run ending
        immediately after a tool call would overwrite the sentence spoken before
        the call -- the same destruction, one tool call later.
        """
        text = cls._payload_text(event) or cls._summary(event)
        if not text:
            return
        last_text_index = -1
        for index in range(len(drafts) - 1, -1, -1):
            if drafts[index].kind is TurnPartKind.TEXT:
                last_text_index = index
                break
        if (
            may_reconcile
            and last_text_index != -1
            and last_text_index == len(drafts) - 1
        ):
            drafts[last_text_index].text = text
            return
        drafts.append(
            _DraftPart(
                kind=TurnPartKind.TEXT,
                text=text,
                seq=cls._int(event.get(cls.Keys.Event.SEQUENCE_NO)),
                started_at_ms=cls._created_at_ms(event),
                updated_at_ms=cls._created_at_ms(event),
                closed=True,
            )
        )

    @classmethod
    def _payload_text(cls, event: Mapping[str, object]) -> str:
        payload = event.get(cls.Keys.Event.PAYLOAD)
        if not isinstance(payload, Mapping):
            return ""
        for key in cls.Keys.Payload.TEXT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @classmethod
    def _reasoning_summary_text(cls, event: Mapping[str, object]) -> str:
        payload = event.get(cls.Keys.Event.PAYLOAD)
        if isinstance(payload, Mapping):
            value = payload.get(cls.Keys.Payload.SUMMARY)
            if isinstance(value, str) and value:
                return value
        return cls._payload_text(event) or cls._summary(event)

    @classmethod
    def _summary(cls, event: Mapping[str, object]) -> str:
        value = event.get(cls.Keys.Event.SUMMARY)
        return value if isinstance(value, str) else ""

    @classmethod
    def _created_at_ms(cls, event: Mapping[str, object]) -> int | None:
        """Epoch ms of the event, from either wire form.

        Persisted events carry ``created_at`` (ISO string or datetime); the
        differential corpus carries a pre-resolved ``created_at_ms`` so both
        folds read the identical number rather than each re-deriving it.
        """
        value = event.get(cls.Keys.Event.CREATED_AT_MS)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raw = event.get(cls.Keys.Event.CREATED_AT)
        if isinstance(raw, datetime):
            return int(raw.timestamp() * 1000)
        if isinstance(raw, str) and raw:
            try:
                # ``Z`` is valid ISO-8601 but not accepted by fromisoformat
                # before 3.11's relaxation; normalise it either way.
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        return None

    @classmethod
    def _int(cls, value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0
