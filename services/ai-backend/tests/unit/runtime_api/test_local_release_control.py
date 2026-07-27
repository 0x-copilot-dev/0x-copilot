from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationScope,
    HarnessManifest,
)
from agent_runtime.release.control import LocalReleaseControlService
from agent_runtime.release.local_control import (
    LocalReleaseControlPolicy,
    ReleaseControlProfile,
)
from agent_runtime.release.manifest import ReleaseManifestVerifier
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.local_release_control import LocalReleaseControlRouter


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
_TOKEN = "local-release-control-token"


def _manifest(
    private_key: Ed25519PrivateKey,
    *,
    now: datetime = _NOW,
) -> HarnessManifest:
    payload: dict[str, object] = {
        "schema_version": 1,
        "manifest_id": "manifest-local-1",
        "revision": "release-r1",
        "assignments": [
            {
                "variant_ref": "harness://candidate-r1",
                "variant_digest": "a" * 64,
                "allocation_basis_points": 5_000,
            },
            {
                "variant_ref": "harness://control-r1",
                "variant_digest": "b" * 64,
                "allocation_basis_points": 5_000,
            },
        ],
        "fallback_variant_ref": "harness://control-r1",
        "assignment_revision": "assignment-r1",
        "source_report_ref": "paired-report://report-r1",
        "previous_manifest_ref": None,
        "issued_at": (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "not_before": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "key_id": "release-key-1",
        "signature_algorithm": "ed25519",
    }
    digest = canonical_json_sha256(payload)
    return HarnessManifest(
        **payload,
        payload_digest=digest,
        signature_b64=base64.b64encode(
            private_key.sign(canonical_json_bytes(payload))
        ).decode("ascii"),
    )


def _app(private_key: Ed25519PrivateKey) -> FastAPI:
    app = FastAPI()
    app.state.local_release_control_service = LocalReleaseControlService(
        repository=InMemoryEvaluationRepository(),
        verifier=ReleaseManifestVerifier(
            verification_keys={"release-key-1": private_key.public_key()},
            clock=lambda: _NOW,
        ),
        policy=LocalReleaseControlPolicy(
            profile=ReleaseControlProfile.DEVELOPMENT,
            explicitly_enabled=True,
            bind_host="127.0.0.1",
        ),
        scope=EvaluationScope(
            profile_id="desktop-local",
            project_id="project-1",
        ),
    )
    app.include_router(LocalReleaseControlRouter.create_router())
    return app


def _release_config(path, private_key: Ed25519PrivateKey) -> None:
    public_key = private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_profile": "development",
                "verification_keys": [
                    {
                        "key_id": "release-key-1",
                        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
                    }
                ],
                "assignments": [],
                "development_override": None,
            }
        ),
        encoding="utf-8",
    )


def test_loopback_service_token_can_verify_install_and_export(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    private_key = Ed25519PrivateKey.generate()
    manifest = _manifest(private_key)
    client = TestClient(_app(private_key), client=("127.0.0.1", 51_000))
    headers = {"x-enterprise-service-token": _TOKEN}

    verified = client.post(
        "/internal/dev/evaluation/releases/verify",
        headers=headers,
        json=manifest.model_dump(mode="json"),
    )
    installed = client.post(
        "/internal/dev/evaluation/releases/install",
        headers=headers,
        json={
            "manifest": manifest.model_dump(mode="json"),
            "activation_decision_id": "local-install-1",
        },
    )
    exported = client.post(
        "/internal/dev/evaluation/releases/export",
        headers=headers,
    )

    assert verified.status_code == 200, verified.text
    assert verified.json()["manifest_ref"] == manifest.manifest_ref
    assert installed.status_code == 200, installed.text
    assert installed.json()["manifest_payload_digest"] == manifest.payload_digest
    assert installed.headers["x-runtime-restart-required"] == "true"
    assert exported.status_code == 200, exported.text
    assert len(exported.headers["x-content-sha256"]) == 64
    assert manifest.manifest_id.encode("utf-8") in exported.content


def test_non_loopback_peer_is_forbidden_even_with_service_token(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    private_key = Ed25519PrivateKey.generate()
    client = TestClient(_app(private_key), client=("203.0.113.9", 51_000))

    response = client.post(
        "/internal/dev/evaluation/releases/verify",
        headers={"x-enterprise-service-token": _TOKEN},
        json=_manifest(
            private_key,
            now=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )

    assert response.status_code == 403


def test_missing_service_token_is_rejected_before_local_control(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    private_key = Ed25519PrivateKey.generate()
    client = TestClient(_app(private_key), client=("127.0.0.1", 51_000))

    response = client.post(
        "/internal/dev/evaluation/releases/verify",
        json=_manifest(private_key).model_dump(mode="json"),
    )

    assert response.status_code == 401


def test_full_app_omits_route_while_local_control_is_dark(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "development",
            "RUNTIME_START_IN_PROCESS_WORKER": "false",
        }
    )
    app = RuntimeApiAppFactory.create_app(
        ports=RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore()),
        settings=settings,
    )

    response = TestClient(
        app,
        client=("127.0.0.1", 51_000),
    ).post("/internal/dev/evaluation/releases/verify", json={})
    diagnostics = TestClient(
        app,
        client=("127.0.0.1", 51_000),
    ).get("/internal/dev/evaluation/diagnostics/snapshot")

    assert response.status_code == 404
    assert diagnostics.status_code == 404


def test_full_app_mounts_route_only_with_explicit_local_configuration(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    private_key = Ed25519PrivateKey.generate()
    config_path = tmp_path / "release.json"
    _release_config(config_path, private_key)
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "development",
            "RUNTIME_START_IN_PROCESS_WORKER": "false",
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": str(config_path),
            "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
        }
    )
    app = RuntimeApiAppFactory.create_app(
        ports=RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore()),
        settings=settings,
    )
    client = TestClient(app, client=("127.0.0.1", 51_000))

    response = client.post(
        "/internal/dev/evaluation/releases/verify",
        headers={"x-enterprise-service-token": _TOKEN},
        json=_manifest(
            private_key,
            now=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )
    diagnostics = client.get(
        "/internal/dev/evaluation/diagnostics/snapshot",
        headers={"x-enterprise-service-token": _TOKEN},
    )

    assert response.status_code == 200, response.text
    assert diagnostics.status_code == 200, diagnostics.text
