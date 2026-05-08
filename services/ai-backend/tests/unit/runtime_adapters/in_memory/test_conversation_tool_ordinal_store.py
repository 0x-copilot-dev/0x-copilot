"""PR 04 — InMemoryConversationToolOrdinalStore conformance."""

from __future__ import annotations

import pytest

from agent_runtime.persistence.ports import ConversationOrdinalConflict
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)


class FixtureMixin:
    ORG_ID = "org_citations"
    CONV_ID = "conv_pr04"
    OTHER_CONV_ID = "conv_pr04_sibling"
    RUN_ID = "run_pr04"

    def make_store(self) -> InMemoryConversationToolOrdinalStore:
        return InMemoryConversationToolOrdinalStore()

    async def record_default(
        self,
        store: InMemoryConversationToolOrdinalStore,
        *,
        conversation_id: str | None = None,
        ordinal: int = 1,
        tool_call_id: str = "call_one",
        tool_name: str = "web_search",
    ):
        return await store.record(
            org_id=self.ORG_ID,
            conversation_id=conversation_id or self.CONV_ID,
            conversation_ordinal=ordinal,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=self.RUN_ID,
        )


class TestRecord(FixtureMixin):
    @pytest.mark.asyncio
    async def test_inserts_and_returns_canonical_row(self) -> None:
        store = self.make_store()
        record = await self.record_default(store)
        assert record.conversation_ordinal == 1
        assert record.tool_call_id == "call_one"
        assert record.tool_name == "web_search"
        assert record.run_id == self.RUN_ID
        assert record.org_id == self.ORG_ID
        assert record.conversation_id == self.CONV_ID

    @pytest.mark.asyncio
    async def test_idempotent_on_same_tool_call_id_same_ordinal(self) -> None:
        # Regression pin: a retried allocator call for the same
        # tool_call_id + ordinal must collapse to the existing row.
        # This is the LangGraph re-dispatch case (e.g. after an
        # approval pause resumes the original tool dispatch with the
        # same call_id).
        store = self.make_store()
        first = await self.record_default(store)
        second = await self.record_default(store)
        assert second is first or second == first
        assert len(store.rows) == 1

    @pytest.mark.asyncio
    async def test_conflict_when_same_tool_call_id_different_ordinal(self) -> None:
        # Regression pin: two allocators racing for the same call_id
        # must surface as a typed conflict so the caller can reload
        # state and retry with the canonical ordinal.
        store = self.make_store()
        await self.record_default(store, ordinal=1, tool_call_id="call_a")
        with pytest.raises(ConversationOrdinalConflict) as excinfo:
            await self.record_default(store, ordinal=2, tool_call_id="call_a")
        assert excinfo.value.attempted_ordinal == 2
        assert excinfo.value.existing_ordinal == 1
        assert excinfo.value.tool_call_id == "call_a"

    @pytest.mark.asyncio
    async def test_conflict_when_same_ordinal_different_tool_call_id(self) -> None:
        # The PK collision case — two distinct tool_call_ids racing
        # for the same conversation_ordinal value.
        store = self.make_store()
        await self.record_default(store, ordinal=1, tool_call_id="call_a")
        with pytest.raises(ConversationOrdinalConflict):
            await self.record_default(store, ordinal=1, tool_call_id="call_b")

    @pytest.mark.asyncio
    async def test_rejects_zero_ordinal(self) -> None:
        store = self.make_store()
        with pytest.raises(ValueError):
            await self.record_default(store, ordinal=0)

    @pytest.mark.asyncio
    async def test_rejects_empty_tool_call_id(self) -> None:
        store = self.make_store()
        with pytest.raises(ValueError):
            await self.record_default(store, tool_call_id="")


class TestLoad(FixtureMixin):
    @pytest.mark.asyncio
    async def test_returns_bindings_sorted_by_ordinal(self) -> None:
        store = self.make_store()
        await self.record_default(store, ordinal=2, tool_call_id="call_b")
        await self.record_default(store, ordinal=1, tool_call_id="call_a")
        await self.record_default(store, ordinal=3, tool_call_id="call_c")
        rows = await store.load(org_id=self.ORG_ID, conversation_id=self.CONV_ID)
        assert [row.conversation_ordinal for row in rows] == [1, 2, 3]
        assert [row.tool_call_id for row in rows] == ["call_a", "call_b", "call_c"]

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_conversation(self) -> None:
        store = self.make_store()
        await self.record_default(store)
        rows = await store.load(
            org_id=self.ORG_ID, conversation_id="conv_does_not_exist"
        )
        assert rows == ()

    @pytest.mark.asyncio
    async def test_isolates_conversations(self) -> None:
        # Same ordinal value across two conversations is two distinct
        # bindings; load() must not bleed across conversation_id.
        store = self.make_store()
        await self.record_default(store, ordinal=1, tool_call_id="call_left")
        await self.record_default(
            store,
            conversation_id=self.OTHER_CONV_ID,
            ordinal=1,
            tool_call_id="call_right",
        )
        left = await store.load(org_id=self.ORG_ID, conversation_id=self.CONV_ID)
        right = await store.load(org_id=self.ORG_ID, conversation_id=self.OTHER_CONV_ID)
        assert [row.tool_call_id for row in left] == ["call_left"]
        assert [row.tool_call_id for row in right] == ["call_right"]

    @pytest.mark.asyncio
    async def test_filters_by_org_id(self) -> None:
        store = self.make_store()
        await self.record_default(store)
        rows = await store.load(org_id="org_other_tenant", conversation_id=self.CONV_ID)
        assert rows == ()
