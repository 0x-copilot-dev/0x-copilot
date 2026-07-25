"""Additive D4 ReceiptFoldV2 tests.

The shared v2.1 golden journeys are deliberately replayed at every prefix here
and in ``projectReceiptV2.test.ts``. That keeps the two pure projectors pinned
to one event fixture without changing the existing receipt fixture or mount.
"""

from __future__ import annotations

import json
from copy import deepcopy

from copilot_service_contracts.work_ledger import (
    load_ledger_expected_receipt,
    load_ledger_golden_events,
    load_ledger_golden_journeys,
)

from agent_runtime.surfaces_v2.receipt import ReceiptFoldV2


def _journey(journey_id: str) -> dict[str, object]:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    return next(
        item
        for item in journeys
        if isinstance(item, dict) and item.get("id") == journey_id
    )


def _events(journey: dict[str, object]) -> list[dict[str, object]]:
    events = journey["events"]
    assert isinstance(events, list)
    return deepcopy(events)


def test_shared_golden_journey_prefixes_are_total_and_idempotent() -> None:
    """Cross-language fixture/prefix parity pin — mirrored in the chat test."""

    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        run_id = journey["events"][0]["run_id"]  # type: ignore[index]
        assert isinstance(run_id, str)
        events = _events(journey)
        for prefix_length in range(len(events) + 1):
            prefix = events[:prefix_length]
            receipt = ReceiptFoldV2.fold_raw(
                run_id=run_id,
                events=prefix,
                run_status="completed",
            )
            assert receipt.model_dump(mode="json") == ReceiptFoldV2.fold_raw(
                run_id=run_id,
                events=prefix,
                run_status="completed",
            ).model_dump(mode="json")


def test_shared_journey_counters_match_the_canonical_fixture_facts() -> None:
    """The new names retain the ledger facts already pinned in A1's fixture."""

    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        events = _events(journey)
        run_id = events[0]["run_id"]
        assert isinstance(run_id, str)
        expected = journey["expected"]
        assert isinstance(expected, dict)
        expected_receipt = expected["receipt"]
        assert isinstance(expected_receipt, dict)
        expected_operations = expected_receipt["operations"]
        expected_effects = expected_receipt["effects"]
        expected_gates = expected_receipt["gates"]
        assert isinstance(expected_operations, dict)
        assert isinstance(expected_effects, dict)
        assert isinstance(expected_gates, dict)

        receipt = ReceiptFoldV2.fold_raw(
            run_id=run_id,
            events=events,
            run_status="completed",
        )

        assert receipt.operations.requested == expected_operations["requested"]
        assert receipt.operations.completed == sum(
            value
            for key, value in expected_operations.items()
            if key not in {"requested", "failed"}
        )
        assert receipt.operations.failed == expected_operations["failed"]
        assert receipt.effects.proposed == expected_effects["staged"]
        assert receipt.effects.applied == (
            expected_effects["applied"] + expected_effects["already_applied"]
        )
        assert receipt.effects.partial == expected_effects["partial"]
        assert receipt.effects.indeterminate == expected_effects["indeterminate"]
        assert receipt.gates.opened == expected_gates["opened"]
        assert receipt.gates.resolved == expected_gates["resolved"]


def test_legacy_expected_receipt_remains_readable_as_compatibility_facts() -> None:
    """Read the original expected fixture without altering its V1 receipt shape."""

    fixture = load_ledger_golden_events()
    events = fixture["events"]
    assert isinstance(events, list)
    expected = load_ledger_expected_receipt()
    receipt = ReceiptFoldV2.fold_raw(
        run_id=str(fixture["run_id"]),
        events=deepcopy(events),
        run_status="completed",
    )

    assert receipt.fold_ref == expected["fold_ref"]
    assert receipt.reads.completed == expected["tiles"]["reads_auto_ran"]
    assert receipt.effects.proposed > 0
    assert receipt.effects.external == receipt.effects.proposed


def test_artifact_counts_and_external_effect_counts_are_distinct() -> None:
    artifact = _journey("model_authored_code_artifact")
    effect = _journey("workspace_commit_success")
    artifact_events = _events(artifact)
    effect_events = _events(effect)
    artifact_run_id = artifact_events[0]["run_id"]
    effect_run_id = effect_events[0]["run_id"]
    assert isinstance(artifact_run_id, str)
    assert isinstance(effect_run_id, str)

    artifact_receipt = ReceiptFoldV2.fold_raw(
        run_id=artifact_run_id,
        events=artifact_events,
        run_status="completed",
    )
    effect_receipt = ReceiptFoldV2.fold_raw(
        run_id=effect_run_id,
        events=effect_events,
        run_status="completed",
    )

    assert artifact_receipt.artifacts.created == 1
    assert artifact_receipt.effects.external == 0
    assert effect_receipt.artifacts.created == 0
    assert effect_receipt.effects.external == 1
    assert effect_receipt.effects.applied == 1


def test_usage_is_ledger_backed_and_malformed_payloads_never_leak() -> None:
    secret = "sk-never-copy-this"
    path = "/Users/alice/private.txt"
    events = [
        {
            "event_type": "usage.recorded",
            "sequence_no": 1,
            "created_at": "2026-07-25T00:00:01Z",
            "payload": {
                "v": 1,
                "purpose": "run",
                "model": secret,
                "tokens_in": 3,
                "tokens_out": 5,
            },
        },
        {
            "event_type": "effect.staged",
            "sequence_no": 2,
            "created_at": "2026-07-25T00:00:02Z",
            "payload": {
                "v": 1,
                "stage_id": "stg_018f47a6-7b2c-7c10-8f21-12345678c001",
                "operation_id": "op_018f47a6-7b2c-7a10-8f21-12345678a001",
                "executor": "workspace",
                "target_ref": f"file://{path}",
                "target_digest": "a" * 64,
                "proposal_ref": "proposal://stg_018f47a6-7b2c-7c10-8f21-12345678c001/1",
                "proposal_digest": "b" * 64,
                "policy": "require",
                "body": secret,
            },
        },
        {"event_type": "unknown.event", "sequence_no": 3, "payload": secret},
    ]

    receipt = ReceiptFoldV2.fold_raw(
        run_id="run00000001abcdef",
        events=events,
        run_status="completed",
    )
    rendered = json.dumps(receipt.model_dump(mode="json"), sort_keys=True)

    assert receipt.usage.totals_by_purpose[0].tokens_in == 3
    assert receipt.usage.totals_by_purpose[0].tokens_out == 5
    assert len(receipt.usage.references) == 1
    assert receipt.effects.proposed == 0
    assert "malformed_events" in {
        warning.code for warning in receipt.unresolved_warnings
    }
    assert secret not in rendered
    assert path not in rendered
    assert "file://" not in rendered
