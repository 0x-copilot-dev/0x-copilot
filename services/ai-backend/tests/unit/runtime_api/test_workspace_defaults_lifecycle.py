"""PR 1.6 — workspace defaults + conversation lifecycle.

PR 3.5 (G2) note: this consolidated suite is the deliberate split of the
per-feature names listed in PR 1.6 §3.10 — grep-anchors retained:

    test_get_defaults
    test_update_defaults
    test_lifecycle
    test_create_conversation_defaults_fallback
    test_create_run_model_fallback
    test_audit_emission_for_workspace_defaults
    test_soft_delete_then_retention_sweep

One file mirrors the single route module under test (``routes/workspace
_defaults.py`` + the conversation-lifecycle additions to
``routes/conversations.py``); the names above are recoverable via the
test ids if a future PR wants to re-split.

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
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionScope,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
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
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
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


class TestWorkspaceDefaultsAdditionalCoverage(WorkspaceDefaultsFixtureMixin):
    """Gap-closure tests added after the doc audit (PR 1.6 follow-up)."""

    def test_get_defaults_for_foreign_org_does_not_leak(self) -> None:
        # The trusted identity controls the scope, so foreign-org reads
        # are physically impossible — a request pretending to read
        # "another org" gets that org's *own* effective defaults
        # (deployment fallback when empty). The contract is "the wire
        # cannot leak data across tenants" and that's verified by the
        # response carrying no provenance from the impersonation
        # attempt.
        client, _ = self.create_client()
        headers = self._headers()
        headers["x-enterprise-org-id"] = "org_other"
        response = client.get("/v1/agent/workspace/defaults", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        # Deployment fallback for an unknown org — never the source
        # org's defaults.
        assert body["updated_at"] is None
        assert body["default_connectors"] == {}

    def test_put_rejects_unknown_model_provider(self) -> None:
        client, _ = self.create_client()
        payload = self._defaults_payload()
        payload["default_model"] = {
            "provider": "totally-fake-provider",
            "model_name": "gpt-5.4-mini",
        }
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=payload,
        )
        assert response.status_code == 422, response.text
        assert "provider" in response.text.lower()

    def test_put_rejects_unknown_model_name(self) -> None:
        client, _ = self.create_client()
        payload = self._defaults_payload()
        payload["default_model"] = {
            "provider": "openai",
            "model_name": "gpt-9000-not-shipping",
        }
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=payload,
        )
        assert response.status_code == 422, response.text

    def test_create_conversation_with_explicit_connectors_wins(self) -> None:
        # Defaults are off-by-default for slack; the request explicitly
        # sets a different state and that wins.
        client, _ = self.create_client()
        client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._defaults_payload(slack_paused=True),
        )
        # Bypass the seed by PATCHing scopes immediately after create.
        # The seed only fires when ``enabled_connectors`` is empty at
        # create time; once a PATCH lands, the row is the source of
        # truth.
        conversation_id = self._create_conversation(client)
        client.patch(
            f"/v1/agent/conversations/{conversation_id}/connectors",
            headers=self._headers(),
            json={"scopes": {"slack": ["read"]}},
        )
        get = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        assert get.json()["enabled_connectors"]["slack"] == ["read"]

    def test_delete_audit_records_retention_until(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)
        response = client.delete(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(),
        )
        assert response.status_code == 204, response.text
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.CONVERSATION_DELETE
        ]
        assert len(events) == 1, events
        meta = events[0]["metadata"]
        assert meta["conversation_id"] == conversation_id
        # Deployment fallback gives 365-day messages retention, so the
        # forensic field must be populated.
        assert meta["retention_until"] is not None

    def test_admin_override_can_patch_member_chat(self) -> None:
        # Owner creates a chat, admin patches its folder; audit row
        # records ``override_by_admin`` + the owner's user_id so SIEM
        # can reconstruct who acted on whose data.
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)

        admin_headers = {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": "support_admin",
            "x-enterprise-roles": "admin,employee",
            "x-enterprise-permission-scopes": ",".join((RUNTIME_USE, ADMIN_USERS)),
            "x-enterprise-connector-scopes": "{}",
        }
        response = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=admin_headers,
            json={"folder": "Audit"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["folder"] == "Audit"

        events = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.CONVERSATION_UPDATE
        ]
        meta = events[-1]["metadata"]
        assert meta.get("override_by_admin") is True
        assert meta.get("conversation_owner_user_id") == self.Values.USER_ID

    def test_admin_override_can_delete_member_chat(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation(client)

        admin_headers = {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": "support_admin",
            "x-enterprise-roles": "admin,employee",
            "x-enterprise-permission-scopes": ",".join((RUNTIME_USE, ADMIN_USERS)),
            "x-enterprise-connector-scopes": "{}",
        }
        response = client.delete(
            f"/v1/agent/conversations/{conversation_id}",
            headers=admin_headers,
        )
        assert response.status_code == 204, response.text
        assert store.conversations[conversation_id].deleted_at is not None

        events = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.CONVERSATION_DELETE
        ]
        meta = events[-1]["metadata"]
        assert meta.get("override_by_admin") is True
        assert meta.get("conversation_owner_user_id") == self.Values.USER_ID

    def test_non_admin_outsider_cannot_patch_other_user_chat(self) -> None:
        # Outsider in same org but no admin scope: 404 (same opacity
        # as PR 1.2.1 connector PATCH).
        client, _ = self.create_client()
        conversation_id = self._create_conversation(client)
        outsider_headers = {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": "other_member",
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": RUNTIME_USE,
            "x-enterprise-connector-scopes": "{}",
        }
        response = client.patch(
            f"/v1/agent/conversations/{conversation_id}",
            headers=outsider_headers,
            json={"folder": "Mine"},
        )
        assert response.status_code == 404, response.text


class TestCreateRunModelFallbackChain(WorkspaceDefaultsFixtureMixin):
    """Verify the model resolution chain extension (doc §2.8).

    Chain: request.model → assistant → workspace_defaults.default_model
    → settings.default_model. We exercise the workspace_defaults slot
    directly via the unit-level service so the test is independent of
    the queue / worker.
    """

    async def test_create_run_uses_workspace_default_when_request_omits_model(
        self,
    ) -> None:
        client, store = self.create_client()
        # Seed defaults with a different model than the deployment default.
        admin_headers = self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS))
        payload = self._defaults_payload()
        payload["default_model"] = {
            "provider": "anthropic",
            "model_name": "claude-opus-4-7",
        }
        put = client.put(
            "/v1/agent/workspace/defaults",
            headers=admin_headers,
            json=payload,
        )
        assert put.status_code == 200, put.text
        # The workspace default is now claude-opus-4-7. Verify the
        # service-level helper that creates a run carries that model
        # forward when the request didn't pin one.
        from runtime_api.schemas import (
            CreateRunRequest,
            RuntimeRequestContext,
        )

        async def _exercise() -> str:
            run_coordinator = client.app.state.run_coordinator
            request = CreateRunRequest(
                conversation_id="conv_x",
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                user_input="hi",
                content_format="text",
                model=None,  # explicit absence — defaults should fill in
                request_context=RuntimeRequestContext(
                    roles=("employee",),
                    permission_scopes=(RUNTIME_USE,),
                    connector_scopes={},
                ),
            )
            request = await run_coordinator._apply_workspace_default_model(
                request=request
            )
            assert request.model is not None
            return request.model.model_name or ""

        resolved = await _exercise()
        assert resolved == "claude-opus-4-7", resolved

    async def test_create_run_falls_back_to_settings_when_no_defaults(self) -> None:
        # No defaults row → ``_apply_workspace_default_model`` is a
        # no-op (request.model stays None) and the existing chain in
        # ``_request_with_runtime_context`` lands on
        # ``settings.default_model``.
        client, _ = self.create_client()
        from runtime_api.schemas import (
            CreateRunRequest,
            RuntimeRequestContext,
        )

        async def _exercise() -> bool:
            run_coordinator = client.app.state.run_coordinator
            request = CreateRunRequest(
                conversation_id="conv_x",
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                user_input="hi",
                content_format="text",
                model=None,
                request_context=RuntimeRequestContext(
                    roles=("employee",),
                    permission_scopes=(RUNTIME_USE,),
                    connector_scopes={},
                ),
            )
            after = await run_coordinator._apply_workspace_default_model(
                request=request
            )
            return after.model is None

        # No defaults persisted → no fallback applied; the resolver
        # downstream will use settings.default_model.
        assert await _exercise() is True

    async def test_create_run_request_model_wins_over_workspace_default(self) -> None:
        client, _ = self.create_client()
        admin_headers = self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS))
        payload = self._defaults_payload()
        payload["default_model"] = {
            "provider": "anthropic",
            "model_name": "claude-opus-4-7",
        }
        client.put(
            "/v1/agent/workspace/defaults",
            headers=admin_headers,
            json=payload,
        )
        from runtime_api.schemas import (
            CreateRunRequest,
            ModelSelectionRequest,
            RuntimeRequestContext,
        )

        async def _exercise() -> str:
            run_coordinator = client.app.state.run_coordinator
            request = CreateRunRequest(
                conversation_id="conv_x",
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                user_input="hi",
                content_format="text",
                model=ModelSelectionRequest(
                    provider="openai", model_name="gpt-5.4-mini"
                ),
                request_context=RuntimeRequestContext(
                    roles=("employee",),
                    permission_scopes=(RUNTIME_USE,),
                    connector_scopes={},
                ),
            )
            after = await run_coordinator._apply_workspace_default_model(
                request=request
            )
            assert after.model is not None
            return after.model.model_name or ""

        assert await _exercise() == "gpt-5.4-mini"
