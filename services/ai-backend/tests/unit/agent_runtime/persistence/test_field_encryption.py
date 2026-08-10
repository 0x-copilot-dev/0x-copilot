"""C7 unit tests: AAD round-trip, KMS unavailability, AAD-swap rejection."""

from __future__ import annotations

import secrets

import pytest

from agent_runtime.persistence.encryption import (
    CiphertextDecodeError,
    EncryptionUnavailableError,
    EnvelopeFieldEncryption,
    FieldEncryptionFactory,
    NullFieldEncryption,
    _DekCache,
)


class _FakeKms:
    """Stand-in for AWS KMS that wraps DEKs with a static XOR key."""

    _STATIC_WRAP_KEY = bytes.fromhex("a5" * 32)

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self._fail_after = fail_after

    def _maybe_fail(self) -> None:
        if self._fail_after is not None and self.calls >= self._fail_after:
            raise RuntimeError("KMS unavailable (fake)")
        self.calls += 1

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        self._maybe_fail()
        wrapped = bytes(a ^ b for a, b in zip(plaintext_dek, self._STATIC_WRAP_KEY))
        return wrapped, "alias/test-cmk"

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        self._maybe_fail()
        return bytes(a ^ b for a, b in zip(wrapped_dek, self._STATIC_WRAP_KEY))


class TestNullFieldEncryption:
    def test_passthrough_round_trip(self) -> None:
        adapter = NullFieldEncryption()
        plaintext = b'{"hello":"world"}'
        ciphertext = adapter.encrypt(plaintext, table="t", column="c", org_id="org_a")
        assert ciphertext == plaintext.decode("utf-8")
        assert (
            adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")
            == plaintext
        )

    def test_refuses_v1_envelope(self) -> None:
        adapter = NullFieldEncryption()
        with pytest.raises(CiphertextDecodeError):
            adapter.decrypt("v1:a:b:c", table="t", column="c", org_id="org_a")


class TestEnvelopeRoundTrip:
    def test_round_trip(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        plaintext = b"sensitive payload"
        ciphertext = adapter.encrypt(
            plaintext, table="agent_messages", column="content_text", org_id="org_a"
        )
        assert ciphertext.startswith("v1:")
        assert (
            adapter.decrypt(
                ciphertext,
                table="agent_messages",
                column="content_text",
                org_id="org_a",
            )
            == plaintext
        )

    def test_each_encrypt_uses_fresh_dek(self) -> None:
        """Two encrypts of the same plaintext must produce different ciphertexts."""

        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        ct1 = adapter.encrypt(b"x", table="t", column="c", org_id="org_a")
        ct2 = adapter.encrypt(b"x", table="t", column="c", org_id="org_a")
        assert ct1 != ct2

    def test_large_payload_round_trip(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        plaintext = secrets.token_bytes(64 * 1024)
        ciphertext = adapter.encrypt(plaintext, table="t", column="c", org_id="org_a")
        assert (
            adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")
            == plaintext
        )


class TestAadBinding:
    """AAD binds (table, column, org_id) — swapped reads must fail."""

    def test_cross_column_swap_rejected(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        ciphertext = adapter.encrypt(
            b"secret", table="agent_messages", column="content_text", org_id="org_a"
        )
        with pytest.raises(CiphertextDecodeError):
            adapter.decrypt(
                ciphertext,
                table="runtime_audit_log",
                column="metadata_json_redacted",
                org_id="org_a",
            )

    def test_cross_tenant_swap_rejected(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        ciphertext = adapter.encrypt(b"secret", table="t", column="c", org_id="org_a")
        with pytest.raises(CiphertextDecodeError):
            adapter.decrypt(ciphertext, table="t", column="c", org_id="org_b")

    def test_same_aad_succeeds(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        ciphertext = adapter.encrypt(b"secret", table="t", column="c", org_id="org_a")
        assert (
            adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")
            == b"secret"
        )


class TestKmsFailures:
    def test_encrypt_fails_when_kms_unavailable(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms(fail_after=0))
        with pytest.raises(EncryptionUnavailableError):
            adapter.encrypt(b"x", table="t", column="c", org_id="org_a")

    def test_decrypt_fails_when_kms_unavailable(self) -> None:
        # Wedge KMS only after first encrypt.
        kms = _FakeKms()
        adapter = EnvelopeFieldEncryption(kms_client=kms)
        ciphertext = adapter.encrypt(b"x", table="t", column="c", org_id="org_a")
        kms._fail_after = 0  # type: ignore[attr-defined]
        # Bypass the cache for this test so the KMS unwrap is invoked again.
        adapter._cache.clear()
        with pytest.raises(EncryptionUnavailableError):
            adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")


class TestEnvelopeFormat:
    def test_parse_rejects_garbage(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        with pytest.raises(CiphertextDecodeError):
            adapter.decrypt("garbage-no-prefix", table="t", column="c", org_id="org_a")
        with pytest.raises(CiphertextDecodeError):
            adapter.decrypt("v1:onehalf", table="t", column="c", org_id="org_a")

    def test_envelope_url_safe_base64(self) -> None:
        adapter = EnvelopeFieldEncryption(kms_client=_FakeKms())
        ciphertext = adapter.encrypt(
            b"\xff" * 256, table="t", column="c", org_id="org_a"
        )
        # base64 standard would emit '+' and '/'; urlsafe substitutes '-' / '_'.
        assert "+" not in ciphertext
        assert "/" not in ciphertext


class TestDekCache:
    def test_cache_hit_skips_kms(self) -> None:
        kms = _FakeKms()
        adapter = EnvelopeFieldEncryption(kms_client=kms, dek_cache_ttl=300)
        ciphertext = adapter.encrypt(b"x", table="t", column="c", org_id="org_a")
        adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")
        before = kms.calls
        adapter.decrypt(ciphertext, table="t", column="c", org_id="org_a")
        # Second decrypt of the same row uses the cached DEK; no extra KMS hit.
        assert kms.calls == before

    def test_cache_ttl_expiry(self) -> None:
        clock = [0.0]
        cache = _DekCache(ttl_seconds=10, max_entries=10, clock=lambda: clock[0])
        cache.put(b"wrapped", b"\x01" * 32)
        assert cache.get(b"wrapped") == b"\x01" * 32
        clock[0] = 100
        assert cache.get(b"wrapped") is None


class TestFactory:
    def test_disabled_returns_null_adapter(self) -> None:
        adapter = FieldEncryptionFactory.from_env(
            environ={"RUNTIME_FIELD_ENCRYPTION": "disabled"}
        )
        assert isinstance(adapter, NullFieldEncryption)

    def test_default_is_disabled(self) -> None:
        adapter = FieldEncryptionFactory.from_env(environ={})
        assert isinstance(adapter, NullFieldEncryption)

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(RuntimeError, match="Unknown RUNTIME_FIELD_ENCRYPTION"):
            FieldEncryptionFactory.from_env(environ={"RUNTIME_FIELD_ENCRYPTION": "wat"})

    def test_envelope_v1_requires_kms_backend(self) -> None:
        with pytest.raises(RuntimeError, match="Unsupported RUNTIME_KMS_BACKEND"):
            FieldEncryptionFactory.from_env(
                environ={"RUNTIME_FIELD_ENCRYPTION": "envelope_v1"}
            )

    def test_envelope_v1_requires_key_id(self) -> None:
        with pytest.raises(RuntimeError, match="RUNTIME_KMS_KEY_ID is required"):
            FieldEncryptionFactory.from_env(
                environ={
                    "RUNTIME_FIELD_ENCRYPTION": "envelope_v1",
                    "RUNTIME_KMS_BACKEND": "aws_kms",
                }
            )
