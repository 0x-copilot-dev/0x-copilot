"""Agents destination (Phase 8) — CRUD + versions + installs + usage aggregation.

Slices land in parallel; each owns its own module under this package.

* **P8-A1** — ``store.py`` + ``service.py`` + ``routes.py``: catalog CRUD,
  ACL, owner-only writes, 404-not-403, slug uniqueness, soft-delete.
* **P8-A2** — ``versions.py``: immutable agent_version snapshots used by
  Routines §9.7 Q11's ``agent_version_pin`` field. Idempotent on
  ``(agent_id, state_hash)``. No PATCH/DELETE (405).
* **P8-A3** — ``installs.py``: per-user install + override layer with
  the fork-vs-overlay rule (instruction edits force ``duplicate()``;
  only model + permissions tweaks live in overrides). Narrow
  :class:`AgentSourcePort` protocol decouples from P8-A1's store.
* **P8-A4** — ai-backend ``agent_usage.py``: read-only aggregation over
  the existing ``runtime_model_call_usage`` tracker (no new tracker,
  no parallel table — preserves cross-audit §5.5 single-tracker invariant).
* **P8-A5** — api-types deltas (Project.default_agent_id cross-cut).

Authorization (agents-prd §6.2 + cross-audit §1.3 binding):

* ``system`` + ``community`` agents — readable tenant-wide; PATCH on
  origin immutability returns 409.
* ``custom`` agents — owner-only writes; installer reads; non-owner
  non-installer sees 404 (existence-not-leaked).
* Admins get compliance read on every row, audited.

Wire shape is canonical at ``packages/api-types/src/agents.ts``.
"""

from __future__ import annotations

from backend_app.agents.installs import (
    AgentCatalogRecord,
    AgentInstallOverrides,
    AgentInstallRow,
    AgentInstallStore,
    AgentSourcePort,
    DuplicateAgentResult,
    InMemoryAgentInstallStore,
    InMemoryAgentSource,
    OverridesValidationError,
    register_agent_install_routes,
    validate_overrides,
)
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
    "AgentCatalogRecord",
    "AgentConflict",
    "AgentForbidden",
    "AgentInstallOverrides",
    "AgentInstallRecord",
    "AgentInstallRow",
    "AgentInstallStore",
    "AgentInvalidRequest",
    "AgentNotFound",
    "AgentRecord",
    "AgentSourcePort",
    "AgentVersionRecord",
    "AgentsService",
    "AgentsStore",
    "DuplicateAgentResult",
    "InMemoryAgentInstallStore",
    "InMemoryAgentSource",
    "InMemoryAgentsStore",
    "OverridesValidationError",
    "register_agent_install_routes",
    "register_agents_routes",
    "validate_overrides",
]
