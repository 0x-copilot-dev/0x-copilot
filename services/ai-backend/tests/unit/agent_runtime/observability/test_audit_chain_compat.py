"""Signature-compatibility fixture between legacy and shared audit chain.

The legacy implementation lived at ``agent_runtime/observability/audit_chain.py``
and was deleted when ``packages/audit-chain`` (``enterprise_audit_chain``)
became the single home for the HMAC chain primitive.

Pre-existing rows in production were signed by the legacy implementation.
The shared package's signer must produce byte-identical signatures for the
same inputs so historical rows continue to verify.

The hex strings below are signatures captured from the legacy
``AuditChainSigner.from_env(fail_closed=False)`` (i.e. the dev sentinel
key) before deletion. Any drift here means the canonical-signing form has
changed and historical chains will fail verification — treat that as a
breaking change to the package's major version.
"""

from __future__ import annotations

from datetime import datetime, timezone

from enterprise_audit_chain import AuditChainRow, AuditChainSigner


_DEV_SENTINEL_KEY = b"dev-audit-hmac-sentinel-key-32by"  # legacy value, byte-identical


def _sentinel_signer() -> AuditChainSigner:
    return AuditChainSigner(keys={0: _DEV_SENTINEL_KEY}, active_version=0)


class TestLegacySignatureCompat:
    def test_first_row_signature_matches_legacy_capture(self) -> None:
        signer = _sentinel_signer()
        payload = {
            "audit_id": "a1",
            "org_id": "org_test",
            "user_id": "u1",
            "actor_type": "user",
            "action": "conversation_created",
            "resource_type": "conversation",
            "resource_id": "conv_1",
            "run_id": None,
            "trace_id": None,
            "outcome": "success",
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "__event_type__": "conversation_created",
        }
        sig = signer.sign(prev_hash=None, payload=payload)
        assert (
            sig.signature.hex()
            == "75192172345e07e49f451fcfcc3b45f1b20ca4b1347b6cb0b71ee4854db011fe"
        )
        assert sig.key_version == 0

    def test_second_row_chained_signature_matches_legacy_capture(self) -> None:
        signer = _sentinel_signer()
        prev_hash = bytes.fromhex(
            "75192172345e07e49f451fcfcc3b45f1b20ca4b1347b6cb0b71ee4854db011fe"
        )
        payload = {
            "audit_id": "a2",
            "org_id": "org_test",
            "user_id": "u1",
            "actor_type": "worker",
            "action": "run_started",
            "resource_type": "agent_run",
            "resource_id": "run_1",
            "run_id": "run_1",
            "trace_id": "trace_1",
            "outcome": "success",
            "metadata": {"conversation_id": "conv_1"},
            "created_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            "__event_type__": "run_started",
        }
        sig = signer.sign(prev_hash=prev_hash, payload=payload)
        assert (
            sig.signature.hex()
            == "cd94f9b4a76f980810a34bc1c88f5b4660ad1d0ebe4d8594dd937146d31edc56"
        )

    def test_legacy_chain_verifies_under_shared_signer(self) -> None:
        signer = _sentinel_signer()
        payload1 = {
            "audit_id": "a1",
            "org_id": "org_test",
            "user_id": "u1",
            "actor_type": "user",
            "action": "conversation_created",
            "resource_type": "conversation",
            "resource_id": "conv_1",
            "run_id": None,
            "trace_id": None,
            "outcome": "success",
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "__event_type__": "conversation_created",
        }
        payload2 = {
            "audit_id": "a2",
            "org_id": "org_test",
            "user_id": "u1",
            "actor_type": "worker",
            "action": "run_started",
            "resource_type": "agent_run",
            "resource_id": "run_1",
            "run_id": "run_1",
            "trace_id": "trace_1",
            "outcome": "success",
            "metadata": {"conversation_id": "conv_1"},
            "created_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            "__event_type__": "run_started",
        }
        rows = [
            AuditChainRow(
                seq=1,
                payload=payload1,
                prev_hash=None,
                signature=bytes.fromhex(
                    "75192172345e07e49f451fcfcc3b45f1b20ca4b1347b6cb0b71ee4854db011fe"
                ),
                key_version=0,
            ),
            AuditChainRow(
                seq=2,
                payload=payload2,
                prev_hash=bytes.fromhex(
                    "75192172345e07e49f451fcfcc3b45f1b20ca4b1347b6cb0b71ee4854db011fe"
                ),
                signature=bytes.fromhex(
                    "cd94f9b4a76f980810a34bc1c88f5b4660ad1d0ebe4d8594dd937146d31edc56"
                ),
                key_version=0,
            ),
        ]
        result = signer.verify_chain(rows)
        assert result.ok is True
