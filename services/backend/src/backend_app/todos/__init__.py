"""Todos destination (Phase 3) — CRUD + ACL + extraction provenance.

Public surface: ``GET/POST/PATCH/DELETE /v1/todos[/<id>]`` and the
``POST /v1/todos/bulk`` bulk-action endpoint. Identity is the verified
session caller; tenant isolation is enforced at every store call.

Wire shape is canonical at ``packages/api-types/src/todos.ts``; the
Python mirrors live in ``todos.contracts``. Routes wire ACL + audit
via ``todos.service`` so the route layer stays presentation-only.

Authorization (cross-audit §1.3):

* Owner-only by default; non-owner workspace members get 404 (not 403)
  to avoid leaking existence.
* When ``project_id IS NOT NULL``: project members can READ (still no
  writes — owner-only for mutation).
* Tenant admins (``admin`` role in the verified roles tuple) can READ
  any todo in their tenant for compliance; cannot mutate.

Subtask invariants (impl-plan §11.2):

* One level of nesting only (parent of a parent → 400).
* ``project_id`` inherited from parent on create.
* Cascade-delete parent → children (DB-level via FK in postgres; the
  in-memory adapter mirrors the same semantics).
"""

from __future__ import annotations

from backend_app.todos.routes import register_todos_routes
from backend_app.todos.service import TodosService
from backend_app.todos.store import (
    InMemoryTodosStore,
    TodoAuditRecord,
    TodoRecord,
    TodosStore,
)

__all__ = [
    "InMemoryTodosStore",
    "TodoAuditRecord",
    "TodoRecord",
    "TodosService",
    "TodosStore",
    "register_todos_routes",
]
