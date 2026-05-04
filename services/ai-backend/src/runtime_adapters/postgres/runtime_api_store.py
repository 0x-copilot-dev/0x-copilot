"""Async Postgres-backed runtime API, event store, and durable queue adapter.

Built on ``psycopg.AsyncConnection`` and ``psycopg_pool.AsyncConnectionPool``.
Hazard-fix highlights:

- ``async with self._pool.connection() as conn:`` + ``async with
  conn.transaction():`` for transactional cancellation safety.
- ``append_event`` takes ``SELECT … FROM agent_runs … FOR UPDATE`` first
  (H1) so concurrent appends per run serialize on the ``agent_runs`` row
  lock; the UNIQUE on ``runtime_events(run_id, sequence_no)`` is the
  load-bearing safety net.
- ``create_approval_request`` uses ``INSERT … ON CONFLICT (id) DO NOTHING``
  followed by a fallback ``SELECT`` (H2). No check-then-insert race.
- ``set_run_latest_sequence`` is monotonic: ``UPDATE … WHERE id = $1 AND
  latest_sequence_no < $2``. Out-of-order writes never rewind the cursor (H3).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.observability.audit_chain import AuditChainSigner
from agent_runtime.execution.contracts import (
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
    StreamEventSource,
)
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.pool_metrics import PoolMetrics
from agent_runtime.persistence.records import (
    OutboxStatus,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
)
from agent_runtime.persistence.schema.migrate import MigrationRunner
from runtime_adapters.base import (
    RuntimeAdapterHelpers,
    StatusTransition,
    _Fields,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
    RuntimeRunCommand,
    RunRecord,
)


class _Columns:
    """SQL column-name constants for dict-row access."""

    ACTIVITY_KIND = "activity_kind"
    AGGREGATE_ID = "aggregate_id"
    ARCHIVED_AT = "archived_at"
    ASSISTANT_ID = "assistant_id"
    ATTACHMENTS_JSON = "attachments_json"
    ATTEMPTS = "attempts"
    BRANCH_ID = "branch_id"
    CANCELLED_AT = "cancelled_at"
    COMPLETED_AT = "completed_at"
    CONTENT_FORMAT = "content_format"
    CONTENT_JSON = "content_json"
    CONTENT_TEXT = "content_text"
    CONVERSATION_ID = "conversation_id"
    COUNT = "count"
    CREATED_AT = "created_at"
    DELETED_AT = "deleted_at"
    DISPLAY_TITLE = "display_title"
    EDITED_AT = "edited_at"
    EVENT_TYPE = "event_type"
    EXPIRES_AT = "expires_at"
    ID = "id"
    IDEMPOTENCY_KEY = "idempotency_key"
    LATEST = "latest"
    LATEST_SEQUENCE_NO = "latest_sequence_no"
    LOCK_EXPIRES_AT = "lock_expires_at"
    LOCKED_BY = "locked_by"
    METADATA_JSON = "metadata_json"
    METADATA_JSON_REDACTED = "metadata_json_redacted"
    MODEL_NAME = "model_name"
    MODEL_PROVIDER = "model_provider"
    NEXT_SEQUENCE = "next_sequence"
    ORG_ID = "org_id"
    PARENT_EVENT_ID = "parent_event_id"
    PARENT_MESSAGE_ID = "parent_message_id"
    PARENT_SPAN_ID = "parent_span_id"
    PARENT_TASK_ID = "parent_task_id"
    PAYLOAD_JSON = "payload_json"
    PAYLOAD_JSON_REDACTED = "payload_json_redacted"
    PRESENTATION_JSON = "presentation_json"
    QUOTE_JSON = "quote_json"
    REDACTION_STATE = "redaction_state"
    REQUEST_OPTIONS_JSON = "request_options_json"
    REQUEST_PAYLOAD_JSON_REDACTED = "request_payload_json_redacted"
    ROLE = "role"
    RUN_ID = "run_id"
    RUNTIME_CONTEXT_JSON = "runtime_context_json"
    SAFE_ERROR_CODE = "safe_error_code"
    SAFE_ERROR_MESSAGE = "safe_error_message"
    SCHEMA_VERSION = "schema_version"
    SEQUENCE_NO = "sequence_no"
    SOURCE = "source"
    SOURCE_MESSAGE_ID = "source_message_id"
    SPAN_ID = "span_id"
    STARTED_AT = "started_at"
    STATUS = "status"
    SUBAGENT_ID = "subagent_id"
    SUMMARY = "summary"
    TASK_ID = "task_id"
    TITLE = "title"
    TOKEN_COUNT = "token_count"
    TRACE_ID = "trace_id"
    UPDATED_AT = "updated_at"
    USER_CONTENT_TEXT = "user_content_text"
    USER_ID = "user_id"
    USER_MESSAGE_ID = "user_message_id"
    VISIBILITY = "visibility"


class _PoolEnv:
    """Env-var keys + defaults for runtime DB pool tuning (C4)."""

    POOL_MIN_SIZE = "RUNTIME_DB_POOL_MIN_SIZE"
    POOL_MAX_SIZE = "RUNTIME_DB_POOL_MAX_SIZE"
    POOL_ACQUIRE_TIMEOUT_SECONDS = "RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS"
    STATEMENT_TIMEOUT_MS = "RUNTIME_DB_STATEMENT_TIMEOUT_MS"
    LOCK_TIMEOUT_MS = "RUNTIME_DB_LOCK_TIMEOUT_MS"
    IDLE_IN_TXN_TIMEOUT_MS = "RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS"

    DEFAULT_POOL_MIN_SIZE = 5
    DEFAULT_POOL_MAX_SIZE = 50
    DEFAULT_POOL_ACQUIRE_TIMEOUT_SECONDS = 5.0
    DEFAULT_STATEMENT_TIMEOUT_MS = 10000
    DEFAULT_LOCK_TIMEOUT_MS = 3000
    DEFAULT_IDLE_IN_TXN_TIMEOUT_MS = 30000

    SERVICE_NAME = "ai-backend"

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def build_pool_kwargs(cls, *, role: str) -> dict[str, object]:
        """Return psycopg pool ``kwargs={...}`` with env-driven server guards.

        Includes statement_timeout (per-statement cap), lock_timeout (per-row
        wait cap), idle_in_transaction_session_timeout (server-side abort on
        long-idle txns), and application_name (greppable per service+role in
        ``pg_stat_activity``).
        """

        statement_timeout_ms = cls.env_int(
            cls.STATEMENT_TIMEOUT_MS, cls.DEFAULT_STATEMENT_TIMEOUT_MS
        )
        lock_timeout_ms = cls.env_int(cls.LOCK_TIMEOUT_MS, cls.DEFAULT_LOCK_TIMEOUT_MS)
        idle_in_txn_ms = cls.env_int(
            cls.IDLE_IN_TXN_TIMEOUT_MS, cls.DEFAULT_IDLE_IN_TXN_TIMEOUT_MS
        )
        return {
            "row_factory": dict_row,
            "options": (
                f"-c statement_timeout={statement_timeout_ms} "
                f"-c lock_timeout={lock_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={idle_in_txn_ms} "
                f"-c application_name={cls.SERVICE_NAME}:{role}"
            ),
        }


async def _take_runtime_audit_chain_lock_async(
    conn: psycopg.AsyncConnection,  # type: ignore[type-arg]
    *,
    org_id: str,
) -> None:
    """Serialize concurrent runtime_audit_log appends within one org chain.

    Two concurrent appends would otherwise both read the same prev_hash and
    fork the chain. ``pg_advisory_xact_lock`` releases at transaction commit
    so the lock scope matches the insert's atomic unit. The key is the high
    8 bytes of sha256("audit_chain:runtime_audit_log:<org_id>") interpreted
    as a signed int64 -- collisions between unrelated org chains are
    theoretically possible but harmless (extra serialization, never lost
    integrity).
    """

    import hashlib

    digest = hashlib.sha256(
        f"audit_chain:runtime_audit_log:{org_id}".encode("utf-8")
    ).digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))


async def _read_runtime_audit_chain_head_async(
    conn: psycopg.AsyncConnection,  # type: ignore[type-arg]
    *,
    org_id: str,
) -> tuple[int, bytes | None]:
    """Return ``(last_seq, last_signature)`` for the org's chain head.

    Returns ``(0, None)`` when no rows exist yet, signaling the chain is
    being started for this org. The advisory lock must already be held.
    """

    cur = await conn.execute(
        """
        SELECT seq, signature
          FROM runtime_audit_log
         WHERE org_id = %s
         ORDER BY seq DESC NULLS LAST
         LIMIT 1
        """,
        (org_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return 0, None
    last_seq = int(row["seq"]) if row.get("seq") is not None else 0
    sig = row.get("signature")
    last_sig = bytes(sig) if sig is not None else None
    return last_seq, last_sig


class PostgresRuntimeApiStore:
    """Async Postgres implementation of persistence, event store, and queue ports."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        pool: AsyncConnectionPool | None = None,
        role: str = "api",
        pool_min_size: int | None = None,
        pool_max_size: int | None = None,
        pool_acquire_timeout_seconds: float | None = None,
    ) -> None:
        if pool is None and database_url is None:
            raise ValueError("Either database_url or pool must be provided.")
        self.database_url = database_url
        self._role = role
        self._metrics = PoolMetrics(service=_PoolEnv.SERVICE_NAME, role=role)
        if pool is not None:
            self._pool = pool
            self._owns_pool = False
            self._metrics.bind_pool(pool)
        else:
            assert database_url is not None
            min_size = (
                pool_min_size
                if pool_min_size is not None
                else _PoolEnv.env_int(
                    _PoolEnv.POOL_MIN_SIZE, _PoolEnv.DEFAULT_POOL_MIN_SIZE
                )
            )
            max_size = (
                pool_max_size
                if pool_max_size is not None
                else _PoolEnv.env_int(
                    _PoolEnv.POOL_MAX_SIZE, _PoolEnv.DEFAULT_POOL_MAX_SIZE
                )
            )
            acquire_timeout = (
                pool_acquire_timeout_seconds
                if pool_acquire_timeout_seconds is not None
                else _PoolEnv.env_float(
                    _PoolEnv.POOL_ACQUIRE_TIMEOUT_SECONDS,
                    _PoolEnv.DEFAULT_POOL_ACQUIRE_TIMEOUT_SECONDS,
                )
            )
            self._pool = AsyncConnectionPool(
                conninfo=database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=acquire_timeout,
                kwargs=_PoolEnv.build_pool_kwargs(role=role),
                open=False,
            )
            self._owns_pool = True
            self._metrics.bind_pool(self._pool)

    async def open(self) -> None:
        """Open the underlying pool. Required when this store owns the pool."""

        if self._owns_pool:
            await self._pool.open()
            await self._pool.wait()

    async def close(self) -> None:
        """Close the connection pool when this store owns it."""

        if self._owns_pool:
            await self._pool.close()

    async def __aenter__(self) -> PostgresRuntimeApiStore:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def migrate(self) -> None:
        """Apply pending schema migrations.

        Delegates to the versioned ``yoyo``-backed runner. When
        ``RUNTIME_MIGRATIONS_AUTO_APPLY=false`` (production), this method
        becomes a no-op so a separate deploy step owns the apply. The legacy
        embedded SQL is no longer executed inline; the same DDL is now
        recorded as ``0001_initial_runtime_persistence.sql`` and
        ``0002_runtime_events_presentation.sql``.
        """

        if not MigrationRunner.auto_apply_enabled():
            return
        if self.database_url is None:
            # Pool provided externally without a connection string — caller is
            # expected to run migrations out-of-band. Common in tests where the
            # test harness sets up the schema independently.
            return
        # yoyo uses a sync DB driver; run it on a worker thread so the async
        # event loop stays free.
        import asyncio

        await asyncio.to_thread(MigrationRunner.apply, self.database_url)

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        """Create or idempotently return a scoped conversation."""

        async with self._pool.connection() as conn:
            async with conn.transaction():
                if request.idempotency_key is not None:
                    cur = await conn.execute(
                        """
                        SELECT * FROM agent_conversations
                        WHERE org_id = %s AND user_id = %s AND idempotency_key = %s
                        """,
                        (request.org_id, request.user_id, request.idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is not None:
                        return self._conversation_record(existing)

                record = ConversationRecord(
                    org_id=request.org_id,
                    user_id=request.user_id,
                    assistant_id=request.assistant_id,
                    title=request.title,
                    metadata=request.metadata,
                    idempotency_key=request.idempotency_key,
                )
                await conn.execute(
                    """
                    INSERT INTO agent_conversations (
                        id, org_id, user_id, assistant_id, title, status, created_at,
                        updated_at, archived_at, metadata_json, schema_version, idempotency_key
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.conversation_id,
                        record.org_id,
                        record.user_id,
                        record.assistant_id,
                        record.title,
                        record.status.value,
                        record.created_at,
                        record.updated_at,
                        record.archived_at,
                        Jsonb(record.metadata),
                        record.schema_version,
                        record.idempotency_key,
                    ),
                )
                return record

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation only when org and user scope match."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_conversations
                WHERE id = %s AND org_id = %s AND user_id = %s
                """,
                (conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return scoped conversations ordered by latest update."""

        archived_filter = "" if include_archived else "AND status <> 'archived'"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT * FROM agent_conversations
                WHERE org_id = %s AND user_id = %s {archived_filter}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (org_id, user_id, limit),
            )
            rows = await cur.fetchall()
        return tuple(self._conversation_record(row) for row in rows)

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return messages ordered by creation time."""

        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""
                SELECT * FROM agent_messages
                WHERE org_id = %s AND conversation_id = %s {deleted_filter}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (org_id, conversation_id, limit),
            )
            rows = await cur.fetchall()
        return tuple(self._message_record(row) for row in rows)

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a runtime-created message."""

        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._insert_message(conn, message)
                await conn.execute(
                    "UPDATE agent_conversations SET updated_at = %s WHERE id = %s",
                    (message.created_at, message.conversation_id),
                )
        return message

    async def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create a user message and run, or return an idempotent existing run."""

        context = request.runtime_context
        if context is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is required.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )

        async with self._pool.connection() as conn:
            # Single transaction for the whole multi-statement op (H4): if we
            # release the connection mid-way we lose atomicity.
            async with conn.transaction():
                if request.idempotency_key is not None:
                    cur = await conn.execute(
                        """
                        SELECT r.*, m.content_text AS user_content_text
                        FROM agent_runs r
                        JOIN agent_messages m ON m.id = r.user_message_id
                        WHERE r.org_id = %s AND r.user_id = %s AND r.idempotency_key = %s
                        """,
                        (context.org_id, context.user_id, request.idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is not None:
                        if (
                            existing[_Columns.CONVERSATION_ID],
                            existing[_Columns.USER_CONTENT_TEXT],
                        ) != (request.conversation_id, request.user_input):
                            raise RuntimeApiError(
                                RuntimeErrorCode.VALIDATION_ERROR,
                                Messages.Error.IDEMPOTENCY_CONFLICT,
                                http_status=status.HTTP_409_CONFLICT,
                                retryable=False,
                                correlation_id=context.trace_id,
                            )
                        run = self._run_record(existing)
                        msg_cur = await conn.execute(
                            "SELECT * FROM agent_messages WHERE id = %s",
                            (run.user_message_id,),
                        )
                        message_row = await msg_cur.fetchone()
                        return run, self._message_record(message_row), False

                # The lookup helpers below run inside the same connection /
                # transaction so they see in-flight inserts.
                async def _get_msg(message_id: str) -> MessageRecord | None:
                    cur = await conn.execute(
                        "SELECT * FROM agent_messages WHERE id = %s",
                        (message_id,),
                    )
                    row = await cur.fetchone()
                    return self._message_record(row) if row is not None else None

                async def _latest_msg_id(
                    org_id: str, conversation_id: str
                ) -> str | None:
                    cur = await conn.execute(
                        """
                        SELECT id FROM agent_messages
                        WHERE org_id = %s AND conversation_id = %s AND deleted_at IS NULL
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (org_id, conversation_id),
                    )
                    row = await cur.fetchone()
                    return row[_Columns.ID] if row is not None else None

                async def _latest_asst(
                    org_id: str, conversation_id: str, run_id: str
                ) -> str | None:
                    cur = await conn.execute(
                        """
                        SELECT id FROM agent_messages
                        WHERE org_id = %s AND conversation_id = %s AND run_id = %s
                          AND role = %s AND deleted_at IS NULL
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (
                            org_id,
                            conversation_id,
                            run_id,
                            MessageRole.ASSISTANT.value,
                        ),
                    )
                    row = await cur.fetchone()
                    return row[_Columns.ID] if row is not None else None

                user_message = await RuntimeAdapterHelpers.amessage_for_run_request(
                    request=request,
                    conversation=conversation,
                    aget_message=_get_msg,
                    aget_latest_message_id=_latest_msg_id,
                    afind_latest_assistant_for_run=_latest_asst,
                )
                run = RunRecord(
                    run_id=context.run_id,
                    conversation_id=conversation.conversation_id,
                    org_id=context.org_id,
                    user_id=context.user_id,
                    user_message_id=user_message.message_id,
                    idempotency_key=request.idempotency_key,
                    trace_id=context.trace_id,
                    model_provider=context.model_profile.provider,
                    model_name=context.model_profile.model_name,
                    runtime_context=context,
                    request_options=request.request_options,
                )
                if request.regenerate_from_message_id is None:
                    await self._insert_message(conn, user_message)
                await self._insert_run(conn, run)
                if request.regenerate_from_message_id is None:
                    await conn.execute(
                        "UPDATE agent_messages SET run_id = %s WHERE id = %s",
                        (run.run_id, user_message.message_id),
                    )
                await conn.execute(
                    "UPDATE agent_conversations SET updated_at = %s WHERE id = %s",
                    (run.created_at, conversation.conversation_id),
                )
                if request.regenerate_from_message_id is None:
                    user_message = user_message.model_copy(
                        update={"run_id": run.run_id}
                    )
                return run, user_message, True

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM agent_runs WHERE id = %s AND org_id = %s",
                (run_id, org_id),
            )
            row = await cur.fetchone()
        return self._run_record(row) if row is not None else None

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        """Update mutable run status with optimistic-lock CAS (C3).

        Reads ``row_version`` alongside the run row, then issues an UPDATE
        whose WHERE clause asserts the same version and bumps it. If a
        concurrent writer beat us to the row our UPDATE returns no rows; we
        raise :class:`ConcurrentRunUpdateError` so the worker's
        ``with_optimistic_retry`` helper can refetch and retry.
        """

        from agent_runtime.persistence.errors import ConcurrentRunUpdateError

        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM agent_runs WHERE id = %s", (run_id,)
                )
                existing = await cur.fetchone()
                timestamps = StatusTransition.timestamp_updates(
                    status,
                    already_started=existing[_Columns.STARTED_AT] is not None,
                )
                expected_version = int(existing["row_version"])
                updates: dict[str, object] = {
                    _Columns.STATUS: status.value,
                    **timestamps,
                }
                assignments = ", ".join(f"{key} = %s" for key in updates)
                cur = await conn.execute(
                    f"UPDATE agent_runs SET {assignments}, "
                    f"row_version = row_version + 1 "
                    f"WHERE id = %s AND row_version = %s RETURNING *",
                    (*updates.values(), run_id, expected_version),
                )
                row = await cur.fetchone()
                if row is None:
                    raise ConcurrentRunUpdateError(
                        run_id=run_id, expected_version=expected_version
                    )
        return self._run_record(row)

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for a run, monotonically (H3).

        The UPDATE only writes when the new value is strictly greater than the
        stored one. If two appends arrive out of order under async concurrency,
        the smaller-numbered one is a no-op and the cursor never goes
        backwards.
        """

        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    UPDATE agent_runs
                    SET latest_sequence_no = %s
                    WHERE id = %s
                      AND (latest_sequence_no IS NULL OR latest_sequence_no < %s)
                    RETURNING *
                    """,
                    (latest_sequence_no, run_id, latest_sequence_no),
                )
                row = await cur.fetchone()
                if row is None:
                    # No-op write (already at >= latest_sequence_no). Return
                    # the current record to honor the contract.
                    cur = await conn.execute(
                        "SELECT * FROM agent_runs WHERE id = %s",
                        (run_id,),
                    )
                    row = await cur.fetchone()
        return self._run_record(row)

    async def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist an approval decision against the approval request row."""

        decision_reason = record.reason if record.reason is not None else record.answer
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE runtime_approval_requests
                    SET status = %s, decided_by_user_id = %s, decision_reason = %s, decided_at = %s
                    WHERE id = %s AND org_id = %s
                    """,
                    (
                        record.status.value,
                        record.decided_by_user_id,
                        decision_reason,
                        record.decided_at,
                        record.approval_id,
                        record.org_id,
                    ),
                )
        return record

    async def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        """Persist a pending approval request, idempotent on ``approval_id`` (H2).

        Atomic upsert: ``INSERT … ON CONFLICT (id) DO NOTHING``. If the insert
        was a no-op (someone else got there first), fetch the existing row and
        return it. No check-then-insert race window.
        """

        risk_class = RuntimeAdapterHelpers.normalize_risk_class(record.metadata)
        action_summary = RuntimeAdapterHelpers.derive_action_summary(record.metadata)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    INSERT INTO runtime_approval_requests (
                        id,
                        run_id,
                        org_id,
                        requested_by_user_id,
                        status,
                        risk_class,
                        action_summary,
                        request_payload_json_redacted,
                        expires_at,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        record.approval_id,
                        record.run_id,
                        record.org_id,
                        record.user_id,
                        record.status.value,
                        risk_class,
                        action_summary,
                        Jsonb(record.metadata),
                        record.expires_at,
                        record.created_at,
                    ),
                )
                inserted = await cur.fetchone()
                if inserted is not None:
                    return record
                # Lost the race; return the row that won. Join agent_runs to
                # populate conversation_id / user_id like the original
                # adapter did.
                cur = await conn.execute(
                    """
                    SELECT a.*, r.conversation_id, r.user_id
                    FROM runtime_approval_requests a
                    JOIN agent_runs r ON r.id = a.run_id
                    WHERE a.id = %s AND a.org_id = %s
                    """,
                    (record.approval_id, record.org_id),
                )
                existing = await cur.fetchone()
        return ApprovalRequestRecord(
            approval_id=existing[_Columns.ID],
            run_id=existing[_Columns.RUN_ID],
            conversation_id=existing[_Columns.CONVERSATION_ID],
            org_id=existing[_Columns.ORG_ID],
            user_id=existing[_Columns.USER_ID],
            status=existing[_Columns.STATUS],
            created_at=existing[_Columns.CREATED_AT],
            expires_at=existing[_Columns.EXPIRES_AT],
            metadata=existing[_Columns.REQUEST_PAYLOAD_JSON_REDACTED] or {},
        )

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT a.*, r.conversation_id, r.user_id
                FROM runtime_approval_requests a
                JOIN agent_runs r ON r.id = a.run_id
                WHERE a.id = %s AND a.org_id = %s
                """,
                (approval_id, org_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return ApprovalRequestRecord(
            approval_id=row[_Columns.ID],
            run_id=row[_Columns.RUN_ID],
            conversation_id=row[_Columns.CONVERSATION_ID],
            org_id=row[_Columns.ORG_ID],
            user_id=row[_Columns.USER_ID],
            status=row[_Columns.STATUS],
            created_at=row[_Columns.CREATED_AT],
            expires_at=row[_Columns.EXPIRES_AT],
            metadata=row[_Columns.REQUEST_PAYLOAD_JSON_REDACTED] or {},
        )

    async def write_audit_log(
        self, *, event_type: str, record: dict[str, object]
    ) -> None:
        """Append an HMAC-chained audit record for security-relevant actions.

        The chain is per-(table, org_id) and serialized via
        ``pg_advisory_xact_lock`` so concurrent appends in the same org
        cannot fork. The signed payload is the canonical record minus the
        chain fields plus ``__event_type__`` to bind action identity.
        """

        data = record if isinstance(record, dict) else {_Fields.RECORD: str(record)}
        now = datetime.now(timezone.utc)
        raw_meta = data.get(_Fields.METADATA)
        metadata = raw_meta if isinstance(raw_meta, dict) else {}
        ts_ns = RuntimeAdapterHelpers.timestamp_ns(now)
        audit_id = str(data.get(_Fields.AUDIT_EVENT_ID) or f"audit_{ts_ns}")
        org_id = str(data.get(_Fields.ORG_ID, "unknown"))
        signer = AuditChainSigner.from_env()
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await _take_runtime_audit_chain_lock_async(conn, org_id=org_id)
                seq, prev_hash = await _read_runtime_audit_chain_head_async(
                    conn, org_id=org_id
                )
                payload = {
                    "audit_id": audit_id,
                    "org_id": org_id,
                    "user_id": data.get(_Fields.USER_ID),
                    "actor_type": str(data.get(_Fields.ACTOR_TYPE, "system")),
                    "action": event_type,
                    "resource_type": str(data.get(_Fields.RESOURCE_TYPE, "runtime")),
                    "resource_id": str(data.get(_Fields.RESOURCE_ID, "unknown")),
                    "run_id": data.get(_Fields.RUN_ID),
                    "trace_id": data.get(_Fields.TRACE_ID),
                    "outcome": str(data.get(_Fields.OUTCOME, "success")),
                    "metadata": metadata,
                    "created_at": now,
                    "__event_type__": event_type,
                }
                sig = signer.sign(prev_hash=prev_hash, payload=payload)
                await conn.execute(
                    """
                    INSERT INTO runtime_audit_log (
                        id, org_id, user_id, actor_type, action, resource_type, resource_id,
                        run_id, trace_id, outcome, metadata_json_redacted, created_at,
                        seq, prev_hash, signature, key_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        audit_id,
                        org_id,
                        data.get(_Fields.USER_ID)
                        if isinstance(data.get(_Fields.USER_ID), str)
                        else None,
                        str(data.get(_Fields.ACTOR_TYPE, "system")),
                        event_type,
                        str(data.get(_Fields.RESOURCE_TYPE, "runtime")),
                        str(data.get(_Fields.RESOURCE_ID, "unknown")),
                        data.get(_Fields.RUN_ID)
                        if isinstance(data.get(_Fields.RUN_ID), str)
                        else None,
                        data.get(_Fields.TRACE_ID)
                        if isinstance(data.get(_Fields.TRACE_ID), str)
                        else None,
                        str(data.get(_Fields.OUTCOME, "success")),
                        Jsonb(metadata),
                        now,
                        seq + 1,
                        sig.prev_hash,
                        sig.signature,
                        sig.key_version,
                    ),
                )

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while preserving audit/event evidence."""

        # TODO(legal-hold-race): the legal-hold check below is TOCTOU; another
        # writer can insert a hold between this SELECT and the UPDATEs that
        # follow. Pre-existing hazard, tracked separately from the async
        # migration.
        now = datetime.now(timezone.utc)
        ts_ns = RuntimeAdapterHelpers.timestamp_ns(now)
        audit_event_id = f"history_delete_{ts_ns}"
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    SELECT id FROM runtime_legal_holds
                    WHERE org_id = %s
                      AND released_at IS NULL
                      AND (
                        (scope = 'org' AND resource_id = %s)
                        OR (scope = 'user' AND user_id = %s)
                        OR (
                            scope = 'conversation'
                            AND resource_id IN (
                                SELECT id FROM agent_conversations WHERE org_id = %s AND user_id = %s
                            )
                        )
                      )
                    LIMIT 1
                    """,
                    (org_id, org_id, user_id, org_id, user_id),
                )
                hold = await cur.fetchone()
                if hold is not None:
                    raise RuntimeApiError(
                        RuntimeErrorCode.VALIDATION_ERROR,
                        "Deletion is blocked by an active legal hold.",
                        http_status=status.HTTP_409_CONFLICT,
                        retryable=False,
                    )
                cur = await conn.execute(
                    """
                    UPDATE agent_conversations
                    SET status = 'archived', archived_at = COALESCE(archived_at, %s), updated_at = %s
                    WHERE org_id = %s AND user_id = %s AND status <> 'archived'
                    """,
                    (now, now, org_id, user_id),
                )
                conversations_archived = cur.rowcount
                cur = await conn.execute(
                    """
                    UPDATE agent_messages
                    SET status = 'deleted', deleted_at = COALESCE(deleted_at, %s),
                        content_text = '[deleted by user request]'
                    WHERE org_id = %s
                      AND conversation_id IN (
                        SELECT id FROM agent_conversations WHERE org_id = %s AND user_id = %s
                      )
                      AND deleted_at IS NULL
                    """,
                    (now, org_id, org_id, user_id),
                )
                messages_tombstoned = cur.rowcount
                cur = await conn.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'cancelled', cancelled_at = COALESCE(cancelled_at, %s)
                    WHERE org_id = %s AND user_id = %s
                      AND status NOT IN ('cancelled', 'completed', 'failed', 'timed_out')
                    """,
                    (now, org_id, user_id),
                )
                runs_cancelled = cur.rowcount
                cur = await conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM runtime_events e
                    JOIN agent_runs r ON r.id = e.run_id
                    WHERE e.org_id = %s AND r.user_id = %s
                    """,
                    (org_id, user_id),
                )
                events_row = await cur.fetchone()
                events_retained = (
                    int(events_row[_Columns.COUNT]) if events_row is not None else 0
                )
                # Hold the chain lock and read the head BEFORE inserting,
                # then sign and insert with full chain fields. The outer
                # transaction is already open so the lock auto-releases on
                # commit alongside the rest of the deletion.
                signer = AuditChainSigner.from_env()
                await _take_runtime_audit_chain_lock_async(conn, org_id=org_id)
                last_seq, prev_hash = await _read_runtime_audit_chain_head_async(
                    conn, org_id=org_id
                )
                deletion_metadata = {
                    _Fields.REASON: reason,
                    _Fields.CONVERSATIONS_ARCHIVED: conversations_archived,
                    _Fields.MESSAGES_TOMBSTONED: messages_tombstoned,
                    _Fields.RUNS_CANCELLED: runs_cancelled,
                    _Fields.EVENTS_RETAINED: events_retained,
                }
                deletion_payload = {
                    "audit_id": audit_event_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "actor_type": "user",
                    "action": "user_history_deleted",
                    "resource_type": "user_history",
                    "resource_id": user_id,
                    "run_id": None,
                    "trace_id": None,
                    "outcome": "success",
                    "metadata": deletion_metadata,
                    "created_at": now,
                    "__event_type__": "user_history_deleted",
                }
                deletion_sig = signer.sign(
                    prev_hash=prev_hash, payload=deletion_payload
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_audit_log (
                        id, org_id, user_id, actor_type, action, resource_type, resource_id,
                        run_id, trace_id, outcome, metadata_json_redacted, created_at,
                        seq, prev_hash, signature, key_version
                    )
                    VALUES (%s, %s, %s, 'user', 'user_history_deleted', 'user_history', %s,
                            NULL, NULL, 'success', %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        audit_event_id,
                        org_id,
                        user_id,
                        user_id,
                        Jsonb(deletion_metadata),
                        now,
                        last_seq + 1,
                        deletion_sig.prev_hash,
                        deletion_sig.signature,
                        deletion_sig.key_version,
                    ),
                )
                evidence_id = f"deletion_evidence_{ts_ns}"
                await conn.execute(
                    """
                    INSERT INTO runtime_deletion_evidence (
                        id, org_id, user_id, request_type, reason, conversations_archived,
                        messages_tombstoned, runs_cancelled, events_retained, audit_event_id,
                        created_at
                    )
                    VALUES (%s, %s, %s, 'user_history_delete', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        evidence_id,
                        org_id,
                        user_id,
                        reason,
                        conversations_archived,
                        messages_tombstoned,
                        runs_cancelled,
                        events_retained,
                        audit_event_id,
                        now,
                    ),
                )
        return HistoryDeletionResponse(
            org_id=org_id,
            user_id=user_id,
            conversations_archived=conversations_archived,
            messages_tombstoned=messages_tombstoned,
            runs_cancelled=runs_cancelled,
            events_retained=events_retained,
            audit_event_id=audit_event_id,
        )

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with the next per-run sequence number (H1).

        Concurrent appenders for the same run serialize on the
        ``agent_runs(run_id)`` row lock acquired via ``SELECT … FOR UPDATE``.
        Inside that lock we read ``MAX(sequence_no)+1`` from
        ``runtime_events`` and INSERT, so the next appender (which blocks on
        the lock) sees the freshly committed row. The
        ``runtime_events(run_id, sequence_no)`` UNIQUE constraint is a backstop
        — if it ever fires, the lock pattern is broken.
        """

        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT org_id FROM agent_runs WHERE id = %s FOR UPDATE",
                    (event.run_id,),
                )
                run = await cur.fetchone()
                cur = await conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                    FROM runtime_events
                    WHERE run_id = %s
                    """,
                    (event.run_id,),
                )
                sequence_row = await cur.fetchone()
                activity_kind = (
                    event.activity_kind
                    or RuntimeEventPresentationProjector.activity_kind_for(
                        event_type=event.event_type,
                        source=event.source,
                    )
                )
                envelope = RuntimeEventEnvelope(
                    run_id=event.run_id,
                    conversation_id=event.conversation_id,
                    sequence_no=sequence_row[_Columns.NEXT_SEQUENCE],
                    source=event.source,
                    event_type=event.event_type,
                    trace_id=event.trace_id,
                    parent_event_id=event.parent_event_id,
                    span_id=event.span_id,
                    parent_span_id=event.parent_span_id,
                    parent_task_id=event.parent_task_id,
                    task_id=event.task_id,
                    subagent_id=event.subagent_id,
                    display_title=event.display_title,
                    summary=event.summary,
                    status=event.status,
                    activity_kind=activity_kind,
                    visibility=event.visibility,
                    redaction_state=event.redaction_state,
                    presentation=event.presentation,
                    payload=event.payload,
                    metadata=event.metadata,
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_events (
                        id, run_id, conversation_id, org_id, sequence_no, event_protocol_version,
                        source, event_type, parent_event_id, span_id, parent_span_id,
                        parent_task_id, task_id, subagent_id, display_title, summary, status,
                        trace_id, payload_json_redacted, metadata_json_redacted, visibility,
                        redaction_state, activity_kind, presentation_json, created_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        envelope.event_id,
                        envelope.run_id,
                        envelope.conversation_id,
                        run[_Columns.ORG_ID],
                        envelope.sequence_no,
                        envelope.event_protocol_version,
                        envelope.source.value,
                        envelope.event_type.value,
                        envelope.parent_event_id,
                        envelope.span_id,
                        envelope.parent_span_id,
                        envelope.parent_task_id,
                        envelope.task_id,
                        envelope.subagent_id,
                        envelope.display_title,
                        envelope.summary,
                        envelope.status,
                        envelope.trace_id,
                        Jsonb(envelope.payload),
                        Jsonb(envelope.metadata),
                        envelope.visibility.value,
                        envelope.redaction_state.value,
                        envelope.activity_kind,
                        Jsonb(envelope.presentation),
                        envelope.created_at,
                    ),
                )
        return envelope

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_events
                WHERE org_id = %s AND run_id = %s AND sequence_no > %s
                ORDER BY sequence_no ASC
                """,
                (org_id, run_id, after_sequence),
            )
            rows = await cur.fetchall()
        return tuple(self._event_envelope(row) for row in rows)

    async def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""

        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) AS latest FROM runtime_events WHERE run_id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
        return int(row[_Columns.LATEST])

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for workers."""

        await self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_REQUESTED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancellation command for workers."""

        await self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_CANCEL_REQUESTED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        """Enqueue an approval resolution command for workers."""

        await self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.APPROVAL_RESOLVED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        """Claim the next available runtime command using SKIP LOCKED."""

        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    WITH next_event AS (
                        SELECT id
                        FROM runtime_outbox_events
                        WHERE (
                            status IN ('pending', 'retry')
                            OR (status = 'claimed' AND lock_expires_at <= now())
                        )
                        AND available_at <= now()
                        ORDER BY available_at ASC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE runtime_outbox_events outbox
                    SET status = 'claimed',
                        attempts = outbox.attempts + 1,
                        locked_by = %s,
                        lock_expires_at = %s,
                        updated_at = now()
                    FROM next_event
                    WHERE outbox.id = next_event.id
                    RETURNING outbox.*
                    """,
                    (worker_id, lock_expires_at),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        payload = dict(row[_Columns.PAYLOAD_JSON])
        return RuntimeWorkerClaim(
            command_id=row[_Columns.ID],
            command_type=row[_Columns.EVENT_TYPE],
            org_id=row[_Columns.ORG_ID],
            run_id=payload.get(_Fields.RUN_ID)
            if isinstance(payload.get(_Fields.RUN_ID), str)
            else row[_Columns.AGGREGATE_ID],
            approval_id=payload.get(_Fields.APPROVAL_ID)
            if isinstance(payload.get(_Fields.APPROVAL_ID), str)
            else None,
            locked_by=row[_Columns.LOCKED_BY],
            lock_expires_at=row[_Columns.LOCK_EXPIRES_AT],
            attempts=row[_Columns.ATTEMPTS],
            payload=payload,
        )

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

        await self._mark_outbox(result=result, status_value=OutboxStatus.COMPLETED)

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a claimed command for retry after its available time."""

        await self._mark_outbox(result=result, status_value=OutboxStatus.RETRY)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""

        await self._mark_outbox(result=result, status_value=OutboxStatus.DEAD_LETTER)

    async def _enqueue_command(
        self,
        *,
        command_id: str,
        command_type: str,
        org_id: str,
        aggregate_id: str,
        payload: dict[str, object],
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO runtime_outbox_events (
                        id, aggregate_type, aggregate_id, org_id, event_type, payload_json,
                        status, attempts, available_at, locked_by, lock_expires_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, %s, NULL, NULL, %s, %s)
                    """,
                    (
                        command_id,
                        PersistenceValues.AggregateType.AGENT_RUN,
                        aggregate_id,
                        org_id,
                        command_type,
                        Jsonb(payload),
                        now,
                        now,
                        now,
                    ),
                )

    async def _mark_outbox(
        self, *, result: RuntimeWorkerResult, status_value: OutboxStatus
    ) -> None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE runtime_outbox_events
                    SET status = %s, available_at = COALESCE(%s, available_at),
                        locked_by = NULL, lock_expires_at = NULL, updated_at = now()
                    WHERE id = %s
                    """,
                    (status_value.value, result.retry_available_at, result.command_id),
                )

    @classmethod
    def _conversation_record(cls, row: dict[str, object]) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=row[_Columns.ID],
            org_id=row[_Columns.ORG_ID],
            user_id=row[_Columns.USER_ID],
            assistant_id=row[_Columns.ASSISTANT_ID],
            title=row[_Columns.TITLE],
            status=row[_Columns.STATUS],
            created_at=row[_Columns.CREATED_AT],
            updated_at=row[_Columns.UPDATED_AT],
            archived_at=row[_Columns.ARCHIVED_AT],
            metadata=dict(row[_Columns.METADATA_JSON]),
            schema_version=row[_Columns.SCHEMA_VERSION],
            idempotency_key=row[_Columns.IDEMPOTENCY_KEY],
        )

    @classmethod
    def _message_record(cls, row: dict[str, object]) -> MessageRecord:
        return MessageRecord(
            message_id=row[_Columns.ID],
            conversation_id=row[_Columns.CONVERSATION_ID],
            org_id=row[_Columns.ORG_ID],
            run_id=row[_Columns.RUN_ID],
            role=row[_Columns.ROLE],
            content_text=row[_Columns.CONTENT_TEXT],
            content_format=row[_Columns.CONTENT_FORMAT],
            content=tuple(dict(part) for part in row[_Columns.CONTENT_JSON]),
            attachments=tuple(
                dict(attachment) for attachment in row[_Columns.ATTACHMENTS_JSON]
            ),
            quote=dict(row[_Columns.QUOTE_JSON])
            if row[_Columns.QUOTE_JSON] is not None
            else None,
            metadata=dict(row[_Columns.METADATA_JSON]),
            parent_message_id=row[_Columns.PARENT_MESSAGE_ID],
            source_message_id=row[_Columns.SOURCE_MESSAGE_ID],
            branch_id=row[_Columns.BRANCH_ID],
            token_count=row[_Columns.TOKEN_COUNT],
            trace_id=row[_Columns.TRACE_ID],
            status=row[_Columns.STATUS],
            created_at=row[_Columns.CREATED_AT],
            edited_at=row[_Columns.EDITED_AT],
            deleted_at=row[_Columns.DELETED_AT],
        )

    @classmethod
    def _run_record(cls, row: dict[str, object]) -> RunRecord:
        safe_error = None
        if (
            row[_Columns.SAFE_ERROR_CODE] is not None
            and row[_Columns.SAFE_ERROR_MESSAGE] is not None
        ):
            safe_error = RuntimeErrorEnvelope(
                code=row[_Columns.SAFE_ERROR_CODE],
                safe_message=row[_Columns.SAFE_ERROR_MESSAGE],
                retryable=False,
                correlation_id=row[_Columns.TRACE_ID],
            )
        return RunRecord(
            run_id=row[_Columns.ID],
            conversation_id=row[_Columns.CONVERSATION_ID],
            org_id=row[_Columns.ORG_ID],
            user_id=row[_Columns.USER_ID],
            user_message_id=row[_Columns.USER_MESSAGE_ID],
            idempotency_key=row[_Columns.IDEMPOTENCY_KEY],
            trace_id=row[_Columns.TRACE_ID],
            status=row[_Columns.STATUS],
            model_provider=row[_Columns.MODEL_PROVIDER],
            model_name=row[_Columns.MODEL_NAME],
            runtime_context=dict(row[_Columns.RUNTIME_CONTEXT_JSON]),
            request_options=dict(row[_Columns.REQUEST_OPTIONS_JSON]),
            created_at=row[_Columns.CREATED_AT],
            started_at=row[_Columns.STARTED_AT],
            completed_at=row[_Columns.COMPLETED_AT],
            cancelled_at=row[_Columns.CANCELLED_AT],
            safe_error=safe_error,
            latest_sequence_no=row[_Columns.LATEST_SEQUENCE_NO],
        )

    @classmethod
    def _event_envelope(cls, row: dict[str, object]) -> RuntimeEventEnvelope:
        stored_activity = row.get(_Columns.ACTIVITY_KIND)
        if stored_activity is None:
            stored_activity = RuntimeEventPresentationProjector.activity_kind_for(
                event_type=RuntimeApiEventType(row[_Columns.EVENT_TYPE]),
                source=StreamEventSource(row[_Columns.SOURCE]),
            )

        stored_presentation = row.get(_Columns.PRESENTATION_JSON)
        if stored_presentation is not None:
            presentation = dict(stored_presentation)
        else:
            presentation = RuntimeEventPresentationProjector.presentation_metadata(
                dict(row[_Columns.METADATA_JSON_REDACTED])
            )

        return RuntimeEventEnvelope(
            event_id=row[_Columns.ID],
            run_id=row[_Columns.RUN_ID],
            conversation_id=row[_Columns.CONVERSATION_ID],
            sequence_no=row[_Columns.SEQUENCE_NO],
            source=row[_Columns.SOURCE],
            event_type=row[_Columns.EVENT_TYPE],
            trace_id=row[_Columns.TRACE_ID],
            parent_event_id=row[_Columns.PARENT_EVENT_ID],
            span_id=row[_Columns.SPAN_ID],
            parent_span_id=row[_Columns.PARENT_SPAN_ID],
            parent_task_id=row[_Columns.PARENT_TASK_ID],
            task_id=row[_Columns.TASK_ID],
            subagent_id=row[_Columns.SUBAGENT_ID],
            display_title=row[_Columns.DISPLAY_TITLE],
            summary=row[_Columns.SUMMARY],
            status=row[_Columns.STATUS],
            activity_kind=stored_activity,
            visibility=row[_Columns.VISIBILITY],
            redaction_state=row[_Columns.REDACTION_STATE],
            presentation=presentation,
            payload=dict(row[_Columns.PAYLOAD_JSON_REDACTED]),
            metadata=dict(row[_Columns.METADATA_JSON_REDACTED]),
            created_at=row[_Columns.CREATED_AT],
        )

    @classmethod
    async def _insert_message(
        cls, conn: psycopg.AsyncConnection, message: MessageRecord
    ) -> None:
        await conn.execute(
            """
            INSERT INTO agent_messages (
                id, conversation_id, org_id, run_id, role, content_text, content_format,
                content_json, attachments_json, quote_json, metadata_json,
                parent_message_id, source_message_id, branch_id, token_count, trace_id,
                status, created_at, edited_at, deleted_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                message.message_id,
                message.conversation_id,
                message.org_id,
                message.run_id,
                message.role.value,
                message.content_text,
                message.content_format,
                Jsonb(message.content),
                Jsonb(message.attachments),
                Jsonb(message.quote) if message.quote is not None else None,
                Jsonb(message.metadata),
                message.parent_message_id,
                message.source_message_id,
                message.branch_id,
                message.token_count,
                message.trace_id,
                message.status.value,
                message.created_at,
                message.edited_at,
                message.deleted_at,
            ),
        )

    @classmethod
    async def _insert_run(cls, conn: psycopg.AsyncConnection, run: RunRecord) -> None:
        await conn.execute(
            """
            INSERT INTO agent_runs (
                id, conversation_id, org_id, user_id, user_message_id, idempotency_key,
                trace_id, status, model_provider, model_name, model_config_json,
                runtime_context_json, runtime_version, request_options_json,
                latest_sequence_no, row_version, created_at, started_at, completed_at,
                cancelled_at, safe_error_code, safe_error_message
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                NULL, %s, %s, 1, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                run.run_id,
                run.conversation_id,
                run.org_id,
                run.user_id,
                run.user_message_id,
                run.idempotency_key,
                run.trace_id,
                run.status.value,
                run.model_provider,
                run.model_name,
                Jsonb(run.runtime_context.model_profile.model_dump(mode="json")),
                Jsonb(run.runtime_context.model_dump(mode="json")),
                Jsonb(run.request_options),
                run.latest_sequence_no,
                run.created_at,
                run.started_at,
                run.completed_at,
                run.cancelled_at,
                run.safe_error.code.value if run.safe_error else None,
                run.safe_error.safe_message if run.safe_error else None,
            ),
        )
