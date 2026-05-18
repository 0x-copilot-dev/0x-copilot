"""Tools destination (Phase 10) — catalog + CRUD + ACL + audit + usage projection + SSE.

Public surface: ``GET /v1/tools``, ``GET /v1/tools/{id}``,
``POST /v1/tools``, ``PATCH /v1/tools/{id}``,
``POST /v1/tools/{id}/test``, ``POST /v1/tools/{id}/disable`` /
``…/enable``, ``DELETE /v1/tools/{id}``,
``GET /v1/tools/{id}/invocations``, ``GET /v1/tools/{id}/usage``,
``GET /v1/tools/stream`` (SSE).

Internal routes (service-token-gated, consumed by ai-backend):
``POST /internal/v1/tools/by_ids``,
``POST /internal/v1/tools/{id}/invocations``,
``POST /internal/v1/tools/{id}/error``.

Wire shape is canonical at ``packages/api-types/src/tools.ts``; the
Python mirrors live in ``tools.routes``.

Authorization (tools-prd §6 + cross-audit §1.3):

* **Reads.** Tenant-readable when ``project_id`` is null; OWNER or
  project-member (via the canonical ``backend_app.projects.acl.is_member``
  predicate) OR tenant admin (compliance read) when set.
* **Writes.** Owner OR tenant admin. Project members do NOT mutate.
* **Cross-tenant.** Tenant scoping is the verified bearer's tenant claim.
* Non-readers see 404, never 403.

Usage / invocations (tools-prd §3.2):

* Usage is computed read-time as a GROUP BY over the existing
  ``runtime_tool_invocations`` table. There is NO parallel
  ``tool_usage_daily`` table — TU-1 single-tracker invariant
  (cross-audit §5.5) preserved.

Sandbox / test-call:

* P10-A2 ships ``POST /v1/tools/{id}/test`` as a 501 stub — the audit
  row lands, but the executor returns 501 ``code_sandbox_not_yet_wired``.
  P10-A3 lands the sandbox executor and flips the route to a real
  ``TestToolCallResponse``.
"""

from __future__ import annotations

from backend_app.tools.routes import (
    register_tool_internal_routes,
    register_tool_routes,
)
from backend_app.tools.service import (
    ERROR_THRESHOLD_DEFAULT,
    ToolConflict,
    ToolForbidden,
    ToolInvalidRequest,
    ToolNotFound,
    ToolNotImplemented,
    ToolsService,
)
from backend_app.tools.sse import (
    InMemoryToolsActivityBus,
    ToolsActivityBus,
    register_tool_sse_routes,
)
from backend_app.tools.store import (
    InMemoryToolsStore,
    ToolAuditRecord,
    ToolInvocationRecord,
    ToolRecord,
    ToolsStore,
)

__all__ = [
    "ERROR_THRESHOLD_DEFAULT",
    "InMemoryToolsActivityBus",
    "InMemoryToolsStore",
    "ToolAuditRecord",
    "ToolConflict",
    "ToolForbidden",
    "ToolInvalidRequest",
    "ToolInvocationRecord",
    "ToolNotFound",
    "ToolNotImplemented",
    "ToolRecord",
    "ToolsActivityBus",
    "ToolsService",
    "ToolsStore",
    "register_tool_internal_routes",
    "register_tool_routes",
    "register_tool_sse_routes",
]
