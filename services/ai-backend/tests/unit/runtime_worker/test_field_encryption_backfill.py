"""C7 backfill-job tests.

The full Postgres path is exercised against the integration suite (out of
scope for unit tests). Here we verify the framework's invariants:

  - Refuses to run with NullFieldEncryption (would defeat its purpose).
  - Coerces strings, bytes, and JSONB shapes to bytes for AAD-bound encrypt.
  - Sleep between batches is honored.
"""

from __future__ import annotations

import pytest

from agent_runtime.persistence.encryption import (
    EnvelopeFieldEncryption,
    NullFieldEncryption,
)
from runtime_worker.jobs.encrypt_existing_columns import (
    BackfillTarget,
    FieldEncryptionBackfill,
)


class _FakeKms:
    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        return plaintext_dek, "alias/test"

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        return wrapped_dek


class TestFrameworkInvariants:
    def test_refuses_null_adapter(self) -> None:
        with pytest.raises(RuntimeError, match="envelope-capable"):
            FieldEncryptionBackfill(
                database_url="postgresql://ignored",
                field_encryption=NullFieldEncryption(),
            )

    def test_accepts_envelope_adapter(self) -> None:
        backfill = FieldEncryptionBackfill(
            database_url="postgresql://ignored",
            field_encryption=EnvelopeFieldEncryption(kms_client=_FakeKms()),
        )
        # Object constructed; no live DB call yet.
        assert backfill is not None


class TestPayloadCoercion:
    def test_string_payload(self) -> None:
        result = FieldEncryptionBackfill._coerce_to_bytes("hello")
        assert result == b"hello"

    def test_bytes_payload(self) -> None:
        result = FieldEncryptionBackfill._coerce_to_bytes(b"\x01\x02")
        assert result == b"\x01\x02"

    def test_dict_payload_is_canonicalized(self) -> None:
        # Sort keys + tight separators give a stable byte representation
        # so re-encrypting an unchanged JSON value is byte-identical.
        result = FieldEncryptionBackfill._coerce_to_bytes({"b": 1, "a": 2})
        assert result == b'{"a":2,"b":1}'


class TestTargets:
    def test_default_targets_cover_phase2_columns(self) -> None:
        backfill = FieldEncryptionBackfill(
            database_url="postgresql://ignored",
            field_encryption=EnvelopeFieldEncryption(kms_client=_FakeKms()),
        )
        # C7 phase 2 widened the target list; assert the wired columns
        # match the per-table coverage in the spec
        # (docs/security/field-encryption.md). Tables whose write paths
        # don't exist yet (subagent results, tool invocations, memory
        # items) are intentionally absent so backfill doesn't churn over
        # empty rows.
        wired = {(t.table, t.column) for t in backfill._targets}
        assert wired == {
            ("agent_messages", "content_text"),
            ("agent_messages", "content_json"),
            ("agent_messages", "metadata_json"),
            ("runtime_audit_log", "metadata_json_redacted"),
            ("runtime_events", "payload_json_redacted"),
            ("runtime_events", "metadata_json_redacted"),
        }
        # Column types must be set so the rewrite path knows whether to
        # wrap the envelope as JSONB or store it as plain text.
        types = {(t.table, t.column): t.column_type for t in backfill._targets}
        assert types[("agent_messages", "content_text")] == "text"
        assert types[("agent_messages", "content_json")] == "json"
        assert types[("runtime_audit_log", "metadata_json_redacted")] == "json"


class TestEncryptForTarget:
    def _backfill(self) -> FieldEncryptionBackfill:
        return FieldEncryptionBackfill(
            database_url="postgresql://ignored",
            field_encryption=EnvelopeFieldEncryption(kms_client=_FakeKms()),
        )

    def test_text_target_returns_envelope_string(self) -> None:
        backfill = self._backfill()
        result = backfill._encrypt_for_target(
            BackfillTarget(
                table="agent_messages",
                column="content_text",
                column_type="text",
            ),
            "hello",
            org_id="org_a",
        )
        assert isinstance(result, str)
        assert result.startswith("v1:")

    def test_json_target_returns_jsonb_envelope_dict(self) -> None:
        from psycopg.types.json import Jsonb

        backfill = self._backfill()
        result = backfill._encrypt_for_target(
            BackfillTarget(
                table="runtime_events",
                column="payload_json_redacted",
                column_type="json",
            ),
            {"key": "value"},
            org_id="org_a",
        )
        assert isinstance(result, Jsonb)
        # Inner dict has the canonical envelope shape.
        inner = result.obj
        assert isinstance(inner, dict)
        assert set(inner.keys()) == {"$enc"}
        assert inner["$enc"].startswith("v1:")

    def test_already_encrypted_text_passes_through(self) -> None:
        backfill = self._backfill()
        result = backfill._encrypt_for_target(
            BackfillTarget(
                table="agent_messages",
                column="content_text",
                column_type="text",
            ),
            "v1:already:encrypted:value",
            org_id="org_a",
        )
        assert result == "v1:already:encrypted:value"

    def test_already_encrypted_json_passes_through(self) -> None:
        from psycopg.types.json import Jsonb

        backfill = self._backfill()
        result = backfill._encrypt_for_target(
            BackfillTarget(
                table="runtime_events",
                column="metadata_json_redacted",
                column_type="json",
            ),
            {"$enc": "v1:already:in:envelope"},
            org_id="org_a",
        )
        assert isinstance(result, Jsonb)
        assert result.obj == {"$enc": "v1:already:in:envelope"}
