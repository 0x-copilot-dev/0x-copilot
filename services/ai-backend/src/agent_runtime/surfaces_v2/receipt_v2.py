"""Additive Receipt v2 — a pure, total accountability fold (PRD-E1 D4).

``ReceiptFoldV2`` intentionally coexists with the existing ``ReceiptFold``.
The original receipt remains the emitted receipt-surface/export shape; this
module is a read model only.  It folds the canonical v2.1 Work Ledger and the
contract-defined read-side compatibility rows without emitting events, opening
UI, resolving opaque references, or reading mutable state.

The fold is conservative at replay boundaries:

* malformed rows are skipped and represented only by fixed warning codes;
* only ledger counters and synthesized ledger ids leave the fold;
* usage uses its recorded purpose/token facts and ledger references only — it
  never invents an attribution edge; and
* a caller may pass the enclosing run status, but an absent/invalid value stays
  ``unknown`` rather than being inferred from a partial ledger prefix.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec, LedgerIdFormatError
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    UsagePurpose,
    WorkLedgerVocabulary,
)


class ReceiptRunStatusV2(StrEnum):
    """Run states a caller may truthfully supply to the pure fold."""

    UNKNOWN = "unknown"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    INDETERMINATE = "indeterminate"


class ReceiptOperationsV2(RuntimeContract):
    requested: int
    completed: int
    failed: int
    blocked: int


class ReceiptArtifactsV2(RuntimeContract):
    created: int
    revised: int
    promoted: int


class ReceiptReadsV2(RuntimeContract):
    completed: int


class ReceiptEffectsV2(RuntimeContract):
    proposed: int
    approved: int
    rejected: int
    applied: int
    partial: int
    held: int
    indeterminate: int
    external: int
    internal: int
    unclassified: int


class ReceiptGatesV2(RuntimeContract):
    opened: int
    resolved: int
    pending: int


class ReceiptUsageTotalV2(RuntimeContract):
    purpose: UsagePurpose
    records: int
    tokens_in: int
    tokens_out: int


class ReceiptUsageReferenceV2(RuntimeContract):
    """A recorded usage row's ledger identity, not an attribution edge."""

    ledger_id: str
    purpose: UsagePurpose


class ReceiptUsageV2(RuntimeContract):
    totals_by_purpose: tuple[ReceiptUsageTotalV2, ...]
    references: tuple[ReceiptUsageReferenceV2, ...]


class ReceiptWarningV2(RuntimeContract):
    """A fixed warning code with a deterministic count and no event content."""

    code: str
    count: int


class RunReceiptV2(RuntimeContract):
    """The additive D4 receipt projection for one ledger prefix."""

    run_id: str
    status: ReceiptRunStatusV2
    generated_at: str
    fold_ref: str
    operations: ReceiptOperationsV2
    artifacts: ReceiptArtifactsV2
    reads: ReceiptReadsV2
    effects: ReceiptEffectsV2
    gates: ReceiptGatesV2
    usage: ReceiptUsageV2
    unresolved_warnings: tuple[ReceiptWarningV2, ...]


@runtime_checkable
class _ReceiptV2EventLike(Protocol):
    """Envelope-lite shape accepted by :meth:`ReceiptFoldV2.fold`."""

    event_type: object
    sequence_no: object
    created_at: object
    payload: object


@dataclass(frozen=True)
class _EventRow:
    sequence_no: int
    index: int
    created_at: str
    event_type: LedgerEventType | None
    payload: Mapping[str, object] | None


@dataclass
class _EffectStage:
    proposed: bool = False
    status: str = "unknown"
    scope: str = "unclassified"


@dataclass
class _UsageAccumulator:
    records: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class _Warning:
    MALFORMED_EVENTS = "malformed_events"
    EFFECTS_HELD = "effects_held"
    EFFECTS_INDETERMINATE = "effects_indeterminate"
    EFFECTS_UNCLASSIFIED = "effects_unclassified"
    EFFECTS_MISSING_PROPOSAL = "effects_missing_proposal"
    GATES_PENDING = "gates_pending"
    GATE_RESOLVED_WITHOUT_OPEN = "gate_resolved_without_open"
    OPERATIONS_BLOCKED = "operations_blocked"
    RUN_STATUS_UNAVAILABLE = "run_status_unavailable"
    USAGE_REFERENCE_UNAVAILABLE = "usage_reference_unavailable"


class _Values:
    FOLD_PREFIX = "ledger://"
    FOLD_SEPARATOR = "@"
    EXTERNAL = "external"
    INTERNAL = "internal"
    UNCLASSIFIED = "unclassified"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLAIMED = "claimed"
    HELD = "held"
    APPLIED = "applied"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    PRECONDITION_DRIFT = "precondition_drift"


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_MAX_SAFE_INTEGER = (1 << 53) - 1
_OPERATION_OUTCOMES = frozenset(
    {"succeeded", "staged", "blocked", "cancelled", "failed"}
)
_EFFECT_OUTCOMES = frozenset(
    {
        _Values.APPLIED,
        _Values.PARTIAL,
        _Values.FAILED,
        _Values.CANCELLED,
        _Values.INDETERMINATE,
        "already_applied",
        _Values.PRECONDITION_DRIFT,
    }
)
_EFFECT_DECISIONS = frozenset({"approve", "reject", "restore", "cancel", "hold"})
_PENDING_EFFECT_STATUSES = frozenset(
    {_Values.PROPOSED, _Values.APPROVED, _Values.CLAIMED, _Values.HELD}
)


class ReceiptFoldV2:
    """Total, deterministic D4 receipt fold with no IO or mutable UI state."""

    @classmethod
    def fold(
        cls,
        *,
        run_id: str,
        events: Sequence[_ReceiptV2EventLike],
        status: object | None = None,
        run_status: object | None = None,
    ) -> RunReceiptV2:
        """Fold envelope-like events, tolerating malformed envelope objects."""

        raw_events: list[dict[str, object]] = []
        for event in events:
            try:
                raw_events.append(
                    {
                        "event_type": event.event_type,
                        "sequence_no": event.sequence_no,
                        "created_at": event.created_at,
                        "payload": event.payload,
                    }
                )
            except Exception:  # noqa: BLE001 - total replay boundary
                raw_events.append({})
        return cls.fold_raw(
            run_id=run_id,
            events=raw_events,
            status=status,
            run_status=run_status,
        )

    @classmethod
    def fold_raw(
        cls,
        *,
        run_id: str,
        events: Sequence[Mapping[str, object] | object],
        status: object | None = None,
        run_status: object | None = None,
    ) -> RunReceiptV2:
        """Fold raw rows without raising on unknown or malformed input.

        ``status``/``run_status`` are optional enclosing-run facts. ``run_status``
        is the explicit name for new callers; ``status`` keeps the entry point
        ergonomic for structural callers. Neither is inferred from a partial
        ledger prefix.
        """

        warnings: dict[str, int] = {}
        ordered = cls._ordered_events(events, warnings)
        resolved_status = cls._status_of(
            run_status if run_status is not None else status,
            warnings,
        )

        requested = completed = failed = blocked = 0
        created = revised = promoted = 0
        reads_completed = 0
        proposed = approved = rejected = applied = partial = indeterminate = 0
        external = internal = unclassified = 0
        gates_opened = gates_resolved = 0
        open_gates: set[str] = set()
        operation_classes: dict[str, str] = {}
        stages: dict[str, _EffectStage] = {}
        usage: dict[UsagePurpose, _UsageAccumulator] = {}
        usage_references: list[ReceiptUsageReferenceV2] = []
        through_sequence = 0
        generated_at = ""

        for event in ordered:
            if event.sequence_no >= through_sequence:
                through_sequence = event.sequence_no
                generated_at = event.created_at
            if event.event_type is None or event.payload is None:
                continue
            if not cls._valid_payload(event.event_type, event.payload):
                cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                continue

            payload = event.payload
            event_type = event.event_type
            if event_type is LedgerEventType.OPERATION_REQUESTED:
                requested += 1
            elif event_type is LedgerEventType.OPERATION_CLASSIFIED:
                operation_id = cls._identifier(payload.get("operation_id"))
                effect_class = cls._effect_class(payload.get("effect_class"))
                if operation_id is None or effect_class is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                else:
                    operation_classes[operation_id] = effect_class
            elif event_type is LedgerEventType.OPERATION_COMPLETED:
                outcome = cls._enum_value(payload.get("outcome"), _OPERATION_OUTCOMES)
                if outcome is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                completed += 1
                if outcome == _Values.FAILED:
                    failed += 1
                elif outcome == "blocked":
                    blocked += 1
            elif event_type is LedgerEventType.OPERATION_FAILED:
                failed += 1
            elif event_type is LedgerEventType.READ_EXECUTED:
                # The compatibility map names legacy reads as completed operations;
                # retain their dedicated read count as well, without inventing an id.
                if (
                    WorkLedgerVocabulary.compatibility_event_type(event_type.value)
                    is LedgerEventType.OPERATION_COMPLETED
                ):
                    completed += 1
                reads_completed += 1
            elif event_type is LedgerEventType.ARTIFACT_CREATED:
                created += 1
            elif event_type is LedgerEventType.ARTIFACT_REVISED:
                revised += 1
            elif event_type is LedgerEventType.ARTIFACT_PROMOTED:
                promoted += 1
            elif event_type is LedgerEventType.EFFECT_STAGED:
                stage_id = cls._identifier(payload.get("stage_id"))
                if stage_id is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                scope = cls._canonical_effect_scope(payload, operation_classes)
                stage = stages.setdefault(stage_id, _EffectStage())
                stage.proposed = True
                stage.status = _Values.PROPOSED
                stage.scope = scope
                proposed += 1
                if scope == _Values.EXTERNAL:
                    external += 1
                elif scope == _Values.INTERNAL:
                    internal += 1
                else:
                    unclassified += 1
            elif event_type is LedgerEventType.WRITE_STAGED:
                # Legacy write stages are an MCP connector write. The compatibility
                # projection explicitly models them as such, never as v2.1 input.
                stage_id = cls._identifier(payload.get("stage_id"))
                if stage_id is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                if (
                    WorkLedgerVocabulary.compatibility_event_type(event_type.value)
                    is LedgerEventType.EFFECT_STAGED
                ):
                    stage = stages.setdefault(stage_id, _EffectStage())
                    stage.proposed = True
                    stage.status = _Values.PROPOSED
                    stage.scope = _Values.EXTERNAL
                    proposed += 1
                    external += 1
            elif event_type is LedgerEventType.EFFECT_DECISION_RECORDED:
                decision = cls._enum_value(payload.get("decision"), _EFFECT_DECISIONS)
                stage = cls._stage_for_event(payload, stages, warnings)
                if decision is None or stage is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                if decision == "approve":
                    approved += 1
                    stage.status = _Values.APPROVED
                elif decision == "reject":
                    rejected += 1
                    stage.status = _Values.REJECTED
                elif decision == "restore":
                    stage.status = _Values.PROPOSED
                elif decision == "cancel":
                    stage.status = _Values.CANCELLED
                else:  # ``hold`` is legacy-only, but remains harmlessly readable.
                    stage.status = _Values.HELD
            elif event_type is LedgerEventType.DECISION_RECORDED:
                decision = cls._enum_value(payload.get("decision"), _EFFECT_DECISIONS)
                stage = cls._stage_for_event(payload, stages, warnings)
                if decision is None or stage is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                if (
                    WorkLedgerVocabulary.compatibility_event_type(event_type.value)
                    is LedgerEventType.EFFECT_DECISION_RECORDED
                ):
                    if decision == "approve":
                        approved += 1
                        stage.status = _Values.APPROVED
                    elif decision == "reject":
                        rejected += 1
                        stage.status = _Values.REJECTED
                    elif decision == "hold":
                        stage.status = _Values.HELD
                    elif decision == "restore":
                        stage.status = _Values.PROPOSED
            elif event_type is LedgerEventType.EFFECT_CLAIMED:
                stage = cls._stage_for_event(payload, stages, warnings)
                if stage is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                else:
                    stage.status = _Values.CLAIMED
            elif event_type in {
                LedgerEventType.EFFECT_APPLIED,
                LedgerEventType.EFFECT_RECONCILED,
            }:
                stage = cls._stage_for_event(payload, stages, warnings)
                outcome = cls._enum_value(payload.get("outcome"), _EFFECT_OUTCOMES)
                if stage is None or outcome is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                applied, partial, indeterminate = cls._note_effect_outcome(
                    stage,
                    outcome,
                    applied=applied,
                    partial=partial,
                    indeterminate=indeterminate,
                )
            elif event_type is LedgerEventType.WRITE_APPLIED:
                stage = cls._stage_for_event(payload, stages, warnings)
                result = cls._enum_value(
                    payload.get("result"),
                    {_Values.APPLIED, _Values.PARTIAL, _Values.FAILED},
                )
                if stage is None or result is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                if (
                    WorkLedgerVocabulary.compatibility_event_type(event_type.value)
                    is LedgerEventType.EFFECT_APPLIED
                ):
                    if result == _Values.APPLIED:
                        applied += 1
                    elif result == _Values.PARTIAL:
                        partial += 1
                    stage.status = result
            elif event_type is LedgerEventType.EFFECT_INDETERMINATE:
                stage = cls._stage_for_event(payload, stages, warnings)
                if stage is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                else:
                    indeterminate += 1
                    stage.status = _Values.INDETERMINATE
            elif event_type in {
                LedgerEventType.GATE_OPENED,
                LedgerEventType.GATE_OPENED_V2,
            }:
                gate_id = cls._identifier(payload.get("gate_id"))
                if gate_id is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                gates_opened += 1
                open_gates.add(gate_id)
            elif event_type in {
                LedgerEventType.GATE_RESOLVED,
                LedgerEventType.GATE_RESOLVED_V2,
            }:
                gate_id = cls._identifier(payload.get("gate_id"))
                if gate_id is None:
                    cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                    continue
                gates_resolved += 1
                if gate_id not in open_gates:
                    cls._warn(warnings, _Warning.GATE_RESOLVED_WITHOUT_OPEN)
                open_gates.discard(gate_id)
            elif event_type is LedgerEventType.USAGE_RECORDED:
                cls._note_usage(
                    payload,
                    run_id=run_id,
                    sequence_no=event.sequence_no,
                    usage=usage,
                    references=usage_references,
                    warnings=warnings,
                )

        held = sum(
            1
            for stage in stages.values()
            if stage.proposed and stage.status in _PENDING_EFFECT_STATUSES
        )
        unresolved_indeterminate = sum(
            1
            for stage in stages.values()
            if stage.proposed and stage.status == _Values.INDETERMINATE
        )
        missing_proposals = sum(
            1
            for stage in stages.values()
            if not stage.proposed and stage.status != "unknown"
        )
        if held:
            cls._warn(warnings, _Warning.EFFECTS_HELD, held)
        if unresolved_indeterminate:
            cls._warn(
                warnings, _Warning.EFFECTS_INDETERMINATE, unresolved_indeterminate
            )
        if unclassified:
            cls._warn(warnings, _Warning.EFFECTS_UNCLASSIFIED, unclassified)
        if missing_proposals:
            cls._warn(warnings, _Warning.EFFECTS_MISSING_PROPOSAL, missing_proposals)
        if open_gates:
            cls._warn(warnings, _Warning.GATES_PENDING, len(open_gates))
        if blocked:
            cls._warn(warnings, _Warning.OPERATIONS_BLOCKED, blocked)

        return RunReceiptV2(
            run_id=run_id,
            status=resolved_status,
            generated_at=generated_at,
            fold_ref=(
                f"{_Values.FOLD_PREFIX}{run_id}{_Values.FOLD_SEPARATOR}"
                f"{through_sequence}"
            ),
            operations=ReceiptOperationsV2(
                requested=requested,
                completed=completed,
                failed=failed,
                blocked=blocked,
            ),
            artifacts=ReceiptArtifactsV2(
                created=created,
                revised=revised,
                promoted=promoted,
            ),
            reads=ReceiptReadsV2(completed=reads_completed),
            effects=ReceiptEffectsV2(
                proposed=proposed,
                approved=approved,
                rejected=rejected,
                applied=applied,
                partial=partial,
                held=held,
                indeterminate=indeterminate,
                external=external,
                internal=internal,
                unclassified=unclassified,
            ),
            gates=ReceiptGatesV2(
                opened=gates_opened,
                resolved=gates_resolved,
                pending=len(open_gates),
            ),
            usage=ReceiptUsageV2(
                totals_by_purpose=tuple(
                    ReceiptUsageTotalV2(
                        purpose=purpose,
                        records=totals.records,
                        tokens_in=totals.tokens_in,
                        tokens_out=totals.tokens_out,
                    )
                    for purpose, totals in sorted(
                        usage.items(), key=lambda item: item[0].value
                    )
                ),
                references=tuple(usage_references),
            ),
            unresolved_warnings=tuple(
                ReceiptWarningV2(code=code, count=count)
                for code, count in sorted(warnings.items())
            ),
        )

    @classmethod
    def _ordered_events(
        cls,
        events: Sequence[Mapping[str, object] | object],
        warnings: dict[str, int],
    ) -> list[_EventRow]:
        rows: list[_EventRow] = []
        for index, raw_event in enumerate(events):
            if not isinstance(raw_event, Mapping):
                cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                continue
            sequence_no = cls._positive_int(raw_event.get("sequence_no"))
            if sequence_no is None:
                cls._warn(warnings, _Warning.MALFORMED_EVENTS)
                continue
            raw_event_type = getattr(
                raw_event.get("event_type"), "value", raw_event.get("event_type")
            )
            try:
                event_type = (
                    LedgerEventType(raw_event_type)
                    if isinstance(raw_event_type, str)
                    else None
                )
            except ValueError:
                event_type = None
            payload = raw_event.get("payload")
            rows.append(
                _EventRow(
                    sequence_no=sequence_no,
                    index=index,
                    created_at=cls._timestamp(raw_event.get("created_at")),
                    event_type=event_type,
                    payload=payload if isinstance(payload, Mapping) else None,
                )
            )
        return sorted(rows, key=lambda row: (row.sequence_no, row.index))

    @staticmethod
    def _valid_payload(
        event_type: LedgerEventType, payload: Mapping[str, object]
    ) -> bool:
        try:
            WorkLedgerVocabulary.validate_payload(event_type.value, payload)
        except Exception:  # noqa: BLE001 - malformed history is intentionally skipped
            return False
        return True

    @staticmethod
    def _positive_int(value: object) -> int | None:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= _MAX_SAFE_INTEGER
            else None
        )

    @staticmethod
    def _nonnegative_int(value: object) -> int | None:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_SAFE_INTEGER
            else None
        )

    @staticmethod
    def _identifier(value: object) -> str | None:
        return (
            value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None
        )

    @staticmethod
    def _timestamp(value: object) -> str:
        if isinstance(value, datetime):
            value = value.isoformat()
        return value if isinstance(value, str) and _TIMESTAMP.fullmatch(value) else ""

    @staticmethod
    def _enum_value(value: object, allowed: frozenset[str] | set[str]) -> str | None:
        return value if isinstance(value, str) and value in allowed else None

    @staticmethod
    def _effect_class(value: object) -> str | None:
        return ReceiptFoldV2._enum_value(
            value,
            {
                "none",
                "internal_reversible",
                "external_reversible",
                "external_destructive",
                "unknown",
            },
        )

    @classmethod
    def _canonical_effect_scope(
        cls,
        payload: Mapping[str, object],
        operation_classes: Mapping[str, str],
    ) -> str:
        effect_class = cls._effect_class(payload.get("effect_class"))
        if effect_class is None:
            operation_id = cls._identifier(payload.get("operation_id"))
            effect_class = operation_classes.get(operation_id or "")
        if effect_class in {"external_reversible", "external_destructive"}:
            return _Values.EXTERNAL
        if effect_class == "internal_reversible":
            return _Values.INTERNAL
        return _Values.UNCLASSIFIED

    @classmethod
    def _stage_for_event(
        cls,
        payload: Mapping[str, object],
        stages: dict[str, _EffectStage],
        warnings: dict[str, int],
    ) -> _EffectStage | None:
        stage_id = cls._identifier(payload.get("stage_id"))
        if stage_id is None:
            return None
        stage = stages.get(stage_id)
        if stage is None:
            stage = _EffectStage()
            stages[stage_id] = stage
        return stage

    @staticmethod
    def _note_effect_outcome(
        stage: _EffectStage,
        outcome: str,
        *,
        applied: int,
        partial: int,
        indeterminate: int,
    ) -> tuple[int, int, int]:
        if outcome in {_Values.APPLIED, "already_applied"}:
            applied += 1
            stage.status = _Values.APPLIED
        elif outcome == _Values.PARTIAL:
            partial += 1
            stage.status = _Values.PARTIAL
        elif outcome == _Values.INDETERMINATE:
            indeterminate += 1
            stage.status = _Values.INDETERMINATE
        else:
            stage.status = outcome
        return applied, partial, indeterminate

    @classmethod
    def _note_usage(
        cls,
        payload: Mapping[str, object],
        *,
        run_id: str,
        sequence_no: int,
        usage: dict[UsagePurpose, _UsageAccumulator],
        references: list[ReceiptUsageReferenceV2],
        warnings: dict[str, int],
    ) -> None:
        try:
            purpose = UsagePurpose(payload.get("purpose"))
        except (TypeError, ValueError):
            cls._warn(warnings, _Warning.MALFORMED_EVENTS)
            return
        tokens_in = cls._nonnegative_int(payload.get("tokens_in"))
        tokens_out = cls._nonnegative_int(payload.get("tokens_out"))
        if tokens_in is None or tokens_out is None:
            cls._warn(warnings, _Warning.MALFORMED_EVENTS)
            return
        totals = usage.setdefault(purpose, _UsageAccumulator())
        totals.records += 1
        totals.tokens_in += tokens_in
        totals.tokens_out += tokens_out
        try:
            ledger_id = LedgerIdCodec.format(run_id, sequence_no)
        except LedgerIdFormatError:
            cls._warn(warnings, _Warning.USAGE_REFERENCE_UNAVAILABLE)
            return
        references.append(ReceiptUsageReferenceV2(ledger_id=ledger_id, purpose=purpose))

    @staticmethod
    def _warn(warnings: dict[str, int], code: str, count: int = 1) -> None:
        warnings[code] = warnings.get(code, 0) + count

    @classmethod
    def _status_of(
        cls, value: object | None, warnings: dict[str, int]
    ) -> ReceiptRunStatusV2:
        if value is None:
            return ReceiptRunStatusV2.UNKNOWN
        raw = getattr(value, "value", value)
        try:
            return ReceiptRunStatusV2(raw)
        except (TypeError, ValueError):
            cls._warn(warnings, _Warning.RUN_STATUS_UNAVAILABLE)
            return ReceiptRunStatusV2.UNKNOWN


__all__ = [
    "ReceiptArtifactsV2",
    "ReceiptEffectsV2",
    "ReceiptFoldV2",
    "ReceiptGatesV2",
    "ReceiptOperationsV2",
    "ReceiptReadsV2",
    "ReceiptRunStatusV2",
    "ReceiptUsageReferenceV2",
    "ReceiptUsageTotalV2",
    "ReceiptUsageV2",
    "ReceiptWarningV2",
    "RunReceiptV2",
]
