"""DB-gated Postgres coverage for the context-occupancy ledger (design §5).

The backend-agnostic behaviours — attempt-keyed identity, oldest-first ordering,
the scope filter — are exercised against ``in_memory`` and ``file`` by
``tests/unit/runtime_adapters/test_context_occupancy_stores.py``, and the SQL is
pinned statically by
``tests/unit/agent_runtime/persistence/test_context_occupancy_migration.py``.
Neither of those can prove the three properties that only a real database has:

1. **The UNIQUE constraint is what makes the append idempotent.** The other
   backends dedupe in a Python dict; here ``ON CONFLICT DO NOTHING`` has to do
   it, and a wrong conflict target would silently write duplicate rows.
2. **Retention needs no new class** (§5) only because both foreign keys cascade.
   A missing cascade is invisible until an occupancy row outlives the
   conversation it describes — a deletion-completeness defect, not a bug report.
3. **The row is immutable and tenant-isolated.** RLS plus a grant of
   ``SELECT, INSERT`` and nothing else is a compliance claim, and a claim that is
   only asserted against migration text is not evidence.

Skipped silently when ``TEST_DATABASE_URL`` is unset, exactly like the rest of
this directory.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRequestContext,
)


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required for the context-occupancy store tests.",
    ),
]


@pytest.fixture
async def store() -> AsyncIterator[PostgresRuntimeApiStore]:
    adapter = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        pool_min_size=1,
        pool_max_size=5,
        pool_acquire_timeout_seconds=10.0,
    )
    await adapter.open()
    try:
        await adapter.migrate()
        yield adapter
    finally:
        await adapter.close()


class ContextOccupancyPostgresMixin:
    """Seeds real parent rows and builds occupancy snapshots against them.

    Parents are created through the store's own APIs rather than raw inserts:
    the foreign keys have to hold against the rows the runtime actually writes,
    and a hand-rolled fixture row can satisfy a constraint that production data
    would not.
    """

    CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    WINDOW = 200_000

    async def conversation(
        self, store: PostgresRuntimeApiStore, *, org_id: str, user_id: str
    ) -> ConversationRecord:
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=org_id,
                user_id=user_id,
                assistant_id="assistant",
                title="occupancy",
            )
        )

    async def run(
        self,
        store: PostgresRuntimeApiStore,
        *,
        conversation: ConversationRecord,
        org_id: str,
        user_id: str,
        suffix: str,
    ) -> str:
        client_request = CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=org_id,
            user_id=user_id,
            user_input="hello",
            idempotency_key=suffix,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            request_context=RuntimeRequestContext(
                roles=("Admin",), permission_scopes=("Search:Read",)
            ),
        )
        request = client_request.model_copy(
            update={
                "runtime_context": AgentRuntimeContext(
                    user_id=user_id,
                    org_id=org_id,
                    roles=("Admin",),
                    model_profile={
                        "provider": "openai",
                        "model_name": "gpt-5.4-mini",
                        "max_input_tokens": 128000,
                        "timeout_seconds": 30,
                        "temperature": 0,
                        "supports_streaming": True,
                    },
                    run_id=f"run_{suffix}",
                    trace_id=f"trace_{suffix}",
                )
            }
        )
        run, _message, _created = await store.create_run_with_user_message(
            request=request, conversation=conversation
        )
        return run.run_id

    def occupancy(
        self,
        *,
        org_id: str,
        run_id: str,
        conversation_id: str,
        model_call_id: str,
        attempt_ordinal: int = 1,
        graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT,
        estimated_input_tokens: int = 1_200,
        provider_input_tokens: int | None = 1_180,
        context_window_tokens: int | None = WINDOW,
        created_at: datetime | None = None,
        record_id: str | None = None,
    ) -> RuntimeContextOccupancyRecord:
        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=org_id,
            run_id=run_id,
            conversation_id=conversation_id,
            model_call_id=model_call_id,
            attempt_ordinal=attempt_ordinal,
            graph_scope=graph_scope,
            provider="anthropic",
            model_family="claude-opus-5",
            context_window_tokens=context_window_tokens,
            estimated_input_tokens=estimated_input_tokens,
            provider_input_tokens=provider_input_tokens,
            segments=(
                {
                    "segment_class": "tools",
                    "label": "agent_runtime.capabilities.backends:publish_artifact",
                    "lifecycle": "resident",
                    "detail": "publish_artifact",
                    "byte_count": 2_600,
                    "estimated_tokens": 650,
                    "item_count": 1,
                    "counter_source": "tokenizer",
                },
            ),
            record_id=record_id,
            created_at=created_at or self.CREATED_AT,
        )

    def sql(self, statement: str, parameters: tuple[object, ...] = ()) -> object:
        """Run one statement as the table owner, outside the store's RLS scope.

        Used only for the referential-action tests: a cascade is something the
        *database* does when a parent disappears, and there is no store method
        that hard-deletes a run or a conversation — by design, since the product
        tombstones history rather than erasing it.
        """

        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
            cursor = conn.execute(statement, parameters)
            if cursor.description is None:
                return None
            return cursor.fetchall()


class TestContextOccupancyAppend(ContextOccupancyPostgresMixin):
    async def test_the_unique_constraint_makes_the_append_idempotent(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        first = self.occupancy(
            org_id=org_id,
            run_id=run_id,
            conversation_id=conversation.conversation_id,
            model_call_id=f"call_{suffix}",
            record_id=f"row1_{suffix}",
        )

        assert await store.append_context_occupancy(first) is True
        # The same measured attempt redelivered under a fresh transport id. The
        # conflict target is the natural key, so this must not become a row.
        assert (
            await store.append_context_occupancy(
                self.occupancy(
                    org_id=org_id,
                    run_id=run_id,
                    conversation_id=conversation.conversation_id,
                    model_call_id=f"call_{suffix}",
                    record_id=f"row2_{suffix}",
                    estimated_input_tokens=99,
                    provider_input_tokens=99,
                )
            )
            is False
        )
        # A model retry is a different context against a different window, so it
        # earns a second row rather than overwriting the first (§6.3).
        assert await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation.conversation_id,
                model_call_id=f"call_{suffix}",
                attempt_ordinal=2,
                created_at=self.CREATED_AT + timedelta(seconds=1),
            )
        )

        rows = await store.list_context_occupancy(org_id=org_id, run_id=run_id)
        assert [row.attempt_ordinal for row in rows] == [1, 2]
        # DO NOTHING, not DO UPDATE: the first measurement is what survives.
        assert rows[0].id == f"row1_{suffix}"
        assert rows[0].estimated_input_tokens == 1_200

    async def test_null_counts_survive_the_column_round_trip(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation.conversation_id,
                model_call_id=f"call_{suffix}",
                provider_input_tokens=None,
                context_window_tokens=None,
            )
        )

        (row,) = await store.list_context_occupancy(org_id=org_id, run_id=run_id)

        # "Absent from the pricing catalog" and "the provider reported nothing"
        # must not read back as a confident zero.
        assert row.provider_input_tokens is None
        assert row.context_window_tokens is None
        assert row.free_tokens is None
        assert row.unattributed_delta == 0
        assert row.segments[0]["detail"] == "publish_artifact"

    async def test_a_negative_residual_is_stored_signed(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation.conversation_id,
                model_call_id=f"call_{suffix}",
                estimated_input_tokens=1_200,
                provider_input_tokens=1_180,
            )
        )

        (row,) = await store.list_context_occupancy(org_id=org_id, run_id=run_id)

        # No non-negative CHECK on this column, deliberately: clamping it would
        # hide the tokenizer over-count it exists to expose.
        assert row.unattributed_delta == -20
        assert row.free_tokens == self.WINDOW - 1_180


class TestContextOccupancyRead(ContextOccupancyPostgresMixin):
    async def test_the_series_is_oldest_first_and_narrows_to_one_window(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        for index, scope in enumerate(
            (
                RuntimeContextGraphScope.ROOT,
                RuntimeContextGraphScope.SUBAGENT,
                RuntimeContextGraphScope.ROOT,
            )
        ):
            await store.append_context_occupancy(
                self.occupancy(
                    org_id=org_id,
                    run_id=run_id,
                    conversation_id=conversation.conversation_id,
                    model_call_id=f"call_{index}_{suffix}",
                    graph_scope=scope,
                    created_at=self.CREATED_AT + timedelta(seconds=index),
                )
            )

        every_scope = await store.list_context_occupancy(org_id=org_id, run_id=run_id)
        assert [row.model_call_id for row in every_scope] == [
            f"call_0_{suffix}",
            f"call_1_{suffix}",
            f"call_2_{suffix}",
        ]

        root_only = await store.list_context_occupancy(
            org_id=org_id, run_id=run_id, graph_scope=RuntimeContextGraphScope.ROOT
        )
        # A subagent's window is a different denominator; the filter is what
        # keeps a caller from summing two of them (§6.2).
        assert [row.model_call_id for row in root_only] == [
            f"call_0_{suffix}",
            f"call_2_{suffix}",
        ]
        child_only = await store.list_context_occupancy(
            org_id=org_id, run_id=run_id, graph_scope=RuntimeContextGraphScope.SUBAGENT
        )
        assert [row.model_call_id for row in child_only] == [f"call_1_{suffix}"]

    async def test_another_tenants_run_is_never_readable(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        mine, theirs = uuid4().hex, uuid4().hex
        my_org, their_org = f"org_{mine}", f"org_{theirs}"
        my_conversation = await self.conversation(
            store, org_id=my_org, user_id=f"user_{mine}"
        )
        their_conversation = await self.conversation(
            store, org_id=their_org, user_id=f"user_{theirs}"
        )
        my_run = await self.run(
            store,
            conversation=my_conversation,
            org_id=my_org,
            user_id=f"user_{mine}",
            suffix=mine,
        )
        their_run = await self.run(
            store,
            conversation=their_conversation,
            org_id=their_org,
            user_id=f"user_{theirs}",
            suffix=theirs,
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=their_org,
                run_id=their_run,
                conversation_id=their_conversation.conversation_id,
                model_call_id=f"call_{theirs}",
            )
        )

        # Even with their run id in hand — model-call and run ids are opaque,
        # so tenancy cannot rest on them being unguessable.
        assert await store.list_context_occupancy(org_id=my_org, run_id=their_run) == ()
        assert await store.list_context_occupancy(org_id=my_org, run_id=my_run) == ()
        theirs_read = await store.list_context_occupancy(
            org_id=their_org, run_id=their_run
        )
        assert [row.model_call_id for row in theirs_read] == [f"call_{theirs}"]


class TestContextOccupancyRetention(ContextOccupancyPostgresMixin):
    async def test_rows_leave_with_their_run(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        # ``agent_messages.run_id`` and ``agent_runs.user_message_id`` reference
        # each other, so the pointer back from the message is cleared first;
        # that is bookkeeping for the circular pair, not part of what is being
        # proven. What is proven is that once the run row goes, the composite
        # (org_id, run_id) foreign key takes the occupancy row with it.
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation.conversation_id,
                model_call_id=f"call_{suffix}",
            )
        )
        assert await store.list_context_occupancy(org_id=org_id, run_id=run_id)

        self.sql(
            "UPDATE agent_messages SET run_id = NULL WHERE org_id = %s AND run_id = %s",
            (org_id, run_id),
        )
        self.sql(
            "DELETE FROM agent_runs WHERE org_id = %s AND id = %s", (org_id, run_id)
        )

        assert await store.list_context_occupancy(org_id=org_id, run_id=run_id) == ()

    async def test_a_run_rekey_carries_the_row_with_it(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        # ON UPDATE CASCADE on the composite key is why this table is absent
        # from the account-merge re-keyer's simple-table list: an approved
        # re-key moves occupancy with `agent_runs` and needs no second mutable
        # write path into a row the application role cannot UPDATE.
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        survivor_org = f"survivor_{suffix}"
        conversation = await self.conversation(store, org_id=org_id, user_id=user_id)
        run_id = await self.run(
            store,
            conversation=conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation.conversation_id,
                model_call_id=f"call_{suffix}",
            )
        )

        self.sql(
            "UPDATE agent_runs SET org_id = %s WHERE org_id = %s AND id = %s",
            (survivor_org, org_id, run_id),
        )

        assert await store.list_context_occupancy(org_id=org_id, run_id=run_id) == ()
        moved = await store.list_context_occupancy(org_id=survivor_org, run_id=run_id)
        assert [row.model_call_id for row in moved] == [f"call_{suffix}"]

    async def test_a_row_cannot_be_stamped_onto_a_foreign_tenants_run(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        # Tenancy on this row is not a claim the caller makes, it is a key that
        # has to exist: the composite foreign key refuses (my org, their run),
        # so a mis-stamped snapshot fails at the write instead of landing where
        # a scoped read would later serve it.
        mine, theirs = uuid4().hex, uuid4().hex
        my_org, their_org = f"org_{mine}", f"org_{theirs}"
        their_conversation = await self.conversation(
            store, org_id=their_org, user_id=f"user_{theirs}"
        )
        their_run = await self.run(
            store,
            conversation=their_conversation,
            org_id=their_org,
            user_id=f"user_{theirs}",
            suffix=theirs,
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await store.append_context_occupancy(
                self.occupancy(
                    org_id=my_org,
                    run_id=their_run,
                    conversation_id=their_conversation.conversation_id,
                    model_call_id=f"call_{mine}",
                )
            )

    async def test_rows_leave_with_their_conversation_independently(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        # The conversation cascade is tested on a conversation that owns no run,
        # so only the conversation foreign key can remove the row. Deleting a
        # conversation that owns the run would prove nothing about which of the
        # two cascades fired.
        suffix = uuid4().hex
        org_id, user_id = f"org_{suffix}", f"user_{suffix}"
        run_conversation = await self.conversation(
            store, org_id=org_id, user_id=user_id
        )
        run_id = await self.run(
            store,
            conversation=run_conversation,
            org_id=org_id,
            user_id=user_id,
            suffix=suffix,
        )
        orphan_conversation = await self.conversation(
            store, org_id=org_id, user_id=user_id
        )
        await store.append_context_occupancy(
            self.occupancy(
                org_id=org_id,
                run_id=run_id,
                conversation_id=orphan_conversation.conversation_id,
                model_call_id=f"call_{suffix}",
            )
        )

        self.sql(
            "DELETE FROM agent_conversations WHERE org_id = %s AND id = %s",
            (org_id, orphan_conversation.conversation_id),
        )

        surviving_runs = self.sql(
            "SELECT count(*) FROM agent_runs WHERE org_id = %s AND id = %s",
            (org_id, run_id),
        )
        assert surviving_runs == [(1,)]
        assert await store.list_context_occupancy(org_id=org_id, run_id=run_id) == ()

    async def test_the_application_role_holds_no_correcting_write(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        # An occupancy measurement is a fact about a request already sent, so
        # UPDATE and DELETE are withheld rather than merely unused. Cascading
        # deletes still work: referential actions run as the table owner.
        grants = self.sql(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'runtime_context_occupancy' "
            "AND grantee = 'enterprise_app' ORDER BY privilege_type"
        )

        assert grants == [("INSERT",), ("SELECT",)]
