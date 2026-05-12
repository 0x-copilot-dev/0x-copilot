"""Cross-tenant isolation for the agent runtime API (facade-style auth path).

**Policy (404 for cross-tenant access):** When a caller presents trusted service
identity for org B, fetches for resources created under org A return **404** (same
as truly missing resources) so we do not leak whether an ID exists in another
tenant. Error message bodies must not include the other tenant's data.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import ApprovalRequestRecord
from runtime_api.sse.adapter import RuntimeSseAdapter


class TenantIsolationRuntimeMixin:
    """Build a runtime API TestClient with shared ENTERPRISE_SERVICE_TOKEN."""

    TOKEN = "tenant-isolation-service-token"
    ORG_A = "org_tenant_a"
    USER_A = "user_tenant_a"
    ORG_B = "org_tenant_b"
    USER_B = "user_tenant_b"
    ASSISTANT_ID = "assistant_iso_test"

    def _service_environ(self) -> dict[str, str]:
        return {
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_PARALLEL_TASKS": "4",
            "ENTERPRISE_SERVICE_TOKEN": self.TOKEN,
            "RUNTIME_STORE_BACKEND": "in_memory",
        }

    def headers(self, org_id: str, user_id: str) -> dict[str, str]:
        return {
            SERVICE_TOKEN_HEADER: self.TOKEN,
            ORG_HEADER: org_id,
            USER_HEADER: user_id,
        }

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(environ=self._service_environ())
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        app.state.runtime_api_store = store
        return TestClient(app), store

    def conversation_payload(self, org_id: str, user_id: str) -> dict[str, Any]:
        return {
            "org_id": org_id,
            "user_id": user_id,
            "assistant_id": self.ASSISTANT_ID,
            "title": "Tenant isolation fixture",
            "metadata": {},
            "idempotency_key": f"idem_iso_{org_id}_{user_id}",
        }

    def run_payload(
        self, conversation_id: str, org_id: str, user_id: str
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "org_id": org_id,
            "user_id": user_id,
            "user_input": "Hello from isolation test.",
            "content_format": "text",
            "idempotency_key": f"idem_run_{conversation_id}",
            "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
            "request_context": {
                "roles": ["employee"],
                "permission_scopes": ["search:read"],
                "trace_metadata": {"source": "tenant-isolation-test"},
            },
            "request_options": {},
        }


class TestTenantIsolationRuntimeApi(TenantIsolationRuntimeMixin):
    def test_org_b_cannot_read_org_a_conversation(self) -> None:
        client, _store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        created = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]

        h_b = self.headers(self.ORG_B, self.USER_B)
        miss = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            headers=h_b,
            params={"org_id": self.ORG_B, "user_id": self.USER_B},
        )
        assert miss.status_code == 404
        detail = miss.json().get("detail", "")
        assert self.ORG_A not in str(detail)
        assert "Tenant isolation fixture" not in str(detail)

    def test_org_b_cannot_list_messages_for_org_a_conversation(self) -> None:
        client, _store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        created = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        ).json()
        conversation_id = created["conversation_id"]

        h_b = self.headers(self.ORG_B, self.USER_B)
        miss = client.get(
            f"/v1/agent/conversations/{conversation_id}/messages",
            headers=h_b,
            params={"org_id": self.ORG_B, "user_id": self.USER_B},
        )
        assert miss.status_code == 404

    def test_org_b_cannot_read_org_a_run_events_or_status(self) -> None:
        client, _store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        conv = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        ).json()
        run = client.post(
            "/v1/agent/runs",
            headers=h_a,
            json=self.run_payload(conv["conversation_id"], self.ORG_A, self.USER_A),
        ).json()
        run_id = run["run_id"]

        h_b = self.headers(self.ORG_B, self.USER_B)
        params_b = {"org_id": self.ORG_B, "user_id": self.USER_B}

        assert (
            client.get(
                f"/v1/agent/runs/{run_id}",
                headers=h_b,
                params=params_b,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/agent/runs/{run_id}/events",
                headers=h_b,
                params=params_b,
            ).status_code
            == 404
        )

    async def test_org_b_cannot_stream_org_a_run_via_sse_adapter(self) -> None:
        """HTTP StreamingResponse may start before replay validates scope; assert at adapter layer."""

        client, _store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        conv = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        ).json()
        run = client.post(
            "/v1/agent/runs",
            headers=h_a,
            json=self.run_payload(conv["conversation_id"], self.ORG_A, self.USER_A),
        ).json()
        run_id = run["run_id"]
        service = client.app.state.conversation_query_service

        async def first_sse_chunk() -> None:
            stream = RuntimeSseAdapter.stream(
                service=service,
                org_id=self.ORG_B,
                user_id=self.USER_B,
                run_id=run_id,
                after_sequence=0,
                follow=False,
            )
            await stream.__anext__()

        with pytest.raises(RuntimeApiError):
            await first_sse_chunk()

    def test_org_b_cannot_cancel_org_a_run(self) -> None:
        client, _store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        conv = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        ).json()
        run = client.post(
            "/v1/agent/runs",
            headers=h_a,
            json=self.run_payload(conv["conversation_id"], self.ORG_A, self.USER_A),
        ).json()
        run_id = run["run_id"]

        h_b = self.headers(self.ORG_B, self.USER_B)
        cancel = client.post(
            f"/v1/agent/runs/{run_id}/cancel",
            headers=h_b,
            params={"org_id": self.ORG_B, "user_id": self.USER_B},
            json={"requested_by_user_id": self.USER_B, "reason": "cross-tenant"},
        )
        assert cancel.status_code == 404

    async def test_org_b_cannot_decide_org_a_approval(self) -> None:
        client, store = self.create_client()
        h_a = self.headers(self.ORG_A, self.USER_A)
        conv = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, self.USER_A),
        ).json()
        run = client.post(
            "/v1/agent/runs",
            headers=h_a,
            json=self.run_payload(conv["conversation_id"], self.ORG_A, self.USER_A),
        ).json()
        approval_id = "approval_iso_1"
        await store.seed_approval_request(
            ApprovalRequestRecord(
                approval_id=approval_id,
                run_id=run["run_id"],
                conversation_id=conv["conversation_id"],
                org_id=self.ORG_A,
                user_id=self.USER_A,
            )
        )

        h_b = self.headers(self.ORG_B, self.USER_B)
        decision = client.post(
            f"/v1/agent/approvals/{approval_id}/decision",
            headers=h_b,
            params={"org_id": self.ORG_B},
            json={"decision": "approved", "decided_by_user_id": self.USER_B},
        )
        assert decision.status_code == 404

    def test_same_user_id_different_org_still_isolated(self) -> None:
        """User IDs may collide across orgs; scope is always (org_id, user_id)."""

        client, _store = self.create_client()
        shared_user = "same_user_id_two_orgs"
        h_a = self.headers(self.ORG_A, shared_user)
        created = client.post(
            "/v1/agent/conversations",
            headers=h_a,
            json=self.conversation_payload(self.ORG_A, shared_user),
        ).json()
        conversation_id = created["conversation_id"]

        h_b = self.headers(self.ORG_B, shared_user)
        miss = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            headers=h_b,
            params={"org_id": self.ORG_B, "user_id": shared_user},
        )
        assert miss.status_code == 404
