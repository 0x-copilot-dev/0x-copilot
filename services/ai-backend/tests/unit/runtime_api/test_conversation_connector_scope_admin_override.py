"""PR 1.2.1 — workspace-admin override on the per-chat connector PATCH route.

Validates that:
  - A caller holding ``admin:users`` can PATCH a conversation owned by a
    different member of the same org; audit row carries
    ``override_by_admin=true`` plus ``conversation_owner_user_id``.
  - A non-admin caller PATCHing someone else's chat still gets 404
    (opacity preserved — never reveals existence).
  - An admin patching their OWN chat is recorded as an owner self-PATCH
    (no override flag) — the override path is taken only when the
    caller is provably acting on someone else's data.

Trusted-identity headers are mirrored from
``services/ai-backend/tests/unit/runtime_api/test_rbac.py`` so the route
sees a real RBAC-validated identity rather than a query-param spoof.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from runtime_api.app import RuntimeApiAppFactory
from agent_runtime.api.constants import Messages
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.settings import RuntimeSettings


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "enforce")
    yield


class AdminOverrideFixtureMixin:
    class Values:
        ORG_ID = "org_pr12_1"
        OWNER_USER_ID = "owner_member"
        ADMIN_USER_ID = "support_admin"
        OUTSIDER_USER_ID = "other_member"
        ASSISTANT_ID = "assistant_pr12_1"

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        return TestClient(app), store

    def _headers(
        self,
        *,
        user_id: str,
        permission_scopes: tuple[str, ...] = (RUNTIME_USE,),
    ) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": user_id,
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": ",".join(permission_scopes),
            "x-enterprise-connector-scopes": "{}",
        }

    def _create_conversation_for(self, client: TestClient, user_id: str) -> str:
        # Create-conversation is gated on RUNTIME_USE; reuse the trusted
        # header identity so the conversation is owned by `user_id`.
        response = client.post(
            "/v1/agent/conversations",
            headers=self._headers(user_id=user_id),
            json={
                "org_id": self.Values.ORG_ID,
                "user_id": user_id,
                "assistant_id": self.Values.ASSISTANT_ID,
                "title": "owner's chat",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["conversation_id"]

    def _patch(
        self,
        client: TestClient,
        conversation_id: str,
        *,
        actor_user_id: str,
        permission_scopes: tuple[str, ...],
        scopes: dict[str, list[str] | None],
    ) -> Any:
        return client.patch(
            f"/v1/agent/conversations/{conversation_id}/connectors",
            headers=self._headers(
                user_id=actor_user_id, permission_scopes=permission_scopes
            ),
            json={"scopes": scopes},
        )


class TestAdminOverridePath(AdminOverrideFixtureMixin):
    def test_admin_overrides_member_chat_with_audit_flag(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation_for(
            client, self.Values.OWNER_USER_ID
        )

        response = self._patch(
            client,
            conversation_id,
            actor_user_id=self.Values.ADMIN_USER_ID,
            permission_scopes=(RUNTIME_USE, ADMIN_USERS),
            scopes={"slack": None},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scopes"] == {"slack": None}

        # Owner's snapshot reflects the admin write.
        snapshot = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            headers=self._headers(user_id=self.Values.OWNER_USER_ID),
        )
        assert snapshot.status_code == 200
        assert snapshot.json()["enabled_connectors"] == {"slack": None}

        rows = [
            record
            for kind, record in store.audit_log
            if kind == Messages.Audit.CONVERSATION_CONNECTORS_UPDATE
        ]
        assert len(rows) == 1
        last = rows[0]
        assert last["user_id"] == self.Values.ADMIN_USER_ID  # actor, not owner
        meta = last["metadata"]
        assert meta["override_by_admin"] is True
        assert meta["conversation_owner_user_id"] == self.Values.OWNER_USER_ID
        assert meta["diff_keys"] == ["slack"]

    def test_non_admin_outsider_404s_without_audit(self) -> None:
        client, store = self.create_client()
        conversation_id = self._create_conversation_for(
            client, self.Values.OWNER_USER_ID
        )

        response = self._patch(
            client,
            conversation_id,
            actor_user_id=self.Values.OUTSIDER_USER_ID,
            permission_scopes=(RUNTIME_USE,),  # no admin scope
            scopes={"slack": None},
        )
        assert response.status_code == 404
        # Same opacity as foreign-tenant 404 — no audit row written.
        rows = [
            record
            for kind, record in store.audit_log
            if kind == Messages.Audit.CONVERSATION_CONNECTORS_UPDATE
        ]
        assert rows == []

    def test_admin_owner_self_patch_is_not_an_override(self) -> None:
        client, store = self.create_client()
        # The admin creates their OWN chat then patches it.
        conversation_id = self._create_conversation_for(
            client, self.Values.ADMIN_USER_ID
        )

        response = self._patch(
            client,
            conversation_id,
            actor_user_id=self.Values.ADMIN_USER_ID,
            permission_scopes=(RUNTIME_USE, ADMIN_USERS),
            scopes={"slack": None},
        )
        assert response.status_code == 200, response.text

        rows = [
            record
            for kind, record in store.audit_log
            if kind == Messages.Audit.CONVERSATION_CONNECTORS_UPDATE
        ]
        assert len(rows) == 1
        meta = rows[0]["metadata"]
        # Owner self-path: override_by_admin not set, owner_user_id absent.
        assert meta.get("override_by_admin") in (None, False)
        assert "conversation_owner_user_id" not in meta
