"""Token vault abstraction for MCP OAuth credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from secrets import token_bytes


class TokenVault:
    """Encrypt and decrypt connector tokens behind a narrow interface."""

    def encrypt(self, plaintext: str) -> str:
        raise NotImplementedError

    def decrypt(self, ciphertext: str) -> str:
        raise NotImplementedError


class LocalTokenVault(TokenVault):
    """Small encrypted vault for local development and tests.

    Production deployments should replace this with KMS or a managed secret store.
    The envelope contains a random nonce, XOR-encrypted bytes, and an HMAC.
    """

    def __init__(self, secret: str | None = None) -> None:
        raw_secret = secret or os.environ.get("MCP_TOKEN_VAULT_SECRET") or TokenVaultFactory.development_secret()
        if len(raw_secret) < 32:
            raise RuntimeError("MCP_TOKEN_VAULT_SECRET must be at least 32 characters")
        self._key = hashlib.sha256(raw_secret.encode("utf-8")).digest()

    def encrypt(self, plaintext: str) -> str:
        nonce = token_bytes(16)
        data = plaintext.encode("utf-8")
        keystream = self._keystream(nonce, len(data))
        encrypted = bytes(left ^ right for left, right in zip(data, keystream, strict=True))
        signature = hmac.new(self._key, nonce + encrypted, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(nonce + signature + encrypted).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        nonce = raw[:16]
        signature = raw[16:48]
        encrypted = raw[48:]
        expected = hmac.new(self._key, nonce + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("token envelope signature is invalid")
        keystream = self._keystream(nonce, len(encrypted))
        decrypted = bytes(left ^ right for left, right in zip(encrypted, keystream, strict=True))
        return decrypted.decode("utf-8")

    def _keystream(self, nonce: bytes, size: int) -> bytes:
        chunks: list[bytes] = []
        counter = 0
        while sum(len(chunk) for chunk in chunks) < size:
            counter_bytes = counter.to_bytes(8, "big")
            chunks.append(hmac.new(self._key, nonce + counter_bytes, hashlib.sha256).digest())
            counter += 1
        return b"".join(chunks)[:size]


class ManagedSecretTokenVault(LocalTokenVault):
    """Production-mode envelope vault backed by externally managed root material.

    Deployments must inject `MCP_TOKEN_VAULT_SECRET` from KMS or a managed secret
    store and rotate it through that provider. The application refuses to use a
    baked-in development fallback in production.
    """

    def __init__(self) -> None:
        raw_secret = os.environ.get("MCP_TOKEN_VAULT_SECRET", "").strip()
        if not raw_secret:
            raise RuntimeError("MCP_TOKEN_VAULT_SECRET must be supplied by the managed secret store")
        super().__init__(raw_secret)


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
