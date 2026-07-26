"""Adversarial unit tests for the Electron-main C2 attestation verifier."""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent_runtime.capabilities.desktop.workspace_attestation import (
    DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV,
    DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV,
    DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV,
    DesktopWorkspaceAttestationClaims,
    DesktopWorkspaceAttestationEnvelope,
    DesktopWorkspaceAttestationRegistry,
    canonical_claims_json,
)

_NOW = 1_700_000_000_000
_BOOT_ID = "dwa_abcdefghijklmnopqrstuvwxyz123456"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _registry() -> tuple[DesktopWorkspaceAttestationRegistry, Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = _b64(
        private_key.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return (
        DesktopWorkspaceAttestationRegistry.from_public_key(
            public_key,
            now_ms=lambda: _NOW,
        ),
        private_key,
        public_key,
    )


def _envelope(
    private_key: Ed25519PrivateKey,
    *,
    isolation: str = "enforced",
    primitives: str = "available",
    unsafe_dev: bool = False,
    issued_at_ms: int = _NOW,
    expires_at_ms: int = _NOW + 60_000,
) -> DesktopWorkspaceAttestationEnvelope:
    claims = DesktopWorkspaceAttestationClaims.model_validate(
        {
            "v": 1,
            "boot_id": _BOOT_ID,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "native_workspace_primitives": primitives,
            "unsafe_dev_workspace_tcb": unsafe_dev,
            "workspace_write_isolation": isolation,
        }
    )
    payload = _b64(canonical_claims_json(claims).encode("utf-8"))
    return DesktopWorkspaceAttestationEnvelope(
        payload=payload,
        signature=_b64(private_key.sign(payload.encode("utf-8"))),
    )


def test_valid_signed_native_attestation_enables_only_c2_readiness() -> None:
    registry, private_key, _public_key = _registry()

    assert registry.submit(_envelope(private_key)) is True
    assert registry.workspace_commit_attested() is True


def test_missing_attestation_stays_fail_closed() -> None:
    registry = DesktopWorkspaceAttestationRegistry.from_environment(
        environ={}, now_ms=lambda: _NOW
    )

    assert registry.workspace_commit_attested() is False


def test_tampered_payload_is_rejected_and_cannot_enable_workspace_commit() -> None:
    registry, private_key, _public_key = _registry()
    signed = _envelope(private_key)
    tampered = DesktopWorkspaceAttestationEnvelope(
        payload=f"{signed.payload[:-1]}{'A' if signed.payload[-1] != 'A' else 'B'}",
        signature=signed.signature,
    )

    assert registry.submit(tampered) is False
    assert registry.workspace_commit_attested() is False


def test_unavailable_or_unsafe_main_statement_never_counts_as_launch_evidence() -> None:
    registry, private_key, _public_key = _registry()

    assert (
        registry.submit(
            _envelope(
                private_key,
                isolation="unavailable",
                primitives="unavailable",
                unsafe_dev=True,
            )
        )
        is True
    )
    assert registry.workspace_commit_attested() is False


def test_expired_or_overlong_attestation_is_rejected() -> None:
    registry, private_key, _public_key = _registry()

    assert (
        registry.submit(
            _envelope(
                private_key,
                issued_at_ms=_NOW - 120_000,
                expires_at_ms=_NOW - 1,
            )
        )
        is False
    )
    assert (
        registry.submit(
            _envelope(
                private_key,
                issued_at_ms=_NOW,
                expires_at_ms=_NOW + 11 * 60_000,
            )
        )
        is False
    )
    assert registry.workspace_commit_attested() is False


def test_env_bootstrap_verifies_same_signed_contract() -> None:
    _registry_value, private_key, public_key = _registry()
    envelope = _envelope(private_key)

    registry = DesktopWorkspaceAttestationRegistry.from_environment(
        environ={
            DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV: public_key,
            DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV: envelope.payload,
            DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV: envelope.signature,
        },
        now_ms=lambda: _NOW,
    )

    assert registry.workspace_commit_attested() is True


def test_malformed_environment_bootstrap_stays_unattested_without_crashing() -> None:
    _registry_value, _private_key, public_key = _registry()

    registry = DesktopWorkspaceAttestationRegistry.from_environment(
        environ={
            DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY_ENV: public_key,
            DESKTOP_WORKSPACE_ATTESTATION_PAYLOAD_ENV: "a" * 4_097,
            DESKTOP_WORKSPACE_ATTESTATION_SIGNATURE_ENV: "b" * 86,
        },
        now_ms=lambda: _NOW,
    )

    assert registry.workspace_commit_attested() is False


def test_even_a_signed_claim_with_a_host_path_is_not_a_c2_attestation() -> None:
    registry, private_key, _public_key = _registry()
    claims = {
        "boot_id": _BOOT_ID,
        "expires_at_ms": _NOW + 60_000,
        "host_path": "/Users/alice/private-project",
        "issued_at_ms": _NOW,
        "native_workspace_primitives": "available",
        "unsafe_dev_workspace_tcb": False,
        "v": 1,
        "workspace_write_isolation": "enforced",
    }
    payload = _b64(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    envelope = DesktopWorkspaceAttestationEnvelope(
        payload=payload,
        signature=_b64(private_key.sign(payload.encode("utf-8"))),
    )

    assert registry.submit(envelope) is False
    assert registry.workspace_commit_attested() is False
