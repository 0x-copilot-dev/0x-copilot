"""Focused tests for the canonical v2.1 PendingWorkProjectionV2 fold."""

from __future__ import annotations

from collections.abc import Iterable

from copilot_service_contracts.work_ledger import load_ledger_golden_journeys

from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.pending_work_v2 import (
    PendingWorkProjectionV2,
    PendingWorkStatusV2,
)


_RUN_ID = "run_v21_pending"
_STAGE_A = "stg_018f47a6-7b2c-7c10-8f21-12345678c101"
_STAGE_B = "stg_018f47a6-7b2c-7c10-8f21-12345678c102"
_OPERATION_ID = "op_018f47a6-7b2c-7a10-8f21-12345678a101"


def _event(
    event_type: str,
    sequence_no: int,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "sequence_no": sequence_no,
        "payload": {"v": 1, **payload},
    }


def _staged(
    sequence_no: int,
    *,
    stage_id: str = _STAGE_A,
    policy: str = "ask",
    agent_hold: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "stage_id": stage_id,
        "operation_id": _OPERATION_ID,
        "executor": "workspace",
        "policy": policy,
    }
    if agent_hold is not None:
        payload["agent_hold"] = agent_hold
    return _event(LedgerEventType.EFFECT_STAGED.value, sequence_no, payload)


def _decision(
    sequence_no: int,
    decision: str,
    *,
    stage_id: str = _STAGE_A,
    actor: str = "user",
) -> dict[str, object]:
    return _event(
        LedgerEventType.EFFECT_DECISION_RECORDED.value,
        sequence_no,
        {
            "stage_id": stage_id,
            "revision": 1,
            "decision": decision,
            "actor": actor,
        },
    )


def _claimed(
    sequence_no: int,
    *,
    stage_id: str = _STAGE_A,
) -> dict[str, object]:
    return _event(
        LedgerEventType.EFFECT_CLAIMED.value,
        sequence_no,
        {
            "stage_id": stage_id,
            "revision": 1,
            "claim_id": "claim_pending_1",
            "executor": "workspace",
        },
    )


def _applied(
    sequence_no: int,
    outcome: str,
    *,
    stage_id: str = _STAGE_A,
) -> dict[str, object]:
    return _event(
        LedgerEventType.EFFECT_APPLIED.value,
        sequence_no,
        {"stage_id": stage_id, "revision": 1, "outcome": outcome},
    )


def _indeterminate(
    sequence_no: int,
    *,
    stage_id: str = _STAGE_A,
) -> dict[str, object]:
    return _event(
        LedgerEventType.EFFECT_INDETERMINATE.value,
        sequence_no,
        {
            "stage_id": stage_id,
            "revision": 1,
            "claim_id": "claim_pending_1",
            "reason": "worker_lost_after_dispatch",
        },
    )


def _reconciled(
    sequence_no: int,
    *,
    stage_id: str = _STAGE_A,
) -> dict[str, object]:
    return _event(
        LedgerEventType.EFFECT_RECONCILED.value,
        sequence_no,
        {
            "stage_id": stage_id,
            "revision": 1,
            "claim_id": "claim_pending_1",
            "outcome": "already_applied",
        },
    )


def _gate_opened(
    sequence_no: int, *, gate_id: str = "gate_workspace_1"
) -> dict[str, object]:
    return _event(
        LedgerEventType.GATE_OPENED_V2.value,
        sequence_no,
        {
            "gate_id": gate_id,
            "operation_id": _OPERATION_ID,
            "gate_kind": "grant",
            "capability": "workspace",
            "reason": "Select a workspace folder",
        },
    )


def _gate_resolved(
    sequence_no: int, *, gate_id: str = "gate_workspace_1"
) -> dict[str, object]:
    return _event(
        LedgerEventType.GATE_RESOLVED_V2.value,
        sequence_no,
        {"gate_id": gate_id, "decision": "granted", "actor": "user"},
    )


def _items(events: Iterable[dict[str, object]]) -> list[tuple[str, str, str, int, int]]:
    projection = PendingWorkProjectionV2.fold_raw(_RUN_ID, events)
    return [
        (
            item.subject_kind.value,
            item.subject_id,
            item.status.value,
            item.opened_sequence_no,
            item.latest_sequence_no,
        )
        for item in projection.items
    ]


class TestPendingWorkProjectionV2GoldenJourneys:
    def test_final_subjects_match_every_existing_v21_golden_journey(self) -> None:
        fixture = load_ledger_golden_journeys()
        journeys = fixture["journeys"]
        assert isinstance(journeys, list)
        for journey in journeys:
            assert isinstance(journey, dict)
            events = journey["events"]
            expected = journey["expected"]
            assert isinstance(events, list)
            assert isinstance(expected, dict)
            run_id = str(events[0]["run_id"]) if events else _RUN_ID
            projection = PendingWorkProjectionV2.fold_raw(run_id, events)
            expected_pending = expected["pending_work"]
            assert isinstance(expected_pending, list)
            assert [
                {"kind": item.subject_kind.value, "id": item.subject_id}
                for item in projection.items
            ] == expected_pending, journey["id"]

    def test_golden_prefixes_cover_stage_and_gate_transitions(self) -> None:
        fixture = load_ledger_golden_journeys()
        journeys = fixture["journeys"]
        assert isinstance(journeys, list)
        by_id = {str(journey["id"]): journey for journey in journeys}

        successful = by_id["workspace_commit_success"]
        assert isinstance(successful, dict)
        successful_events = successful["events"]
        assert isinstance(successful_events, list)
        run_id = str(successful_events[0]["run_id"])
        assert (
            PendingWorkProjectionV2.fold_raw(run_id, successful_events[:3])
            .items[0]
            .status
            is PendingWorkStatusV2.HELD
        )
        assert (
            PendingWorkProjectionV2.fold_raw(run_id, successful_events[:4])
            .items[0]
            .status
            is PendingWorkStatusV2.APPROVED
        )
        assert (
            PendingWorkProjectionV2.fold_raw(run_id, successful_events[:5])
            .items[0]
            .status
            is PendingWorkStatusV2.CLAIMED
        )
        assert (
            PendingWorkProjectionV2.fold_raw(run_id, successful_events[:6]).items == ()
        )

        drift = by_id["workspace_precondition_drift"]
        assert isinstance(drift, dict)
        drift_events = drift["events"]
        assert isinstance(drift_events, list)
        assert (
            PendingWorkProjectionV2.fold_raw(
                str(drift_events[0]["run_id"]), drift_events
            )
            .items[0]
            .status
            is PendingWorkStatusV2.RECOVERY
        )

        recovery = by_id["claim_crash_then_reconcile"]
        assert isinstance(recovery, dict)
        recovery_events = recovery["events"]
        assert isinstance(recovery_events, list)
        recovery_run_id = str(recovery_events[0]["run_id"])
        assert (
            PendingWorkProjectionV2.fold_raw(recovery_run_id, recovery_events[:5])
            .items[0]
            .status
            is PendingWorkStatusV2.INDETERMINATE
        )
        assert (
            PendingWorkProjectionV2.fold_raw(recovery_run_id, recovery_events).items
            == ()
        )

        gate = by_id["generalized_grant_gate"]
        assert isinstance(gate, dict)
        gate_events = gate["events"]
        assert isinstance(gate_events, list)
        gate_run_id = str(gate_events[0]["run_id"])
        assert (
            PendingWorkProjectionV2.fold_raw(gate_run_id, gate_events[:2])
            .items[0]
            .status
            is PendingWorkStatusV2.OPEN
        )
        assert PendingWorkProjectionV2.fold_raw(gate_run_id, gate_events).items == ()


class TestPendingWorkProjectionV2States:
    def test_auto_policy_is_queued_but_an_agent_hold_wins(self) -> None:
        assert _items([_staged(1, policy="auto")]) == [
            ("effect", _STAGE_A, "queued", 1, 1)
        ]
        assert _items([_staged(1, policy="auto", agent_hold=True)]) == [
            ("effect", _STAGE_A, "held", 1, 1)
        ]

    def test_rejected_and_cancelled_stages_are_absent(self) -> None:
        assert _items([_staged(1), _decision(2, "reject")]) == []
        assert _items([_staged(1), _decision(2, "cancel")]) == []

    def test_revised_rejected_stage_reopens_as_held_recovery(self) -> None:
        revised = _event(
            LedgerEventType.EFFECT_REVISED.value,
            3,
            {"stage_id": _STAGE_A, "revision": 2},
        )
        assert _items([_staged(1), _decision(2, "reject"), revised]) == [
            ("effect", _STAGE_A, "held", 1, 3)
        ]

    def test_stale_revision_cannot_approve_or_claim_a_newer_revision(self) -> None:
        revised = _event(
            LedgerEventType.EFFECT_REVISED.value,
            2,
            {"stage_id": _STAGE_A, "revision": 2},
        )
        assert _items(
            [
                _staged(1),
                revised,
                _decision(3, "approve"),
                _claimed(4),
            ]
        ) == [("effect", _STAGE_A, "held", 1, 2)]

    def test_terminal_applied_cancelled_and_reconciled_stages_are_absent(self) -> None:
        approved = [_staged(1), _decision(2, "approve"), _claimed(3)]
        assert _items([*approved, _applied(4, "applied")]) == []
        assert _items([*approved, _applied(4, "cancelled")]) == []
        assert _items([*approved, _indeterminate(4), _reconciled(5)]) == []

    def test_failed_partial_and_precondition_drift_are_recovery_work(self) -> None:
        approved = [_staged(1), _decision(2, "approve"), _claimed(3)]
        for outcome in ("failed", "partial", "precondition_drift"):
            assert _items([*approved, _applied(4, outcome)]) == [
                ("effect", _STAGE_A, "recovery", 1, 4)
            ]

    def test_preclaim_precondition_drift_is_recovery_work(self) -> None:
        assert _items(
            [_staged(1), _decision(2, "approve"), _applied(3, "precondition_drift")]
        ) == [("effect", _STAGE_A, "recovery", 1, 3)]


class TestPendingWorkProjectionV2Safety:
    def test_orders_by_sequence_and_never_exposes_paths_content_or_references(
        self,
    ) -> None:
        staged = _staged(3, stage_id=_STAGE_A)
        assert isinstance(staged["payload"], dict)
        staged["payload"].update(
            {
                "target_ref": "file:///Users/alice/secret.md",
                "proposal_ref": "proposal://private/full-body",
                "display_target": "/Users/alice/secret.md",
                "reason": "password=not-for-output",
                "body": "untrusted body must not leave the fold",
            }
        )
        projection = PendingWorkProjectionV2.fold_raw(
            _RUN_ID,
            [_gate_opened(4), staged, _staged(2, stage_id=_STAGE_B)],
        )
        assert [item.subject_id for item in projection.items] == [
            _STAGE_B,
            _STAGE_A,
            "gate_workspace_1",
        ]
        rendered = projection.model_dump_json()
        for forbidden in (
            "/Users/alice/secret.md",
            "proposal://private/full-body",
            "password=not-for-output",
            "untrusted body must not leave the fold",
        ):
            assert forbidden not in rendered

    def test_invalid_or_orphan_events_cannot_manufacture_pending_work(self) -> None:
        invalid_stage = _staged(1, stage_id="file:///Users/alice/secret.md")
        invalid_gate = _gate_opened(2, gate_id="file:///Users/alice/secret.md")
        orphan_claim = _claimed(3)
        malformed = {
            "event_type": LedgerEventType.EFFECT_STAGED.value,
            "sequence_no": "4",
            "payload": {"v": 1, "stage_id": _STAGE_A, "policy": "ask"},
        }
        wrong_version = _staged(5)
        assert isinstance(wrong_version["payload"], dict)
        wrong_version["payload"]["v"] = 2
        assert (
            _items(
                [invalid_stage, invalid_gate, orphan_claim, malformed, wrong_version]
            )
            == []
        )

    def test_workspace_gate_ids_are_accepted_but_paths_are_not(self) -> None:
        workspace_gate_id = f"workspace:{_OPERATION_ID}"
        assert _items([_gate_opened(1, gate_id=workspace_gate_id)]) == [
            ("gate", workspace_gate_id, "open", 1, 1)
        ]
        assert _items([_gate_opened(1, gate_id="file:///Users/alice/secret")]) == []

    def test_invalid_run_identifier_returns_no_items_without_echoing_it(self) -> None:
        projection = PendingWorkProjectionV2.fold_raw(
            "/Users/alice/secret-run",
            [_staged(1)],
        )
        assert projection.run_id == ""
        assert projection.items == ()
        assert "/Users/alice/secret-run" not in projection.model_dump_json()
