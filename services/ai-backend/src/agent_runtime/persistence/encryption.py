"""C7 field-level envelope encryption for tenant PII columns.

Each row gets a fresh AES-256-GCM data encryption key (DEK). The DEK is
wrapped (encrypted) by a customer-managed KMS CMK; the wrapped DEK + IV +
ciphertext+tag are concatenated into a self-describing envelope:

    v1:<b64(wrapped_dek)>:<b64(iv)>:<b64(ciphertext+tag)>

The Additional Authenticated Data (AAD) binds the ciphertext to its
``(table, column, org_id)`` triple — a ciphertext extracted from
``agent_messages.content_text`` cannot be decrypted as if read from
``runtime_audit_log``, defeating ciphertext-swap attacks across columns or
tenants. CMK rotation only invalidates the DEK cache; row ciphertexts stay
valid because the wrapped DEK is decrypted on demand.

The KMS surface is intentionally minimal — a ``KmsClient`` Protocol with
``wrap_data_key``/``unwrap_data_key``. The backend C6 ``ManagedSecretToken
Vault`` uses KMS for token wrapping; this module does the same for column
DEKs but with a separate adapter so the two paths can move independently
(per the monorepo's hard service boundary: ai-backend cannot import from
backend's ``src/``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Protocol


_ENVELOPE_PREFIX = "v1:"
_DEK_BYTES = 32  # AES-256
_GCM_IV_BYTES = 12  # NIST SP 800-38D recommendation


class FieldEncryptionError(Exception):
    """Base error for field-encryption failures."""


class EncryptionUnavailableError(FieldEncryptionError):
    """KMS or crypto backend unreachable; writes must fail closed."""


class CiphertextDecodeError(FieldEncryptionError):
    """Stored ciphertext is malformed or the AAD doesn't match."""


class FieldEncryption(ABC):
    """Encrypt and decrypt a single column value bound to ``(table, column, org_id)``."""

    @abstractmethod
    def is_envelope_v1(self) -> bool: ...

    @abstractmethod
    def encrypt(
        self,
        plaintext: bytes,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> str: ...

    @abstractmethod
    def decrypt(
        self,
        ciphertext: str,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> bytes: ...


class NullFieldEncryption(FieldEncryption):
    """Pass-through used when ``RUNTIME_FIELD_ENCRYPTION=disabled``.

    Returns the plaintext as-is on encrypt; on decrypt, refuses to handle a
    v1 envelope so writes that bypass field encryption cannot accidentally
    silently roundtrip through this adapter. Used for dev and as the
    default during phase-1 of the rollout when reads must tolerate v0.
    """

    def is_envelope_v1(self) -> bool:
        return False

    def encrypt(
        self,
        plaintext: bytes,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> str:
        del table, column, org_id
        return plaintext.decode("utf-8")

    def decrypt(
        self,
        ciphertext: str,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> bytes:
        del table, column, org_id
        if ciphertext.startswith(_ENVELOPE_PREFIX):
            raise CiphertextDecodeError(
                "NullFieldEncryption cannot decrypt v1 envelopes; the "
                "EnvelopeFieldEncryption adapter is required."
            )
        return ciphertext.encode("utf-8")


class KmsClient(Protocol):
    """Wraps and unwraps a per-row DEK against a customer-managed CMK."""

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        """Return ``(wrapped_blob, key_id)``."""

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        """Return the plaintext DEK."""


class _DekCache:
    """TTL cache from ``sha256(wrapped_dek)`` to the unwrapped DEK.

    Bounded size; thread-safe; scoped per-process. CMK revocation
    propagates within the TTL.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 60,
        max_entries: int = 1024,
        clock=time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _key(wrapped_dek: bytes) -> str:
        return hashlib.sha256(wrapped_dek).hexdigest()

    def get(self, wrapped_dek: bytes) -> bytes | None:
        digest = self._key(wrapped_dek)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                self._misses += 1
                return None
            expires_at, dek = entry
            if expires_at < now:
                self._entries.pop(digest, None)
                self._misses += 1
                return None
            self._hits += 1
            return dek

    def put(self, wrapped_dek: bytes, dek: bytes) -> None:
        digest = self._key(wrapped_dek)
        expires_at = self._clock() + self._ttl
        with self._lock:
            if len(self._entries) >= self._max:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[digest] = (expires_at, dek)

    def stats(self) -> tuple[int, int]:
        with self._lock:
            return self._hits, self._misses

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class EnvelopeFieldEncryption(FieldEncryption):
    """AES-256-GCM with KMS-wrapped per-row DEKs and AAD-bound columns."""

    def __init__(
        self,
        *,
        kms_client: KmsClient,
        dek_cache_ttl: int = 60,
        dek_cache_size: int = 1024,
    ) -> None:
        self._kms = kms_client
        self._cache = _DekCache(ttl_seconds=dek_cache_ttl, max_entries=dek_cache_size)

    def is_envelope_v1(self) -> bool:
        return True

    def encrypt(
        self,
        plaintext: bytes,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> str:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - cryptography is a runtime dep
            raise EncryptionUnavailableError(
                "cryptography library is required for envelope_v1"
            ) from exc

        dek = secrets.token_bytes(_DEK_BYTES)
        try:
            wrapped, key_id = self._kms.wrap_data_key(dek)
        except Exception as exc:
            raise EncryptionUnavailableError(
                "KMS wrap_data_key failed; refusing to write plaintext"
            ) from exc
        del key_id  # AWS KMS Decrypt is self-describing for symmetric keys.
        iv = secrets.token_bytes(_GCM_IV_BYTES)
        aad = self._aad(table=table, column=column, org_id=org_id)
        ciphertext_with_tag = AESGCM(dek).encrypt(iv, plaintext, aad)
        # Wipe the in-memory plaintext DEK ASAP. Best-effort.
        dek = b"\x00" * _DEK_BYTES
        del dek
        return self._format_envelope(wrapped, iv, ciphertext_with_tag)

    def decrypt(
        self,
        ciphertext: str,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.exceptions import InvalidTag
        except ImportError as exc:  # pragma: no cover
            raise EncryptionUnavailableError(
                "cryptography library is required for envelope_v1"
            ) from exc

        wrapped, iv, ct_with_tag = self._parse_envelope(ciphertext)
        dek = self._cache.get(wrapped)
        if dek is None:
            try:
                dek = self._kms.unwrap_data_key(wrapped, key_id=None)
            except Exception as exc:
                raise EncryptionUnavailableError(
                    "KMS unwrap_data_key failed; ciphertext is not currently decryptable"
                ) from exc
            self._cache.put(wrapped, dek)
        aad = self._aad(table=table, column=column, org_id=org_id)
        try:
            return AESGCM(dek).decrypt(iv, ct_with_tag, aad)
        except InvalidTag as exc:
            raise CiphertextDecodeError(
                "AAD or tag mismatch — ciphertext was either tampered with "
                "or read from a column/tenant it wasn't encrypted for."
            ) from exc

    @staticmethod
    def _aad(*, table: str, column: str, org_id: str) -> bytes:
        return f"{table}|{column}|{org_id}".encode("utf-8")

    @staticmethod
    def _format_envelope(wrapped: bytes, iv: bytes, ct_with_tag: bytes) -> str:
        def _b64(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

        return _ENVELOPE_PREFIX + ":".join((_b64(wrapped), _b64(iv), _b64(ct_with_tag)))

    @staticmethod
    def _parse_envelope(ciphertext: str) -> tuple[bytes, bytes, bytes]:
        if not ciphertext.startswith(_ENVELOPE_PREFIX):
            raise CiphertextDecodeError("ciphertext missing v1 envelope prefix")
        body = ciphertext[len(_ENVELOPE_PREFIX) :]
        parts = body.split(":")
        if len(parts) != 3:
            raise CiphertextDecodeError(
                "v1 envelope must be 'v1:<wrapped_dek>:<iv>:<ct+tag>'"
            )

        def _decode(value: str, label: str) -> bytes:
            padding = "=" * (-len(value) % 4)
            try:
                return base64.urlsafe_b64decode(value + padding)
            except Exception as exc:
                raise CiphertextDecodeError(
                    f"v1 envelope {label} is not valid base64"
                ) from exc

        return (
            _decode(parts[0], "wrapped_dek"),
            _decode(parts[1], "iv"),
            _decode(parts[2], "ct+tag"),
        )


class EncryptionVersionRequired(FieldEncryptionError):
    """Raised when ``RUNTIME_FIELD_ENCRYPTION_STRICT_READS=true`` and a v0 row
    is encountered.

    Operators flip strict reads on after backfill confirms
    ``min(encryption_version)=1`` everywhere — at that point any v0 row is a
    bug or a missed retention sweep, and we want the read path to surface it
    rather than silently return plaintext.
    """


_JSON_ENVELOPE_KEY = "$enc"
_TEXT_ENVELOPE_PREFIX_BYTES = len(_ENVELOPE_PREFIX)


class FieldCodec:
    """Per-column encrypt/decrypt facade over a :class:`FieldEncryption` adapter.

    Hides the (text vs JSONB) marshaling and the (v0 vs v1) version
    branching so the per-call-site code in ``PostgresRuntimeApiStore``
    stays one-line per column.

    JSONB columns wrap the v1 envelope inside a single-key object —
    ``{"$enc": "v1:<wrapped>:<iv>:<ct+tag>"}`` — so the column stays
    valid JSONB at the Postgres level. Text columns store the envelope
    string directly.

    When ``strict_reads=True``, decrypt-on-read against an
    ``encryption_version=0`` row raises :class:`EncryptionVersionRequired`.
    Operators turn this on after backfill confirms every row is v1 to
    surface any bug or missed sweep.
    """

    def __init__(
        self,
        encryption: FieldEncryption,
        *,
        strict_reads: bool = False,
    ) -> None:
        self._enc = encryption
        self._strict_reads = strict_reads

    @property
    def write_version(self) -> int:
        """Encryption version that new INSERTs / UPDATEs should set."""

        return 1 if self._enc.is_envelope_v1() else 0

    @property
    def is_envelope_v1(self) -> bool:
        return self._enc.is_envelope_v1()

    def encrypt_text(
        self,
        value: str | None,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> str | None:
        if value is None:
            return None
        if not self._enc.is_envelope_v1():
            return value
        return self._enc.encrypt(
            value.encode("utf-8"), table=table, column=column, org_id=org_id
        )

    def decrypt_text(
        self,
        stored: str | None,
        *,
        encryption_version: int,
        table: str,
        column: str,
        org_id: str,
    ) -> str | None:
        if stored is None:
            return None
        if encryption_version == 0:
            self._check_strict(table=table, column=column, org_id=org_id)
            return stored
        if not stored.startswith(_ENVELOPE_PREFIX):
            # v1 row but value isn't an envelope (e.g. retention sweeper
            # rewrote the column to a placeholder). Pass through.
            return stored
        decrypted = self._enc.decrypt(stored, table=table, column=column, org_id=org_id)
        return decrypted.decode("utf-8")

    def encrypt_jsonb(
        self,
        value: Any,
        *,
        table: str,
        column: str,
        org_id: str,
    ) -> Any:
        if value is None:
            return None
        if not self._enc.is_envelope_v1():
            return value
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        envelope = self._enc.encrypt(
            serialized, table=table, column=column, org_id=org_id
        )
        return {_JSON_ENVELOPE_KEY: envelope}

    def decrypt_jsonb(
        self,
        stored: Any,
        *,
        encryption_version: int,
        table: str,
        column: str,
        org_id: str,
    ) -> Any:
        if stored is None:
            return None
        if encryption_version == 0:
            self._check_strict(table=table, column=column, org_id=org_id)
            return stored
        if (
            isinstance(stored, dict)
            and len(stored) == 1
            and _JSON_ENVELOPE_KEY in stored
            and isinstance(stored[_JSON_ENVELOPE_KEY], str)
        ):
            decrypted = self._enc.decrypt(
                stored[_JSON_ENVELOPE_KEY],
                table=table,
                column=column,
                org_id=org_id,
            )
            return json.loads(decrypted.decode("utf-8"))
        # v1 row but value isn't an envelope dict — same logic as text.
        return stored

    def _check_strict(self, *, table: str, column: str, org_id: str) -> None:
        # Strict reads only make sense once envelope_v1 is on; under a Null
        # adapter every row is legitimately v0.
        if self._strict_reads and self._enc.is_envelope_v1():
            raise EncryptionVersionRequired(
                f"strict reads enabled but {table}.{column} (org_id={org_id}) "
                "is encryption_version=0 — backfill incomplete or row missed."
            )


class FieldEncryptionFactory:
    """Resolve the active ``FieldEncryption`` adapter from environment + KMS.

    ``RUNTIME_FIELD_ENCRYPTION`` values:

      - ``disabled`` (default in dev) — ``NullFieldEncryption`` pass-through.
      - ``envelope_v1`` — ``EnvelopeFieldEncryption`` against the KMS adapter.

    ``RUNTIME_KMS_BACKEND`` selects the KMS:

      - ``aws_kms`` — boto3 / AWS KMS, key id from
        ``RUNTIME_KMS_KEY_ID``.
      - (others ship as follow-ups, mirroring the C6 backend adapters.)
    """

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
    ) -> FieldEncryption:
        env = environ if environ is not None else dict(os.environ)
        mode = env.get("RUNTIME_FIELD_ENCRYPTION", "disabled").strip().lower()
        if mode == "disabled":
            return NullFieldEncryption()
        if mode == "envelope_v1":
            kms = cls._build_kms_client(env)
            ttl = int(env.get("RUNTIME_FIELD_ENCRYPTION_DEK_CACHE_TTL", "60"))
            size = int(env.get("RUNTIME_FIELD_ENCRYPTION_DEK_CACHE_SIZE", "1024"))
            return EnvelopeFieldEncryption(
                kms_client=kms,
                dek_cache_ttl=ttl,
                dek_cache_size=size,
            )
        raise RuntimeError(f"Unknown RUNTIME_FIELD_ENCRYPTION mode: {mode!r}")

    @classmethod
    def _build_kms_client(cls, env: dict[str, str]) -> KmsClient:
        backend = env.get("RUNTIME_KMS_BACKEND", "").strip().lower()
        if backend == "aws_kms":
            from agent_runtime.persistence._aws_kms_client import AwsKmsClient

            key_id = env.get("RUNTIME_KMS_KEY_ID", "").strip()
            if not key_id:
                raise RuntimeError(
                    "RUNTIME_KMS_KEY_ID is required for RUNTIME_KMS_BACKEND=aws_kms"
                )
            return AwsKmsClient(
                key_id=key_id,
                region_name=env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION"),
            )
        raise RuntimeError(
            f"Unsupported RUNTIME_KMS_BACKEND={backend!r}; "
            "set 'aws_kms' or RUNTIME_FIELD_ENCRYPTION=disabled."
        )
