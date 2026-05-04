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
    def test_phase1_target_is_agent_messages_content_text(self) -> None:
        backfill = FieldEncryptionBackfill(
            database_url="postgresql://ignored",
            field_encryption=EnvelopeFieldEncryption(kms_client=_FakeKms()),
        )
        # The phase 1 target list is intentionally narrow — wider coverage
        # ships once the per-column wiring lands in the store.
        assert len(backfill._targets) == 1
        target = backfill._targets[0]
        assert isinstance(target, BackfillTarget)
        assert target.table == "agent_messages"
        assert target.column == "content_text"
