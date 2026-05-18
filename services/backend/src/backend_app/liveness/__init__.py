"""Liveness orchestrator — read-only aggregator for "is project X alive?".

Phase 6.5 sub-PRD §3 — THE single source of truth for project-liveness reads.
Consumed by:

* Archive endpoint (``DELETE /v1/projects/{id}``) — 409 if alive.
* Routine pre-fire validation (operator-triggered activate).
* Connector revoke pre-check.
* Template fork pre-validation (warning banner).

Hard rules from §3:

* READ-ONLY. No mutation of domain state. Allowed side-effect: a single
  audit row on cache-miss when the debug flag is on (off by default).
* 2-second TTL cache. Bounded by tenant active-project working set.
* Partial-failure tolerated — ``asyncio.gather(return_exceptions=True)``
  on every upstream. A failed source surfaces in ``details[].error``;
  the report still returns 200, never 500.
* Tenant-isolated. Cache key is ``(tenant_id, project_id)``.
* Public surface: ONE method — ``LivenessService.is_project_alive(...)``.
  Anything beyond aggregation belongs elsewhere.

LOC budget: ≤ 250 across the module. Public API has one method.
"""

from __future__ import annotations

from backend_app.liveness.routes import register_liveness_routes
from backend_app.liveness.service import (
    AiBackendLivenessClient,
    LivenessDetail,
    LivenessReport,
    LivenessService,
    LivenessSourceName,
    RoutinesLivenessReader,
    InboxLivenessReader,
)


__all__ = [
    "AiBackendLivenessClient",
    "InboxLivenessReader",
    "LivenessDetail",
    "LivenessReport",
    "LivenessService",
    "LivenessSourceName",
    "RoutinesLivenessReader",
    "register_liveness_routes",
]
