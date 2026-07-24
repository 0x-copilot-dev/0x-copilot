"""Legacy v2 read-side compatibility projection."""

from __future__ import annotations

from copy import deepcopy

from copilot_service_contracts.work_ledger import (
    load_ledger_contract_vectors,
    load_ledger_golden_events,
)

from agent_runtime.surfaces_v2.compatibility import (
    project_legacy_ledger_for_read,
)


def test_legacy_fixture_projects_to_shared_expected_snapshot() -> None:
    events = load_ledger_golden_events()["events"]
    expected = load_ledger_contract_vectors()["legacy_compatibility"]["expected"]
    assert isinstance(events, list)
    projection = project_legacy_ledger_for_read(deepcopy(events))
    assert projection.model_dump(mode="json") == expected


def test_every_legacy_prefix_remains_replayable() -> None:
    events = load_ledger_golden_events()["events"]
    assert isinstance(events, list)
    for length in range(len(events) + 1):
        project_legacy_ledger_for_read(deepcopy(events[:length]))


def test_legacy_gates_are_readable_but_never_generalized_write_inputs() -> None:
    events = load_ledger_golden_events()["events"]
    assert isinstance(events, list)
    projection = project_legacy_ledger_for_read(deepcopy(events))
    assert projection.legacy_gates
    assert all(
        gate.valid_generalized_write_input is False for gate in projection.legacy_gates
    )
