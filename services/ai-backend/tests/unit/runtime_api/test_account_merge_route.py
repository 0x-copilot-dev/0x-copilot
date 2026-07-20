"""Internal account-merge endpoint tests (account-linking PRD §6.4).

The endpoint is the backend merge saga's re-key call into ai-backend. It is
service-token gated (never tenant-scoped), returns per-store moved-row
counts, and must be idempotent: a second call for the same absorbed account
is a ``noop`` with empty counts, never an error.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import ConversationRecord, MessageRecord, MessageRole


class _MergeClientMixin:
    """Client builder + direct-store seeding for the merge endpoint."""

    _ABSORBED_ORG = "org_absorbed"
    _ABSORBED_USER = "user_absorbed"
    _SURVIVOR_ORG = "org_survivor"
    _SURVIVOR_USER = "user_survivor"
    _DECOY_ORG = "org_decoy"
    _DECOY_USER = "user_decoy"

    def _build_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
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

    def _seed_conversation(
        self, store: InMemoryRuntimeApiStore, *, org_id: str, user_id: str
    ) -> str:
        """Insert one conversation + message directly into the store dicts."""

        conversation = ConversationRecord(
            org_id=org_id,
            user_id=user_id,
            assistant_id="assistant",
            title=f"chat-{org_id}",
        )
        store.conversations[conversation.conversation_id] = conversation
        message = MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=org_id,
            role=MessageRole.USER,
            content_text="hello",
        )
        store.messages[message.message_id] = message
        return conversation.conversation_id

    def _merge_body(self) -> dict[str, str]:
        return {
            "merge_id": "merge_1",
            "absorbed_org_id": self._ABSORBED_ORG,
            "absorbed_user_id": self._ABSORBED_USER,
            "survivor_org_id": self._SURVIVOR_ORG,
            "survivor_user_id": self._SURVIVOR_USER,
        }


class TestAccountMergeAuth(_MergeClientMixin):
    """Service-token enforcement: wrong token is 401 before any re-key."""

    def test_invalid_service_token_returns_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "expected-token")
        client, store = self._build_client()
        self._seed_conversation(
            store, org_id=self._ABSORBED_ORG, user_id=self._ABSORBED_USER
        )

        response = client.post(
            "/internal/v1/admin/account-merge",
            headers={"x-enterprise-service-token": "wrong-token"},
            json=self._merge_body(),
        )
        assert response.status_code == 401
        # Auth failed before the re-key: absorbed rows are untouched.
        assert any(c.org_id == self._ABSORBED_ORG for c in store.conversations.values())

    def test_valid_service_token_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "expected-token")
        client, _store = self._build_client()

        response = client.post(
            "/internal/v1/admin/account-merge",
            headers={"x-enterprise-service-token": "expected-token"},
            json=self._merge_body(),
        )
        assert response.status_code == 200


class TestAccountMergeHappyPath(_MergeClientMixin):
    def test_moves_absorbed_rows_and_reports_counts(self) -> None:
        client, store = self._build_client()
        absorbed_conv = self._seed_conversation(
            store, org_id=self._ABSORBED_ORG, user_id=self._ABSORBED_USER
        )
        decoy_conv = self._seed_conversation(
            store, org_id=self._DECOY_ORG, user_id=self._DECOY_USER
        )
        audit_len_before = len(store.audit_log)

        response = client.post(
            "/internal/v1/admin/account-merge", json=self._merge_body()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["merge_id"] == "merge_1"
        assert body["status"] == "completed"
        assert body["tables"]["agent_conversations"] == 1
        assert body["tables"]["agent_messages"] == 1
        assert body["warnings"] == []

        moved = store.conversations[absorbed_conv]
        assert moved.org_id == self._SURVIVOR_ORG
        assert moved.user_id == self._SURVIVOR_USER
        # Decoy third account is untouched.
        assert store.conversations[decoy_conv].org_id == self._DECOY_ORG
        # Exactly one merge marker was appended — to the SURVIVOR chain.
        appended = store.audit_log[audit_len_before:]
        assert len(appended) == 1
        event_type, record = appended[0]
        assert event_type == "account_merged"
        assert record["org_id"] == self._SURVIVOR_ORG
        assert record["metadata"]["absorbed_org_id"] == self._ABSORBED_ORG

    def test_second_call_is_noop_with_zero_counts(self) -> None:
        client, store = self._build_client()
        self._seed_conversation(
            store, org_id=self._ABSORBED_ORG, user_id=self._ABSORBED_USER
        )

        first = client.post("/internal/v1/admin/account-merge", json=self._merge_body())
        assert first.status_code == 200
        assert first.json()["status"] == "completed"
        audit_len_after_first = len(store.audit_log)

        second = client.post(
            "/internal/v1/admin/account-merge", json=self._merge_body()
        )
        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "noop"
        assert body["tables"] == {}
        assert body["warnings"] == []
        # A noop appends no second marker.
        assert len(store.audit_log) == audit_len_after_first

    def test_merge_into_self_is_rejected(self) -> None:
        client, _store = self._build_client()
        response = client.post(
            "/internal/v1/admin/account-merge",
            json={
                "merge_id": "merge_1",
                "absorbed_org_id": self._ABSORBED_ORG,
                "absorbed_user_id": self._ABSORBED_USER,
                "survivor_org_id": self._ABSORBED_ORG,
                "survivor_user_id": self._ABSORBED_USER,
            },
        )
        assert response.status_code == 400
        assert (
            response.json()["safe_message"]
            == "absorbed and survivor account must differ"
        )

    def test_missing_fields_rejected(self) -> None:
        client, _store = self._build_client()
        response = client.post(
            "/internal/v1/admin/account-merge",
            json={"merge_id": "merge_1"},
        )
        assert response.status_code in {400, 422}
