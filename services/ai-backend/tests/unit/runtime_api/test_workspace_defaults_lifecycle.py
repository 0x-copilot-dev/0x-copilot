"""PR 1.6 — workspace defaults + conversation lifecycle.

Covers:

  * GET /v1/agent/workspace/defaults  — deployment-fallback when no row.
  * PUT /v1/agent/workspace/defaults  — admin-only; composes defaults
    upsert + ``retention_policies`` rows + one audit row.
  * Create-conversation seeds ``enabled_connectors`` from defaults.
  * PATCH /v1/agent/conversations/{id}  — title/folder/archived merge-patch.
  * DELETE /v1/agent/conversations/{id}  — soft-delete + audit; hidden
    from list by default; ``include_deleted=true`` brings them back.
  * POST /v1/agent/conversations/{id}/restore  — clears deleted_at.
  * delete + active run → cancel via existing cancel_run path.

Tests follow the trusted-header pattern used by sibling routes
(``test_conversation_connector_scope_admin_override.py``): every request
carries the service token + identity headers; ADMIN_USERS scope flips
admin behaviour for the workspace-defaults PUT.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi.testclient import TestClient

from agent_runtime.api.constants import Messages
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionScope,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "enforce")
    yield


class WorkspaceDefaultsFixtureMixin:
    """Test fixtures + builders for PR 1.6 routes."""

    class Values:
        ORG_ID = "org_pr16"
        USER_ID = "user_pr16"
        ASSISTANT_ID = "assistant_pr16"
        DEFAULT_MODEL_NAME = "gpt-5.4-mini"

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": self.Values.DEFAULT_MODEL_NAME,
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        app = RuntimeApiAppFactory.create_app(service)
        return TestClient(app), store

    def _headers(
        self,
        *,
        permission_scopes: tuple[str, ...] = (RUNTIME_USE,),
    ) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": self.Values.USER_ID,
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": ",".join(permission_scopes),
            "x-enterprise-connector-scopes": "{}",
        }

    def _create_conversation(self, client: TestClient) -> str:
        resp = client.post(
            "/v1/agent/conversations",
            headers=self._headers(),
            json={
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "assistant_id": self.Values.ASSISTANT_ID,
                "title": "lifecycle test",
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["conversation_id"]

    def _defaults_payload(
        self,
        *,
        retention_days: int = 90,
        slack_paused: bool = True,
    ) -> dict[str, Any]:
        connectors: dict[str, Any] = {
            "notion": ["read", "write_drafts"],
            "drive": ["read"],
        }
        if slack_paused:
            connectors["slack"] = None
        return {
            "default_model": {
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
            },
            "default_connectors": connectors,
            "retention_days": retention_days,
        }


class TestWorkspaceDefaultsRoute(WorkspaceDefaultsFixtureMixin):
    def test_get_returns_deployment_fallback_when_empty(self) -> None:
        client, _ = self.create_client()
        response = client.get("/v1/agent/workspace/defaults", headers=self._headers())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["default_model"]["model_name"] == self.Values.DEFAULT_MODEL_NAME
        assert body["default_connectors"] == {}
        # Deployment SaaS retention floor is 365 days.
        assert body["retention_days"] == 365
        assert body["updated_at"] is None

    def test_put_requires_admin(self) -> None:
        client, _ = self.create_client()
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(),
            json=self._defaults_payload(),
        )
        assert response.status_code == 403, response.text

    def test_put_writes_defaults_retention_and_audit(self) -> None:
        client, store = self.create_client()
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._defaults_payload(retention_days=90),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["retention_days"] == 90
        assert body["default_connectors"]["slack"] is None
        assert body["updated_by_user_id"] == self.Values.USER_ID

        # Defaults row persisted.
        assert self.Values.ORG_ID in store.workspace_defaults
        persisted = store.workspace_defaults[self.Values.ORG_ID]
        assert persisted.default_connectors == {
            "notion": ("read", "write_drafts"),
            "drive": ("read",),
            "slack": None,
        }

        # Three retention rows written (messages/events/checkpoints).
        policies = store.retention_policies.get(self.Values.ORG_ID, ())
        kinds = {policy.kind for policy in policies}
        assert kinds == {
            RetentionKind.MESSAGES,
            RetentionKind.EVENTS,
            RetentionKind.CHECKPOINTS,
        }
        for policy in policies:
            assert policy.scope is RetentionScope.ORG
            assert policy.resource_id is None
            assert policy.ttl_seconds == 90 * 24 * 60 * 60

        # One audit row referencing the policy ids.
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.WORKSPACE_DEFAULTS_UPDATE
        ]
        assert len(events) == 1, events
        meta = events[0]["metadata"]
        assert "retention_policy_ids" in meta
        assert len(meta["retention_policy_ids"]) == 3

    def test_put_rejects_invalid_retention_days(self) -> None:
        client, _ = self.create_client()
        payload = self._defaults_payload(retention_days=0)
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=payload,
        )
        # Pydantic field-validation failures surface as 400 in the
        # runtime API's typed-error handler (see runtime_api.http.errors).
        assert response.status_code == 400, response.text


class TestCreateConversationDefaultsFallback(WorkspaceDefaultsFixtureMixin):
    def test_new_conversation_inherits_default_connectors(self) -> None:
        client, _ = self.create_client()
        put = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._defaults_payload(slack_paused=True),
        )
        assert put.status_code == 200, put.text

        conversation_id = self._create_conversation(client)
        response = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled_connectors"]["slack"] is None
        assert body["enabled_connectors"]["notion"] == ["read", "write_drafts"]


class TestConversationLifecycleRoute(WorkspaceDefaultsFixtureMixin):
    def test_patch_folder_then_unset(self) -> None:
        client, _ = self.create_client()
        conversation_id = self._create_conversation(client)
        patched = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
            json={"folder": "Launches"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["folder"] == "Launches"

        cleared = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
            json={"folder": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["folder"] is None

    def test_patch_archived_toggle(self) -> None:
        client, _ = self.create_client()
        conversation_id = self._create_conversation(client)
        archived = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
            json={"archived": True},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"

        restored = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
            json={"archived": False},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "active"

    def test_patch_rejects_oversized_folder(self) -> None:
        client, _ = self.create_client()
        conversation_id = self._create_conversation(client)
        response = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
            json={"folder": "x" * 200},
        )
        # Pydantic validators surface as 400 via the typed-error handler.
        assert response.status_code == 400, response.text

    def test_delete_soft_deletes_and_filters_from_list(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)
        response = client.delete(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        assert response.status_code == 204, response.text
        assert store.conversations[conversation_id].deleted_at is not None

        listed = client.get("/v1/agent/conversations", headers=self._headers())
        assert listed.status_code == 200, listed.text
        assert listed.json()["conversations"] == []

        with_deleted = client.get(
            "/v1/agent/conversations",
            params={"include_deleted": "true"},
            headers=self._headers(),
        )
        assert with_deleted.status_code == 200, with_deleted.text
        ids = [c["conversation_id"] for c in with_deleted.json()["conversations"]]
        assert conversation_id in ids

    def test_restore_clears_deleted_at(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)
        client.delete(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        response = client.post(
            f"/v1/agent/conversations/{conversation_id}/restore",
            headers=self._headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["deleted_at"] is None
        assert store.conversations[conversation_id].deleted_at is None

    def test_delete_cancels_active_run(self) -> None:
        # The full create_run path requires DB-resident commands; we
        # exercise the cancel-on-delete branch by injecting a
        # non-terminal run directly into the store and asserting that
        # delete_conversation flips its status via cancel_run.
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)
        from agent_runtime.execution.contracts import (
            AgentRuntimeContext,
            ModelConfig,
        )
        from runtime_api.schemas import RunRecord

        runtime_context = AgentRuntimeContext(
            user_id=self.Values.USER_ID,
            org_id=self.Values.ORG_ID,
            roles=frozenset({"employee"}),
            model_profile=ModelConfig(
                provider="openai",
                model_name=self.Values.DEFAULT_MODEL_NAME,
                max_input_tokens=128_000,
                timeout_seconds=60.0,
                temperature=0.0,
            ),
        )
        run = RunRecord(
            run_id="run_lifecycle_test",
            conversation_id=conversation_id,
            org_id=self.Values.ORG_ID,
            user_id=self.Values.USER_ID,
            user_message_id="msg_seed",
            model_provider="openai",
            model_name=self.Values.DEFAULT_MODEL_NAME,
            runtime_context=runtime_context,
            trace_id=runtime_context.trace_id,
            status=AgentRunStatus.QUEUED,
        )
        store.runs[run.run_id] = run

        response = client.delete(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        assert response.status_code == 204, response.text
        cancelled = store.runs[run.run_id]
        assert cancelled.status in {
            AgentRunStatus.CANCELLING,
            AgentRunStatus.CANCELLED,
        }
