"""End-to-end tests for PostgresConversationToolOrdinalStore (PR 04).

Skipped silently when ``TEST_DATABASE_URL`` is unset — same pattern as
the sibling postgres adapter tests. To run locally:

    docker compose -f services/ai-backend/docker-compose.yml up -d postgres
    cd services/ai-backend
    TEST_DATABASE_URL=postgresql://ai_backend:ai_backend@127.0.0.1:5432/ai_backend \\
        PYTHONPATH=src:../../packages/service-contracts/src \\
        .venv/bin/python -m pytest tests/unit/runtime_adapters/postgres/test_conversation_tool_ordinal_store.py -q

Mirrors the InMemory conformance suite assertions:

* ``record`` is idempotent on (conversation_id, tool_call_id) — the
  LangGraph re-dispatch case after an approval pause.
* ``record`` raises :class:`ConversationOrdinalConflict` on
  same-call_id-different-ordinal and same-ordinal-different-call_id
  collisions — the concurrent-allocator races.
* ``load`` returns bindings sorted by conversation_ordinal asc and is
  scoped to (org_id, conversation_id) — no cross-tenant or
  cross-conversation bleed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from agent_runtime.persistence.ports import ConversationOrdinalConflict
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.conversation_tool_ordinal_store import (
    PostgresConversationToolOrdinalStore,
)
from runtime_api.schemas import CreateConversationRequest


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason=(
            "TEST_DATABASE_URL is required for "
            "PostgresConversationToolOrdinalStore tests."
        ),
    ),
]


@pytest.fixture
async def parent() -> AsyncIterator[PostgresRuntimeApiStore]:
    s = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        pool_min_size=2,
        pool_max_size=10,
        pool_acquire_timeout_seconds=10.0,
    )
    await s.open()
    try:
        await s.migrate()
        yield s
    finally:
        await s.close()


async def _seed_conversation(
    store: PostgresRuntimeApiStore, suffix: str | None = None
) -> tuple[str, str]:
    suffix = suffix or uuid4().hex
    org_id = f"org_{suffix}"
    user_id = f"user_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id,
            user_id=user_id,
            assistant_id=f"assistant_{suffix}",
        )
    )
    return org_id, conversation.conversation_id


class TestRecord:
    """``record`` semantics against real Postgres."""

    async def test_inserts_and_returns_canonical_row(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        record = await store.record(
            org_id=org_id,
            conversation_id=conv_id,
            conversation_ordinal=1,
            tool_call_id="call_one",
            tool_name="web_search",
            run_id=f"run_{uuid4().hex}",
        )
        assert record.conversation_ordinal == 1
        assert record.tool_call_id == "call_one"
        assert record.org_id == org_id
        assert record.conversation_id == conv_id

    async def test_idempotent_on_same_tool_call_id_same_ordinal(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        run_id = f"run_{uuid4().hex}"
        first = await store.record(
            org_id=org_id,
            conversation_id=conv_id,
            conversation_ordinal=1,
            tool_call_id="call_one",
            tool_name="web_search",
            run_id=run_id,
        )
        # Same call_id + same ordinal → returns the canonical row,
        # no second row inserted.
        second = await store.record(
            org_id=org_id,
            conversation_id=conv_id,
            conversation_ordinal=1,
            tool_call_id="call_one",
            tool_name="web_search",
            run_id=run_id,
        )
        assert second.conversation_ordinal == first.conversation_ordinal
        rows = await store.load(org_id=org_id, conversation_id=conv_id)
        assert len(rows) == 1

    async def test_conflict_when_same_tool_call_id_different_ordinal(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        await store.record(
            org_id=org_id,
            conversation_id=conv_id,
            conversation_ordinal=1,
            tool_call_id="call_a",
            tool_name="web_search",
            run_id=f"run_{uuid4().hex}",
        )
        with pytest.raises(ConversationOrdinalConflict) as excinfo:
            await store.record(
                org_id=org_id,
                conversation_id=conv_id,
                conversation_ordinal=2,
                tool_call_id="call_a",
                tool_name="web_search",
                run_id=f"run_{uuid4().hex}",
            )
        assert excinfo.value.attempted_ordinal == 2
        assert excinfo.value.existing_ordinal == 1

    async def test_conflict_when_same_ordinal_different_tool_call_id(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        await store.record(
            org_id=org_id,
            conversation_id=conv_id,
            conversation_ordinal=1,
            tool_call_id="call_a",
            tool_name="web_search",
            run_id=f"run_{uuid4().hex}",
        )
        with pytest.raises(ConversationOrdinalConflict):
            await store.record(
                org_id=org_id,
                conversation_id=conv_id,
                conversation_ordinal=1,
                tool_call_id="call_b",
                tool_name="web_search",
                run_id=f"run_{uuid4().hex}",
            )

    async def test_rejects_zero_ordinal(self, parent: PostgresRuntimeApiStore) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        with pytest.raises(ValueError):
            await store.record(
                org_id=org_id,
                conversation_id=conv_id,
                conversation_ordinal=0,
                tool_call_id="call_one",
                tool_name="web_search",
                run_id=f"run_{uuid4().hex}",
            )

    async def test_rejects_empty_tool_call_id(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        with pytest.raises(ValueError):
            await store.record(
                org_id=org_id,
                conversation_id=conv_id,
                conversation_ordinal=1,
                tool_call_id="",
                tool_name="web_search",
                run_id=f"run_{uuid4().hex}",
            )


class TestLoad:
    """``load`` semantics against real Postgres."""

    async def test_returns_bindings_sorted_by_ordinal(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        run_id = f"run_{uuid4().hex}"
        for ordinal, call_id in [
            (2, "call_b"),
            (1, "call_a"),
            (3, "call_c"),
        ]:
            await store.record(
                org_id=org_id,
                conversation_id=conv_id,
                conversation_ordinal=ordinal,
                tool_call_id=call_id,
                tool_name="web_search",
                run_id=run_id,
            )
        rows = await store.load(org_id=org_id, conversation_id=conv_id)
        assert [r.conversation_ordinal for r in rows] == [1, 2, 3]
        assert [r.tool_call_id for r in rows] == ["call_a", "call_b", "call_c"]

    async def test_returns_empty_for_unknown_conversation(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        org_id, _conv_id = await _seed_conversation(parent)
        store = PostgresConversationToolOrdinalStore(parent)
        rows = await store.load(org_id=org_id, conversation_id="conv_does_not_exist")
        assert rows == ()

    async def test_isolates_conversations(
        self, parent: PostgresRuntimeApiStore
    ) -> None:
        # Same ordinal across two conversations is two distinct
        # bindings; ``load`` must not bleed across conversation_id.
        # Use the same org so we exercise the conversation_id filter
        # specifically (RLS handles the cross-org case separately).
        suffix = uuid4().hex
        store = PostgresConversationToolOrdinalStore(parent)
        # Two conversations under the same org.
        org_id_left, conv_left = await _seed_conversation(parent, suffix=suffix)
        # Reuse the org by creating a second conversation under it
        # via a fresh assistant id.
        conversation = await parent.create_conversation(
            CreateConversationRequest(
                org_id=org_id_left,
                user_id=f"user_{suffix}",
                assistant_id=f"assistant_{suffix}_2",
            )
        )
        conv_right = conversation.conversation_id
        await store.record(
            org_id=org_id_left,
            conversation_id=conv_left,
            conversation_ordinal=1,
            tool_call_id="call_left",
            tool_name="web_search",
            run_id=f"run_{uuid4().hex}",
        )
        await store.record(
            org_id=org_id_left,
            conversation_id=conv_right,
            conversation_ordinal=1,
            tool_call_id="call_right",
            tool_name="web_search",
            run_id=f"run_{uuid4().hex}",
        )
        left = await store.load(org_id=org_id_left, conversation_id=conv_left)
        right = await store.load(org_id=org_id_left, conversation_id=conv_right)
        assert [r.tool_call_id for r in left] == ["call_left"]
        assert [r.tool_call_id for r in right] == ["call_right"]
