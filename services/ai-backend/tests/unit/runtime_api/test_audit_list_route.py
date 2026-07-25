"""PR 7.1 — internal audit list route for the runtime audit log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


class _AuthorizationMetricSpy:
    def __init__(self) -> None:
        self.denials: list[dict[str, str]] = []

    def record_authorization_denial(
        self,
        *,
        boundary: str,
        reason: str,
        enforcement: str,
    ) -> None:
        self.denials.append(
            {
                "boundary": boundary,
                "reason": reason,
                "enforcement": enforcement,
            }
        )


@pytest.fixture(autouse=True)
def _audit_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_HMAC_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("AUDIT_HMAC_KEY_VERSION", "1")
    # Enforce RBAC so the 403 path is exercised end-to-end in tests.
    monkeypatch.setenv("RBAC_MODE", "enforce")


async def _seed_audit(
    store: InMemoryRuntimeApiStore,
    *,
    org_id: str,
    user_id: str,
    action: str,
    when: datetime,
) -> None:
    await store.write_audit_log(
        event_type="audit",
        record={
            "audit_id": f"audit-{action}-{when.isoformat()}",
            "org_id": org_id,
            "user_id": user_id,
            "actor_type": "user",
            "action": action,
            "resource_type": "conversation",
            "resource_id": "conv-1",
            "outcome": "success",
            "metadata": {"reason": "test"},
            "created_at": when.isoformat(),
        },
    )


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


class TestAuditListRoute:
    async def test_returns_audit_rows_newest_first(self) -> None:
        client, store = _client()
        now = datetime.now(timezone.utc)
        await _seed_audit(
            store,
            org_id="org_a",
            user_id="user_1",
            action="approval.created",
            when=now - timedelta(seconds=2),
        )
        await _seed_audit(
            store,
            org_id="org_a",
            user_id="user_1",
            action="approval.decided",
            when=now - timedelta(seconds=1),
        )
        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_a", "user_id": "user_1"},
            headers={
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "user_1",
                "x-enterprise-permission-scopes": "admin:audit_export",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["rows"]) == 2
        # Newest first.
        assert body["rows"][0]["action"] == "approval.decided"
        assert body["rows"][1]["action"] == "approval.created"
        assert body["rows"][0]["chain"]["seq"] is not None

    async def test_filter_action_prefix(self) -> None:
        client, store = _client()
        now = datetime.now(timezone.utc)
        await _seed_audit(
            store,
            org_id="org_a",
            user_id="user_1",
            action="approval.decided",
            when=now,
        )
        await _seed_audit(
            store,
            org_id="org_a",
            user_id="user_1",
            action="conversation.delete",
            when=now,
        )
        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_a", "user_id": "user_1", "action": "approval."},
            headers={
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "user_1",
                "x-enterprise-permission-scopes": "admin:audit_export",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["action"] == "approval.decided"

    def test_403_without_scope(self) -> None:
        client, _ = _client()
        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_a", "user_id": "user_1"},
            headers={
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "user_1",
                "x-enterprise-permission-scopes": "runtime:use",
            },
        )
        assert response.status_code == 403

    def test_identity_mismatch_emits_closed_authorization_metric(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import runtime_api.http.audit_list_routes as audit_list_routes

        spy = _AuthorizationMetricSpy()
        monkeypatch.setattr(
            audit_list_routes,
            "get_lifecycle_operational_metrics",
            lambda: spy,
        )
        client, _ = _client()

        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_other", "user_id": "user_1"},
            headers={
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "user_1",
                "x-enterprise-permission-scopes": "admin:audit_export",
            },
        )

        assert response.status_code == 403
        assert spy.denials == [
            {
                "boundary": "audit_list_identity",
                "reason": "identity_mismatch",
                "enforcement": "enforce",
            }
        ]

    def test_invalid_cursor_400(self) -> None:
        client, _ = _client()
        response = client.get(
            "/internal/v1/audit/list",
            params={
                "org_id": "org_a",
                "user_id": "user_1",
                "cursor": "not-base64",
            },
            headers={
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "user_1",
                "x-enterprise-permission-scopes": "admin:audit_export",
            },
        )
        assert response.status_code == 400
