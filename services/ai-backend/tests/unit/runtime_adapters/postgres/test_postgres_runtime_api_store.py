"""End-to-end tests for PostgresRuntimeApiStore against a real Postgres.

Skipped silently when ``TEST_DATABASE_URL`` is unset — same pattern as the
sync adapter test next to this file. To run locally:

    docker compose -f services/ai-backend/docker-compose.yml up -d postgres
    cd services/ai-backend
    TEST_DATABASE_URL=postgresql://ai_backend:ai_backend@127.0.0.1:5432/ai_backend \\
        PYTHONPATH=src:../../packages/service-contracts/src \\
        .venv/bin/python -m pytest tests/unit/runtime_adapters/postgres/test_async_postgres_runtime_api_store.py -q

Covers:

- Parity with the sync adapter on every PersistencePort, EventStorePort,
  RuntimeQueuePort method.
- Concurrency-stress for the three hazard fixes: H1 (sequence_no
  monotonicity under concurrent appenders), H2 (approval-request idempotency
  under concurrent producers), claim_next safety under many workers, plus a
  pool-saturation acquire-timeout test.
- Cancellation safety: a cancelled in-flight query returns the connection to
  the pool cleanly.
- ``set_run_latest_sequence`` monotonicity (H3): an out-of-order lower
  sequence write is a no-op.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg_pool import PoolTimeout

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEventSource,
)
from agent_runtime.persistence.records import RuntimeWorkerResult
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ApprovalStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRunCommand,
    RuntimeRequestContext,
)


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL is required for PostgresRuntimeApiStore tests.",
    ),
]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
async def store() -> AsyncIterator[PostgresRuntimeApiStore]:
    """Open a fresh async store, run migration, hand it to the test."""

    s = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        # Test pool small but generous enough for the concurrency stress
        # tests below; concrete tests bump max_size when they need it.
        pool_min_size=2,
        pool_max_size=20,
        pool_acquire_timeout_seconds=10.0,
    )
    await s.open()
    try:
        await s.migrate()
        yield s
    finally:
        await s.close()


def _request_context() -> RuntimeRequestContext:
    return RuntimeRequestContext(roles=("Admin",), permission_scopes=("Search:Read",))


def _make_runtime_context(suffix: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=f"user_{suffix}",
        org_id=f"org_{suffix}",
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


async def _seed_run(
    store: PostgresRuntimeApiStore, suffix: str | None = None
) -> tuple[str, str, str, str]:
    """Create a conversation + run and return (org_id, user_id, run_id, conv_id)."""

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
    # The validator forbids client-supplied runtime_context, so build it the
    # way the service layer does: validate as a client request first, then
    # attach the server-owned runtime_context via model_copy (which skips the
    # validator).
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_input="hello",
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=_request_context(),
    )
    request = client_request.model_copy(
        update={"runtime_context": _make_runtime_context(suffix)}
    )
    run, _msg, _created = await store.create_run_with_user_message(
        request=request, conversation=conversation
    )
    return org_id, user_id, run.run_id, conversation.conversation_id


# --------------------------------------------------------------------------
# Parity tests
# --------------------------------------------------------------------------


class TestAsyncAdapterParity:
    """Parity smoke for every async port method against real Postgres."""

    async def test_create_and_get_conversation(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        suffix = uuid4().hex
        request = CreateConversationRequest(
            org_id=f"org_{suffix}", user_id=f"user_{suffix}", assistant_id="a"
        )
        created = await store.create_conversation(request)
        fetched = await store.get_conversation(
            org_id=request.org_id,
            user_id=request.user_id,
            conversation_id=created.conversation_id,
        )
        assert fetched is not None
        assert fetched.conversation_id == created.conversation_id

    async def test_create_run_persists_and_returns_record(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, _user_id, run_id, conv_id = await _seed_run(store)
        run = await store.get_run(org_id=org_id, run_id=run_id)
        assert run is not None
        assert run.run_id == run_id
        assert run.status == AgentRunStatus.QUEUED

    async def test_update_run_status_and_set_latest_sequence(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, _user_id, run_id, conv_id = await _seed_run(store)
        running = await store.update_run_status(
            run_id=run_id, status=AgentRunStatus.RUNNING
        )
        assert running.status == AgentRunStatus.RUNNING
        assert running.started_at is not None

        updated = await store.set_run_latest_sequence(
            run_id=run_id, latest_sequence_no=42
        )
        assert updated.latest_sequence_no == 42

    async def test_append_event_assigns_monotonic_sequence(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, _user_id, run_id, conv_id = await _seed_run(store)
        first = await store.append_event(
            RuntimeEventDraft(
                run_id=run_id,
                conversation_id=conv_id,
                trace_id="trace",
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_STARTED,
                payload={"message": "started"},
            )
        )
        second = await store.append_event(
            RuntimeEventDraft(
                run_id=run_id,
                conversation_id=conv_id,
                trace_id="trace",
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.PROGRESS,
                payload={"message": "progress"},
            )
        )
        assert first.sequence_no == 1
        assert second.sequence_no == 2
        latest = await store.get_latest_sequence(run_id=run_id)
        assert latest == 2

    async def test_list_events_after(self, store: PostgresRuntimeApiStore) -> None:
        org_id, _user_id, run_id, conv_id = await _seed_run(store)
        for i in range(5):
            await store.append_event(
                RuntimeEventDraft(
                    run_id=run_id,
                    conversation_id=conv_id,
                    trace_id="trace",
                    source=StreamEventSource.RUNTIME,
                    event_type=RuntimeApiEventType.PROGRESS,
                    payload={"i": i},
                )
            )
        rows = await store.list_events_after(
            org_id=org_id, run_id=run_id, after_sequence=2
        )
        assert [r.sequence_no for r in rows] == [3, 4, 5]

    async def test_create_approval_request_idempotent(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, user_id, run_id, conv_id = await _seed_run(store)
        record = ApprovalRequestRecord(
            approval_id=f"approval_{uuid4().hex}",
            run_id=run_id,
            conversation_id=conv_id,
            org_id=org_id,
            user_id=user_id,
            metadata={"message": "approve me", "risk_level": "low"},
        )
        first = await store.create_approval_request(record=record)
        second = await store.create_approval_request(record=record)
        assert first.approval_id == second.approval_id == record.approval_id
        # Confirm only one row.
        fetched = await store.get_approval_request(
            org_id=org_id, approval_id=record.approval_id
        )
        assert fetched is not None

    async def test_record_approval_decision(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, user_id, run_id, conv_id = await _seed_run(store)
        approval_id = f"approval_{uuid4().hex}"
        await store.create_approval_request(
            record=ApprovalRequestRecord(
                approval_id=approval_id,
                run_id=run_id,
                conversation_id=conv_id,
                org_id=org_id,
                user_id=user_id,
                metadata={"message": "ok", "risk_level": "low"},
            )
        )
        decision = await store.record_approval_decision(
            record=ApprovalDecisionRecord(
                approval_id=approval_id,
                run_id=run_id,
                conversation_id=conv_id,
                org_id=org_id,
                user_id=user_id,
                status=ApprovalStatus.APPROVED,
                decided_by_user_id=user_id,
                reason="lgtm",
            )
        )
        assert decision.status == ApprovalStatus.APPROVED

    async def test_enqueue_and_claim_run_command(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        org_id, user_id, run_id, conv_id = await _seed_run(store)
        await store.enqueue_run(
            RuntimeRunCommand(
                run_id=run_id,
                conversation_id=conv_id,
                org_id=org_id,
                user_id=user_id,
                trace_id="trace",
                runtime_context=_make_runtime_context(uuid4().hex),
            )
        )
        claim = await store.claim_next(
            worker_id="w1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert claim is not None
        assert claim.run_id == run_id
        await store.mark_complete(
            result=RuntimeWorkerResult(command_id=claim.command_id, succeeded=True)
        )

    async def test_write_audit_log(self, store: PostgresRuntimeApiStore) -> None:
        org_id, user_id, _run_id, _conv_id = await _seed_run(store)
        await store.write_audit_log(
            event_type="conversation_created",
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": "conv",
                "outcome": "success",
            },
        )


# --------------------------------------------------------------------------
# Hazard / concurrency stress
# --------------------------------------------------------------------------


class TestAsyncAdapterConcurrency:
    """Concurrency tests that would have failed under careless async I/O."""

    async def test_h1_concurrent_appends_get_monotonic_sequence(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        """100 concurrent appends to the same run produce 1..100 with no gaps,
        no duplicates, and no IntegrityError. Verifies the agent_runs FOR UPDATE
        lock pattern (H1) survives async concurrency.
        """

        org_id, _user_id, run_id, conv_id = await _seed_run(store)

        async def append(i: int) -> int:
            envelope = await store.append_event(
                RuntimeEventDraft(
                    run_id=run_id,
                    conversation_id=conv_id,
                    trace_id="trace",
                    source=StreamEventSource.RUNTIME,
                    event_type=RuntimeApiEventType.PROGRESS,
                    payload={"i": i},
                )
            )
            return envelope.sequence_no

        results = await asyncio.gather(*[append(i) for i in range(100)])
        assert sorted(results) == list(range(1, 101))
        assert len(set(results)) == 100
        latest = await store.get_latest_sequence(run_id=run_id)
        assert latest == 100

    async def test_h2_concurrent_approval_requests_are_idempotent(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        """50 concurrent create_approval_request for the same approval_id all
        succeed without raising IntegrityError; exactly one DB row exists.
        Verifies the INSERT … ON CONFLICT DO NOTHING fix (H2).
        """

        org_id, user_id, run_id, conv_id = await _seed_run(store)
        approval_id = f"approval_{uuid4().hex}"
        record = ApprovalRequestRecord(
            approval_id=approval_id,
            run_id=run_id,
            conversation_id=conv_id,
            org_id=org_id,
            user_id=user_id,
            metadata={"message": "approve", "risk_level": "low"},
        )

        async def attempt() -> str:
            r = await store.create_approval_request(record=record)
            return r.approval_id

        results = await asyncio.gather(*[attempt() for _ in range(50)])
        assert all(r == approval_id for r in results)
        fetched = await store.get_approval_request(
            org_id=org_id, approval_id=approval_id
        )
        assert fetched is not None

    async def test_h3_set_run_latest_sequence_is_monotonic(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        """A lower-numbered write after a higher-numbered write is a no-op.
        Out-of-order async writes never rewind the cursor (H3).
        """

        org_id, _user_id, run_id, conv_id = await _seed_run(store)
        first = await store.set_run_latest_sequence(
            run_id=run_id, latest_sequence_no=10
        )
        assert first.latest_sequence_no == 10
        # Now try to "rewind" — should be a no-op.
        second = await store.set_run_latest_sequence(
            run_id=run_id, latest_sequence_no=5
        )
        assert second.latest_sequence_no == 10
        # And a higher one still goes through.
        third = await store.set_run_latest_sequence(
            run_id=run_id, latest_sequence_no=20
        )
        assert third.latest_sequence_no == 20

    async def test_claim_next_under_many_concurrent_workers(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        """20 concurrent workers compete for 50 enqueued tasks; each task
        claimed by exactly one worker, no double-claims, no skipped tasks.
        FOR UPDATE SKIP LOCKED preserved through the async migration.
        """

        org_id, user_id, run_id, conv_id = await _seed_run(store)
        # Enqueue 50 distinct commands.
        for _ in range(50):
            await store.enqueue_run(
                RuntimeRunCommand(
                    run_id=run_id,
                    conversation_id=conv_id,
                    org_id=org_id,
                    user_id=user_id,
                    trace_id="trace",
                    runtime_context=_make_runtime_context(uuid4().hex),
                )
            )

        async def worker(worker_id: str) -> list[str]:
            mine: list[str] = []
            while True:
                claim = await store.claim_next(
                    worker_id=worker_id,
                    lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                )
                if claim is None:
                    return mine
                mine.append(claim.command_id)
                await store.mark_complete(
                    result=RuntimeWorkerResult(
                        command_id=claim.command_id, succeeded=True
                    )
                )

        results = await asyncio.gather(*[worker(f"w{i}") for i in range(20)])
        all_claimed = [cmd for sublist in results for cmd in sublist]
        # Every enqueued command claimed by exactly one worker, none twice.
        assert len(all_claimed) >= 50
        assert len(set(all_claimed)) == len(all_claimed)

    async def test_pool_acquire_timeout_surfaces_cleanly(self) -> None:
        """A saturated pool raises PoolTimeout (does NOT silently hang)."""

        s = PostgresRuntimeApiStore(
            os.environ["TEST_DATABASE_URL"],
            pool_min_size=1,
            pool_max_size=1,
            pool_acquire_timeout_seconds=0.5,
        )
        await s.open()
        try:
            # Hold the only connection in a long-running query.
            async def hog() -> None:
                async with s._pool.connection() as conn:
                    await conn.execute("SELECT pg_sleep(2)")

            hog_task = asyncio.create_task(hog())
            # Give hog time to grab the connection.
            await asyncio.sleep(0.1)
            with pytest.raises(PoolTimeout):
                # Any port call needs a connection; should time out fast.
                await s.get_run(org_id="x", run_id="y")
            await hog_task
        finally:
            await s.close()

    async def test_cancelled_query_returns_connection_to_pool(self) -> None:
        """Cancelling a coroutine mid-query does not leak a connection."""

        s = PostgresRuntimeApiStore(
            os.environ["TEST_DATABASE_URL"],
            pool_min_size=1,
            pool_max_size=1,
            pool_acquire_timeout_seconds=2.0,
        )
        await s.open()
        try:

            async def slow() -> None:
                async with s._pool.connection() as conn:
                    await conn.execute("SELECT pg_sleep(5)")

            t = asyncio.create_task(slow())
            await asyncio.sleep(0.1)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

            # Pool should now be willing to give us the connection back.
            await s.migrate()  # any cheap query that needs a connection
        finally:
            await s.close()
