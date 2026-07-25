"""D9 lifecycle-reference enumeration and ownership registry tests."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from copilot_service_contracts.work_ledger import (
    load_ledger_contract_vectors,
    load_ledger_golden_events,
    load_ledger_golden_journeys,
)

from agent_runtime.surfaces_v2.lifecycle_refs import (
    LifecycleDiagnosticCode,
    LifecycleNodeKind,
    LifecycleReferenceEnumerator,
    LifecycleReferenceField,
    LifecycleReferenceGraphError,
    LifecycleReferenceOwner,
    LifecycleReferenceParseError,
    LifecycleReferenceRegistry,
    LifecycleReferenceScheme,
)


def _v2_1_events() -> list[dict[str, object]]:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    return [
        event
        for journey in journeys
        if isinstance(journey, dict)
        for event in journey["events"]
        if isinstance(event, dict)
    ]


def _event(event_type: str) -> dict[str, object]:
    return next(event for event in _v2_1_events() if event["event_type"] == event_type)


_ARTIFACT_URI_SOURCE = (
    Path(__file__).resolve().parents[6] / "packages/chat-surface/src/artifacts/uri.ts"
)
_ARTIFACT_ID = "art_018f47a6-7b2c-7b10-8f21-12345678b002"


def _ui_artifact_surface_schemes() -> dict[str, str]:
    source = _ARTIFACT_URI_SOURCE.read_text(encoding="utf-8")
    pairs = re.findall(
        r'^\s*(code|document|dataset|file):\s*"(artifact-[a-z]+)",$',
        source,
        re.MULTILINE,
    )
    return dict(pairs)


def test_registry_owns_every_advertised_contract_reference_and_example() -> None:
    registry = LifecycleReferenceRegistry.default()

    registry.assert_contract_coverage()
    registry.assert_registered_examples()

    assert LifecycleReferenceEnumerator.unmapped_contract_reference_events() == set()
    assert len({row.scheme for row in registry.registrations}) == len(
        registry.registrations
    )
    assert all(row.owner for row in registry.registrations)


def test_contract_reference_vectors_are_owned_by_the_registry() -> None:
    registry = LifecycleReferenceRegistry.default()
    vectors = load_ledger_contract_vectors()["references"]
    assert isinstance(vectors, list)

    parsed = [registry.parse(vector["formatted"]) for vector in vectors]

    assert {value.scheme for value in parsed} == {
        LifecycleReferenceScheme.ARTIFACT,
        LifecycleReferenceScheme.OPERATION,
        LifecycleReferenceScheme.PROPOSAL,
        LifecycleReferenceScheme.RECEIPT,
        LifecycleReferenceScheme.WORKSPACE_TARGET,
    }


def test_every_golden_journey_and_legacy_fixture_enumerates_completely() -> None:
    enumerator = LifecycleReferenceEnumerator()
    legacy = load_ledger_golden_events()["events"]
    assert isinstance(legacy, list)
    legacy_graph = enumerator.enumerate(run_id="legacy_fixture", events=legacy)
    assert len(legacy_graph.nodes) > len(legacy)
    assert len(legacy_graph.edges) >= len(legacy)

    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        journey_id = journey["id"]
        events = journey["events"]
        assert isinstance(journey_id, str)
        assert isinstance(events, list)
        graph = enumerator.enumerate(run_id=f"journey_{journey_id}", events=events)
        event_nodes = [
            node for node in graph.nodes if node.kind is LifecycleNodeKind.EVENT
        ]
        assert len(event_nodes) == len(events), journey_id


def test_operation_requested_derives_the_canonical_args_reference() -> None:
    event = deepcopy(_event("operation.requested"))
    graph = LifecycleReferenceEnumerator().enumerate(
        run_id="operation_run", events=[event]
    )

    references = {
        node.identifier
        for node in graph.nodes
        if node.kind is LifecycleNodeKind.OPERATION
    }
    assert any(reference.endswith("/args") for reference in references)


def test_message_namespace_is_disambiguated_by_shape_and_context() -> None:
    registry = LifecycleReferenceRegistry.default()

    source = registry.parse("message://msg_01")
    surface = registry.parse_surface_id("message://linear/get_issue/ENG_1")

    assert source.scheme is LifecycleReferenceScheme.MESSAGE
    assert source.owner is LifecycleReferenceOwner.RUNTIME_EVENT_STORE
    assert surface is not None
    assert surface.scheme is LifecycleReferenceScheme.MESSAGE_SURFACE
    assert surface.owner is LifecycleReferenceOwner.SURFACE_PRESENTATION
    with pytest.raises(LifecycleReferenceParseError):
        registry.parse("message://linear/get_issue/ENG_1")


def test_surface_file_uri_is_only_valid_in_a_surface_id_context() -> None:
    registry = LifecycleReferenceRegistry.default()

    surface = registry.parse_surface_id("file://linear/get_issue/ENG_1")
    assert surface is not None
    assert surface.scheme is LifecycleReferenceScheme.FILE_SURFACE
    with pytest.raises(LifecycleReferenceParseError):
        registry.parse("file://linear/get_issue/ENG_1")
    with pytest.raises(LifecycleReferenceParseError):
        registry.parse_surface_id("file:///private/path")


def test_bare_surface_ids_are_explicitly_owned_and_graph_enumerated() -> None:
    registry = LifecycleReferenceRegistry.default()
    parsed = registry.parse_surface_id("surface_issue")

    assert parsed.scheme is LifecycleReferenceScheme.BARE_SURFACE
    assert parsed.owner is LifecycleReferenceOwner.SURFACE_PRESENTATION

    event = deepcopy(_event("surface.created"))
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["surface_id"] = "surface_issue"
    graph = LifecycleReferenceEnumerator().enumerate(
        run_id="bare_surface", events=[event]
    )
    surface = next(node for node in graph.nodes if node.identifier == "surface_issue")
    assert surface.kind is LifecycleNodeKind.SURFACE
    assert surface.owner is LifecycleReferenceOwner.SURFACE_PRESENTATION


@pytest.mark.parametrize(
    ("surface_id", "diagnostic_code"),
    (
        ("file:private-token", LifecycleDiagnosticCode.FORBIDDEN_REFERENCE),
        ("data:secret", LifecycleDiagnosticCode.FORBIDDEN_REFERENCE),
        ("https:private-token", LifecycleDiagnosticCode.FORBIDDEN_REFERENCE),
        ("unknown-owner:secret", LifecycleDiagnosticCode.UNKNOWN_SCHEME),
    ),
)
def test_uri_like_surface_ids_fail_closed_without_leaking_raw_values(
    surface_id: str,
    diagnostic_code: LifecycleDiagnosticCode,
) -> None:
    event = deepcopy(_event("surface.created"))
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["surface_id"] = surface_id

    with pytest.raises(LifecycleReferenceGraphError) as raised:
        LifecycleReferenceEnumerator().enumerate(
            run_id="surface_failure", events=[event]
        )

    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code is diagnostic_code
    assert diagnostic.event_type is not None
    diagnostic_json = json.dumps(
        [row.model_dump(mode="json") for row in raised.value.diagnostics]
    )
    assert surface_id not in diagnostic_json
    assert "secret" not in diagnostic_json
    assert "private" not in diagnostic_json


def test_ui_artifact_surface_schemes_have_exact_registry_parity() -> None:
    registry = LifecycleReferenceRegistry.default()
    ui_schemes = _ui_artifact_surface_schemes()
    registered = {
        registration.wire_scheme: registration
        for registration in registry.registrations
        if registration.owner is LifecycleReferenceOwner.ARTIFACT_REPOSITORY
        and registration.node_kind is LifecycleNodeKind.SURFACE
    }

    assert ui_schemes == {
        "code": "artifact-code",
        "document": "artifact-document",
        "dataset": "artifact-dataset",
        "file": "artifact-file",
    }
    assert set(registered) == set(ui_schemes.values())
    assert "artifact-doc" not in registered
    for scheme in ui_schemes.values():
        parsed = registry.parse_surface_id(f"{scheme}://{_ARTIFACT_ID}@2")
        assert parsed.owner is LifecycleReferenceOwner.ARTIFACT_REPOSITORY
        assert parsed.node_kind is LifecycleNodeKind.SURFACE


@pytest.mark.parametrize(
    "surface_id",
    (
        "artifact-document://not_an_artifact@1",
        f"artifact-dataset://{_ARTIFACT_ID}@0",
        f"artifact-file://{_ARTIFACT_ID}@9007199254740992",
        f"artifact-doc://{_ARTIFACT_ID}@1",
    ),
)
def test_artifact_surface_ids_require_canonical_artifact_and_safe_revision(
    surface_id: str,
) -> None:
    registry = LifecycleReferenceRegistry.default()

    with pytest.raises(LifecycleReferenceParseError) as raised:
        registry.parse_surface_id(surface_id)

    diagnostic_json = json.dumps(
        [row.model_dump(mode="json") for row in raised.value.diagnostics]
    )
    assert surface_id not in diagnostic_json


@pytest.mark.parametrize(
    "reference",
    (
        "unknown-owner://opaque_01",
        "file:///private/secret.txt",
        "/Users/private/secret.txt",
        "payload://evt_01/../private",
        "payload://evt_01%2f..%2fsecret",
        "operation://op_018f47a6-7b2c-7a10-8f21-12345678a004/args?token=secret",
        "data:text/plain,secret",
        "https://example.test/private",
    ),
)
def test_malformed_or_private_references_fail_with_redacted_diagnostics(
    reference: str,
) -> None:
    registry = LifecycleReferenceRegistry.default()

    with pytest.raises(LifecycleReferenceParseError) as raised:
        registry.parse(reference)

    diagnostic_json = json.dumps(
        [diagnostic.model_dump(mode="json") for diagnostic in raised.value.diagnostics]
    )
    assert reference not in diagnostic_json
    assert "secret" not in diagnostic_json
    assert "private" not in diagnostic_json


def test_unknown_ledger_ref_aborts_the_entire_graph_with_no_body_leak() -> None:
    event = deepcopy(_event("operation.completed"))
    sequence_no = event.get("sequence_no", 1)
    assert isinstance(sequence_no, int)
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["result_ref"] = "unknown-owner://private/secret-token"

    with pytest.raises(LifecycleReferenceGraphError) as raised:
        LifecycleReferenceEnumerator().enumerate(run_id="ref_failure", events=[event])

    diagnostics = raised.value.diagnostics
    assert diagnostics == (
        diagnostics[0].model_copy(
            update={
                "code": LifecycleDiagnosticCode.UNKNOWN_SCHEME,
                "event_type": diagnostics[0].event_type,
                "sequence_no": sequence_no,
                "field": LifecycleReferenceField.RESULT_REF,
            }
        ),
    )
    diagnostic_json = json.dumps(
        [diagnostic.model_dump(mode="json") for diagnostic in diagnostics]
    )
    assert "private" not in diagnostic_json
    assert "secret" not in diagnostic_json
    assert "token" not in diagnostic_json


def test_closed_field_enumeration_does_not_scan_display_or_body_text() -> None:
    event = deepcopy(_event("effect.staged"))
    payload = event["payload"]
    assert isinstance(payload, dict)
    payload["display_target"] = "unknown-owner://not-a-lifecycle-reference"

    graph = LifecycleReferenceEnumerator().enumerate(
        run_id="closed_fields", events=[event]
    )

    assert graph.nodes
    assert all("unknown-owner" not in node.identifier for node in graph.nodes)


def test_sequence_errors_are_safe_and_fail_closed() -> None:
    event = deepcopy(_event("operation.requested"))
    event["sequence_no"] = 0

    with pytest.raises(LifecycleReferenceGraphError) as raised:
        LifecycleReferenceEnumerator().enumerate(
            run_id="sequence_failure", events=[event]
        )

    assert raised.value.diagnostics[0].code is LifecycleDiagnosticCode.INVALID_SEQUENCE
    assert raised.value.diagnostics[0].event_type is not None
