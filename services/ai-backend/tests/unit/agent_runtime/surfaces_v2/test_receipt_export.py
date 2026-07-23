"""HMAC-chained receipt export builder + verifier (PRD-E3 D1, the tamper suite).

The DoD's audit-hardening proof: a genuine export verifies, and flipping ONE
byte anywhere — a row payload, the sealed receipt, a signature, the order, a
dropped row — fails verification with ``broken_at_seq`` on the offending row.
Mirrors ``packages/audit-chain/tests/test_signer.py`` conventions (explicit
keys, rotation-key map, wrong-key forge). Pure builder/verifier — no IO, no
network, no live LLM.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone

from copilot_audit_chain import AuditChainSigner

from agent_runtime.surfaces_v2.entities import (
    ReceiptAttribution,
    RunReceipt,
    RunReceiptRow,
    RunReceiptTiles,
)
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.receipt_export import (
    ReceiptExportBuilder,
    ReceiptExportVerifier,
)

_RUN_ID = "run7f3abed0c1"
_KEY_V1 = b"e3-receipt-export-key-v1-32-bytes"
_KEY_V2 = b"e3-receipt-export-key-v2-32-bytes"


def _signer(*, version: int = 1, key: bytes = _KEY_V1) -> AuditChainSigner:
    return AuditChainSigner(keys={version: key}, active_version=version)


@dataclass
class _FakeEvent:
    """The four structural fields the builder reads (a ``RuntimeEventEnvelope`` fits)."""

    event_type: str
    sequence_no: int
    payload: dict[str, object]
    created_at: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ledger_events() -> list[_FakeEvent]:
    """A small run: a read + a surface + a view (all ledger vocabulary)."""

    return [
        _FakeEvent(
            LedgerEventType.READ_EXECUTED.value,
            1,
            {
                "v": 1,
                "call_id": "call_1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 12,
                "payload_ref": "call:call_1",
            },
        ),
        _FakeEvent(
            LedgerEventType.SURFACE_CREATED.value,
            2,
            {
                "v": 1,
                "surface_id": "record://linear/get_issue/issue-1",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue"},
                "title": "ENG-1 Fix",
                "payload_ref": "call:call_1",
            },
        ),
        _FakeEvent(
            LedgerEventType.VIEW_DERIVED.value,
            3,
            {
                "v": 1,
                "surface_id": "record://linear/get_issue/issue-1",
                "tier": "shaped",
                "basis": "registry",
            },
        ),
    ]


def _receipt(events: list[_FakeEvent]) -> RunReceipt:
    return ReceiptFold.fold(run_id=_RUN_ID, events=events)


def _build(events: list[_FakeEvent], *, signer: AuditChainSigner | None = None):
    signer = signer or _signer()
    receipt = _receipt(events)
    bundle = ReceiptExportBuilder(signer=signer).build(
        run_id=_RUN_ID, events=events, receipt=receipt
    )
    return bundle, signer


class TestBuild:
    def test_build_chains_ledger_events_in_sequence_order(self) -> None:
        bundle, _ = _build(_ledger_events())
        # 3 ledger rows + 1 synthetic receipt row.
        assert len(bundle.rows) == 4
        assert [r.seq for r in bundle.rows] == [1, 2, 3, 4]
        assert [r.sequence_no for r in bundle.rows[:3]] == [1, 2, 3]
        assert bundle.rows[0].prev_hash is None
        # Each row's prev_hash chains to the prior row's signature.
        for prev, cur in zip(bundle.rows, bundle.rows[1:]):
            assert cur.prev_hash == prev.signature
        assert bundle.head_hash == bundle.rows[-1].signature

    def test_build_orders_out_of_order_events(self) -> None:
        events = list(reversed(_ledger_events()))
        bundle, _ = _build(events)
        assert [r.sequence_no for r in bundle.rows[:3]] == [1, 2, 3]

    def test_non_ledger_event_types_excluded(self) -> None:
        events = _ledger_events()
        # model_delta / tool_result internals are NOT the accountability record.
        events.append(_FakeEvent("model_delta", 4, {"delta": "hi"}))
        events.append(_FakeEvent("tool_result", 5, {"output": "x"}))
        bundle, _ = _build(events)
        row_types = {r.event_type for r in bundle.rows}
        assert "model_delta" not in row_types
        assert "tool_result" not in row_types
        # 3 ledger rows survive + the synthetic row.
        assert len(bundle.rows) == 4

    def test_final_row_covers_receipt_fold(self) -> None:
        events = _ledger_events()
        bundle, _ = _build(events)
        synthetic = bundle.rows[-1]
        assert synthetic.event_type == "receipt.export"
        assert synthetic.seq == 4
        # sequence_no = highest folded event's sequence_no + 1.
        assert synthetic.sequence_no == 4
        assert synthetic.payload == bundle.receipt.model_dump(mode="json")

    def test_empty_run_yields_single_synthetic_row(self) -> None:
        bundle, _ = _build([])
        assert len(bundle.rows) == 1
        synthetic = bundle.rows[0]
        assert synthetic.event_type == "receipt.export"
        assert synthetic.seq == 1
        # Zero ledger rows ⇒ synthetic sequence_no defaults to 1.
        assert synthetic.sequence_no == 1
        assert synthetic.prev_hash is None


class TestVerifyRoundtrip:
    def test_verify_roundtrip_ok(self) -> None:
        bundle, signer = _build(_ledger_events())
        result = ReceiptExportVerifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is True
        assert result.broken_at_seq is None

    def test_empty_run_verify_ok(self) -> None:
        bundle, signer = _build([])
        result = ReceiptExportVerifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is True


class TestTamperDetection:
    def test_flipped_byte_in_row_payload_fails_verification(self) -> None:
        bundle, signer = _build(_ledger_events())
        wire = bundle.model_dump(mode="json")
        # Mutate one character in the first row's payload.
        wire["rows"][0]["payload"]["op"] = "get_issue_TAMPERED"
        result = ReceiptExportVerifier(signer=signer).verify(wire)
        assert result.ok is False
        assert result.broken_at_seq == 1

    def test_flipped_byte_in_receipt_fails_verification(self) -> None:
        bundle, signer = _build(_ledger_events())
        wire = bundle.model_dump(mode="json")
        synthetic = wire["rows"][-1]
        # Tamper the sealed receipt fold carried on the synthetic row.
        synthetic["payload"]["tiles"]["reads_auto_ran"] = 999
        result = ReceiptExportVerifier(signer=signer).verify(wire)
        assert result.ok is False
        assert result.broken_at_seq == synthetic["seq"]

    def test_reordered_rows_fail_verification(self) -> None:
        bundle, signer = _build(_ledger_events())
        wire = bundle.model_dump(mode="json")
        wire["rows"][0], wire["rows"][1] = wire["rows"][1], wire["rows"][0]
        result = ReceiptExportVerifier(signer=signer).verify(wire)
        assert result.ok is False

    def test_dropped_row_fails_verification(self) -> None:
        bundle, signer = _build(_ledger_events())
        wire = bundle.model_dump(mode="json")
        # Drop the middle row — the chain linkage (prev_hash) breaks.
        del wire["rows"][1]
        result = ReceiptExportVerifier(signer=signer).verify(wire)
        assert result.ok is False

    def test_flipped_signature_byte_fails_verification(self) -> None:
        bundle, signer = _build(_ledger_events())
        wire = bundle.model_dump(mode="json")
        sig = wire["rows"][2]["signature"]
        flipped = ("f" if sig[0] != "f" else "0") + sig[1:]
        wire["rows"][2]["signature"] = flipped
        result = ReceiptExportVerifier(signer=signer).verify(wire)
        assert result.ok is False
        assert result.broken_at_seq == 3

    def test_forged_signature_with_wrong_key_fails(self) -> None:
        bundle, _ = _build(_ledger_events(), signer=_signer(key=_KEY_V1))
        # A second signer with the SAME version but a different key.
        other = _signer(key=_KEY_V2)
        result = ReceiptExportVerifier(signer=other).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is False
        assert result.broken_at_seq == 1

    def test_unknown_key_version_fails(self) -> None:
        bundle, _ = _build(_ledger_events(), signer=_signer(version=1))
        # A verifier that only knows version 2 cannot look up the v1 rows.
        other = _signer(version=2, key=_KEY_V2)
        result = ReceiptExportVerifier(signer=other).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is False


class TestKeyRotation:
    def test_key_rotation_verifies_old_rows(self) -> None:
        # Sign with v1, then verify with a rotated signer holding BOTH keys.
        bundle, _ = _build(_ledger_events(), signer=_signer(version=1, key=_KEY_V1))
        rotated = AuditChainSigner(keys={1: _KEY_V1, 2: _KEY_V2}, active_version=2)
        result = ReceiptExportVerifier(signer=rotated).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is True
        assert all(row.key_version == 1 for row in bundle.rows)


class TestDeterminism:
    def test_refold_yields_byte_identical_bundle(self) -> None:
        events = _ledger_events()
        first, signer = _build(events)
        second = ReceiptExportBuilder(signer=signer).build(
            run_id=_RUN_ID, events=copy.deepcopy(events), receipt=_receipt(events)
        )
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_receipt_with_rows_seals_correctly(self) -> None:
        # A hand-built receipt with rows still round-trips through the chain.
        receipt = RunReceipt(
            run_id=_RUN_ID,
            surface_id=f"receipt://{_RUN_ID}",
            fold_ref=f"ledger://{_RUN_ID}@3",
            generated_at="2026-01-01T00:00:03+00:00",
            tiles=RunReceiptTiles(
                reads_auto_ran=1,
                writes_proposed=0,
                writes_approved=0,
                holds_untouched=0,
            ),
            rows=(
                RunReceiptRow(
                    ledger_id="r7f3abed0c1·001",
                    event_type=LedgerEventType.READ_EXECUTED,
                    title="linear · get_issue",
                    attribution=ReceiptAttribution.AUTO_RAN,
                    at="2026-01-01T00:00:01+00:00",
                ),
            ),
        )
        signer = _signer()
        bundle = ReceiptExportBuilder(signer=signer).build(
            run_id=_RUN_ID, events=_ledger_events(), receipt=receipt
        )
        result = ReceiptExportVerifier(signer=signer).verify(
            bundle.model_dump(mode="json")
        )
        assert result.ok is True
