"""PR 1.2 — in-memory store: per-chat connector scope merge-patch."""

from __future__ import annotations

from datetime import datetime, timezone

from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest


class ConversationFixtureMixin:
    ORG_ID = "org_pr12"
    USER_ID = "user_pr12"

    def seed_conversation(self, store: InMemoryRuntimeApiStore) -> str:
        record = store.create_conversation(
            CreateConversationRequest(
                org_id=self.ORG_ID,
                user_id=self.USER_ID,
                assistant_id="assistant_pr12",
                title="scope test",
            )
        )
        return record.conversation_id


class TestUpdateConversationConnectors(ConversationFixtureMixin):
    def test_merge_patch_overwrites_present_keys_only(self) -> None:
        store = InMemoryRuntimeApiStore()
        conversation_id = self.seed_conversation(store)
        now = datetime.now(timezone.utc)

        first = store.update_conversation_connectors(
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            conversation_id=conversation_id,
            scopes_patch={"slack": ("read",), "drive": ("read", "comment")},
            now=now,
        )
        assert first is not None
        assert first.enabled_connectors == {
            "slack": ("read",),
            "drive": ("read", "comment"),
        }
        assert first.connectors_updated_at == now

        # Second patch only touches `slack`; `drive` survives untouched.
        later = now.replace(microsecond=0)
        second = store.update_conversation_connectors(
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            conversation_id=conversation_id,
            scopes_patch={"slack": None},
            now=later,
        )
        assert second is not None
        assert second.enabled_connectors == {
            "slack": None,  # paused
            "drive": ("read", "comment"),  # untouched
        }
        assert second.runtime_connector_scopes() == {"drive": ("read", "comment")}

    def test_returns_none_for_foreign_org(self) -> None:
        store = InMemoryRuntimeApiStore()
        conversation_id = self.seed_conversation(store)
        result = store.update_conversation_connectors(
            org_id="other_org",
            user_id=self.USER_ID,
            conversation_id=conversation_id,
            scopes_patch={"slack": None},
            now=datetime.now(timezone.utc),
        )
        assert result is None

    def test_runtime_connector_scopes_drops_paused(self) -> None:
        store = InMemoryRuntimeApiStore()
        conversation_id = self.seed_conversation(store)
        store.update_conversation_connectors(
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            conversation_id=conversation_id,
            scopes_patch={"slack": None, "notion": ("read",)},
            now=datetime.now(timezone.utc),
        )
        record = store.get_conversation(
            org_id=self.ORG_ID,
            user_id=self.USER_ID,
            conversation_id=conversation_id,
        )
        assert record is not None
        # Paused (None) connectors are not surfaced to the runtime context.
        assert record.runtime_connector_scopes() == {"notion": ("read",)}
