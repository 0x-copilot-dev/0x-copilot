"""HTTP ingress tests for Electron-main C2 capability attestations."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from agent_runtime.capabilities.desktop.workspace_attestation import (
    DesktopWorkspaceAttestationClaims,
    DesktopWorkspaceAttestationEnvelope,
    canonical_claims_json,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory

_NOW = 1_700_000_000_000
_SERVICE_TOKEN = "desktop-main-host-token"
_BOOT_ID = "dwa_abcdefghijklmnopqrstuvwxyz123456"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signed_envelope(
    private_key: Ed25519PrivateKey,
) -> DesktopWorkspaceAttestationEnvelope:
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
    return DesktopWorkspaceAttestationEnvelope(
        payload=payload,
        signature=_b64(private_key.sign(payload.encode("utf-8"))),
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    public_key = _b64(
        private_key.public_key().public_bytes(
            Encoding.DER,
            PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("DESKTOP_WORKSPACE_ATTESTATION_PUBLIC_KEY", public_key)
    # Pin time in the registry composed by the app without weakening its
    # verifier: only this route test controls its fixture's signed timestamp.
    monkeypatch.setattr(
        "agent_runtime.capabilities.desktop.workspace_attestation.time.time",
        lambda: _NOW / 1000,
    )
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    app = RuntimeApiAppFactory.create_app(
        ports=RuntimeAdapterFactory.from_store(store), settings=settings
    )
    return TestClient(app), private_key


def test_valid_main_signed_attestation_reaches_runtime_registry(
    client: tuple[TestClient, Ed25519PrivateKey],
) -> None:
    test_client, private_key = client
    envelope = _signed_envelope(private_key)

    response = test_client.post(
        "/v1/agent/desktop-workspace-attestation",
        headers={"x-enterprise-service-token": _SERVICE_TOKEN},
        json=envelope.model_dump(mode="json"),
    )

    assert response.status_code == 204, response.text
    assert (
        test_client.app.state.desktop_workspace_attestation_registry.workspace_commit_attested()
        is True
    )


def test_missing_host_token_never_reaches_attestation_registry(
    client: tuple[TestClient, Ed25519PrivateKey],
) -> None:
    test_client, private_key = client

    response = test_client.post(
        "/v1/agent/desktop-workspace-attestation",
        json=_signed_envelope(private_key).model_dump(mode="json"),
    )

    assert response.status_code == 401
    assert (
        test_client.app.state.desktop_workspace_attestation_registry.workspace_commit_attested()
        is False
    )


def test_tampered_envelope_is_rejected_without_changing_readiness(
    client: tuple[TestClient, Ed25519PrivateKey],
) -> None:
    test_client, private_key = client
    envelope = _signed_envelope(private_key)
    tampered = {
        "payload": f"{envelope.payload[:-1]}{'A' if envelope.payload[-1] != 'A' else 'B'}",
        "signature": envelope.signature,
    }

    response = test_client.post(
        "/v1/agent/desktop-workspace-attestation",
        headers={"x-enterprise-service-token": _SERVICE_TOKEN},
        json=tampered,
    )

    assert response.status_code == 422
    assert (
        test_client.app.state.desktop_workspace_attestation_registry.workspace_commit_attested()
        is False
    )


def test_renderer_supplied_path_field_is_rejected_without_reaching_c2_state(
    client: tuple[TestClient, Ed25519PrivateKey],
) -> None:
    test_client, private_key = client
    body = _signed_envelope(private_key).model_dump(mode="json")
    body["path"] = "/Users/alice/private-project"

    response = test_client.post(
        "/v1/agent/desktop-workspace-attestation",
        headers={"x-enterprise-service-token": _SERVICE_TOKEN},
        json=body,
    )

    # The shared request-validation mapper intentionally turns a malformed
    # public body into 400 before the route handler sees it.
    assert response.status_code == 400
    assert (
        test_client.app.state.desktop_workspace_attestation_registry.workspace_commit_attested()
        is False
    )
