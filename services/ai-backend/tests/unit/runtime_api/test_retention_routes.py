"""HTTP route tests for the C8 ``/v1/retention/*`` admin endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


def _client() -> tuple[TestClient, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    return TestClient(
        RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
    ), store


class TestRetentionAdminRoutes:
    def test_post_then_list(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json={
                "scope": "org",
                "kind": "messages",
                "ttl_seconds": 86400,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "org"
        assert body["kind"] == "messages"
        assert body["ttl_seconds"] == 86400
        assert body["created_by_user_id"] == "user_admin"

        listing = client.get(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        assert listing.status_code == 200
        rows = listing.json()["policies"]
        assert len(rows) == 1
        assert rows[0]["id"] == body["id"]

    def test_post_org_scope_with_resource_id_rejected(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json={
                "scope": "org",
                "kind": "events",
                "ttl_seconds": 3600,
                "resource_id": "conv_x",
            },
        )
        assert response.status_code == 400

    def test_post_conversation_scope_requires_resource_id(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json={
                "scope": "conversation",
                "kind": "messages",
                "ttl_seconds": 3600,
            },
        )
        assert response.status_code == 400

    def test_upsert_is_idempotent_per_key(self) -> None:
        client, store = _client()
        body = {"scope": "org", "kind": "messages", "ttl_seconds": 86400}
        client.post(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json=body,
        )
        # Second POST with same shape replaces the first row instead of
        # creating a duplicate (in-memory store enforces the same uniqueness
        # the SQL unique index does in prod).
        client.post(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json={**body, "ttl_seconds": 7200},
        )
        listing = client.get(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        rows = listing.json()["policies"]
        assert len(rows) == 1
        assert rows[0]["ttl_seconds"] == 7200

    def test_delete_removes_policy(self) -> None:
        client, store = _client()
        record = RetentionPolicyRecord(
            org_id="org_a",
            scope=RetentionScope.ORG,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=86400,
        )
        store.retention_policies = {"org_a": (record,)}
        response = client.delete(
            f"/v1/retention/policies/{record.id}",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        listing = client.get(
            "/v1/retention/policies",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        assert listing.json()["policies"] == []
