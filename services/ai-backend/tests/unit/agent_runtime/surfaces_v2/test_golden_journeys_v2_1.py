"""Deterministic referee fold for every v2.1 golden journey.

This fold is test-only: A1 defines contracts, not runtime behavior. The
TypeScript test contains the same small referee and compares against the same
checked-in expected snapshots, providing transitive byte-level parity without
shipping a second production projector.
"""

from __future__ import annotations

from copy import deepcopy

from copilot_service_contracts.work_ledger import load_ledger_golden_journeys


def _empty_receipt() -> dict[str, object]:
    return {
        "operations": {
            "requested": 0,
            "succeeded": 0,
            "staged": 0,
            "blocked": 0,
            "cancelled": 0,
            "failed": 0,
        },
        "effects": {
            "staged": 0,
            "applied": 0,
            "partial": 0,
            "failed": 0,
            "cancelled": 0,
            "indeterminate": 0,
            "already_applied": 0,
            "precondition_drift": 0,
        },
        "gates": {"opened": 0, "resolved": 0},
    }


def _fold(events: list[dict[str, object]]) -> dict[str, object]:
    artifacts: dict[str, dict[str, object]] = {}
    stages: dict[str, dict[str, object]] = {}
    canvas: list[dict[str, object]] = []
    open_gates: dict[str, dict[str, object]] = {}
    receipt = _empty_receipt()
    operation_counts = receipt["operations"]
    effect_counts = receipt["effects"]
    gate_counts = receipt["gates"]
    assert isinstance(operation_counts, dict)
    assert isinstance(effect_counts, dict)
    assert isinstance(gate_counts, dict)

    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        assert isinstance(event_type, str)
        assert isinstance(payload, dict)

        if event_type == "operation.requested":
            operation_counts["requested"] += 1
        elif event_type == "operation.completed":
            operation_counts[str(payload["outcome"])] += 1
        elif event_type == "operation.failed":
            operation_counts["failed"] += 1
        elif event_type == "artifact.created":
            artifact_id = str(payload["artifact_id"])
            artifacts[artifact_id] = {
                "artifact_id": artifact_id,
                "kind": payload["kind"],
                "revision": payload["revision"],
                "content_ref": payload["content_ref"],
                "content_digest": payload["content_digest"],
                "author": payload["author"],
                "presentation": None,
            }
        elif event_type == "artifact.revised":
            artifact = artifacts[str(payload["artifact_id"])]
            artifact.update(
                revision=payload["revision"],
                content_ref=payload["content_ref"],
                content_digest=payload["content_digest"],
                author=payload["author"],
            )
        elif event_type == "artifact.presentation_decided":
            artifact_id = str(payload["artifact_id"])
            artifact = artifacts[artifact_id]
            surface_id = payload.get("surface_id")
            artifact["presentation"] = {
                "decision": payload["decision"],
                "basis": payload["basis"],
                "surface_id": surface_id,
            }
            canvas[:] = [
                row
                for row in canvas
                if not (
                    row["subject_type"] == "artifact"
                    and row["subject_id"] == artifact_id
                )
            ]
            if payload["decision"] == "canvas":
                canvas.append(
                    {
                        "subject_type": "artifact",
                        "subject_id": artifact_id,
                        "surface_id": surface_id,
                    }
                )
        elif event_type == "surface.created":
            surface_id = str(payload["surface_id"])
            canvas.append(
                {
                    "subject_type": "record",
                    "subject_id": surface_id,
                    "surface_id": surface_id,
                }
            )
        elif event_type == "effect.staged":
            stage_id = str(payload["stage_id"])
            stages[stage_id] = {
                "stage_id": stage_id,
                "operation_id": payload["operation_id"],
                "executor": payload["executor"],
                "target_ref": payload["target_ref"],
                "target_digest": payload["target_digest"],
                "proposal_ref": payload["proposal_ref"],
                "proposal_digest": payload["proposal_digest"],
                "revision": 1,
                "status": "staged",
                "policy": payload["policy"],
                "decision": None,
                "claim_id": None,
                "outcome": None,
            }
            canvas.append(
                {
                    "subject_type": "stage",
                    "subject_id": stage_id,
                    "surface_id": None,
                }
            )
            effect_counts["staged"] += 1
        elif event_type == "effect.revised":
            stage = stages[str(payload["stage_id"])]
            stage.update(
                revision=payload["revision"],
                proposal_ref=payload["proposal_ref"],
                proposal_digest=payload["proposal_digest"],
                status="staged",
                decision=None,
                claim_id=None,
                outcome=None,
            )
        elif event_type == "effect.decision_recorded":
            stage = stages[str(payload["stage_id"])]
            decision = str(payload["decision"])
            stage["decision"] = {
                "decision": decision,
                "actor": payload["actor"],
            }
            stage["status"] = {
                "approve": "approved",
                "reject": "rejected",
                "restore": "staged",
                "cancel": "cancelled",
            }[decision]
        elif event_type == "effect.claimed":
            stage = stages[str(payload["stage_id"])]
            stage["status"] = "claimed"
            stage["claim_id"] = payload["claim_id"]
        elif event_type == "effect.applied":
            _apply_outcome(stages[str(payload["stage_id"])], str(payload["outcome"]))
            effect_counts[str(payload["outcome"])] += 1
        elif event_type == "effect.indeterminate":
            stage = stages[str(payload["stage_id"])]
            stage["status"] = "indeterminate"
            stage["claim_id"] = payload["claim_id"]
            stage["outcome"] = "indeterminate"
            effect_counts["indeterminate"] += 1
        elif event_type == "effect.reconciled":
            _apply_outcome(stages[str(payload["stage_id"])], str(payload["outcome"]))
            effect_counts[str(payload["outcome"])] += 1
        elif event_type == "gate.opened.v2":
            gate_id = str(payload["gate_id"])
            open_gates[gate_id] = {"kind": "gate", "id": gate_id}
            gate_counts["opened"] += 1
        elif event_type == "gate.resolved.v2":
            open_gates.pop(str(payload["gate_id"]), None)
            gate_counts["resolved"] += 1

    pending_statuses = {
        "staged",
        "approved",
        "claimed",
        "indeterminate",
        "precondition_drift",
    }
    pending = list(open_gates.values())
    pending.extend(
        {"kind": "effect", "id": stage_id}
        for stage_id, stage in stages.items()
        if stage["status"] in pending_statuses
    )
    pending.sort(key=lambda row: (str(row["kind"]), str(row["id"])))
    return {
        "artifacts": [artifacts[key] for key in sorted(artifacts)],
        "stages": [stages[key] for key in sorted(stages)],
        "canvas": canvas,
        "receipt": receipt,
        "pending_work": pending,
    }


def _apply_outcome(stage: dict[str, object], outcome: str) -> None:
    stage["outcome"] = outcome
    stage["status"] = {
        "applied": "applied",
        "already_applied": "applied",
        "partial": "partial",
        "failed": "failed",
        "cancelled": "cancelled",
        "indeterminate": "indeterminate",
        "precondition_drift": "precondition_drift",
    }[outcome]


def test_every_prefix_folds_without_throwing() -> None:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    assert len(journeys) >= 12
    for journey in journeys:
        assert isinstance(journey, dict)
        events = journey["events"]
        assert isinstance(events, list)
        for length in range(len(events) + 1):
            _fold(deepcopy(events[:length]))


def test_final_snapshots_match_checked_in_referee() -> None:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        events = journey["events"]
        expected = journey["expected"]
        assert isinstance(events, list)
        assert _fold(deepcopy(events)) == expected, journey["id"]


def test_destructive_effect_stays_held_despite_allow_always_posture() -> None:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    journey = next(
        item
        for item in journeys
        if isinstance(item, dict) and item.get("id") == "destructive_effect_held"
    )
    assert journey["policy_context"] == {
        "configured_write_policy": "allow_always",
        "effect_class": "external_destructive",
        "resolved_effect_policy": "require",
    }
    events = journey["events"]
    assert isinstance(events, list)
    assert not any(
        event["event_type"]
        in {"effect.decision_recorded", "effect.claimed", "effect.applied"}
        for event in events
    )
    folded = _fold(deepcopy(events))
    assert folded["stages"][0]["status"] == "staged"  # type: ignore[index]
    assert folded["pending_work"] == [
        {
            "kind": "effect",
            "id": "stg_018f47a6-7b2c-7c10-8f21-12345678c012",
        }
    ]
