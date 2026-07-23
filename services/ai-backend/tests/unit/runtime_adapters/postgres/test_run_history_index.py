"""DB-gated tests for the PRD-05 run-history read against real Postgres.

Skipped silently when ``TEST_DATABASE_URL`` is unset — same pattern as the rest
of this directory. Exercises the behaviours the in-memory / file conformance
suite cannot: that the new ``idx_agent_runs_org_user_created`` index actually
drives the keyset scan, and that the join excludes soft-deleted conversations
on the real engine.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRequestContext,
)


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required for the run-history index tests.",
    ),
]


@pytest.fixture
async def store() -> AsyncIterator[PostgresRuntimeApiStore]:
    s = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        pool_min_size=1,
        pool_max_size=5,
        pool_acquire_timeout_seconds=10.0,
    )
    await s.open()
    try:
        await s.migrate()
        yield s
    finally:
        await s.close()


def _runtime_context(
    *, org_id: str, user_id: str, run_suffix: str
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
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
        run_id=f"run_{run_suffix}",
        trace_id=f"trace_{run_suffix}",
    )


async def _seed_run(store, *, org_id, user_id, conversation, idem):
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_input="hello",
        idempotency_key=idem,
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=RuntimeRequestContext(
            roles=("Admin",), permission_scopes=("Search:Read",)
        ),
    )
    request = client_request.model_copy(
        update={
            "runtime_context": _runtime_context(
                org_id=org_id, user_id=user_id, run_suffix=idem
            )
        }
    )
    run, _msg, _created = await store.create_run_with_user_message(
        request=request, conversation=conversation
    )
    return run


async def test_explain_uses_org_user_created_index(store) -> None:
    """The run-history keyset query is driven by ``idx_agent_runs_org_user_created``."""
    suffix = uuid4().hex
    org_id = f"org_{suffix}"
    user_id = f"user_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id, user_id=user_id, assistant_id="assistant", title="idx"
        )
    )
    for index in range(5):
        await _seed_run(
            store,
            org_id=org_id,
            user_id=user_id,
            conversation=conversation,
            idem=f"r{index}",
        )

    query = """
        SELECT r.*, c.title AS conversation_title
          FROM agent_runs r
          JOIN agent_conversations c
            ON c.id = r.conversation_id AND c.org_id = r.org_id
         WHERE r.org_id  = %(org_id)s
           AND r.user_id = %(user_id)s
           AND c.deleted_at IS NULL
           AND (%(before_created_at)s IS NULL
                OR (r.created_at, r.id) < (%(before_created_at)s, %(before_run_id)s))
         ORDER BY r.created_at DESC, r.id DESC
         LIMIT %(limit)s
    """
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        # Bind RLS tenant + force the planner off seq-scan so the index — not a
        # seq-scan the planner prefers on a tiny table — is the driving scan.
        conn.execute("SET app.current_org_id = %s", (org_id,))
        conn.execute("SET enable_seqscan = off")
        cur = conn.execute(
            "EXPLAIN " + query,
            {
                "org_id": org_id,
                "user_id": user_id,
                "before_created_at": None,
                "before_run_id": None,
                "limit": 50,
            },
        )
        plan = "\n".join(str(row[0]) for row in cur.fetchall())
    assert "idx_agent_runs_org_user_created" in plan, plan


async def test_join_excludes_soft_deleted_conversation(store) -> None:
    suffix = uuid4().hex
    org_id = f"org_{suffix}"
    user_id = f"user_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id, user_id=user_id, assistant_id="assistant", title="join"
        )
    )
    await _seed_run(
        store, org_id=org_id, user_id=user_id, conversation=conversation, idem="one"
    )
    before = await store.list_runs_for_org(org_id=org_id, user_id=user_id, limit=50)
    assert len(before) == 1

    await store.soft_delete_conversation(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation.conversation_id,
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    after = await store.list_runs_for_org(org_id=org_id, user_id=user_id, limit=50)
    assert after == ()


async def test_completed_run_is_returned(store) -> None:
    suffix = uuid4().hex
    org_id = f"org_{suffix}"
    user_id = f"user_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id, user_id=user_id, assistant_id="assistant", title="done"
        )
    )
    run = await _seed_run(
        store, org_id=org_id, user_id=user_id, conversation=conversation, idem="done"
    )
    await store.update_run_status(run_id=run.run_id, status=AgentRunStatus.COMPLETED)
    history = await store.list_runs_for_org(org_id=org_id, user_id=user_id, limit=50)
    assert len(history) == 1
    assert history[0].status is AgentRunStatus.COMPLETED
    assert history[0].conversation_title == "done"
