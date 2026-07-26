"""Authorization and composition tests for the internal E2 migration route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeApiEventType


ORG = "org_e2_route"
USER = "user_e2_route"
TOKEN = "e2-control-plane-token"
JOB_TOKEN = "e2-sealed-stage-job-token"


def _seed_legacy_stage(
    store: InMemoryRuntimeApiStore, *, org_id: str, run_id: str
) -> None:
    user_id = "user_e2_route"
    store.runs[run_id] = RunRecord(
        run_id=run_id,
        conversation_id=f"conv_{run_id}",
        org_id=org_id,
        user_id=user_id,
        user_message_id=f"msg_{run_id}",
        trace_id=f"trace_{run_id}",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            org_id=org_id,
            run_id=run_id,
            trace_id=f"trace_{run_id}",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )
    store.events_by_run[run_id] = [
        SimpleNamespace(
            event_id=f"event_{run_id}",
            event_type=RuntimeApiEventType.WRITE_STAGED,
            sequence_no=1,
            payload={
                "stage_id": "legacy_route_stage",
                "surface_id": "surface_route_stage",
                "target": {"connector": "linear", "op": "create_issue"},
                "proposal_ref": "draft://draft_route/v1",
            },
        )
    ]


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

    @classmethod
    def _stage_headers(cls, *, org_id: str = ORG) -> dict[str, str]:
        return {
            **cls._headers(org_id=org_id),
            "x-e2-migration-job-token": JOB_TOKEN,
            "x-e2-migration-capability": "e2_legacy_stage_materialization_v1",
            "x-e2-migration-job-id": "job_e2_stage_test",
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


class TestLegacyStageMigrationRouteAuthorization(_ClientMixin):
    def test_generic_service_identity_cannot_run_stage_migration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        monkeypatch.setenv("E2_LEGACY_STAGE_MIGRATION_JOB_TOKEN", JOB_TOKEN)
        client, store = self._client()

        response = client.post(
            "/internal/v1/admin/e2/legacy-stage-migrations/e2_stage_route",
            headers=self._headers(),
            json={"org_id": ORG, "dry_run": False},
        )

        assert response.status_code == 403
        assert store.audit_log == []

    def test_stage_control_plane_requires_matching_trusted_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        monkeypatch.setenv("E2_LEGACY_STAGE_MIGRATION_JOB_TOKEN", JOB_TOKEN)
        client, store = self._client()

        response = client.post(
            "/internal/v1/admin/e2/legacy-stage-migrations/e2_stage_route",
            headers=self._stage_headers(org_id="org_other"),
            json={"org_id": ORG, "dry_run": False},
        )

        assert response.status_code == 403
        assert store.audit_log == []

    def test_stage_control_plane_is_composed_and_tenant_scoped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", TOKEN)
        monkeypatch.setenv("E2_LEGACY_STAGE_MIGRATION_JOB_TOKEN", JOB_TOKEN)
        client, store = self._client()

        _seed_legacy_stage(store, org_id=ORG, run_id="run_e2_route")
        _seed_legacy_stage(store, org_id="org_foreign", run_id="run_foreign")
        response = client.post(
            "/internal/v1/admin/e2/legacy-stage-migrations/e2_stage_route",
            headers=self._stage_headers(),
            json={"org_id": ORG, "dry_run": False, "batch_size": 5},
        )

        assert response.status_code == 200
        body = response.json()
        # Existing old staged writes do not contain the immutable universal
        # target/argument bundle. The *real* composed path therefore reports
        # one audit-visible quarantine rather than fabricating a canonical
        # stage or quietly skipping it; the foreign tenant is not scanned.
        assert body["scanned"] == 1
        assert body["quarantined"] == 1
        assert store.effect_commit_commands == []
        assert store.audit_log[-1][0] == "e2_legacy_stage_migration_recorded"
        assert store.audit_log[-1][1]["actor"] == {
            "operator_ref": f"principal://users/{USER}",
            "migration_job_id": "job_e2_stage_test",
        }
