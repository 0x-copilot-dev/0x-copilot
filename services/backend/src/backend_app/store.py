"""Backend stores used by local development, tests, and production adapters."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from enterprise_audit_chain import AuditChainSigner

from backend_app.contracts import (
    AuditEventRecord,
    DeployAuditEventRecord,
    McpAuthSessionRecord,
    McpOAuthClientConfig,
    McpServerRecord,
    SkillAuditEventRecord,
    SkillRecord,
    TokenEnvelope,
)


class CrossTenantWriteError(Exception):
    """Raised when an upsert is rejected by a cross-tenant ``WHERE`` guard.

    The composite-key row exists for a different ``org_id`` than the one
    attempting the write. The caller's row is left untouched. The public
    error message is intentionally generic to avoid leaking the existing
    org's identity to the writer.
    """

    def __init__(self, *, table: str) -> None:
        super().__init__(f"cross-tenant write rejected on {table}")
        self.table = table


class _BackendPoolEnv:
    """Env-var keys + defaults for backend DB pool tuning (C4)."""

    POOL_MIN_SIZE = "BACKEND_DB_POOL_MIN_SIZE"
    POOL_MAX_SIZE = "BACKEND_DB_POOL_MAX_SIZE"
    POOL_ACQUIRE_TIMEOUT_SECONDS = "BACKEND_DB_POOL_ACQUIRE_TIMEOUT_SECONDS"
    STATEMENT_TIMEOUT_MS = "BACKEND_DB_STATEMENT_TIMEOUT_MS"
    LOCK_TIMEOUT_MS = "BACKEND_DB_LOCK_TIMEOUT_MS"
    IDLE_IN_TXN_TIMEOUT_MS = "BACKEND_DB_IDLE_IN_TXN_TIMEOUT_MS"

    DEFAULT_POOL_MIN_SIZE = 5
    DEFAULT_POOL_MAX_SIZE = 50
    DEFAULT_POOL_ACQUIRE_TIMEOUT_SECONDS = 5.0
    DEFAULT_STATEMENT_TIMEOUT_MS = 10000
    DEFAULT_LOCK_TIMEOUT_MS = 3000
    DEFAULT_IDLE_IN_TXN_TIMEOUT_MS = 30000

    SERVICE_NAME = "backend"

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
    def build_options(cls, *, role: str) -> str:
        statement_timeout_ms = cls.env_int(
            cls.STATEMENT_TIMEOUT_MS, cls.DEFAULT_STATEMENT_TIMEOUT_MS
        )
        lock_timeout_ms = cls.env_int(cls.LOCK_TIMEOUT_MS, cls.DEFAULT_LOCK_TIMEOUT_MS)
        idle_in_txn_ms = cls.env_int(
            cls.IDLE_IN_TXN_TIMEOUT_MS, cls.DEFAULT_IDLE_IN_TXN_TIMEOUT_MS
        )
        return (
            f"-c statement_timeout={statement_timeout_ms} "
            f"-c lock_timeout={lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={idle_in_txn_ms} "
            f"-c application_name={cls.SERVICE_NAME}:{role}"
        )


class _AuditChain:
    """Shared chain-state holder for in-memory audit stores.

    Tracks the most recent signature per (table, org) so each new row is
    signed against its predecessor without needing an O(N) scan of the list.
    """

    def __init__(self, signer: AuditChainSigner | None = None) -> None:
        self._signer = signer or AuditChainSigner.from_env(
            environment_env_var="BACKEND_ENVIRONMENT"
        )
        self._heads_by_org: dict[str, bytes] = {}
        self._counts_by_org: dict[str, int] = {}

    @property
    def signer(self) -> AuditChainSigner:
        return self._signer

    def next(
        self, *, org_id: str, payload: dict[str, Any]
    ) -> tuple[int, bytes | None, bytes, int]:
        """Return ``(seq, prev_hash, signature, key_version)`` for a new row."""

        prev = self._heads_by_org.get(org_id)
        sig = self._signer.sign(prev_hash=prev, payload=payload)
        seq = self._counts_by_org.get(org_id, 0) + 1
        self._counts_by_org[org_id] = seq
        self._heads_by_org[org_id] = sig.signature
        return seq, sig.prev_hash, sig.signature, sig.key_version


class PostgresConnectionPool:
    """Shared connection pool for all Postgres-backed stores.

    Uses the singleton pattern so multiple store instances share the same pool.
    Call ``close_shared`` during application shutdown.
    """

    _instance: PostgresConnectionPool | None = None

    def __init__(
        self,
        database_url: str,
        *,
        role: str = "api",
        min_size: int | None = None,
        max_size: int | None = None,
        acquire_timeout_seconds: float | None = None,
    ) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        resolved_min = (
            min_size
            if min_size is not None
            else _BackendPoolEnv.env_int(
                _BackendPoolEnv.POOL_MIN_SIZE, _BackendPoolEnv.DEFAULT_POOL_MIN_SIZE
            )
        )
        resolved_max = (
            max_size
            if max_size is not None
            else _BackendPoolEnv.env_int(
                _BackendPoolEnv.POOL_MAX_SIZE, _BackendPoolEnv.DEFAULT_POOL_MAX_SIZE
            )
        )
        resolved_timeout = (
            acquire_timeout_seconds
            if acquire_timeout_seconds is not None
            else _BackendPoolEnv.env_float(
                _BackendPoolEnv.POOL_ACQUIRE_TIMEOUT_SECONDS,
                _BackendPoolEnv.DEFAULT_POOL_ACQUIRE_TIMEOUT_SECONDS,
            )
        )
        from backend_app.db.pool_metrics import PoolMetrics

        self._role = role
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=resolved_min,
            max_size=resolved_max,
            timeout=resolved_timeout,
            kwargs={
                "row_factory": dict_row,
                "options": _BackendPoolEnv.build_options(role=role),
            },
        )
        self._metrics = PoolMetrics(service=_BackendPoolEnv.SERVICE_NAME, role=role)
        self._metrics.bind_pool(self._pool)

    @property
    def metrics(self) -> Any:
        return self._metrics

    def connection(self) -> Any:
        return self._pool.connection()

    def close(self) -> None:
        self._pool.close()

    @classmethod
    def shared(cls, database_url: str, **kwargs: Any) -> PostgresConnectionPool:
        if cls._instance is None:
            cls._instance = cls(database_url, **kwargs)
        return cls._instance

    @classmethod
    def close_shared(cls) -> None:
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None


@dataclass
class InMemoryMcpStore:
    servers: dict[str, McpServerRecord] = field(default_factory=dict)
    auth_sessions: dict[str, McpAuthSessionRecord] = field(default_factory=dict)
    tokens_by_server: dict[str, TokenEnvelope] = field(default_factory=dict)
    audit_events: list[AuditEventRecord] = field(default_factory=list)
    _chain: _AuditChain = field(default_factory=_AuditChain, init=False, repr=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """No-op transaction so the service layer can compose uniformly."""

        yield None

    def create_server(
        self, record: McpServerRecord, *, conn: Any | None = None
    ) -> McpServerRecord:
        del conn
        self.servers[record.server_id] = record
        return record

    def update_server(
        self, record: McpServerRecord, *, conn: Any | None = None
    ) -> McpServerRecord:
        del conn
        self.servers[record.server_id] = record
        return record

    def get_server(self, *, org_id: str, server_id: str) -> McpServerRecord | None:
        record = self.servers.get(server_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def list_servers(self, *, org_id: str, user_id: str) -> tuple[McpServerRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.servers.values()
                    if record.org_id == org_id and record.user_id == user_id
                ),
                key=lambda record: record.created_at,
            )
        )

    def delete_server(
        self, *, org_id: str, server_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        record = self.get_server(org_id=org_id, server_id=server_id)
        if record is None:
            return False
        self.servers.pop(server_id, None)
        self.tokens_by_server.pop(server_id, None)
        return True

    def create_auth_session(self, record: McpAuthSessionRecord) -> McpAuthSessionRecord:
        self.auth_sessions[record.state] = record
        return record

    def pop_auth_session(self, *, state: str) -> McpAuthSessionRecord | None:
        return self.auth_sessions.pop(state, None)

    def put_token(
        self, record: TokenEnvelope, *, conn: Any | None = None
    ) -> TokenEnvelope:
        del conn
        existing = self.tokens_by_server.get(record.server_id)
        if existing is not None and existing.org_id != record.org_id:
            raise CrossTenantWriteError(table="mcp_auth_connections")
        self.tokens_by_server[record.server_id] = record
        return record

    def get_token(self, *, server_id: str) -> TokenEnvelope | None:
        return self.tokens_by_server.get(server_id)

    def append_audit(
        self, record: AuditEventRecord, *, conn: Any | None = None
    ) -> AuditEventRecord:
        del conn
        signed = _sign_mcp_audit(record, self._chain)
        self.audit_events.append(signed)
        return signed

    def list_audit_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[AuditEventRecord, ...]:
        """PR 7.1 — paginated read across the chain, newest-first by seq."""

        rows = [
            event
            for event in self.audit_events
            if event.org_id == org_id
            and (event.seq or 0) > after_seq
            and (action_prefix is None or event.action.startswith(action_prefix))
            and (actor_user_id is None or event.user_id == actor_user_id)
            and (since is None or event.created_at >= since)
            and (until is None or event.created_at < until)
        ]
        rows.sort(key=lambda e: (e.created_at, e.seq or 0), reverse=True)
        return tuple(rows[:limit])


def _take_audit_chain_lock(cur, *, table: str, org_id: str) -> None:  # type: ignore[no-untyped-def]
    """Serialize concurrent audit inserts within one (table, org) chain.

    Two concurrent appends would otherwise both read the same prev_hash and
    produce a forked chain. ``pg_advisory_xact_lock`` releases automatically
    at transaction end, so the lock scope matches the insert's atomic unit.
    The lock key is a 64-bit hash of the table name + org_id; collisions
    between unrelated chains are theoretically possible but harmless (extra
    serialization of unrelated chains, never lost integrity).
    """

    import hashlib

    digest = hashlib.sha256(f"audit_chain:{table}:{org_id}".encode("utf-8")).digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))


def _apply_rls_session_vars(  # type: ignore[no-untyped-def]
    conn, *, org_id: str | None, role: str | None
) -> None:
    """Stamp ``app.current_org_id`` / ``app.role`` on a checked-out conn (C5).

    Used by both ``PostgresMcpStore`` and ``PostgresSkillStore`` so the
    set_config behaviour stays in one place. ``set_config(_, _, true)`` is a
    transaction-local setting — once Stage 3 enables RLS, the policy
    references these names and the row visibility flips on with no
    application change.
    """

    if org_id is None and role is None:
        return
    with conn.cursor() as cur:
        if org_id is not None:
            cur.execute(
                "SELECT set_config('app.current_org_id', %s, true)",
                (org_id,),
            )
        if role is not None:
            cur.execute(
                "SELECT set_config('app.role', %s, true)",
                (role,),
            )


def _sign_mcp_audit(record: AuditEventRecord, chain: _AuditChain) -> AuditEventRecord:
    payload = {
        "audit_id": record.audit_id,
        "org_id": record.org_id,
        "user_id": record.user_id,
        "server_id": record.server_id,
        "action": record.action,
        "metadata": record.metadata,
        "created_at": record.created_at,
    }
    seq, prev_hash, signature, key_version = chain.next(
        org_id=record.org_id, payload=payload
    )
    return record.model_copy(
        update={
            "seq": seq,
            "prev_hash": prev_hash,
            "signature": signature,
            "key_version": key_version,
        }
    )


class PostgresMcpStore:
    """PostgreSQL-backed MCP registry store with connection pooling."""

    def __init__(self, *, pool: PostgresConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[Any]:
        """Yield a connection inside a transaction the caller can compose with.

        Used by the service layer to wrap (write + audit) pairs so a partial
        failure rolls back both rows. Each store write method participating in
        a composite must accept the optional ``conn`` and reuse it; otherwise
        the audit and primary writes land on separate connections and the
        atomicity guarantee is lost (C3).

        ``org_id``: when provided, stamps ``app.current_org_id`` so the C5
        ``tenant_isolation`` policies match. Defaults to None (no stamp) for
        callers that have not yet been refactored — once Stage 3 enables RLS,
        un-stamped composites will fail closed and the missing site is
        surfaced in tests/logs.
        """

        with self._pool.connection() as conn:
            _apply_rls_session_vars(conn, org_id=org_id, role="api")
            with conn.transaction():
                yield conn

    def create_server(
        self, record: McpServerRecord, *, conn: Any | None = None
    ) -> McpServerRecord:
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_servers (
                      server_id, org_id, user_id, name, display_name, url,
                      transport, auth_mode, auth_state, health, enabled,
                      required_scopes, last_discovery, oauth_client,
                      logo_url, brand_color, scopes_summary,
                      default_scopes, admin_managed,
                      created_at, updated_at
                    ) VALUES (
                      %(server_id)s, %(org_id)s, %(user_id)s, %(name)s,
                      %(display_name)s, %(url)s, %(transport)s, %(auth_mode)s,
                      %(auth_state)s, %(health)s, %(enabled)s,
                      %(required_scopes)s, %(last_discovery)s, %(oauth_client)s,
                      %(logo_url)s, %(brand_color)s, %(scopes_summary)s,
                      %(default_scopes)s, %(admin_managed)s,
                      %(created_at)s, %(updated_at)s
                    )
                    """,
                    self._server_params(record),
                )
        return record

    def update_server(
        self, record: McpServerRecord, *, conn: Any | None = None
    ) -> McpServerRecord:
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mcp_servers
                    SET name = %(name)s,
                        display_name = %(display_name)s,
                        url = %(url)s,
                        transport = %(transport)s,
                        auth_mode = %(auth_mode)s,
                        auth_state = %(auth_state)s,
                        health = %(health)s,
                        enabled = %(enabled)s,
                        required_scopes = %(required_scopes)s,
                        last_discovery = %(last_discovery)s,
                        oauth_client = %(oauth_client)s,
                        logo_url = %(logo_url)s,
                        brand_color = %(brand_color)s,
                        scopes_summary = %(scopes_summary)s,
                        default_scopes = %(default_scopes)s,
                        admin_managed = %(admin_managed)s,
                        updated_at = %(updated_at)s
                    WHERE org_id = %(org_id)s AND server_id = %(server_id)s
                    """,
                    self._server_params(record),
                )
        return record

    def get_server(self, *, org_id: str, server_id: str) -> McpServerRecord | None:
        return self._fetch_one_server(
            "SELECT * FROM mcp_servers WHERE org_id = %(org_id)s AND server_id = %(server_id)s",
            {"org_id": org_id, "server_id": server_id},
        )

    def list_servers(self, *, org_id: str, user_id: str) -> tuple[McpServerRecord, ...]:
        return self._fetch_many_servers(
            """
            SELECT * FROM mcp_servers
            WHERE org_id = %(org_id)s AND user_id = %(user_id)s
            ORDER BY created_at
            """,
            {"org_id": org_id, "user_id": user_id},
        )

    def delete_server(
        self, *, org_id: str, server_id: str, conn: Any | None = None
    ) -> bool:
        with self._connect_or_inherit(conn, org_id=org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM mcp_servers WHERE org_id = %(org_id)s AND server_id = %(server_id)s",
                    {"org_id": org_id, "server_id": server_id},
                )
                return cur.rowcount > 0

    def create_auth_session(self, record: McpAuthSessionRecord) -> McpAuthSessionRecord:
        with self._connect(org_id=record.org_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_auth_sessions (
                      session_id, server_id, org_id, user_id, state,
                      code_verifier, redirect_uri, auth_url,
                      expires_at, created_at
                    ) VALUES (
                      %(session_id)s, %(server_id)s, %(org_id)s, %(user_id)s,
                      %(state)s, %(code_verifier)s, %(redirect_uri)s, %(auth_url)s,
                      %(expires_at)s, %(created_at)s
                    )
                    """,
                    {
                        "session_id": record.session_id,
                        "server_id": record.server_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "state": record.state,
                        "code_verifier": record.code_verifier,
                        "redirect_uri": record.redirect_uri,
                        "auth_url": record.auth_url,
                        "expires_at": record.expires_at,
                        "created_at": record.created_at,
                    },
                )
        return record

    def pop_auth_session(self, *, state: str) -> McpAuthSessionRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mcp_auth_sessions WHERE state = %(state)s RETURNING *",
                    {"state": state},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return McpAuthSessionRecord(
                    session_id=str(row["session_id"]),
                    server_id=str(row["server_id"]),
                    org_id=str(row["org_id"]),
                    user_id=str(row["user_id"]),
                    state=str(row["state"]),
                    code_verifier=str(row["code_verifier"]),
                    redirect_uri=str(row["redirect_uri"]),
                    auth_url=str(row["auth_url"]),
                    expires_at=self._datetime(row["expires_at"]),
                    created_at=self._datetime(row["created_at"]),
                )

    def put_token(
        self, record: TokenEnvelope, *, conn: Any | None = None
    ) -> TokenEnvelope:
        """Atomic upsert keyed by ``server_id`` with a cross-tenant guard.

        Prior implementation did DELETE-then-INSERT in an implicit transaction
        — a process kill between the two statements left the user with no
        usable token. This is now a single ``INSERT ... ON CONFLICT (server_id)
        DO UPDATE`` whose ``WHERE`` clause asserts ``org_id`` match. If a row
        already exists for a different org we reject the write and the
        existing row stays untouched (C3, ticket put_token).
        """

        params = {
            "connection_id": record.connection_id,
            "server_id": record.server_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "encrypted_access_token": record.encrypted_access_token,
            "encrypted_refresh_token": record.encrypted_refresh_token,
            "expires_at": record.expires_at,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "kms_key_id": record.kms_key_id,
        }
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_auth_connections (
                      connection_id, server_id, org_id, user_id,
                      encrypted_access_token, encrypted_refresh_token,
                      expires_at, created_at, updated_at, kms_key_id
                    ) VALUES (
                      %(connection_id)s, %(server_id)s, %(org_id)s, %(user_id)s,
                      %(encrypted_access_token)s, %(encrypted_refresh_token)s,
                      %(expires_at)s, %(created_at)s, %(updated_at)s, %(kms_key_id)s
                    )
                    ON CONFLICT (server_id) DO UPDATE SET
                      encrypted_access_token = EXCLUDED.encrypted_access_token,
                      encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                      expires_at = EXCLUDED.expires_at,
                      updated_at = EXCLUDED.updated_at,
                      user_id = EXCLUDED.user_id,
                      kms_key_id = EXCLUDED.kms_key_id
                    WHERE mcp_auth_connections.org_id = EXCLUDED.org_id
                    """,
                    params,
                )
                if cur.rowcount == 0:
                    raise CrossTenantWriteError(table="mcp_auth_connections")
        return record

    def get_token(self, *, server_id: str) -> TokenEnvelope | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM mcp_auth_connections
                    WHERE server_id = %(server_id)s
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    {"server_id": server_id},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return TokenEnvelope(
                    connection_id=str(row["connection_id"]),
                    server_id=str(row["server_id"]),
                    org_id=str(row["org_id"]),
                    user_id=str(row["user_id"]),
                    encrypted_access_token=str(row["encrypted_access_token"]),
                    encrypted_refresh_token=str(row["encrypted_refresh_token"])
                    if row.get("encrypted_refresh_token") is not None
                    else None,
                    expires_at=self._datetime(row["expires_at"])
                    if row.get("expires_at") is not None
                    else None,
                    created_at=self._datetime(row["created_at"]),
                    updated_at=self._datetime(row["updated_at"]),
                    kms_key_id=str(row["kms_key_id"])
                    if row.get("kms_key_id") is not None
                    else None,
                )

    def append_audit(
        self, record: AuditEventRecord, *, conn: Any | None = None
    ) -> AuditEventRecord:
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                _take_audit_chain_lock(
                    cur, table="mcp_audit_events", org_id=record.org_id
                )
                cur.execute(
                    """
                    SELECT seq, signature
                      FROM mcp_audit_events
                     WHERE org_id = %(org_id)s
                     ORDER BY seq DESC NULLS LAST
                     LIMIT 1
                    """,
                    {"org_id": record.org_id},
                )
                head = cur.fetchone()
                last_seq = (
                    int(head["seq"]) if head and head.get("seq") is not None else 0
                )
                prev_hash = (
                    bytes(head["signature"])
                    if head and head.get("signature") is not None
                    else None
                )
                seq = last_seq + 1
                payload = {
                    "audit_id": record.audit_id,
                    "org_id": record.org_id,
                    "user_id": record.user_id,
                    "server_id": record.server_id,
                    "action": record.action,
                    "metadata": record.metadata,
                    "created_at": record.created_at,
                }
                sig = signer.sign(prev_hash=prev_hash, payload=payload)
                cur.execute(
                    """
                    INSERT INTO mcp_audit_events (
                      audit_id, org_id, user_id, server_id, action,
                      metadata, created_at,
                      seq, prev_hash, signature, key_version
                    ) VALUES (
                      %(audit_id)s, %(org_id)s, %(user_id)s, %(server_id)s,
                      %(action)s, %(metadata)s, %(created_at)s,
                      %(seq)s, %(prev_hash)s, %(signature)s, %(key_version)s
                    )
                    """,
                    {
                        "audit_id": record.audit_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "server_id": record.server_id,
                        "action": record.action,
                        "metadata": json.dumps(record.metadata),
                        "created_at": record.created_at,
                        "seq": seq,
                        "prev_hash": sig.prev_hash,
                        "signature": sig.signature,
                        "key_version": sig.key_version,
                    },
                )
        return record.model_copy(
            update={
                "seq": seq,
                "prev_hash": sig.prev_hash,
                "signature": sig.signature,
                "key_version": sig.key_version,
            }
        )

    def _fetch_one_server(
        self, query: str, params: dict[str, object]
    ) -> McpServerRecord | None:
        rows = self._fetch_many_servers(query, params)
        return rows[0] if rows else None

    def _fetch_many_servers(
        self, query: str, params: dict[str, object]
    ) -> tuple[McpServerRecord, ...]:
        # ``params`` is built by the caller and always carries ``org_id`` for
        # tenant-scoped reads (see ``get_server`` / ``list_servers``). Passing
        # it through to ``_connect`` stamps the RLS session var.
        org_id = params.get("org_id")
        org_id_str = str(org_id) if isinstance(org_id, str) else None
        with self._connect(org_id=org_id_str) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return tuple(self._row_to_server(row) for row in cur.fetchall())

    @contextmanager
    def _connect(self, *, org_id: str | None = None) -> Iterator[Any]:
        """Acquire a pool connection and stamp RLS session vars (C5).

        ``org_id``: when set, stamps ``app.current_org_id`` so tenant-isolation
        policies match. Always stamps ``app.role='api'`` so the outbox-style
        ``tenant_or_worker`` policies on other databases keyed off the role
        var distinguish API vs worker traffic.
        """

        with self._pool.connection() as conn:
            _apply_rls_session_vars(conn, org_id=org_id, role="api")
            yield conn

    @contextmanager
    def _connect_or_inherit(
        self, conn: Any | None, *, org_id: str | None = None
    ) -> Iterator[Any]:
        """Yield ``conn`` directly if non-None, else acquire a fresh pool conn.

        Lets store methods participate in a caller-provided transaction (when
        composed by the service layer) while keeping single-call behavior
        unchanged. When ``conn`` is inherited the session vars were stamped
        by the outer ``transaction(org_id=...)`` call; we don't restamp.
        """

        if conn is not None:
            yield conn
            return
        with self._connect(org_id=org_id) as own:
            yield own

    @classmethod
    def _server_params(cls, record: McpServerRecord) -> dict[str, object]:
        return {
            "server_id": record.server_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "name": record.name,
            "display_name": record.display_name,
            "url": record.url,
            "transport": record.transport.value,
            "auth_mode": record.auth_mode.value,
            "auth_state": record.auth_state.value,
            "health": record.health.value,
            "enabled": record.enabled,
            "required_scopes": json.dumps(list(record.required_scopes)),
            "last_discovery": json.dumps(record.last_discovery),
            "oauth_client": json.dumps(record.oauth_client.model_dump())
            if record.oauth_client is not None
            else None,
            "logo_url": record.logo_url,
            "brand_color": record.brand_color,
            "scopes_summary": record.scopes_summary,
            "default_scopes": json.dumps(list(record.default_scopes)),
            "admin_managed": record.admin_managed,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @classmethod
    def _row_to_server(cls, row: dict[str, object]) -> McpServerRecord:
        oauth_client_raw = cls._json_object(row.get("oauth_client"))
        return McpServerRecord(
            server_id=str(row["server_id"]),
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            url=str(row["url"]),
            transport=str(row["transport"]),
            auth_mode=str(row["auth_mode"]),
            auth_state=str(row["auth_state"]),
            health=str(row["health"]),
            enabled=bool(row["enabled"]),
            required_scopes=tuple(cls._json_list(row.get("required_scopes"))),
            last_discovery=dict(cls._json_object(row.get("last_discovery"))),
            oauth_client=McpOAuthClientConfig(**oauth_client_raw)
            if oauth_client_raw
            else None,
            logo_url=cls._optional_str(row.get("logo_url")),
            brand_color=cls._optional_str(row.get("brand_color")),
            scopes_summary=cls._optional_str(row.get("scopes_summary")),
            default_scopes=tuple(cls._json_list(row.get("default_scopes"))),
            admin_managed=bool(row.get("admin_managed") or False),
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
        )

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _json_list(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [str(item) for item in json.loads(value)]
        return []

    @classmethod
    def _json_object(cls, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @classmethod
    def _datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


@dataclass
class InMemorySkillStore:
    """Skill registry store used when Postgres is unavailable or unnecessary."""

    skills: dict[str, SkillRecord] = field(default_factory=dict)
    audit_events: list[SkillAuditEventRecord] = field(default_factory=list)
    _chain: _AuditChain = field(default_factory=_AuditChain, init=False, repr=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """No-op transaction so the service layer can compose uniformly."""

        yield None

    def create_skill(
        self, record: SkillRecord, *, conn: Any | None = None
    ) -> SkillRecord:
        del conn
        self.skills[record.skill_id] = record
        return record

    def update_skill(
        self, record: SkillRecord, *, conn: Any | None = None
    ) -> SkillRecord:
        del conn
        self.skills[record.skill_id] = record
        return record

    def get_skill(self, *, org_id: str, skill_id: str) -> SkillRecord | None:
        record = self.skills.get(skill_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def get_skill_by_name(
        self,
        *,
        org_id: str,
        user_id: str,
        name: str,
    ) -> SkillRecord | None:
        for record in self.skills.values():
            if record.org_id != org_id or record.name != name:
                continue
            if record.user_id == user_id or record.scope == "org":
                return record
        return None

    def list_skills(
        self,
        *,
        org_id: str,
        user_id: str,
        include_disabled: bool = True,
    ) -> tuple[SkillRecord, ...]:
        records = (
            record
            for record in self.skills.values()
            if record.org_id == org_id
            and (record.user_id == user_id or record.scope == "org")
            and (include_disabled or record.enabled)
        )
        return tuple(
            sorted(records, key=lambda record: (record.created_at, record.name))
        )

    def delete_skill(
        self,
        *,
        org_id: str,
        user_id: str,
        skill_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.get_skill(org_id=org_id, skill_id=skill_id)
        if record is None or record.user_id != user_id:
            return False
        self.skills.pop(skill_id, None)
        return True

    def append_skill_audit(
        self,
        record: SkillAuditEventRecord,
        *,
        conn: Any | None = None,
    ) -> SkillAuditEventRecord:
        del conn
        signed = _sign_skill_audit(record, self._chain)
        self.audit_events.append(signed)
        return signed

    def list_skill_audit_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[SkillAuditEventRecord, ...]:
        """PR 7.1 — paginated read across the skill audit chain."""

        rows = [
            event
            for event in self.audit_events
            if event.org_id == org_id
            and (event.seq or 0) > after_seq
            and (action_prefix is None or event.action.startswith(action_prefix))
            and (actor_user_id is None or event.user_id == actor_user_id)
            and (since is None or event.created_at >= since)
            and (until is None or event.created_at < until)
        ]
        rows.sort(key=lambda e: (e.created_at, e.seq or 0), reverse=True)
        return tuple(rows[:limit])


def _sign_skill_audit(
    record: SkillAuditEventRecord, chain: _AuditChain
) -> SkillAuditEventRecord:
    payload = {
        "audit_id": record.audit_id,
        "org_id": record.org_id,
        "user_id": record.user_id,
        "skill_id": record.skill_id,
        "action": record.action,
        "metadata": record.metadata,
        "created_at": record.created_at,
    }
    seq, prev_hash, signature, key_version = chain.next(
        org_id=record.org_id, payload=payload
    )
    return record.model_copy(
        update={
            "seq": seq,
            "prev_hash": prev_hash,
            "signature": signature,
            "key_version": key_version,
        }
    )


class PostgresSkillStore:
    """PostgreSQL-backed Skill registry store with connection pooling."""

    def __init__(self, *, pool: PostgresConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[Any]:
        """Yield a connection inside a transaction; see PostgresMcpStore."""

        with self._pool.connection() as conn:
            _apply_rls_session_vars(conn, org_id=org_id, role="api")
            with conn.transaction():
                yield conn

    def create_skill(
        self, record: SkillRecord, *, conn: Any | None = None
    ) -> SkillRecord:
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skills (
                      skill_id, org_id, user_id, name, display_name, description,
                      markdown, virtual_path, enabled, scope, source_type, version,
                      allowed_tools, compatibility, metadata, created_at, updated_at
                    ) VALUES (
                      %(skill_id)s, %(org_id)s, %(user_id)s, %(name)s, %(display_name)s,
                      %(description)s, %(markdown)s, %(virtual_path)s, %(enabled)s,
                      %(scope)s, %(source_type)s, %(version)s, %(allowed_tools)s,
                      %(compatibility)s, %(metadata)s, %(created_at)s, %(updated_at)s
                    )
                    """,
                    self._record_params(record),
                )
        return record

    def update_skill(
        self, record: SkillRecord, *, conn: Any | None = None
    ) -> SkillRecord:
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE skills
                    SET display_name = %(display_name)s,
                        description = %(description)s,
                        markdown = %(markdown)s,
                        virtual_path = %(virtual_path)s,
                        enabled = %(enabled)s,
                        scope = %(scope)s,
                        source_type = %(source_type)s,
                        version = %(version)s,
                        allowed_tools = %(allowed_tools)s,
                        compatibility = %(compatibility)s,
                        metadata = %(metadata)s,
                        updated_at = %(updated_at)s
                    WHERE org_id = %(org_id)s AND skill_id = %(skill_id)s
                    """,
                    self._record_params(record),
                )
        return record

    def get_skill(self, *, org_id: str, skill_id: str) -> SkillRecord | None:
        return self._fetch_one(
            "SELECT * FROM skills WHERE org_id = %(org_id)s AND skill_id = %(skill_id)s",
            {"org_id": org_id, "skill_id": skill_id},
        )

    def get_skill_by_name(
        self,
        *,
        org_id: str,
        user_id: str,
        name: str,
    ) -> SkillRecord | None:
        return self._fetch_one(
            """
            SELECT * FROM skills
            WHERE org_id = %(org_id)s
              AND name = %(name)s
              AND (user_id = %(user_id)s OR scope = 'org')
            ORDER BY scope DESC, updated_at DESC
            LIMIT 1
            """,
            {"org_id": org_id, "user_id": user_id, "name": name},
        )

    def list_skills(
        self,
        *,
        org_id: str,
        user_id: str,
        include_disabled: bool = True,
    ) -> tuple[SkillRecord, ...]:
        enabled_clause = "" if include_disabled else "AND enabled = TRUE"
        return self._fetch_many(
            f"""
            SELECT * FROM skills
            WHERE org_id = %(org_id)s
              AND (user_id = %(user_id)s OR scope = 'org')
              {enabled_clause}
            ORDER BY created_at, name
            """,
            {"org_id": org_id, "user_id": user_id},
        )

    def delete_skill(
        self,
        *,
        org_id: str,
        user_id: str,
        skill_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._connect_or_inherit(conn, org_id=org_id) as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM skills
                    WHERE org_id = %(org_id)s AND user_id = %(user_id)s AND skill_id = %(skill_id)s
                    """,
                    {"org_id": org_id, "user_id": user_id, "skill_id": skill_id},
                )
                return cur.rowcount > 0

    def append_skill_audit(
        self,
        record: SkillAuditEventRecord,
        *,
        conn: Any | None = None,
    ) -> SkillAuditEventRecord:
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        with self._connect_or_inherit(conn, org_id=record.org_id) as connection:
            with connection.cursor() as cur:
                _take_audit_chain_lock(
                    cur, table="skill_audit_events", org_id=record.org_id
                )
                cur.execute(
                    """
                    SELECT seq, signature
                      FROM skill_audit_events
                     WHERE org_id = %(org_id)s
                     ORDER BY seq DESC NULLS LAST
                     LIMIT 1
                    """,
                    {"org_id": record.org_id},
                )
                head = cur.fetchone()
                last_seq = (
                    int(head["seq"]) if head and head.get("seq") is not None else 0
                )
                prev_hash = (
                    bytes(head["signature"])
                    if head and head.get("signature") is not None
                    else None
                )
                seq = last_seq + 1
                payload = {
                    "audit_id": record.audit_id,
                    "org_id": record.org_id,
                    "user_id": record.user_id,
                    "skill_id": record.skill_id,
                    "action": record.action,
                    "metadata": record.metadata,
                    "created_at": record.created_at,
                }
                sig = signer.sign(prev_hash=prev_hash, payload=payload)
                cur.execute(
                    """
                    INSERT INTO skill_audit_events (
                      audit_id, org_id, user_id, skill_id, action, metadata, created_at,
                      seq, prev_hash, signature, key_version
                    ) VALUES (
                      %(audit_id)s, %(org_id)s, %(user_id)s, %(skill_id)s,
                      %(action)s, %(metadata)s, %(created_at)s,
                      %(seq)s, %(prev_hash)s, %(signature)s, %(key_version)s
                    )
                    """,
                    {
                        "audit_id": record.audit_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "skill_id": record.skill_id,
                        "action": record.action,
                        "metadata": json.dumps(record.metadata),
                        "created_at": record.created_at,
                        "seq": seq,
                        "prev_hash": sig.prev_hash,
                        "signature": sig.signature,
                        "key_version": sig.key_version,
                    },
                )
        return record.model_copy(
            update={
                "seq": seq,
                "prev_hash": sig.prev_hash,
                "signature": sig.signature,
                "key_version": sig.key_version,
            }
        )

    def _fetch_one(self, query: str, params: dict[str, object]) -> SkillRecord | None:
        rows = self._fetch_many(query, params)
        return rows[0] if rows else None

    def _fetch_many(
        self, query: str, params: dict[str, object]
    ) -> tuple[SkillRecord, ...]:
        org_id = params.get("org_id")
        org_id_str = str(org_id) if isinstance(org_id, str) else None
        with self._connect(org_id=org_id_str) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return tuple(self._row_to_record(row) for row in cur.fetchall())

    @contextmanager
    def _connect(self, *, org_id: str | None = None) -> Iterator[Any]:
        with self._pool.connection() as conn:
            _apply_rls_session_vars(conn, org_id=org_id, role="api")
            yield conn

    @contextmanager
    def _connect_or_inherit(
        self, conn: Any | None, *, org_id: str | None = None
    ) -> Iterator[Any]:
        if conn is not None:
            yield conn
            return
        with self._connect(org_id=org_id) as own:
            yield own

    @classmethod
    def _record_params(cls, record: SkillRecord) -> dict[str, object]:
        return {
            "skill_id": record.skill_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "name": record.name,
            "display_name": record.display_name,
            "description": record.description,
            "markdown": record.markdown,
            "virtual_path": record.virtual_path,
            "enabled": record.enabled,
            "scope": record.scope.value,
            "source_type": record.source_type.value,
            "version": record.version,
            "allowed_tools": json.dumps(record.allowed_tools),
            "compatibility": json.dumps(record.compatibility),
            "metadata": json.dumps(record.metadata),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @classmethod
    def _row_to_record(cls, row: dict[str, object]) -> SkillRecord:
        return SkillRecord(
            skill_id=str(row["skill_id"]),
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            description=str(row["description"]),
            markdown=str(row["markdown"]),
            virtual_path=str(row["virtual_path"]),
            enabled=bool(row["enabled"]),
            scope=str(row["scope"]),
            source_type=str(row["source_type"]),
            version=int(row["version"]),
            allowed_tools=tuple(cls._json_list(row["allowed_tools"])),
            compatibility=tuple(cls._json_list(row["compatibility"])),
            metadata=dict(cls._json_object(row["metadata"])),
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
        )

    @classmethod
    def _json_list(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [str(item) for item in json.loads(value)]
        return []

    @classmethod
    def _json_object(cls, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @classmethod
    def _datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


@dataclass
class InMemoryDeployAuditStore:
    """Deploy audit log used until a postgres-backed adapter is wired in.

    The CI/CD assurance spec tracks postgres backing for deploy audit as a known gap.
    The in-memory adapter is sufficient for local dev and tests; production must inject
    a persistent adapter that mirrors this contract before claiming the control complete.
    """

    audit_events: list[DeployAuditEventRecord] = field(default_factory=list)
    _chain: _AuditChain = field(default_factory=_AuditChain, init=False, repr=False)

    def append_deploy_audit(
        self, record: DeployAuditEventRecord
    ) -> DeployAuditEventRecord:
        signed = _sign_deploy_audit(record, self._chain)
        self.audit_events.append(signed)
        return signed

    def list_deploy_audit_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[DeployAuditEventRecord, ...]:
        """PR 7.1 — paginated read across the deploy audit chain.

        ``action_prefix`` matches against ``outcome`` since deploy events
        don't carry a free-form action string; the conventional prefixes
        are ``deploy.success`` / ``deploy.failed`` etc., which the
        compositor maps onto the ``outcome`` column.
        """

        rows = [
            event
            for event in self.audit_events
            if event.org_id == org_id
            and (event.seq or 0) > after_seq
            and (
                action_prefix is None
                or f"deploy.{event.outcome}".startswith(action_prefix)
            )
            and (actor_user_id is None or event.user_id == actor_user_id)
            and (since is None or event.created_at >= since)
            and (until is None or event.created_at < until)
        ]
        rows.sort(key=lambda e: (e.created_at, e.seq or 0), reverse=True)
        return tuple(rows[:limit])


def _sign_deploy_audit(
    record: DeployAuditEventRecord, chain: _AuditChain
) -> DeployAuditEventRecord:
    payload = {
        "audit_id": record.audit_id,
        "org_id": record.org_id,
        "user_id": record.user_id,
        "tenant_id": record.tenant_id,
        "environment": record.environment,
        "release_sha": record.release_sha,
        "image_digests": [d.model_dump() for d in record.image_digests],
        "approver": record.approver,
        "workflow_run_url": record.workflow_run_url,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "outcome": record.outcome,
        "force_deploy": record.force_deploy,
        "actor_kind": record.actor_kind,
        "created_at": record.created_at,
    }
    seq, prev_hash, signature, key_version = chain.next(
        org_id=record.org_id, payload=payload
    )
    return record.model_copy(
        update={
            "seq": seq,
            "prev_hash": prev_hash,
            "signature": signature,
            "key_version": key_version,
        }
    )
