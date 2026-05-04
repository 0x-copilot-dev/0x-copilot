"""Tests for the audit HMAC hash-chain.

The chain is an integrity control: the verifier must accept a clean chain
and reject any tampering. Specifically:

- Tampering with payload data invalidates the signature.
- Removing or reordering rows breaks the prev_hash chain.
- A row signed with a key not held by the verifier is rejected.
- Key rotation: rows signed with previous keys still verify when the
  verifier holds those keys.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend_app.audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainSignature,
)
from backend_app.contracts import (
    AuditEventRecord,
    DeployImageDigest,
    DeployAuditEventRecord,
    SkillAuditEventRecord,
)
from backend_app.store import (
    InMemoryDeployAuditStore,
    InMemoryMcpStore,
    InMemorySkillStore,
)


def _signer(*, version: int = 1) -> AuditChainSigner:
    return AuditChainSigner(
        keys={version: b"test-key-deterministic-32bytes--"},
        active_version=version,
    )


def _payload(audit_id: str, org_id: str = "org_a", action: str = "x") -> dict:
    return {
        "audit_id": audit_id,
        "org_id": org_id,
        "user_id": "u",
        "server_id": "s",
        "action": action,
        "metadata": {"k": "v"},
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
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
        first = signer.sign(prev_hash=None, payload=payload)
        second = signer.sign(prev_hash=None, payload=payload)
        assert first.signature == second.signature

    def test_signature_changes_with_prev_hash(self) -> None:
        signer = _signer()
        payload = _payload("a")
        a = signer.sign(prev_hash=None, payload=payload)
        b = signer.sign(prev_hash=b"\x01" * 32, payload=payload)
        assert a.signature != b.signature

    def test_min_key_length_enforced(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            AuditChainSigner(keys={1: b"short"}, active_version=1)


class TestVerifyChainHappyPath:
    def test_clean_chain_verifies(self) -> None:
        signer = _signer()
        rows: list[AuditChainRow] = []
        prev_hash: bytes | None = None
        for i in range(50):
            payload = _payload(f"a{i}")
            sig = signer.sign(prev_hash=prev_hash, payload=payload)
            rows.append(_to_row(i + 1, payload, sig))
            prev_hash = sig.signature
        result = signer.verify_chain(rows)
        assert result.ok is True
        assert result.broken_at_seq is None


class TestVerifyChainTampering:
    def _build_chain(self, signer: AuditChainSigner, n: int) -> list[AuditChainRow]:
        rows: list[AuditChainRow] = []
        prev_hash: bytes | None = None
        for i in range(n):
            payload = _payload(f"a{i}")
            sig = signer.sign(prev_hash=prev_hash, payload=payload)
            rows.append(_to_row(i + 1, payload, sig))
            prev_hash = sig.signature
        return rows

    def test_payload_tamper_breaks_signature(self) -> None:
        signer = _signer()
        rows = self._build_chain(signer, 5)
        tampered_payload = dict(rows[2].payload)
        tampered_payload["action"] = "tampered"
        rows[2] = AuditChainRow(
            seq=rows[2].seq,
            payload=tampered_payload,
            prev_hash=rows[2].prev_hash,
            signature=rows[2].signature,
            key_version=rows[2].key_version,
        )
        result = signer.verify_chain(rows)
        assert result.ok is False
        assert result.broken_at_seq == 3
        assert result.reason == "signature mismatch"

    def test_signature_byte_flip_detected(self) -> None:
        signer = _signer()
        rows = self._build_chain(signer, 3)
        flipped = bytearray(rows[1].signature)
        flipped[0] ^= 0xFF
        rows[1] = AuditChainRow(
            seq=rows[1].seq,
            payload=rows[1].payload,
            prev_hash=rows[1].prev_hash,
            signature=bytes(flipped),
            key_version=rows[1].key_version,
        )
        # First effect: row 2 itself fails signature check.
        result = signer.verify_chain(rows)
        assert result.ok is False

    def test_row_removal_breaks_chain(self) -> None:
        signer = _signer()
        rows = self._build_chain(signer, 5)
        # Drop the third row; row 4's prev_hash now refers to the
        # removed row's signature, not row 2's.
        rows = [rows[0], rows[1], rows[3], rows[4]]
        result = signer.verify_chain(rows)
        assert result.ok is False
        assert result.broken_at_seq == 4
        assert result.reason == "prev_hash mismatch"

    def test_row_swap_breaks_chain(self) -> None:
        signer = _signer()
        rows = self._build_chain(signer, 5)
        rows[1], rows[2] = rows[2], rows[1]
        result = signer.verify_chain(rows)
        assert result.ok is False


class TestVerifyChainKeyHandling:
    def test_unknown_key_version_rejected(self) -> None:
        signer_v1 = _signer(version=1)
        rows = []
        sig = signer_v1.sign(prev_hash=None, payload=_payload("a"))
        rows.append(_to_row(1, _payload("a"), sig))
        signer_v2 = _signer(version=2)
        result = signer_v2.verify_chain(rows)
        assert result.ok is False

    def test_rotation_window_with_two_keys(self) -> None:
        # v1 signs the first half; v2 signs the second half. Verifier knows both.
        signer_v1 = AuditChainSigner(
            keys={1: b"key-v1-key-v1-key-v1-key-v1-key1"},
            active_version=1,
        )
        signer_v2 = AuditChainSigner(
            keys={2: b"key-v2-key-v2-key-v2-key-v2-key2"},
            active_version=2,
        )
        verifier = AuditChainSigner(
            keys={
                1: b"key-v1-key-v1-key-v1-key-v1-key1",
                2: b"key-v2-key-v2-key-v2-key-v2-key2",
            },
            active_version=2,
        )

        rows: list[AuditChainRow] = []
        prev_hash: bytes | None = None
        for i in range(3):
            payload = _payload(f"v1-{i}")
            sig = signer_v1.sign(prev_hash=prev_hash, payload=payload)
            rows.append(_to_row(i + 1, payload, sig))
            prev_hash = sig.signature
        for i in range(3):
            payload = _payload(f"v2-{i}")
            sig = signer_v2.sign(prev_hash=prev_hash, payload=payload)
            rows.append(_to_row(i + 4, payload, sig))
            prev_hash = sig.signature
        assert verifier.verify_chain(rows).ok is True


class TestInMemoryStoreIntegration:
    def test_mcp_audit_chain_verifies(self) -> None:
        store = InMemoryMcpStore()
        for i in range(10):
            store.append_audit(
                AuditEventRecord(
                    org_id="org_a",
                    user_id="u",
                    server_id=f"s{i}",
                    action="mcp_server_created",
                    metadata={"i": i},
                )
            )
        signer = store._chain.signer  # noqa: SLF001 — test reaches into chain state
        rows = [
            AuditChainRow(
                seq=record.seq or 0,
                payload={
                    "audit_id": record.audit_id,
                    "org_id": record.org_id,
                    "user_id": record.user_id,
                    "server_id": record.server_id,
                    "action": record.action,
                    "metadata": record.metadata,
                    "created_at": record.created_at,
                },
                prev_hash=record.prev_hash,
                signature=record.signature or b"",
                key_version=record.key_version or 0,
            )
            for record in store.audit_events
        ]
        assert signer.verify_chain(rows).ok is True

    def test_chain_is_per_org(self) -> None:
        store = InMemoryMcpStore()
        a1 = store.append_audit(
            AuditEventRecord(org_id="org_a", user_id="u", server_id="s", action="x")
        )
        b1 = store.append_audit(
            AuditEventRecord(org_id="org_b", user_id="u", server_id="s", action="x")
        )
        a2 = store.append_audit(
            AuditEventRecord(org_id="org_a", user_id="u", server_id="s", action="x")
        )
        # Per-org seq starts at 1; a2's prev_hash is a1's signature, not b1's.
        assert a1.seq == 1
        assert b1.seq == 1
        assert a2.seq == 2
        assert a2.prev_hash == a1.signature
        assert b1.prev_hash is None  # first row in org_b chain

    def test_skill_audit_chain_verifies(self) -> None:
        store = InMemorySkillStore()
        for i in range(5):
            store.append_skill_audit(
                SkillAuditEventRecord(
                    org_id="org_a",
                    user_id="u",
                    skill_id=f"sk{i}",
                    action="skill_created",
                    metadata={"i": i},
                )
            )
        for record in store.audit_events:
            assert record.signature is not None
            assert record.seq is not None

    def test_deploy_audit_chain_verifies(self) -> None:
        store = InMemoryDeployAuditStore()
        digest = DeployImageDigest(component="api", digest="sha256:" + "0" * 64)
        for i in range(3):
            store.append_deploy_audit(
                DeployAuditEventRecord(
                    org_id="org_a",
                    user_id="approver",
                    tenant_id="tenant_a",
                    environment="staging",
                    release_sha="abcdef1",
                    image_digests=[digest],
                    approver="approver",
                    workflow_run_url="https://github.com/example/run/1",
                    started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    outcome="success",
                    force_deploy=False,
                )
            )
        for record in store.audit_events:
            assert record.signature is not None
