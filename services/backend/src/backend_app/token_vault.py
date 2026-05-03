"""Token vault abstraction for MCP OAuth credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


class TokenVault:
    """Encrypt and decrypt connector tokens behind a narrow interface."""

    def encrypt(self, plaintext: str) -> str:
        raise NotImplementedError

    def decrypt(self, ciphertext: str) -> str:
        raise NotImplementedError


class LocalTokenVault(TokenVault):
    """Fernet-based encrypted vault for local development and tests.

    Production deployments should replace this with KMS or a managed secret store.
    The Fernet key is derived deterministically from the existing SHA-256 secret,
    so the same secret always produces the same encryption key.

    Legacy XOR-encrypted tokens are transparently decrypted on read; all new
    encryptions use Fernet.
    """

    def __init__(self, secret: str | None = None) -> None:
        raw_secret = (
            secret
            or os.environ.get("MCP_TOKEN_VAULT_SECRET")
            or TokenVaultFactory.development_secret()
        )
        if len(raw_secret) < 32:
            raise RuntimeError("MCP_TOKEN_VAULT_SECRET must be at least 32 characters")
        self._raw_key = hashlib.sha256(raw_secret.encode("utf-8")).digest()
        self._fernet = self._build_fernet(self._raw_key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, Exception):
            return self._legacy_xor_decrypt(ciphertext)

    @staticmethod
    def _build_fernet(raw_key: bytes):
        from cryptography.fernet import Fernet

        fernet_key = base64.urlsafe_b64encode(raw_key)
        return Fernet(fernet_key)

    def _legacy_xor_decrypt(self, ciphertext: str) -> str:
        """Decrypt tokens produced by the previous XOR-based envelope.

        This fallback enables zero-downtime migration: existing tokens
        encrypted with the old scheme are decrypted transparently.
        Callers should re-encrypt with Fernet after reading.
        """
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        nonce = raw[:16]
        signature = raw[16:48]
        encrypted = raw[48:]
        expected = hmac.new(self._raw_key, nonce + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("token envelope signature is invalid")
        keystream = self._legacy_keystream(nonce, len(encrypted))
        decrypted = bytes(
            left ^ right for left, right in zip(encrypted, keystream, strict=True)
        )
        return decrypted.decode("utf-8")

    def _legacy_keystream(self, nonce: bytes, size: int) -> bytes:
        chunks: list[bytes] = []
        counter = 0
        while sum(len(chunk) for chunk in chunks) < size:
            counter_bytes = counter.to_bytes(8, "big")
            chunks.append(
                hmac.new(self._raw_key, nonce + counter_bytes, hashlib.sha256).digest()
            )
            counter += 1
        return b"".join(chunks)[:size]


class ManagedSecretTokenVault(TokenVault):
    """Fail-closed boundary for production managed secret-store integration.

    Production must provide a real adapter for the deployment's KMS or managed
    secret store. This class deliberately does not reuse the local envelope.
    """

    def __init__(self) -> None:
        raise RuntimeError("Managed MCP token vault adapter is not configured")


class TokenVaultFactory:
    """Factory for token vault implementations and environment policy."""

    @classmethod
    def create(cls) -> TokenVault:
        """Create a token vault that is explicit about production secret handling."""

        environment = cls.environment()
        provider = os.environ.get("MCP_TOKEN_VAULT_PROVIDER", "local").strip().lower()
        if environment == "production" and provider != "managed":
            raise RuntimeError("Production requires MCP_TOKEN_VAULT_PROVIDER=managed")
        if provider == "managed":
            return ManagedSecretTokenVault()
        if provider == "local":
            return LocalTokenVault()
        raise RuntimeError(f"Unsupported MCP_TOKEN_VAULT_PROVIDER: {provider}")

    @classmethod
    def development_secret(cls) -> str:
        if cls.environment() == "production":
            raise RuntimeError("MCP_TOKEN_VAULT_SECRET is required in production")
        return "local-dev-token-vault-secret-not-for-production"

    @staticmethod
    def environment() -> str:
        return os.environ.get("BACKEND_ENVIRONMENT", "development").strip().lower()
