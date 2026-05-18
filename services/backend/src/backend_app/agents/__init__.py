"""Agents destination (Phase 8 P8-A1) — CRUD + ACL + canonical wire contract.

This package owns the data model for the Agents app-store surface. P8-A1
ships the CRUD + ACL + tenant-isolated read/write paths; the operational
endpoints land in adjacent sub-PRDs:

  * P8-A2 — version snapshots (POST /v1/agents/<id>/versions + GET list).
  * P8-A3 — install / uninstall / disable + per-user override layer.
  * P8-A4 — usage aggregation (read-only projection over the existing
    ``runtime_model_call_usage`` tracker in ai-backend).
  * P8-A5 — api-types deltas across-the-board (index.ts re-exports +
    cross-destination type touches).

Authorization (agents-prd §6.2 + cross-audit §1.3 binding):

  * ``system`` + ``community`` agents — readable by every tenant member;
    PATCH rejected with 409 ``agent_origin_immutable`` (must duplicate
    first; see P8-A2 §4.10).
  * ``custom`` agents — owner-only writes; tenant members who installed
    the agent get read access; non-owners non-installers see 404 (cross-
    audit §1.3 master rule: existence not leaked).
  * Admins (tenant role ``admin`` / ``owner``) get a compliance read on
    every row, audited at the route layer.

Wire shape is canonical at ``packages/api-types/src/agents.ts``; the
Python mirrors live in ``agents.routes``.
"""

from __future__ import annotations

from backend_app.agents.routes import register_agents_routes
from backend_app.agents.service import (
    AgentConflict,
    AgentForbidden,
    AgentInvalidRequest,
    AgentNotFound,
    AgentsService,
)
from backend_app.agents.store import (
    AgentAuditRecord,
    AgentInstallRecord,
    AgentRecord,
    AgentVersionRecord,
    AgentsStore,
    InMemoryAgentsStore,
)


__all__ = [
    "AgentAuditRecord",
    "AgentConflict",
    "AgentForbidden",
    "AgentInstallRecord",
    "AgentInvalidRequest",
    "AgentNotFound",
    "AgentRecord",
    "AgentVersionRecord",
    "AgentsService",
    "AgentsStore",
    "InMemoryAgentsStore",
    "register_agents_routes",
]
