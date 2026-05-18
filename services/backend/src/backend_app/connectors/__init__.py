"""Connectors destination (Phase 11) — CRUD + ACL + audit + SSE wrapping the existing MCP OAuth path.

Public surface: ``GET /v1/connectors``, ``GET /v1/connectors/{id}``,
``POST /v1/connectors/{slug}/start-oauth``,
``POST /v1/connectors/oauth-callback``,
``POST /v1/connectors/{id}/refresh``,
``POST /v1/connectors/{id}/disconnect``,
``PATCH /v1/connectors/{id}/scopes``,
``GET /v1/connectors/{id}/audit``, and
``GET /v1/connectors/stream`` (SSE).

The destination is a denormalized READ MODEL over the existing MCP
registration + token vault path — see ``connectors-prd.md`` §3.2. Writes
flow through ``McpRegistryService`` and ``TokenVault``; this module's
``ConnectorsService.write_through_from_mcp`` is the substitution point
that denormalizes the row + emits the destination-level audit row.

Authorization (cross-audit §1.3, connectors-prd §6.1):

* Reads: tenant member. 404-not-403 for out-of-tenant.
* Writes (refresh / disconnect / scope patch): owner OR tenant admin.
* Audit reads (``GET .../audit``): tenant member (admins see the full
  cross-owner view; non-admin owners see their own rows).

Audit (connectors-prd §6.2):

* ``connector.connected`` (write-through-from-mcp)
* ``connector.disconnected`` / ``connector.token_refreshed`` /
  ``connector.error`` / ``connector.scope_added`` / ``connector.scope_removed``

Webhook management (connectors-prd §4.10 + §9 — Routines §9.7 Q6 HMAC UX)
is owned by sub-phase P11-A3 (separate module).
"""

from __future__ import annotations

from backend_app.connectors.routes import register_connector_routes
from backend_app.connectors.service import (
    ConnectorCatalogEntry,
    ConnectorForbidden,
    ConnectorInvalidRequest,
    ConnectorNotFound,
    ConnectorsService,
    ConsumerProjectionPort,
    load_catalog,
)
from backend_app.connectors.sse import (
    ConnectorActivityBus,
    ConnectorEventEnvelope,
    ConnectorEventType,
    InMemoryConnectorActivityBus,
    register_connector_sse_routes,
)
from backend_app.connectors.store import (
    ConnectorAuditRecord,
    ConnectorRecord,
    ConnectorScopeEntry,
    ConnectorsStore,
    InMemoryConnectorsStore,
    McpUpsertInput,
)

__all__ = [
    "ConnectorActivityBus",
    "ConnectorAuditRecord",
    "ConnectorCatalogEntry",
    "ConnectorEventEnvelope",
    "ConnectorEventType",
    "ConnectorForbidden",
    "ConnectorInvalidRequest",
    "ConnectorNotFound",
    "ConnectorRecord",
    "ConnectorScopeEntry",
    "ConnectorsService",
    "ConnectorsStore",
    "ConsumerProjectionPort",
    "InMemoryConnectorActivityBus",
    "InMemoryConnectorsStore",
    "McpUpsertInput",
    "load_catalog",
    "register_connector_routes",
    "register_connector_sse_routes",
]
