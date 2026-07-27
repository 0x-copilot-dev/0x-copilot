"""Verification-only runtime support for signed harness release manifests."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_runtime.harness_quality.evaluation_contracts import HarnessManifest
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)


class ReleaseManifestVerificationError(RuntimeError):
    """A signed release manifest failed a fail-closed verification check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedHarnessManifest:
    """Transient verification result; signature presence alone is insufficient."""

    manifest: HarnessManifest
    verified_at: datetime
    verification_digest: str


class ReleaseManifestVerifier:
    """Verify canonical Ed25519 envelopes with public keys only.

    This runtime class intentionally has no private-key input and exposes no
    signing or promotion operation. Production builds can consume externally
    signed manifests but cannot mint a release.
    """

    def __init__(
        self,
        *,
        verification_keys: Mapping[str, Ed25519PublicKey | bytes],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._keys = {
            key_id: self._coerce_public_key(key)
            for key_id, key in verification_keys.items()
        }
        self._clock = clock

    def verify(self, manifest: HarnessManifest) -> VerifiedHarnessManifest:
        """Verify key identity, validity window, digest, and signature."""

        key = self._keys.get(manifest.key_id)
        if key is None:
            raise ReleaseManifestVerificationError("manifest_key_unknown")
        now = self._clock()
        if not self._is_aware(now):
            raise ReleaseManifestVerificationError("verification_clock_not_utc_aware")
        if not self._is_aware(manifest.issued_at):
            raise ReleaseManifestVerificationError("manifest_issued_at_not_utc_aware")
        if not self._is_aware(manifest.not_before):
            raise ReleaseManifestVerificationError("manifest_not_before_not_utc_aware")
        if manifest.expires_at is not None and not self._is_aware(manifest.expires_at):
            raise ReleaseManifestVerificationError("manifest_expires_at_not_utc_aware")
        if now < manifest.not_before:
            raise ReleaseManifestVerificationError("manifest_not_yet_valid")
        if manifest.expires_at is not None and now >= manifest.expires_at:
            raise ReleaseManifestVerificationError("manifest_expired")
        if manifest.issued_at > now:
            raise ReleaseManifestVerificationError("manifest_issued_in_future")

        payload = manifest.signed_payload()
        if canonical_json_sha256(payload) != manifest.payload_digest:
            # Pydantic validates this too; retaining the check makes the
            # cryptographic boundary fail closed if construction changes.
            raise ReleaseManifestVerificationError("manifest_payload_digest_invalid")
        signature = self._decode_signature(manifest.signature_b64)
        try:
            key.verify(signature, canonical_json_bytes(payload))
        except InvalidSignature as exc:
            raise ReleaseManifestVerificationError(
                "manifest_signature_invalid"
            ) from exc

        verification_digest = canonical_json_sha256(
            {
                "key_id": manifest.key_id,
                "manifest_ref": manifest.manifest_ref,
                "payload_digest": manifest.payload_digest,
                "signature_b64": manifest.signature_b64,
            }
        )
        return VerifiedHarnessManifest(
            manifest=manifest,
            verified_at=now,
            verification_digest=verification_digest,
        )

    @staticmethod
    def _coerce_public_key(key: Ed25519PublicKey | bytes) -> Ed25519PublicKey:
        if isinstance(key, Ed25519PublicKey):
            return key
        try:
            return Ed25519PublicKey.from_public_bytes(bytes(key))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Ed25519 verification key") from exc

    @staticmethod
    def _decode_signature(value: str) -> bytes:
        try:
            signature = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ReleaseManifestVerificationError(
                "manifest_signature_encoding_invalid"
            ) from exc
        if len(signature) != 64:
            raise ReleaseManifestVerificationError("manifest_signature_length_invalid")
        if base64.b64encode(signature).decode("ascii") != value:
            raise ReleaseManifestVerificationError(
                "manifest_signature_encoding_noncanonical"
            )
        return signature

    @staticmethod
    def _is_aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "ReleaseManifestVerificationError",
    "ReleaseManifestVerifier",
    "VerifiedHarnessManifest",
)
