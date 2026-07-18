"""Conversation full-text search over the disposable catalog's FTS5 index.

Covers the AC2 search DoD:

* ranked results over conversation title + redacted user/assistant text;
* the index survives an ``index/`` delete + rebuild with identical results;
* only redacted user/assistant text is indexed — a secret parked in a tool
  message or an event payload is never searchable (secret canary); and
* FTS unavailability disables search only, never blocking direct reads.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
)
from agent_runtime.execution.contracts import StreamEventSource

_ORG = "org_search"
_USER = "user_search"
_OTHER_USER = "user_other"
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ConversationSearchMixin:
    """Store setup + seeding helpers shared across the search tests."""

    _clock = 0

    async def _open_store(self, tmp_path) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        return store

    def _next_ts(self) -> datetime:
        self._clock += 1
        return _BASE + timedelta(seconds=self._clock)

    async def _new_conversation(
        self, store: FileRuntimeApiStore, *, title: str, user_id: str = _USER
    ):
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=user_id, assistant_id="assistant", title=title
            )
        )

    async def _add_message(
        self,
        store: FileRuntimeApiStore,
        *,
        conversation_id: str,
        role: MessageRole,
        text: str,
    ) -> MessageRecord:
        message = MessageRecord(
            conversation_id=conversation_id,
            org_id=_ORG,
            role=role,
            content_text=text,
            created_at=self._next_ts(),
        )
        return await store.append_message(message)

    async def _add_event_with_payload(
        self, store: FileRuntimeApiStore, *, conversation_id: str, payload: dict
    ) -> None:
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=f"run_{conversation_id}",
                conversation_id=conversation_id,
                trace_id="trace_search",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                summary=str(payload),
                payload=payload,
            )
        )

    @staticmethod
    def _ids(hits) -> list[str]:
        return [hit.conversation.conversation_id for hit in hits]


class TestConversationSearch(ConversationSearchMixin):
    async def test_matches_title_and_message_text_scoped_to_user(
        self, tmp_path
    ) -> None:
        store = await self._open_store(tmp_path)
        budget = await self._new_conversation(store, title="Quarterly budget planning")
        await self._add_message(
            store,
            conversation_id=budget.conversation_id,
            role=MessageRole.USER,
            text="let's reconcile the budget numbers before Friday",
        )
        await self._add_message(
            store,
            conversation_id=budget.conversation_id,
            role=MessageRole.ASSISTANT,
            text="the budget reconciliation looks complete",
        )
        vacation = await self._new_conversation(
            store, title="Lisbon vacation itinerary"
        )
        await self._add_message(
            store,
            conversation_id=vacation.conversation_id,
            role=MessageRole.USER,
            text="book flights and a hotel",
        )
        # A same-org conversation owned by a different user must never leak.
        other = await self._new_conversation(
            store, title="Budget for the other team", user_id=_OTHER_USER
        )
        await self._add_message(
            store,
            conversation_id=other.conversation_id,
            role=MessageRole.USER,
            text="allocate the remaining budget",
        )

        # Title-only match.
        assert self._ids(
            await store.search_conversations(
                org_id=_ORG, user_id=_USER, query="itinerary", limit=10
            )
        ) == [vacation.conversation_id]
        # Message-body match.
        assert self._ids(
            await store.search_conversations(
                org_id=_ORG, user_id=_USER, query="flights", limit=10
            )
        ) == [vacation.conversation_id]
        # Term shared across title + messages, scoped to the requesting user.
        budget_hits = await store.search_conversations(
            org_id=_ORG, user_id=_USER, query="budget", limit=10
        )
        assert self._ids(budget_hits) == [budget.conversation_id]
        assert other.conversation_id not in self._ids(budget_hits)

        await store.close()

    async def test_ranks_stronger_match_first(self, tmp_path) -> None:
        store = await self._open_store(tmp_path)
        strong = await self._new_conversation(store, title="Migration plan")
        await self._add_message(
            store,
            conversation_id=strong.conversation_id,
            role=MessageRole.USER,
            text="migration migration migration steps for the database migration",
        )
        weak = await self._new_conversation(store, title="Weekly notes")
        await self._add_message(
            store,
            conversation_id=weak.conversation_id,
            role=MessageRole.USER,
            text=(
                "long meeting notes covering staffing, roadmap, budget, hiring, "
                "and one aside about a migration we might schedule later"
            ),
        )

        hits = await store.search_conversations(
            org_id=_ORG, user_id=_USER, query="migration", limit=10
        )
        assert self._ids(hits) == [strong.conversation_id, weak.conversation_id]
        # Scores are ordered best-first (bm25: smaller is stronger).
        assert hits[0].score <= hits[1].score
        await store.close()

    async def test_blank_and_punctuation_queries_return_empty(self, tmp_path) -> None:
        store = await self._open_store(tmp_path)
        conv = await self._new_conversation(store, title="Anything")
        await self._add_message(
            store,
            conversation_id=conv.conversation_id,
            role=MessageRole.USER,
            text="hello world",
        )
        for query in ["", "   ", "***", '"']:
            assert (
                await store.search_conversations(
                    org_id=_ORG, user_id=_USER, query=query, limit=10
                )
                == ()
            )
        await store.close()

    async def test_survives_index_delete_and_rebuild(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        alpha = await self._new_conversation(store, title="Budget alpha")
        await self._add_message(
            store,
            conversation_id=alpha.conversation_id,
            role=MessageRole.USER,
            text="alpha budget details and forecast",
        )
        beta = await self._new_conversation(store, title="Roadmap beta")
        await self._add_message(
            store,
            conversation_id=beta.conversation_id,
            role=MessageRole.ASSISTANT,
            text="the roadmap budget is approved",
        )

        before = await store.search_conversations(
            org_id=_ORG, user_id=_USER, query="budget", limit=10
        )
        assert self._ids(before)  # non-empty
        await store.close()

        # Delete the disposable index -> forces a full rebuild from JSONL.
        shutil.rmtree(root / "index")
        assert not (root / "index").exists()

        rebuilt = FileRuntimeApiStore(root)
        await rebuilt.open()
        after = await rebuilt.search_conversations(
            org_id=_ORG, user_id=_USER, query="budget", limit=10
        )
        assert self._ids(after) == self._ids(before)
        assert [round(h.score, 9) for h in after] == [round(h.score, 9) for h in before]
        await rebuilt.close()

    async def test_secret_in_tool_message_or_event_is_not_searchable(
        self, tmp_path
    ) -> None:
        store = await self._open_store(tmp_path)
        conv = await self._new_conversation(store, title="Payment workflow")
        await self._add_message(
            store,
            conversation_id=conv.conversation_id,
            role=MessageRole.USER,
            text="run the payment reconciliation workflow",
        )
        # Secret arrives via a TOOL turn and an event payload — neither is
        # indexable. The canary token appears nowhere a user/assistant would
        # type it, so a hit would mean the FTS index leaked a secret.
        canary = "zzsecretcanary9137"
        tool_message = await self._add_message(
            store,
            conversation_id=conv.conversation_id,
            role=MessageRole.TOOL,
            text=f"AUTHTOKEN {canary} returned by the connector",
        )
        await self._add_event_with_payload(
            store,
            conversation_id=conv.conversation_id,
            payload={"api_key": canary, "note": f"secret {canary}"},
        )

        # The conversation is findable by its redacted user text ...
        assert self._ids(
            await store.search_conversations(
                org_id=_ORG, user_id=_USER, query="payment", limit=10
            )
        ) == [conv.conversation_id]
        # ... but the secret is not indexed and cannot be searched.
        assert (
            await store.search_conversations(
                org_id=_ORG, user_id=_USER, query=canary, limit=10
            )
            == ()
        )
        # Proof the secret genuinely exists in canonical storage (so the empty
        # search result is exclusion, not absence): the tool message is stored.
        messages = await store.list_messages(
            org_id=_ORG, conversation_id=conv.conversation_id, limit=50
        )
        stored_tool = next(
            m for m in messages if m.message_id == tool_message.message_id
        )
        assert canary in stored_tool.content_text
        await store.close()

    async def test_fts_unavailable_disables_search_only(self, tmp_path) -> None:
        store = await self._open_store(tmp_path)
        conv = await self._new_conversation(store, title="Budget review")
        await self._add_message(
            store,
            conversation_id=conv.conversation_id,
            role=MessageRole.USER,
            text="review the budget",
        )
        # Simulate a SQLite build without FTS5: search degrades to empty ...
        store._index._fts_available = False
        assert (
            await store.search_conversations(
                org_id=_ORG, user_id=_USER, query="budget", limit=10
            )
            == ()
        )
        # ... while direct reads keep working.
        listed = await store.list_conversations(org_id=_ORG, user_id=_USER, limit=10)
        assert any(c.conversation_id == conv.conversation_id for c in listed)
        messages = await store.list_messages(
            org_id=_ORG, conversation_id=conv.conversation_id, limit=10
        )
        assert len(messages) == 1
        await store.close()
