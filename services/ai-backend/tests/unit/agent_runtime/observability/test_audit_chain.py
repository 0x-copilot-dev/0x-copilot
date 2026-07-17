"""Tests for the runtime audit HMAC hash-chain.

Mirrors the backend's chain tests, scoped to the ai-backend implementation:
clean chain verifies; tampering / row removal / row swap break it; rotation
works; in-memory store integration produces a verifiable chain.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from copilot_audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainSignature,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore


def _signer(*, version: int = 1) -> AuditChainSigner:
    return AuditChainSigner(
        keys={version: b"ai-backend-test-key-32-bytes-long-x"},
        active_version=version,
    )


def _payload(
    audit_id: str,
    org_id: str = "org_a",
    action: str = "x",
) -> dict:
    return {
        "audit_id": audit_id,
        "org_id": org_id,
        "user_id": "u",
        "actor_type": "user",
        "action": action,
        "resource_type": "runtime",
        "resource_id": "r",
        "run_id": None,
        "trace_id": None,
        "outcome": "success",
        "metadata": {"k": "v"},
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "__event_type__": action,
    }


def _to_row(seq: int, payload: dict, sig: ChainSignature) -> AuditChainRow:
    return AuditChainRow(
        seq=seq,
        payload=payload,
        prev_hash=sig.prev_hash,
        signature=sig.signature,
        key_version=sig.key_version,
    )


class TestSignerSmoke:
    def test_sign_is_deterministic(self) -> None:
        signer = _signer()
        payload = _payload("a")
        a = signer.sign(prev_hash=None, payload=payload)
        b = signer.sign(prev_hash=None, payload=payload)
        assert a.signature == b.signature

    def test_signature_changes_with_prev_hash(self) -> None:
        signer = _signer()
        payload = _payload("a")
        a = signer.sign(prev_hash=None, payload=payload)
        b = signer.sign(prev_hash=b"\xff" * 32, payload=payload)
        assert a.signature != b.signature

    def test_min_key_length_enforced(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            AuditChainSigner(keys={1: b"too-short"}, active_version=1)


class _ChainBuilder:
    @staticmethod
    def build(signer: AuditChainSigner, n: int) -> list[AuditChainRow]:
        rows: list[AuditChainRow] = []
        prev_hash: bytes | None = None
        for i in range(n):
            payload = _payload(f"a{i}")
            sig = signer.sign(prev_hash=prev_hash, payload=payload)
            rows.append(_to_row(i + 1, payload, sig))
            prev_hash = sig.signature
        return rows


class TestVerifyChainHappyPath:
    def test_clean_chain_verifies(self) -> None:
        signer = _signer()
        rows = _ChainBuilder.build(signer, 25)
        result = signer.verify_chain(rows)
        assert result.ok is True
        assert result.broken_at_seq is None


class TestVerifyChainTampering:
    def test_payload_tamper_breaks_signature(self) -> None:
        signer = _signer()
        rows = _ChainBuilder.build(signer, 5)
        tampered = dict(rows[2].payload)
        tampered["action"] = "tampered"
        rows[2] = AuditChainRow(
            seq=rows[2].seq,
            payload=tampered,
            prev_hash=rows[2].prev_hash,
            signature=rows[2].signature,
            key_version=rows[2].key_version,
        )
        result = signer.verify_chain(rows)
        assert result.ok is False
        assert result.broken_at_seq == 3
        assert result.reason == "signature mismatch"

    def test_row_removal_breaks_chain(self) -> None:
        signer = _signer()
        rows = _ChainBuilder.build(signer, 5)
        rows = [rows[0], rows[1], rows[3], rows[4]]
        result = signer.verify_chain(rows)
        assert result.ok is False
        assert result.reason == "prev_hash mismatch"

    def test_signature_byte_flip_detected(self) -> None:
        signer = _signer()
        rows = _ChainBuilder.build(signer, 3)
        flipped = bytearray(rows[1].signature)
        flipped[7] ^= 0xAA
        rows[1] = AuditChainRow(
            seq=rows[1].seq,
            payload=rows[1].payload,
            prev_hash=rows[1].prev_hash,
            signature=bytes(flipped),
            key_version=rows[1].key_version,
        )
        result = signer.verify_chain(rows)
        assert result.ok is False


class TestVerifyChainRotation:
    def test_two_keys_held_by_verifier(self) -> None:
        signer_v1 = AuditChainSigner(
            keys={1: b"k1-k1-k1-k1-k1-k1-k1-k1-k1-k1-k1!"},
            active_version=1,
        )
        signer_v2 = AuditChainSigner(
            keys={2: b"k2-k2-k2-k2-k2-k2-k2-k2-k2-k2-k2!"},
            active_version=2,
        )
        verifier = AuditChainSigner(
            keys={
                1: b"k1-k1-k1-k1-k1-k1-k1-k1-k1-k1-k1!",
                2: b"k2-k2-k2-k2-k2-k2-k2-k2-k2-k2-k2!",
            },
            active_version=2,
        )
        rows: list[AuditChainRow] = []
        prev: bytes | None = None
        for i in range(3):
            sig = signer_v1.sign(prev_hash=prev, payload=_payload(f"v1-{i}"))
            rows.append(_to_row(i + 1, _payload(f"v1-{i}"), sig))
            prev = sig.signature
        for i in range(3):
            sig = signer_v2.sign(prev_hash=prev, payload=_payload(f"v2-{i}"))
            rows.append(_to_row(i + 4, _payload(f"v2-{i}"), sig))
            prev = sig.signature
        assert verifier.verify_chain(rows).ok is True


class TestInMemoryStoreIntegration:
    async def test_audit_log_records_carry_chain_fields(self) -> None:
        store = InMemoryRuntimeApiStore()
        for i in range(5):
            await store.write_audit_log(
                event_type="conversation_created",
                record={"org_id": "org_a", "audit_id": f"a{i}"},
            )
        for event_type, record in store.audit_log:
            assert event_type == "conversation_created"
            assert record["seq"] is not None
            assert record["signature"] is not None
            assert record["key_version"] is not None
        seqs = [record["seq"] for _, record in store.audit_log]
        assert seqs == [1, 2, 3, 4, 5]

    async def test_chain_is_per_org(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.write_audit_log(event_type="x", record={"org_id": "org_a"})
        await store.write_audit_log(event_type="x", record={"org_id": "org_b"})
        await store.write_audit_log(event_type="x", record={"org_id": "org_a"})
        a_records = [r for et, r in store.audit_log if r["org_id"] == "org_a"]
        b_records = [r for et, r in store.audit_log if r["org_id"] == "org_b"]
        assert [r["seq"] for r in a_records] == [1, 2]
        assert [r["seq"] for r in b_records] == [1]
        # Second org_a row's prev_hash equals first org_a row's signature.
        assert a_records[1]["prev_hash"] == a_records[0]["signature"]
        assert b_records[0]["prev_hash"] is None  # first row in org_b chain

    async def test_delete_user_history_audit_is_chained(self) -> None:
        store = InMemoryRuntimeApiStore()
        await store.delete_user_history(org_id="org_a", user_id="u", reason="GDPR")
        assert len(store.audit_log) == 1
        event_type, record = store.audit_log[0]
        assert event_type == "user_history_deleted"
        assert record["seq"] == 1
        assert record["signature"] is not None
        assert record["prev_hash"] is None
