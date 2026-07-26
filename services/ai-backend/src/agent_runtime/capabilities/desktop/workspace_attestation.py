"""Verified C2 native-workspace capability attestations.

Electron main signs a compact, path-free statement with a fresh Ed25519 key
for every supervised boot.  The worker receives only the public key and must
verify the statement before an E2 workspace-commit cohort can claim that C2
is available.  A service bearer authenticates transport through the facade,
but it is *not* attestation proof: the private signing key never leaves
Electron main.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
import re
from threading import Lock
import time
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from pydantic import Field, StrictBool, StrictInt, ValidationError

from agent_runtime.execution.contracts import RuntimeContract


DESKTOP_WORKSPACE_ATTESTATION_VERSION = 1
DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV = (
    "DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY"
)
DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV = "DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD"
DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV = "DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE"

_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_BOOT_ID = re.compile(r"^dwa_[A-Za-z0-9_-]{16,128}$")
_MAX_ENVELOPE_BYTES = 4096
_MAX_ATTESTATION_TTL_MS = 10 * 60 * 1000
_MAX_FUTURE_SKEW_MS = 30 * 1000


class DesktopWorkspaceAttestationClaims(RuntimeContract):
    """Closed, path-free signed capability facts emitted by Electron main."""

    v: Literal[DESKTOP_WORKSPACE_ATTESTATION_VERSION]
    boot_id: str = Field(min_length=20, max_length=132)
    issued_at_ms: StrictInt = Field(ge=0)
    expires_at_ms: StrictInt = Field(ge=0)
    native_workspace_primitives: Literal["available", "unavailable"]
    unsafe_dev_workspace_tcb: StrictBool = False
    workspace_write_isolation: Literal["enforced", "unavailable"]


class DesktopWorkspaceAttestationEnvelope(RuntimeContract):
    """Compact signed envelope safe to carry through the facade."""

    payload: str = Field(min_length=1, max_length=_MAX_ENVELOPE_BYTES)
    signature: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True)
class VerifiedDesktopWorkspaceAttestation:
    """A cryptographically verified statement held only in process memory."""

    claims: DesktopWorkspaceAttestationClaims
    payload: str
    signature: str

    def supports_workspace_commit(self, *, now_ms: int) -> bool:
        """True only for the exact production-safe C2 capability pair."""

        return (
            self.claims.expires_at_ms >= now_ms
            and self.claims.workspace_write_isolation == "enforced"
            and self.claims.native_workspace_primitives == "available"
            # Development's explicit unsafe escape hatch is never launch
            # evidence, even when it is signed by Electron main.
            and self.claims.unsafe_dev_workspace_tcb is False
        )


class DesktopWorkspaceAttestationRegistry:
    """In-memory, fail-closed verifier and current-attestation reader.

    The registry has no filesystem, broker, or commit authority.  It answers
    one narrow startup/readiness question: did this boot's Electron main prove
    the native C2 prerequisites with a current, correctly signed statement?
    """

    def __init__(
        self,
        *,
        public_key: Ed25519PublicKey | None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._public_key = public_key
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._current: VerifiedDesktopWorkspaceAttestation | None = None
        self._lock = Lock()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> "DesktopWorkspaceAttestationRegistry":
        """Build from the supervised-child env and verify optional bootstrap.

        Missing, malformed, or partial bootstrap configuration intentionally
        yields an unattested registry rather than raising or inventing a
        permissive desktop default.
        """

        source = environ if environ is not None else os.environ
        registry = cls(
            public_key=cls._load_public_key(
                source.get(DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV, "")
            ),
            now_ms=now_ms,
        )
        payload = source.get(DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV, "")
        signature = source.get(DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV, "")
        if payload and signature:
            try:
                envelope = DesktopWorkspaceAttestationEnvelope(
                    payload=payload,
                    signature=signature,
                )
            except ValidationError:
                # Environment values are deployment input, not proof. A
                # malformed bootstrap must leave C2 unavailable rather than
                # preventing the desktop service from starting.
                return registry
            registry.submit(envelope)
        return registry

    @classmethod
    def from_public_key(
        cls,
        public_key: str,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> "DesktopWorkspaceAttestationRegistry":
        """Test/composition helper for an explicitly supplied public key."""

        return cls(public_key=cls._load_public_key(public_key), now_ms=now_ms)

    def submit(self, envelope: DesktopWorkspaceAttestationEnvelope) -> bool:
        """Verify and store a newer statement; invalid input changes nothing."""

        verified = self._verify(envelope)
        if verified is None:
            return False
        with self._lock:
            current = self._current
            if current is not None:
                # The same per-boot signing key may renew a statement, but a
                # stale/replayed statement must never replace a newer one.
                if current.claims.boot_id != verified.claims.boot_id:
                    return False
                if verified.claims.issued_at_ms < current.claims.issued_at_ms:
                    return False
            self._current = verified
        return True

    def workspace_commit_attested(self) -> bool:
        """Return the readiness fact consumed by E2's startup validator."""

        with self._lock:
            current = self._current
        return current is not None and current.supports_workspace_commit(
            now_ms=self._now_ms()
        )

    def _verify(
        self, envelope: DesktopWorkspaceAttestationEnvelope
    ) -> VerifiedDesktopWorkspaceAttestation | None:
        public_key = self._public_key
        if public_key is None:
            return None
        payload_bytes = _decode_base64url(envelope.payload)
        signature = _decode_base64url(envelope.signature)
        if (
            payload_bytes is None
            or signature is None
            or len(payload_bytes) > _MAX_ENVELOPE_BYTES
            or len(signature) != 64
        ):
            return None
        try:
            public_key.verify(signature, envelope.payload.encode("utf-8"))
        except InvalidSignature:
            return None
        try:
            decoded = json.loads(payload_bytes.decode("utf-8"))
            claims = DesktopWorkspaceAttestationClaims.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            return None
        if not _BOOT_ID.fullmatch(claims.boot_id):
            return None
        if canonical_claims_json(claims).encode("utf-8") != payload_bytes:
            return None
        now_ms = self._now_ms()
        if (
            claims.issued_at_ms > now_ms + _MAX_FUTURE_SKEW_MS
            or claims.expires_at_ms < now_ms
            or claims.expires_at_ms < claims.issued_at_ms
            or claims.expires_at_ms - claims.issued_at_ms > _MAX_ATTESTATION_TTL_MS
        ):
            return None
        return VerifiedDesktopWorkspaceAttestation(
            claims=claims,
            payload=envelope.payload,
            signature=envelope.signature,
        )

    @staticmethod
    def _load_public_key(value: str) -> Ed25519PublicKey | None:
        encoded = _decode_base64url(value)
        if encoded is None or len(encoded) > _MAX_ENVELOPE_BYTES:
            return None
        try:
            key = load_der_public_key(encoded)
        except (TypeError, ValueError):
            return None
        return key if isinstance(key, Ed25519PublicKey) else None


def canonical_claims_json(claims: DesktopWorkspaceAttestationClaims) -> str:
    """Canonical cross-language signature payload (see Electron main twin)."""

    return json.dumps(
        claims.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_base64url(value: str) -> bytes | None:
    if not value or not _BASE64URL.fullmatch(value):
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        return None


__all__ = (
    "DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV",
    "DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV",
    "DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV",
    "DESKTOP_WORKSPACE_ATTESTATION_VERSION",
    "DesktopWorkspaceAttestationClaims",
    "DesktopWorkspaceAttestationEnvelope",
    "DesktopWorkspaceAttestationRegistry",
    "VerifiedDesktopWorkspaceAttestation",
    "canonical_claims_json",
)
