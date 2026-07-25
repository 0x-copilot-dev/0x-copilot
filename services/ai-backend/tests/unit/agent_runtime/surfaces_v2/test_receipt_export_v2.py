"""D7 safe receipt export v2: offline integrity, compatibility, and redaction."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json

from copilot_audit_chain import AuditChainSigner
from copilot_service_contracts.work_ledger import (
    load_ledger_golden_events,
    load_ledger_golden_journeys,
)

from agent_runtime.surfaces_v2.ledger_ids import ProposalUriCodec
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.receipt import ReceiptFold, ReceiptFoldV2
from agent_runtime.surfaces_v2.receipt_export import ReceiptExportBuilder
from agent_runtime.surfaces_v2.receipt_export_v2 import (
    ReceiptExportV2Builder,
    ReceiptExportV2Verifier,
)


_RUN_ID = "run_d7_receipt"
_OPERATION_ID = "op_00000000-0000-4000-8000-000000000001"
_STAGE_ID = "stg_00000000-0000-4000-8000-000000000001"
_KEY_V1 = b"d7-receipt-export-v1-key-material"
_KEY_V2 = b"d7-receipt-export-v2-key-material"
_DIGEST = "a" * 64


@dataclass
class _Event:
    event_type: LedgerEventType | str
    sequence_no: int
    payload: dict[str, object]
    created_at: datetime = datetime(2026, 7, 25, tzinfo=timezone.utc)


def _signer(*, version: int = 1, key: bytes = _KEY_V1) -> AuditChainSigner:
    return AuditChainSigner(keys={version: key}, active_version=version)


def _events() -> list[_Event]:
    return [
        _Event(
            LedgerEventType.OPERATION_REQUESTED,
            1,
            {
                "v": 1,
                "operation_id": _OPERATION_ID,
                "producer": "system",
                "capability": "linear",
                "op": "update_issue",
                "args_digest": _DIGEST,
                "parent_operation_id": None,
            },
        ),
        _Event(
            LedgerEventType.OPERATION_CLASSIFIED,
            2,
            {
                "v": 1,
                "operation_id": _OPERATION_ID,
                "effect_class": "external_reversible",
                "basis": "catalog",
                "confidence": 1.0,
            },
        ),
        _Event(
            LedgerEventType.EFFECT_STAGED,
            3,
            {
                "v": 1,
                "stage_id": _STAGE_ID,
                "operation_id": _OPERATION_ID,
                "executor": "mcp",
                "target_ref": "mcp://linear/issue",
                "target_digest": _DIGEST,
                "proposal_ref": ProposalUriCodec.format(_STAGE_ID, 1),
                "proposal_digest": _DIGEST,
                "policy": "ask",
                "effect_class": "external_reversible",
            },
        ),
        _Event(
            LedgerEventType.EFFECT_DECISION_RECORDED,
            4,
            {
                "v": 1,
                "stage_id": _STAGE_ID,
                "revision": 1,
                "decision": "approve",
                "actor": "user",
                "proposal_digest": _DIGEST,
                "target_digest": _DIGEST,
                "actor_ref": None,
                "decided_at": None,
            },
        ),
        _Event(
            LedgerEventType.EFFECT_APPLIED,
            5,
            {
                "v": 1,
                "stage_id": _STAGE_ID,
                "revision": 1,
                "outcome": "applied",
                "receipt_ref": None,
                "result_digest": _DIGEST,
            },
        ),
        _Event(
            LedgerEventType.GATE_OPENED_V2,
            6,
            {
                "v": 1,
                "gate_id": "gate-sensitive-but-not-exported",
                "operation_id": _OPERATION_ID,
                "gate_kind": "policy",
                "capability": "linear",
                "reason": "policy required approval",
            },
        ),
        _Event(
            LedgerEventType.GATE_RESOLVED_V2,
            7,
            {
                "v": 1,
                "gate_id": "gate-sensitive-but-not-exported",
                "decision": "granted",
                "actor": "user",
            },
        ),
        _Event(
            LedgerEventType.USAGE_RECORDED,
            8,
            {
                "v": 1,
                "purpose": "run",
                "model": "gpt-5.4-mini",
                "tokens_in": 12,
                "tokens_out": 34,
                "surface_id": None,
            },
        ),
        _Event(
            LedgerEventType.READ_EXECUTED,
            9,
            {
                "v": 1,
                "call_id": "call_1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 4,
                "payload_ref": "call:call_1",
            },
        ),
    ]


def _build(
    *, signer: AuditChainSigner | None = None, events: list[_Event] | None = None
):
    signer = signer or _signer()
    bundle = ReceiptExportV2Builder(signer=signer).build(
        run_id=_RUN_ID,
        events=events or _events(),
        run_status="completed",
    )
    return bundle, signer


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resign(wire: dict[str, object], signer: AuditChainSigner) -> None:
    previous_signature: bytes | None = None
    rows = wire["rows"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        signing_payload = {
            "bundle_version": wire["bundle_version"],
            "run_id": wire["run_id"],
            "sequence_no": row["sequence_no"],
            "event_type": row["event_type"],
            "created_at": row["created_at"],
            "payload_digest": row["payload_digest"],
            "safe_payload": row["safe_payload"],
            "ref_class": row["ref_class"],
            "key_id": row["key_id"],
        }
        signature = signer.sign(
            prev_hash=previous_signature,
            payload=signing_payload,
        )
        row["prev_hash"] = (
            signature.prev_hash.hex() if signature.prev_hash is not None else None
        )
        row["signature"] = signature.signature.hex()
        row["key_version"] = signature.key_version
        row["key_id"] = f"audit-hmac:v{signature.key_version}"
        previous_signature = signature.signature
    assert previous_signature is not None
    wire["key_id"] = f"audit-hmac:v{signer.active_version}"
    wire["head_hash"] = previous_signature.hex()


class TestReceiptExportV2:
    def test_round_trip_has_safe_canonical_rows_and_terminal_receipt(self) -> None:
        bundle, signer = _build()

        result = ReceiptExportV2Verifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )

        assert result.ok is True
        assert bundle.bundle_version == 2
        assert bundle.row_count == len(bundle.rows)
        assert bundle.rows[-1].event_type == "receipt.v2"
        assert bundle.rows[-1].payload_digest == bundle.receipt_digest
        assert bundle.rows[-1].safe_payload["operations"]["requested"] == 1
        assert bundle.rows[-1].safe_payload["effects"]["external"] == 1
        assert bundle.rows[-1].safe_payload["usage"]["totals_by_purpose"] == [
            {"purpose": "run", "records": 1, "tokens_in": 12, "tokens_out": 34}
        ]
        assert all("payload" not in row.model_dump() for row in bundle.rows)

    def test_modified_dropped_reordered_and_forged_rows_fail(self) -> None:
        bundle, signer = _build()
        verifier = ReceiptExportV2Verifier(signer=signer)

        modified = bundle.model_dump(mode="json")
        modified["rows"][1]["safe_payload"]["effect_class"] = "unknown"
        assert verifier.verify(modified).ok is False

        dropped = bundle.model_dump(mode="json")
        del dropped["rows"][1]
        dropped["row_count"] -= 1
        dropped_result = verifier.verify(dropped)
        assert dropped_result.ok is False
        assert dropped_result.reason == "prev_hash mismatch"

        reordered = bundle.model_dump(mode="json")
        reordered["rows"][0], reordered["rows"][1] = (
            reordered["rows"][1],
            reordered["rows"][0],
        )
        assert verifier.verify(reordered).ok is False

        forged = ReceiptExportV2Verifier(signer=_signer(key=_KEY_V2)).verify(
            bundle.model_dump(mode="json")
        )
        assert forged.ok is False

    def test_verifier_refolds_terminal_receipt_even_if_forger_has_signing_key(
        self,
    ) -> None:
        bundle, signer = _build()
        wire = bundle.model_dump(mode="json")
        terminal = wire["rows"][-1]
        terminal["safe_payload"]["operations"]["requested"] = 99
        digest = _digest(terminal["safe_payload"])
        terminal["payload_digest"] = digest
        wire["receipt_digest"] = digest
        _resign(wire, signer)

        result = ReceiptExportV2Verifier(signer=signer).verify(wire)

        assert result.ok is False
        assert result.reason == "receipt fold mismatch"

    def test_key_rotation_and_legacy_v1_bundle_are_supported(self) -> None:
        bundle, _ = _build(signer=_signer(version=1, key=_KEY_V1))
        rotated = AuditChainSigner(keys={1: _KEY_V1, 2: _KEY_V2}, active_version=2)
        assert (
            ReceiptExportV2Verifier(signer=rotated)
            .verify(bundle.model_dump(mode="json"))
            .ok
            is True
        )

        legacy_events = [_events()[-1]]
        legacy_receipt = ReceiptFold.fold(run_id=_RUN_ID, events=legacy_events)
        legacy = ReceiptExportBuilder(signer=_signer()).build(
            run_id=_RUN_ID,
            events=legacy_events,
            receipt=legacy_receipt,
        )
        assert (
            ReceiptExportV2Verifier(signer=_signer())
            .verify(legacy.model_dump(mode="json"))
            .ok
            is True
        )

    def test_private_body_paths_tokens_and_refs_never_leave_safe_projection(
        self,
    ) -> None:
        secret = "cookie=session-secret-token"
        physical_path = "/Users/private/ledger-body.txt"
        events = [
            _Event(
                LedgerEventType.SURFACE_CREATED,
                1,
                {
                    "v": 1,
                    "surface_id": "record://linear/get_issue/issue-1",
                    "kind": "record",
                    "source": {"connector": "linear", "op": "get_issue"},
                    "title": secret,
                    "payload_ref": physical_path,
                    "raw_args": {"cookie": secret},
                },
            )
        ]
        bundle, signer = _build(events=events)
        wire = bundle.model_dump_json()

        assert (
            ReceiptExportV2Verifier(signer=signer)
            .verify(bundle.model_dump(mode="json"))
            .ok
            is True
        )
        assert secret not in wire
        assert physical_path not in wire
        assert "payload_ref" not in wire
        assert "raw_args" not in wire
        assert "cookie" not in wire
        assert bundle.rows[0].ref_class.value == "private_body_omitted"

    def test_malformed_canonical_payload_stays_verifiable_and_warning_only(
        self,
    ) -> None:
        malformed = [
            _Event(LedgerEventType.OPERATION_REQUESTED, 1, {"v": 1}),
        ]
        bundle, signer = _build(events=malformed)

        result = ReceiptExportV2Verifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )

        assert result.ok is True
        assert bundle.rows[0].safe_payload == {"valid": False}
        assert bundle.rows[-1].safe_payload["unresolved_warnings"] == [
            {"code": "malformed_events", "count": 1}
        ]


def test_builder_is_deterministic_for_a_ledger_prefix() -> None:
    events = _events()
    signer = _signer()
    first, _ = _build(signer=signer, events=events)
    second, _ = _build(signer=signer, events=copy.deepcopy(events))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_duplicate_sequence_history_is_total_and_stays_signed() -> None:
    """A corrupt but persisted duplicate sequence does not crash D7 export."""

    events = _events()
    events.insert(1, copy.deepcopy(events[0]))
    bundle, signer = _build(events=events)

    assert bundle.rows[0].sequence_no == bundle.rows[1].sequence_no
    assert (
        ReceiptExportV2Verifier(signer=signer).verify(bundle.model_dump(mode="json")).ok
        is True
    )


def test_shared_canonical_journeys_round_trip_from_mapping_rows() -> None:
    """D7 accepts the same structural canonical rows D4 folds in pure tests."""

    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    signer = _signer()
    verifier = ReceiptExportV2Verifier(signer=signer)
    for journey in journeys:
        assert isinstance(journey, dict)
        events = copy.deepcopy(journey["events"])
        assert isinstance(events, list) and events
        run_id = events[0]["run_id"]
        assert isinstance(run_id, str)

        bundle = ReceiptExportV2Builder(signer=signer).build(
            run_id=run_id,
            events=events,
            run_status="completed",
        )
        expected = ReceiptFoldV2.fold_raw(
            run_id=run_id,
            events=events,
            run_status="completed",
        )

        assert bundle.rows[-1].safe_payload == expected.model_dump(mode="json")
        assert verifier.verify(bundle.model_dump(mode="json")).ok is True


def test_legacy_compatibility_rows_refold_without_payload_disclosure() -> None:
    """D7 keeps canonical D4 compatibility facts without exporting their bodies."""

    fixture = load_ledger_golden_events()
    run_id = fixture["run_id"]
    events = copy.deepcopy(fixture["events"])
    assert isinstance(run_id, str)
    assert isinstance(events, list)
    signer = _signer()

    bundle = ReceiptExportV2Builder(signer=signer).build(
        run_id=run_id,
        events=events,
        run_status="completed",
    )
    expected = ReceiptFoldV2.fold_raw(
        run_id=run_id,
        events=events,
        run_status="completed",
    )

    assert bundle.rows[-1].safe_payload == expected.model_dump(mode="json")
    assert (
        ReceiptExportV2Verifier(signer=signer).verify(bundle.model_dump(mode="json")).ok
        is True
    )
    assert all("payload" not in row.model_dump() for row in bundle.rows)
