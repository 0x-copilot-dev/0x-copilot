"""Routines destination (Phase 5) — CRUD + ACL + state machine + quota + audit + webhook ingest.

Public surface:

* CRUD (P5-A1): ``GET /v1/routines``, ``GET /v1/routines/{id}``,
  ``POST /v1/routines``, ``PATCH /v1/routines/{id}``,
  ``DELETE /v1/routines/{id}``, ``POST /v1/routines/{id}/run``.
* Webhook ingest (P5-A3): ``POST /v1/webhook/routines/{trigger_id}``
  (public; auth IS the secret + HMAC), ``POST /v1/routines/triggers/
  {trigger_id}/rotate-secret`` and ``GET /v1/routines/triggers/
  {trigger_id}/webhook/secret`` (owner-only).

Identity is the verified session caller; tenant isolation is enforced
at every store call.

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

Errored routines must be reset to draft before re-activating.
Invalid moves return 409.

Quota (cross-audit §9.7 Q8): 100 ACTIVE routines per USER.

Webhook (cross-audit §2.4 + §9.7 Q6):

* Per-trigger rotating secret with 7-day grace, optional CIDR
  allowlist, HMAC-of-payload signature.
* Audit on every hit (success + failed-auth) with
  ``context = { trigger_id, source_ip, auth_method, reason? }``.

Sub-PRDs landing in sibling Phase 5 waves:

* P5-A2 — scheduler (claim-queue, cron resolution, missed-fire
  catch-up) + run-coordinator (ai-backend handoff).
* P5-A4 — permission intersection at fire time (auto-pause +
  Inbox CTA on shrinkage).
* P5-B1/B2/B3 — chat-surface destination, editor, detail.
* P5-C — frontend wiring.
"""

from __future__ import annotations

from backend_app.routines.routes import register_routines_routes
from backend_app.routines.service import (
    ACTIVE_ROUTINES_PER_USER_LIMIT,
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
from backend_app.routines.webhook import (
    InMemoryRoutineWebhookStore,
    RoutineWebhookSecret,
    RoutineWebhookStore,
    RoutineWebhookValidator,
    WebhookAuthFailure,
    WebhookAuthResult,
    WebhookValidationError,
)
from backend_app.routines.webhook_routes import register_routines_webhook_routes

__all__ = [
    "ACTIVE_ROUTINES_PER_USER_LIMIT",
    "InMemoryRoutineWebhookStore",
    "InMemoryRoutinesStore",
    "RoutineAuditRecord",
    "RoutineFireRecord",
    "RoutineForbidden",
    "RoutineInvalidRequest",
    "RoutineInvalidTransition",
    "RoutineNotFound",
    "RoutineQuotaExceeded",
    "RoutineRecord",
    "RoutineWebhookSecret",
    "RoutineWebhookStore",
    "RoutineWebhookValidator",
    "RoutinesService",
    "RoutinesStore",
    "WebhookAuthFailure",
    "WebhookAuthResult",
    "WebhookValidationError",
    "register_routines_routes",
    "register_routines_webhook_routes",
]
