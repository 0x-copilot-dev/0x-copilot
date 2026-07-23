"""DB-gated postgres coverage for the PRD-08 D1b tool-invocation ledger.

The backend-agnostic behaviours of ``record_tool_invocation`` +
``count_tool_invocations_for_runs`` + ``count_pending_approvals_for_runs`` are
exercised against ``in_memory`` and ``file`` by
``tests/unit/runtime_adapters/test_store_conformance.py``
(``TestToolInvocationLedgerConformance``). Those two backends need no external
service; the real Postgres adapter — whose SQL aggregates
(``postgres/runtime_api_store.py:1548,1581``) the in-memory/file stores cannot
prove — is exercised HERE, DB-gated, mirroring the same assertions.

Skipped silently when ``TEST_DATABASE_URL`` is unset, exactly like the rest of
this directory. Each test uses a fresh uuid-scoped org so it is isolated even
before the autouse truncation fixture in ``conftest.py`` runs.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    ToolInvocationRecord,
    ToolInvocationStatus,
)
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    ApprovalRequestRecord,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRequestContext,
)


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required for the tool-invocation ledger tests.",
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


async def _conv(store, *, org_id, user_id):
    return await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id, user_id=user_id, assistant_id="assistant", title="ti"
        )
    )


async def test_counts_steps_and_distinct_connectors_for_a_run(store) -> None:
    """7 invocations / 4 distinct connectors (2 native → None) → (7, 4)."""
    suffix = uuid4().hex
    org_id, user_id = f"org_{suffix}", f"user_{suffix}"
    conversation = await _conv(store, org_id=org_id, user_id=user_id)
    run = await _seed_run(
        store,
        org_id=org_id,
        user_id=user_id,
        conversation=conversation,
        idem="ti-count",
    )

    connectors = ["sheets", "safe", "dune", "sheets", "docs", None, None]
    for i, slug in enumerate(connectors):
        await store.record_tool_invocation(
            ToolInvocationRecord(
                run_id=run.run_id,
                org_id=org_id,
                tool_name=f"tool_{i}",
                connector_slug=slug,
                call_id=f"call_{i}",
            )
        )

    counts = await store.count_tool_invocations_for_runs(
        org_id=org_id, run_ids=[run.run_id]
    )
    assert counts[run.run_id] == (7, 4)


async def test_run_without_invocations_is_absent_from_the_map(store) -> None:
    suffix = uuid4().hex
    org_id, user_id = f"org_{suffix}", f"user_{suffix}"
    conversation = await _conv(store, org_id=org_id, user_id=user_id)
    run = await _seed_run(
        store,
        org_id=org_id,
        user_id=user_id,
        conversation=conversation,
        idem="ti-empty",
    )
    counts = await store.count_tool_invocations_for_runs(
        org_id=org_id, run_ids=[run.run_id]
    )
    # Absent — the service renders None/unknown, never (0, 0).
    assert run.run_id not in counts


async def test_upsert_is_idempotent_on_invocation_id(store) -> None:
    suffix = uuid4().hex
    org_id, user_id = f"org_{suffix}", f"user_{suffix}"
    conversation = await _conv(store, org_id=org_id, user_id=user_id)
    run = await _seed_run(
        store,
        org_id=org_id,
        user_id=user_id,
        conversation=conversation,
        idem="ti-upsert",
    )
    rec = ToolInvocationRecord(
        run_id=run.run_id,
        org_id=org_id,
        tool_name="tool_x",
        connector_slug="github",
        call_id="call_x",
    )
    await store.record_tool_invocation(rec)
    # Same invocation_id, settled — the ON CONFLICT (id) upsert must NOT insert a
    # second row.
    await store.record_tool_invocation(
        rec.model_copy(update={"status": ToolInvocationStatus.COMPLETED})
    )
    counts = await store.count_tool_invocations_for_runs(
        org_id=org_id, run_ids=[run.run_id]
    )
    assert counts[run.run_id] == (1, 1)


async def test_counts_pending_approvals_for_a_run(store) -> None:
    suffix = uuid4().hex
    org_id, user_id = f"org_{suffix}", f"user_{suffix}"
    conversation = await _conv(store, org_id=org_id, user_id=user_id)
    run = await _seed_run(
        store, org_id=org_id, user_id=user_id, conversation=conversation, idem="ti-appr"
    )

    for i in range(2):
        await store.create_approval_request(
            record=ApprovalRequestRecord(
                approval_id=f"appr_{suffix}_{i}",
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                org_id=org_id,
                user_id=user_id,
                metadata={"message": "approve a swap", "risk_level": "low"},
            )
        )

    pending = await store.count_pending_approvals_for_runs(
        org_id=org_id, run_ids=[run.run_id]
    )
    assert pending[run.run_id] == 2


async def test_tenant_isolation_on_the_aggregates(store) -> None:
    suffix = uuid4().hex
    org_id, user_id = f"org_{suffix}", f"user_{suffix}"
    conversation = await _conv(store, org_id=org_id, user_id=user_id)
    run = await _seed_run(
        store,
        org_id=org_id,
        user_id=user_id,
        conversation=conversation,
        idem="ti-tenant",
    )
    await store.record_tool_invocation(
        ToolInvocationRecord(
            run_id=run.run_id,
            org_id=org_id,
            tool_name="tool_a",
            connector_slug="sheets",
            call_id="call_a",
        )
    )
    # A different org must not see this run's counts (org predicate + RLS).
    counts = await store.count_tool_invocations_for_runs(
        org_id=f"org_other_{suffix}", run_ids=[run.run_id]
    )
    assert run.run_id not in counts
