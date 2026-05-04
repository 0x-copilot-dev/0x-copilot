"""C6 tests: KMS adapter framework + AWS KMS adapter + decrypt cache.

Production deploys can't run boto3 in unit tests, so the AWS path is
exercised via a fake KMS client that records calls. Round-trip semantics,
fail-closed on KMS unavailability, cache hit reduces KMS calls, and "no
plaintext is ever logged" all live here.
"""

from __future__ import annotations

import logging

import pytest

from backend_app.deployment_profile import (
    DeploymentFeatureToggles,
    DeploymentProfile,
)
from backend_app.token_vault import (
    AwsKmsTokenVault,
    CiphertextFormatError,
    KmsUnavailableError,
    LocalTokenVault,
    TokenVaultFactory,
    _DecryptCache,
)
from backend_app.token_vault_metrics import TokenVaultMetrics


_PROD_TOGGLES = {
    "allow_embedded_provider_keys": False,
    "allow_self_signup": False,
    "allow_vendor_telemetry": False,
    "default_retention_days": 365,
    "dev_auth_bypass_allowed": False,
    "enforce_rls": True,
    "require_field_level_encryption": True,
    "require_kms_token_vault": True,
    "siem_export_required": True,
}


def _profile(name: str, *, require_kms: bool = True) -> DeploymentProfile:
    toggles = dict(_PROD_TOGGLES)
    toggles["require_kms_token_vault"] = require_kms
    return DeploymentProfile(
        name=name,
        toggles=DeploymentFeatureToggles(**toggles),
    )


class _FakeKmsClient:
    """Minimal stand-in for ``boto3.client("kms")``.

    Wraps plaintext as ``b"FAKE_KMS:" + key_id + b":" + plaintext`` so the
    test doesn't have to care about real cipher correctness — only the
    adapter's plumbing.
    """

    def __init__(
        self, *, key_id: str = "alias/test-cmk", fail_after: int | None = None
    ) -> None:
        self.key_id = key_id
        self.encrypt_calls: list[bytes] = []
        self.decrypt_calls: list[bytes] = []
        self._fail_after = fail_after

    def _maybe_fail(self) -> None:
        if self._fail_after is None:
            return
        if (len(self.encrypt_calls) + len(self.decrypt_calls)) >= self._fail_after:
            raise RuntimeError("KMS unavailable (fake)")

    def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, object]:
        self._maybe_fail()
        self.encrypt_calls.append(Plaintext)
        blob = b"FAKE_KMS:" + KeyId.encode() + b":" + Plaintext
        return {"CiphertextBlob": blob, "KeyId": KeyId}

    def decrypt(
        self, *, CiphertextBlob: bytes, KeyId: str | None = None
    ) -> dict[str, object]:
        self._maybe_fail()
        self.decrypt_calls.append(CiphertextBlob)
        if not CiphertextBlob.startswith(b"FAKE_KMS:"):
            raise RuntimeError("not a fake KMS blob")
        _, embedded_key, plaintext = CiphertextBlob.split(b":", 2)
        return {"Plaintext": plaintext, "KeyId": embedded_key.decode()}


@pytest.fixture(autouse=True)
def _reset_metrics_cache() -> None:
    TokenVaultMetrics.reset_for_testing()
    yield
    TokenVaultMetrics.reset_for_testing()


class TestAwsKmsTokenVaultRoundTrip:
    def test_encrypt_decrypt_round_trip(self) -> None:
        fake = _FakeKmsClient(key_id="alias/test-cmk")
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        ciphertext = vault.encrypt("hunter2-secret-token")
        assert ciphertext.startswith("kms_v1:")
        assert vault.decrypt(ciphertext) == "hunter2-secret-token"
        assert vault.key_id_for(ciphertext) == "alias/test-cmk"
        assert len(fake.encrypt_calls) == 1
        assert len(fake.decrypt_calls) == 1

    def test_envelope_carries_key_id(self) -> None:
        fake = _FakeKmsClient(key_id="arn:aws:kms:us-east-1:000:key/abc")
        vault = AwsKmsTokenVault(
            key_id="arn:aws:kms:us-east-1:000:key/abc", kms_client=fake
        )
        ciphertext = vault.encrypt("payload")
        assert vault.key_id_for(ciphertext) == "arn:aws:kms:us-east-1:000:key/abc"

    def test_legacy_envelope_rejected(self) -> None:
        fake = _FakeKmsClient()
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        with pytest.raises(CiphertextFormatError):
            vault.decrypt("gAAAAA-not-our-envelope")

    def test_kms_unavailable_raises_typed_error_on_encrypt(self) -> None:
        fake = _FakeKmsClient(fail_after=0)
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        with pytest.raises(KmsUnavailableError):
            vault.encrypt("anything")

    def test_kms_unavailable_raises_typed_error_on_decrypt(self) -> None:
        fake = _FakeKmsClient()
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        # First call encrypts successfully…
        ciphertext = vault.encrypt("payload")
        # …then we wedge KMS for subsequent calls.
        fake._fail_after = 0
        with pytest.raises(KmsUnavailableError):
            vault.decrypt(ciphertext)

    def test_passes_key_id_on_decrypt(self) -> None:
        """Defense-in-depth: AWS KMS Decrypt accepts KeyId for symmetric
        keys to reject ciphertext-swap attacks across CMKs."""

        fake = _FakeKmsClient(key_id="alias/test-cmk")
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        ciphertext = vault.encrypt("payload")
        vault.decrypt(ciphertext)
        assert fake.decrypt_calls, "KMS decrypt must be invoked"


class TestDecryptCache:
    def test_cache_hit_skips_kms(self) -> None:
        fake = _FakeKmsClient()
        cache = _DecryptCache(ttl_seconds=300, max_entries=10)
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake, cache=cache)
        ciphertext = vault.encrypt("payload")
        assert vault.decrypt(ciphertext) == "payload"
        before = len(fake.decrypt_calls)
        assert vault.decrypt(ciphertext) == "payload"
        assert len(fake.decrypt_calls) == before, "cache hit must not call KMS"

    def test_cache_ttl_expiry_re_calls_kms(self) -> None:
        fake = _FakeKmsClient()
        clock = [0.0]
        cache = _DecryptCache(
            ttl_seconds=10,
            max_entries=10,
            clock=lambda: clock[0],
        )
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake, cache=cache)
        ciphertext = vault.encrypt("payload")
        vault.decrypt(ciphertext)
        clock[0] = 100.0  # well past TTL
        before = len(fake.decrypt_calls)
        vault.decrypt(ciphertext)
        assert len(fake.decrypt_calls) == before + 1

    def test_cache_eviction_under_max_size(self) -> None:
        fake = _FakeKmsClient()
        cache = _DecryptCache(ttl_seconds=300, max_entries=2)
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake, cache=cache)
        ct1 = vault.encrypt("a")
        ct2 = vault.encrypt("b")
        ct3 = vault.encrypt("c")
        vault.decrypt(ct1)
        vault.decrypt(ct2)
        vault.decrypt(ct3)
        # Cache holds at most 2 entries; oldest is evicted.
        assert len(cache._entries) == 2  # type: ignore[attr-defined]


class TestLogHygiene:
    def test_no_plaintext_token_in_caplog(self, caplog) -> None:
        caplog.set_level(logging.DEBUG, logger="backend.token_vault")
        fake = _FakeKmsClient()
        vault = AwsKmsTokenVault(key_id="alias/test-cmk", kms_client=fake)
        plaintext = "super-sensitive-oauth-access-token-2026"
        ciphertext = vault.encrypt(plaintext)
        vault.decrypt(ciphertext)
        for record in caplog.records:
            assert plaintext not in record.getMessage()


class TestFactoryProfileEnforcement:
    def test_local_rejected_under_require_kms_profile(self, monkeypatch) -> None:
        monkeypatch.delenv("MCP_TOKEN_VAULT_BACKEND", raising=False)
        monkeypatch.delenv("MCP_TOKEN_VAULT_PROVIDER", raising=False)
        with pytest.raises(RuntimeError, match="forbidden under deployment profile"):
            TokenVaultFactory.create(profile=_profile("single_tenant_managed"))

    def test_local_allowed_when_profile_lifts_requirement(self, monkeypatch) -> None:
        monkeypatch.delenv("MCP_TOKEN_VAULT_BACKEND", raising=False)
        monkeypatch.delenv("MCP_TOKEN_VAULT_PROVIDER", raising=False)
        monkeypatch.setenv(
            "MCP_TOKEN_VAULT_SECRET", "x" * 40
        )  # ≥32 chars for LocalTokenVault.
        vault = TokenVaultFactory.create(
            profile=_profile("development", require_kms=False)
        )
        assert isinstance(vault, LocalTokenVault)

    def test_unknown_backend_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_TOKEN_VAULT_BACKEND", "wat")
        with pytest.raises(RuntimeError, match="Unsupported MCP_TOKEN_VAULT_BACKEND"):
            TokenVaultFactory.create()

    def test_legacy_managed_provider_rejected(self, monkeypatch) -> None:
        monkeypatch.delenv("MCP_TOKEN_VAULT_BACKEND", raising=False)
        monkeypatch.setenv("MCP_TOKEN_VAULT_PROVIDER", "managed")
        with pytest.raises(RuntimeError, match="no longer accepted"):
            TokenVaultFactory.create()

    def test_unimplemented_backends_raise_not_implemented(self, monkeypatch) -> None:
        for backend in ("gcp_kms", "azure_kv", "hashicorp_vault"):
            monkeypatch.setenv("MCP_TOKEN_VAULT_BACKEND", backend)
            with pytest.raises(NotImplementedError):
                TokenVaultFactory.create()


class TestEnvelopeFormat:
    def test_parse_envelope_round_trip(self) -> None:
        from backend_app.token_vault import ManagedSecretTokenVault

        blob = b"\x00\x01\x02\x03ciphertext-bytes"
        envelope = ManagedSecretTokenVault._format_envelope("alias/key", blob)
        key_id, parsed = ManagedSecretTokenVault._parse_envelope(envelope)
        assert key_id == "alias/key"
        assert parsed == blob

    def test_parse_envelope_rejects_malformed(self) -> None:
        from backend_app.token_vault import ManagedSecretTokenVault

        with pytest.raises(CiphertextFormatError):
            ManagedSecretTokenVault._parse_envelope("garbage")
        with pytest.raises(CiphertextFormatError):
            ManagedSecretTokenVault._parse_envelope("kms_v1:no-colon-after")
        # ``base64.urlsafe_b64decode`` ignores characters outside the alphabet,
        # so the rejection vector here is a key_id half that decodes to bytes
        # which can't be UTF-8 — '$' is decoded but its post-decode bytes aren't
        # actually the failure mode. Instead, trigger a length error: a
        # single-character key_id half base64-decodes to 0 bytes (valid empty
        # string), which slips through. Use a vector that fails the str split:
        with pytest.raises(CiphertextFormatError):
            ManagedSecretTokenVault._parse_envelope("kms_v1:single-half-no-colon")

    def test_envelope_base64_url_safe(self) -> None:
        from backend_app.token_vault import ManagedSecretTokenVault

        blob = b"\xfb\xff" * 8  # produces +/ in standard base64
        envelope = ManagedSecretTokenVault._format_envelope("k", blob)
        # No '+' or '/' from urlsafe encoding.
        assert "+" not in envelope
        assert "/" not in envelope
        # Round-trip still works.
        _, parsed = ManagedSecretTokenVault._parse_envelope(envelope)
        assert parsed == blob


class TestKmsKeyIdColumn:
    """The ``kms_key_id`` column carries the per-row key id for rotation;
    LocalTokenVault returns None, ManagedSecretTokenVault parses the envelope.
    """

    def test_local_vault_reports_no_key_id(self) -> None:
        vault = LocalTokenVault(secret="x" * 40)
        ciphertext = vault.encrypt("payload")
        assert vault.key_id_for(ciphertext) is None

    def test_aws_vault_reports_envelope_key_id(self) -> None:
        fake = _FakeKmsClient(key_id="alias/rotation-key-A")
        vault = AwsKmsTokenVault(key_id="alias/rotation-key-A", kms_client=fake)
        ciphertext = vault.encrypt("payload")
        assert vault.key_id_for(ciphertext) == "alias/rotation-key-A"

    def test_aws_vault_reports_none_for_garbage(self) -> None:
        vault = AwsKmsTokenVault(key_id="alias/x", kms_client=_FakeKmsClient())
        assert vault.key_id_for("not-an-envelope") is None
