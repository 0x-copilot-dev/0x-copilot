"""Connectors store — adapter contract + in-memory implementation.

Storage shape mirrors ``schema.sql`` in this package. The destination's
rows are a DENORMALIZED READ MODEL over the existing MCP registration +
token vault path — see ``connectors-prd.md`` §3.2 + §5.1. The in-memory
adapter is the dev / test default; production deploys inject the
postgres adapter (the write-through helper is the same regardless of
backend).

Authorization is NOT enforced here. The service layer composes
:class:`ConnectorsStore` with the project-membership port (canonical
``backend_app.projects.acl``) + the identity store to decide read/write
authority; the store exposes raw queries scoped to ``tenant_id``.

Write path — the substitution principle:

* Writes flow through ``upsert_from_mcp_registration`` which takes the
  existing :class:`McpServerRecord` + token vault meta and writes the
  denormalized row. The same helper is called from the OAuth-callback
  path (after the existing :class:`McpRegistryService.complete_auth`
  wrote the MCP row) AND from the refresh / disconnect paths. This is
  the DRY win: zero new auth code in the destination, and the rows are
  guaranteed consistent with the underlying MCP/token state.
* Direct ``insert_connector`` / ``update_connector`` exist for tests
  and edge cases that don't go through MCP (custom connectors). The
  PRD's "no parallel write model" rule means production writes always
  pass through the MCP path.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterator, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from copilot_audit_chain import AuditChainSigner

# Reuse the backend's canonical connection-hardening primitives (same
# deployable component) so the connectors store stamps RLS session vars
# and serialises audit-chain inserts through exactly one implementation:
#   * ``_apply_rls_session_vars`` — SET LOCAL app.current_org_id / app.role
#   * ``_take_audit_chain_lock``  — pg_advisory_xact_lock per (table, org)
# Same pattern as ``backend_app.projects.store`` (PRD-H.3 hardening).
from backend_app.store import _apply_rls_session_vars, _take_audit_chain_lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connector_id() -> str:
    return f"conn_{uuid4().hex}"


def _audit_id() -> str:
    return f"audcon_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class ConnectorAccessMode(StrEnum):
    """Per-connector agent access mode (Tools destination 3-way segment).

    Single enumeration shared by the ``connectors.access_mode`` CHECK
    constraint (``0046_connector_access_mode.sql`` / ``schema.sql``) and this
    Pydantic model, so the DB constraint and the record type can never drift.
    Byte-identical to ``ConnectorAccessMode`` in
    ``packages/api-types/src/connectors.ts``.

    * ``read``     — the agent may READ from the connector (least privilege
                     that still lets it see data).
    * ``read_act`` — the agent may read AND ACT through the connector
                     (write / side-effecting calls, still subject to the
                     global approval policy in Settings -> Model & behavior).
    * ``off``      — the connector is disabled for the agent; no reads, no
                     acts. Enforced at the ``proxy_internal_rpc`` chokepoint.
    """

    READ = "read"
    READ_ACT = "read_act"
    OFF = "off"


class ConnectorScopeEntry(BaseModel):
    """One OAuth scope on a connector row.

    Mirrors ``packages/api-types::ConnectorScopeEntry``. Provider-specific
    string values; ``description`` is sourced from the catalog file at
    backend bootstrap.
    """

    model_config = ConfigDict(extra="forbid")

    scope: str
    granted: bool = True
    description: str = ""


class ConnectorRecord(BaseModel):
    """One row in the ``connectors`` table.

    Pydantic model so the Postgres + in-memory adapters share one
    read/write contract. ``scopes`` is a JSONB blob on the wire; we keep
    it as a list of :class:`ConnectorScopeEntry` here and let the routes
    serialise to the wire shape. ``vault_ref`` is opaque — the service
    layer never decodes it; the existing :class:`TokenVault` is the
    sole owner of token bytes.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_connector_id)
    tenant_id: str
    slug: str
    display_name: str
    description: str = ""
    status: str = "connected"
    status_reason: str | None = None
    # Per-connector agent access mode. Defaults to ``read`` on first insert
    # (D1: existing rows were installed under a fully-usable regime — ``off``
    # would silently break every deployed workspace, ``read_act`` would grant
    # more than the user ever saw). Preserved verbatim across MCP
    # re-registration (see ``upsert_from_mcp_registration``).
    access_mode: ConnectorAccessMode = ConnectorAccessMode.READ
    owner_user_id: str
    scopes: list[ConnectorScopeEntry] = Field(default_factory=list)
    last_sync_at: datetime | None = None
    last_error_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    vault_ref: str = ""


class ConnectorAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    Same shape discipline as ``inbox_audit_events`` /
    ``todo_audit_events``. ``correlation_id`` stamps the rows belonging
    to one bulk write; ``before_state`` / ``after_state`` are dicts; the
    audit-chain integration (``packages/audit-chain``) signs + chains
    rows in production. The in-memory adapter appends raw rows for
    tests.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "connector"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Write-through input — what the service hands the store after the
# existing MCP/OAuth path has produced its rows.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpUpsertInput:
    """Inputs the service hands the store when an MCP registration lands.

    Frozen dataclass so the write-through path is data-only — the helper
    that produces this input does the projection from
    :class:`McpServerRecord` + the token-vault metadata at the service
    layer; the store doesn't see McpServerRecord directly (deliberate
    boundary: the store stays decoupled from the MCP module's contracts).
    """

    tenant_id: str
    slug: str
    owner_user_id: str
    display_name: str
    description: str
    status: str
    status_reason: str | None
    scopes: tuple[ConnectorScopeEntry, ...]
    last_sync_at: datetime | None
    last_error_at: datetime | None
    vault_ref: str
    # If known, the existing connector ID — re-use across upserts so the
    # destination row is stable for the same MCP server. Otherwise a
    # fresh ``conn_<ulid>`` is generated on first write.
    existing_id: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ConnectorsStore(Protocol):
    """Adapter contract for the Postgres + in-memory connectors stores."""

    @contextmanager
    def transaction(  # pragma: no cover
        self, *, org_id: str | None = None
    ) -> Iterator[None]: ...

    # -- reads --------------------------------------------------------

    def get_connector(
        self, *, tenant_id: str, connector_id: str
    ) -> ConnectorRecord | None: ...

    def list_connectors(
        self,
        *,
        tenant_id: str,
        statuses: tuple[str, ...] | None = None,
        slugs: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorRecord, ...], str | None]: ...

    def get_by_owner_and_slug(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        slug: str,
    ) -> ConnectorRecord | None: ...

    # -- writes -------------------------------------------------------

    def insert_connector(self, record: ConnectorRecord) -> ConnectorRecord: ...

    def update_connector(self, record: ConnectorRecord) -> ConnectorRecord: ...

    def upsert_from_mcp_registration(
        self, mcp_input: McpUpsertInput
    ) -> ConnectorRecord: ...

    # -- audit --------------------------------------------------------

    def append_audit(self, record: ConnectorAuditRecord) -> ConnectorAuditRecord: ...

    def list_audit_for_connector(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorAuditRecord, ...], str | None]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryConnectorsStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is a
    filter on every query; status transitions are recorded by replacing
    the row (no soft-delete column — ``disconnected`` is a status value,
    not a deletion). The audit log is append-only.
    """

    connectors: dict[str, ConnectorRecord] = field(default_factory=dict)
    audits: list[ConnectorAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the postgres adapter without a
        # branch. ``org_id`` is accepted (and ignored) for signature
        # parity with the Postgres adapter, which stamps it as an RLS
        # session var on the shared write connection.
        yield

    # -- reads --------------------------------------------------------

    def get_connector(
        self, *, tenant_id: str, connector_id: str
    ) -> ConnectorRecord | None:
        record = self.connectors.get(connector_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    def list_connectors(
        self,
        *,
        tenant_id: str,
        statuses: tuple[str, ...] | None = None,
        slugs: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorRecord, ...], str | None]:
        needle = q.strip().lower() if q else None
        candidates: list[ConnectorRecord] = []
        for record in self.connectors.values():
            if record.tenant_id != tenant_id:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            if slugs is not None and record.slug not in slugs:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if needle is not None:
                haystack = (
                    f"{record.display_name} {record.description} {record.slug}".lower()
                )
                if needle not in haystack:
                    continue
            candidates.append(record)

        # Stable sort by (created_at, id) so the postgres-style keyset
        # pagination has deterministic order. The cursor is the index
        # into the sorted list (opaque to the client).
        candidates.sort(key=lambda r: (r.created_at, r.id))
        start = 0
        if cursor is not None:
            try:
                start = int(cursor)
            except ValueError:
                start = 0
        page = candidates[start : start + limit]
        next_cursor: str | None = None
        if start + limit < len(candidates):
            next_cursor = str(start + limit)
        return tuple(page), next_cursor

    def get_by_owner_and_slug(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        slug: str,
    ) -> ConnectorRecord | None:
        for record in self.connectors.values():
            if (
                record.tenant_id == tenant_id
                and record.owner_user_id == owner_user_id
                and record.slug == slug
            ):
                return record
        return None

    # -- writes -------------------------------------------------------

    def insert_connector(self, record: ConnectorRecord) -> ConnectorRecord:
        self.connectors[record.id] = record
        return record

    def update_connector(self, record: ConnectorRecord) -> ConnectorRecord:
        self.connectors[record.id] = record
        return record

    def upsert_from_mcp_registration(
        self, mcp_input: McpUpsertInput
    ) -> ConnectorRecord:
        """Write-through helper — denormalize from an MCP registration.

        Existing rows are looked up by ``existing_id`` if given, falling
        back to the natural key ``(tenant_id, owner_user_id, slug)``.
        This is the substitution point: the MCP/OAuth path produces the
        same input shape, and the same helper writes the denormalized
        view — no parallel auth code.
        """

        existing: ConnectorRecord | None = None
        if mcp_input.existing_id is not None:
            existing = self.connectors.get(mcp_input.existing_id)
        if existing is None:
            existing = self.get_by_owner_and_slug(
                tenant_id=mcp_input.tenant_id,
                owner_user_id=mcp_input.owner_user_id,
                slug=mcp_input.slug,
            )
        if existing is None:
            record = ConnectorRecord(
                tenant_id=mcp_input.tenant_id,
                slug=mcp_input.slug,
                display_name=mcp_input.display_name,
                description=mcp_input.description,
                status=mcp_input.status,
                status_reason=mcp_input.status_reason,
                owner_user_id=mcp_input.owner_user_id,
                scopes=list(mcp_input.scopes),
                last_sync_at=mcp_input.last_sync_at,
                last_error_at=mcp_input.last_error_at,
                vault_ref=mcp_input.vault_ref,
            )
        else:
            record = existing.model_copy(
                update={
                    "display_name": mcp_input.display_name,
                    "description": mcp_input.description,
                    "status": mcp_input.status,
                    "status_reason": mcp_input.status_reason,
                    "scopes": list(mcp_input.scopes),
                    "last_sync_at": mcp_input.last_sync_at,
                    "last_error_at": mcp_input.last_error_at,
                    "vault_ref": mcp_input.vault_ref,
                    "updated_at": _now(),
                }
            )
        self.connectors[record.id] = record
        return record

    # -- audit --------------------------------------------------------

    def append_audit(self, record: ConnectorAuditRecord) -> ConnectorAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_connector(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorAuditRecord, ...], str | None]:
        candidates = [
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == connector_id
        ]
        candidates.sort(key=lambda r: (r.ts, r.audit_id), reverse=True)
        start = int(cursor) if cursor else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor


# ---------------------------------------------------------------------------
# Postgres adapter (PRD-I FR-I3.1 / FR-I3.2)
# ---------------------------------------------------------------------------
#
# Durable connectors store implementing the same :class:`ConnectorsStore`
# Protocol as :class:`InMemoryConnectorsStore`, against the DDL in
# ``schema.sql`` (migration ``0044_connectors``). Selected in
# ``desktop_app.py`` alongside the other Postgres adapters (the in-memory
# store stays the default for tests/dev).
#
# The :class:`ConnectorsStore` Protocol methods take no ``conn`` argument
# (the service composes ``with store.transaction(): store.write(...)``
# without threading a connection — see
# :meth:`ConnectorsService.write_through_from_mcp`). To keep the composed
# writes atomic on ONE connection while staying safe under concurrent
# requests, the active transaction connection is held in a
# :class:`contextvars.ContextVar` — each request's execution context reads
# back the same connection its ``transaction()`` opened, and unrelated
# requests never share it. Outside a ``transaction()`` block each method
# checks out its own short-lived pooled connection.
#
# Hardened from day one, mirroring ``PostgresProjectsStore`` (PR #182):
#   * ``transaction(org_id=...)`` and the fresh-connection path in
#     ``_cursor(tenant_id=...)`` stamp ``app.current_org_id`` / ``app.role``
#     so the tenant-isolation policies in ``schema.sql`` back the
#     application-side ``WHERE tenant_id = %s`` scoping.
#   * ``append_audit`` reads the per-tenant chain head under an advisory
#     lock, then signs (seq / prev_hash / signature / key_version) through
#     the shared :class:`AuditChainSigner` — the same path
#     ``PostgresMcpStore.append_audit`` uses for ``mcp_audit_events``.
# Live-Postgres SQL execution stays DEFERRED (PRD-J J2): the fake-conn
# tests in ``tests/test_connectors_store_selection.py`` exercise the
# Python paths; the supervised-boot smoke exercises the real RLS + chain.

_ACTIVE_CONNECTORS_CONN: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "connectors_active_conn", default=None
)


class PostgresConnectorsStore:
    """psycopg-backed connectors store. Uses the shared backend pool.

    ``pool`` is duck-typed (tests pass a fake) but in production it is the
    shared ``PostgresConnectionPool``. Every query is scoped to
    ``tenant_id`` in the application-side ``WHERE`` clause (the RLS policy
    in ``schema.sql`` is the second wall).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # -- connection / transaction plumbing ----------------------------

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[Any]:
        """Open a transaction and publish its connection to the context.

        Composed store writes inside the ``with`` block run on this one
        connection so a partial failure rolls back every row together.

        ``org_id`` (the caller's ``tenant_id``) is stamped as the
        ``app.current_org_id`` RLS session var on the shared connection so
        every composed write inside the block is backed by the
        tenant-isolation policies in ``schema.sql``. ``app.role='api'`` is
        always stamped. Defaults to ``None`` (no stamp) for signature
        parity with the in-memory adapter and callers not yet passing a
        tenant.
        """

        existing = _ACTIVE_CONNECTORS_CONN.get()
        if existing is not None:
            # Re-entrant: already inside a transaction on this context.
            yield existing
            return
        with self._pool.connection() as conn:
            token = _ACTIVE_CONNECTORS_CONN.set(conn)
            try:
                with conn.transaction():
                    # Stamp inside the transaction so the SET LOCAL scope
                    # matches the composed writes' atomic unit.
                    _apply_rls_session_vars(conn, org_id=org_id, role="api")
                    yield conn
            finally:
                _ACTIVE_CONNECTORS_CONN.reset(token)

    @contextmanager
    def _cursor(self, *, tenant_id: str | None = None) -> Iterator[Any]:
        """Yield a cursor on the active transaction conn, or a fresh one.

        When a fresh (non-transaction) connection is checked out, stamp the
        RLS session vars from ``tenant_id`` so standalone reads/writes are
        tenant-scoped by the policy as well as the ``WHERE`` clause. Inside
        a transaction the connection was already stamped by
        :meth:`transaction`, so we don't restamp.
        """

        active = _ACTIVE_CONNECTORS_CONN.get()
        if active is not None:
            with active.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            _apply_rls_session_vars(owned, org_id=tenant_id, role="api")
            with owned.cursor() as cur:
                yield cur

    # -- reads --------------------------------------------------------

    def get_connector(
        self, *, tenant_id: str, connector_id: str
    ) -> ConnectorRecord | None:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                "SELECT * FROM connectors WHERE tenant_id = %s AND id = %s",
                (tenant_id, connector_id),
            )
            row = cur.fetchone()
        return _row_to_connector(row) if row else None

    def list_connectors(
        self,
        *,
        tenant_id: str,
        statuses: tuple[str, ...] | None = None,
        slugs: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorRecord, ...], str | None]:
        where: list[str] = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if statuses is not None:
            where.append("status = ANY(%s)")
            params.append(list(statuses))
        if slugs is not None:
            where.append("slug = ANY(%s)")
            params.append(list(slugs))
        if owner_user_id is not None:
            where.append("owner_user_id = %s")
            params.append(owner_user_id)
        if q and q.strip():
            where.append("(display_name || ' ' || description || ' ' || slug) ILIKE %s")
            params.append(f"%{q.strip()}%")

        offset = _decode_cursor(cursor)
        # Fetch one extra row to compute the next cursor without COUNT(*).
        params.extend([offset, limit + 1])
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                "SELECT * FROM connectors WHERE "
                + " AND ".join(where)
                + " ORDER BY created_at ASC, id ASC OFFSET %s LIMIT %s",
                tuple(params),
            )
            rows = cur.fetchall()
        has_more = len(rows) > limit
        page = tuple(_row_to_connector(r) for r in rows[:limit])
        next_cursor = str(offset + limit) if has_more else None
        return page, next_cursor

    def get_by_owner_and_slug(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        slug: str,
    ) -> ConnectorRecord | None:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM connectors
                WHERE tenant_id = %s AND owner_user_id = %s AND slug = %s
                LIMIT 1
                """,
                (tenant_id, owner_user_id, slug),
            )
            row = cur.fetchone()
        return _row_to_connector(row) if row else None

    # -- writes -------------------------------------------------------

    def insert_connector(self, record: ConnectorRecord) -> ConnectorRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                INSERT INTO connectors (
                    id, tenant_id, slug, display_name, description,
                    status, status_reason, access_mode, owner_user_id, scopes,
                    last_sync_at, last_error_at, created_at, updated_at,
                    vault_ref
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s
                )
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.slug,
                    record.display_name,
                    record.description,
                    record.status,
                    record.status_reason,
                    record.access_mode.value,
                    record.owner_user_id,
                    _jsonb([s.model_dump() for s in record.scopes]),
                    record.last_sync_at,
                    record.last_error_at,
                    record.created_at,
                    record.updated_at,
                    record.vault_ref,
                ),
            )
        return record

    def update_connector(self, record: ConnectorRecord) -> ConnectorRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                UPDATE connectors SET
                    slug = %s, display_name = %s, description = %s,
                    status = %s, status_reason = %s, access_mode = %s,
                    owner_user_id = %s,
                    scopes = %s::jsonb, last_sync_at = %s,
                    last_error_at = %s, updated_at = %s, vault_ref = %s
                WHERE tenant_id = %s AND id = %s
                """,
                (
                    record.slug,
                    record.display_name,
                    record.description,
                    record.status,
                    record.status_reason,
                    record.access_mode.value,
                    record.owner_user_id,
                    _jsonb([s.model_dump() for s in record.scopes]),
                    record.last_sync_at,
                    record.last_error_at,
                    record.updated_at,
                    record.vault_ref,
                    record.tenant_id,
                    record.id,
                ),
            )
        return record

    def upsert_from_mcp_registration(
        self, mcp_input: McpUpsertInput
    ) -> ConnectorRecord:
        """Write-through helper — denormalize from an MCP registration.

        Same substitution semantics as the in-memory adapter: existing
        rows are looked up by ``existing_id`` if given, falling back to
        the natural key ``(tenant_id, owner_user_id, slug)``. The
        lookup + write share one connection — re-entrant when the
        service already opened ``store.transaction()``, or a fresh
        transaction stamped with the input's tenant otherwise.
        """

        with self.transaction(org_id=mcp_input.tenant_id):
            existing: ConnectorRecord | None = None
            if mcp_input.existing_id is not None:
                existing = self.get_connector(
                    tenant_id=mcp_input.tenant_id,
                    connector_id=mcp_input.existing_id,
                )
            if existing is None:
                existing = self.get_by_owner_and_slug(
                    tenant_id=mcp_input.tenant_id,
                    owner_user_id=mcp_input.owner_user_id,
                    slug=mcp_input.slug,
                )
            if existing is None:
                record = ConnectorRecord(
                    tenant_id=mcp_input.tenant_id,
                    slug=mcp_input.slug,
                    display_name=mcp_input.display_name,
                    description=mcp_input.description,
                    status=mcp_input.status,
                    status_reason=mcp_input.status_reason,
                    owner_user_id=mcp_input.owner_user_id,
                    scopes=list(mcp_input.scopes),
                    last_sync_at=mcp_input.last_sync_at,
                    last_error_at=mcp_input.last_error_at,
                    vault_ref=mcp_input.vault_ref,
                )
                return self.insert_connector(record)
            record = existing.model_copy(
                update={
                    "display_name": mcp_input.display_name,
                    "description": mcp_input.description,
                    "status": mcp_input.status,
                    "status_reason": mcp_input.status_reason,
                    "scopes": list(mcp_input.scopes),
                    "last_sync_at": mcp_input.last_sync_at,
                    "last_error_at": mcp_input.last_error_at,
                    "vault_ref": mcp_input.vault_ref,
                    "updated_at": _now(),
                }
            )
            return self.update_connector(record)

    # -- audit --------------------------------------------------------

    def append_audit(self, record: ConnectorAuditRecord) -> ConnectorAuditRecord:
        # Per-tenant HMAC hash chain, signed through the shared
        # :class:`AuditChainSigner` — same path as
        # ``PostgresMcpStore.append_audit`` (``mcp_audit_events``) and
        # ``PostgresProjectsStore.append_audit`` (``project_audit_events``).
        # We take a per-(table, tenant) advisory xact lock, read the chain
        # head, then sign seq/prev_hash/signature/key_version over the
        # canonical business payload and insert. The chain columns are
        # DB-only; the ``ConnectorAuditRecord`` model (``extra='forbid'``)
        # does not carry them, so the returned record is unchanged.
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        payload = _connector_audit_payload(record)
        with self._cursor(tenant_id=record.tenant_id) as cur:
            _take_audit_chain_lock(
                cur, table="connector_audit_events", org_id=record.tenant_id
            )
            cur.execute(
                """
                SELECT seq, signature
                  FROM connector_audit_events
                 WHERE tenant_id = %s
                 ORDER BY seq DESC NULLS LAST
                 LIMIT 1
                """,
                (record.tenant_id,),
            )
            head = cur.fetchone()
            last_seq, prev_hash = _chain_head(head)
            seq = last_seq + 1
            sig = signer.sign(prev_hash=prev_hash, payload=payload)
            cur.execute(
                """
                INSERT INTO connector_audit_events (
                    audit_id, tenant_id, actor_user_id, action, target_kind,
                    target_id, before_state, after_state, correlation_id, ts,
                    seq, prev_hash, signature, key_version
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,
                    %s,%s,%s,%s
                )
                """,
                (
                    record.audit_id,
                    record.tenant_id,
                    record.actor_user_id,
                    record.action,
                    record.target_kind,
                    record.target_id,
                    _jsonb(record.before_state),
                    _jsonb(record.after_state),
                    record.correlation_id,
                    record.ts,
                    seq,
                    sig.prev_hash,
                    sig.signature,
                    sig.key_version,
                ),
            )
        return record

    def list_audit_for_connector(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorAuditRecord, ...], str | None]:
        offset = _decode_cursor(cursor)
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM connector_audit_events
                WHERE tenant_id = %s AND target_id = %s
                ORDER BY ts DESC, audit_id DESC
                OFFSET %s LIMIT %s
                """,
                (tenant_id, connector_id, offset, limit + 1),
            )
            rows = cur.fetchall()
        has_more = len(rows) > limit
        page = tuple(_row_to_conn_audit(r) for r in rows[:limit])
        next_cursor = str(offset + limit) if has_more else None
        return page, next_cursor


# ---------------------------------------------------------------------------
# Row mapping + Postgres helpers
# ---------------------------------------------------------------------------


def _jsonb(value: Any) -> str | None:
    """Serialise a JSON-able value for a ``%s::jsonb`` placeholder.

    ``None`` stays ``NULL`` (distinct from a JSON ``null``); everything
    else is ``json.dumps``-ed so psycopg binds a text param the cast
    turns into JSONB.
    """

    if value is None:
        return None
    return json.dumps(value)


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_connector(row: dict[str, Any]) -> ConnectorRecord:
    data = dict(row)
    scopes = _coerce_json(data.get("scopes")) or []
    data["scopes"] = [ConnectorScopeEntry.model_validate(s) for s in scopes]
    return ConnectorRecord.model_validate(data)


def _row_to_conn_audit(row: dict[str, Any]) -> ConnectorAuditRecord:
    data = dict(row)
    for key in ("before_state", "after_state"):
        if data.get(key) is not None:
            data[key] = _coerce_json(data[key])
    # Drop chain columns the Pydantic model doesn't declare.
    for key in ("seq", "prev_hash", "signature", "key_version"):
        data.pop(key, None)
    return ConnectorAuditRecord.model_validate(data)


def _connector_audit_payload(record: ConnectorAuditRecord) -> dict[str, Any]:
    """Canonical business payload signed into the audit hash chain.

    Mirrors the payload shape ``PostgresProjectsStore.append_audit`` builds
    for ``project_audit_events`` — the exact fields that must not change
    without breaking verification. Chain columns (seq/prev_hash/signature/
    key_version) are intentionally excluded; they live in the envelope the
    :class:`AuditChainSigner` wraps around this payload.
    """

    return {
        "audit_id": record.audit_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "action": record.action,
        "target_kind": record.target_kind,
        "target_id": record.target_id,
        "before_state": record.before_state,
        "after_state": record.after_state,
        "correlation_id": record.correlation_id,
        "ts": record.ts,
    }


def _chain_head(head: Any) -> tuple[int, bytes | None]:
    """Read ``(last_seq, prev_hash)`` from the chain-head row (or empty chain).

    ``head`` is the ``SELECT seq, signature ... ORDER BY seq DESC`` row (a
    mapping) or ``None`` when the tenant's chain is empty. Matches the head
    decoding in ``PostgresMcpStore.append_audit``.
    """

    if not head:
        return 0, None
    last_seq = int(head["seq"]) if head.get("seq") is not None else 0
    prev_hash = bytes(head["signature"]) if head.get("signature") is not None else None
    return last_seq, prev_hash


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_audit_rows(
    records: Iterable[ConnectorAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[ConnectorAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a multi-row write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "ConnectorAccessMode",
    "ConnectorAuditRecord",
    "ConnectorRecord",
    "ConnectorScopeEntry",
    "ConnectorsStore",
    "InMemoryConnectorsStore",
    "McpUpsertInput",
    "PostgresConnectorsStore",
    "iter_audit_rows",
]
