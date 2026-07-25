"""Live-Postgres D11 gate for a legal hold racing retention workers.

The gate is intentionally skip-gated: it needs a disposable database because
it applies migrations, writes real retention rows, and runs concurrent sweep
transactions.  It proves the production predicates and advisory fences rather
than a mocked SQL string.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from agent_runtime.persistence.records import (
    LegalHoldReasonCode,
    LegalHoldRecord,
    LegalHoldScope,
    RetentionKind,
)
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, MessageRecord, MessageRole


pytestmark = pytest.mark.skipif(
    not os.environ.get("MERGE_LIVE_TEST_DATABASE_URL"),
    reason="Set MERGE_LIVE_TEST_DATABASE_URL to run the D11 live retention gate.",
)

_ORG = "org_legal_hold_live"
_USER = "user_legal_hold_live"
_TABLES = (
    "runtime_deletion_evidence",
    "runtime_legal_holds",
    "runtime_audit_log",
    "runtime_context_payloads",
    "runtime_memory_items",
    "runtime_memory_scopes",
    "runtime_events",
    "agent_runs",
    "agent_messages",
    "agent_conversations",
)


def _truncate(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        try:
            connection.execute("TRUNCATE TABLE " + ", ".join(_TABLES) + " CASCADE")
        except psycopg.errors.UndefinedTable:
            pass


@pytest.fixture
def database_url() -> str:
    return os.environ["MERGE_LIVE_TEST_DATABASE_URL"]


@pytest.fixture(autouse=True)
def _clean(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Never inherit a developer/CI signing key into this destructive live test.
    # The audit-chain package supplies a documented, intentionally public dev
    # sentinel outside production, which is sufficient to verify chain writes.
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
    monkeypatch.delenv("AUDIT_HMAC_KEY_VERSION", raising=False)
    _truncate(database_url)
    yield
    _truncate(database_url)


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    instance = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=2,
        pool_max_size=8,
        pool_acquire_timeout_seconds=10.0,
    )
    await instance.open()
    try:
        await instance.migrate()
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def raw(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def _audit(*, action: str, hold_id: str) -> dict[str, object]:
    return {
        "org_id": _ORG,
        "user_id": "user_retention_admin",
        "actor_type": "user",
        "action": action,
        "resource_type": "legal_hold",
        "resource_id": hold_id,
        "outcome": "success",
        "metadata": {"scope": "conversation", "reason_code": "legal_request"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class TestLegalHoldRetentionRaceLive:
    async def test_active_hold_wins_concurrent_retention_then_release_allows_due_row(
        self, store: PostgresRuntimeApiStore, raw: psycopg.Connection
    ) -> None:
        conversation = await store.create_conversation(
            CreateConversationRequest(
                org_id=_ORG,
                user_id=_USER,
                assistant_id="assistant_legal_hold_live",
            )
        )
        message = await store.append_message(
            MessageRecord(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                role=MessageRole.USER,
                content_text="must survive while held",
            )
        )
        raw.execute(
            "UPDATE agent_messages SET retention_until = NOW() - interval '1 hour' "
            "WHERE id = %s",
            (message.message_id,),
        )
        hold = LegalHoldRecord(
            id="lh_live_race",
            org_id=_ORG,
            scope=LegalHoldScope.CONVERSATION,
            resource_id=conversation.conversation_id,
            subject_user_id=_USER,
            reason_code=LegalHoldReasonCode.LEGAL_REQUEST,
            created_by_user_id="user_retention_admin",
            create_idempotency_key="live-create-hold-001",
            create_request_digest=hashlib.sha256(b"live-create").hexdigest(),
        )
        await store.create_legal_hold(
            record=hold,
            audit_event=_audit(action="legal_hold.created", hold_id=hold.id),
        )

        # Multiple production-style sweep transactions race for the same due
        # row.  Every one must observe the active hold after taking its fence.
        outcomes = await asyncio.gather(
            *(
                store.sweep_retention_kind(
                    org_id=_ORG,
                    kind=RetentionKind.MESSAGES,
                    ttl_seconds=0,
                    chunk_size=1,
                )
                for _ in range(4)
            )
        )
        assert all(outcome.tombstoned == 0 for outcome in outcomes)
        assert raw.execute(
            "SELECT status, deleted_at FROM agent_messages WHERE id = %s",
            (message.message_id,),
        ).fetchone() == {"status": "created", "deleted_at": None}

        released = await store.release_legal_hold(
            org_id=_ORG,
            hold_id=hold.id,
            expected_revision=1,
            released_by_user_id="user_retention_admin",
            idempotency_key="live-release-hold-001",
            request_digest=hashlib.sha256(b"live-release").hexdigest(),
            released_at=datetime.now(timezone.utc),
            audit_event=_audit(action="legal_hold.released", hold_id=hold.id),
        )
        assert released is not None and released.hold.released_at is not None

        # The row was already beyond its ordinary retention grace. Releasing a
        # hold does not erase it; it makes the regular policy eligible again.
        outcome = await store.sweep_retention_kind(
            org_id=_ORG,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=0,
            chunk_size=1,
        )
        assert outcome.tombstoned == 1
        assert raw.execute(
            "SELECT status FROM agent_messages WHERE id = %s", (message.message_id,)
        ).fetchone() == {"status": "deleted"}
