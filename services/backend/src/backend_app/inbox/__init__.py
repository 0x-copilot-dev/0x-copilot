"""Inbox destination (Phase 4) — CRUD + ACL + state machine + audit + SSE + producer.

Public surface: ``GET /v1/inbox``, ``GET /v1/inbox/{id}``,
``PATCH /v1/inbox/{id}``, ``POST /v1/inbox/bulk``,
``GET /v1/inbox/unread_count``, ``GET /v1/inbox/stream`` (SSE), and
``POST /internal/v1/inbox/items`` (service-token producer endpoint).
Identity is the verified session caller; tenant isolation is enforced
at every store call.

Wire shape is canonical at ``packages/api-types/src/inbox.ts``; the
Python mirrors live in ``inbox.routes``. Routes wire ACL + audit via
``inbox.service`` so the route layer stays presentation-only.

Authorization (cross-audit §1.3, inbox-prd §7):

* Recipient-only writes (mutating another user's inbox is forbidden;
  admin compliance reads cannot mutate).
* Reads: recipient OR (project_id member when project_id is set) OR
  tenant admin compliance reads.
* Non-readers see 404 (not 403) to avoid leaking existence
  cross-tenant or cross-user.

State machine (api-types/src/inbox.ts):

* ``unread → read`` / ``unread → snoozed`` / ``unread → dismissed``.
* Snooze requires future ``snoozed_until`` (cron wakes back to unread
  when the timestamp passes — wake worker lives in P4-A3).
* ``dismissed`` is terminal; producer revives via internal route
  (P4-A2 endpoint) using the same ``external_ref`` if needed.

Body split:

* List rows carry ``body_ref`` opaque pointer; body lives in the
  ``inbox_bodies`` table (inbox-prd §3 + §10).
* ``GET /v1/inbox/{id}`` lazy-loads the body markdown on detail mount.

SSE (P4-A3):

* ``GET /v1/inbox/stream`` emits ``event: inbox_event`` frames per
  ``(org_id, user_id)`` channel with monotonic ``sequence_no``;
  ``Last-Event-ID`` header (or ``?after_sequence=N`` fallback) resumes
  without replay. 30s heartbeats. P4-A1's mutation handlers and P4-A2's
  producer publish to ``app.state.inbox_activity_bus`` (set by
  ``register_inbox_sse_routes``).

Producer (P4-A2):

* ``POST /internal/v1/inbox/items`` is the service-token-gated producer
  endpoint that ai-backend posts to when an approval-fallback (or other
  agent event) needs to land a durable inbox row. Idempotent on
  ``(producer_id, external_ref)``. Wired through the canonical
  ``InboxService`` for shared ACL + audit.
"""

from __future__ import annotations

from backend_app.inbox.internal_routes import register_inbox_internal_routes
from backend_app.inbox.routes import register_inbox_routes
from backend_app.inbox.service import InboxService
from backend_app.inbox.sse import register_inbox_sse_routes
from backend_app.inbox.store import (
    InMemoryInboxStore,
    InboxAuditRecord,
    InboxBodyRecord,
    InboxItemRecord,
    InboxStore,
)

__all__ = [
    "InMemoryInboxStore",
    "InboxAuditRecord",
    "InboxBodyRecord",
    "InboxItemRecord",
    "InboxService",
    "InboxStore",
    "register_inbox_internal_routes",
    "register_inbox_routes",
    "register_inbox_sse_routes",
]
