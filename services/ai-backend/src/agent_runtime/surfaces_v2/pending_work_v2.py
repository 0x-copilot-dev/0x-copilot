"""Pure pending-work projection for canonical Generative Surfaces v2.1 events.

``PendingWorkProjectionV2`` folds one run's persisted ledger prefix into the
unresolved effect stages and capability gates it contains.  It never opens a
reference, reads a claim store, imports the runtime API, or retains proposal,
target, reason, or content fields.  The older :mod:`pending_work` projection
continues to serve legacy v2 events during migration.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    EffectStageIdCodec,
    OperationIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
    EffectPolicy,
    GateDecision,
    GateKind,
    LedgerEventType,
)


class PendingWorkSubjectKindV2(StrEnum):
    """The only pending subjects current canonical events represent."""

    EFFECT = "effect"
    GATE = "gate"


class PendingWorkStatusV2(StrEnum):
    """Ledger-backed state of a pending subject, with no presentation text."""

    OPEN = "open"
    HELD = "held"
    QUEUED = "queued"
    APPROVED = "approved"
    CLAIMED = "claimed"
    INDETERMINATE = "indeterminate"
    RECOVERY = "recovery"


class PendingWorkItemV2(RuntimeContract):
    """An exact run/subject identifier and state, deliberately nothing else."""

    run_id: str
    subject_kind: PendingWorkSubjectKindV2
    subject_id: str
    status: PendingWorkStatusV2
    opened_sequence_no: int
    latest_sequence_no: int


class PendingWorkProjectionStateV2(RuntimeContract):
    """Deterministic result for one run's complete or replayed event prefix."""

    v: Literal[2] = 2
    run_id: str
    latest_sequence_no: int
    items: tuple[PendingWorkItemV2, ...]


class _LedgerEventLike(Protocol):
    event_type: object
    sequence_no: object
    payload: object


class _Key:
    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    PAYLOAD = "payload"
    VERSION = "v"
    STAGE_ID = "stage_id"
    GATE_ID = "gate_id"
    POLICY = "policy"
    AGENT_HOLD = "agent_hold"
    DECISION = "decision"
    ACTOR = "actor"
    REVISION = "revision"
    CLAIM_ID = "claim_id"
    EXECUTOR = "executor"
    OUTCOME = "outcome"
    OPERATION_ID = "operation_id"
    GATE_KIND = "gate_kind"
    REASON = "reason"


@dataclass
class _EffectState:
    stage_id: str
    opened_sequence_no: int
    latest_sequence_no: int
    status: PendingWorkStatusV2 | None
    revision: int = 1
    claim_id: str | None = None
    final: bool = False


@dataclass
class _GateState:
    gate_id: str
    opened_sequence_no: int
    latest_sequence_no: int


class PendingWorkProjectionV2:
    """Total, deterministic fold of canonical v2.1 stage/gate transitions.

    An invalid, unknown, or impossible-transition row is ignored.  In
    particular, an orphan claim/result cannot manufacture pending work.  A
    rejected stage can re-open only with a later ``effect.revised`` event; a
    cancelled, applied, or reconciled stage is final for this immutable id.
    """

    _OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
    # Workspace gates currently use ``workspace:op_<uuid>``.  A gate id remains
    # opaque to this fold, but never accepts a path delimiter or ``file:`` URI.
    _GATE_IDENTIFIER = re.compile(
        r"^(?!file:)[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$", re.IGNORECASE
    )
    _RECOVERY_OUTCOMES = frozenset(
        {
            EffectOutcome.PARTIAL,
            EffectOutcome.FAILED,
            EffectOutcome.PRECONDITION_DRIFT,
        }
    )

    @classmethod
    def fold(
        cls,
        run_id: str,
        events: Iterable[_LedgerEventLike],
    ) -> PendingWorkProjectionStateV2:
        """Fold envelope-like events without a transport dependency."""

        raw_events: list[dict[str, object]] = []
        for event in events:
            try:
                raw_events.append(
                    {
                        _Key.EVENT_TYPE: event.event_type,
                        _Key.SEQUENCE_NO: event.sequence_no,
                        _Key.PAYLOAD: event.payload,
                    }
                )
            except Exception:  # noqa: BLE001 - total replay boundary
                raw_events.append({})
        return cls.fold_raw(run_id, raw_events)

    @classmethod
    def fold_raw(
        cls,
        run_id: str,
        events: Iterable[Mapping[str, object] | object],
    ) -> PendingWorkProjectionStateV2:
        """Fold mapping-shaped canonical rows without I/O or mutable state."""

        ordered, latest_sequence_no = cls._ordered_events(events)
        safe_run_id = cls._opaque_identifier(run_id)
        if safe_run_id is None:
            return PendingWorkProjectionStateV2(
                run_id="", latest_sequence_no=latest_sequence_no, items=()
            )

        effects: dict[str, _EffectState] = {}
        gates: dict[str, _GateState] = {}
        for sequence_no, _index, event_type, payload in ordered:
            if not cls._is_v1(payload):
                continue
            if event_type == LedgerEventType.EFFECT_STAGED.value:
                cls._stage(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_REVISED.value:
                cls._revise(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_DECISION_RECORDED.value:
                cls._decide(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_CLAIMED.value:
                cls._claim(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_INDETERMINATE.value:
                cls._mark_indeterminate(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_APPLIED.value:
                cls._complete(effects, sequence_no, payload)
            elif event_type == LedgerEventType.EFFECT_RECONCILED.value:
                cls._reconcile(effects, sequence_no, payload)
            elif event_type == LedgerEventType.GATE_OPENED_V2.value:
                cls._open_gate(gates, sequence_no, payload)
            elif event_type == LedgerEventType.GATE_RESOLVED_V2.value:
                cls._resolve_gate(gates, payload)

        items = [
            PendingWorkItemV2(
                run_id=safe_run_id,
                subject_kind=PendingWorkSubjectKindV2.EFFECT,
                subject_id=state.stage_id,
                status=state.status,
                opened_sequence_no=state.opened_sequence_no,
                latest_sequence_no=state.latest_sequence_no,
            )
            for state in effects.values()
            if state.status is not None and not state.final
        ]
        items.extend(
            PendingWorkItemV2(
                run_id=safe_run_id,
                subject_kind=PendingWorkSubjectKindV2.GATE,
                subject_id=state.gate_id,
                status=PendingWorkStatusV2.OPEN,
                opened_sequence_no=state.opened_sequence_no,
                latest_sequence_no=state.latest_sequence_no,
            )
            for state in gates.values()
        )
        return PendingWorkProjectionStateV2(
            run_id=safe_run_id,
            latest_sequence_no=latest_sequence_no,
            items=tuple(
                sorted(
                    items,
                    key=lambda item: (
                        item.opened_sequence_no,
                        item.latest_sequence_no,
                        item.subject_kind.value,
                        item.subject_id,
                    ),
                )
            ),
        )

    @classmethod
    def _ordered_events(
        cls,
        events: Iterable[Mapping[str, object] | object],
    ) -> tuple[list[tuple[int, int, str, Mapping[str, object]]], int]:
        rows: list[tuple[int, int, str, Mapping[str, object]]] = []
        latest_sequence_no = 0
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                continue
            sequence_no = cls._positive_integer(event.get(_Key.SEQUENCE_NO))
            if sequence_no is None:
                continue
            latest_sequence_no = max(latest_sequence_no, sequence_no)
            event_type = cls._event_type(event.get(_Key.EVENT_TYPE))
            payload = event.get(_Key.PAYLOAD)
            if event_type is not None and isinstance(payload, Mapping):
                rows.append((sequence_no, index, event_type, payload))
        return sorted(rows, key=lambda row: (row[0], row[1])), latest_sequence_no

    @classmethod
    def _stage(
        cls,
        effects: dict[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        stage_id = cls._stage_id(payload)
        policy = cls._enum(payload.get(_Key.POLICY), EffectPolicy)
        executor = cls._enum(payload.get(_Key.EXECUTOR), EffectExecutorKind)
        agent_hold = payload.get(_Key.AGENT_HOLD, False)
        if (
            stage_id is None
            or policy is None
            or executor is None
            or cls._operation_id(payload.get(_Key.OPERATION_ID)) is None
            or not isinstance(agent_hold, bool)
            or policy is EffectPolicy.BLOCK
            or stage_id in effects
        ):
            return
        effects[stage_id] = _EffectState(
            stage_id=stage_id,
            opened_sequence_no=sequence_no,
            latest_sequence_no=sequence_no,
            status=(
                PendingWorkStatusV2.HELD
                if agent_hold or policy is not EffectPolicy.AUTO
                else PendingWorkStatusV2.QUEUED
            ),
        )

    @classmethod
    def _revise(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        if state is None or revision != state.revision + 1:
            return
        state.revision = revision
        state.claim_id = None
        state.status = PendingWorkStatusV2.HELD
        state.latest_sequence_no = sequence_no

    @classmethod
    def _decide(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        decision = cls._enum(payload.get(_Key.DECISION), EffectDecisionKind)
        actor = cls._enum(payload.get(_Key.ACTOR), EffectActor)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        if (
            state is None
            or decision is None
            or actor is None
            or revision != state.revision
            or state.status
            not in {PendingWorkStatusV2.HELD, PendingWorkStatusV2.QUEUED}
            or decision
            not in {
                EffectDecisionKind.APPROVE,
                EffectDecisionKind.REJECT,
                EffectDecisionKind.CANCEL,
            }
        ):
            return
        state.latest_sequence_no = sequence_no
        if decision is EffectDecisionKind.APPROVE:
            state.status = PendingWorkStatusV2.APPROVED
        elif decision is EffectDecisionKind.REJECT:
            state.status = None
        elif decision is EffectDecisionKind.CANCEL:
            state.status = None
            state.final = True

    @classmethod
    def _claim(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        claim_id = cls._opaque_identifier(payload.get(_Key.CLAIM_ID))
        if (
            state is None
            or claim_id is None
            or cls._enum(payload.get(_Key.EXECUTOR), EffectExecutorKind) is None
            or revision != state.revision
            or state.status is not PendingWorkStatusV2.APPROVED
        ):
            return
        state.claim_id = claim_id
        state.status = PendingWorkStatusV2.CLAIMED
        state.latest_sequence_no = sequence_no

    @classmethod
    def _mark_indeterminate(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        claim_id = cls._opaque_identifier(payload.get(_Key.CLAIM_ID))
        if (
            state is None
            or claim_id is None
            or claim_id != state.claim_id
            or revision != state.revision
            or not isinstance(payload.get(_Key.REASON), str)
            or state.status is not PendingWorkStatusV2.CLAIMED
        ):
            return
        state.status = PendingWorkStatusV2.INDETERMINATE
        state.latest_sequence_no = sequence_no

    @classmethod
    def _complete(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        outcome = cls._enum(payload.get(_Key.OUTCOME), EffectOutcome)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        accepted_from = {PendingWorkStatusV2.CLAIMED}
        if outcome is EffectOutcome.PRECONDITION_DRIFT:
            # The coordinator may detect drift during preparation, before it
            # creates a claim.  It is still unresolved recovery work.
            accepted_from.add(PendingWorkStatusV2.APPROVED)
        if (
            state is None
            or outcome is None
            or revision != state.revision
            or state.status not in accepted_from
        ):
            return
        state.latest_sequence_no = sequence_no
        if outcome in {
            EffectOutcome.APPLIED,
            EffectOutcome.ALREADY_APPLIED,
            EffectOutcome.CANCELLED,
        }:
            state.status = None
            state.final = True
        elif outcome is EffectOutcome.INDETERMINATE:
            state.status = PendingWorkStatusV2.INDETERMINATE
        elif outcome in cls._RECOVERY_OUTCOMES:
            state.status = PendingWorkStatusV2.RECOVERY

    @classmethod
    def _reconcile(
        cls,
        effects: Mapping[str, _EffectState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        state = cls._active_stage(effects, payload)
        revision = cls._positive_integer(payload.get(_Key.REVISION))
        claim_id = cls._opaque_identifier(payload.get(_Key.CLAIM_ID))
        if (
            state is None
            or claim_id is None
            or claim_id != state.claim_id
            or cls._enum(payload.get(_Key.OUTCOME), EffectOutcome) is None
            or revision != state.revision
            or state.status is not PendingWorkStatusV2.INDETERMINATE
        ):
            return
        state.status = None
        state.final = True
        state.latest_sequence_no = sequence_no

    @classmethod
    def _open_gate(
        cls,
        gates: dict[str, _GateState],
        sequence_no: int,
        payload: Mapping[str, object],
    ) -> None:
        gate_id = cls._gate_id(payload.get(_Key.GATE_ID))
        if (
            gate_id is None
            or cls._operation_id(payload.get(_Key.OPERATION_ID)) is None
            or cls._enum(payload.get(_Key.GATE_KIND), GateKind) is None
            or not cls._nonempty_text(payload.get("capability"))
            or not cls._nonempty_text(payload.get(_Key.REASON))
        ):
            return
        current = gates.get(gate_id)
        if current is None:
            gates[gate_id] = _GateState(gate_id, sequence_no, sequence_no)
        else:
            current.latest_sequence_no = sequence_no

    @classmethod
    def _resolve_gate(
        cls, gates: dict[str, _GateState], payload: Mapping[str, object]
    ) -> None:
        gate_id = cls._gate_id(payload.get(_Key.GATE_ID))
        if (
            gate_id is None
            or cls._enum(payload.get(_Key.DECISION), GateDecision) is None
            or cls._enum(payload.get(_Key.ACTOR), EffectActor) is None
        ):
            return
        gates.pop(gate_id, None)

    @classmethod
    def _active_stage(
        cls,
        effects: Mapping[str, _EffectState],
        payload: Mapping[str, object],
    ) -> _EffectState | None:
        stage_id = cls._stage_id(payload)
        state = effects.get(stage_id) if stage_id is not None else None
        return state if state is not None and not state.final else None

    @classmethod
    def _stage_id(cls, payload: Mapping[str, object]) -> str | None:
        value = payload.get(_Key.STAGE_ID)
        if not isinstance(value, str):
            return None
        try:
            EffectStageIdCodec.parse(value)
        except ValueError:
            return None
        return value

    @staticmethod
    def _operation_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            OperationIdCodec.parse(value)
        except ValueError:
            return None
        return value

    @staticmethod
    def _is_v1(payload: Mapping[str, object]) -> bool:
        return type(payload.get(_Key.VERSION)) is int and payload[_Key.VERSION] == 1

    @staticmethod
    def _positive_integer(value: object) -> int | None:
        return value if type(value) is int and value > 0 else None

    @staticmethod
    def _event_type(value: object) -> str | None:
        value = getattr(value, "value", value)
        return value if isinstance(value, str) else None

    @classmethod
    def _opaque_identifier(cls, value: object) -> str | None:
        if isinstance(value, str) and cls._OPAQUE_IDENTIFIER.fullmatch(value):
            return value
        return None

    @classmethod
    def _gate_id(cls, value: object) -> str | None:
        if isinstance(value, str) and cls._GATE_IDENTIFIER.fullmatch(value):
            return value
        return None

    @staticmethod
    def _nonempty_text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _enum(value: object, enum_type: type[StrEnum]) -> StrEnum | None:
        if not isinstance(value, str):
            return None
        try:
            return enum_type(value)
        except ValueError:
            return None


__all__ = [
    "PendingWorkItemV2",
    "PendingWorkProjectionStateV2",
    "PendingWorkProjectionV2",
    "PendingWorkStatusV2",
    "PendingWorkSubjectKindV2",
]
