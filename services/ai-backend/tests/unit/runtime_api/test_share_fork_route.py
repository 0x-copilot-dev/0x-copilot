"""HTTP-route smoke tests for PR 6.2 ``POST /v1/agent/shares/{token}/fork``.

Exercises the route plumbing end-to-end through the FastAPI app
factory: identity propagation, payload shape, error mapping, and the
503 fallback when the fork service is not wired.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.conversation_fork import ConversationForkService
from agent_runtime.api.notifications import LoggingNotificationDispatcher
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.async_runtime_api_store import (
    AsyncInMemoryRuntimeApiStore,
)
from runtime_adapters.in_memory.share_snapshot_store import InMemoryShareSnapshotStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import (
    CreateConversationRequest,
    MessageRecord,
    ShareSnapshot,
)
from runtime_api.schemas.common import MessageRole, MessageStatus
from runtime_worker.audit import WorkerAuditEmitter


class _RouteFixtureMixin:
    class Values:
        ORG = "org_acme"
        SHARING_USER = "user_sarah"
        RECIPIENT = "user_marcus"
        SOURCE_CONV = "conv_launch"
        SHARE_TOKEN = "s_3f7b2c9a04d1"
        SHARE_ID = "share_01HZ"

    def make_client(
        self, *, wire_fork: bool = True
    ) -> tuple[TestClient, InMemoryRuntimeApiStore, InMemoryShareSnapshotStore]:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        share_store = InMemoryShareSnapshotStore()
        service = RuntimeApiService(
            persistence=async_store,
            event_store=async_store,
            queue=async_store,
            settings=settings,
        )
        app = RuntimeApiAppFactory.create_app(service)
        if wire_fork:
            app.state.share_snapshot_port = share_store
            app.state.conversation_fork_service = ConversationForkService(
                persistence=async_store,
                share_snapshots=share_store,
                audit=WorkerAuditEmitter(async_store),
                notifications=LoggingNotificationDispatcher(),
            )
        else:
            app.state.conversation_fork_service = None
        return TestClient(app), sync_store, share_store

    async def seed_source(
        self, sync_store: InMemoryRuntimeApiStore, *, message_count: int = 2
    ) -> None:
        record = sync_store.create_conversation(
            CreateConversationRequest(
                org_id=self.Values.ORG,
                user_id=self.Values.SHARING_USER,
                title="FY26 Q1 launch",
            )
        )
        record = record.model_copy(update={"conversation_id": self.Values.SOURCE_CONV})
        sync_store.conversations[self.Values.SOURCE_CONV] = record
        for index in range(message_count):
            sync_store.append_message(
                MessageRecord(
                    message_id=f"m{index}",
                    conversation_id=self.Values.SOURCE_CONV,
                    org_id=self.Values.ORG,
                    run_id="run_seed",
                    role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                    content_text=f"turn {index}",
                    parent_message_id=f"m{index - 1}" if index > 0 else None,
                    status=MessageStatus.CREATED,
                    created_at=datetime(2026, 5, 5, 12, 0, index, tzinfo=timezone.utc),
                )
            )

    def register_share(self, share_store: InMemoryShareSnapshotStore) -> None:
        share_store.register(
            token=self.Values.SHARE_TOKEN,
            snapshot=ShareSnapshot(
                share_id=self.Values.SHARE_ID,
                org_id=self.Values.ORG,
                conversation_id=self.Values.SOURCE_CONV,
                snapshot_at=datetime(2026, 5, 5, 18, 0, tzinfo=timezone.utc),
                view_access="workspace",
                sources_visible_to_viewer=False,
                created_by_user_id=self.Values.SHARING_USER,
            ),
        )


class TestShareForkRoute(_RouteFixtureMixin):
    @pytest.mark.anyio
    async def test_fork_returns_new_conversation(self) -> None:
        client, sync_store, share_store = self.make_client()
        await self.seed_source(sync_store, message_count=2)
        self.register_share(share_store)

        response = client.post(
            f"/v1/agent/shares/{self.Values.SHARE_TOKEN}/fork",
            json={},
            headers={
                "x-enterprise-org-id": self.Values.ORG,
                "x-enterprise-user-id": self.Values.RECIPIENT,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["parent_conversation_id"] == self.Values.SOURCE_CONV
        assert body["forked_from_share_id"] == self.Values.SHARE_ID
        assert body["fork_message_count"] == 2
        assert body["user_id"] == self.Values.RECIPIENT

    @pytest.mark.anyio
    async def test_unknown_token_returns_404(self) -> None:
        client, _sync_store, _share_store = self.make_client()
        response = client.post(
            "/v1/agent/shares/s_unknown/fork",
            json={},
            headers={
                "x-enterprise-org-id": self.Values.ORG,
                "x-enterprise-user-id": self.Values.RECIPIENT,
            },
        )
        assert response.status_code == 404

    @pytest.mark.anyio
    async def test_service_unavailable_when_not_wired(self) -> None:
        client, sync_store, _share_store = self.make_client(wire_fork=False)
        await self.seed_source(sync_store, message_count=1)
        response = client.post(
            f"/v1/agent/shares/{self.Values.SHARE_TOKEN}/fork",
            json={},
            headers={
                "x-enterprise-org-id": self.Values.ORG,
                "x-enterprise-user-id": self.Values.RECIPIENT,
            },
        )
        assert response.status_code == 503

    @pytest.mark.anyio
    async def test_payload_with_title_and_folder(self) -> None:
        client, sync_store, share_store = self.make_client()
        await self.seed_source(sync_store, message_count=1)
        self.register_share(share_store)

        response = client.post(
            f"/v1/agent/shares/{self.Values.SHARE_TOKEN}/fork",
            json={"title": "My exploration", "folder": "Launches"},
            headers={
                "x-enterprise-org-id": self.Values.ORG,
                "x-enterprise-user-id": self.Values.RECIPIENT,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "My exploration"
        assert body["folder"] == "Launches"
