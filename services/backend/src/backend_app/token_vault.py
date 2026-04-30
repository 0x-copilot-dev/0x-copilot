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
        raw_secret = secret or os.environ.get("MCP_TOKEN_VAULT_SECRET") or "local-dev-token-vault"
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
