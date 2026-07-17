"""HMAC hash-chain signing for audit events.

Each persisted audit row is signed with HMAC-SHA256 over the row's canonical
JSON plus the previous row's signature ("prev_hash"). The chain is per-stream
(typically per audit table, per ``org_id``) so verification stays scoped to
one tenant. Tampering -- altering a row, deleting a row, reordering rows,
or replaying a row from a different chain -- breaks the chain because the
recomputed signature will not match the stored one.

Key material lives in ``AUDIT_HMAC_KEY`` (hex-encoded). ``AUDIT_HMAC_KEY_VERSION``
identifies which key signed a given row so callers can rotate without
rewriting history; the verifier keeps a small map of {version -> key} and
picks the right one per row. Production (the environment env var the
caller passes equals "production") fails closed when ``AUDIT_HMAC_KEY`` is
unset.

The signed envelope is::

    {
      "prev_hash": "<hex of prior row's signature, or null on first row>",
      "key_version": <int>,
      "payload": <canonical JSON of the audit record minus signature/prev/seq>
    }

Sorted keys + tight separators give one canonical byte sequence per logical
record. ``datetime`` values are ISO-8601 strings, ``bytes`` become hex,
``UUID`` becomes its canonical string -- types we can verify
deterministically.

Replaces the in-tree duplicates that previously lived at
``services/backend/src/backend_app/audit_chain.py`` and
``services/ai-backend/src/agent_runtime/observability/audit_chain.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
from typing import Any
from uuid import UUID


_DEFAULT_KEY_ENV = "AUDIT_HMAC_KEY"
_DEFAULT_KEY_VERSION_ENV = "AUDIT_HMAC_KEY_VERSION"
_PREVIOUS_KEY_ENV_PREFIX = "AUDIT_HMAC_KEY_V"  # e.g. AUDIT_HMAC_KEY_V0 for rotation
_MIN_KEY_BYTES = 16

# Sentinel dev key. Byte-identical to the value the legacy in-tree
# implementations used so dev fixtures and pre-recorded chains keep
# verifying after the consolidation.
_DEV_SENTINEL_KEY = b"dev-audit-hmac-sentinel-key-32by"  # 32 bytes


@dataclass(frozen=True)
class ChainSignature:
    """One signed audit row's chain fields."""

    prev_hash: bytes | None
    signature: bytes
    key_version: int


@dataclass(frozen=True)
class ChainVerificationResult:
    """Outcome of verifying a chain. ``ok`` is the only positive answer."""

    ok: bool
    broken_at_seq: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AuditChainRow:
    """One row of audit data, including chain fields, used by the verifier."""

    seq: int
    payload: dict[str, Any]
    prev_hash: bytes | None
    signature: bytes
    key_version: int


class AuditChainSigner:
    """HMAC-SHA256 hash-chain signer/verifier for audit rows.

    One instance is configured with the active key + version and may also hold
    previous keys for rotation; chosen at verification time by the row's
    ``key_version``. Sign and verify use ``hmac.compare_digest`` for
    constant-time comparison.
    """

    def __init__(
        self,
        *,
        keys: dict[int, bytes],
        active_version: int,
    ) -> None:
        if not keys:
            raise ValueError("AuditChainSigner requires at least one key")
        if active_version not in keys:
            raise ValueError(f"active_version {active_version} not in key map")
        for version, key in keys.items():
            if len(key) < _MIN_KEY_BYTES:
                raise ValueError(
                    f"audit HMAC key v{version} is too short "
                    f"({len(key)} bytes; need >= {_MIN_KEY_BYTES})"
                )
        self._keys = dict(keys)
        self._active_version = active_version

    @classmethod
    def from_env(
        cls,
        *,
        environment_env_var: str,
        fail_closed: bool | None = None,
    ) -> "AuditChainSigner":
        """Load keys from environment.

        ``AUDIT_HMAC_KEY`` is the active key (hex-encoded);
        ``AUDIT_HMAC_KEY_VERSION`` is its integer version (default 1).
        ``AUDIT_HMAC_KEY_V<N>`` provides additional historical keys for
        verification only.

        ``environment_env_var`` is the name of the environment variable the
        caller's service uses to identify the runtime environment (e.g.
        ``"BACKEND_ENVIRONMENT"`` or ``"RUNTIME_ENVIRONMENT"``). When that
        var equals ``"production"`` and ``AUDIT_HMAC_KEY`` is unset, this
        method raises. Otherwise we fall back to a fixed sentinel key so
        local development without configured keys still produces a
        verifiable chain.

        Tests can pass ``fail_closed=False`` to opt out of the env check.
        """

        env = os.environ.get(environment_env_var, "development").strip().lower()
        is_prod = env == "production"
        if fail_closed is None:
            fail_closed = is_prod

        active_hex = os.environ.get(_DEFAULT_KEY_ENV, "").strip()
        if not active_hex:
            if fail_closed:
                raise RuntimeError(
                    f"{_DEFAULT_KEY_ENV} must be set in production",
                )
            return cls(keys={0: _DEV_SENTINEL_KEY}, active_version=0)

        try:
            active_key = bytes.fromhex(active_hex)
        except ValueError as exc:
            raise RuntimeError(f"{_DEFAULT_KEY_ENV} must be hex-encoded") from exc

        version_str = os.environ.get(_DEFAULT_KEY_VERSION_ENV, "1").strip()
        try:
            active_version = int(version_str)
        except ValueError as exc:
            raise RuntimeError(
                f"{_DEFAULT_KEY_VERSION_ENV} must be an integer"
            ) from exc

        keys: dict[int, bytes] = {active_version: active_key}
        for env_name, env_value in os.environ.items():
            if not env_name.startswith(_PREVIOUS_KEY_ENV_PREFIX):
                continue
            suffix = env_name[len(_PREVIOUS_KEY_ENV_PREFIX) :]
            if not suffix.isdigit():
                continue
            previous_version = int(suffix)
            if previous_version == active_version:
                continue
            try:
                keys[previous_version] = bytes.fromhex(env_value.strip())
            except ValueError:
                continue

        return cls(keys=keys, active_version=active_version)

    @property
    def active_version(self) -> int:
        return self._active_version

    def sign(
        self,
        *,
        prev_hash: bytes | None,
        payload: dict[str, Any],
    ) -> ChainSignature:
        """Sign a row given the prior row's signature (or ``None`` for first)."""

        canonical = self._canonicalize(
            payload, prev_hash=prev_hash, key_version=self._active_version
        )
        signature = hmac.new(
            self._keys[self._active_version], canonical, hashlib.sha256
        ).digest()
        return ChainSignature(
            prev_hash=prev_hash,
            signature=signature,
            key_version=self._active_version,
        )

    def verify_row(
        self,
        *,
        prev_hash: bytes | None,
        payload: dict[str, Any],
        signature: bytes,
        key_version: int,
    ) -> bool:
        key = self._keys.get(key_version)
        if key is None:
            return False
        canonical = self._canonicalize(
            payload, prev_hash=prev_hash, key_version=key_version
        )
        expected = hmac.new(key, canonical, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)

    def verify_chain(self, rows: list[AuditChainRow]) -> ChainVerificationResult:
        """Verify a sequence of rows ordered by ``seq`` ascending.

        Returns ``ok=True`` only if every row's signature recomputes correctly
        and every row's ``prev_hash`` equals the prior row's ``signature``.
        """

        prev_hash: bytes | None = None
        for row in rows:
            if row.prev_hash != prev_hash:
                return ChainVerificationResult(
                    ok=False,
                    broken_at_seq=row.seq,
                    reason="prev_hash mismatch",
                )
            if not self.verify_row(
                prev_hash=row.prev_hash,
                payload=row.payload,
                signature=row.signature,
                key_version=row.key_version,
            ):
                return ChainVerificationResult(
                    ok=False,
                    broken_at_seq=row.seq,
                    reason="signature mismatch",
                )
            prev_hash = row.signature
        return ChainVerificationResult(ok=True)

    @staticmethod
    def _canonicalize(
        payload: dict[str, Any], *, prev_hash: bytes | None, key_version: int
    ) -> bytes:
        envelope = {
            "prev_hash": prev_hash.hex() if prev_hash else None,
            "key_version": key_version,
            "payload": payload,
        }
        return json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            default=AuditChainSigner._stringify,
        ).encode("utf-8")

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, UUID):
            return str(value)
        raise TypeError(
            f"audit chain canonicalization rejected unserializable type: "
            f"{type(value).__name__}"
        )
