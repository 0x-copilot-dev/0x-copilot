"""Cross-run pending-work fold + read service (PRD-E2).

Everything the user still has to decide — parked gates (C2), held single-artifact
drafts (D1), and undecided row-sets (D3) — projected into ONE queue across all of
the user's runs. E2 adds **zero** ledger events and **zero** writes: it is a
read model.

Two pieces:

* :class:`PendingWorkFold` — pure, per-run: fold one run's ledger events into the
  pending items that run contributes. Composes :class:`StagedWriteFold` (D1/D3)
  for the stage half + a gate open/resolve pairing for the C2 half. No IO, no
  clock — every field derives from the events themselves; ``*_ref`` values are
  NEVER dereferenced (that would be IO).
* :class:`PendingWorkService` — fold-on-read across the caller's candidate runs
  (bounded by caps), enriching each item with its conversation title and building
  the fleet ``agents`` list. O(runs) folds are acceptable under the NFR-11 solo
  posture and keep the ledger the only truth (the DoD's "cards match ledger
  state").

**Pending predicate (SDR §5, one definition — mirrored byte-for-byte in the
TypeScript ``projectPendingCards``):**

* a **gate** is pending iff a ``gate.opened`` has no later ``gate.resolved`` with
  the same ``gate_id``;
* a **single-artifact stage** is pending iff its folded status is ``STAGED`` —
  approved / rejected / applied stages are not waiting on the user;
* a **row-set stage** is pending iff its status is ``STAGED`` and at least one row
  is still undecided-by-the-user (``rows_pending`` = rows whose ``decided_by`` is
  neither ``user`` nor ``policy`` AND that carry no ``apply_outcome`` — reusing
  D3's per-row fold accounting rather than re-deriving it).

Layering: pure domain. This module NEVER imports ``runtime_api`` — the fold reads
events structurally (``event_type`` / ``sequence_no`` / ``payload`` /
``created_at`` / ``run_id`` / ``conversation_id``), so a ``RuntimeEventEnvelope``
satisfies it by shape without an import (mirrors :class:`StagedWriteFold`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import PositiveInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.constants import Keys, Titles
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec, LedgerIdFormatError
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.staging import (
    StagedWriteFold,
    StagedWriteState,
    StagedWriteStatus,
)


# ---------------------------------------------------------------------------
# Constants (no repeated key/value string inlined — service CLAUDE.md)
# ---------------------------------------------------------------------------


class _RawKey:
    """Keys of the plain-dict event shape the fold consumes."""

    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    PAYLOAD = "payload"
    CREATED_AT = "created_at"
    RUN_ID = "run_id"
    CONVERSATION_ID = "conversation_id"


class _RowDecider:
    """``decided_by`` values that count as a resolved-by-the-user row decision.

    An agent pre-hold (``decided_by == "agent"``) is NOT a user decision — the
    row is still waiting for the user to keep or approve it (FR-C7).
    """

    USER = "user"
    POLICY = "policy"
    RESOLVED = frozenset({USER, POLICY})


class Values:
    """Bounds for the candidate-run scan (NFR-11 solo posture — v0 window)."""

    # Newest conversations to fold per request; older pending work is outside
    # the v0 window (no second table, no migration).
    CAP_CONVERSATIONS = 30
    # Newest runs to fold per conversation.
    CAP_RUNS_PER_CONVERSATION = 5


class PendingItemKind(StrEnum):
    """What a queue card decides on."""

    GATE = "gate"
    STAGED_WRITE = "staged_write"


# ---------------------------------------------------------------------------
# Contracts (served as-is by the endpoint; api-types mirrors them)
# ---------------------------------------------------------------------------


class PendingWorkItem(RuntimeContract):
    """One thing waiting on the user, with enough to render a card + jump."""

    v: Literal[1] = 1
    item_kind: PendingItemKind
    run_id: str
    conversation_id: str
    conversation_title: str | None = None  # ConversationRecord.title (str | None)
    gate_id: str | None = None  # GATE only
    stage_id: str | None = None  # STAGED_WRITE only
    surface_id: str | None = None  # STAGED_WRITE only (canvas jump target)
    title: str  # gate: purpose line; stage: "{connector} · {op}" target line
    connector: str
    op: str | None = None
    ledger_id: str  # r<short>·<seq> of the opening event (A1 formatter)
    opened_sequence_no: PositiveInt
    opened_at: datetime
    rows_pending: int | None = None  # row-sets only
    rows_total: int | None = None


class PendingAgentRow(RuntimeContract):
    """One run in the fleet view (this run + others with in-flight / held work)."""

    v: Literal[1] = 1
    run_id: str
    conversation_id: str
    conversation_title: str | None = None
    run_status: str  # AgentRunStatus value, presentation-ready
    pending_count: int  # this run's items in the queue


class PendingWorkResponse(RuntimeContract):
    """``GET /v1/agent/pending-work`` — the cross-run aggregate."""

    v: Literal[1] = 1
    items: tuple[PendingWorkItem, ...] = ()
    agents: tuple[PendingAgentRow, ...] = ()


# ---------------------------------------------------------------------------
# Pure fold (one run's events -> its pending items)
# ---------------------------------------------------------------------------


@dataclass
class _GateAccumulator:
    gate_id: str
    connector: str
    purpose: str
    opened_seq: int
    opened_at: datetime
    resolved: bool = False


class PendingWorkFold:
    """Pure: one run's ledger events -> pending items. No IO, no clock."""

    @classmethod
    def fold(cls, events: Sequence[object]) -> tuple[PendingWorkItem, ...]:
        """Fold typed envelopes (or any object carrying the read fields)."""

        return cls.fold_raw(
            {
                _RawKey.EVENT_TYPE: cls._event_type_value(
                    getattr(event, "event_type", "")
                ),
                _RawKey.SEQUENCE_NO: getattr(event, "sequence_no", 0),
                _RawKey.PAYLOAD: getattr(event, "payload", None),
                _RawKey.CREATED_AT: getattr(event, "created_at", None),
                _RawKey.RUN_ID: getattr(event, "run_id", ""),
                _RawKey.CONVERSATION_ID: getattr(event, "conversation_id", ""),
            }
            for event in events
        )

    @classmethod
    def fold_raw(
        cls, events: "Sequence[Mapping[str, object]] | object"
    ) -> tuple[PendingWorkItem, ...]:
        """Fold plain ``{event_type, sequence_no, payload, ...}`` dicts.

        Deterministic + total: events are processed in ``sequence_no`` order;
        malformed payloads are skipped, never raised; every non-v2 event type is
        tolerated. Items are returned ascending by ``opened_sequence_no`` (the
        cross-run sort into newest-first lives in the service).
        """

        ordered = sorted(events, key=cls._seq_of)  # type: ignore[arg-type]
        run_id, conversation_id = cls._run_scope(ordered)
        seq_created_at = cls._created_at_index(ordered)

        gates = cls._fold_gates(ordered)
        stages = StagedWriteFold.fold_raw(
            {
                _RawKey.EVENT_TYPE: str(event.get(_RawKey.EVENT_TYPE, "")),
                _RawKey.SEQUENCE_NO: cls._seq_of(event),
                _RawKey.PAYLOAD: event.get(_RawKey.PAYLOAD),
            }
            for event in ordered
        )

        items: list[PendingWorkItem] = []
        for gate in gates.values():
            if gate.resolved:
                continue
            items.append(
                cls._gate_item(
                    gate,
                    run_id=run_id,
                    conversation_id=conversation_id,
                )
            )
        for state in stages.values():
            pending, rows_pending, rows_total = cls._stage_pending(state)
            if not pending:
                continue
            items.append(
                cls._stage_item(
                    state,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    opened_at=seq_created_at.get(state.first_sequence_no),
                    rows_pending=rows_pending,
                    rows_total=rows_total,
                )
            )

        items.sort(key=lambda item: item.opened_sequence_no)
        return tuple(items)

    # -- gate pairing -------------------------------------------------------

    @classmethod
    def _fold_gates(
        cls, ordered: Sequence[Mapping[str, object]]
    ) -> dict[str, _GateAccumulator]:
        gates: dict[str, _GateAccumulator] = {}
        for event in ordered:
            event_type = str(event.get(_RawKey.EVENT_TYPE, ""))
            payload = event.get(_RawKey.PAYLOAD)
            payload = payload if isinstance(payload, Mapping) else {}
            seq = cls._seq_of(event)
            if event_type == LedgerEventType.GATE_OPENED.value:
                gate_id = cls._str_or(payload.get(Keys.Field.GATE_ID), "")
                if not gate_id or gate_id in gates:
                    continue
                gates[gate_id] = _GateAccumulator(
                    gate_id=gate_id,
                    connector=cls._str_or(payload.get(Keys.Field.CONNECTOR), ""),
                    purpose=cls._str_or(payload.get(Keys.Field.PURPOSE), ""),
                    opened_seq=seq,
                    opened_at=cls._created_at_of(event),
                )
            elif event_type == LedgerEventType.GATE_RESOLVED.value:
                gate_id = cls._str_or(payload.get(Keys.Field.GATE_ID), "")
                accumulator = gates.get(gate_id)
                if accumulator is not None:
                    accumulator.resolved = True
                # A resolve for an unseen gate is ignored (defensive + pure).
        return gates

    # -- stage predicate ----------------------------------------------------

    @classmethod
    def _stage_pending(
        cls, state: StagedWriteState
    ) -> tuple[bool, int | None, int | None]:
        """Return ``(pending, rows_pending, rows_total)`` per the §5 predicate."""

        if state.status is not StagedWriteStatus.STAGED:
            # approved / rejected / applied / apply_pending / … are not waiting.
            if state.is_rowset():
                return False, cls._rows_pending(state), cls._rows_total(state)
            return False, None, None
        if not state.is_rowset():
            return True, None, None
        rows_pending = cls._rows_pending(state)
        rows_total = cls._rows_total(state)
        return rows_pending > 0, rows_pending, rows_total

    @staticmethod
    def _rows_pending(state: StagedWriteState) -> int:
        rows = state.rows or ()
        return sum(
            1
            for row in rows
            if row.decided_by not in _RowDecider.RESOLVED and row.apply_outcome is None
        )

    @staticmethod
    def _rows_total(state: StagedWriteState) -> int:
        return len(state.rows or ())

    # -- item builders ------------------------------------------------------

    @classmethod
    def _gate_item(
        cls,
        gate: _GateAccumulator,
        *,
        run_id: str,
        conversation_id: str,
    ) -> PendingWorkItem:
        return PendingWorkItem(
            item_kind=PendingItemKind.GATE,
            run_id=run_id,
            conversation_id=conversation_id,
            gate_id=gate.gate_id,
            title=gate.purpose,
            connector=gate.connector,
            ledger_id=cls._safe_ledger_id(run_id, gate.opened_seq),
            opened_sequence_no=cls._positive(gate.opened_seq),
            opened_at=gate.opened_at,
        )

    @classmethod
    def _stage_item(
        cls,
        state: StagedWriteState,
        *,
        run_id: str,
        conversation_id: str,
        opened_at: datetime | None,
        rows_pending: int | None,
        rows_total: int | None,
    ) -> PendingWorkItem:
        connector = state.target_connector
        op = state.target_op
        return PendingWorkItem(
            item_kind=PendingItemKind.STAGED_WRITE,
            run_id=run_id,
            conversation_id=conversation_id,
            stage_id=state.stage_id,
            surface_id=state.surface_id or None,
            title=cls._stage_title(connector, op),
            connector=connector,
            op=op or None,
            ledger_id=cls._safe_ledger_id(run_id, state.first_sequence_no),
            opened_sequence_no=cls._positive(state.first_sequence_no),
            opened_at=opened_at if opened_at is not None else cls._epoch(),
            rows_pending=rows_pending,
            rows_total=rows_total,
        )

    @staticmethod
    def _stage_title(connector: str, op: str) -> str:
        """The human target line — the SAME derivation the TS fold uses (parity).

        D1's fold exposes no stage title, so both languages compose the target
        line ``"{connector} · {op}"`` from ``write.staged.target``.
        """

        return f"{connector}{Titles.SEPARATOR}{op}"

    # -- helpers ------------------------------------------------------------

    @classmethod
    def _run_scope(cls, ordered: Sequence[Mapping[str, object]]) -> tuple[str, str]:
        run_id = ""
        conversation_id = ""
        for event in ordered:
            if not run_id:
                run_id = cls._str_or(event.get(_RawKey.RUN_ID), "")
            if not conversation_id:
                conversation_id = cls._str_or(event.get(_RawKey.CONVERSATION_ID), "")
            if run_id and conversation_id:
                break
        return run_id, conversation_id

    @classmethod
    def _created_at_index(
        cls, ordered: Sequence[Mapping[str, object]]
    ) -> dict[int, datetime]:
        index: dict[int, datetime] = {}
        for event in ordered:
            index[cls._seq_of(event)] = cls._created_at_of(event)
        return index

    @classmethod
    def _created_at_of(cls, event: Mapping[str, object]) -> datetime:
        raw = event.get(_RawKey.CREATED_AT)
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str) and raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return cls._epoch()
        return cls._epoch()

    @staticmethod
    def _epoch() -> datetime:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    @staticmethod
    def _safe_ledger_id(run_id: str, seq: int) -> str:
        """A1 formatter, falling back so a malformed run id never raises the fold."""

        try:
            return LedgerIdCodec.format(run_id, seq)
        except LedgerIdFormatError:
            return f"r{run_id}{Titles.SEPARATOR.strip()}{seq}"

    @staticmethod
    def _positive(seq: int) -> int:
        # ``opened_sequence_no`` is a ``PositiveInt``; a real ledger seq is >= 1,
        # but a malformed / synthetic 0 must not raise — clamp to 1.
        return seq if seq >= 1 else 1

    @staticmethod
    def _seq_of(event: Mapping[str, object]) -> int:
        raw = event.get(_RawKey.SEQUENCE_NO, 0)
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", None)
        if isinstance(value, str):
            return value
        return str(event_type)

    @staticmethod
    def _str_or(value: object, default: str) -> str:
        return value if isinstance(value, str) else default


# ---------------------------------------------------------------------------
# Read service (fold-on-read across the caller's candidate runs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingWorkService:
    """Aggregate the caller's pending work across runs — the ``/pending-work`` model."""

    persistence: object
    event_store: object

    async def list_pending(self, *, org_id: str, user_id: str) -> PendingWorkResponse:
        """Fold every candidate run and assemble the queue + fleet.

        Cross-tenant is impossible: every read is ``org_id``-scoped, and the
        conversation scan is ``user_id``-scoped, so a foreign user's / org's runs
        are never folded. One bad run degrades to zero items (logged upstream),
        never a 500.
        """

        conversations = await self._list_conversations(org_id=org_id, user_id=user_id)
        items: list[PendingWorkItem] = []
        agents: list[PendingAgentRow] = []

        for conversation in conversations:
            conversation_id = getattr(conversation, "conversation_id", "")
            title = getattr(conversation, "title", None)
            if not conversation_id:
                continue
            runs = await self._list_runs(org_id=org_id, conversation_id=conversation_id)
            active_run = await self._active_run(
                org_id=org_id, conversation_id=conversation_id
            )
            active_run_id = getattr(active_run, "run_id", None)

            per_run_pending: dict[str, int] = {}
            for run in runs:
                run_id = getattr(run, "run_id", "")
                if not run_id or getattr(run, "user_id", None) != user_id:
                    continue
                folded = await self._fold_run(org_id=org_id, run_id=run_id)
                enriched = [
                    folded_item.model_copy(update={"conversation_title": title})
                    for folded_item in folded
                ]
                items.extend(enriched)
                per_run_pending[run_id] = len(enriched)

            for run in runs:
                run_id = getattr(run, "run_id", "")
                if not run_id or getattr(run, "user_id", None) != user_id:
                    continue
                pending_count = per_run_pending.get(run_id, 0)
                is_active = run_id == active_run_id
                if not is_active and pending_count == 0:
                    # Terminal run with nothing waiting — not a fleet row.
                    continue
                agents.append(
                    PendingAgentRow(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        conversation_title=title,
                        run_status=self._status_value(getattr(run, "status", "")),
                        pending_count=pending_count,
                    )
                )

        items.sort(
            key=lambda item: (item.opened_at, item.opened_sequence_no),
            reverse=True,
        )
        agents.sort(key=self._agent_sort_key)
        return PendingWorkResponse(items=tuple(items), agents=tuple(agents))

    # -- per-run fold (degrades, never raises) ------------------------------

    async def _fold_run(
        self, *, org_id: str, run_id: str
    ) -> tuple[PendingWorkItem, ...]:
        try:
            events = await self.event_store.list_events_after(  # type: ignore[attr-defined]
                org_id=org_id, run_id=run_id, after_sequence=0
            )
            return PendingWorkFold.fold(events)
        except Exception:  # noqa: BLE001 — one bad run must never 500 the queue.
            return ()

    # -- persistence adapters (duck-typed; keyword-only per ports.py) --------

    async def _list_conversations(
        self, *, org_id: str, user_id: str
    ) -> Sequence[object]:
        method = getattr(self.persistence, "list_conversations", None)
        if method is None:
            return ()
        return await method(
            org_id=org_id, user_id=user_id, limit=Values.CAP_CONVERSATIONS
        )

    async def _list_runs(
        self, *, org_id: str, conversation_id: str
    ) -> Sequence[object]:
        method = getattr(self.persistence, "list_runs_for_conversation", None)
        if method is None:
            return ()
        return await method(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=Values.CAP_RUNS_PER_CONVERSATION,
        )

    async def _active_run(self, *, org_id: str, conversation_id: str) -> object | None:
        method = getattr(self.persistence, "get_active_run_for_conversation", None)
        if method is None:
            return None
        return await method(org_id=org_id, conversation_id=conversation_id)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _status_value(status: object) -> str:
        value = getattr(status, "value", None)
        if isinstance(value, str):
            return value
        return str(status)

    @staticmethod
    def _agent_sort_key(agent: PendingAgentRow) -> tuple[int, int]:
        # Running-first (active statuses ahead of terminal), then more-pending
        # first. ``ACTIVE_RUN_STATUSES`` lives in runtime_api (transport layer),
        # so the pure-domain sort keys off the presentation status string.
        running = 0 if agent.run_status in _ACTIVE_STATUS_VALUES else 1
        return (running, -agent.pending_count)


# The non-terminal run-status VALUES (strings), duplicated here so the pure
# domain sort does not import ``runtime_api.schemas`` (a transport-layer module).
# Mirrors ``AgentRunStatus`` / ``ACTIVE_RUN_STATUSES``.
_ACTIVE_STATUS_VALUES: frozenset[str] = frozenset(
    {"queued", "running", "waiting_for_approval", "cancelling"}
)


__all__ = [
    "PendingAgentRow",
    "PendingItemKind",
    "PendingWorkFold",
    "PendingWorkItem",
    "PendingWorkResponse",
    "PendingWorkService",
    "Values",
]
