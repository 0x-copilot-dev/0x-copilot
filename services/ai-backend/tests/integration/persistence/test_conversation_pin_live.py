"""LIVE-Postgres tests for the conversation ``pinned`` flag + Chats-list
projections (PRD-H.4 / PRD-J FR-J2.1c).

The pin flag (PR #154) and the ``preview`` / ``model`` projection reads
have unit-tested Python paths but their SQL had never executed against a
real Postgres in this effort. This suite proves:

1. the ``pinned`` column exists in the applied schema (baseline squash of
   migration 0034) with its partial index,
2. ``set_conversation_pinned`` persists across a full store close +
   reopen (a genuine reconnect — not the same pool),
3. the pin write is idempotent at the store layer: re-pinning does NOT
   bump ``updated_at`` (so the sidebar order is stable) while a real
   flip does, and
4. the Chats-list projection reads return the newest message
   (``get_latest_message_for_conversation``) and the latest run's model
   (``get_latest_run_for_conversation``), and ``list_conversations``
   carries ``pinned`` on the row.

Gated on ``MERGE_LIVE_TEST_DATABASE_URL`` — the same disposable cluster
the account-merge live gate boots (see
``test_account_merge_live.py``, whose fixture conventions this file
mirrors). Destructive — tables are truncated around each test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeRequestContext,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("MERGE_LIVE_TEST_DATABASE_URL"),
    reason=(
        "Set MERGE_LIVE_TEST_DATABASE_URL to a disposable Postgres database "
        "to exercise the conversation-pin live gate (PRD-J FR-J2.1c)."
    ),
)


# Same truncation list + idiom as test_account_merge_live.py so both
# suites can share the disposable database within one gate run.
_RUNTIME_TABLES = (
    "runtime_deletion_evidence",
    "runtime_legal_holds",
    "runtime_audit_log",
    "runtime_capability_snapshots",
    "runtime_compression_events",
    "runtime_context_payloads",
    "runtime_memory_items",
    "runtime_memory_scopes",
    "runtime_tool_invocations",
    "runtime_approval_requests",
    "runtime_subagent_results",
    "runtime_async_tasks",
    "runtime_consumer_cursors",
    "runtime_outbox_events",
    "runtime_events",
    "agent_runs",
    "agent_messages",
    "agent_conversations",
)


def _truncate_runtime_tables(database_url: str) -> None:
    statement = (
        "TRUNCATE TABLE " + ", ".join(_RUNTIME_TABLES) + " RESTART IDENTITY CASCADE"
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        try:
            conn.execute(statement)
        except psycopg.errors.UndefinedTable:
            pass


@pytest.fixture
def database_url() -> str:
    return os.environ["MERGE_LIVE_TEST_DATABASE_URL"]


@pytest.fixture(autouse=True)
def _clean_tables(database_url: str) -> Iterator[None]:
    _truncate_runtime_tables(database_url)
    yield
    _truncate_runtime_tables(database_url)


async def _open_store(database_url: str) -> PostgresRuntimeApiStore:
    store = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=2,
        pool_max_size=10,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    await store.migrate()
    return store


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    s = await _open_store(database_url)
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def raw(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        yield conn


_ORG = "org_pin_live"
_USER = "usr_pin_live"
_MODEL = "gpt-5.4-mini"


def _runtime_context(*, suffix: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=_USER,
        org_id=_ORG,
        roles=("Admin",),
        model_profile={
            "provider": "openai",
            "model_name": _MODEL,
            "max_input_tokens": 128000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id=f"run_{suffix}",
        trace_id=f"trace_{suffix}",
    )


async def _seed_conversation(store: PostgresRuntimeApiStore) -> str:
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=_ORG, user_id=_USER, assistant_id="assistant_pin"
        )
    )
    return conversation.conversation_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestPinnedColumnSchema:
    async def test_pinned_column_and_partial_index_exist(
        self, store: PostgresRuntimeApiStore, raw: psycopg.Connection
    ) -> None:
        cur = raw.execute(
            """
            SELECT column_default, is_nullable FROM information_schema.columns
            WHERE table_name = 'agent_conversations' AND column_name = 'pinned'
            """
        )
        row = cur.fetchone()
        assert row is not None, "pinned column missing from agent_conversations"
        assert row["is_nullable"] == "NO"
        assert row["column_default"] == "false"

        cur = raw.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'agent_conversations'
              AND indexname = 'idx_agent_conversations_org_user_pinned_updated'
            """
        )
        assert cur.fetchone() is not None


class TestPinPersistsAcrossReconnect:
    async def test_pin_survives_store_close_and_reopen(
        self, store: PostgresRuntimeApiStore, database_url: str
    ) -> None:
        conversation_id = await _seed_conversation(store)
        pinned = await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=True,
            now=_now(),
        )
        assert pinned is not None and pinned.pinned is True
        await store.close()

        reopened = await _open_store(database_url)
        try:
            fetched = await reopened.get_conversation(
                org_id=_ORG, user_id=_USER, conversation_id=conversation_id
            )
            assert fetched is not None
            assert fetched.pinned is True  # the COLUMN persisted, not a cache
        finally:
            await reopened.close()

    async def test_unpin_persists_too(
        self, store: PostgresRuntimeApiStore, raw: psycopg.Connection
    ) -> None:
        conversation_id = await _seed_conversation(store)
        await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=True,
            now=_now(),
        )
        await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=False,
            now=_now(),
        )
        cur = raw.execute(
            "SELECT pinned FROM agent_conversations WHERE id = %s",
            (conversation_id,),
        )
        assert cur.fetchone()["pinned"] is False


class TestPinIdempotency:
    async def test_redundant_pin_does_not_bump_updated_at(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        conversation_id = await _seed_conversation(store)
        first = await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=True,
            now=_now(),
        )
        assert first is not None
        # Re-pin with a LATER timestamp: IS DISTINCT FROM keeps updated_at.
        second = await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=True,
            now=_now(),
        )
        assert second is not None and second.pinned is True
        assert second.updated_at == first.updated_at

        # A real flip DOES bump it (newest-first sidebar reshuffles).
        flip_now = _now()
        third = await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation_id,
            pinned=False,
            now=flip_now,
        )
        assert third is not None and third.pinned is False
        assert third.updated_at == flip_now

    async def test_pin_scoped_to_owner(self, store: PostgresRuntimeApiStore) -> None:
        conversation_id = await _seed_conversation(store)
        stranger = await store.set_conversation_pinned(
            org_id=_ORG,
            user_id="usr_someone_else",
            conversation_id=conversation_id,
            pinned=True,
            now=_now(),
        )
        assert stranger is None  # no cross-user pin
        fetched = await store.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation_id
        )
        assert fetched is not None and fetched.pinned is False


class TestListProjections:
    async def test_preview_and_model_projection_reads(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid.uuid4().hex
        conversation = await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant_pin"
            )
        )
        client_request = CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="what's the plan for today?",
            model={"provider": "openai", "model_name": _MODEL},
            request_context=RuntimeRequestContext(
                roles=("Admin",), permission_scopes=("Search:Read",)
            ),
        )
        request = client_request.model_copy(
            update={"runtime_context": _runtime_context(suffix=suffix)}
        )
        run, _user_message, _created = await store.create_run_with_user_message(
            request=request, conversation=conversation
        )
        await store.append_message(
            MessageRecord(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                run_id=run.run_id,
                role=MessageRole.ASSISTANT,
                content_text="Here is the plan: ship J2.",
                content=(
                    {"type": "output_text", "text": "Here is the plan: ship J2."},
                ),
                trace_id=f"trace_{suffix}",
            )
        )

        latest_message = await store.get_latest_message_for_conversation(
            org_id=_ORG, conversation_id=conversation.conversation_id
        )
        assert latest_message is not None
        assert latest_message.content_text == "Here is the plan: ship J2."
        assert latest_message.role is MessageRole.ASSISTANT  # newest, not first

        latest_run = await store.get_latest_run_for_conversation(
            org_id=_ORG, conversation_id=conversation.conversation_id
        )
        assert latest_run is not None
        assert latest_run.run_id == run.run_id
        assert latest_run.model_name == _MODEL  # the ``model`` chip source

    async def test_list_conversations_carries_pinned(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        pinned_id = await _seed_conversation(store)
        other_id = await _seed_conversation(store)
        await store.set_conversation_pinned(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=pinned_id,
            pinned=True,
            now=_now(),
        )
        rows = await store.list_conversations(org_id=_ORG, user_id=_USER, limit=10)
        by_id = {record.conversation_id: record for record in rows}
        assert by_id[pinned_id].pinned is True
        assert by_id[other_id].pinned is False
