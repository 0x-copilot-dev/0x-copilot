"""Projector allow-lists for the four A3 ledger events (PRD-A3 D5).

Each ``payload_for_event`` branch is a strict allow-list: only the SDR §5 keys
survive with type checks, unknown keys drop, nested ``source`` / ``gen`` rebuild
from their own allow-lists. All four project to ``RuntimeActivityKind.EVENT``.
``read.executed`` / ``surface.created`` carry a ``*_ref`` key ⇒ OFFLOADED;
``action.classified`` has none ⇒ not OFFLOADED.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas.common import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
)
from agent_runtime.surfaces_v2.ledger_models import CURRENT_LEDGER_WRITER, ViewBasis
from runtime_api.schemas.events import RuntimeEventPresentationProjector as P

# The append funnel signs every ledger row it projects, so each allow-list result
# below carries the writer stamp on top of the SDR §5 keys.
_W = CURRENT_LEDGER_WRITER.value


class TestActionClassifiedProjection:
    def test_keeps_only_allowed_keys(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "class": "unknown",
                "basis": "default",
                "secret": "leak",
                "org_id": "org_x",
            },
        )
        assert safe == {
            "v": 1,
            "w": _W,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "class": "unknown",
            "basis": "default",
        }

    def test_bad_version_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
            payload={"v": True, "call_id": "c1"},
        )
        assert "v" not in safe
        assert safe["call_id"] == "c1"


class TestReadExecutedProjection:
    def test_keeps_latency_and_payload_ref(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 15,
                "payload_ref": "call:c1",
                "extra": "drop",
            },
        )
        assert safe == {
            "v": 1,
            "w": _W,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "latency_ms": 15,
            "payload_ref": "call:c1",
        }

    def test_negative_latency_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={"v": 1, "latency_ms": -5, "payload_ref": "call:c1"},
        )
        assert "latency_ms" not in safe


class TestSurfaceCreatedProjection:
    def test_source_rebuilt_from_nested_allow_list(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": "record://linear/get_issue/1",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue", "evil": "x"},
                "title": "ENG-1",
                "payload_ref": "call:c1",
                "junk": {"nested": "drop"},
            },
        )
        assert safe["source"] == {"connector": "linear", "op": "get_issue"}
        assert "junk" not in safe
        assert safe["surface_id"] == "record://linear/get_issue/1"

    def test_malformed_source_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={"v": 1, "surface_id": "s1", "source": {"connector": "linear"}},
        )
        assert "source" not in safe


class TestViewDerivedProjection:
    def test_gen_rebuilt_admits_model_and_ms_drops_extra(self) -> None:
        # PRD-B3 widened A3's ``gen`` allow-list to admit the generation duration
        # ``ms`` (int) the ViewDeriver now populates; untrusted extra keys still
        # never ride through.
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": "s1",
                "tier": "shaped",
                "basis": "generated",
                "spec_ref": "spec/x",
                "gen": {"model": "gpt-5.4-mini", "ms": 820, "extra": "x"},
            },
        )
        assert safe["gen"] == {"model": "gpt-5.4-mini", "ms": 820}
        assert safe["spec_ref"] == "spec/x"
        assert safe["tier"] == "shaped"

    def test_gen_negative_ms_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": "s1",
                "tier": "shaped",
                "basis": "generated",
                "gen": {"model": "m", "ms": -5},
            },
        )
        assert safe["gen"] == {"model": "m"}

    def test_gen_without_model_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={"v": 1, "surface_id": "s1", "tier": "generic", "gen": {"ms": 1}},
        )
        assert "gen" not in safe

    def test_every_declared_basis_reaches_the_wire(self) -> None:
        """Including ``selected`` — a model chose the shape, values stay the
        connector's. The transport allow-list has silently stripped a field
        before, and provenance is the last thing that should reach a receipt
        half-told.
        """

        for basis in ViewBasis:
            safe = P.payload_for_event(
                event_type=RuntimeApiEventType.VIEW_DERIVED,
                payload={
                    "v": 1,
                    "surface_id": "s1",
                    "tier": "shaped",
                    "basis": basis.value,
                },
            )
            assert safe["basis"] == basis.value, basis

    def test_an_undeclared_basis_is_dropped(self) -> None:
        """``basis`` is a closed vocabulary, not free text.

        An emitter that skipped the payload model could otherwise put a word
        nobody declared onto the wire, leaving every reader to invent a meaning
        for it. Dropping is the honest outcome: no claim beats a false one.
        """

        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": "s1",
                "tier": "telepathy",
                "basis": "vibes",
            },
        )

        assert "basis" not in safe
        assert "tier" not in safe
        assert safe["surface_id"] == "s1"


class TestReceiptEmittedProjection:
    """PRD-E1 — ``receipt.emitted`` allow-list, activity kind, redaction."""

    def test_keeps_only_v_surface_id_fold_ref(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.RECEIPT_EMITTED,
            payload={
                "v": 1,
                "surface_id": "receipt://run_1",
                "fold_ref": "ledger://run_1@42",
                "org_id": "org_x",
                "secret": "leak",
            },
        )
        assert safe == {
            "v": 1,
            "w": _W,
            "surface_id": "receipt://run_1",
            "fold_ref": "ledger://run_1@42",
        }

    def test_projects_to_event_activity(self) -> None:
        # Even a TOOL-sourced emit must not reroute into the tool bucket.
        assert (
            P.activity_kind_for(
                event_type=RuntimeApiEventType.RECEIPT_EMITTED,
                source=StreamEventSource.TOOL,
            )
            is RuntimeActivityKind.EVENT
        )

    def test_fold_ref_marks_offloaded(self) -> None:
        # ``fold_ref`` contains "ref" ⇒ the receipt is a reference, not a blob.
        state = P._redaction_state_for(
            payload={
                "v": 1,
                "surface_id": "receipt://run_1",
                "fold_ref": "ledger://run_1@42",
            },
            metadata={},
        )
        assert state is RuntimeEventRedactionState.OFFLOADED

    def test_display_title_is_run_receipt(self) -> None:
        title = P._display_title_for(
            event_type=RuntimeApiEventType.RECEIPT_EMITTED,
            payload={"v": 1, "surface_id": "receipt://run_1", "fold_ref": "x"},
        )
        assert title == "Run receipt"


class TestActivityKindAndRedaction:
    def test_all_four_project_to_event_activity(self) -> None:
        for event_type in (
            RuntimeApiEventType.ACTION_CLASSIFIED,
            RuntimeApiEventType.READ_EXECUTED,
            RuntimeApiEventType.SURFACE_CREATED,
            RuntimeApiEventType.VIEW_DERIVED,
        ):
            # Even a TOOL-sourced emit must not reroute into the tool bucket.
            assert (
                P.activity_kind_for(
                    event_type=event_type, source=StreamEventSource.TOOL
                )
                is RuntimeActivityKind.EVENT
            )

    def test_read_executed_payload_marked_offloaded(self) -> None:
        state = P._redaction_state_for(
            payload={"v": 1, "payload_ref": "call:c1"}, metadata={}
        )
        assert state is RuntimeEventRedactionState.OFFLOADED

    def test_action_classified_not_offloaded(self) -> None:
        state = P._redaction_state_for(
            payload={"v": 1, "call_id": "c1", "class": "unknown"}, metadata={}
        )
        assert state is not RuntimeEventRedactionState.OFFLOADED


class TestStagedRowSendsProjection:
    """``sends`` is the row's account of what it will dispatch — it must survive.

    ``target_args`` is server-only and ``changes`` is a column-keyed cell diff,
    so this is the ONLY complete description of the outbound write the client
    ever receives. Two rules, and they pull in opposite directions from the rest
    of this projector: unknown keys still drop, but an entry that does not parse
    empties the WHOLE list rather than being skipped. A partial account
    under-discloses an arg that still dispatches, and the row would look
    reviewable.
    """

    def project(self, rows: list[object]) -> dict:
        return P.payload_for_event(
            event_type=RuntimeApiEventType.REVISION_ADDED,
            payload={
                "v": 1,
                "stage_id": "stage_1",
                "rev": 1,
                "author": "agent",
                "diff_ref": "diff://1",
                "rowset": {"rows": rows},
            },
        )

    def row(self, sends: list[object]) -> dict[str, object]:
        return {
            "row_key": "PAR-9",
            "title": "Fix the login redirect",
            "target_args": {"issue_id": "PAR-9", "priority": "low"},
            "changes": [{"field": "priority", "old": "high", "new": "low"}],
            "sends": sends,
        }

    def test_a_well_formed_account_reaches_the_client(self) -> None:
        projected = self.project(
            [
                self.row(
                    [
                        {
                            "arg": "issue_id",
                            "origin": "carried",
                            "column": "issue_id",
                            "old": "PAR-9",
                            "new": "PAR-9",
                            "secret": "leak",
                        }
                    ]
                )
            ]
        )

        assert projected["rowset"]["rows"][0]["sends"] == [
            {
                "arg": "issue_id",
                "origin": "carried",
                "column": "issue_id",
                "old": "PAR-9",
                "new": "PAR-9",
            }
        ]

    def test_an_unknown_origin_empties_the_whole_account(self) -> None:
        projected = self.project(
            [self.row([{"arg": "issue_id", "origin": "invented", "new": "PAR-9"}])]
        )

        assert projected["rowset"]["rows"][0]["sends"] == []

    def test_one_unparseable_entry_empties_the_others_with_it(self) -> None:
        projected = self.project(
            [
                self.row(
                    [
                        {"arg": "issue_id", "origin": "carried", "new": "PAR-9"},
                        "not-an-object",
                    ]
                )
            ]
        )

        assert projected["rowset"]["rows"][0]["sends"] == []
