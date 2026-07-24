"""Honest read-side projection of legacy v2 Work Ledger events.

Legacy payloads do not contain v2.1 operation ids, target/proposal digests, or
policy snapshots. This projector therefore never fabricates a valid v2.1
writer payload. It preserves the legacy identifiers and folds the information
that is actually present into a stable compatibility read model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class LegacyOperationProjection(RuntimeContract):
    legacy_call_id: str
    connector: str
    op: str
    action_class: str | None
    classification_basis: str | None
    completed: bool
    latency_ms: int | None
    result_ref: str | None
    semantic_event_types: tuple[str, ...]


class LegacyStageTarget(RuntimeContract):
    connector: str
    op: str


class LegacyStageProjection(RuntimeContract):
    legacy_stage_id: str
    surface_id: str
    executor: Literal["mcp"]
    target: LegacyStageTarget
    proposal_ref: str
    latest_revision: int
    decision_count: int
    apply_results: tuple[str, ...]
    semantic_event_types: tuple[str, ...]
    authoritative_v21: Literal[False]


class LegacyPresentationProjection(RuntimeContract):
    event_type: Literal["surface.created", "view.derived"]
    surface_id: str


class LegacyGateProjection(RuntimeContract):
    gate_id: str
    connector: str
    opened: bool
    resolved: bool
    outcome: str | None
    valid_generalized_write_input: Literal[False]


class LegacyCompatibilityProjection(RuntimeContract):
    operations: tuple[LegacyOperationProjection, ...]
    stages: tuple[LegacyStageProjection, ...]
    presentation_events: tuple[LegacyPresentationProjection, ...]
    legacy_gates: tuple[LegacyGateProjection, ...]
    passthrough_event_types: tuple[str, ...]


def project_legacy_ledger_for_read(
    events: Iterable[Mapping[str, object]],
) -> LegacyCompatibilityProjection:
    """Fold legacy events without pretending they are valid v2.1 write events."""

    operations: dict[str, dict[str, object]] = {}
    stages: dict[str, dict[str, object]] = {}
    presentations: list[LegacyPresentationProjection] = []
    gates: dict[str, dict[str, object]] = {}
    passthrough: set[str] = set()

    for event in events:
        event_type = str(event.get("event_type", ""))
        payload_raw = event.get("payload")
        if not isinstance(payload_raw, Mapping):
            passthrough.add(event_type)
            continue
        payload = payload_raw

        if event_type == "action.classified":
            call_id = str(payload["call_id"])
            operation = _operation(operations, call_id, payload)
            operation["action_class"] = str(payload["class"])
            operation["classification_basis"] = str(payload["basis"])
            _append_semantic(operation, LedgerEventType.OPERATION_CLASSIFIED.value)
        elif event_type == "read.executed":
            call_id = str(payload["call_id"])
            operation = _operation(operations, call_id, payload)
            operation["completed"] = True
            operation["latency_ms"] = int(payload["latency_ms"])
            operation["result_ref"] = str(payload["payload_ref"])
            _append_semantic(operation, LedgerEventType.OPERATION_COMPLETED.value)
        elif event_type in {"surface.created", "view.derived"}:
            presentations.append(
                LegacyPresentationProjection(
                    event_type=event_type,  # type: ignore[arg-type]
                    surface_id=str(payload["surface_id"]),
                )
            )
        elif event_type == "write.staged":
            stage_id = str(payload["stage_id"])
            target = payload["target"]
            if not isinstance(target, Mapping):
                raise ValueError("legacy write target must be an object")
            stages[stage_id] = {
                "legacy_stage_id": stage_id,
                "surface_id": str(payload["surface_id"]),
                "executor": "mcp",
                "target": {
                    "connector": str(target["connector"]),
                    "op": str(target["op"]),
                },
                "proposal_ref": str(payload["proposal_ref"]),
                "latest_revision": 0,
                "decision_count": 0,
                "apply_results": [],
                "semantic_event_types": [LedgerEventType.EFFECT_STAGED.value],
                "authoritative_v21": False,
            }
        elif event_type == "revision.added":
            stage = stages[str(payload["stage_id"])]
            stage["latest_revision"] = max(
                int(stage["latest_revision"]), int(payload["rev"])
            )
            _append_semantic(stage, LedgerEventType.EFFECT_REVISED.value)
        elif event_type == "decision.recorded":
            stage = stages[str(payload["stage_id"])]
            stage["decision_count"] = int(stage["decision_count"]) + 1
            _append_semantic(stage, LedgerEventType.EFFECT_DECISION_RECORDED.value)
        elif event_type == "write.applied":
            stage = stages[str(payload["stage_id"])]
            apply_results = stage["apply_results"]
            assert isinstance(apply_results, list)
            apply_results.append(str(payload["result"]))
            _append_semantic(stage, LedgerEventType.EFFECT_APPLIED.value)
        elif event_type == "gate.opened":
            gate_id = str(payload["gate_id"])
            gates[gate_id] = {
                "gate_id": gate_id,
                "connector": str(payload["connector"]),
                "opened": True,
                "resolved": False,
                "outcome": None,
                "valid_generalized_write_input": False,
            }
        elif event_type == "gate.resolved":
            gate_id = str(payload["gate_id"])
            gate = gates.setdefault(
                gate_id,
                {
                    "gate_id": gate_id,
                    "connector": "",
                    "opened": False,
                    "resolved": False,
                    "outcome": None,
                    "valid_generalized_write_input": False,
                },
            )
            gate["resolved"] = True
            gate["outcome"] = str(payload["outcome"])
        else:
            passthrough.add(event_type)

    return LegacyCompatibilityProjection(
        operations=tuple(
            LegacyOperationProjection.model_validate(operations[key])
            for key in sorted(operations)
        ),
        stages=tuple(
            LegacyStageProjection.model_validate(stages[key]) for key in sorted(stages)
        ),
        presentation_events=tuple(presentations),
        legacy_gates=tuple(
            LegacyGateProjection.model_validate(gates[key]) for key in sorted(gates)
        ),
        passthrough_event_types=tuple(sorted(passthrough)),
    )


def _operation(
    operations: dict[str, dict[str, object]],
    call_id: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return operations.setdefault(
        call_id,
        {
            "legacy_call_id": call_id,
            "connector": str(payload["connector"]),
            "op": str(payload["op"]),
            "action_class": None,
            "classification_basis": None,
            "completed": False,
            "latency_ms": None,
            "result_ref": None,
            "semantic_event_types": [],
        },
    )


def _append_semantic(state: dict[str, object], event_type: str) -> None:
    values = state["semantic_event_types"]
    assert isinstance(values, list)
    if event_type not in values:
        values.append(event_type)


__all__ = [
    "LegacyCompatibilityProjection",
    "LegacyGateProjection",
    "LegacyOperationProjection",
    "LegacyPresentationProjection",
    "LegacyStageProjection",
    "LegacyStageTarget",
    "project_legacy_ledger_for_read",
]
