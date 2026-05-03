from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

from backend_app.app import create_app
from backend_app.service import DeployAuditService
from backend_app.store import InMemoryDeployAuditStore


SERVICE_TOKEN = "secret-service-token-for-tests"
SHA256_BACKEND = "sha256:" + "a" * 64
SHA256_FACADE = "sha256:" + "b" * 64
SHA256_AI = "sha256:" + "c" * 64
SHA256_FRONTEND = "sha256:" + "d" * 64


def _payload(**overrides):
    body = {
        "tenant_id": "acme-corp",
        "environment": "production",
        "release_sha": "abc1234567890abcdef",
        "image_digests": [
            {"component": "enterprise-search-backend", "digest": SHA256_BACKEND},
            {"component": "enterprise-search-backend-facade", "digest": SHA256_FACADE},
            {"component": "enterprise-search-ai-backend", "digest": SHA256_AI},
            {"component": "enterprise-search-frontend", "digest": SHA256_FRONTEND},
        ],
        "approver": "alice",
        "workflow_run_url": "https://github.com/example/repo/actions/runs/12345",
        "started_at": "2026-05-03T18:00:00+00:00",
        "completed_at": "2026-05-03T18:04:12+00:00",
        "outcome": "success",
        "force_deploy": False,
    }
    body.update(overrides)
    return body


@pytest.fixture
def store() -> InMemoryDeployAuditStore:
    return InMemoryDeployAuditStore()


@pytest.fixture
def client(store) -> TestClient:
    app = create_app(deploy_audit_service=DeployAuditService(store=store))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def production_env(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")


def test_records_deploy_event_with_valid_service_token(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy", json=_payload(), headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "audit_id" in body and len(body["audit_id"]) == 32
    assert "received_at" in body

    assert len(store.audit_events) == 1
    event = store.audit_events[0]
    assert event.tenant_id == "acme-corp"
    assert event.org_id == "acme-corp"
    assert event.user_id == "ci:alice"
    assert event.actor_kind == "ci"
    assert event.outcome == "success"
    assert len(event.image_digests) == 4


def test_rejects_missing_service_token_in_production(client, production_env):
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(),
        headers={ORG_HEADER: "acme-corp", USER_HEADER: "ci:alice"},
    )
    assert response.status_code == 401


def test_rejects_invalid_service_token(client, production_env):
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(),
        headers={
            SERVICE_TOKEN_HEADER: "wrong",
            ORG_HEADER: "acme-corp",
            USER_HEADER: "ci:alice",
        },
    )
    assert response.status_code == 401


def test_rejects_missing_org_header(client, production_env):
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(),
        headers={SERVICE_TOKEN_HEADER: SERVICE_TOKEN, USER_HEADER: "ci:alice"},
    )
    assert response.status_code == 401


def test_rejects_tenant_id_mismatch_with_verified_org(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "different-tenant",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(tenant_id="acme-corp"),
        headers=headers,
    )
    assert response.status_code == 400
    assert "tenant_id" in response.text
    assert store.audit_events == []


def test_rejects_invalid_digest_shape(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    bad_payload = _payload()
    bad_payload["image_digests"][0]["digest"] = "not-a-sha256"
    response = client.post(
        "/internal/v1/audit/deploy", json=bad_payload, headers=headers
    )
    assert response.status_code == 422
    assert store.audit_events == []


def test_rejects_unknown_environment(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(environment="canary"),
        headers=headers,
    )
    assert response.status_code == 422
    assert store.audit_events == []


def test_rejects_unknown_outcome(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(outcome="kinda-worked"),
        headers=headers,
    )
    assert response.status_code == 422


def test_rejects_workflow_run_url_without_scheme(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(workflow_run_url="github.com/example/repo/actions/runs/12345"),
        headers=headers,
    )
    assert response.status_code == 422


def test_dev_mode_uses_body_identity_when_token_unset(client, store, monkeypatch):
    monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
    response = client.post("/internal/v1/audit/deploy", json=_payload())
    assert response.status_code == 201, response.text
    assert len(store.audit_events) == 1
    assert store.audit_events[0].tenant_id == "acme-corp"


def test_force_deploy_flag_persists(client, store, production_env):
    headers = {
        SERVICE_TOKEN_HEADER: SERVICE_TOKEN,
        ORG_HEADER: "acme-corp",
        USER_HEADER: "ci:alice",
    }
    response = client.post(
        "/internal/v1/audit/deploy",
        json=_payload(force_deploy=True),
        headers=headers,
    )
    assert response.status_code == 201
    assert store.audit_events[0].force_deploy is True
