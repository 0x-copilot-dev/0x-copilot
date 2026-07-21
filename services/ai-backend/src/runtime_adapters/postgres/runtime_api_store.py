"""Async Postgres-backed runtime API, event store, and durable queue adapter."""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import errors as psycopg_errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.execution.contracts import (
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
    StreamEventSource,
)
from agent_runtime.persistence._reader import reader
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.ports import RuntimeEventSequenceConflict
from agent_runtime.persistence.encryption import (
    FieldCodec,
    FieldEncryption,
    FieldEncryptionFactory,
)
from agent_runtime.persistence.pool_metrics import PoolMetrics
from copilot_audit_chain import AuditChainSigner
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
    ApprovalBatchStatus,
    BatchItemDecision,
    BatchOutcomeStatus,
    BatchTransitionOutcome,
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetReservationRecord,
    BudgetScope,
    BudgetStateRecord,
    BudgetStatus,
    BudgetWithState,
    ChargeOutcome,
    CitationRecord,
    CompressionEventRecord,
    ModelPricingRecord,
    OutboxStatus,
    RetentionDeletionEvidenceRecord,
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
    ToolBudgetEnforcement,
    ToolBudgetRecord,
    UsageConversationAggregateRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyPurposeRow,
    UsageDailySubagentRow,
    UsageDailyUserRow,
)
from agent_runtime.persistence.schema.migrate import MigrationRunner
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)
from runtime_adapters.base import (
    RuntimeAdapterHelpers,
    StatusTransition,
    _Fields,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    DefaultModelSelection,
    WorkspaceBehaviorOverrides,
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
    WorkspaceDefaultsRecord,
)


class _Tables:
    """SQL table-name constants used as Additional Authenticated Data in field encryption."""

    AGENT_MESSAGES = "agent_messages"
    RUNTIME_AUDIT_LOG = "runtime_audit_log"
    RUNTIME_EVENTS = "runtime_events"
    RUNTIME_CITATIONS = "runtime_citations"


class _AppendEventRetry:
    """Tuning constants for the lock-free ``append_event`` retry loop.

    The unique index ``idx_runtime_events_run_sequence (run_id, sequence_no)`` is the
    primary guard. Concurrent appenders that land on the same sequence number retry
    up to ``MAX_ATTEMPTS`` times with jittered backoff before surfacing a conflict error.
    """

    SEQUENCE_INDEX = "idx_runtime_events_run_sequence"
    MAX_ATTEMPTS = 3
    BASE_DELAY_SECONDS = 0.005
    MAX_DELAY_SECONDS = 0.050


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
    CONNECTORS_UPDATED_AT = "connectors_updated_at"
    CONVERSATION_ID = "conversation_id"
    COUNT = "count"
    CREATED_AT = "created_at"
    # Two-stage approval forwarding bookkeeping columns on runtime_approval_requests.
    CHAIN_PARENT_APPROVAL_ID = "chain_parent_approval_id"
    CHAIN_DEPTH = "chain_depth"
    FORWARDED_AT = "forwarded_at"
    FORWARDED_DECIDED_AT = "forwarded_decided_at"
    FORWARDED_TO_USER_ID = "forwarded_to_user_id"
    ENABLED_CONNECTORS = "enabled_connectors"
    DELETED_AT = "deleted_at"
    # Workspace defaults + conversation lifecycle columns.
    DEFAULT_MODEL = "default_model"
    DEFAULT_CONNECTORS = "default_connectors"
    # Workspace-policy knobs JSONB column on workspace_defaults.
    BEHAVIOR_OVERRIDES = "behavior_overrides"
    # Per-workspace model curation (JSON array | NULL) on workspace_defaults.
    ENABLED_MODELS = "enabled_models"
    UPDATED_BY_USER_ID = "updated_by_user_id"
    FOLDER = "folder"
    # PRD-H.4 — first-class conversation pin flag (migration 0034).
    PINNED = "pinned"
    PARENT_CONVERSATION_ID = "parent_conversation_id"
    # Conversation fork lineage column.
    FORKED_FROM_SHARE_ID = "forked_from_share_id"
    DISPLAY_TITLE = "display_title"
    EDITED_AT = "edited_at"
    ENCRYPTION_VERSION = "encryption_version"
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
    SNIPPET = "snippet"
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
    """Env-var keys and defaults for runtime DB connection-pool tuning."""

    POOL_MIN_SIZE = "RUNTIME_DB_POOL_MIN_SIZE"
    POOL_MAX_SIZE = "RUNTIME_DB_POOL_MAX_SIZE"
    POOL_ACQUIRE_TIMEOUT_SECONDS = "RUNTIME_DB_POOL_ACQUIRE_TIMEOUT_SECONDS"
    STATEMENT_TIMEOUT_MS = "RUNTIME_DB_STATEMENT_TIMEOUT_MS"
    LOCK_TIMEOUT_MS = "RUNTIME_DB_LOCK_TIMEOUT_MS"
    IDLE_IN_TXN_TIMEOUT_MS = "RUNTIME_DB_IDLE_IN_TXN_TIMEOUT_MS"
    # Read-replica routing.
    READ_REPLICA_URL = "RUNTIME_DB_READ_REPLICA_URL"
    READ_REPLICA_MAX_LAG_SECONDS = "RUNTIME_DB_READ_REPLICA_MAX_LAG_SECONDS"

    DEFAULT_POOL_MIN_SIZE = 5
    DEFAULT_POOL_MAX_SIZE = 50
    DEFAULT_POOL_ACQUIRE_TIMEOUT_SECONDS = 5.0
    DEFAULT_STATEMENT_TIMEOUT_MS = 10000
    DEFAULT_LOCK_TIMEOUT_MS = 3000
    DEFAULT_IDLE_IN_TXN_TIMEOUT_MS = 30000

    SERVICE_NAME = "ai-backend"

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        """Return an int from an env var, falling back to ``default`` on missing or parse error."""
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        """Return a float from an env var, falling back to ``default`` on missing or parse error."""
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
        field_encryption: FieldEncryption | None = None,
        replica_pool: AsyncConnectionPool | None = None,
        replica_database_url: str | None = None,
        notify_after_append: bool = False,
        notify_channel: str = "runtime_events_v1",
    ) -> None:
        if pool is None and database_url is None:
            raise ValueError("Either database_url or pool must be provided.")
        self.database_url = database_url
        self._role = role
        # When True, every successful ``append_event`` / ``append_events_batch``
        # fires ``NOTIFY <channel>, '<run_id>:<seq>'`` so cross-process SSE listeners
        # wake within milliseconds. The NOTIFY runs inside the same transaction as the
        # INSERT — if the INSERT rolls back the NOTIFY is silently discarded by Postgres.
        self._notify_after_append = notify_after_append
        self._notify_channel = notify_channel
        # ``field_encryption`` defaults to ``NullFieldEncryption`` so writes stay
        # v0 (plaintext) until an operator flips ``RUNTIME_FIELD_ENCRYPTION=envelope_v1``.
        # The injection point is available now so write-and-read wiring can happen
        # without changing the constructor signature.
        self._field_encryption: FieldEncryption = (
            field_encryption
            if field_encryption is not None
            else FieldEncryptionFactory.from_env()
        )
        # ``RUNTIME_FIELD_ENCRYPTION_STRICT_READS=true`` turns v0 reads into errors
        # after a successful backfill — any row still at version 0 surfaces
        # immediately rather than silently returning plaintext.
        strict_reads = os.environ.get(
            "RUNTIME_FIELD_ENCRYPTION_STRICT_READS", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._codec = FieldCodec(self._field_encryption, strict_reads=strict_reads)
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
            # Optional read replica — falls back to primary on health failure or if unset.
        # Constructor injection keeps the test harness injectable; env-driven config
        # kicks in only when no explicit replica was passed.
        self._replica_pool: AsyncConnectionPool | None = replica_pool
        self._owns_replica_pool = False
        self._replica_healthy = self._replica_pool is not None
        if self._replica_pool is None:
            replica_url = (
                replica_database_url
                if replica_database_url is not None
                else os.environ.get(_PoolEnv.READ_REPLICA_URL, "").strip() or None
            )
            if replica_url and database_url is not None:
                self._replica_pool = AsyncConnectionPool(
                    conninfo=replica_url,
                    min_size=2,
                    max_size=max(2, max_size // 2) if pool is None else 10,
                    timeout=acquire_timeout if pool is None else 5.0,
                    kwargs=_PoolEnv.build_pool_kwargs(role=f"{role}-replica"),
                    open=False,
                )
                self._owns_replica_pool = True
                self._replica_healthy = True

    async def open(self) -> None:
        """Open the underlying pool. Required when this store owns the pool."""

        if self._owns_pool:
            await self._pool.open()
            await self._pool.wait()
        if self._owns_replica_pool and self._replica_pool is not None:
            try:
                await self._replica_pool.open()
                await self._replica_pool.wait()
            except Exception:
                # Replica unreachable at boot — degrade silently to primary.
                self._replica_healthy = False

    async def close(self) -> None:
        """Close the connection pool when this store owns it."""

        if self._owns_pool:
            await self._pool.close()
        if self._owns_replica_pool and self._replica_pool is not None:
            try:
                await self._replica_pool.close()
            except Exception:
                pass

    async def __aenter__(self) -> PostgresRuntimeApiStore:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @asynccontextmanager
    async def _tenant_connection(
        self, *, org_id: str | None = None, role: str | None = None
    ) -> AsyncIterator[psycopg.AsyncConnection]:  # type: ignore[type-arg]
        """Acquire a pool connection and stamp RLS session variables.

        Sets ``app.current_org_id`` when ``org_id`` is given so row-level security
        policies on tenant-scoped tables match. Sets ``app.role`` so policies that
        distinguish 'worker' from 'api' access (e.g. the outbox cross-tenant policy)
        grant the right scope. Harmless when RLS is not yet enabled.
        """

        async with self._pool.connection() as conn:
            if org_id is not None:
                await conn.execute(
                    "SELECT set_config('app.current_org_id', %s, true)",
                    (org_id,),
                )
            effective_role = role if role is not None else self._role
            if effective_role:
                await conn.execute(
                    "SELECT set_config('app.role', %s, true)",
                    (effective_role,),
                )
            yield conn

    @asynccontextmanager
    async def _role_connection(
        self, role: str
    ) -> AsyncIterator[psycopg.AsyncConnection]:  # type: ignore[type-arg]
        """Acquire a pool connection without binding it to a tenant.

        Used by cross-tenant operator paths (worker outbox claim,
        backfill jobs). Sets ``app.role`` so the
        ``runtime_outbox_events.tenant_or_worker`` policy grants access.
        """

        async with self._pool.connection() as conn:
            await conn.execute(
                "SELECT set_config('app.role', %s, true)",
                (role,),
            )
            yield conn

    @asynccontextmanager
    async def _read_only_connection(
        self, *, org_id: str | None = None
    ) -> AsyncIterator[psycopg.AsyncConnection]:  # type: ignore[type-arg]
        """Acquire a read-only connection, preferring the replica over the primary.

        Used by ``@reader``-annotated methods. Tenant RLS session variables still
        apply on the replica (policies replicate by default). Replica failures degrade
        silently to the primary — latency degrades, but the request still succeeds.
        """

        if self._replica_pool is not None and self._replica_healthy:
            try:
                async with self._replica_pool.connection() as conn:
                    if org_id is not None:
                        await conn.execute(
                            "SELECT set_config('app.current_org_id', %s, true)",
                            (org_id,),
                        )
                    yield conn
                return
            except Exception:
                self._replica_healthy = False
        async with self._tenant_connection(org_id=org_id) as conn:
            yield conn

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

        async with self._tenant_connection(org_id=request.org_id) as conn:
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

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_conversations
                WHERE id = %s AND org_id = %s AND user_id = %s
                """,
                (conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def get_conversation_for_org(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation scoped by org only, bypassing the user filter.

        Admin authorization is enforced by the service layer; this method
        only enforces tenant isolation. Cross-tenant rows are filtered by
        the org_id predicate and by RLS at the connection level.
        """

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_conversations
                WHERE id = %s AND org_id = %s
                """,
                (conversation_id, org_id),
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
        include_deleted: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return scoped conversations ordered by latest update.

        Soft-deleted rows (``deleted_at IS NOT NULL``) are excluded by default —
        the sidebar query path. A partial index covering active conversations
        serves this hot path; the include-deleted path falls back to the full index.

        """

        archived_filter = "" if include_archived else "AND status <> 'archived'"
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                f"""
                SELECT * FROM agent_conversations
                WHERE org_id = %s AND user_id = %s {archived_filter} {deleted_filter}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (org_id, user_id, limit),
            )
            rows = await cur.fetchall()
        return tuple(self._conversation_record(row) for row in rows)

    async def list_conversation_scopes(self) -> tuple[tuple[str, str], ...]:
        """Return every distinct ``(org_id, user_id)`` that owns a conversation.

        Read-only, cross-tenant scope discovery consumed by the offline
        file-store migration (``runtime_adapters.file.migration.StoreMigrator``):
        it lets a Postgres source migrate *every* tenant without hand-passed
        ``--org-id``/``--user-id`` scopes. Archived and soft-deleted
        conversations are included (no status / ``deleted_at`` filter) so no
        tenant's history is missed.

        Returns an empty tuple when the ``agent_conversations`` relation does not
        exist yet: a brand-new desktop install that has never run the Postgres AI
        store has no schema, which correctly means "no scopes to migrate" (the
        first-file-boot import treats this as a clean no-op rather than a crash).

        Tenant-isolation note: this is a deliberate cross-tenant scan for an
        operator / first-boot path. It must run under a connection that can see
        every row — a superuser/owner (the desktop embedded Postgres) or a
        ``BYPASSRLS`` role. On an RLS-*enforced* multi-tenant cluster the
        ``tenant_isolation`` policy on ``agent_conversations`` (which has no
        worker fallback) would otherwise hide rows from a session that has not
        bound ``app.current_org_id``.
        """

        async with self._role_connection(self._role) as conn:
            try:
                cur = await conn.execute(
                    """
                    SELECT DISTINCT org_id, user_id
                      FROM agent_conversations
                     ORDER BY org_id, user_id
                    """
                )
                rows = await cur.fetchall()
            except psycopg_errors.UndefinedTable:
                return ()
        return tuple(
            (str(row[_Columns.ORG_ID]), str(row[_Columns.USER_ID])) for row in rows
        )

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        scopes_patch: dict[str, tuple[str, ...] | None],
        now: datetime,
    ) -> ConversationRecord | None:
        """Merge ``scopes_patch`` into ``enabled_connectors`` atomically.

        Uses jsonb concat (``||``) so keys present in the patch overwrite
        the stored value (including JSON null = paused) while keys absent
        in the patch survive untouched. Returns the post-update row, or
        ``None`` when no row matches the (org, user, conversation) scope.
        """

        # Pre-encode the patch as JSONB so the merge happens entirely in
        # Postgres: stored || %s::jsonb. JSON null in the patch survives
        # the merge as a "paused" marker.
        patch_jsonb: dict[str, list[str] | None] = {
            connector_id: (list(scopes) if scopes is not None else None)
            for connector_id, scopes in scopes_patch.items()
        }
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                UPDATE agent_conversations
                   SET enabled_connectors    = enabled_connectors || %s::jsonb,
                       connectors_updated_at = %s,
                       updated_at            = %s
                 WHERE id      = %s
                   AND org_id  = %s
                   AND user_id = %s
                 RETURNING *
                """,
                (Jsonb(patch_jsonb), now, now, conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def get_workspace_defaults(
        self, *, org_id: str
    ) -> WorkspaceDefaultsRecord | None:
        """Return the workspace defaults row for an org, or ``None`` if not yet set."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT org_id, default_model, default_connectors,
                       behavior_overrides, enabled_models,
                       updated_at, updated_by_user_id
                  FROM workspace_defaults
                 WHERE org_id = %s
                """,
                (org_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return self._workspace_defaults_record(row)

    async def upsert_workspace_defaults(
        self, *, record: WorkspaceDefaultsRecord
    ) -> WorkspaceDefaultsRecord:
        """Persist workspace-level defaults for an org, inserting or replacing the existing row."""
        # Retention is composed by the service from ``retention_policies``
        # — never touched here. Persist only the columns owned by the
        # workspace_defaults table.
        default_model_json = (
            record.default_model.model_dump(mode="json", exclude_none=True)
            if record.default_model is not None
            else {}
        )
        default_connectors_json: dict[str, list[str] | None] = {
            connector_id: (list(scopes) if scopes is not None else None)
            for connector_id, scopes in record.default_connectors.items()
        }
        # Serialise the behavior_overrides Pydantic model into the JSONB blob.
        # ``exclude_none=True`` keeps absent fields out so the stored shape is
        # clean; consumers always deserialise via the typed model on the way back.
        behavior_overrides_json = record.behavior_overrides.model_dump(
            mode="json",
            exclude_none=True,
        )
        # None (no curation) is stored as SQL NULL — distinct from an empty
        # array (workspace disabled everything). Jsonb(None) yields NULL.
        enabled_models_json = (
            Jsonb(list(record.enabled_models))
            if record.enabled_models is not None
            else None
        )
        now = record.updated_at or datetime.now(timezone.utc)
        async with self._tenant_connection(org_id=record.org_id) as conn:
            cur = await conn.execute(
                """
                INSERT INTO workspace_defaults
                       (org_id, default_model, default_connectors,
                        behavior_overrides, enabled_models,
                        updated_at, updated_by_user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (org_id) DO UPDATE
                   SET default_model       = EXCLUDED.default_model,
                       default_connectors  = EXCLUDED.default_connectors,
                       behavior_overrides  = EXCLUDED.behavior_overrides,
                       enabled_models      = EXCLUDED.enabled_models,
                       updated_at          = EXCLUDED.updated_at,
                       updated_by_user_id  = EXCLUDED.updated_by_user_id
                 RETURNING org_id, default_model, default_connectors,
                           behavior_overrides, enabled_models,
                           updated_at, updated_by_user_id
                """,
                (
                    record.org_id,
                    Jsonb(default_model_json),
                    Jsonb(default_connectors_json),
                    Jsonb(behavior_overrides_json),
                    enabled_models_json,
                    now,
                    record.updated_by_user_id,
                ),
            )
            row = await cur.fetchone()
        return self._workspace_defaults_record(row)

    @classmethod
    def _workspace_defaults_record(
        cls, row: dict[str, object]
    ) -> WorkspaceDefaultsRecord:
        """Hydrate one workspace_defaults row.

        ``retention_days`` is intentionally None — composed by the
        service from ``retention_policies`` (we never persist it on
        this row).

        ``behavior_overrides`` is hydrated from JSONB; an absent, empty, or
        forward-incompatible blob falls back to the Pydantic default (all-None / opt-out=False).
        """

        default_model_json = row.get(_Columns.DEFAULT_MODEL) or {}
        default_model: DefaultModelSelection | None = None
        if isinstance(default_model_json, dict) and default_model_json:
            try:
                default_model = DefaultModelSelection.model_validate(
                    dict(default_model_json)
                )
            except Exception:
                # Forward-compat: stored shape may have evolved beyond
                # what this binary understands. Treat as no default
                # rather than crashing the read path.
                default_model = None
        behavior_overrides_json = row.get(_Columns.BEHAVIOR_OVERRIDES) or {}
        behavior_overrides = WorkspaceBehaviorOverrides()
        if isinstance(behavior_overrides_json, dict) and behavior_overrides_json:
            try:
                behavior_overrides = WorkspaceBehaviorOverrides.model_validate(
                    dict(behavior_overrides_json)
                )
            except Exception:
                # Forward-compat: same rationale as default_model. A
                # row written by a future binary with new keys (or a
                # broken admin write) becomes "no overrides" here
                # rather than crashing the read path. Old runs keep
                # streaming.
                behavior_overrides = WorkspaceBehaviorOverrides()
        # enabled_models: SQL NULL → None (heuristic default); a JSON array →
        # tuple. A forward-incompatible blob (non-list) degrades to None
        # rather than crashing the read path — same posture as the columns above.
        enabled_models_raw = row.get(_Columns.ENABLED_MODELS)
        enabled_models: tuple[str, ...] | None = None
        if isinstance(enabled_models_raw, list):
            enabled_models = tuple(
                str(entry) for entry in enabled_models_raw if isinstance(entry, str)
            )
        return WorkspaceDefaultsRecord(
            org_id=str(row[_Columns.ORG_ID]),
            default_model=default_model,
            default_connectors=cls._coerce_enabled_connectors(
                row.get(_Columns.DEFAULT_CONNECTORS)
            ),
            retention_days=None,
            behavior_overrides=behavior_overrides,
            enabled_models=enabled_models,
            updated_at=row.get(_Columns.UPDATED_AT),
            updated_by_user_id=row.get(_Columns.UPDATED_BY_USER_ID),
        )

    async def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        title: str | None,
        title_changed: bool,
        folder: str | None,
        folder_changed: bool,
        archived: bool | None,
        archived_changed: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Apply a lifecycle PATCH (title / folder / archived) atomically.

        Builds a SET clause from the ``*_changed`` flags so the column
        list mirrors the caller's intent exactly. ``updated_at`` is
        always bumped (idempotent no-op refreshes the row's
        last-touched timestamp).
        """

        sets: list[str] = ["updated_at = %s"]
        params: list[object] = [now]
        if title_changed:
            sets.append("title = %s")
            params.append(title)
        if folder_changed:
            sets.append("folder = %s")
            params.append(folder)
        if archived_changed:
            if archived:
                sets.append("status = 'archived'")
                sets.append("archived_at = %s")
                params.append(now)
            else:
                sets.append("status = 'active'")
                sets.append("archived_at = NULL")
        params.extend([conversation_id, org_id, user_id])
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                f"""
                UPDATE agent_conversations
                   SET {", ".join(sets)}
                 WHERE id      = %s
                   AND org_id  = %s
                   AND user_id = %s
                 RETURNING *
                """,
                tuple(params),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def soft_delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Stamp ``deleted_at`` (idempotent on already-deleted rows)."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                UPDATE agent_conversations
                   SET deleted_at = COALESCE(deleted_at, %s),
                       updated_at = %s
                 WHERE id      = %s
                   AND org_id  = %s
                   AND user_id = %s
                 RETURNING *
                """,
                (now, now, conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Clear ``deleted_at``. Returns ``None`` when the row was reaped."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                UPDATE agent_conversations
                   SET deleted_at = NULL,
                       updated_at = %s
                 WHERE id      = %s
                   AND org_id  = %s
                   AND user_id = %s
                 RETURNING *
                """,
                (now, conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

    async def set_conversation_pinned(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        pinned: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Set the first-class ``pinned`` flag (PRD-H.4), idempotent on re-call.

        ``updated_at`` is bumped only when the flag actually changes (via
        ``IS DISTINCT FROM``) so a redundant pin does not reshuffle the
        newest-first sidebar order. Returns ``None`` when no row matches
        the (org, user, conversation) scope.
        """

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                UPDATE agent_conversations
                   SET updated_at = CASE
                           WHEN pinned IS DISTINCT FROM %s THEN %s
                           ELSE updated_at
                       END,
                       pinned = %s
                 WHERE id      = %s
                   AND org_id  = %s
                   AND user_id = %s
                 RETURNING *
                """,
                (pinned, now, pinned, conversation_id, org_id, user_id),
            )
            row = await cur.fetchone()
        return self._conversation_record(row) if row is not None else None

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
        async with self._tenant_connection(org_id=org_id) as conn:
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

        async with self._tenant_connection(org_id=message.org_id) as conn:
            async with conn.transaction():
                await self._insert_message(conn, message)
                await conn.execute(
                    "UPDATE agent_conversations SET updated_at = %s WHERE id = %s",
                    (message.created_at, message.conversation_id),
                )
        return message

    async def insert_forked_conversation(
        self, conversation: ConversationRecord
    ) -> ConversationRecord:
        """Insert a fork-authored conversation row verbatim.

        Distinct from ``create_conversation``: bypasses idempotency (forks always mint
        a new row) and writes every column the caller has populated, including lineage
        pointers (``parent_conversation_id``, ``forked_from_share_id``) and the per-chat
        connector scope override that the standard path drops.
        """

        # Encode the per-chat connector scope override the same way the
        # PATCH path does (``update_conversation_connectors``): JSON null
        # means "paused", JSON array means "active with these scopes".
        enabled_jsonb: dict[str, list[str] | None] = {
            connector_id: (list(scopes) if scopes is not None else None)
            for connector_id, scopes in conversation.enabled_connectors.items()
        }
        async with self._tenant_connection(org_id=conversation.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO agent_conversations (
                    id, org_id, user_id, assistant_id, title, status, created_at,
                    updated_at, archived_at, metadata_json, schema_version,
                    idempotency_key, enabled_connectors, connectors_updated_at,
                    deleted_at, folder, parent_conversation_id,
                    forked_from_share_id
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    conversation.conversation_id,
                    conversation.org_id,
                    conversation.user_id,
                    conversation.assistant_id,
                    conversation.title,
                    conversation.status.value,
                    conversation.created_at,
                    conversation.updated_at,
                    conversation.archived_at,
                    Jsonb(conversation.metadata),
                    conversation.schema_version,
                    conversation.idempotency_key,
                    Jsonb(enabled_jsonb),
                    conversation.connectors_updated_at,
                    conversation.deleted_at,
                    conversation.folder,
                    conversation.parent_conversation_id,
                    conversation.forked_from_share_id,
                ),
            )
        return conversation

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

        async with self._tenant_connection(org_id=conversation.org_id) as conn:
            # Single transaction for the whole multi-statement op: if we
            # release the connection mid-way we lose atomicity.
            async with conn.transaction():
                if request.idempotency_key is not None:
                    cur = await conn.execute(
                        """
                        SELECT r.*,
                               m.content_text AS user_content_text,
                               m.encryption_version AS user_content_version
                        FROM agent_runs r
                        JOIN agent_messages m ON m.id = r.user_message_id
                        WHERE r.org_id = %s AND r.user_id = %s AND r.idempotency_key = %s
                        """,
                        (context.org_id, context.user_id, request.idempotency_key),
                    )
                    existing = await cur.fetchone()
                    if existing is not None:
                        # The joined ``user_content_text`` may be an encrypted envelope;
                        # decrypt before comparing against the caller's plaintext input.
                        existing_user_text = self._codec.decrypt_text(
                            existing[_Columns.USER_CONTENT_TEXT],
                            encryption_version=int(
                                existing.get("user_content_version", 0) or 0
                            ),
                            table=_Tables.AGENT_MESSAGES,
                            column=_Columns.CONTENT_TEXT,
                            org_id=context.org_id,
                        )
                        if (
                            existing[_Columns.CONVERSATION_ID],
                            existing_user_text,
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

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM agent_runs WHERE id = %s AND org_id = %s",
                (run_id, org_id),
            )
            row = await cur.fetchone()
        return self._run_record(row) if row is not None else None

    async def get_active_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the most recent non-terminal run on a conversation."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_runs
                 WHERE org_id          = %s
                   AND conversation_id = %s
                   AND status IN (
                       'queued', 'running', 'waiting_for_approval', 'cancelling'
                   )
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (org_id, conversation_id),
            )
            row = await cur.fetchone()
        return self._run_record(row) if row is not None else None

    async def get_latest_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the newest run for a conversation regardless of status (PRD-H.4).

        Unlike :meth:`get_active_run_for_conversation`, no status filter —
        the Chats-list ``model`` projection wants the last model used even
        after the run completed.
        """

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_runs
                 WHERE org_id          = %s
                   AND conversation_id = %s
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (org_id, conversation_id),
            )
            row = await cur.fetchone()
        return self._run_record(row) if row is not None else None

    async def get_latest_message_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> MessageRecord | None:
        """Return the newest non-deleted message for the Chats-list preview (PRD-H.4)."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM agent_messages
                 WHERE org_id          = %s
                   AND conversation_id = %s
                   AND deleted_at IS NULL
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (org_id, conversation_id),
            )
            row = await cur.fetchone()
        return self._message_record(row) if row is not None else None

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        """Update mutable run status with optimistic-lock compare-and-swap.

        Reads ``row_version`` alongside the run row, then issues an UPDATE
        whose WHERE clause asserts the same version and bumps it. If a
        concurrent writer beat us to the row our UPDATE returns no rows; we
        raise :class:`ConcurrentRunUpdateError` so the worker's
        ``with_optimistic_retry`` helper can refetch and retry.
        """

        from agent_runtime.persistence.errors import ConcurrentRunUpdateError

        async with self._tenant_connection(role=self._role) as conn:
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
        """Persist latest event sequence for a run, monotonically.

        The UPDATE only writes when the new value is strictly greater than the
        stored one. If two appends arrive out of order under async concurrency,
        the smaller-numbered one is a no-op and the cursor never goes
        backwards.
        """

        async with self._tenant_connection(role=self._role) as conn:
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
        # Round-trip ``decided_at`` into the metadata blob
        # so the undo endpoint can compute the 60s window without a
        # separate decision lookup. Mirrors the in-memory adapter; the
        # blob is the existing zero-migration seam.
        decided_at_iso = record.decided_at.isoformat()
        async with self._tenant_connection(org_id=record.org_id) as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE runtime_approval_requests
                    SET status = %s,
                        decided_by_user_id = %s,
                        decision_reason = %s,
                        decided_at = %s,
                        request_payload_json_redacted = COALESCE(
                            request_payload_json_redacted, '{}'::jsonb
                        ) || jsonb_build_object('decided_at', %s::text)
                    WHERE id = %s AND org_id = %s
                    """,
                    (
                        record.status.value,
                        record.decided_by_user_id,
                        decision_reason,
                        record.decided_at,
                        decided_at_iso,
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
        """Persist a pending approval request, idempotent on ``approval_id``.

        Atomic upsert: ``INSERT … ON CONFLICT (id) DO NOTHING``. If the insert
        was a no-op (someone else got there first), fetch the existing row and
        return it. No check-then-insert race window.
        """

        risk_class = RuntimeAdapterHelpers.normalize_risk_class(record.metadata)
        action_summary = RuntimeAdapterHelpers.derive_action_summary(record.metadata)
        async with self._tenant_connection(org_id=record.org_id) as conn:
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
        return self._approval_request_record_from_row(existing)

    async def forward_approval_request(
        self,
        *,
        parent_approval_id: str,
        org_id: str,
        decided_by_user_id: str,
        forwarded_to_user_id: str,
        decision_reason: str | None,
        child: ApprovalRequestRecord,
        now: datetime,
    ) -> tuple[ApprovalRequestRecord, ApprovalRequestRecord]:
        """Atomically transition the parent to FORWARDED and insert the child approval.

        A single transaction covers both writes so a partial chain never persists.
        Idempotent on ``child.approval_id`` via ``ON CONFLICT (id) DO NOTHING`` —
        the same guard used by ``create_approval_request``.
        """

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        risk_class = RuntimeAdapterHelpers.normalize_risk_class(child.metadata)
        action_summary = RuntimeAdapterHelpers.derive_action_summary(child.metadata)
        async with self._tenant_connection(org_id=org_id) as conn:
            async with conn.transaction():
                # 1. Resolve parent → FORWARDED.
                cur = await conn.execute(
                    """
                    UPDATE runtime_approval_requests
                    SET status = %s,
                        decided_by_user_id = %s,
                        decision_reason = %s,
                        decided_at = %s,
                        forwarded_to_user_id = %s,
                        forwarded_at = %s
                    WHERE id = %s AND org_id = %s AND status = %s
                    """,
                    (
                        ApprovalStatus.FORWARDED.value,
                        decided_by_user_id,
                        decision_reason,
                        now,
                        forwarded_to_user_id,
                        now,
                        parent_approval_id,
                        org_id,
                        ApprovalStatus.PENDING.value,
                    ),
                )
                if cur.rowcount != 1:
                    # Lost race or status moved away from PENDING; surface
                    # to the service so it can return the right HTTP code.
                    raise RuntimeError("approval_forward_parent_no_longer_pending")
                # 2. Insert the child row addressed to the forward target.
                await conn.execute(
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
                        created_at,
                        chain_parent_approval_id,
                        chain_depth
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        child.approval_id,
                        child.run_id,
                        org_id,
                        forwarded_to_user_id,
                        ApprovalStatus.PENDING.value,
                        risk_class,
                        action_summary,
                        Jsonb(child.metadata),
                        child.expires_at,
                        child.created_at,
                        parent_approval_id,
                        # The service caps the value via
                        # APPROVAL_FORWARD_MAX_CHAIN_DEPTH; the table CHECK
                        # is the belt-and-braces guard.
                        child.chain_depth or 1,
                    ),
                )
                # 3. Re-read both rows so the service has authoritative state
                # (status, timestamps, joined conversation_id / user_id) for
                # the events + audit emit it does after the txn commits.
                cur = await conn.execute(
                    """
                    SELECT a.*, r.conversation_id, r.user_id
                    FROM runtime_approval_requests a
                    JOIN agent_runs r ON r.id = a.run_id
                    WHERE a.id IN (%s, %s) AND a.org_id = %s
                    """,
                    (parent_approval_id, child.approval_id, org_id),
                )
                rows = await cur.fetchall()
        rows_by_id = {row[_Columns.ID]: row for row in rows}
        parent_row = rows_by_id.get(parent_approval_id)
        child_row = rows_by_id.get(child.approval_id)
        if parent_row is None or child_row is None:
            raise RuntimeError("approval_forward_post_txn_read_missing_row")
        return (
            self._approval_request_record_from_row(parent_row),
            self._approval_request_record_from_row(child_row),
        )

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

        async with self._tenant_connection(org_id=org_id) as conn:
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
        return self._approval_request_record_from_row(row)

    @staticmethod
    def _approval_request_record_from_row(row) -> ApprovalRequestRecord:  # type: ignore[no-untyped-def]
        """Project a runtime_approval_requests row into the record shape.

        Centralised so the create_approval_request / forward_approval_request
        / get_approval_request paths all populate the new chain fields
        consistently. ``row.get`` is used for the columns added in
        schema columns added in a later migration so older rows (or test
        fixtures) that predate those columns still load correctly.
        """

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
            chain_parent_approval_id=row.get(_Columns.CHAIN_PARENT_APPROVAL_ID)
            if hasattr(row, "get")
            else (
                row[_Columns.CHAIN_PARENT_APPROVAL_ID]
                if _Columns.CHAIN_PARENT_APPROVAL_ID in row
                else None
            ),
            forwarded_to_user_id=row.get(_Columns.FORWARDED_TO_USER_ID)
            if hasattr(row, "get")
            else (
                row[_Columns.FORWARDED_TO_USER_ID]
                if _Columns.FORWARDED_TO_USER_ID in row
                else None
            ),
            forwarded_at=row.get(_Columns.FORWARDED_AT)
            if hasattr(row, "get")
            else (row[_Columns.FORWARDED_AT] if _Columns.FORWARDED_AT in row else None),
            forwarded_decided_at=row.get(_Columns.FORWARDED_DECIDED_AT)
            if hasattr(row, "get")
            else (
                row[_Columns.FORWARDED_DECIDED_AT]
                if _Columns.FORWARDED_DECIDED_AT in row
                else None
            ),
            chain_depth=(
                row.get(_Columns.CHAIN_DEPTH, 0)
                if hasattr(row, "get")
                else (row[_Columns.CHAIN_DEPTH] if _Columns.CHAIN_DEPTH in row else 0)
            )
            or 0,
        )

    # ------------------------------------------------------------------
    # ApprovalBatch — first-class entity (PR #43).
    #
    # The batch row is the lock target for the atomic "record decision +
    # maybe flip to RESUMING" primitive. ``SELECT ... FOR UPDATE`` on the
    # batch row inside a single transaction with the item update and the
    # conditional status flip guarantees exactly-once ``PENDING -> RESUMING``
    # under concurrent decision callers.
    # ------------------------------------------------------------------

    async def insert_approval_batch(
        self,
        *,
        spec: ApprovalBatchSpec,
    ) -> ApprovalBatchRecord:
        """Insert one batch row plus its ordered items in a single transaction.

        Idempotent on ``batch_id``: a retry returns the previously-persisted
        batch unchanged.
        """

        async with self._tenant_connection(org_id=spec.batch.org_id) as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """
                    INSERT INTO runtime_approval_batches (
                        id, run_id, org_id, status, created_at, expires_at, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        spec.batch.batch_id,
                        spec.batch.run_id,
                        spec.batch.org_id,
                        spec.batch.status.value,
                        spec.batch.created_at,
                        spec.batch.expires_at,
                        Jsonb(dict(spec.batch.metadata)),
                    ),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    # Lost the race; return the existing row so the caller
                    # sees a consistent shape.
                    return (
                        await self._fetch_approval_batch(
                            conn=conn,
                            org_id=spec.batch.org_id,
                            batch_id=spec.batch.batch_id,
                        )
                        or spec.batch
                    )
                for item in spec.items:
                    await conn.execute(
                        """
                        INSERT INTO runtime_approval_batch_items (
                            id, batch_id, item_index, decision
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            item.item_id,
                            item.batch_id,
                            item.index,
                            item.decision.value if item.decision is not None else None,
                        ),
                    )
        return spec.batch

    async def get_approval_batch(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> ApprovalBatchRecord | None:
        """Return one batch scoped by org, or ``None``."""

        async with self._tenant_connection(org_id=org_id) as conn:
            return await self._fetch_approval_batch(
                conn=conn, org_id=org_id, batch_id=batch_id
            )

    @staticmethod
    async def _fetch_approval_batch(
        *,
        conn,
        org_id: str,
        batch_id: str,  # type: ignore[no-untyped-def]
    ) -> ApprovalBatchRecord | None:
        """Read one batch row inside an open connection."""
        cur = await conn.execute(
            """
            SELECT id, run_id, org_id, status, created_at, expires_at, metadata
            FROM runtime_approval_batches
            WHERE id = %s AND org_id = %s
            """,
            (batch_id, org_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return PostgresRuntimeApiStore._approval_batch_record_from_row(row)

    @staticmethod
    def _approval_batch_record_from_row(row) -> ApprovalBatchRecord:  # type: ignore[no-untyped-def]
        """Project a runtime_approval_batches row into the typed record."""
        return ApprovalBatchRecord(
            batch_id=row["id"],
            run_id=row["run_id"],
            org_id=row["org_id"],
            status=ApprovalBatchStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            metadata=row["metadata"] or {},
        )

    @staticmethod
    def _approval_batch_item_record_from_row(row) -> ApprovalBatchItemRecord:  # type: ignore[no-untyped-def]
        """Project a runtime_approval_batch_items row into the typed record."""
        decision_value = row["decision"]
        decision = (
            BatchItemDecision(decision_value) if decision_value is not None else None
        )
        return ApprovalBatchItemRecord(
            item_id=row["id"],
            batch_id=row["batch_id"],
            index=row["item_index"],
            decision=decision,
        )

    async def get_approval_batch_item(
        self,
        *,
        org_id: str,
        item_id: str,
    ) -> ApprovalBatchItemRecord | None:
        """Return one item joined to its batch's org scope, or ``None``."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT i.id, i.batch_id, i.item_index, i.decision
                FROM runtime_approval_batch_items i
                JOIN runtime_approval_batches b ON b.id = i.batch_id
                WHERE i.id = %s AND b.org_id = %s
                """,
                (item_id, org_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return self._approval_batch_item_record_from_row(row)

    async def list_items_for_batch(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> tuple[ApprovalBatchItemRecord, ...]:
        """Return every item belonging to ``batch_id`` in ``item_index`` order."""

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT i.id, i.batch_id, i.item_index, i.decision
                FROM runtime_approval_batch_items i
                JOIN runtime_approval_batches b ON b.id = i.batch_id
                WHERE i.batch_id = %s AND b.org_id = %s
                ORDER BY i.item_index ASC
                """,
                (batch_id, org_id),
            )
            rows = await cur.fetchall()
        return tuple(self._approval_batch_item_record_from_row(row) for row in rows)

    async def record_item_decision_and_maybe_lock_batch(
        self,
        *,
        org_id: str,
        item_id: str,
        decision: ApprovalDecision,
    ) -> BatchTransitionOutcome:
        """Atomic: record the item decision; flip the batch if newly complete.

        Single transaction; ``SELECT ... FOR UPDATE`` on the batch row inside
        the transaction ensures exactly-one concurrent caller can flip
        ``PENDING -> RESUMING``.
        """

        async with self._tenant_connection(org_id=org_id) as conn:
            async with conn.transaction():
                # Step 1 — locate the item and its parent batch_id.
                cur = await conn.execute(
                    """
                    SELECT i.id, i.batch_id, i.item_index
                    FROM runtime_approval_batch_items i
                    JOIN runtime_approval_batches b ON b.id = i.batch_id
                    WHERE i.id = %s AND b.org_id = %s
                    """,
                    (item_id, org_id),
                )
                item_row = await cur.fetchone()
                if item_row is None:
                    return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
                batch_id = item_row["batch_id"]
                # Step 2 — SELECT ... FOR UPDATE on the batch row. Serialises
                # concurrent record_item_decision callers for the same batch.
                cur = await conn.execute(
                    """
                    SELECT id, run_id, org_id, status, created_at, expires_at, metadata
                    FROM runtime_approval_batches
                    WHERE id = %s AND org_id = %s
                    FOR UPDATE
                    """,
                    (batch_id, org_id),
                )
                batch_row = await cur.fetchone()
                if batch_row is None:
                    return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
                batch = self._approval_batch_record_from_row(batch_row)
                if batch.status is not ApprovalBatchStatus.PENDING:
                    return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
                # Step 3 — write the item decision (idempotent on item_id).
                await conn.execute(
                    """
                    UPDATE runtime_approval_batch_items
                    SET decision = %s
                    WHERE id = %s
                    """,
                    (decision.value, item_id),
                )
                # Step 4 — re-read every sibling in index order to test
                # completeness.
                cur = await conn.execute(
                    """
                    SELECT id, batch_id, item_index, decision
                    FROM runtime_approval_batch_items
                    WHERE batch_id = %s
                    ORDER BY item_index ASC
                    """,
                    (batch_id,),
                )
                rows = await cur.fetchall()
                items = tuple(
                    self._approval_batch_item_record_from_row(row) for row in rows
                )
                if any(item.decision is None for item in items):
                    return BatchTransitionOutcome(
                        status=BatchOutcomeStatus.BATCH_INCOMPLETE
                    )
                # Step 5 — flip PENDING -> RESUMING in the same transaction.
                await conn.execute(
                    """
                    UPDATE runtime_approval_batches
                    SET status = %s
                    WHERE id = %s AND org_id = %s AND status = %s
                    """,
                    (
                        ApprovalBatchStatus.RESUMING.value,
                        batch_id,
                        org_id,
                        ApprovalBatchStatus.PENDING.value,
                    ),
                )
                resuming = batch.model_copy(
                    update={"status": ApprovalBatchStatus.RESUMING}
                )
                return BatchTransitionOutcome(
                    status=BatchOutcomeStatus.READY_TO_RESUME,
                    batch=resuming,
                    items=items,
                )

    async def mark_approval_batch_resolved(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> None:
        """Stamp ``RESUMING -> RESOLVED``; idempotent for terminal statuses."""

        async with self._tenant_connection(org_id=org_id) as conn:
            await conn.execute(
                """
                UPDATE runtime_approval_batches
                SET status = %s
                WHERE id = %s AND org_id = %s
                  AND status NOT IN (%s, %s)
                """,
                (
                    ApprovalBatchStatus.RESOLVED.value,
                    batch_id,
                    org_id,
                    ApprovalBatchStatus.RESOLVED.value,
                    ApprovalBatchStatus.EXPIRED.value,
                ),
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
        signer = AuditChainSigner.from_env(environment_env_var="RUNTIME_ENVIRONMENT")
        # `record` is a dict (the audit-write contract across all adapters), so the
        # tenant connection must use the normalized local `org_id` computed above —
        # `record.org_id` is an attribute access on a dict and raises AttributeError,
        # which 500s every conversation/run create on the Postgres store.
        async with self._tenant_connection(org_id=org_id) as conn:
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
                # Encrypt the redacted metadata blob; the
                # rest of the row is HMAC-chain-signed clear (the chain
                # is the load-bearing tamper guard, not the metadata).
                metadata_encrypted = self._codec.encrypt_jsonb(
                    metadata,
                    table=_Tables.RUNTIME_AUDIT_LOG,
                    column=_Columns.METADATA_JSON_REDACTED,
                    org_id=org_id,
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_audit_log (
                        id, org_id, user_id, actor_type, action, resource_type, resource_id,
                        run_id, trace_id, outcome, metadata_json_redacted, created_at,
                        seq, prev_hash, signature, key_version, encryption_version
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        Jsonb(metadata_encrypted),
                        now,
                        seq + 1,
                        sig.prev_hash,
                        sig.signature,
                        sig.key_version,
                        self._codec.write_version,
                    ),
                )

    async def list_audit_log_for_export(
        self,
        *,
        after_id: str | None,
        limit: int,
    ) -> tuple[dict, ...]:
        """Return up to ``limit`` audit log rows for SIEM export, paginated by ``after_id``."""
        # Cross-tenant scan via the worker role (same trust contract as
        # ``query_run_usage_for_range(org_id=None)``); the SIEM pump is the
        # only legitimate caller and runs under the operator's service token.
        bounded = max(1, min(limit, 1000))
        async with self._role_connection("worker") as conn:
            async with conn.cursor() as cur:
                if after_id is None:
                    await cur.execute(
                        """
                        SELECT id, org_id, user_id, actor_type, event_type,
                               resource_type, resource_id, outcome,
                               metadata_json_redacted, encryption_version, created_at
                          FROM runtime_audit_log
                         ORDER BY created_at ASC, id ASC
                         LIMIT %s
                        """,
                        (bounded,),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT id, org_id, user_id, actor_type, event_type,
                               resource_type, resource_id, outcome,
                               metadata_json_redacted, encryption_version, created_at
                          FROM runtime_audit_log
                         WHERE id > %s
                         ORDER BY created_at ASC, id ASC
                         LIMIT %s
                        """,
                        (after_id, bounded),
                    )
                rows = await cur.fetchall()
        # SIEM consumers receive plaintext metadata — decrypt
        # per-row using the row's own encryption_version so v0 (legacy)
        # rows pass through and v1 rows get unwrapped.
        decoded: list[dict] = []
        for row in rows:
            row_dict = dict(row)
            row_dict[_Columns.METADATA_JSON_REDACTED] = self._codec.decrypt_jsonb(
                row_dict.get(_Columns.METADATA_JSON_REDACTED),
                encryption_version=int(
                    row_dict.get(_Columns.ENCRYPTION_VERSION, 0) or 0
                ),
                table=_Tables.RUNTIME_AUDIT_LOG,
                column=_Columns.METADATA_JSON_REDACTED,
                org_id=str(row_dict[_Columns.ORG_ID]),
            )
            decoded.append(row_dict)
        return tuple(decoded)

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
        async with self._tenant_connection(org_id=org_id) as conn:
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
                signer = AuditChainSigner.from_env(
                    environment_env_var="RUNTIME_ENVIRONMENT"
                )
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
                # Encrypt the redacted metadata JSON.
                deletion_metadata_encrypted = self._codec.encrypt_jsonb(
                    deletion_metadata,
                    table=_Tables.RUNTIME_AUDIT_LOG,
                    column=_Columns.METADATA_JSON_REDACTED,
                    org_id=org_id,
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_audit_log (
                        id, org_id, user_id, actor_type, action, resource_type, resource_id,
                        run_id, trace_id, outcome, metadata_json_redacted, created_at,
                        seq, prev_hash, signature, key_version, encryption_version
                    )
                    VALUES (%s, %s, %s, 'user', 'user_history_deleted', 'user_history', %s,
                            NULL, NULL, 'success', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        audit_event_id,
                        org_id,
                        user_id,
                        user_id,
                        Jsonb(deletion_metadata_encrypted),
                        now,
                        last_seq + 1,
                        deletion_sig.prev_hash,
                        deletion_sig.signature,
                        deletion_sig.key_version,
                        self._codec.write_version,
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

    # ------------------------------------------------------------------
    # Usage + pricing
    # ------------------------------------------------------------------

    async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        """Idempotent INSERT of one ``runtime_run_usage`` row.

        ``ON CONFLICT (run_id) DO NOTHING`` makes worker retries safe; if
        the row already exists, the second write is a no-op. The row is a
        derived aggregate — failure to write it must not break the run
        completion path, so the caller swallows write errors and metrics
        them rather than propagating.
        """

        async with self._tenant_connection(org_id=record.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_run_usage (
                    id, org_id, user_id, conversation_id, run_id, assistant_id,
                    model_provider, model_name, input_tokens, output_tokens,
                    cached_input_tokens, cache_creation_input_tokens,
                    reasoning_tokens, audio_input_tokens, audio_output_tokens,
                    total_tokens, chunk_count,
                    first_token_ms, duration_ms, started_at, completed_at,
                    status, schema_version, retention_until, pii_purged_at,
                    cost_micro_usd, pricing_id, pricing_version, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    record.id,
                    record.org_id,
                    record.user_id,
                    record.conversation_id,
                    record.run_id,
                    record.assistant_id,
                    record.model_provider,
                    record.model_name,
                    record.input_tokens,
                    record.output_tokens,
                    record.cached_input_tokens,
                    record.cache_creation_input_tokens,
                    record.reasoning_tokens,
                    record.audio_input_tokens,
                    record.audio_output_tokens,
                    record.total_tokens,
                    record.chunk_count,
                    record.first_token_ms,
                    record.duration_ms,
                    record.started_at,
                    record.completed_at,
                    record.status,
                    record.schema_version,
                    record.retention_until,
                    record.pii_purged_at,
                    record.cost_micro_usd,
                    record.pricing_id,
                    record.pricing_version,
                    record.created_at,
                ),
            )

    async def record_model_call_usage(
        self, record: RuntimeModelCallUsageRecord
    ) -> None:
        """Append a per-LLM-call usage row.

        Rows are unique by their own UUID id so no ON CONFLICT is needed;
        upstream dedupe (one row per AIMessage id) is the worker's job.
        """

        async with self._tenant_connection(org_id=record.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_model_call_usage (
                    id, org_id, run_id, conversation_id, parent_event_id,
                    trace_id, task_id, subagent_id, model_provider, model_name,
                    connector_slug,
                    purpose, originating_tool_call_id, originating_tool_name,
                    input_tokens, output_tokens, cached_input_tokens,
                    cache_creation_input_tokens, reasoning_tokens,
                    audio_input_tokens, audio_output_tokens,
                    total_tokens, duration_ms, schema_version, cost_micro_usd,
                    pricing_id, pricing_version, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    record.id,
                    record.org_id,
                    record.run_id,
                    record.conversation_id,
                    record.parent_event_id,
                    record.trace_id,
                    record.task_id,
                    record.subagent_id,
                    record.model_provider,
                    record.model_name,
                    record.connector_slug,
                    record.purpose,
                    record.originating_tool_call_id,
                    record.originating_tool_name,
                    record.input_tokens,
                    record.output_tokens,
                    record.cached_input_tokens,
                    record.cache_creation_input_tokens,
                    record.reasoning_tokens,
                    record.audio_input_tokens,
                    record.audio_output_tokens,
                    record.total_tokens,
                    record.duration_ms,
                    record.schema_version,
                    record.cost_micro_usd,
                    record.pricing_id,
                    record.pricing_version,
                    record.created_at,
                ),
            )

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Back-fill the cost fields on a run usage row after pricing is resolved."""
        async with self._tenant_connection(role=self._role) as conn:
            await conn.execute(
                """
                UPDATE runtime_run_usage
                   SET cost_micro_usd = %s,
                       pricing_id = %s,
                       pricing_version = %s
                 WHERE run_id = %s
                """,
                (cost_micro_usd, pricing_id, pricing_version, run_id),
            )

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Back-fill the cost fields on a model-call usage row after pricing is resolved."""
        async with self._tenant_connection(role=self._role) as conn:
            await conn.execute(
                """
                UPDATE runtime_model_call_usage
                   SET cost_micro_usd = %s,
                       pricing_id = %s,
                       pricing_version = %s
                 WHERE id = %s
                """,
                (cost_micro_usd, pricing_id, pricing_version, usage_id),
            )

    async def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
        """Replace the active pricing row for (provider, model, region).

        The partial unique index on ``effective_until IS NULL`` requires
        that we close any prior active row before inserting the new one.
        Both writes happen in a single transaction so a reader never sees
        zero rows or two active rows.
        """

        async with self._tenant_connection(role=self._role) as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE model_pricing
                       SET effective_until = %s
                     WHERE provider = %s
                       AND model_name = %s
                       AND region = %s
                       AND effective_until IS NULL
                       AND effective_from < %s
                    """,
                    (
                        record.effective_from,
                        record.provider,
                        record.model_name,
                        record.region,
                        record.effective_from,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO model_pricing (
                        id, provider, model_name, region, effective_from,
                        effective_until, input_per_1m_micro_usd,
                        output_per_1m_micro_usd, cached_input_per_1m_micro_usd,
                        context_window_tokens, pricing_source, pricing_version,
                        created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s
                    )
                    """,
                    (
                        record.id,
                        record.provider,
                        record.model_name,
                        record.region,
                        record.effective_from,
                        record.effective_until,
                        record.input_per_1m_micro_usd,
                        record.output_per_1m_micro_usd,
                        record.cached_input_per_1m_micro_usd,
                        record.context_window_tokens,
                        record.pricing_source,
                        record.pricing_version,
                        record.created_at,
                    ),
                )
        return record

    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        """Return the active pricing row for (provider, model, region) at ``at``, or ``None``."""
        async with self._tenant_connection(role=self._role) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM model_pricing
                 WHERE provider = %s
                   AND model_name = %s
                   AND region = %s
                   AND effective_from <= %s
                   AND (effective_until IS NULL OR effective_until > %s)
                 ORDER BY effective_from DESC
                 LIMIT 1
                """,
                (provider, model_name, region, at, at),
            )
            row = await cur.fetchone()
        return self._pricing_record(row) if row is not None else None

    async def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run usage rows with no cost yet computed, keyset-paginated by ``cursor``."""
        cursor_clause = "AND id > %s" if cursor is not None else ""
        params: tuple[object, ...] = (limit, cursor) if cursor is not None else (limit,)
        async with self._tenant_connection(role=self._role) as conn:
            cur = await conn.execute(
                f"""
                SELECT * FROM runtime_run_usage
                 WHERE cost_micro_usd IS NULL
                   {cursor_clause}
                 ORDER BY id
                 LIMIT %s
                """,
                # Param order: cursor (if any), limit
                tuple(reversed(params)) if cursor is not None else (limit,),
            )
            rows = await cur.fetchall()
        return tuple(self._run_usage_record(row) for row in rows)

    async def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        """Insert or replace the per-user per-day usage aggregate for (org, user, day, model)."""
        async with self._tenant_connection(org_id=row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_usage_daily_user (
                    org_id, user_id, day, model_provider, model_name,
                    runs_count, input_tokens, output_tokens,
                    cached_input_tokens, total_tokens, cost_micro_usd,
                    refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (org_id, user_id, day, model_provider, model_name)
                DO UPDATE SET
                    runs_count = EXCLUDED.runs_count,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cached_input_tokens = EXCLUDED.cached_input_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cost_micro_usd = EXCLUDED.cost_micro_usd,
                    refreshed_at = EXCLUDED.refreshed_at
                """,
                (
                    row.org_id,
                    row.user_id,
                    row.day.date(),
                    row.model_provider,
                    row.model_name,
                    row.runs_count,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_input_tokens,
                    row.total_tokens,
                    row.cost_micro_usd,
                    row.refreshed_at,
                ),
            )

    async def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        """Insert or replace the per-org per-day usage aggregate for (org, day, model)."""
        async with self._tenant_connection(org_id=row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_usage_daily_org (
                    org_id, day, model_provider, model_name, runs_count,
                    distinct_users, input_tokens, output_tokens,
                    cached_input_tokens, total_tokens, cost_micro_usd,
                    refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (org_id, day, model_provider, model_name)
                DO UPDATE SET
                    runs_count = EXCLUDED.runs_count,
                    distinct_users = EXCLUDED.distinct_users,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cached_input_tokens = EXCLUDED.cached_input_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cost_micro_usd = EXCLUDED.cost_micro_usd,
                    refreshed_at = EXCLUDED.refreshed_at
                """,
                (
                    row.org_id,
                    row.day.date(),
                    row.model_provider,
                    row.model_name,
                    row.runs_count,
                    row.distinct_users,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_input_tokens,
                    row.total_tokens,
                    row.cost_micro_usd,
                    row.refreshed_at,
                ),
            )

    async def upsert_connector_daily_usage(self, row: UsageDailyConnectorRow) -> None:
        """Insert or replace the per-connector per-day usage aggregate for (org, day, connector, model)."""
        async with self._tenant_connection(org_id=row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_usage_daily_connector (
                    org_id, day, connector_slug, model_name, runs_count,
                    distinct_users, input_tokens, output_tokens,
                    cached_input_tokens, total_tokens, cost_micro_usd,
                    refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
                ON CONFLICT (org_id, day, connector_slug, model_name)
                DO UPDATE SET
                    runs_count = EXCLUDED.runs_count,
                    distinct_users = EXCLUDED.distinct_users,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cached_input_tokens = EXCLUDED.cached_input_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cost_micro_usd = EXCLUDED.cost_micro_usd,
                    refreshed_at = EXCLUDED.refreshed_at
                """,
                (
                    row.org_id,
                    row.day.date(),
                    row.connector_slug,
                    row.model_name,
                    row.runs_count,
                    row.distinct_users,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_input_tokens,
                    row.total_tokens,
                    row.cost_micro_usd,
                    row.refreshed_at,
                ),
            )

    async def upsert_subagent_daily_usage(self, row: UsageDailySubagentRow) -> None:
        """Insert or replace the per-subagent per-day usage aggregate for (org, day, subagent, model)."""
        async with self._tenant_connection(org_id=row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_usage_daily_subagent (
                    org_id, day, subagent_slug, model_provider, model_name,
                    call_count,
                    input_tokens, output_tokens, cached_input_tokens,
                    cache_creation_input_tokens, reasoning_tokens,
                    audio_input_tokens, audio_output_tokens,
                    total_tokens, cost_micro_usd, refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (org_id, day, subagent_slug, model_provider, model_name)
                DO UPDATE SET
                    call_count = EXCLUDED.call_count,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cached_input_tokens = EXCLUDED.cached_input_tokens,
                    cache_creation_input_tokens = EXCLUDED.cache_creation_input_tokens,
                    reasoning_tokens = EXCLUDED.reasoning_tokens,
                    audio_input_tokens = EXCLUDED.audio_input_tokens,
                    audio_output_tokens = EXCLUDED.audio_output_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cost_micro_usd = EXCLUDED.cost_micro_usd,
                    refreshed_at = EXCLUDED.refreshed_at
                """,
                (
                    row.org_id,
                    row.day.date(),
                    row.subagent_slug,
                    row.model_provider,
                    row.model_name,
                    row.call_count,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_input_tokens,
                    row.cache_creation_input_tokens,
                    row.reasoning_tokens,
                    row.audio_input_tokens,
                    row.audio_output_tokens,
                    row.total_tokens,
                    row.cost_micro_usd,
                    row.refreshed_at,
                ),
            )

    async def upsert_purpose_daily_usage(self, row: UsageDailyPurposeRow) -> None:
        """Insert or replace the per-purpose per-day usage aggregate for (org, day, purpose, model)."""
        async with self._tenant_connection(org_id=row.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_usage_daily_purpose (
                    org_id, day, purpose, model_provider, model_name,
                    call_count,
                    input_tokens, output_tokens, cached_input_tokens,
                    cache_creation_input_tokens, reasoning_tokens,
                    audio_input_tokens, audio_output_tokens,
                    total_tokens, cost_micro_usd, refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (org_id, day, purpose, model_provider, model_name)
                DO UPDATE SET
                    call_count = EXCLUDED.call_count,
                    input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens,
                    cached_input_tokens = EXCLUDED.cached_input_tokens,
                    cache_creation_input_tokens = EXCLUDED.cache_creation_input_tokens,
                    reasoning_tokens = EXCLUDED.reasoning_tokens,
                    audio_input_tokens = EXCLUDED.audio_input_tokens,
                    audio_output_tokens = EXCLUDED.audio_output_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cost_micro_usd = EXCLUDED.cost_micro_usd,
                    refreshed_at = EXCLUDED.refreshed_at
                """,
                (
                    row.org_id,
                    row.day.date(),
                    row.purpose,
                    row.model_provider,
                    row.model_name,
                    row.call_count,
                    row.input_tokens,
                    row.output_tokens,
                    row.cached_input_tokens,
                    row.cache_creation_input_tokens,
                    row.reasoning_tokens,
                    row.audio_input_tokens,
                    row.audio_output_tokens,
                    row.total_tokens,
                    row.cost_micro_usd,
                    row.refreshed_at,
                ),
            )

    @reader
    async def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyUserRow]:
        """Return daily usage rows for a user within the given day range, newest first."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_usage_daily_user
                 WHERE org_id = %s
                   AND user_id = %s
                   AND day BETWEEN %s AND %s
                 ORDER BY day DESC
                """,
                (org_id, user_id, start_day.date(), end_day.date()),
            )
            rows = await cur.fetchall()
        return tuple(self._user_daily_row(r) for r in rows)

    @reader
    async def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyOrgRow]:
        """Return daily usage rows for an org within the given day range, newest first."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_usage_daily_org
                 WHERE org_id = %s
                   AND day BETWEEN %s AND %s
                 ORDER BY day DESC
                """,
                (org_id, start_day.date(), end_day.date()),
            )
            rows = await cur.fetchall()
        return tuple(self._org_daily_row(r) for r in rows)

    @reader
    async def query_connector_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyConnectorRow]:
        """Return daily connector usage rows for an org within the given day range, newest first."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_usage_daily_connector
                 WHERE org_id = %s
                   AND day BETWEEN %s AND %s
                 ORDER BY day DESC
                """,
                (org_id, start_day.date(), end_day.date()),
            )
            rows = await cur.fetchall()
        return tuple(self._connector_daily_row(r) for r in rows)

    @reader
    async def query_subagent_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailySubagentRow]:
        """Return daily subagent usage rows for an org within the given day range, newest first."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_usage_daily_subagent
                 WHERE org_id = %s
                   AND day BETWEEN %s AND %s
                 ORDER BY day DESC
                """,
                (org_id, start_day.date(), end_day.date()),
            )
            rows = await cur.fetchall()
        return tuple(self._subagent_daily_row(r) for r in rows)

    @reader
    async def query_purpose_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyPurposeRow]:
        """Return daily purpose usage rows for an org within the given day range, newest first."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_usage_daily_purpose
                 WHERE org_id = %s
                   AND day BETWEEN %s AND %s
                 ORDER BY day DESC
                """,
                (org_id, start_day.date(), end_day.date()),
            )
            rows = await cur.fetchall()
        return tuple(self._purpose_daily_row(r) for r in rows)

    async def query_model_call_usage_for_range(
        self,
        *,
        org_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return model-call usage rows within the time window; ``org_id=None`` scans all tenants."""
        # Cold-start fallback (per-tenant) AND rollup-loop scan (org_id=None).
        # Caller bounds the window: API endpoints cap at 30d, rollup loop at
        # the configured trailing window (default 2d).
        if org_id is None:
            sql = """
                SELECT * FROM runtime_model_call_usage
                 WHERE created_at BETWEEN %s AND %s
                 ORDER BY created_at DESC
            """
            params: tuple[object, ...] = (start, end)
            cm = self._role_connection("worker")
        else:
            sql = """
                SELECT * FROM runtime_model_call_usage
                 WHERE org_id = %s
                   AND created_at BETWEEN %s AND %s
                 ORDER BY created_at DESC
            """
            params = (org_id, start, end)
            cm = self._tenant_connection(org_id=org_id)
        async with cm as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        return tuple(self._model_call_record(r) for r in rows)

    async def list_run_ids_for_agent(
        self,
        *,
        org_id: str,
        agent_id: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[str]:
        """Return run IDs whose ``runtime_context_json -> 'trace_metadata' -> 'agent_id'`` matches.

        Read-only. Tenant-scoped via ``_tenant_connection`` (RLS-enforced).
        ``agent_id`` lives inside the existing ``runtime_context_json``
        JSONB column under ``trace_metadata.agent_id`` — no new column,
        no parallel store, no new tracker (cross-audit §5.5).
        """

        if not agent_id:
            return ()
        sql = """
            SELECT id FROM agent_runs
             WHERE org_id = %s
               AND created_at BETWEEN %s AND %s
               AND runtime_context_json #>> '{trace_metadata,agent_id}' = %s
             ORDER BY created_at DESC
        """
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(sql, (org_id, start, end, agent_id))
            rows = await cur.fetchall()
        return tuple(str(row["id"]) for row in rows)

    @reader
    async def list_audit_log_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[dict[str, object]]:
        """Return audit log events for an org with optional filters; binary hash fields are hex-encoded."""
        clauses = ["org_id = %s", "(seq IS NULL OR seq > %s)"]
        params: list[object] = [org_id, after_seq]
        if action_prefix is not None:
            clauses.append("action LIKE %s")
            params.append(action_prefix + "%")
        if actor_user_id is not None:
            clauses.append("user_id = %s")
            params.append(actor_user_id)
        if since is not None:
            clauses.append("created_at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("created_at < %s")
            params.append(until)
        params.append(limit)
        sql = (
            "SELECT id AS audit_id, org_id, user_id, actor_type, action, "
            "resource_type, resource_id, run_id, trace_id, outcome, "
            "metadata_json_redacted AS metadata, created_at, seq, prev_hash, "
            "signature, key_version "
            "FROM runtime_audit_log "
            "WHERE " + " AND ".join(clauses) + " "
            "ORDER BY created_at DESC, seq DESC NULLS LAST "
            "LIMIT %s"
        )
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            mapped = dict(row)
            for key in ("prev_hash", "signature"):
                value = mapped.get(key)
                if isinstance(value, (bytes, bytearray, memoryview)):
                    mapped[key] = bytes(value).hex()
            result.append(mapped)
        return tuple(result)

    async def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the usage record for a single run, or ``None`` if not yet written."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_run_usage
                 WHERE org_id = %s AND run_id = %s
                """,
                (org_id, run_id),
            )
            row = await cur.fetchone()
        return self._run_usage_record(row) if row is not None else None

    async def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run usage rows within the time window; ``org_id=None`` scans all tenants for rollup."""
        # Cold-start fallback (per-tenant) AND rollup-loop scan (org_id=None).
        # Caller bounds the window: API endpoints cap at 30d, rollup loop at
        # the configured trailing window (default 2d).
        if org_id is None:
            sql = """
                SELECT * FROM runtime_run_usage
                 WHERE completed_at BETWEEN %s AND %s
                 ORDER BY completed_at DESC
            """
            params: tuple[object, ...] = (start, end)
        elif user_id is not None:
            sql = """
                SELECT * FROM runtime_run_usage
                 WHERE org_id = %s AND user_id = %s
                   AND completed_at BETWEEN %s AND %s
                   AND pii_purged_at IS NULL
                 ORDER BY completed_at DESC
            """
            params = (org_id, user_id, start, end)
        else:
            sql = """
                SELECT * FROM runtime_run_usage
                 WHERE org_id = %s
                   AND completed_at BETWEEN %s AND %s
                 ORDER BY completed_at DESC
            """
            params = (org_id, start, end)
        # When ``org_id`` is None we're scanning across tenants for the
        # rollup loop — use the worker role connection so the
        # outbox-style ``tenant_or_worker`` RLS policy grants cross-tenant access.
        if org_id is None:
            cm = self._role_connection("worker")
        else:
            cm = self._tenant_connection(org_id=org_id)
        async with cm as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        return tuple(self._run_usage_record(r) for r in rows)

    @reader
    async def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Sequence[UsageConversationAggregateRecord]:
        """Return the top conversations by token usage for a user within the time window."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT
                    u.conversation_id,
                    c.title,
                    SUM(u.input_tokens) AS input_tokens,
                    SUM(u.output_tokens) AS output_tokens,
                    SUM(u.cached_input_tokens) AS cached_input_tokens,
                    SUM(u.total_tokens) AS total_tokens,
                    COUNT(*) AS runs_count,
                    CASE
                        WHEN COUNT(u.cost_micro_usd) = 0 THEN NULL
                        ELSE SUM(u.cost_micro_usd)
                    END AS cost_micro_usd
                  FROM runtime_run_usage AS u
                  LEFT JOIN agent_conversations AS c
                    ON c.org_id = u.org_id AND c.id = u.conversation_id
                 WHERE u.org_id = %s AND u.user_id = %s
                   AND u.completed_at BETWEEN %s AND %s
                   AND u.pii_purged_at IS NULL
                 GROUP BY u.conversation_id, c.title
                 ORDER BY total_tokens DESC
                 LIMIT %s
                """,
                (org_id, user_id, start, end, limit),
            )
            rows = await cur.fetchall()
        return tuple(
            UsageConversationAggregateRecord(
                conversation_id=str(r["conversation_id"]),
                title=r["title"],
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
                cached_input_tokens=int(r["cached_input_tokens"] or 0),
                total_tokens=int(r["total_tokens"] or 0),
                runs_count=int(r["runs_count"] or 0),
                cost_micro_usd=(
                    int(r["cost_micro_usd"])
                    if r["cost_micro_usd"] is not None
                    else None
                ),
            )
            for r in rows
        )

    @reader
    async def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return all model-call usage rows for a run, ordered by call time ascending."""
        async with self._read_only_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_model_call_usage
                 WHERE org_id = %s AND run_id = %s
                 ORDER BY created_at ASC
                """,
                (org_id, run_id),
            )
            rows = await cur.fetchall()
        return tuple(self._model_call_record(r) for r in rows)

    async def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the most recent run usage record for a conversation, skipping PII-purged rows."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_run_usage
                 WHERE org_id = %s AND user_id = %s AND conversation_id = %s
                   AND pii_purged_at IS NULL
                 ORDER BY completed_at DESC
                 LIMIT 1
                """,
                (org_id, user_id, conversation_id),
            )
            row = await cur.fetchone()
        return self._run_usage_record(row) if row is not None else None

    async def query_compression_events_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[CompressionEventRecord]:
        """Return all context-compression events for a run, ordered by occurrence time ascending."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_compression_events
                 WHERE org_id = %s AND run_id = %s
                 ORDER BY created_at ASC
                """,
                (org_id, run_id),
            )
            rows = await cur.fetchall()
        return tuple(self._compression_event_record(r) for r in rows)

    # ------------------------------------------------------------------
    # Budgets.
    # ------------------------------------------------------------------

    async def lookup_budgets_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> Sequence[BudgetWithState]:
        """Return active budgets (org-scope + user-scope) with current period spend and reservations."""
        # ``LEFT JOIN LATERAL`` collapses three queries (budget, state for
        # the current period, sum of unconsumed reservations for the
        # current period) into one round-trip. Period start is computed
        # in SQL so the API doesn't have to know UTC midnight semantics.
        # The ``now`` parameter is honored by the in-memory store for
        # test determinism; here SQL ``now()`` (server-clock) is the
        # authoritative source — the round-trip latency in production
        # is well under a second.
        del now
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                WITH active AS (
                    SELECT * FROM usage_budgets
                     WHERE org_id = %s
                       AND status = 'active'
                       AND (scope = 'org' OR (scope = 'user' AND user_id = %s))
                ),
                period AS (
                    SELECT
                        b.id AS budget_id,
                        CASE
                            WHEN b.period = 'day' THEN date_trunc('day', now() AT TIME ZONE 'UTC')::date
                            WHEN b.period = 'month' THEN date_trunc('month', now() AT TIME ZONE 'UTC')::date
                        END AS period_start,
                        CASE
                            WHEN b.period = 'day' THEN date_trunc('day', now() AT TIME ZONE 'UTC')::date
                            WHEN b.period = 'month' THEN (date_trunc('month', now() AT TIME ZONE 'UTC')
                                + interval '1 month' - interval '1 day')::date
                        END AS period_end
                      FROM active b
                ),
                reserved AS (
                    SELECT r.budget_id,
                           r.period_start,
                           COALESCE(SUM(r.reserved_micro_usd), 0) AS reserved_micro,
                           COALESCE(SUM(r.reserved_tokens), 0) AS reserved_tokens
                      FROM usage_budget_reservations r
                      JOIN period p ON p.budget_id = r.budget_id AND p.period_start = r.period_start
                     WHERE r.consumed_at IS NULL
                     GROUP BY r.budget_id, r.period_start
                )
                SELECT
                    b.id, b.org_id, b.user_id, b.scope, b.period, b.enforcement,
                    b.limit_micro_usd, b.limit_tokens, b.status,
                    b.created_at, b.updated_at, b.created_by_user_id,
                    p.period_start, p.period_end,
                    COALESCE(s.current_spend_micro_usd, 0) AS current_spend_micro_usd,
                    COALESCE(s.current_spend_tokens, 0) AS current_spend_tokens,
                    COALESCE(s.row_version, 1) AS row_version,
                    s.last_charged_run_id,
                    COALESCE(s.updated_at, b.updated_at) AS state_updated_at,
                    COALESCE(r.reserved_micro, 0) AS reserved_micro,
                    COALESCE(r.reserved_tokens, 0) AS reserved_tokens,
                    (s.budget_id IS NOT NULL) AS has_state
                  FROM active b
                  JOIN period p ON p.budget_id = b.id
                  LEFT JOIN usage_budget_state s
                    ON s.budget_id = b.id AND s.period_start = p.period_start
                  LEFT JOIN reserved r
                    ON r.budget_id = b.id AND r.period_start = p.period_start
                 ORDER BY b.id ASC
                """,
                (org_id, user_id),
            )
            rows = await cur.fetchall()
        return tuple(self._budget_with_state(row) for row in rows)

    async def charge_budget(
        self,
        *,
        budget_id: str,
        period_start,
        period_end,
        delta_micro_usd: int,
        delta_tokens: int,
        run_id: str,
        now: datetime,
    ) -> ChargeOutcome:
        """Atomically increment period spend; idempotent per run_id; returns the charge outcome."""
        async with self._role_connection("worker") as conn:
            # Try INSERT first so a fresh-period charge succeeds without
            # a separate "create state row" call. ON CONFLICT DO NOTHING
            # then we run the UPDATE branch below for the existing row.
            await conn.execute(
                """
                INSERT INTO usage_budget_state (
                    budget_id, period_start, period_end,
                    current_spend_micro_usd, current_spend_tokens,
                    row_version, last_charged_run_id, updated_at
                ) VALUES (%s, %s, %s, 0, 0, 1, NULL, %s)
                ON CONFLICT (budget_id, period_start) DO NOTHING
                """,
                (budget_id, period_start, period_end, now),
            )
            cur = await conn.execute(
                """
                UPDATE usage_budget_state
                   SET current_spend_micro_usd = current_spend_micro_usd + %s,
                       current_spend_tokens = current_spend_tokens + %s,
                       row_version = row_version + 1,
                       last_charged_run_id = %s,
                       updated_at = %s
                 WHERE budget_id = %s
                   AND period_start = %s
                   AND last_charged_run_id IS DISTINCT FROM %s
                RETURNING row_version
                """,
                (
                    delta_micro_usd,
                    delta_tokens,
                    run_id,
                    now,
                    budget_id,
                    period_start,
                    run_id,
                ),
            )
            row = await cur.fetchone()
            if row is not None:
                return ChargeOutcome.APPLIED
            # Either idempotent re-charge or row_version drift — disambiguate.
            cur = await conn.execute(
                """
                SELECT last_charged_run_id
                  FROM usage_budget_state
                 WHERE budget_id = %s AND period_start = %s
                """,
                (budget_id, period_start),
            )
            existing = await cur.fetchone()
            if existing is not None and existing.get("last_charged_run_id") == run_id:
                return ChargeOutcome.IDEMPOTENT_NOOP
            return ChargeOutcome.EXHAUSTED_RETRIES

    async def reserve_budget(
        self,
        *,
        budget_id: str,
        period_start,
        run_id: str,
        reserved_micro_usd: int,
        reserved_tokens: int,
        now: datetime,
    ) -> BudgetReservationRecord | None:
        """Create a budget reservation for a run; idempotent per (budget_id, run_id); returns None on conflict."""
        from agent_runtime.budgets.reservations import BudgetReservationManager

        from uuid import uuid4

        reservation_id = uuid4().hex
        expires_at = BudgetReservationManager.expires_at(now=now, ttl_seconds=60)
        async with self._role_connection("worker") as conn:
            cur = await conn.execute(
                """
                INSERT INTO usage_budget_reservations (
                    reservation_id, budget_id, period_start, run_id,
                    reserved_micro_usd, reserved_tokens, expires_at, consumed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (budget_id, run_id) WHERE consumed_at IS NULL DO NOTHING
                RETURNING reservation_id, expires_at
                """,
                (
                    reservation_id,
                    budget_id,
                    period_start,
                    run_id,
                    reserved_micro_usd,
                    reserved_tokens,
                    expires_at,
                ),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return BudgetReservationRecord(
            reservation_id=str(row["reservation_id"]),
            budget_id=budget_id,
            period_start=period_start,
            run_id=run_id,
            reserved_micro_usd=reserved_micro_usd,
            reserved_tokens=reserved_tokens,
            expires_at=self._coerce_datetime(row["expires_at"]),
        )

    async def consume_budget_reservation(
        self, *, reservation_id: str, now: datetime
    ) -> None:
        """Mark a budget reservation as consumed so it is excluded from future hold totals."""
        async with self._role_connection("worker") as conn:
            await conn.execute(
                """
                UPDATE usage_budget_reservations
                   SET consumed_at = %s
                 WHERE reservation_id = %s AND consumed_at IS NULL
                """,
                (now, reservation_id),
            )

    async def reap_expired_budget_reservations(self, *, now: datetime) -> int:
        """Delete all unconsumed reservations whose TTL has passed; returns the count deleted."""
        async with self._role_connection("worker") as conn:
            cur = await conn.execute(
                """
                DELETE FROM usage_budget_reservations
                 WHERE consumed_at IS NULL AND expires_at < %s
                """,
                (now,),
            )
            return cur.rowcount or 0

    async def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        """Return all budgets for an org, newest first."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM usage_budgets
                 WHERE org_id = %s
                 ORDER BY created_at DESC
                """,
                (org_id,),
            )
            rows = await cur.fetchall()
        return tuple(self._budget_record(row) for row in rows)

    async def list_tool_budgets_for_org(
        self, *, org_id: str
    ) -> Sequence[ToolBudgetRecord]:
        """Return per-tool budgets visible to the org: own rows plus global (org_id IS NULL) rows.

        The RLS policy ``tenant_or_global`` on ``runtime_tool_budgets``
        already lets a tenant connection read its own rows AND the
        ``org_id IS NULL`` global rows; the WHERE clause here makes the
        intent explicit so a future RLS tightening doesn't silently
        drop the global rows.
        """

        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM runtime_tool_budgets
                 WHERE org_id = %s OR org_id IS NULL
                """,
                (org_id,),
            )
            rows = await cur.fetchall()
        return tuple(self._tool_budget_record(row) for row in rows)

    @staticmethod
    def _tool_budget_record(row: Mapping[str, object]) -> ToolBudgetRecord:
        return ToolBudgetRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]) if row.get("org_id") is not None else None,
            tool_name=str(row["tool_name"]),
            max_calls_per_run=int(row["max_calls_per_run"]),
            max_input_tokens_per_call=(
                int(row["max_input_tokens_per_call"])
                if row.get("max_input_tokens_per_call") is not None
                else None
            ),
            max_input_tokens_per_run=(
                int(row["max_input_tokens_per_run"])
                if row.get("max_input_tokens_per_run") is not None
                else None
            ),
            enforcement=ToolBudgetEnforcement(row["enforcement"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        """Return a single budget by ID, or ``None`` if not found."""
        async with self._tenant_connection(org_id=org_id) as conn:
            cur = await conn.execute(
                """
                SELECT * FROM usage_budgets WHERE org_id = %s AND id = %s
                """,
                (org_id, budget_id),
            )
            row = await cur.fetchone()
        return self._budget_record(row) if row is not None else None

    async def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Insert a new budget row and return the record unchanged."""
        async with self._tenant_connection(org_id=record.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO usage_budgets (
                    id, org_id, user_id, scope, period, enforcement,
                    limit_micro_usd, limit_tokens, status,
                    created_at, updated_at, created_by_user_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.org_id,
                    record.user_id,
                    record.scope.value,
                    record.period.value,
                    record.enforcement.value,
                    record.limit_micro_usd,
                    record.limit_tokens,
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.created_by_user_id,
                ),
            )
        return record

    async def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Update mutable budget fields (enforcement, limits, status) and return the record."""
        async with self._tenant_connection(org_id=record.org_id) as conn:
            await conn.execute(
                """
                UPDATE usage_budgets SET
                    enforcement = %s,
                    limit_micro_usd = %s,
                    limit_tokens = %s,
                    status = %s,
                    updated_at = %s
                 WHERE id = %s AND org_id = %s
                """,
                (
                    record.enforcement.value,
                    record.limit_micro_usd,
                    record.limit_tokens,
                    record.status.value,
                    record.updated_at,
                    record.id,
                    record.org_id,
                ),
            )
        return record

    async def delete_budget(self, *, org_id: str, budget_id: str) -> None:
        """Hard-delete a budget row; silently a no-op if the row does not exist."""
        async with self._tenant_connection(org_id=org_id) as conn:
            await conn.execute(
                "DELETE FROM usage_budgets WHERE org_id = %s AND id = %s",
                (org_id, budget_id),
            )

    # ------------------------------------------------------------------
    # Retention — the sweeper walks orgs cross-tenant under
    # ``app.role='worker'`` so RLS allows the per-org policy lookup +
    # tombstone/delete; same trust contract as the usage rollup loop.
    # ------------------------------------------------------------------

    async def list_retention_orgs(self) -> tuple[str, ...]:
        """Return distinct org IDs that have rows in any retention-managed table."""
        async with self._role_connection("worker") as conn:
            async with conn.cursor() as cur:
                # Distinct org_ids across the affected tables. The UNION
                # is small (one row per org) and the query runs at the
                # sweeper's tick cadence so it's not hot-path.
                await cur.execute(
                    """
                    SELECT DISTINCT org_id FROM agent_messages
                    UNION SELECT DISTINCT org_id FROM runtime_events
                    UNION SELECT DISTINCT org_id FROM runtime_context_payloads
                    UNION SELECT DISTINCT org_id FROM runtime_checkpoints
                    UNION SELECT DISTINCT org_id FROM runtime_memory_items
                    """
                )
                rows = await cur.fetchall()
        return tuple(str(row["org_id"]) for row in rows)

    async def list_retention_policies(
        self, *, org_id: str
    ) -> tuple[RetentionPolicyRecord, ...]:
        """Return all retention policies for an org, ordered by creation time."""
        async with self._tenant_connection(org_id=org_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, org_id, scope, resource_id, kind, ttl_seconds,
                           created_by_user_id, created_at, updated_at
                      FROM retention_policies
                     WHERE org_id = %s
                     ORDER BY created_at ASC
                    """,
                    (org_id,),
                )
                rows = await cur.fetchall()
        return tuple(self._retention_policy(row) for row in rows)

    async def upsert_retention_policy(
        self, record: RetentionPolicyRecord
    ) -> RetentionPolicyRecord:
        """Insert or update a retention policy; conflict on (org, scope, resource_id, kind) updates TTL only."""
        async with self._tenant_connection(org_id=record.org_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO retention_policies (
                        id, org_id, scope, resource_id, kind, ttl_seconds,
                        created_by_user_id, created_at, updated_at
                    ) VALUES (
                        %(id)s, %(org_id)s, %(scope)s, %(resource_id)s,
                        %(kind)s, %(ttl_seconds)s, %(created_by_user_id)s,
                        %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (org_id, scope, COALESCE(resource_id, ''), kind)
                    DO UPDATE SET
                        ttl_seconds = EXCLUDED.ttl_seconds,
                        updated_at = EXCLUDED.updated_at
                    """,
                    {
                        "id": record.id,
                        "org_id": record.org_id,
                        "scope": record.scope.value,
                        "resource_id": record.resource_id,
                        "kind": record.kind.value,
                        "ttl_seconds": record.ttl_seconds,
                        "created_by_user_id": record.created_by_user_id,
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                    },
                )
        return record

    async def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        """Remove a retention policy row; silently a no-op if the row does not exist."""
        async with self._tenant_connection(org_id=org_id) as conn:
            await conn.execute(
                "DELETE FROM retention_policies WHERE org_id = %s AND id = %s",
                (org_id, policy_id),
            )

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool = False,
        chunk_size: int = 0,
    ) -> RetentionSweepOutcome:
        """Expire or tombstone rows matching the retention kind; ``chunk_size>0`` uses keyset chunking."""
        # chunk_size > 0 → retention_until-based chunked CTE (avoids full-table scan on large orgs).
        # chunk_size == 0 → legacy created_at+ttl unbounded SQL (flag=false).
        if chunk_size > 0:
            if kind is RetentionKind.MESSAGES:
                return await self._sweep_messages_chunked(
                    org_id=org_id, chunk_size=chunk_size, dry_run=dry_run
                )
            if kind is RetentionKind.EVENTS:
                return await self._sweep_events_chunked(
                    org_id=org_id, chunk_size=chunk_size, dry_run=dry_run
                )
            if kind is RetentionKind.CONTEXT_PAYLOADS:
                return await self._sweep_context_payloads_chunked(
                    org_id=org_id, chunk_size=chunk_size, dry_run=dry_run
                )
            if kind is RetentionKind.CHECKPOINTS:
                return await self._sweep_checkpoints_chunked(
                    org_id=org_id,
                    ttl_seconds=ttl_seconds,
                    chunk_size=chunk_size,
                    dry_run=dry_run,
                )
            if kind is RetentionKind.MEMORY_ITEMS:
                return await self._sweep_memory_items_chunked(
                    org_id=org_id, chunk_size=chunk_size, dry_run=dry_run
                )
            if kind is RetentionKind.MESSAGES_TOMBSTONED:
                return await self._sweep_messages_tombstoned_chunked(
                    org_id=org_id,
                    ttl_seconds=ttl_seconds,
                    chunk_size=chunk_size,
                    dry_run=dry_run,
                )
            if kind is RetentionKind.EVENTS_TOMBSTONED:
                return await self._sweep_events_tombstoned_chunked(
                    org_id=org_id,
                    ttl_seconds=ttl_seconds,
                    chunk_size=chunk_size,
                    dry_run=dry_run,
                )
            if kind is RetentionKind.MEMORY_ITEMS_TOMBSTONED:
                return await self._sweep_memory_items_tombstoned_chunked(
                    org_id=org_id,
                    ttl_seconds=ttl_seconds,
                    chunk_size=chunk_size,
                    dry_run=dry_run,
                )
        else:
            if kind is RetentionKind.MESSAGES:
                return await self._sweep_messages(
                    org_id=org_id, ttl_seconds=ttl_seconds, dry_run=dry_run
                )
            if kind is RetentionKind.EVENTS:
                return await self._sweep_events(
                    org_id=org_id, ttl_seconds=ttl_seconds, dry_run=dry_run
                )
            if kind is RetentionKind.CONTEXT_PAYLOADS:
                return await self._sweep_context_payloads(
                    org_id=org_id, dry_run=dry_run
                )
            if kind is RetentionKind.CHECKPOINTS:
                return await self._sweep_checkpoints(
                    org_id=org_id, ttl_seconds=ttl_seconds, dry_run=dry_run
                )
            if kind is RetentionKind.MEMORY_ITEMS:
                return await self._sweep_memory_items(
                    org_id=org_id, ttl_seconds=ttl_seconds, dry_run=dry_run
                )
        raise ValueError(f"unknown retention kind: {kind!r}")

    async def _sweep_messages(
        self, *, org_id: str, ttl_seconds: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # Tombstone messages older than ttl whose conversation isn't on
        # legal hold. Audit rows referencing the message id stay intact.
        sql = """
            UPDATE agent_messages
               SET status = 'deleted',
                   content_text = '[deleted by retention policy]',
                   content_json = '[]'::jsonb,
                   metadata_json = '{}'::jsonb,
                   deleted_at = NOW()
             WHERE org_id = %(org_id)s
               AND status <> 'deleted'
               AND created_at < NOW() - make_interval(secs => %(ttl)s)
               AND conversation_id NOT IN (
                   SELECT resource_id
                     FROM runtime_legal_holds
                    WHERE org_id = %(org_id)s
                      AND scope IN ('conversation','user','org')
                      AND released_at IS NULL
               )
        """
        return await self._execute_sweep(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=ttl_seconds,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _sweep_events(
        self, *, org_id: str, ttl_seconds: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            UPDATE runtime_events
               SET payload_json_redacted = '{}'::jsonb,
                   metadata_json_redacted = jsonb_build_object('retention_purged', true)
             WHERE org_id = %(org_id)s
               AND created_at < NOW() - make_interval(secs => %(ttl)s)
               AND run_id NOT IN (
                   SELECT resource_id
                     FROM runtime_legal_holds
                    WHERE org_id = %(org_id)s
                      AND released_at IS NULL
               )
        """
        return await self._execute_sweep(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.EVENTS,
            ttl_seconds=ttl_seconds,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _sweep_context_payloads(
        self, *, org_id: str, dry_run: bool
    ) -> RetentionSweepOutcome:
        # The schema's retention_until column is authoritative for context
        # payloads; the resolver's TTL is unused on this path.
        sql = """
            DELETE FROM runtime_context_payloads
             WHERE org_id = %(org_id)s
               AND retention_until IS NOT NULL
               AND retention_until < NOW()
        """
        return await self._execute_sweep(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.CONTEXT_PAYLOADS,
            ttl_seconds=0,
            dry_run=dry_run,
            tally_field="deleted",
        )

    async def _sweep_checkpoints(
        self, *, org_id: str, ttl_seconds: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # Keep the latest 10 per (thread_id, namespace) plus anything in
        # the policy window. Older versions outside the window are
        # hard-deleted (no audit need; checkpoint blobs are reproducible
        # state, not user-visible PII).
        sql = """
            DELETE FROM runtime_checkpoints
             WHERE org_id = %(org_id)s
               AND id IN (
                   SELECT id FROM (
                       SELECT id,
                              ROW_NUMBER() OVER (
                                  PARTITION BY thread_id, checkpoint_namespace
                                  ORDER BY checkpoint_version DESC
                              ) AS rn,
                              created_at
                         FROM runtime_checkpoints
                        WHERE org_id = %(org_id)s
                   ) ranked
                   WHERE rn > 10
                     AND created_at < NOW() - make_interval(secs => %(ttl)s)
               )
        """
        return await self._execute_sweep(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.CHECKPOINTS,
            ttl_seconds=ttl_seconds,
            dry_run=dry_run,
            tally_field="deleted",
        )

    async def _sweep_memory_items(
        self, *, org_id: str, ttl_seconds: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            UPDATE runtime_memory_items
               SET deleted_at = NOW(),
                   content_summary = '[deleted by retention policy]'
             WHERE org_id = %(org_id)s
               AND deleted_at IS NULL
               AND created_at < NOW() - make_interval(secs => %(ttl)s)
        """
        return await self._execute_sweep(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MEMORY_ITEMS,
            ttl_seconds=ttl_seconds,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _execute_sweep(
        self,
        *,
        sql: str,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool,
        tally_field: str,
    ) -> RetentionSweepOutcome:
        # Dry-run runs the sweep inside a transaction we explicitly roll
        # back — the rowcount reflects exactly what would change without
        # leaving state behind. Live mode runs in autocommit per the
        # connection helper's default.
        async with self._tenant_connection(org_id=org_id) as conn:
            if dry_run:
                async with conn.transaction(force_rollback=True):
                    async with conn.cursor() as cur:
                        await cur.execute(sql, {"org_id": org_id, "ttl": ttl_seconds})
                        affected = cur.rowcount
            else:
                async with conn.cursor() as cur:
                    await cur.execute(sql, {"org_id": org_id, "ttl": ttl_seconds})
                    affected = cur.rowcount
        outcome = RetentionSweepOutcome(org_id=org_id, kind=kind)
        if tally_field == "tombstoned":
            return outcome.model_copy(update={"tombstoned": affected})
        return outcome.model_copy(update={"deleted": affected})

    # ------------------------------------------------------------------
    # Chunked retention_until-based sweep methods
    #
    # Each uses a CTE to select up to ``chunk_size`` due rows, then
    # UPDATEs (or DELETEs) them in one statement.  FOR UPDATE SKIP LOCKED
    # prevents two concurrent worker processes from racing on the same
    # rows.  Dry-run wraps in force_rollback so the count is accurate
    # without committing.
    # ------------------------------------------------------------------

    async def _execute_sweep_chunked(
        self,
        *,
        sql: str,
        org_id: str,
        kind: RetentionKind,
        chunk_size: int,
        dry_run: bool,
        tally_field: str,
        extra_params: dict | None = None,
    ) -> RetentionSweepOutcome:
        params: dict = {"org_id": org_id, "chunk_size": chunk_size}
        if extra_params:
            params.update(extra_params)
        async with self._tenant_connection(org_id=org_id) as conn:
            if dry_run:
                async with conn.transaction(force_rollback=True):
                    async with conn.cursor() as cur:
                        await cur.execute(sql, params)
                        affected = cur.rowcount
            else:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    affected = cur.rowcount
        outcome = RetentionSweepOutcome(org_id=org_id, kind=kind)
        if tally_field == "tombstoned":
            return outcome.model_copy(update={"tombstoned": affected})
        return outcome.model_copy(update={"deleted": affected})

    async def _sweep_messages_chunked(
        self, *, org_id: str, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            WITH due AS (
                SELECT id FROM agent_messages
                 WHERE org_id = %(org_id)s
                   AND status <> 'deleted'
                   AND retention_until IS NOT NULL
                   AND retention_until < NOW()
                   AND conversation_id NOT IN (
                       SELECT resource_id
                         FROM runtime_legal_holds
                        WHERE org_id = %(org_id)s
                          AND scope IN ('conversation','user','org')
                          AND released_at IS NULL
                   )
                 ORDER BY retention_until
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE agent_messages
               SET status = 'deleted',
                   content_text = '[deleted by retention policy]',
                   content_json = '[]'::jsonb,
                   metadata_json = '{}'::jsonb,
                   deleted_at = NOW()
              FROM due
             WHERE agent_messages.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MESSAGES,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _sweep_events_chunked(
        self, *, org_id: str, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            WITH due AS (
                SELECT id FROM runtime_events
                 WHERE org_id = %(org_id)s
                   AND retention_until IS NOT NULL
                   AND retention_until < NOW()
                   AND run_id NOT IN (
                       SELECT resource_id
                         FROM runtime_legal_holds
                        WHERE org_id = %(org_id)s
                          AND released_at IS NULL
                   )
                 ORDER BY retention_until
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE runtime_events
               SET payload_json_redacted = '{}'::jsonb,
                   metadata_json_redacted = jsonb_build_object('retention_purged', true)
              FROM due
             WHERE runtime_events.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.EVENTS,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _sweep_context_payloads_chunked(
        self, *, org_id: str, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            WITH due AS (
                SELECT id FROM runtime_context_payloads
                 WHERE org_id = %(org_id)s
                   AND retention_until IS NOT NULL
                   AND retention_until < NOW()
                 ORDER BY retention_until
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            DELETE FROM runtime_context_payloads
             USING due
             WHERE runtime_context_payloads.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.CONTEXT_PAYLOADS,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="deleted",
        )

    async def _sweep_checkpoints_chunked(
        self, *, org_id: str, ttl_seconds: int, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # CHECKPOINTS keeps its bespoke keep-N logic; gains chunking via LIMIT.
        sql = """
            WITH due AS (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY thread_id, checkpoint_namespace
                               ORDER BY checkpoint_version DESC
                           ) AS rn,
                           created_at
                      FROM runtime_checkpoints
                     WHERE org_id = %(org_id)s
                ) ranked
                WHERE rn > 10
                  AND created_at < NOW() - make_interval(secs => %(ttl)s)
                LIMIT %(chunk_size)s
            )
            DELETE FROM runtime_checkpoints
             WHERE id IN (SELECT id FROM due)
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.CHECKPOINTS,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="deleted",
            extra_params={"ttl": ttl_seconds},
        )

    async def _sweep_memory_items_chunked(
        self, *, org_id: str, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        sql = """
            WITH due AS (
                SELECT id FROM runtime_memory_items
                 WHERE org_id = %(org_id)s
                   AND deleted_at IS NULL
                   AND retention_until IS NOT NULL
                   AND retention_until < NOW()
                 ORDER BY retention_until
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE runtime_memory_items
               SET deleted_at = NOW(),
                   content_summary = '[deleted by retention policy]'
              FROM due
             WHERE runtime_memory_items.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MEMORY_ITEMS,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="tombstoned",
        )

    async def _sweep_messages_tombstoned_chunked(
        self, *, org_id: str, ttl_seconds: int, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # Hard-delete messages that were tombstoned (status='deleted') and
        # whose deleted_at exceeds the grace period. Legal hold exclusion
        # still applies so a hold placed after tombstone blocks hard-delete.
        sql = """
            WITH due AS (
                SELECT id FROM agent_messages
                 WHERE org_id = %(org_id)s
                   AND status = 'deleted'
                   AND deleted_at IS NOT NULL
                   AND deleted_at < NOW() - make_interval(secs => %(ttl)s)
                   AND conversation_id NOT IN (
                       SELECT resource_id
                         FROM runtime_legal_holds
                        WHERE org_id = %(org_id)s
                          AND released_at IS NULL
                   )
                 ORDER BY deleted_at
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            DELETE FROM agent_messages
             USING due
             WHERE agent_messages.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MESSAGES_TOMBSTONED,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="deleted",
            extra_params={"ttl": ttl_seconds},
        )

    async def _sweep_events_tombstoned_chunked(
        self, *, org_id: str, ttl_seconds: int, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # Hard-delete event rows whose payload was already redacted by the
        # first-pass sweep (retention_purged flag set). Grace window is
        # measured from retention_until (when the row became due for tombstone).
        sql = """
            WITH due AS (
                SELECT id FROM runtime_events
                 WHERE org_id = %(org_id)s
                   AND (metadata_json_redacted->>'retention_purged')::boolean IS TRUE
                   AND retention_until IS NOT NULL
                   AND retention_until < NOW() - make_interval(secs => %(ttl)s)
                   AND run_id NOT IN (
                       SELECT resource_id
                         FROM runtime_legal_holds
                        WHERE org_id = %(org_id)s
                          AND released_at IS NULL
                   )
                 ORDER BY retention_until
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            DELETE FROM runtime_events
             USING due
             WHERE runtime_events.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.EVENTS_TOMBSTONED,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="deleted",
            extra_params={"ttl": ttl_seconds},
        )

    async def _sweep_memory_items_tombstoned_chunked(
        self, *, org_id: str, ttl_seconds: int, chunk_size: int, dry_run: bool
    ) -> RetentionSweepOutcome:
        # Hard-delete memory items soft-deleted by the first pass. Grace
        # window measured from deleted_at.
        sql = """
            WITH due AS (
                SELECT id FROM runtime_memory_items
                 WHERE org_id = %(org_id)s
                   AND deleted_at IS NOT NULL
                   AND deleted_at < NOW() - make_interval(secs => %(ttl)s)
                 ORDER BY deleted_at
                 LIMIT %(chunk_size)s
                 FOR UPDATE SKIP LOCKED
            )
            DELETE FROM runtime_memory_items
             USING due
             WHERE runtime_memory_items.id = due.id
        """
        return await self._execute_sweep_chunked(
            sql=sql,
            org_id=org_id,
            kind=RetentionKind.MEMORY_ITEMS_TOMBSTONED,
            chunk_size=chunk_size,
            dry_run=dry_run,
            tally_field="deleted",
            extra_params={"ttl": ttl_seconds},
        )

    async def backfill_retention_until(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        chunk_size: int,
    ) -> int:
        """Populate ``retention_until`` on rows that have it NULL, up to ``chunk_size`` at a time; returns rows updated."""
        table = {
            RetentionKind.MESSAGES: "agent_messages",
            RetentionKind.EVENTS: "runtime_events",
            RetentionKind.MEMORY_ITEMS: "runtime_memory_items",
        }.get(kind)
        if table is None:
            return 0
        # CTE-based chunked update: select up to chunk_size unset rows in id
        # order, update their retention_until in one statement, return count.
        # SKIP LOCKED avoids contention if multiple worker processes ever run
        # the backfill concurrently (shouldn't happen in practice).
        sql = f"""
            WITH batch AS (
                SELECT id FROM {table}
                 WHERE org_id = %(org_id)s
                   AND retention_until IS NULL
                 ORDER BY id
                 LIMIT %(chunk_size)s
                   FOR UPDATE SKIP LOCKED
            )
            UPDATE {table}
               SET retention_until = created_at + make_interval(secs => %(ttl_seconds)s)
              FROM batch
             WHERE {table}.id = batch.id
        """
        async with self._tenant_connection(org_id=org_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    {
                        "org_id": org_id,
                        "chunk_size": chunk_size,
                        "ttl_seconds": ttl_seconds,
                    },
                )
                return cur.rowcount

    async def recompute_retention_until_for_policy(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        scope: RetentionScope,
        resource_id: str | None,
        ttl_seconds: int | None,
    ) -> int:
        """Rewrite ``retention_until`` for all rows affected by the given policy; returns rows updated."""
        table = {
            RetentionKind.MESSAGES: "agent_messages",
            RetentionKind.EVENTS: "runtime_events",
            RetentionKind.MEMORY_ITEMS: "runtime_memory_items",
        }.get(kind)
        if table is None:
            return 0

        set_expr = (
            "created_at + make_interval(secs => %(ttl_seconds)s)"
            if ttl_seconds is not None
            else "NULL"
        )

        if scope is RetentionScope.CONVERSATION:
            # Narrow update: only rows in this specific conversation.
            sql = f"""
                UPDATE {table}
                   SET retention_until = {set_expr}
                 WHERE org_id = %(org_id)s
                   AND conversation_id = %(resource_id)s
            """
        else:
            # ORG-scope: update all rows EXCEPT those already covered by
            # a more-specific CONVERSATION-scope policy for their conversation.
            sql = f"""
                UPDATE {table}
                   SET retention_until = {set_expr}
                 WHERE org_id = %(org_id)s
                   AND (
                       conversation_id IS NULL
                       OR conversation_id NOT IN (
                           SELECT DISTINCT resource_id
                             FROM retention_policies
                            WHERE org_id = %(org_id)s
                              AND scope = 'CONVERSATION'
                              AND kind = %(kind)s
                              AND resource_id IS NOT NULL
                       )
                   )
            """

        async with self._tenant_connection(org_id=org_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    {
                        "org_id": org_id,
                        "kind": kind.value,
                        "resource_id": resource_id,
                        "ttl_seconds": ttl_seconds,
                    },
                )
                return cur.rowcount

    async def insert_retention_deletion_evidence(
        self, record: RetentionDeletionEvidenceRecord
    ) -> None:
        """Persist sweep evidence so auditors can verify that retention obligations were met."""
        # Map sweeper fields onto the existing runtime_deletion_evidence
        # schema. The table was designed for user-erasure flows; we
        # repurpose it here using:
        #   user_id           → "__retention_sweeper__" sentinel
        #   request_type      → "retention_sweep"
        #   reason            → JSON context (kind, counts, dry_run)
        #   messages_tombstoned → tombstoned count (all tombstone kinds)
        #   runs_cancelled    → deleted count (hard-delete kinds)
        #   events_retained   → skipped_legal_hold count
        # Future: add proper per-kind columns via migration.
        import json

        reason_json = json.dumps(
            {
                "kind": record.kind.value,
                "tombstoned": record.tombstoned,
                "deleted": record.deleted,
                "skipped_legal_hold": record.skipped_legal_hold,
                "dry_run": record.dry_run,
            }
        )
        async with self._tenant_connection(org_id=record.org_id) as conn:
            await conn.execute(
                """
                INSERT INTO runtime_deletion_evidence (
                    id, org_id, user_id, request_type, reason,
                    conversations_archived, messages_tombstoned,
                    runs_cancelled, events_retained, created_at
                ) VALUES (
                    %(id)s, %(org_id)s, '__retention_sweeper__',
                    'retention_sweep', %(reason)s,
                    0, %(tombstoned)s, %(deleted)s, %(skipped)s, %(created_at)s
                )
                """,
                {
                    "id": record.id,
                    "org_id": record.org_id,
                    "reason": reason_json,
                    "tombstoned": record.tombstoned,
                    "deleted": record.deleted,
                    "skipped": record.skipped_legal_hold,
                    "created_at": record.created_at,
                },
            )

    @classmethod
    def _retention_policy(cls, row: dict[str, object]) -> RetentionPolicyRecord:
        return RetentionPolicyRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            scope=RetentionScope(str(row["scope"])),
            resource_id=(
                str(row["resource_id"]) if row.get("resource_id") is not None else None
            ),
            kind=RetentionKind(str(row["kind"])),
            ttl_seconds=int(row["ttl_seconds"]),
            created_by_user_id=(
                str(row["created_by_user_id"])
                if row.get("created_by_user_id") is not None
                else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @classmethod
    def _budget_record(cls, row: dict[str, object]) -> BudgetRecord:
        return BudgetRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            user_id=(str(row["user_id"]) if row.get("user_id") is not None else None),
            scope=BudgetScope(str(row["scope"])),
            period=BudgetPeriod(str(row["period"])),
            enforcement=BudgetEnforcement(str(row["enforcement"])),
            limit_micro_usd=(
                int(row["limit_micro_usd"])
                if row.get("limit_micro_usd") is not None
                else None
            ),
            limit_tokens=(
                int(row["limit_tokens"])
                if row.get("limit_tokens") is not None
                else None
            ),
            status=BudgetStatus(str(row["status"])),
            created_at=cls._coerce_datetime(row["created_at"]),
            updated_at=cls._coerce_datetime(row["updated_at"]),
            created_by_user_id=str(row["created_by_user_id"]),
        )

    @classmethod
    def _budget_with_state(cls, row: dict[str, object]) -> BudgetWithState:
        budget = BudgetRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            user_id=(str(row["user_id"]) if row.get("user_id") is not None else None),
            scope=BudgetScope(str(row["scope"])),
            period=BudgetPeriod(str(row["period"])),
            enforcement=BudgetEnforcement(str(row["enforcement"])),
            limit_micro_usd=(
                int(row["limit_micro_usd"])
                if row.get("limit_micro_usd") is not None
                else None
            ),
            limit_tokens=(
                int(row["limit_tokens"])
                if row.get("limit_tokens") is not None
                else None
            ),
            status=BudgetStatus(str(row["status"])),
            created_at=cls._coerce_datetime(row["created_at"]),
            updated_at=cls._coerce_datetime(row["updated_at"]),
            created_by_user_id=str(row["created_by_user_id"]),
        )
        period_start = row["period_start"]
        period_end = row["period_end"]
        # Inflate spend by active reservations so the enforcer sees the
        # right headroom in one read.
        current_micro = int(row.get("current_spend_micro_usd") or 0) + int(
            row.get("reserved_micro") or 0
        )
        current_tokens = int(row.get("current_spend_tokens") or 0) + int(
            row.get("reserved_tokens") or 0
        )
        # Synthesize a state row when none exists but reservations do —
        # the enforcer can't distinguish "no state" from "zero spend"
        # without seeing the reservations.
        has_state = (
            bool(row.get("has_state")) or current_micro > 0 or current_tokens > 0
        )
        state: BudgetStateRecord | None = None
        if has_state:
            state = BudgetStateRecord(
                budget_id=budget.id,
                period_start=period_start,
                period_end=period_end,
                current_spend_micro_usd=current_micro,
                current_spend_tokens=current_tokens,
                row_version=int(row.get("row_version") or 1),
                last_charged_run_id=(
                    str(row["last_charged_run_id"])
                    if row.get("last_charged_run_id") is not None
                    else None
                ),
                updated_at=cls._coerce_datetime(row["state_updated_at"]),
            )
        return BudgetWithState(budget=budget, state=state)

    @classmethod
    def _run_usage_record(cls, row: dict[str, object]) -> RuntimeRunUsageRecord:
        return RuntimeRunUsageRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            conversation_id=str(row["conversation_id"]),
            run_id=str(row["run_id"]),
            assistant_id=(
                str(row["assistant_id"])
                if row.get("assistant_id") is not None
                else None
            ),
            model_provider=str(row["model_provider"]),
            model_name=str(row["model_name"]),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            chunk_count=int(row.get("chunk_count") or 0),
            first_token_ms=(
                int(row["first_token_ms"])
                if row.get("first_token_ms") is not None
                else None
            ),
            duration_ms=int(row.get("duration_ms") or 0),
            started_at=cls._coerce_datetime(row["started_at"]),
            completed_at=cls._coerce_datetime(row["completed_at"]),
            status=str(row["status"]),
            schema_version=int(row.get("schema_version") or 1),
            retention_until=(
                cls._coerce_datetime(row["retention_until"])
                if row.get("retention_until") is not None
                else None
            ),
            pii_purged_at=(
                cls._coerce_datetime(row["pii_purged_at"])
                if row.get("pii_purged_at") is not None
                else None
            ),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            pricing_id=(
                str(row["pricing_id"]) if row.get("pricing_id") is not None else None
            ),
            pricing_version=(
                str(row["pricing_version"])
                if row.get("pricing_version") is not None
                else None
            ),
            created_at=cls._coerce_datetime(row["created_at"]),
        )

    @classmethod
    def _model_call_record(cls, row: dict[str, object]) -> RuntimeModelCallUsageRecord:
        return RuntimeModelCallUsageRecord(
            id=str(row["id"]),
            org_id=str(row["org_id"]),
            run_id=str(row["run_id"]),
            conversation_id=str(row["conversation_id"]),
            parent_event_id=(
                str(row["parent_event_id"])
                if row.get("parent_event_id") is not None
                else None
            ),
            trace_id=str(row["trace_id"]),
            task_id=(str(row["task_id"]) if row.get("task_id") is not None else None),
            subagent_id=(
                str(row["subagent_id"]) if row.get("subagent_id") is not None else None
            ),
            model_provider=str(row["model_provider"]),
            model_name=str(row["model_name"]),
            connector_slug=(
                str(row["connector_slug"])
                if row.get("connector_slug") is not None
                else None
            ),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            duration_ms=int(row.get("duration_ms") or 0),
            schema_version=int(row.get("schema_version") or 1),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            pricing_id=(
                str(row["pricing_id"]) if row.get("pricing_id") is not None else None
            ),
            pricing_version=(
                str(row["pricing_version"])
                if row.get("pricing_version") is not None
                else None
            ),
            created_at=cls._coerce_datetime(row["created_at"]),
        )

    @classmethod
    def _compression_event_record(
        cls, row: dict[str, object]
    ) -> CompressionEventRecord:
        return CompressionEventRecord(
            compression_event_id=str(row["id"]),
            run_id=str(row["run_id"]),
            org_id=str(row["org_id"]),
            before_tokens=int(row.get("before_tokens") or 0),
            after_tokens=int(row.get("after_tokens") or 0),
            strategy=str(row["strategy"]),
            payload_refs=dict(row.get("payload_refs_json") or {}),
            trace_id=(
                str(row["trace_id"]) if row.get("trace_id") is not None else None
            ),
            created_at=cls._coerce_datetime(row["created_at"]),
        )

    @classmethod
    def _pricing_record(cls, row: dict[str, object]) -> ModelPricingRecord:
        return ModelPricingRecord(
            id=str(row["id"]),
            provider=str(row["provider"]),
            model_name=str(row["model_name"]),
            region=str(row.get("region") or "global"),
            effective_from=cls._coerce_datetime(row["effective_from"]),
            effective_until=(
                cls._coerce_datetime(row["effective_until"])
                if row.get("effective_until") is not None
                else None
            ),
            input_per_1m_micro_usd=int(row["input_per_1m_micro_usd"]),
            output_per_1m_micro_usd=int(row["output_per_1m_micro_usd"]),
            cached_input_per_1m_micro_usd=(
                int(row["cached_input_per_1m_micro_usd"])
                if row.get("cached_input_per_1m_micro_usd") is not None
                else None
            ),
            context_window_tokens=(
                int(row["context_window_tokens"])
                if row.get("context_window_tokens") is not None
                else None
            ),
            pricing_source=str(row.get("pricing_source") or "yaml-seed"),
            pricing_version=str(row["pricing_version"]),
            created_at=cls._coerce_datetime(row["created_at"]),
        )

    @classmethod
    def _user_daily_row(cls, row: dict[str, object]) -> UsageDailyUserRow:
        return UsageDailyUserRow(
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            day=cls._coerce_date_to_datetime(row["day"]),
            model_provider=str(row["model_provider"]),
            model_name=str(row["model_name"]),
            runs_count=int(row.get("runs_count") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            refreshed_at=cls._coerce_datetime(row["refreshed_at"]),
        )

    @classmethod
    def _org_daily_row(cls, row: dict[str, object]) -> UsageDailyOrgRow:
        return UsageDailyOrgRow(
            org_id=str(row["org_id"]),
            day=cls._coerce_date_to_datetime(row["day"]),
            model_provider=str(row["model_provider"]),
            model_name=str(row["model_name"]),
            runs_count=int(row.get("runs_count") or 0),
            distinct_users=int(row.get("distinct_users") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            refreshed_at=cls._coerce_datetime(row["refreshed_at"]),
        )

    @classmethod
    def _connector_daily_row(cls, row: dict[str, object]) -> UsageDailyConnectorRow:
        return UsageDailyConnectorRow(
            org_id=str(row["org_id"]),
            day=cls._coerce_date_to_datetime(row["day"]),
            connector_slug=str(row.get("connector_slug") or ""),
            model_name=str(row.get("model_name") or ""),
            runs_count=int(row.get("runs_count") or 0),
            distinct_users=int(row.get("distinct_users") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            refreshed_at=cls._coerce_datetime(row["refreshed_at"]),
        )

    @classmethod
    def _subagent_daily_row(cls, row: dict[str, object]) -> UsageDailySubagentRow:
        return UsageDailySubagentRow(
            org_id=str(row["org_id"]),
            day=cls._coerce_date_to_datetime(row["day"]),
            subagent_slug=str(row.get("subagent_slug") or ""),
            model_provider=str(row.get("model_provider") or ""),
            model_name=str(row.get("model_name") or ""),
            call_count=int(row.get("call_count") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            cache_creation_input_tokens=int(
                row.get("cache_creation_input_tokens") or 0
            ),
            reasoning_tokens=int(row.get("reasoning_tokens") or 0),
            audio_input_tokens=int(row.get("audio_input_tokens") or 0),
            audio_output_tokens=int(row.get("audio_output_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            refreshed_at=cls._coerce_datetime(row["refreshed_at"]),
        )

    @classmethod
    def _purpose_daily_row(cls, row: dict[str, object]) -> UsageDailyPurposeRow:
        return UsageDailyPurposeRow(
            org_id=str(row["org_id"]),
            day=cls._coerce_date_to_datetime(row["day"]),
            purpose=str(row.get("purpose") or "main"),
            model_provider=str(row.get("model_provider") or ""),
            model_name=str(row.get("model_name") or ""),
            call_count=int(row.get("call_count") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cached_input_tokens=int(row.get("cached_input_tokens") or 0),
            cache_creation_input_tokens=int(
                row.get("cache_creation_input_tokens") or 0
            ),
            reasoning_tokens=int(row.get("reasoning_tokens") or 0),
            audio_input_tokens=int(row.get("audio_input_tokens") or 0),
            audio_output_tokens=int(row.get("audio_output_tokens") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
            cost_micro_usd=(
                int(row["cost_micro_usd"])
                if row.get("cost_micro_usd") is not None
                else None
            ),
            refreshed_at=cls._coerce_datetime(row["refreshed_at"]),
        )

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _coerce_date_to_datetime(value: object) -> datetime:
        from datetime import date as _date

        if isinstance(value, datetime):
            return value
        if isinstance(value, _date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return datetime.fromisoformat(str(value))

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with the next per-run sequence number.

        Lock-free: a concurrent appender that races to the same
        ``sequence_no`` loses with ``UniqueViolation`` and we retry up to
        :data:`_AppendEventRetry.MAX_ATTEMPTS` times. The common case is
        one INSERT with no lock acquire/release per event; the rare
        cancel-mid-stream race costs one retry on the loser.

        Consolidated writes: ``agent_runs.latest_sequence_no`` advances
        inside the same transaction as the event INSERT. A monotonic guard
        prevents rewinds even under retry. ``RuntimeEventProducer``
        therefore skips its separate ``set_run_latest_sequence`` call.

        NOTIFY: fires ``NOTIFY <channel>, '<run_id>:<seq>'`` when
        ``self._notify_after_append`` is True. The NOTIFY rides inside
        the same transaction as the INSERT, so a rolled-back attempt
        produces no NOTIFY.
        """

        last_exc: psycopg_errors.UniqueViolation | None = None
        for attempt in range(_AppendEventRetry.MAX_ATTEMPTS):
            try:
                return await self._append_event_once(event)
            except psycopg_errors.UniqueViolation as exc:
                if not self._is_event_sequence_conflict(exc):
                    raise
                last_exc = exc
                # Don't sleep after the final attempt — caller is about
                # to see :class:`RuntimeEventSequenceConflict` anyway.
                if attempt + 1 < _AppendEventRetry.MAX_ATTEMPTS:
                    await asyncio.sleep(self._retry_backoff(attempt))
        assert last_exc is not None  # loop only exits via return or exception
        raise RuntimeEventSequenceConflict(
            run_id=event.run_id,
            attempts=_AppendEventRetry.MAX_ATTEMPTS,
        ) from last_exc

    async def _append_event_once(
        self,
        event: RuntimeEventDraft,
    ) -> RuntimeEventEnvelope:
        """One attempt at appending an event. See :meth:`append_event`."""

        async with self._tenant_connection(org_id=event.org_id) as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT org_id FROM agent_runs WHERE id = %s",
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
                # Encrypt payload_json_redacted +
                # metadata_json_redacted with AAD bound to (table,
                # column, org_id). The presentation_json is NOT encrypted
                # because it's the projector's pre-rendered card the
                # frontend reads on every event — encrypting it would
                # add a KMS roundtrip per stream tick.
                event_org_id = str(run[_Columns.ORG_ID])
                version = self._codec.write_version
                payload_encrypted = self._codec.encrypt_jsonb(
                    envelope.payload,
                    table=_Tables.RUNTIME_EVENTS,
                    column=_Columns.PAYLOAD_JSON_REDACTED,
                    org_id=event_org_id,
                )
                metadata_encrypted = self._codec.encrypt_jsonb(
                    envelope.metadata,
                    table=_Tables.RUNTIME_EVENTS,
                    column=_Columns.METADATA_JSON_REDACTED,
                    org_id=event_org_id,
                )
                retention_until = await self._resolve_retention_until(
                    conn,
                    org_id=event_org_id,
                    kind=RetentionKind.EVENTS,
                    conversation_id=envelope.conversation_id,
                    created_at=envelope.created_at,
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_events (
                        id, run_id, conversation_id, org_id, sequence_no, event_protocol_version,
                        source, event_type, parent_event_id, span_id, parent_span_id,
                        parent_task_id, task_id, subagent_id, display_title, summary, status,
                        trace_id, payload_json_redacted, metadata_json_redacted, visibility,
                        redaction_state, activity_kind, presentation_json, created_at,
                        encryption_version, retention_until
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        envelope.event_id,
                        envelope.run_id,
                        envelope.conversation_id,
                        event_org_id,
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
                        Jsonb(payload_encrypted),
                        Jsonb(metadata_encrypted),
                        envelope.visibility.value,
                        envelope.redaction_state.value,
                        envelope.activity_kind,
                        Jsonb(envelope.presentation),
                        envelope.created_at,
                        version,
                        retention_until,
                    ),
                )
                # Fold the cursor advance into the same transaction.
                # Monotonic guard mirrors set_run_latest_sequence so a peer
                # that has already advanced the cursor between our
                # MAX(sequence_no) read and our INSERT cannot rewind it.
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET latest_sequence_no = %s
                     WHERE id = %s
                       AND (
                           latest_sequence_no IS NULL
                           OR latest_sequence_no < %s
                       )
                    """,
                    (
                        envelope.sequence_no,
                        envelope.run_id,
                        envelope.sequence_no,
                    ),
                )
                if self._notify_after_append:
                    # Fire NOTIFY inside the same transaction; if the
                    # INSERT rolls back the NOTIFY is silently discarded
                    # by Postgres. Payload is ``<run_id>:<sequence_no>``.
                    #
                    # Must use pg_notify(): the bare ``NOTIFY chan, %s``
                    # form takes no bind parameters, so psycopg's extended
                    # protocol sends ``NOTIFY chan, $1`` and Postgres rejects
                    # it ("syntax error at or near \"$1\""). pg_notify() is
                    # the function form that accepts bound arguments and keeps
                    # the channel parameterised too.
                    await conn.execute(
                        "SELECT pg_notify(%s, %s)",
                        (
                            self._notify_channel,
                            f"{envelope.run_id}:{envelope.sequence_no}",
                        ),
                    )
        return envelope

    @staticmethod
    def _is_event_sequence_conflict(exc: psycopg_errors.UniqueViolation) -> bool:
        """Return True when ``exc`` came from the runtime_events sequence index.

        Discriminates the retry-on-race path from any other unique
        violation that might surface through the same transaction (no
        other writes happen in :meth:`_append_event_once`, but we match
        on the index name explicitly so a future addition can't silently
        widen the retry net).
        """

        constraint = getattr(exc.diag, "constraint_name", None)
        return constraint == _AppendEventRetry.SEQUENCE_INDEX

    @staticmethod
    def _retry_backoff(attempt: int) -> float:
        """Jittered exponential backoff for the lock-free append retry loop.

        The race window is microseconds; the sleep is mostly to let the
        peer commit before we re-read ``MAX(sequence_no)``. Capped at
        :data:`_AppendEventRetry.MAX_DELAY_SECONDS` so a brief contention
        spike can't stretch a single append into a long tail.
        """

        base = _AppendEventRetry.BASE_DELAY_SECONDS * (2**attempt)
        delay = min(base, _AppendEventRetry.MAX_DELAY_SECONDS)
        # Full jitter: U(0, delay). Keeps simultaneous losers from
        # re-colliding on the next attempt.
        return random.random() * delay

    async def append_events_batch(
        self, events: Sequence[RuntimeEventDraft]
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append N events under one atomic transaction.

        Used by the worker's ``DeltaCoalescer`` to flush a batch of
        ``MODEL_DELTA`` chunks. All events must share the same ``run_id``
        (asserted) — coalescing across runs would break per-run sequence
        allocation. Returns envelopes in input order with contiguous
        sequence numbers.

        One transaction holds:
          * ``SELECT … FOR UPDATE`` on ``agent_runs`` (row lock — keeps
            contiguous-range allocation atomic across concurrent batches).
          * One ``SELECT MAX(sequence_no) + 1`` to allocate the starting
            sequence number; subsequent envelopes claim ``start, start+1,
            …, start+N-1`` without re-querying.
          * One multi-row ``INSERT`` of N events (one round-trip to
            Postgres regardless of batch size).
          * One final ``UPDATE agent_runs.latest_sequence_no`` with the
            monotonic guard (no rewinds under retry).

        Empty input returns ``()`` without opening a connection. Per-event
        encryption + envelope building still runs in Python (the codec
        cannot be moved into SQL).
        """

        if not events:
            return ()
        run_ids = {event.run_id for event in events}
        if len(run_ids) > 1:
            raise ValueError(
                "append_events_batch requires all events to share one run_id; "
                f"saw {len(run_ids)}."
            )
        first = events[0]
        async with self._tenant_connection(org_id=first.org_id) as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT org_id FROM agent_runs WHERE id = %s FOR UPDATE",
                    (first.run_id,),
                )
                run = await cur.fetchone()
                cur = await conn.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                    FROM runtime_events
                    WHERE run_id = %s
                    """,
                    (first.run_id,),
                )
                start_row = await cur.fetchone()
                start_sequence = int(start_row[_Columns.NEXT_SEQUENCE])
                event_org_id = str(run[_Columns.ORG_ID])
                version = self._codec.write_version
                # Resolve retention TTL once per batch — all events share
                # the same (org, kind, conversation). We fetch ttl_seconds
                # directly so each row can compute its own expiry from its
                # own created_at without another policy query.
                #
                # Sample the policy against a single "now" timestamp: the
                # draft carries no ``created_at`` (that field lives on the
                # built envelope, matching the single-append path which
                # resolves retention against ``envelope.created_at``). Each
                # envelope's default_factory stamps ~now() microseconds apart,
                # so one shared sample is the correct basis for the delta,
                # which is then re-anchored to each row's own created_at below.
                _sample_created_at = datetime.now(timezone.utc)
                _retention_sample = await self._resolve_retention_until(
                    conn,
                    org_id=event_org_id,
                    kind=RetentionKind.EVENTS,
                    conversation_id=first.conversation_id,
                    created_at=_sample_created_at,
                )
                _event_ttl_seconds: int | None = None
                if _retention_sample is not None:
                    _event_ttl_delta = _retention_sample - _sample_created_at
                    _event_ttl_seconds = int(_event_ttl_delta.total_seconds())

                envelopes: list[RuntimeEventEnvelope] = []
                rows: list[tuple[object, ...]] = []
                for offset, event in enumerate(events):
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
                        sequence_no=start_sequence + offset,
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
                    payload_encrypted = self._codec.encrypt_jsonb(
                        envelope.payload,
                        table=_Tables.RUNTIME_EVENTS,
                        column=_Columns.PAYLOAD_JSON_REDACTED,
                        org_id=event_org_id,
                    )
                    metadata_encrypted = self._codec.encrypt_jsonb(
                        envelope.metadata,
                        table=_Tables.RUNTIME_EVENTS,
                        column=_Columns.METADATA_JSON_REDACTED,
                        org_id=event_org_id,
                    )
                    row_retention_until = (
                        envelope.created_at + timedelta(seconds=_event_ttl_seconds)
                        if _event_ttl_seconds is not None
                        else None
                    )
                    envelopes.append(envelope)
                    rows.append(
                        (
                            envelope.event_id,
                            envelope.run_id,
                            envelope.conversation_id,
                            event_org_id,
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
                            Jsonb(payload_encrypted),
                            Jsonb(metadata_encrypted),
                            envelope.visibility.value,
                            envelope.redaction_state.value,
                            envelope.activity_kind,
                            Jsonb(envelope.presentation),
                            envelope.created_at,
                            version,
                            row_retention_until,
                        )
                    )

                # One round-trip multi-row INSERT.
                placeholder_row = "(" + ", ".join(["%s"] * len(rows[0])) + ")"
                values_clause = ", ".join([placeholder_row] * len(rows))
                flat_params = tuple(value for row in rows for value in row)
                await conn.execute(
                    f"""
                    INSERT INTO runtime_events (
                        id, run_id, conversation_id, org_id, sequence_no,
                        event_protocol_version, source, event_type,
                        parent_event_id, span_id, parent_span_id,
                        parent_task_id, task_id, subagent_id,
                        display_title, summary, status, trace_id,
                        payload_json_redacted, metadata_json_redacted,
                        visibility, redaction_state, activity_kind,
                        presentation_json, created_at, encryption_version,
                        retention_until
                    )
                    VALUES {values_clause}
                    """,
                    flat_params,
                )
                last_sequence = envelopes[-1].sequence_no
                await conn.execute(
                    """
                    UPDATE agent_runs
                       SET latest_sequence_no = %s
                     WHERE id = %s
                       AND (
                           latest_sequence_no IS NULL
                           OR latest_sequence_no < %s
                       )
                    """,
                    (last_sequence, first.run_id, last_sequence),
                )
                if self._notify_after_append:
                    # One NOTIFY per batch — the payload carries the
                    # *highest* sequence number; SSE adapters always
                    # ``replay_events(after_sequence=N)`` so they pick up
                    # everything in the batch in one round-trip. pg_notify()
                    # (not bare ``NOTIFY chan, %s``) so the payload can be a
                    # bound parameter — see the single-append site above.
                    await conn.execute(
                        "SELECT pg_notify(%s, %s)",
                        (self._notify_channel, f"{first.run_id}:{last_sequence}"),
                    )
        return tuple(envelopes)

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

        async with self._tenant_connection(org_id=org_id) as conn:
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

        async with self._tenant_connection(role=self._role) as conn:
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

        async with self._role_connection("worker") as conn:
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
        async with self._tenant_connection(org_id=org_id) as conn:
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
        async with self._role_connection("worker") as conn:
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
            enabled_connectors=cls._coerce_enabled_connectors(
                row.get(_Columns.ENABLED_CONNECTORS)
            ),
            connectors_updated_at=row.get(_Columns.CONNECTORS_UPDATED_AT),
            # Lifecycle columns — ``row.get`` keeps the hydrator
            # forward-compatible with rows from databases pre-migration
            # 0020 (returns None when the column is absent).
            deleted_at=row.get(_Columns.DELETED_AT),
            folder=row.get(_Columns.FOLDER),
            parent_conversation_id=row.get(_Columns.PARENT_CONVERSATION_ID),
            # ``row.get`` keeps the hydrator forward-compatible with rows
            # from databases pre-migration 0034 (returns False when absent).
            pinned=bool(row.get(_Columns.PINNED, False)),
            forked_from_share_id=row.get(_Columns.FORKED_FROM_SHARE_ID),
        )

    @staticmethod
    def _coerce_enabled_connectors(
        value: object,
    ) -> dict[str, tuple[str, ...] | None]:
        """Decode the JSONB column into the runtime shape (None = paused)."""

        if value is None:
            return {}
        if not isinstance(value, dict):
            return {}
        scopes: dict[str, tuple[str, ...] | None] = {}
        for connector_id, raw in value.items():
            if raw is None:
                scopes[str(connector_id)] = None
                continue
            if isinstance(raw, list | tuple):
                scopes[str(connector_id)] = tuple(str(scope) for scope in raw)
        return scopes

    def _message_record(self, row: dict[str, object]) -> MessageRecord:
        # Decrypt content_text / content_json / metadata_json
        # using the row's encryption_version. Unencrypted rows stay v0
        # (plaintext) and pass through unchanged.
        version = int(row.get(_Columns.ENCRYPTION_VERSION, 0) or 0)
        org_id = str(row[_Columns.ORG_ID])
        content_text = self._codec.decrypt_text(
            row[_Columns.CONTENT_TEXT],
            encryption_version=version,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_TEXT,
            org_id=org_id,
        )
        content_json = self._codec.decrypt_jsonb(
            row[_Columns.CONTENT_JSON],
            encryption_version=version,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_JSON,
            org_id=org_id,
        )
        metadata_json = self._codec.decrypt_jsonb(
            row[_Columns.METADATA_JSON],
            encryption_version=version,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.METADATA_JSON,
            org_id=org_id,
        )
        return MessageRecord(
            message_id=row[_Columns.ID],
            conversation_id=row[_Columns.CONVERSATION_ID],
            org_id=org_id,
            run_id=row[_Columns.RUN_ID],
            role=row[_Columns.ROLE],
            content_text=content_text,
            content_format=row[_Columns.CONTENT_FORMAT],
            content=tuple(dict(part) for part in content_json),
            attachments=tuple(
                dict(attachment) for attachment in row[_Columns.ATTACHMENTS_JSON]
            ),
            quote=dict(row[_Columns.QUOTE_JSON])
            if row[_Columns.QUOTE_JSON] is not None
            else None,
            metadata=dict(metadata_json),
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

    def _event_envelope(self, row: dict[str, object]) -> RuntimeEventEnvelope:
        stored_activity = row.get(_Columns.ACTIVITY_KIND)
        if stored_activity is None:
            stored_activity = RuntimeEventPresentationProjector.activity_kind_for(
                event_type=RuntimeApiEventType(row[_Columns.EVENT_TYPE]),
                source=StreamEventSource(row[_Columns.SOURCE]),
            )

        # Decrypt JSONB envelopes for the encrypted columns.
        version = int(row.get(_Columns.ENCRYPTION_VERSION, 0) or 0)
        org_id = str(row[_Columns.ORG_ID])
        payload_json = self._codec.decrypt_jsonb(
            row[_Columns.PAYLOAD_JSON_REDACTED],
            encryption_version=version,
            table=_Tables.RUNTIME_EVENTS,
            column=_Columns.PAYLOAD_JSON_REDACTED,
            org_id=org_id,
        )
        metadata_json = self._codec.decrypt_jsonb(
            row[_Columns.METADATA_JSON_REDACTED],
            encryption_version=version,
            table=_Tables.RUNTIME_EVENTS,
            column=_Columns.METADATA_JSON_REDACTED,
            org_id=org_id,
        )

        stored_presentation = row.get(_Columns.PRESENTATION_JSON)
        if stored_presentation is not None:
            presentation = dict(stored_presentation)
        else:
            presentation = RuntimeEventPresentationProjector.presentation_metadata(
                dict(metadata_json or {})
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
            payload=dict(payload_json or {}),
            metadata=dict(metadata_json or {}),
            created_at=row[_Columns.CREATED_AT],
        )

    async def _resolve_retention_until(
        self,
        conn: psycopg.AsyncConnection,
        *,
        org_id: str,
        kind: RetentionKind,
        conversation_id: str | None,
        created_at: datetime,
    ) -> datetime | None:
        """Resolve the effective TTL for (org, kind, conversation) and return the expiry.

        Queries the policies table within the current connection (no extra
        round-trip when called inside a transaction), builds the resolver,
        and returns ``created_at + ttl_seconds`` or ``None`` when no policy
        and no deployment default applies.
        """
        cur = await conn.execute(
            """
            SELECT id, org_id, scope, resource_id, kind, ttl_seconds,
                   created_by_user_id, created_at, updated_at
              FROM retention_policies
             WHERE org_id = %s AND kind = %s
            """,
            (org_id, kind.value),
        )
        rows = await cur.fetchall()
        policies = tuple(self._retention_policy(r) for r in rows)
        resolver = RetentionPolicyResolver(
            org_id=org_id,
            policies=policies,
            deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
        )
        resolved = resolver.resolve(kind=kind, conversation_id=conversation_id)
        if resolved.ttl_seconds is None:
            return None
        return created_at + timedelta(seconds=resolved.ttl_seconds)

    async def _insert_message(
        self, conn: psycopg.AsyncConnection, message: MessageRecord
    ) -> None:
        # Encrypt content_text / content_json / metadata_json
        # per the codec's active mode. ``encryption_version`` reflects the
        # mode used here so reads of THIS row know whether to decrypt.
        version = self._codec.write_version
        content_text = self._codec.encrypt_text(
            message.content_text,
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_TEXT,
            org_id=message.org_id,
        )
        content_json = self._codec.encrypt_jsonb(
            list(message.content),
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.CONTENT_JSON,
            org_id=message.org_id,
        )
        metadata_json = self._codec.encrypt_jsonb(
            dict(message.metadata),
            table=_Tables.AGENT_MESSAGES,
            column=_Columns.METADATA_JSON,
            org_id=message.org_id,
        )
        retention_until = await self._resolve_retention_until(
            conn,
            org_id=message.org_id,
            kind=RetentionKind.MESSAGES,
            conversation_id=message.conversation_id,
            created_at=message.created_at,
        )
        await conn.execute(
            """
            INSERT INTO agent_messages (
                id, conversation_id, org_id, run_id, role, content_text, content_format,
                content_json, attachments_json, quote_json, metadata_json,
                parent_message_id, source_message_id, branch_id, token_count, trace_id,
                status, created_at, edited_at, deleted_at, encryption_version,
                retention_until
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                message.message_id,
                message.conversation_id,
                message.org_id,
                message.run_id,
                message.role.value,
                content_text,
                message.content_format,
                Jsonb(content_json),
                Jsonb(message.attachments),
                Jsonb(message.quote) if message.quote is not None else None,
                Jsonb(metadata_json),
                message.parent_message_id,
                message.source_message_id,
                message.branch_id,
                message.token_count,
                message.trace_id,
                message.status.value,
                message.created_at,
                message.edited_at,
                message.deleted_at,
                version,
                retention_until,
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

    # ----- CitationStorePort ------------------------------------------------

    async def insert_many_or_get(
        self, records: Sequence[CitationRecord]
    ) -> Sequence[CitationRecord]:
        """Bulk-insert citation rows. Returns canonical rows in input order.

        Two DB round trips total regardless of batch size:

        1. One multi-VALUES ``INSERT … ON CONFLICT DO NOTHING`` against
           the unique index ``runtime_citations_run_source_uk``.
           Conflicts skip silently.
        2. One ``SELECT … WHERE (run_id, connector, doc_id) IN (…)``
           covering every input key, so existing rows come back alongside
           newly-inserted ones.

        Output preserves input order so the caller's ordinal binding map
        stays consistent.
        """

        if not records:
            return ()

        write_version = self._codec.write_version
        flat_values: list[object] = []
        for record in records:
            title_encrypted = self._codec.encrypt_text(
                record.title,
                table=_Tables.RUNTIME_CITATIONS,
                column=_Columns.TITLE,
                org_id=record.org_id,
            )
            snippet_encrypted = self._codec.encrypt_text(
                record.snippet,
                table=_Tables.RUNTIME_CITATIONS,
                column=_Columns.SNIPPET,
                org_id=record.org_id,
            )
            flat_values.extend(
                (
                    record.citation_id,
                    record.run_id,
                    record.conversation_id,
                    record.org_id,
                    record.ordinal,
                    record.source_connector,
                    record.source_doc_id,
                    record.source_url,
                    title_encrypted,
                    snippet_encrypted,
                    record.freshness_at,
                    record.source_tool_call_id,
                    write_version,
                    record.created_at,
                )
            )

        row_placeholder = "(" + ", ".join(["%s"] * 14) + ")"
        values_clause = ", ".join([row_placeholder] * len(records))
        select_keys_placeholder = ", ".join(["(%s, %s, %s)"] * len(records))
        select_keys_params: list[object] = []
        for record in records:
            select_keys_params.extend(
                (record.run_id, record.source_connector, record.source_doc_id)
            )

        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        f"""
                        INSERT INTO runtime_citations (
                            citation_id, run_id, conversation_id, org_id, ordinal,
                            source_connector, source_doc_id, source_url,
                            title, snippet, freshness_at, source_tool_call_id,
                            encryption_version, created_at
                        )
                        VALUES {values_clause}
                        ON CONFLICT (run_id, source_connector, source_doc_id)
                        DO NOTHING
                        """,
                        flat_values,
                    )
                    await cursor.execute(
                        f"""
                        SELECT *
                        FROM runtime_citations
                        WHERE (run_id, source_connector, source_doc_id) IN ({select_keys_placeholder})
                        """,
                        select_keys_params,
                    )
                    rows = await cursor.fetchall()

        by_key: dict[tuple[str, str, str], dict[str, object]] = {
            (
                str(row["run_id"]),
                str(row["source_connector"]),
                str(row["source_doc_id"]),
            ): row
            for row in rows
        }

        output: list[CitationRecord] = []
        for record in records:
            key = (record.run_id, record.source_connector, record.source_doc_id)
            row = by_key.get(key)
            if row is None:
                # Unreachable in normal operation: every input key was
                # either inserted or pre-existed, and the SELECT covers
                # both. A concurrent DELETE could in theory hide a row
                # between the INSERT and the SELECT — surface as a typed
                # persistence error.
                raise RuntimeApiError(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    envelope=RuntimeErrorEnvelope(
                        code=RuntimeErrorCode.PERSISTENCE_ERROR,
                        safe_message=Messages.Error.SAFE_FALLBACK,
                    ),
                )
            output.append(self._row_to_citation(row))
        return tuple(output)

    async def list_for_run(
        self, *, org_id: str, run_id: str
    ) -> Sequence[CitationRecord]:
        """Return all citations for a run, ordered by ordinal ascending."""
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT *
                    FROM runtime_citations
                    WHERE org_id = %s AND run_id = %s
                    ORDER BY ordinal ASC
                    """,
                    (org_id, run_id),
                )
                rows = await cursor.fetchall()
        return tuple(self._row_to_citation(row) for row in rows)

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> Sequence[CitationRecord]:
        """Return all citations for a conversation, ordered by creation time then ordinal ascending."""
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT *
                    FROM runtime_citations
                    WHERE org_id = %s AND conversation_id = %s
                    ORDER BY created_at ASC, ordinal ASC
                    """,
                    (org_id, conversation_id),
                )
                rows = await cursor.fetchall()
        return tuple(self._row_to_citation(row) for row in rows)

    def _row_to_citation(self, row: dict[str, object]) -> CitationRecord:
        encryption_version = int(row[_Columns.ENCRYPTION_VERSION])
        org_id = str(row[_Columns.ORG_ID])
        title = self._codec.decrypt_text(
            row.get(_Columns.TITLE),  # type: ignore[arg-type]
            encryption_version=encryption_version,
            table=_Tables.RUNTIME_CITATIONS,
            column=_Columns.TITLE,
            org_id=org_id,
        )
        snippet = self._codec.decrypt_text(
            row.get(_Columns.SNIPPET),  # type: ignore[arg-type]
            encryption_version=encryption_version,
            table=_Tables.RUNTIME_CITATIONS,
            column=_Columns.SNIPPET,
            org_id=org_id,
        )
        return CitationRecord(
            citation_id=str(row["citation_id"]),
            run_id=str(row[_Columns.RUN_ID]),
            conversation_id=str(row[_Columns.CONVERSATION_ID]),
            org_id=org_id,
            ordinal=int(row["ordinal"]),
            source_connector=str(row["source_connector"]),
            source_doc_id=str(row["source_doc_id"]),
            source_url=row.get("source_url"),  # type: ignore[arg-type]
            title=title or "",
            snippet=snippet,
            freshness_at=row.get("freshness_at"),  # type: ignore[arg-type]
            source_tool_call_id=row.get("source_tool_call_id"),  # type: ignore[arg-type]
            encryption_version=encryption_version,
            created_at=row[_Columns.CREATED_AT],  # type: ignore[arg-type]
        )
