"""Signature-compatibility fixture between legacy and shared audit chain.

The legacy implementation lived at ``backend_app/audit_chain.py`` and was
deleted when ``packages/audit-chain`` (``copilot_audit_chain``) became
the single home for the HMAC chain primitive.

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

from copilot_audit_chain import AuditChainRow, AuditChainSigner


_DEV_SENTINEL_KEY = b"dev-audit-hmac-sentinel-key-32by"  # legacy value, byte-identical


def _sentinel_signer() -> AuditChainSigner:
    return AuditChainSigner(keys={0: _DEV_SENTINEL_KEY}, active_version=0)


class TestLegacySignatureCompat:
    def test_first_row_signature_matches_legacy_capture(self) -> None:
        signer = _sentinel_signer()
        payload = {
            "audit_id": "a1",
            "org_id": "org_test",
            "user_id": "u",
            "server_id": "s",
            "action": "mcp_server_created",
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        sig = signer.sign(prev_hash=None, payload=payload)
        assert (
            sig.signature.hex()
            == "db7d25ab7e95f60a8e1ead35f92ce64bc81988e35f21ec494771aabcd2a65fe3"
        )
        assert sig.key_version == 0

    def test_second_row_chained_signature_matches_legacy_capture(self) -> None:
        signer = _sentinel_signer()
        prev_hash = bytes.fromhex(
            "db7d25ab7e95f60a8e1ead35f92ce64bc81988e35f21ec494771aabcd2a65fe3"
        )
        payload = {
            "audit_id": "a2",
            "org_id": "org_test",
            "user_id": "u",
            "server_id": "s",
            "action": "mcp_server_deleted",
            "metadata": {},
            "created_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        }
        sig = signer.sign(prev_hash=prev_hash, payload=payload)
        assert (
            sig.signature.hex()
            == "71ccf408b5d11a3f3157abb6f41e3b990b5838130940f5729b6f2cf554e9d43e"
        )

    def test_legacy_chain_verifies_under_shared_signer(self) -> None:
        signer = _sentinel_signer()
        payload1 = {
            "audit_id": "a1",
            "org_id": "org_test",
            "user_id": "u",
            "server_id": "s",
            "action": "mcp_server_created",
            "metadata": {"k": "v"},
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        payload2 = {
            "audit_id": "a2",
            "org_id": "org_test",
            "user_id": "u",
            "server_id": "s",
            "action": "mcp_server_deleted",
            "metadata": {},
            "created_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        }
        rows = [
            AuditChainRow(
                seq=1,
                payload=payload1,
                prev_hash=None,
                signature=bytes.fromhex(
                    "db7d25ab7e95f60a8e1ead35f92ce64bc81988e35f21ec494771aabcd2a65fe3"
                ),
                key_version=0,
            ),
            AuditChainRow(
                seq=2,
                payload=payload2,
                prev_hash=bytes.fromhex(
                    "db7d25ab7e95f60a8e1ead35f92ce64bc81988e35f21ec494771aabcd2a65fe3"
                ),
                signature=bytes.fromhex(
                    "71ccf408b5d11a3f3157abb6f41e3b990b5838130940f5729b6f2cf554e9d43e"
                ),
                key_version=0,
            ),
        ]
        result = signer.verify_chain(rows)
        assert result.ok is True
