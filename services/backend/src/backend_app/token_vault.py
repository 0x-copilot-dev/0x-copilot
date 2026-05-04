"""Token vault abstraction for MCP OAuth credentials.

C6 expanded the original Fernet-only stub into an adapter framework so bank
and government deploys can plug in a managed KMS. The hierarchy is:

    TokenVault (interface)
      └── LocalTokenVault           — Fernet, dev-only.
      └── ManagedSecretTokenVault   — abstract base for KMS adapters.
            └── AwsKmsTokenVault    — boto3 / AWS KMS (this PR).
            (GCP / Azure / HashiCorp Vault adapters ship as follow-ups.)

``TokenVaultFactory.create()`` reads ``MCP_TOKEN_VAULT_BACKEND`` and resolves
the right adapter; the deployment profile rejects ``local`` when
``require_kms_token_vault=True`` so no production-class environment can
boot without KMS.

Per-row ``kms_key_id`` is encoded into the public ciphertext envelope so a
rotation script can re-encrypt rows without depending on column denormalization
(see ``services/backend/scripts/rotate_token_vault.py``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from backend_app.deployment_profile import DeploymentProfile


_LOGGER = logging.getLogger("backend.token_vault")


_KMS_ENVELOPE_PREFIX = "kms_v1:"
_LOCAL_FERNET_PREFIX = (
    "gAAAAA"  # Fernet's fixed leading bytes (base64 of 0x80 + version + ts).
)


class TokenVaultError(RuntimeError):
    """Base error for token-vault failures."""


class KmsUnavailableError(TokenVaultError):
    """Raised when the KMS backend is unreachable for an encrypt or decrypt."""


class CiphertextFormatError(TokenVaultError):
    """Raised when a stored ciphertext can't be parsed as any known envelope."""


class TokenVault(ABC):
    """Encrypt and decrypt connector tokens behind a narrow interface."""

    backend_name: str = "unknown"

    @abstractmethod
    def encrypt(self, plaintext: str) -> str: ...

    @abstractmethod
    def decrypt(self, ciphertext: str) -> str: ...

    def key_id_for(self, ciphertext: str) -> str | None:
        """Best-effort key id extraction for rotation reporting."""

        return None


class LocalTokenVault(TokenVault):
    """Fernet-based encrypted vault for local development and tests.

    Production deployments must replace this with a KMS-backed adapter; the
    deployment profile and ``TokenVaultFactory`` enforce that. The Fernet key
    is derived deterministically from ``MCP_TOKEN_VAULT_SECRET`` so the same
    secret always produces the same encryption key. Legacy XOR-encrypted
    tokens are transparently decrypted on read for zero-downtime upgrades.
    """

    backend_name = "local"

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


class _DecryptCacheClock(Protocol):
    def __call__(self) -> float: ...


class _DecryptCache:
    """Bounded TTL cache for decrypted plaintexts, keyed by sha256(ciphertext)."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 10_000,
        clock: _DecryptCacheClock | None = None,
        metrics: "TokenVaultMetricsRecorder | None" = None,
        backend: str = "unknown",
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock or time.monotonic
        self._entries: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._metrics = metrics
        self._backend = backend

    @staticmethod
    def _key(ciphertext: str) -> str:
        return hashlib.sha256(ciphertext.encode("ascii", errors="ignore")).hexdigest()

    def get(self, ciphertext: str) -> str | None:
        digest = self._key(ciphertext)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(digest)
            if entry is None:
                self._record("miss")
                return None
            expires_at, plaintext = entry
            if expires_at < now:
                self._entries.pop(digest, None)
                self._record("miss")
                return None
            self._record("hit")
            return plaintext

    def put(self, ciphertext: str, plaintext: str) -> None:
        digest = self._key(ciphertext)
        expires_at = self._clock() + self._ttl
        with self._lock:
            if len(self._entries) >= self._max:
                # Cheap eviction: drop the oldest by expiry.
                oldest_key = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest_key, None)
            self._entries[digest] = (expires_at, plaintext)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _record(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.record_cache(backend=self._backend, outcome=outcome)


class ManagedSecretTokenVault(TokenVault, ABC):
    """Abstract base for KMS-backed adapters.

    The public ``encrypt``/``decrypt`` shape stays identical to ``TokenVault``;
    subclasses only implement the raw KMS calls. The envelope format is
    ``kms_v1:<key_id>:<base64(ciphertext)>`` so the key id is recoverable
    without DB lookups, which is what the rotation script depends on.
    """

    backend_name = "managed"

    def __init__(
        self,
        *,
        cache: _DecryptCache | None = None,
        metrics: "TokenVaultMetricsRecorder | None" = None,
    ) -> None:
        self._cache = cache
        self._metrics = metrics

    @abstractmethod
    def _kms_encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        """Return ``(ciphertext_blob, key_id)``."""

    @abstractmethod
    def _kms_decrypt(self, ciphertext: bytes, *, key_id: str | None) -> bytes:
        """Decrypt ``ciphertext_blob``. ``key_id`` is informational only."""

    def encrypt(self, plaintext: str) -> str:
        start = time.monotonic()
        try:
            ciphertext_blob, key_id = self._kms_encrypt(plaintext.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            self._record("encrypt", "error", start)
            raise KmsUnavailableError(
                f"KMS encrypt failed for backend={self.backend_name}"
            ) from exc
        envelope = self._format_envelope(key_id, ciphertext_blob)
        self._record("encrypt", "ok", start)
        return envelope

    def decrypt(self, ciphertext: str) -> str:
        if self._cache is not None:
            cached = self._cache.get(ciphertext)
            if cached is not None:
                return cached
        key_id, blob = self._parse_envelope(ciphertext)
        start = time.monotonic()
        try:
            plaintext_bytes = self._kms_decrypt(blob, key_id=key_id)
        except Exception as exc:  # pragma: no cover - defensive
            self._record("decrypt", "error", start)
            raise KmsUnavailableError(
                f"KMS decrypt failed for backend={self.backend_name}"
            ) from exc
        plaintext = plaintext_bytes.decode("utf-8")
        self._record("decrypt", "ok", start)
        if self._cache is not None:
            self._cache.put(ciphertext, plaintext)
        return plaintext

    def key_id_for(self, ciphertext: str) -> str | None:
        try:
            key_id, _ = self._parse_envelope(ciphertext)
        except CiphertextFormatError:
            return None
        return key_id

    @staticmethod
    def _format_envelope(key_id: str, blob: bytes) -> str:
        # AWS KMS key ids are full ARNs containing colons, so we base64 both
        # halves to keep ``kms_v1:<b64_key_id>:<b64_blob>`` unambiguous.
        encoded_key = (
            base64.urlsafe_b64encode(key_id.encode("utf-8")).decode("ascii").rstrip("=")
        )
        encoded_blob = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")
        return f"{_KMS_ENVELOPE_PREFIX}{encoded_key}:{encoded_blob}"

    @staticmethod
    def _parse_envelope(ciphertext: str) -> tuple[str, bytes]:
        if not ciphertext.startswith(_KMS_ENVELOPE_PREFIX):
            raise CiphertextFormatError(
                "ciphertext missing kms_v1 envelope prefix; cannot route to KMS"
            )
        body = ciphertext[len(_KMS_ENVELOPE_PREFIX) :]
        try:
            encoded_key, encoded_blob = body.split(":", 1)
        except ValueError as exc:
            raise CiphertextFormatError(
                "kms_v1 envelope must be 'kms_v1:<b64_key_id>:<b64_blob>'"
            ) from exc

        def _decode(value: str, label: str) -> bytes:
            padding = "=" * (-len(value) % 4)
            try:
                return base64.urlsafe_b64decode(value + padding)
            except Exception as exc:
                raise CiphertextFormatError(
                    f"kms_v1 envelope {label} base64 invalid"
                ) from exc

        try:
            key_id = _decode(encoded_key, "key_id").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CiphertextFormatError(
                "kms_v1 envelope key_id is not valid UTF-8"
            ) from exc
        blob = _decode(encoded_blob, "blob")
        return key_id, blob

    def _record(self, op: str, outcome: str, started_at: float) -> None:
        if self._metrics is None:
            return
        self._metrics.record_op(
            backend=self.backend_name,
            op=op,
            outcome=outcome,
            duration_seconds=time.monotonic() - started_at,
        )


class AwsKmsTokenVault(ManagedSecretTokenVault):
    """boto3-backed AWS KMS adapter.

    The CMK is configured by ``MCP_TOKEN_VAULT_KMS_KEY_ID`` (key arn or alias).
    Credentials follow the standard boto3 chain: env, instance profile, etc.
    boto3 is imported lazily so the base image stays slim; non-AWS deploys
    never pay the import cost.
    """

    backend_name = "aws_kms"

    def __init__(
        self,
        *,
        key_id: str,
        region_name: str | None = None,
        kms_client: object | None = None,
        cache: _DecryptCache | None = None,
        metrics: "TokenVaultMetricsRecorder | None" = None,
    ) -> None:
        super().__init__(cache=cache, metrics=metrics)
        self._key_id = key_id
        self._region_name = region_name
        self._client = kms_client or self._build_default_client()

    @classmethod
    def from_env(
        cls,
        *,
        cache: _DecryptCache | None = None,
        metrics: "TokenVaultMetricsRecorder | None" = None,
    ) -> "AwsKmsTokenVault":
        key_id = os.environ.get("MCP_TOKEN_VAULT_KMS_KEY_ID", "").strip()
        if not key_id:
            raise RuntimeError(
                "MCP_TOKEN_VAULT_KMS_KEY_ID is required for "
                "MCP_TOKEN_VAULT_BACKEND=aws_kms"
            )
        return cls(
            key_id=key_id,
            region_name=os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION"),
            cache=cache,
            metrics=metrics,
        )

    def _build_default_client(self) -> object:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "boto3 is required for AwsKmsTokenVault; install boto3 in the "
                "backend image (or set MCP_TOKEN_VAULT_BACKEND=local for dev)."
            ) from exc
        kwargs: dict[str, object] = {}
        if self._region_name:
            kwargs["region_name"] = self._region_name
        return boto3.client("kms", **kwargs)

    def _kms_encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        response = self._client.encrypt(KeyId=self._key_id, Plaintext=plaintext)  # type: ignore[attr-defined]
        return response["CiphertextBlob"], response.get("KeyId", self._key_id)

    def _kms_decrypt(self, ciphertext: bytes, *, key_id: str | None) -> bytes:
        kwargs: dict[str, object] = {"CiphertextBlob": ciphertext}
        # AWS KMS Decrypt is self-describing for symmetric keys; supplying the
        # KeyId is required for asymmetric keys and a defense-in-depth check
        # against ciphertext-swap when symmetric. Prefer the per-row id when
        # we have one.
        if key_id:
            kwargs["KeyId"] = key_id
        response = self._client.decrypt(**kwargs)  # type: ignore[attr-defined]
        return response["Plaintext"]


class TokenVaultFactory:
    """Factory for token vault implementations and environment policy."""

    @classmethod
    def create(
        cls,
        *,
        profile: "DeploymentProfile | None" = None,
    ) -> TokenVault:
        """Create a token vault that is explicit about production secret handling.

        Resolution order:

        1. ``MCP_TOKEN_VAULT_BACKEND`` (preferred) — ``local``, ``aws_kms``,
           ``gcp_kms`` (NotImplemented), ``azure_kv`` (NotImplemented),
           ``hashicorp_vault`` (NotImplemented).
        2. Legacy ``MCP_TOKEN_VAULT_PROVIDER`` (``local``/``managed``) — kept
           for one release of backwards compatibility; ``managed`` raises
           because no concrete adapter was wired pre-C6.

        ``profile`` is honored when supplied; if absent the legacy
        ``BACKEND_ENVIRONMENT=production`` check still applies so existing
        callers (``app.py``, ``service.py``) keep their fail-closed behavior
        even before the deployment-profile object is fully threaded.
        """

        from backend_app.token_vault_metrics import TokenVaultMetrics

        backend = cls._resolve_backend()
        cls._enforce_profile(profile, backend)
        metrics = TokenVaultMetrics.recorder()
        if backend == "local":
            return LocalTokenVault()
        if backend == "aws_kms":
            cache = cls._build_cache(backend, profile, metrics)
            return AwsKmsTokenVault.from_env(cache=cache, metrics=metrics)
        if backend in {"gcp_kms", "azure_kv", "hashicorp_vault"}:
            raise NotImplementedError(
                f"MCP_TOKEN_VAULT_BACKEND={backend!r} adapter ships in a "
                "follow-up PR; use 'aws_kms' for now or 'local' for dev."
            )
        raise RuntimeError(f"Unsupported MCP_TOKEN_VAULT_BACKEND: {backend!r}")

    @classmethod
    def _resolve_backend(cls) -> str:
        explicit = os.environ.get("MCP_TOKEN_VAULT_BACKEND", "").strip().lower()
        if explicit:
            return explicit
        legacy = os.environ.get("MCP_TOKEN_VAULT_PROVIDER", "").strip().lower()
        if legacy == "managed":
            raise RuntimeError(
                "MCP_TOKEN_VAULT_PROVIDER=managed is no longer accepted; set "
                "MCP_TOKEN_VAULT_BACKEND to a concrete adapter (e.g. 'aws_kms')."
            )
        if legacy == "local":
            return "local"
        return "local"  # default for fresh dev

    @classmethod
    def _enforce_profile(
        cls,
        profile: "DeploymentProfile | None",
        backend: str,
    ) -> None:
        environment = cls.environment()
        if profile is not None:
            if profile.toggles.require_kms_token_vault and backend == "local":
                raise RuntimeError(
                    f"MCP_TOKEN_VAULT_BACKEND=local is forbidden under "
                    f"deployment profile {profile.name!r}; set a managed "
                    "backend (e.g. 'aws_kms')."
                )
            return
        # No profile threaded: fall back to the legacy production guard so
        # we never silently ship Fernet to production.
        if environment == "production" and backend == "local":
            raise RuntimeError(
                "MCP_TOKEN_VAULT_BACKEND=local is forbidden in production; "
                "set a managed backend (e.g. 'aws_kms')."
            )

    @classmethod
    def _build_cache(
        cls,
        backend: str,
        profile: "DeploymentProfile | None",
        metrics: "TokenVaultMetricsRecorder",
    ) -> _DecryptCache | None:
        # Self-hosted single-tenant deployments require every decrypt to be
        # audited against the customer's KMS. Per-process caches break that
        # invariant, so we disable the cache for those profiles.
        if profile is not None and profile.name == "single_tenant_self_hosted":
            return None
        ttl = int(os.environ.get("MCP_TOKEN_VAULT_DECRYPT_CACHE_TTL_SECONDS", "300"))
        max_entries = int(
            os.environ.get("MCP_TOKEN_VAULT_DECRYPT_CACHE_MAX_ENTRIES", "10000")
        )
        if ttl <= 0 or max_entries <= 0:
            return None
        return _DecryptCache(
            ttl_seconds=ttl,
            max_entries=max_entries,
            metrics=metrics,
            backend=backend,
        )

    @classmethod
    def development_secret(cls) -> str:
        if cls.environment() == "production":
            raise RuntimeError("MCP_TOKEN_VAULT_SECRET is required in production")
        return "local-dev-token-vault-secret-not-for-production"

    @staticmethod
    def environment() -> str:
        return os.environ.get("BACKEND_ENVIRONMENT", "development").strip().lower()


class TokenVaultMetricsRecorder(Protocol):
    """Narrow protocol satisfied by the OTel-backed metrics recorder.

    Re-declared here to avoid an import cycle: the metrics module imports
    nothing from this file, but this file references the recorder shape.
    """

    def record_op(
        self,
        *,
        backend: str,
        op: str,
        outcome: str,
        duration_seconds: float,
    ) -> None: ...

    def record_cache(self, *, backend: str, outcome: str) -> None: ...
