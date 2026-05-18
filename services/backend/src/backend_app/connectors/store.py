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

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connector_id() -> str:
    return f"conn_{uuid4().hex}"


def _audit_id() -> str:
    return f"audcon_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


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
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

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
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the postgres adapter without a
        # branch.
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
    "ConnectorAuditRecord",
    "ConnectorRecord",
    "ConnectorScopeEntry",
    "ConnectorsStore",
    "InMemoryConnectorsStore",
    "McpUpsertInput",
    "iter_audit_rows",
]
