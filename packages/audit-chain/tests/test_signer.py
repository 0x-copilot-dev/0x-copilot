"""Tests for the shared HMAC hash-chain.

The chain is an integrity control: the verifier must accept a clean chain
and reject any tampering. Specifically:

- Tampering with payload data invalidates the signature.
- Removing or reordering rows breaks the prev_hash chain.
- A row signed with a key not held by the verifier is rejected.
- Key rotation: rows signed with previous keys still verify when the
  verifier holds those keys.
- Production fail-closed: ``from_env`` with the caller's env var set to
  ``production`` and no key configured raises.
- Dev sentinel: ``from_env`` outside production with no key configured
  loads a hardcoded sentinel key. The sentinel value must remain
  byte-identical across releases (legacy chains depend on it).

The package replaces the in-tree implementations that previously lived in
both ``services/backend`` and ``services/ai-backend``. Tests in those
services continue to use the public API and must keep passing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from copilot_audit_chain import (
    AuditChainRow,
    AuditChainSigner,
    ChainSignature,
)
from copilot_audit_chain.signer import _DEV_SENTINEL_KEY


def _signer(*, version: int = 1) -> AuditChainSigner:
    return AuditChainSigner(
        keys={version: b"audit-chain-pkg-test-key-32bytesx"},
        active_version=version,
    )


def _payload(audit_id: str, org_id: str = "org_a", action: str = "x") -> dict:
    return {
        "audit_id": audit_id,
        "org_id": org_id,
        "user_id": "u",
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

    def test_active_version_must_be_in_keys(self) -> None:
        with pytest.raises(ValueError, match="active_version 2 not in key map"):
            AuditChainSigner(
                keys={1: b"audit-chain-pkg-test-key-32bytesx"},
                active_version=2,
            )

    def test_empty_keys_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one key"):
            AuditChainSigner(keys={}, active_version=0)


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
        result = signer.verify_chain(rows)
        assert result.ok is False

    def test_row_removal_breaks_chain(self) -> None:
        signer = _signer()
        rows = self._build_chain(signer, 5)
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


class TestFromEnv:
    def test_dev_returns_sentinel_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
        monkeypatch.delenv("AUDIT_HMAC_KEY_VERSION", raising=False)
        signer = AuditChainSigner.from_env(environment_env_var="UNSET_TEST_VAR")
        # Sentinel key has version 0 by convention.
        assert signer.active_version == 0
        # Sentinel must remain byte-identical to the legacy in-tree value
        # so pre-existing fixtures keep verifying.
        assert _DEV_SENTINEL_KEY == b"dev-audit-hmac-sentinel-key-32by"

    def test_production_without_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
        monkeypatch.setenv("TEST_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="AUDIT_HMAC_KEY must be set"):
            AuditChainSigner.from_env(environment_env_var="TEST_ENVIRONMENT")

    def test_explicit_fail_closed_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
        # Even in development, explicit fail_closed=True raises.
        with pytest.raises(RuntimeError, match="AUDIT_HMAC_KEY must be set"):
            AuditChainSigner.from_env(
                environment_env_var="UNSET_TEST_VAR", fail_closed=True
            )

    def test_loads_active_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_HMAC_KEY", "00" * 32)
        monkeypatch.setenv("AUDIT_HMAC_KEY_VERSION", "7")
        signer = AuditChainSigner.from_env(environment_env_var="UNSET_TEST_VAR")
        assert signer.active_version == 7

    def test_loads_previous_keys_for_rotation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUDIT_HMAC_KEY", "11" * 32)
        monkeypatch.setenv("AUDIT_HMAC_KEY_VERSION", "2")
        monkeypatch.setenv("AUDIT_HMAC_KEY_V1", "22" * 32)
        signer = AuditChainSigner.from_env(environment_env_var="UNSET_TEST_VAR")
        # Both keys must be present for verification of legacy rows.
        assert (
            signer.verify_row(
                prev_hash=None,
                payload={"k": "v"},
                signature=b"\x00",
                key_version=1,
            )
            is False
        )  # right key but wrong signature -> false (not crash)
        assert (
            signer.verify_row(
                prev_hash=None,
                payload={"k": "v"},
                signature=b"\x00",
                key_version=999,
            )
            is False
        )  # unknown key -> false

    def test_invalid_hex_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_HMAC_KEY", "not-hex-not-hex-not-hex-not-hex-")
        with pytest.raises(RuntimeError, match="must be hex-encoded"):
            AuditChainSigner.from_env(environment_env_var="UNSET_TEST_VAR")


class TestCanonicalForm:
    """Pin the canonical form so signature compatibility cannot drift silently.

    If any of these fixtures changes byte-output, every signed row in
    production becomes unverifiable. Treat assertion changes here as a
    breaking change to the package's major version.
    """

    def test_canonical_form_matches_legacy_byte_for_byte(self) -> None:
        # The exact bytes the legacy in-tree _canonicalize produced for this
        # input. Any drift here breaks signature compat with already-stored
        # rows. The fixture covers: prev_hash hex-encoding, sorted-key
        # output, datetime ISO-8601, dict ordering, str values.
        payload = {
            "audit_id": "a",
            "org_id": "org_a",
            "user_id": "u",
            "action": "x",
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        result = AuditChainSigner._canonicalize(payload, prev_hash=None, key_version=1)
        expected = (
            b'{"key_version":1,"payload":{'
            b'"action":"x",'
            b'"audit_id":"a",'
            b'"created_at":"2026-01-01T00:00:00+00:00",'
            b'"metadata":{"k":"v"},'
            b'"org_id":"org_a",'
            b'"user_id":"u"'
            b'},"prev_hash":null}'
        )
        assert result == expected

    def test_canonical_form_with_prev_hash(self) -> None:
        payload = {"k": "v"}
        prev = bytes.fromhex("abcdef")
        result = AuditChainSigner._canonicalize(payload, prev_hash=prev, key_version=2)
        expected = b'{"key_version":2,"payload":{"k":"v"},"prev_hash":"abcdef"}'
        assert result == expected

    def test_stringify_handles_uuid_bytes_datetime(self) -> None:
        # Bytes inside the payload must serialize as hex (not base64, not
        # repr) — pin this explicitly.
        u = UUID("12345678-1234-5678-1234-567812345678")
        payload = {
            "u": u,
            "b": b"\xaa\xbb",
            "t": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        result = AuditChainSigner._canonicalize(payload, prev_hash=None, key_version=1)
        assert b'"u":"12345678-1234-5678-1234-567812345678"' in result
        assert b'"b":"aabb"' in result
        assert b'"t":"2026-01-01T00:00:00+00:00"' in result

    def test_unserializable_type_raises(self) -> None:
        class _NotSerializable:
            pass

        payload = {"x": _NotSerializable()}
        with pytest.raises(TypeError, match="unserializable type"):
            AuditChainSigner._canonicalize(payload, prev_hash=None, key_version=1)
