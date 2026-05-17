"""Routines destination (Phase 5 P5-A1) — CRUD + ACL + state machine + quota + audit.

Public surface: ``GET /v1/routines``, ``GET /v1/routines/{id}``,
``POST /v1/routines``, ``PATCH /v1/routines/{id}``,
``DELETE /v1/routines/{id}``, and ``POST /v1/routines/{id}/run``
(manual fire). Identity is the verified session caller; tenant
isolation is enforced at every store call.

Wire shape is canonical at ``packages/api-types/src/routines.ts``;
the Python mirrors live in ``routines.routes``. Routes wire ACL +
audit + state-machine + quota via ``routines.service`` so the route
layer stays presentation-only.

Authorization (cross-audit §1.3, routines-prd §7):

* Owner-only writes (PATCH / DELETE / status transitions / activation).
* Reads: owner OR (project_id member when project_id is set) OR
  tenant admin compliance reads.
* Non-readers see 404 (not 403) to avoid leaking existence
  cross-tenant or cross-user.
* Manual-fire ACL: owner by default; widened by
  ``routine.permissions.manual_fire`` to ``"project_members"`` or
  ``"tenant"`` (cross-audit §9.7 Q2). Routine owners can always
  manual-fire regardless of the override.

State machine (api-types/src/routines.ts):

    draft ──activate──▶ active ──pause──▶ paused
                            │                ▲
                            ├──error──▶ errored
                            │                │
    (any) ──reset──▶ draft  ◀────────────────┘

Errored routines must be reset to draft (so the owner edits the
definition) before re-activating. Invalid moves return 409.

Quota (cross-audit §9.7 Q8): 100 ACTIVE routines per USER (not per
tenant). Enforced at create + at any state-machine transition
ending in active.

Routine fires (P5-A1 metadata):

* ``routine_fires`` table records each manual / scheduler / webhook
  fire's metadata; the actual run record lives in ai-backend via
  ``run.source.kind = "routine"`` (cross-audit §9.7 token-usage).
* ``POST /v1/routines/{id}/run`` writes the fire row + audit and
  returns ``{fire_id, run_id}``. The downstream run handoff is
  P5-A2's deliverable (run-coordinator).

Sub-PRDs landing in sibling Phase 5 waves:

* P5-A2 — scheduler (claim-queue, cron resolution, missed-fire
  catch-up) + run-coordinator (ai-backend handoff).
* P5-A3 — webhook ingest router (separate auth-shape; ``X-Atlas-
  Routine-Secret`` header + IP allowlist + HMAC-of-payload).
* P5-A4 — permission intersection at fire time (auto-pause +
  Inbox CTA on shrinkage).
* P5-B1/B2/B3 — chat-surface destination, editor, panel.
* P5-C — frontend wiring.
"""

from __future__ import annotations

from backend_app.routines.routes import register_routines_routes
from backend_app.routines.service import (
    ACTIVE_ROUTINES_PER_USER_LIMIT,
    InMemoryProjectMembershipAdapter,
    ProjectMembershipPort,
    RoutineForbidden,
    RoutineInvalidRequest,
    RoutineInvalidTransition,
    RoutineNotFound,
    RoutineQuotaExceeded,
    RoutinesService,
)
from backend_app.routines.store import (
    InMemoryRoutinesStore,
    RoutineAuditRecord,
    RoutineFireRecord,
    RoutineRecord,
    RoutinesStore,
)

__all__ = [
    "ACTIVE_ROUTINES_PER_USER_LIMIT",
    "InMemoryProjectMembershipAdapter",
    "InMemoryRoutinesStore",
    "ProjectMembershipPort",
    "RoutineAuditRecord",
    "RoutineFireRecord",
    "RoutineForbidden",
    "RoutineInvalidRequest",
    "RoutineInvalidTransition",
    "RoutineNotFound",
    "RoutineQuotaExceeded",
    "RoutineRecord",
    "RoutinesService",
    "RoutinesStore",
    "register_routines_routes",
]
