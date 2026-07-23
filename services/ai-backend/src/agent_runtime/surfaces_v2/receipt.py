"""Run receipt — a pure fold of the Work Ledger (PRD-E1, SDR §5/§7 S6).

The run receipt is the run's accountability artifact: stat tiles over a
per-action ledger with a decision attribution per row, and NEVER hand-assembled
state. :class:`ReceiptFold` is a deterministic, total, IO-free reduction of a
run's events into the A1 :class:`RunReceipt` entity — refolding the same events
yields a byte-identical receipt (E3's export verifies exactly this). The
:class:`ReceiptEmitter` is the SOLE producer of ``receipt.emitted``; it folds
the run's ledger and appends ``surface.created {kind: receipt}`` + ``receipt.emitted``
at run termination, best-effort (a fold/append failure logs and never blocks
termination).

Layering: like the other v2 folds, this module reads events **structurally**
(``event_type`` / ``sequence_no`` / ``created_at`` / ``payload``) and takes an
:data:`ReceiptEmitFn` closure + a structural events port, so it never imports
``runtime_api`` (``runtime_api`` imports ``agent_runtime``, never the reverse).
Stage terminal status is reused from :class:`StagedWriteFold` — the receipt fold
NEVER re-derives it a second way (SDR §10 item 6).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.surfaces_v2.constants import Keys, Messages, Titles, Values
from agent_runtime.surfaces_v2.entities import (
    ReceiptAttribution,
    RunReceipt,
    RunReceiptRow,
    RunReceiptTiles,
)
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec, LedgerIdFormatError
from agent_runtime.surfaces_v2.ledger_models import (
    ApplyResult,
    DecisionKind,
    LedgerEventType,
    SurfaceKind,
    ViewTier,
)
from agent_runtime.surfaces_v2.staging import StagedWriteFold, StagedWriteStatus

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structural ports (never import the transport) — mirror emitter.py precedent
# ---------------------------------------------------------------------------


@runtime_checkable
class _ReceiptEventLike(Protocol):
    """Envelope-lite shape the fold reads (a ``RuntimeEventEnvelope`` fits)."""

    event_type: object
    sequence_no: int
    created_at: object
    payload: Mapping[str, object]


@runtime_checkable
class _RunEventsPort(Protocol):
    """Read a run's persisted events, ascending by ``sequence_no``."""

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[_ReceiptEventLike]:
        """Return persisted events after a sequence number (keyword-only)."""


@runtime_checkable
class _RunLike(Protocol):
    """The opaque run the emitter needs an ``org_id`` / ``run_id`` from."""

    run_id: str
    org_id: str


# (event_type_value, payload, summary) → append. Event types are raw A1 string
# values so this module never imports runtime_api.
ReceiptEmitFn = Callable[[str, Mapping[str, object], str | None], Awaitable[None]]


# ---------------------------------------------------------------------------
# Raw-event access keys + fold bookkeeping
# ---------------------------------------------------------------------------


class _RawKey:
    """Keys the fold reads off a raw ``{event_type, sequence_no, ...}`` dict."""

    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    CREATED_AT = "created_at"
    PAYLOAD = "payload"


@dataclass
class _RowDraft:
    """One receipt row plus its anchoring sequence (for the final sort)."""

    seq: int
    ledger_id: str
    event_type: LedgerEventType
    title: str
    attribution: ReceiptAttribution
    at: str


@dataclass
class _StageFacts:
    """The per-stage facts the receipt fold reads straight off the raw events.

    ``StagedWriteState`` (D1) carries no row data, so E1 reads row counts from
    the raw ``write.staged`` / ``write.applied`` payloads here and consults
    :class:`StagedWriteFold` only for the terminal status.
    """

    stage_id: str
    surface_id: str = ""
    rows_staged: int = 1
    approve_actors: list[str] = field(default_factory=list)
    last_hold_seq: int | None = None
    last_hold_at: str = ""
    last_decision_seq: int | None = None
    last_decision_at: str = ""
    staged_seq: int = 0
    staged_at: str = ""
    # Every ``write.applied`` folded onto this stage (result in applied/partial),
    # as ``(sequence_no, created_at)`` pairs in fold order.
    applied_count: int = 0
    apply_events: list[tuple[int, str]] = field(default_factory=list)
    last_apply_seq: int | None = None
    last_apply_at: str = ""
    has_any_apply: bool = False


class ReceiptFold:
    """Pure fold: run events in → :class:`RunReceipt` out. No IO, no clock, no state."""

    @classmethod
    def fold(cls, *, run_id: str, events: Sequence[_ReceiptEventLike]) -> RunReceipt:
        """Fold typed envelopes (or anything with the four structural fields)."""

        return cls.fold_raw(
            run_id=run_id,
            events=[
                {
                    _RawKey.EVENT_TYPE: cls._event_type_value(event.event_type),
                    _RawKey.SEQUENCE_NO: event.sequence_no,
                    _RawKey.CREATED_AT: cls._created_at_str(event.created_at),
                    _RawKey.PAYLOAD: event.payload,
                }
                for event in events
            ],
        )

    @classmethod
    def fold_raw(
        cls, *, run_id: str, events: Sequence[Mapping[str, object]]
    ) -> RunReceipt:
        """Fold plain ``{event_type, sequence_no, created_at, payload}`` dicts.

        Deterministic + total: events are re-sorted by ``sequence_no`` before
        folding; malformed / unknown payloads are skipped, never raised. The
        same events always yield a byte-identical ``RunReceipt``.
        """

        ordered = sorted(events, key=cls._seq_of)

        # Reuse the D2/D3 stage fold for terminal status ONLY.
        stage_status = {
            stage_id: state.status
            for stage_id, state in StagedWriteFold.fold_raw(ordered).items()
        }

        read_rows: list[_RowDraft] = []
        surface_titles: dict[str, str] = {}
        surface_title_by_payload_ref: dict[str, str] = {}
        raw_surface_rows: dict[str, _RowDraft] = {}  # deduped, first raw wins
        reject_rows: list[_RowDraft] = []
        stages: dict[str, _StageFacts] = {}
        through_seq = 0
        generated_at = ""

        for event in ordered:
            event_type = cls._str_of(event.get(_RawKey.EVENT_TYPE))
            seq = cls._seq_of(event)
            created_at = cls._str_of(event.get(_RawKey.CREATED_AT))
            payload = event.get(_RawKey.PAYLOAD)
            payload = payload if isinstance(payload, Mapping) else {}
            if seq > through_seq:
                through_seq = seq
                generated_at = created_at

            if event_type == LedgerEventType.SURFACE_CREATED.value:
                cls._note_surface(
                    payload,
                    seq=seq,
                    created_at=created_at,
                    run_id=run_id,
                    surface_titles=surface_titles,
                    surface_title_by_payload_ref=surface_title_by_payload_ref,
                    raw_surface_rows=raw_surface_rows,
                )
            elif event_type == LedgerEventType.READ_EXECUTED.value:
                read_rows.append(
                    cls._read_row(
                        payload,
                        seq=seq,
                        created_at=created_at,
                        run_id=run_id,
                        surface_title_by_payload_ref=surface_title_by_payload_ref,
                    )
                )
            elif event_type == LedgerEventType.VIEW_DERIVED.value:
                cls._note_raw_view(
                    payload,
                    seq=seq,
                    created_at=created_at,
                    run_id=run_id,
                    surface_titles=surface_titles,
                    raw_surface_rows=raw_surface_rows,
                )
            elif event_type == LedgerEventType.WRITE_STAGED.value:
                cls._note_write_staged(
                    payload, seq=seq, created_at=created_at, stages=stages
                )
            elif event_type == LedgerEventType.DECISION_RECORDED.value:
                cls._note_decision(
                    payload,
                    seq=seq,
                    created_at=created_at,
                    run_id=run_id,
                    stages=stages,
                    reject_rows=reject_rows,
                    surface_titles=surface_titles,
                )
            elif event_type == LedgerEventType.WRITE_APPLIED.value:
                cls._note_write_applied(
                    payload, seq=seq, created_at=created_at, stages=stages
                )

        tiles, held_rows = cls._tiles_and_holds(
            run_id=run_id,
            read_count=len(read_rows),
            stages=stages,
            stage_status=stage_status,
        )

        applied_rows = cls._applied_rows(
            run_id=run_id, stages=stages, surface_titles=surface_titles
        )

        drafts: list[_RowDraft] = [
            *read_rows,
            *applied_rows,
            *reject_rows,
            *raw_surface_rows.values(),
            *held_rows,
        ]
        drafts.sort(key=lambda draft: draft.seq)
        rows = tuple(
            RunReceiptRow(
                ledger_id=draft.ledger_id,
                event_type=draft.event_type,
                title=draft.title,
                attribution=draft.attribution,
                at=draft.at,
            )
            for draft in drafts
        )

        return RunReceipt(
            run_id=run_id,
            surface_id=f"{Values.RECEIPT_SURFACE_PREFIX}{run_id}",
            fold_ref=cls.fold_ref(run_id=run_id, through_seq=through_seq),
            generated_at=generated_at,
            tiles=tiles,
            rows=rows,
        )

    # -- fold_ref -----------------------------------------------------------

    @staticmethod
    def fold_ref(*, run_id: str, through_seq: int) -> str:
        """``ledger://<run_id>@<through_seq>`` — the receipt's re-derivation ref."""

        return (
            f"{Values.FOLD_REF_PREFIX}{run_id}{Values.FOLD_REF_SEPARATOR}{through_seq}"
        )

    # -- event notes --------------------------------------------------------

    @classmethod
    def _note_surface(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        run_id: str,
        surface_titles: dict[str, str],
        surface_title_by_payload_ref: dict[str, str],
        raw_surface_rows: dict[str, _RowDraft],
    ) -> None:
        surface_id = cls._str_of(payload.get(Keys.Field.SURFACE_ID))
        if not surface_id:
            return
        title = cls._str_of(payload.get(Keys.Field.TITLE))
        surface_titles.setdefault(surface_id, title)
        payload_ref = cls._str_of(payload.get(Keys.Field.PAYLOAD_REF))
        if payload_ref:
            surface_title_by_payload_ref.setdefault(payload_ref, title)
        # A raw surface has no view that fit — one "no view fit" row per surface,
        # first raw event wins (FR-E2).
        if (
            cls._str_of(payload.get(Keys.Field.KIND)) == SurfaceKind.RAW.value
            and surface_id not in raw_surface_rows
        ):
            raw_surface_rows[surface_id] = _RowDraft(
                seq=seq,
                ledger_id=cls._ledger_id(run_id, seq),
                event_type=LedgerEventType.SURFACE_CREATED,
                title=title,
                attribution=ReceiptAttribution.NO_VIEW_FIT,
                at=created_at,
            )

    @classmethod
    def _read_row(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        run_id: str,
        surface_title_by_payload_ref: dict[str, str],
    ) -> _RowDraft:
        connector = cls._str_of(payload.get(Keys.Field.CONNECTOR))
        op = cls._str_of(payload.get(Keys.Field.OP))
        payload_ref = cls._str_of(payload.get(Keys.Field.PAYLOAD_REF))
        title = surface_title_by_payload_ref.get(payload_ref, "")
        if not title:
            title = f"{connector}{Titles.SEPARATOR}{op}"
        return _RowDraft(
            seq=seq,
            ledger_id=cls._ledger_id(run_id, seq),
            event_type=LedgerEventType.READ_EXECUTED,
            title=title,
            attribution=ReceiptAttribution.AUTO_RAN,
            at=created_at,
        )

    @classmethod
    def _note_raw_view(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        run_id: str,
        surface_titles: dict[str, str],
        raw_surface_rows: dict[str, _RowDraft],
    ) -> None:
        if cls._str_of(payload.get(Keys.Field.TIER)) != ViewTier.RAW.value:
            return
        surface_id = cls._str_of(payload.get(Keys.Field.SURFACE_ID))
        if not surface_id or surface_id in raw_surface_rows:
            return
        raw_surface_rows[surface_id] = _RowDraft(
            seq=seq,
            ledger_id=cls._ledger_id(run_id, seq),
            event_type=LedgerEventType.VIEW_DERIVED,
            title=surface_titles.get(surface_id, ""),
            attribution=ReceiptAttribution.NO_VIEW_FIT,
            at=created_at,
        )

    @classmethod
    def _note_write_staged(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        stages: dict[str, _StageFacts],
    ) -> None:
        stage_id = cls._str_of(payload.get(Keys.Field.STAGE_ID))
        if not stage_id or stage_id in stages:
            return
        facts = _StageFacts(stage_id=stage_id)
        facts.surface_id = cls._str_of(payload.get(Keys.Field.SURFACE_ID))
        facts.rows_staged = cls._rows_or_one(payload.get(Keys.Field.ROWS))
        facts.staged_seq = seq
        facts.staged_at = created_at
        stages[stage_id] = facts

    @classmethod
    def _note_decision(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        run_id: str,
        stages: dict[str, _StageFacts],
        reject_rows: list[_RowDraft],
        surface_titles: Mapping[str, str],
    ) -> None:
        stage_id = cls._str_of(payload.get(Keys.Field.STAGE_ID))
        facts = stages.get(stage_id)
        decision = cls._str_of(payload.get(Keys.Field.DECISION))
        actor = cls._str_of(payload.get(Keys.Field.ACTOR))
        if facts is not None:
            facts.last_decision_seq = seq
            facts.last_decision_at = created_at
            if decision == DecisionKind.APPROVE.value:
                facts.approve_actors.append(actor)
            elif decision == DecisionKind.HOLD.value:
                facts.last_hold_seq = seq
                facts.last_hold_at = created_at
        # A reject is its own standalone receipt row (approve/hold/restore never
        # produce one — approve surfaces via write.applied).
        if decision == DecisionKind.REJECT.value:
            reject_rows.append(
                _RowDraft(
                    seq=seq,
                    ledger_id=cls._ledger_id(run_id, seq),
                    event_type=LedgerEventType.DECISION_RECORDED,
                    title=cls._stage_title(facts, surface_titles),
                    attribution=ReceiptAttribution.REJECTED,
                    at=created_at,
                )
            )

    @classmethod
    def _note_write_applied(
        cls,
        payload: Mapping[str, object],
        *,
        seq: int,
        created_at: str,
        stages: dict[str, _StageFacts],
    ) -> None:
        stage_id = cls._str_of(payload.get(Keys.Field.STAGE_ID))
        facts = stages.get(stage_id)
        if facts is None:
            return
        facts.has_any_apply = True
        facts.last_apply_seq = seq
        facts.last_apply_at = created_at
        result = cls._str_of(payload.get(Keys.Field.RESULT))
        if result in (ApplyResult.APPLIED.value, ApplyResult.PARTIAL.value):
            facts.apply_events.append((seq, created_at))
            facts.applied_count += cls._row_keys_or_one(
                payload.get(Keys.Field.ROW_KEYS)
            )

    # -- tiles + held-remainder rows ---------------------------------------

    @classmethod
    def _tiles_and_holds(
        cls,
        *,
        run_id: str,
        read_count: int,
        stages: dict[str, _StageFacts],
        stage_status: Mapping[str, StagedWriteStatus],
    ) -> tuple[RunReceiptTiles, list[_RowDraft]]:
        writes_proposed = 0
        writes_approved = 0
        holds_untouched = 0
        held_rows: list[_RowDraft] = []

        for stage_id, facts in stages.items():
            writes_proposed += facts.rows_staged
            writes_approved += facts.applied_count
            remainder = cls._held_remainder(
                facts, stage_status.get(stage_id, StagedWriteStatus.STAGED)
            )
            holds_untouched += remainder
            if remainder > 0:
                held_rows.append(cls._held_row(facts, run_id=run_id, count=remainder))

        tiles = RunReceiptTiles(
            reads_auto_ran=read_count,
            writes_proposed=writes_proposed,
            writes_approved=writes_approved,
            holds_untouched=holds_untouched,
        )
        return tiles, held_rows

    @staticmethod
    def _held_remainder(facts: _StageFacts, status: StagedWriteStatus) -> int:
        """Rows this stage staged but never applied (FR-C9).

        A stage with any ``write.applied`` holds ``rows_staged − applied_count``
        (clamped ≥ 0 — approved is per-apply extent, held is the untouched
        remainder). A rejected-whole stage with no apply holds every staged row.
        A stage still ``STAGED`` / ``APPROVED`` (pending, no apply) contributes 0
        — pending work is E2's queue, not a receipt hold.
        """

        if facts.has_any_apply:
            return max(0, facts.rows_staged - facts.applied_count)
        if status is StagedWriteStatus.REJECTED:
            return facts.rows_staged
        return 0

    @classmethod
    def _held_row(
        cls,
        facts: _StageFacts,
        *,
        run_id: str,
        count: int,
    ) -> _RowDraft:
        # Anchor to the last hold decision (else the last apply, else the last
        # decision, else the write.staged) — always a real folded event.
        if facts.last_hold_seq is not None:
            seq, at = facts.last_hold_seq, facts.last_hold_at
            event_type = LedgerEventType.DECISION_RECORDED
        elif facts.last_apply_seq is not None:
            seq, at = facts.last_apply_seq, facts.last_apply_at
            event_type = LedgerEventType.WRITE_APPLIED
        elif facts.last_decision_seq is not None:
            seq, at = facts.last_decision_seq, facts.last_decision_at
            event_type = LedgerEventType.DECISION_RECORDED
        else:
            seq, at = facts.staged_seq, facts.staged_at
            event_type = LedgerEventType.WRITE_STAGED
        return _RowDraft(
            seq=seq,
            ledger_id=cls._ledger_id(run_id, seq),
            event_type=event_type,
            title=f"{count} rows held, untouched",
            attribution=ReceiptAttribution.HELD,
            at=at,
        )

    # -- applied (approved / auto-applied) rows -----------------------------

    @classmethod
    def _applied_rows(
        cls,
        *,
        run_id: str,
        stages: dict[str, _StageFacts],
        surface_titles: dict[str, str],
    ) -> list[_RowDraft]:
        rows: list[_RowDraft] = []
        for facts in stages.values():
            # ``auto_applied`` when the stage's approve was a policy allow-always
            # (FR-C8); ``approved`` when a user approved it (the default).
            attribution = (
                ReceiptAttribution.AUTO_APPLIED
                if any(actor == Values.ACTOR_POLICY for actor in facts.approve_actors)
                else ReceiptAttribution.APPROVED
            )
            title = cls._stage_title(facts, surface_titles)
            for seq, at in facts.apply_events:
                rows.append(
                    _RowDraft(
                        seq=seq,
                        ledger_id=cls._ledger_id(run_id, seq),
                        event_type=LedgerEventType.WRITE_APPLIED,
                        title=title,
                        attribution=attribution,
                        at=at,
                    )
                )
        return rows

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _stage_title(
        facts: _StageFacts | None, surface_titles: Mapping[str, str]
    ) -> str:
        if facts is None:
            return ""
        return surface_titles.get(facts.surface_id, "")

    @staticmethod
    def _ledger_id(run_id: str, seq: int) -> str:
        try:
            return LedgerIdCodec.format(run_id, seq)
        except LedgerIdFormatError:
            return f"r{run_id}·{seq}"

    @staticmethod
    def _rows_or_one(value: object) -> int:
        if isinstance(value, bool):
            return 1
        if isinstance(value, int) and value >= 0:
            return value
        return 1

    @staticmethod
    def _row_keys_or_one(value: object) -> int:
        if isinstance(value, (list, tuple)):
            return len(value)
        return 1

    @staticmethod
    def _seq_of(event: Mapping[str, object]) -> int:
        raw = event.get(_RawKey.SEQUENCE_NO, 0)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0

    @staticmethod
    def _str_of(value: object) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", event_type)
        return value if isinstance(value, str) else str(event_type)

    @staticmethod
    def _created_at_str(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        return ""


@dataclass(frozen=True)
class ReceiptEmitter:
    """Sole producer of ``receipt.emitted`` (PRD-E1 §3).

    Constructed by the worker only when ``SURFACES_V2`` is on; best-effort — a
    fold or append failure logs ``[surfaces_v2] receipt.emit_raised`` and never
    propagates. Appends ``surface.created {kind: receipt}`` then
    ``receipt.emitted`` (order matters: the SSE stream stops on terminal run
    status, so both must land before the terminal event).
    """

    emit: ReceiptEmitFn
    event_store: _RunEventsPort

    async def emit_for_run(self, *, run: _RunLike) -> None:
        """Fold the run's ledger and append the two receipt events."""

        try:
            events = await self.event_store.list_events_after(
                org_id=run.org_id, run_id=run.run_id, after_sequence=0
            )
            receipt = ReceiptFold.fold(run_id=run.run_id, events=events)
            surface_payload: dict[str, object] = {
                Keys.Field.V: Values.PAYLOAD_V,
                Keys.Field.SURFACE_ID: receipt.surface_id,
                Keys.Field.KIND: Values.RECEIPT_KIND,
                Keys.Field.SOURCE: {
                    Keys.Field.CONNECTOR: Values.RECEIPT_CONNECTOR,
                    Keys.Field.OP: Values.RECEIPT_OP,
                },
                Keys.Field.TITLE: Values.RECEIPT_TITLE,
                Keys.Field.PAYLOAD_REF: receipt.fold_ref,
            }
            await self.emit(
                LedgerEventType.SURFACE_CREATED.value,
                surface_payload,
                Messages.RECEIPT_SURFACE_CREATED,
            )
            await self.emit(
                LedgerEventType.RECEIPT_EMITTED.value,
                {
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.SURFACE_ID: receipt.surface_id,
                    Keys.Field.FOLD_REF: receipt.fold_ref,
                },
                Messages.RECEIPT_EMITTED,
            )
        except Exception:  # noqa: BLE001 - receipt emission never blocks termination
            _LOGGER.warning(Messages.RECEIPT_EMIT_RAISED, exc_info=True)


__all__ = ["ReceiptEmitFn", "ReceiptEmitter", "ReceiptFold"]
