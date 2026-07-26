"""Authorization and composition tests for the internal E2 migration route."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


ORG = "org_e2_route"
USER = "user_e2_route"
TOKEN = "e2-control-plane-token"


class _ClientMixin:
    def _client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        app = RuntimeApiAppFactory.create_app(
            ports=RuntimeAdapterFactory.from_store(store),
            settings=settings,
        )
        return TestClient(app), store

    @staticmethod
    def _headers(*, org_id: str = ORG, token: str = TOKEN) -> dict[str, str]:
        return {
            "x-enterprise-service-token": token,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": USER,
        }


class TestLegacyMigrationRouteAuthorization(_ClientMixin):
    def test_invalid_service_token_cannot_inventory_a_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        client, store = self._client()

        response = client.post(
            "/internal/v1/admin/e2/legacy-migrations/e2_cohort_route",
            headers=self._headers(token="wrong"),
            json={"org_id": ORG},
        )

        assert response.status_code == 401
        assert store.audit_log == []

    def test_trusted_identity_must_match_the_body_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        client, store = self._client()

        response = client.post(
            "/internal/v1/admin/e2/legacy-migrations/e2_cohort_route",
            headers=self._headers(org_id="org_other"),
            json={"org_id": ORG},
        )

        assert response.status_code == 403
        assert store.audit_log == []

    def test_dry_run_is_internal_authorized_and_audited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        client, store = self._client()

        response = client.post(
            "/internal/v1/admin/e2/legacy-migrations/e2_cohort_route",
            headers=self._headers(),
            json={"org_id": ORG, "dry_run": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["org_id"] == ORG
        assert body["dry_run"] is True
        assert body["cohort_ready"] is False
        assert body["migration_status"] == "dry_run"
        assert len(store.audit_log) == 1
        event_type, record = store.audit_log[0]
        assert event_type == "e2_legacy_migration_reported"
        assert record["org_id"] == ORG
        assert "content_text" not in repr(record)
