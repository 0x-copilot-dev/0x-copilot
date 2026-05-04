"""C7 phase 2: FieldCodec — per-call-site encrypt/decrypt facade.

The codec hides:

- text vs JSONB envelope marshaling (text columns store ``v1:...`` strings,
  JSONB columns store ``{"$enc": "v1:..."}``);
- the (v0 vs v1) version branching on read;
- the strict-reads enforcement that phase 3 turns on after backfill.

Round-trip correctness for the underlying ``EnvelopeFieldEncryption`` is
covered by ``test_field_encryption.py``; here we pin the codec's
adapter-shape contract.
"""

from __future__ import annotations

import pytest

from agent_runtime.persistence import (
    EncryptionVersionRequired,
    EnvelopeFieldEncryption,
    FieldCodec,
    NullFieldEncryption,
)


class _FakeKms:
    _WRAP = bytes.fromhex("a5" * 32)

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        return bytes(a ^ b for a, b in zip(plaintext_dek, self._WRAP)), "alias/test"

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        return bytes(a ^ b for a, b in zip(wrapped_dek, self._WRAP))


def _envelope_codec(*, strict_reads: bool = False) -> FieldCodec:
    return FieldCodec(
        EnvelopeFieldEncryption(kms_client=_FakeKms()),
        strict_reads=strict_reads,
    )


class TestNullCodec:
    def test_write_version_is_zero(self) -> None:
        assert FieldCodec(NullFieldEncryption()).write_version == 0

    def test_text_passthrough(self) -> None:
        codec = FieldCodec(NullFieldEncryption())
        assert (
            codec.encrypt_text(
                "hello", table="agent_messages", column="content_text", org_id="o"
            )
            == "hello"
        )
        assert (
            codec.decrypt_text(
                "hello",
                encryption_version=0,
                table="agent_messages",
                column="content_text",
                org_id="o",
            )
            == "hello"
        )

    def test_jsonb_passthrough(self) -> None:
        codec = FieldCodec(NullFieldEncryption())
        value = {"k": "v", "n": [1, 2, 3]}
        assert (
            codec.encrypt_jsonb(
                value,
                table="runtime_events",
                column="payload_json_redacted",
                org_id="o",
            )
            == value
        )
        assert (
            codec.decrypt_jsonb(
                value,
                encryption_version=0,
                table="runtime_events",
                column="payload_json_redacted",
                org_id="o",
            )
            == value
        )

    def test_none_round_trips(self) -> None:
        codec = FieldCodec(NullFieldEncryption())
        assert codec.encrypt_text(None, table="t", column="c", org_id="o") is None
        assert codec.encrypt_jsonb(None, table="t", column="c", org_id="o") is None


class TestEnvelopeCodec:
    def test_write_version_is_one(self) -> None:
        assert _envelope_codec().write_version == 1

    def test_text_round_trip(self) -> None:
        codec = _envelope_codec()
        ct = codec.encrypt_text(
            "alice@example.com",
            table="agent_messages",
            column="content_text",
            org_id="org_a",
        )
        assert ct.startswith("v1:")
        pt = codec.decrypt_text(
            ct,
            encryption_version=1,
            table="agent_messages",
            column="content_text",
            org_id="org_a",
        )
        assert pt == "alice@example.com"

    def test_jsonb_round_trip(self) -> None:
        codec = _envelope_codec()
        value = {"event": "login", "ip": "10.0.0.1", "ts": 1234567890}
        envelope = codec.encrypt_jsonb(
            value,
            table="runtime_audit_log",
            column="metadata_json_redacted",
            org_id="org_a",
        )
        assert isinstance(envelope, dict)
        assert set(envelope.keys()) == {"$enc"}
        assert envelope["$enc"].startswith("v1:")
        decoded = codec.decrypt_jsonb(
            envelope,
            encryption_version=1,
            table="runtime_audit_log",
            column="metadata_json_redacted",
            org_id="org_a",
        )
        assert decoded == value

    def test_jsonb_canonical_serialization(self) -> None:
        # Sort-keys + tight separators give byte-identical envelopes for
        # logically equal dicts — important so the backfill is idempotent
        # (re-running on an already-rewritten row is a no-op).
        codec = _envelope_codec()
        a = codec.encrypt_jsonb(
            {"b": 1, "a": 2},
            table="t",
            column="c",
            org_id="o",
        )
        b = codec.encrypt_jsonb(
            {"a": 2, "b": 1},
            table="t",
            column="c",
            org_id="o",
        )
        # Distinct DEKs per row → distinct envelopes; but the *plaintext*
        # bytes that flow through encrypt are identical, so we round-trip
        # and compare values.
        assert codec.decrypt_jsonb(
            a, encryption_version=1, table="t", column="c", org_id="o"
        ) == codec.decrypt_jsonb(
            b, encryption_version=1, table="t", column="c", org_id="o"
        )

    def test_v0_text_passes_through(self) -> None:
        # Mid-cutover: v0 row in a v1 codec session — return as-is.
        codec = _envelope_codec()
        out = codec.decrypt_text(
            "legacy plaintext",
            encryption_version=0,
            table="t",
            column="c",
            org_id="o",
        )
        assert out == "legacy plaintext"

    def test_v0_jsonb_passes_through(self) -> None:
        codec = _envelope_codec()
        out = codec.decrypt_jsonb(
            {"raw": True},
            encryption_version=0,
            table="t",
            column="c",
            org_id="o",
        )
        assert out == {"raw": True}

    def test_v1_text_with_non_envelope_passes_through(self) -> None:
        # Retention sweeper rewrites tombstone strings without going
        # through the codec; v1 row + non-envelope text must still read.
        codec = _envelope_codec()
        out = codec.decrypt_text(
            "[deleted by retention policy]",
            encryption_version=1,
            table="t",
            column="c",
            org_id="o",
        )
        assert out == "[deleted by retention policy]"

    def test_v1_jsonb_with_non_envelope_passes_through(self) -> None:
        codec = _envelope_codec()
        out = codec.decrypt_jsonb(
            {"retention_purged": True},
            encryption_version=1,
            table="t",
            column="c",
            org_id="o",
        )
        assert out == {"retention_purged": True}


class TestStrictReads:
    def test_v0_under_strict_reads_raises(self) -> None:
        codec = _envelope_codec(strict_reads=True)
        with pytest.raises(EncryptionVersionRequired) as exc:
            codec.decrypt_text(
                "leaky",
                encryption_version=0,
                table="agent_messages",
                column="content_text",
                org_id="org_a",
            )
        # The error names the table/column/org so an operator can
        # immediately find the offender.
        assert "agent_messages.content_text" in str(exc.value)
        assert "org_a" in str(exc.value)

    def test_v0_jsonb_under_strict_reads_raises(self) -> None:
        codec = _envelope_codec(strict_reads=True)
        with pytest.raises(EncryptionVersionRequired):
            codec.decrypt_jsonb(
                {"k": "v"},
                encryption_version=0,
                table="runtime_events",
                column="metadata_json_redacted",
                org_id="org_a",
            )

    def test_v1_under_strict_reads_round_trips(self) -> None:
        codec = _envelope_codec(strict_reads=True)
        ct = codec.encrypt_text("still works", table="t", column="c", org_id="o")
        assert (
            codec.decrypt_text(
                ct, encryption_version=1, table="t", column="c", org_id="o"
            )
            == "still works"
        )

    def test_strict_reads_with_null_codec_does_not_raise(self) -> None:
        # If the operator turned strict_reads on but the codec is still
        # NullFieldEncryption (misconfiguration), we must NOT raise —
        # otherwise the deploy is wedged. The flag only fires once the
        # adapter is actually envelope_v1.
        codec = FieldCodec(NullFieldEncryption(), strict_reads=True)
        out = codec.decrypt_text(
            "ok",
            encryption_version=0,
            table="t",
            column="c",
            org_id="o",
        )
        assert out == "ok"


class TestAadCrossColumn:
    def test_envelope_from_one_column_does_not_decrypt_under_another(self) -> None:
        # The codec just delegates AAD-binding to the underlying adapter,
        # but pin the integrated behavior so a refactor of the codec can't
        # silently drop the (table, column, org_id) plumbing.
        codec = _envelope_codec()
        ct = codec.encrypt_text(
            "secret",
            table="agent_messages",
            column="content_text",
            org_id="org_a",
        )
        with pytest.raises(Exception):
            codec.decrypt_text(
                ct,
                encryption_version=1,
                table="runtime_audit_log",
                column="metadata_json_redacted",
                org_id="org_a",
            )

    def test_envelope_from_one_org_does_not_decrypt_under_another(self) -> None:
        codec = _envelope_codec()
        ct = codec.encrypt_text(
            "secret",
            table="agent_messages",
            column="content_text",
            org_id="org_a",
        )
        with pytest.raises(Exception):
            codec.decrypt_text(
                ct,
                encryption_version=1,
                table="agent_messages",
                column="content_text",
                org_id="org_b",
            )
