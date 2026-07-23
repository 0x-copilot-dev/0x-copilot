"""Connectors service — ACL + audit + thin wrappers over the existing MCP path.

The destination is a denormalized read model over the existing MCP
registration + token vault path (connectors-prd §3.2). This module wraps
the existing :class:`backend_app.service.McpRegistryService` for state-
changing actions and exposes the destination-shaped read endpoints.

Why a service layer at all if the writes are pass-through?

1. **Audit + correlation.** Every state change writes one
   ``connector.*`` audit row through this module so the destination's
   "Audit" tab and the SIEM export share one source of truth.
2. **ACL.** Reads are tenant-member; writes are owner-or-admin. The
   route layer is presentation-only and does not enforce either.
3. **Catalog projection.** The "Available" tab needs the catalog file
   merged in with the installed rows. Single place to do that.
4. **Consumer projection.** ``ConnectorDetailResponse.consumers`` joins
   the Agents / Tools / Projects destinations. Compose-time is here.

The :class:`McpRegistryService` import is intentional and the DRY
substitution point: zero new OAuth code in the destination.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from backend_app.connectors.store import (
    ConnectorAccessMode,
    ConnectorAuditRecord,
    ConnectorRecord,
    ConnectorScopeEntry,
    ConnectorsStore,
    McpUpsertInput,
)
from backend_app.contracts import McpAuthMode, McpAuthState, McpServerRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Roles with tenant-admin authority (matches the inbox / todos conventions).
_ADMIN_ROLES = frozenset({"admin", "owner"})

_VALID_STATUSES = frozenset({"connected", "disconnected", "error", "expired"})


class ConnectorNotFound(Exception):
    """Raised when the connector doesn't exist OR caller cannot read.

    404-not-403 collapses both branches to one exception so the route
    layer cannot distinguish them — the response is always 404.
    """


class ConnectorForbidden(Exception):
    """Raised when the caller can read but cannot write (403)."""


class ConnectorInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


# ---------------------------------------------------------------------------
# Catalog projection — loaded once at boot from ``catalog.yaml``.
# ---------------------------------------------------------------------------


class ConnectorCatalogEntry:
    """One row in ``catalog.yaml``.

    Lightweight class (not pydantic) because the catalog is loaded once
    at boot and never mutated. The wire shape lives on the route layer;
    this object is the in-memory representation.
    """

    __slots__ = ("slug", "display_name", "description", "icon_hint")

    def __init__(
        self,
        *,
        slug: str,
        display_name: str,
        description: str = "",
        icon_hint: str | None = None,
    ) -> None:
        self.slug = slug
        self.display_name = display_name
        self.description = description
        self.icon_hint = icon_hint


def load_catalog(path: Path | None = None) -> tuple[ConnectorCatalogEntry, ...]:
    """Load the available-to-install catalog from YAML.

    Defaults to the package-local ``catalog.yaml`` if no path is given;
    tests can inject a fixture path. Failure to read the file is fatal
    in production (the FE renders an empty Available tab otherwise);
    callers that want a soft-fail (tests, dev with no catalog) should
    pass an empty list explicitly through the service constructor.
    """

    resolved = path or Path(__file__).resolve().parent / "catalog.yaml"
    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    entries = raw.get("entries") or []
    out: list[ConnectorCatalogEntry] = []
    for row in entries:
        out.append(
            ConnectorCatalogEntry(
                slug=str(row["slug"]),
                display_name=str(row.get("display_name", row["slug"])),
                description=str(row.get("description", "")),
                icon_hint=row.get("icon_hint"),
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Consumer projection — joins agents / tools / projects (read-only)
# ---------------------------------------------------------------------------


class ConsumerProjectionPort:
    """Adapter for the consumer projection on the detail endpoint.

    The destination renders "Used by N agents / M tools / K projects"
    on the detail page. The join is over the existing Agents / Tools /
    Projects destinations; this port keeps the connectors module
    decoupled from those modules (they don't exist in this Phase 11
    branch yet — agents lands in Phase 8, tools in Phase 10). The
    default returns empty lists; production deploys inject a real
    adapter at boot.

    Same Protocol-like shape as :class:`ProjectMembershipPort` — duck-
    typing keeps the import graph clean.
    """

    def list_agents(
        self, *, tenant_id: str, connector_id: str
    ) -> tuple[dict[str, str], ...]:
        return ()

    def list_tools(
        self, *, tenant_id: str, connector_id: str
    ) -> tuple[dict[str, str], ...]:
        return ()

    def list_projects(
        self, *, tenant_id: str, connector_id: str
    ) -> tuple[dict[str, str], ...]:
        return ()

    def count_chats_with_grant(self, *, tenant_id: str, connector_id: str) -> int:
        return 0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ConnectorsService:
    """Composition of the connectors store + catalog + consumer projection."""

    def __init__(
        self,
        *,
        store: ConnectorsStore,
        catalog: Iterable[ConnectorCatalogEntry] | None = None,
        consumer_projection: ConsumerProjectionPort | None = None,
    ) -> None:
        self._store = store
        self._catalog: tuple[ConnectorCatalogEntry, ...] = (
            tuple(catalog) if catalog is not None else ()
        )
        self._consumers = consumer_projection or ConsumerProjectionPort()

    @property
    def catalog(self) -> tuple[ConnectorCatalogEntry, ...]:
        return self._catalog

    # -- reads ---------------------------------------------------------

    def get_connector(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
    ) -> ConnectorRecord:
        record = self._store.get_connector(
            tenant_id=tenant_id, connector_id=connector_id
        )
        if record is None or not self._can_read(record, caller_user_id, caller_roles):
            raise ConnectorNotFound(connector_id)
        return record

    def list_connectors(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        slugs: tuple[str, ...] | None = None,
        installed: bool | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorRecord, ...], str | None]:
        """Tenant-scoped list. Admins see every owner; non-admins see only their own."""

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        owner_filter = None if admin else caller_user_id
        records, next_cursor = self._store.list_connectors(
            tenant_id=tenant_id,
            statuses=statuses,
            slugs=slugs,
            owner_user_id=owner_filter,
            q=q,
            cursor=cursor,
            limit=limit,
        )
        if installed is not None and installed is False:
            # "Available only" — filter the installed-set out by returning
            # an empty installed tuple; the route layer reads the catalog
            # for the available section.
            return (), next_cursor
        return records, next_cursor

    def project_consumers(self, *, tenant_id: str, connector_id: str) -> dict[str, Any]:
        """Build the ``ConnectorDetailResponse.consumers`` projection.

        Each list is shaped as the wire-side ``ItemRef`` (``kind`` + ``id``)
        because that's what the FE's ``<ItemLink>`` registry consumes.
        The port returns plain dicts so the route layer doesn't need to
        construct branded brand objects.
        """

        return {
            "agents": tuple(
                self._consumers.list_agents(
                    tenant_id=tenant_id, connector_id=connector_id
                )
            ),
            "tools": tuple(
                self._consumers.list_tools(
                    tenant_id=tenant_id, connector_id=connector_id
                )
            ),
            "projects": tuple(
                self._consumers.list_projects(
                    tenant_id=tenant_id, connector_id=connector_id
                )
            ),
            "chats_with_grant": self._consumers.count_chats_with_grant(
                tenant_id=tenant_id, connector_id=connector_id
            ),
        }

    # -- writes --------------------------------------------------------

    def write_through_from_mcp(
        self,
        *,
        mcp_input: McpUpsertInput,
        actor_user_id: str,
        action: str,
        correlation_id: str | None = None,
    ) -> ConnectorRecord:
        """The DRY substitution point — write-through from an MCP registration.

        Composed inside ``with store.transaction():`` so the
        denormalized row + the audit row land atomically. Audit-in-
        transaction discipline (cross-audit C3) is the same shape as
        the inbox/todos paths.
        """

        # ``org_id`` stamps ``app.current_org_id`` on the shared write
        # connection so the 0044 RLS WITH CHECK policies pass for a
        # non-superuser role — same discipline as ``ProjectsService``
        # (proven live by test_connectors_store_live.py; without it the
        # INSERT dies with "violates row-level security policy").
        with self._store.transaction(org_id=mcp_input.tenant_id):
            record = self._store.upsert_from_mcp_registration(mcp_input)
            self._store.append_audit(
                ConnectorAuditRecord(
                    tenant_id=record.tenant_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_id=record.id,
                    after_state=_safe_dump(record),
                    correlation_id=correlation_id,
                )
            )
        return record

    def disconnect(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
    ) -> ConnectorRecord:
        """Flip status to ``disconnected``; preserve consumers.

        The actual upstream-token revocation is the existing MCP path's
        job (it calls into :class:`TokenVault` to wipe the entry). This
        method is the destination-level write-through that updates the
        denormalized row + appends the audit. Production wiring calls
        the MCP service first (out-of-tree in this branch); the service
        method is idempotent so a retry after a partial failure
        re-converges.
        """

        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            connector_id=connector_id,
        )
        if existing.status == "disconnected":
            return existing
        new_record = existing.model_copy(
            update={
                "status": "disconnected",
                "status_reason": "user_requested_disconnect",
                "updated_at": _now(),
            }
        )
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction(org_id=tenant_id):
            stored = self._store.update_connector(new_record)
            self._store.append_audit(
                ConnectorAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="connector.disconnected",
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                )
            )
        return stored

    def refresh_token(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
    ) -> ConnectorRecord:
        """Manual refresh wrapper — sets ``status=connected`` + bumps last_sync_at.

        The actual provider-token refresh is the existing MCP path's
        :func:`McpRegistryService._require_valid_token` call. This
        method is the destination-level audit + status projection. A
        provider 4xx flips status to ``error`` (and writes a
        ``connector.error`` row); a 200 flips to ``connected``.
        """

        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            connector_id=connector_id,
        )
        new_record = existing.model_copy(
            update={
                "status": "connected",
                "status_reason": None,
                "last_sync_at": _now(),
                "last_error_at": None,
                "updated_at": _now(),
            }
        )
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction(org_id=tenant_id):
            stored = self._store.update_connector(new_record)
            self._store.append_audit(
                ConnectorAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="connector.token_refreshed",
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                )
            )
        return stored

    def patch_scopes(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
        scopes: tuple[ConnectorScopeEntry, ...],
    ) -> ConnectorRecord:
        """Update the scope set + emit an audit row.

        Substitution: when scopes shrink or expand, the production
        wiring fires a re-OAuth round-trip through the existing
        :func:`McpRegistryService.start_auth` path; the response is a
        ``202`` with the ``reauth_url`` from the existing OAuth client.
        This method updates the destination's denormalized row + emits
        ``connector.scope_added`` / ``connector.scope_removed`` audit
        rows so the SIEM stream sees the user's intent before the
        re-OAuth resolves.
        """

        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            connector_id=connector_id,
        )

        prior_granted = {s.scope for s in existing.scopes if s.granted}
        next_granted = {s.scope for s in scopes if s.granted}
        added = next_granted - prior_granted
        removed = prior_granted - next_granted
        if not added and not removed:
            # No-op; don't emit audit rows for an empty diff.
            return existing

        new_record = existing.model_copy(
            update={
                "scopes": list(scopes),
                "updated_at": _now(),
            }
        )
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction(org_id=tenant_id):
            stored = self._store.update_connector(new_record)
            if added:
                self._store.append_audit(
                    ConnectorAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="connector.scope_added",
                        target_id=stored.id,
                        before_state=before_state,
                        after_state=after_state,
                        correlation_id=",".join(sorted(added)),
                    )
                )
            if removed:
                self._store.append_audit(
                    ConnectorAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="connector.scope_removed",
                        target_id=stored.id,
                        before_state=before_state,
                        after_state=after_state,
                        correlation_id=",".join(sorted(removed)),
                    )
                )
        return stored

    def set_access_mode(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
        access_mode: ConnectorAccessMode,
    ) -> ConnectorRecord:
        """Set the per-connector agent access mode (PRD-06 D2).

        Permission boundary (stated once): the connector's ``owner_user_id``,
        or any caller holding ``admin``/``owner`` in the tenant, may change
        the mode. Nobody else — including other tenant members who can *read*
        the row. ``_authorize_write`` enforces this and 404s (not 403) a
        cross-tenant id so existence never leaks.

        Idempotent: a PATCH whose value equals the stored value returns the
        unchanged row and writes **zero** audit rows. A real change writes
        exactly one ``connector.access_mode_changed`` audit row atomically
        with the row update, carrying ``correlation_id="{previous}->{next}"``
        so the tamper-evident chain records who changed a connector's
        authority, when, and from what to what.
        """

        # Coerce so callers may pass the enum or its string value; an invalid
        # value raises ``ValueError`` (the route maps it to 400 before this).
        access_mode = ConnectorAccessMode(access_mode)
        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            connector_id=connector_id,
        )
        if existing.access_mode == access_mode:
            # No-op: don't emit audit noise for a set-to-current-value.
            return existing

        previous = existing.access_mode
        new_record = existing.model_copy(
            update={
                "access_mode": access_mode,
                "updated_at": _now(),
            }
        )
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction(org_id=tenant_id):
            stored = self._store.update_connector(new_record)
            self._store.append_audit(
                ConnectorAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="connector.access_mode_changed",
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                    correlation_id=f"{previous.value}->{access_mode.value}",
                )
            )
        return stored

    def mark_error(
        self,
        *,
        tenant_id: str,
        connector_id: str,
        actor_user_id: str,
        reason: str,
    ) -> ConnectorRecord:
        """Token-refresh worker hook — flips a connector to ``error``.

        Not exposed on a public route (the worker calls this directly);
        the destination's SSE stream picks up the resulting
        ``connector.status_changed`` event via the route layer's bus
        publish.
        """

        existing = self._store.get_connector(
            tenant_id=tenant_id, connector_id=connector_id
        )
        if existing is None:
            raise ConnectorNotFound(connector_id)
        new_record = existing.model_copy(
            update={
                "status": "error",
                "status_reason": reason,
                "last_error_at": _now(),
                "updated_at": _now(),
            }
        )
        before_state = _safe_dump(existing)
        after_state = _safe_dump(new_record)
        with self._store.transaction(org_id=tenant_id):
            stored = self._store.update_connector(new_record)
            self._store.append_audit(
                ConnectorAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    action="connector.error",
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                )
            )
        return stored

    # -- audit log -----------------------------------------------------

    def list_audit(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ConnectorAuditRecord, ...], str | None]:
        # Read the connector first so the 404-not-403 rule fires before
        # we leak audit-row existence to a non-reader.
        self.get_connector(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            connector_id=connector_id,
        )
        return self._store.list_audit_for_connector(
            tenant_id=tenant_id,
            connector_id=connector_id,
            cursor=cursor,
            limit=limit,
        )

    # -- helpers -------------------------------------------------------

    def _can_read(
        self,
        record: ConnectorRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        """Tenant member by default. Owner sees their own; admins see all.

        Tenant membership is implicit — the store-layer query is already
        scoped to ``tenant_id``, and the route layer derives that from
        the verified bearer. A caller outside the tenant never reaches
        this method (get_connector returns None first → 404).
        """

        if record.owner_user_id == caller_user_id:
            return True
        if any(role in _ADMIN_ROLES for role in caller_roles):
            return True
        # Non-owner tenant members can READ the row (compliance lens at
        # the detail level is admin-only — but the row itself is visible
        # so the FE can render the connector card in the Used By tab).
        return True

    def _authorize_write(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        connector_id: str,
    ) -> ConnectorRecord:
        existing = self._store.get_connector(
            tenant_id=tenant_id, connector_id=connector_id
        )
        if existing is None:
            raise ConnectorNotFound(connector_id)
        if existing.owner_user_id == caller_user_id:
            return existing
        if any(role in _ADMIN_ROLES for role in caller_roles):
            return existing
        # Read access established (tenant member) but writes are
        # owner-or-admin only.
        raise ConnectorForbidden(connector_id)


# ---------------------------------------------------------------------------
# MCP → connector projection (Decision D1 write-through, PR-E.3)
# ---------------------------------------------------------------------------

# Catalog installs use ``server_id = "seed:<slug>"``; strip the prefix so
# the connector row's slug matches the connectors catalog + the FE's
# installed-slug filtering. Custom (register-by-URL) servers fall back to
# the registry's stable ``name``.
_MCP_SEED_PREFIX = "seed:"


def mcp_connector_slug(record: McpServerRecord) -> str:
    """Connector-row slug for an MCP server record (stable natural key)."""

    if record.server_id.startswith(_MCP_SEED_PREFIX):
        return record.server_id[len(_MCP_SEED_PREFIX) :]
    return record.name


# Honest auth_state → connector status projection. The connector status
# taxonomy is CLOSED (api-types ``ConnectorStatus``: connected /
# disconnected / error / expired — connectors-prd §1.6), so states that
# have no first-class value map into the closed set with a
# ``status_reason`` carrying the precise MCP auth state:
#
# * ``authenticated``     → ``connected`` (token in vault, usable).
# * ``auth_skipped``      → ``connected`` + reason (user chose no-auth use).
# * no-auth servers       → ``connected`` (nothing to authenticate).
# * ``auth_pending``      → ``disconnected`` + ``auth_pending`` (OAuth
#   round-trip in flight; not usable yet — there is no "pending" status).
# * ``unauthenticated``   → ``disconnected`` + ``unauthenticated``.
# * ``auth_failed``       → ``error`` + ``auth_failed``.
# * ``auth_unsupported``  → ``error`` + ``auth_unsupported``.
# * disabled servers      → ``disconnected`` + ``disabled`` (wins over
#   auth state — a disabled server is unusable regardless).
_AUTH_STATE_TO_STATUS: dict[McpAuthState, tuple[str, str | None]] = {
    McpAuthState.AUTHENTICATED: ("connected", None),
    McpAuthState.AUTH_SKIPPED: ("connected", "auth_skipped"),
    McpAuthState.AUTH_PENDING: ("disconnected", "auth_pending"),
    McpAuthState.UNAUTHENTICATED: ("disconnected", "unauthenticated"),
    McpAuthState.AUTH_FAILED: ("error", "auth_failed"),
    McpAuthState.AUTH_UNSUPPORTED: ("error", "auth_unsupported"),
}


def project_mcp_status(record: McpServerRecord) -> tuple[str, str | None]:
    """Map an MCP server's (enabled, auth_mode, auth_state) to connector status."""

    if not record.enabled:
        return "disconnected", "disabled"
    if record.auth_mode == McpAuthMode.NONE:
        return "connected", None
    return _AUTH_STATE_TO_STATUS.get(
        record.auth_state,
        ("error", f"unknown_auth_state:{record.auth_state}"),
    )


def mcp_upsert_input_from_server(
    record: McpServerRecord,
    *,
    existing: ConnectorRecord | None = None,
) -> McpUpsertInput:
    """Project an :class:`McpServerRecord` into the write-through input.

    This is the service-layer projection the store deliberately does not
    do (the store never sees ``McpServerRecord`` — see the
    :class:`McpUpsertInput` docstring). ``existing`` is the current
    connector row when known: its ``last_sync_at`` / ``last_error_at``
    are preserved so an MCP-side write-through doesn't clobber the
    refresh-worker's sync bookkeeping, and its ``id`` is re-used so the
    destination row stays stable across upserts.
    """

    status, status_reason = project_mcp_status(record)
    granted = status == "connected"
    scope_values = record.required_scopes or record.default_scopes
    scopes = tuple(
        ConnectorScopeEntry(scope=scope, granted=granted, description="")
        for scope in scope_values
    )
    last_sync_at = existing.last_sync_at if existing is not None else None
    last_error_at = existing.last_error_at if existing is not None else None
    if status == "connected" and record.auth_state == McpAuthState.AUTHENTICATED:
        # An authenticated write-through means the OAuth exchange just
        # succeeded — record it as the last successful sync point.
        last_sync_at = record.updated_at
    if status == "error":
        last_error_at = _now()
    return McpUpsertInput(
        tenant_id=record.org_id,
        slug=mcp_connector_slug(record),
        owner_user_id=record.user_id,
        display_name=record.display_name,
        description=record.description,
        status=status,
        status_reason=status_reason,
        scopes=scopes,
        last_sync_at=last_sync_at,
        last_error_at=last_error_at,
        # Opaque pointer back to the vault's key space — the vault keys
        # token envelopes by (server_id, org, user); the connector row
        # never holds token bytes.
        vault_ref=f"mcp:{record.server_id}",
        existing_id=existing.id if existing is not None else None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_dump(record: ConnectorRecord) -> dict[str, Any]:
    """Dump the record to a JSON-serialisable dict for audit rows."""

    return record.model_dump(mode="json")


def validate_status(value: str) -> str:
    if value not in _VALID_STATUSES:
        raise ConnectorInvalidRequest(f"invalid_status:{value}")
    return value


__all__ = [
    "ConnectorCatalogEntry",
    "ConnectorForbidden",
    "ConnectorInvalidRequest",
    "ConnectorNotFound",
    "ConnectorsService",
    "ConsumerProjectionPort",
    "load_catalog",
    "mcp_connector_slug",
    "mcp_upsert_input_from_server",
    "project_mcp_status",
    "validate_status",
]
