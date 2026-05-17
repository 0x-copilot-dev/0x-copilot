"""Inbox destination (Phase 4) — CRUD + ACL + state machine + audit.

Public surface: ``GET /v1/inbox``, ``GET /v1/inbox/{id}``,
``PATCH /v1/inbox/{id}``, ``POST /v1/inbox/bulk``, and
``GET /v1/inbox/unread_count``. Identity is the verified session
caller; tenant isolation is enforced at every store call.

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
"""

from __future__ import annotations

from backend_app.inbox.routes import register_inbox_routes
from backend_app.inbox.service import InboxService
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
    "register_inbox_routes",
]
