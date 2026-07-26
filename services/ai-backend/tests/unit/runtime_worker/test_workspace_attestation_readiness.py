"""C2 proof wiring tests at the worker's E2 readiness boundary."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from agent_runtime.capabilities.desktop.workspace_attestation import (
    DesktopWorkspaceAttestationClaims,
    DesktopWorkspaceAttestationEnvelope,
    DesktopWorkspaceAttestationRegistry,
    canonical_claims_json,
)
from agent_runtime.rollout import RolloutStartupReadiness, RolloutStartupValidator
from agent_runtime.settings import RuntimeSettings
from runtime_worker.loop import RuntimeWorker


_NOW = 1_700_000_000_000
_BOOT_ID = "dwa_abcdefghijklmnopqrstuvwxyz123456"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _registry(*, valid: bool) -> DesktopWorkspaceAttestationRegistry:
    private_key = Ed25519PrivateKey.generate()
    public_key = _b64(
        private_key.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
    )
    registry = DesktopWorkspaceAttestationRegistry.from_public_key(
        public_key,
        now_ms=lambda: _NOW,
    )
    claims = DesktopWorkspaceAttestationClaims(
        v=1,
        boot_id=_BOOT_ID,
        issued_at_ms=_NOW,
        expires_at_ms=_NOW + 60_000,
        native_workspace_primitives="available",
        unsafe_dev_workspace_tcb=False,
        workspace_write_isolation="enforced",
    )
    payload = _b64(canonical_claims_json(claims).encode("utf-8"))
    signature = _b64(private_key.sign(payload.encode("utf-8")))
    if not valid:
        payload = f"{payload[:-1]}{'A' if payload[-1] != 'A' else 'B'}"
    assert (
        registry.submit(
            DesktopWorkspaceAttestationEnvelope(payload=payload, signature=signature)
        )
        is valid
    )
    return registry


def _worker_target(
    registry: DesktopWorkspaceAttestationRegistry,
) -> RuntimeWorker:
    """Build only the fields consumed by the startup-readiness method."""

    worker = object.__new__(RuntimeWorker)
    worker.settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    worker.artifact_service = None
    worker.workspace_overlay_store = object()
    worker.workspace_host_sessions = object()
    worker.workspace_attestation_registry = registry
    return worker


@pytest.mark.parametrize("valid", [True, False])
def test_worker_readiness_uses_verified_c2_registry(
    monkeypatch: pytest.MonkeyPatch,
    valid: bool,
) -> None:
    """The C2 fact reaches the exact readiness object consumed by E2."""

    captured: list[RolloutStartupReadiness] = []

    def capture(_resolution: object, *, readiness: RolloutStartupReadiness) -> None:
        captured.append(readiness)

    monkeypatch.setattr(
        RolloutStartupValidator,
        "validate_startup",
        staticmethod(capture),
    )
    worker = _worker_target(_registry(valid=valid))

    RuntimeWorker._validate_e2_rollout_startup(
        worker,
        d1_ready=True,
        artifact_blob_store=object(),
        artifact_reference_store=object(),
    )

    assert len(captured) == 1
    assert captured[0].workspace_c2_native_attested is valid


def test_worker_readiness_stays_false_when_attestation_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted child bootstrap never becomes C2 launch evidence."""

    captured: list[RolloutStartupReadiness] = []
    monkeypatch.setattr(
        RolloutStartupValidator,
        "validate_startup",
        staticmethod(lambda _resolution, *, readiness: captured.append(readiness)),
    )
    worker = _worker_target(
        DesktopWorkspaceAttestationRegistry.from_environment(
            environ={}, now_ms=lambda: _NOW
        )
    )

    RuntimeWorker._validate_e2_rollout_startup(
        worker,
        d1_ready=True,
        artifact_blob_store=object(),
        artifact_reference_store=object(),
    )

    assert captured[0].workspace_c2_native_attested is False
