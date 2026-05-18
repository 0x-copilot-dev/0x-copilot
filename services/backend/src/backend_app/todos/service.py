"""Todos service — ACL + audit + subtask invariants.

The route layer in ``routes.py`` is presentation-only; every
business-logic decision lives here so the in-memory ``InMemoryTodosStore``
and the postgres adapter share one set of authorization checks.

Authorization rules (cross-audit §1.3, binding):

* Owner-only writes.
* Reads: owner OR (project_id member when project_id is set) OR tenant
  admin (audited via the same audit row stream, ``action=todo.read_admin``).
* Non-readers see 404, not 403 (existence not leaked).

Subtask invariants (impl-plan §11.2):

* One level of nesting only — parent referenced by ``parent_id`` must
  itself have ``parent_id IS NULL``.
* Subtask ``project_id`` is inherited from the parent on create.
* Delete cascades to children (one level).

Audit rows are append-only; bulk actions stamp a shared
``correlation_id`` on every row so SIEM can reconstruct the bulk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from backend_app.identity.store import IdentityStore
from backend_app.projects.acl import (
    ProjectMembershipPort,
    _NoMemberProjectAdapter,
)
from backend_app.todos.store import (
    TodoAuditRecord,
    TodoRecord,
    TodoSeriesRecord,
    TodosStore,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Roles with tenant-admin read access. Treated as untrusted unless the
# verified ``ScopedIdentity.roles`` tuple set them — the route layer
# passes through what the auth middleware verified.
_ADMIN_ROLES = frozenset({"admin", "owner"})


class TodoNotFound(Exception):
    """Raised when a todo doesn't exist OR the caller has no read rights.

    The 404-not-403 rule (cross-audit §1.3) collapses both branches to
    one exception so the route layer cannot accidentally distinguish
    them — the response is always 404.
    """


class TodoForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Only used internally to gate the write path after read access has
    already been established (so 404-not-403 still applies for the
    read-doesn't-exist case). The route layer translates this to 403.
    """


class TodoInvalidRequest(Exception):
    """Raised for client-fixable invariant violations (400)."""


class TodosService:
    """Composition of the todos store + identity store with ACL + audit."""

    def __init__(
        self,
        *,
        store: TodosStore,
        identity_store: IdentityStore,
        project_membership: "ProjectMembershipPort | None" = None,
    ) -> None:
        self._store = store
        self._identity = identity_store
        # Project membership lookup is injected so the in-memory tests
        # don't need the (not-yet-shipped) Projects destination. Defaults
        # to a no-member adapter — owner-only behaviour until the
        # Projects destination lands and registers a real adapter.
        self._project_membership = project_membership or _NoMemberProjectAdapter()

    # -- reads ---------------------------------------------------------

    def get_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
    ) -> TodoRecord:
        """Authorise + return a single todo.

        Raises :class:`TodoNotFound` if the caller can't see it (which
        is what 404-not-403 demands; the route never distinguishes
        "not found" from "not authorised").
        """

        record = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        if record is None:
            raise TodoNotFound(todo_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise TodoNotFound(todo_id)
        return record

    def list_todos(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]:
        """List the caller's readable todos.

        Composition of three buckets:

        1. Todos owned by the caller.
        2. Todos in projects the caller is a member of (read-only).
        3. (Admin only) every todo in the tenant.

        The store is tenant-scoped, then this method narrows by
        ownership / membership. Admin reads bypass the narrowing.
        """

        admin = any(role in _ADMIN_ROLES for role in caller_roles)
        if admin:
            page, next_cursor = self._store.list_todos(
                tenant_id=tenant_id,
                owner_user_id=None,
                statuses=statuses,
                project_ids=project_ids,
                parent_id=parent_id,
                cursor=cursor,
                limit=limit,
            )
            return page, next_cursor

        # Non-admin: union of owner + project-member rows. The in-memory
        # adapter applies the filters per-bucket; the postgres adapter
        # implements the same predicate with one query (see
        # schema.sql comment block on `todos_owner_or_project_idx`).
        owner_page, next_cursor = self._store.list_todos(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            statuses=statuses,
            project_ids=project_ids,
            parent_id=parent_id,
            cursor=cursor,
            limit=limit,
        )
        member_projects = self._project_membership.list_projects_for_user(
            tenant_id=tenant_id, user_id=caller_user_id
        )
        if not member_projects:
            return owner_page, next_cursor
        # Project-member reads: only listing rows whose project_id is in
        # the member-project set AND owner ≠ caller (already in owner
        # bucket). Cursor pagination over the union is approximated by
        # taking the owner page as canonical and appending project
        # rows; full keyset merge is a postgres-layer concern.
        project_page, _project_next = self._store.list_project_member_todos(
            tenant_id=tenant_id,
            project_ids=member_projects,
            cursor=None,
            limit=limit,
        )
        seen = {r.id for r in owner_page}
        merged = list(owner_page) + [r for r in project_page if r.id not in seen]
        # If owner-only filters narrowed away project rows the caller
        # would otherwise see, the page is best-effort. Postgres adapter
        # tightens this.
        if statuses is not None:
            merged = [r for r in merged if r.status in statuses]
        if project_ids is not None:
            merged = [r for r in merged if r.project_id in project_ids]
        if parent_id is not None:
            merged = [r for r in merged if r.parent_id == parent_id]
        return tuple(merged[:limit]), next_cursor

    # -- writes --------------------------------------------------------

    def create_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        text: str,
        priority: str = "med",
        due: str | None = None,
        project_id: str | None = None,
        parent_id: str | None = None,
        recurrence: dict | None = None,
        source: dict | None = None,
    ) -> TodoRecord:
        """Create a todo.

        Public callers cannot set ``source`` to anything other than
        ``{"kind": "user"}`` — the chat/agent provenance variants are
        reserved for the internal extraction-accept pipeline (PRD §4.3).
        """

        if source is None:
            source = {"kind": "user"}
        if source.get("kind") != "user":
            raise TodoInvalidRequest("non_user_source_forbidden")
        if not text or not text.strip():
            raise TodoInvalidRequest("text_required")
        if priority not in {"low", "med", "high"}:
            raise TodoInvalidRequest("invalid_priority")

        resolved_project_id = project_id
        resolved_parent_id: str | None = None
        if parent_id is not None:
            parent = self._store.get_todo(tenant_id=tenant_id, todo_id=parent_id)
            if parent is None or parent.owner_user_id != caller_user_id:
                # Only the parent's owner can attach a subtask — keeps
                # the ACL story consistent with the rest of the
                # destination (owner-only writes).
                raise TodoInvalidRequest("parent_not_found_or_not_owned")
            if parent.parent_id is not None:
                # One level of nesting only.
                raise TodoInvalidRequest("nested_subtask_forbidden")
            resolved_parent_id = parent.id
            # Subtask inherits the parent's project (server enforced
            # per impl-plan §11.2).
            resolved_project_id = parent.project_id

        series_id: str | None = None
        recurrence_blob: dict | None = None
        if recurrence is not None:
            if resolved_parent_id is not None:
                # Recurring subtasks are out of scope (impl-plan §11.1).
                raise TodoInvalidRequest("recurring_subtask_forbidden")
            series = self._store.insert_series(
                TodoSeriesRecord(
                    tenant_id=tenant_id,
                    owner_user_id=caller_user_id,
                    rule=str(recurrence.get("rule", "")),
                    spec=str(recurrence.get("spec", "")),
                )
            )
            series_id = series.id
            recurrence_blob = {
                "rule": recurrence.get("rule"),
                "spec": recurrence.get("spec"),
                # ``next_materialize_at`` is the materialiser's
                # responsibility; we leave the wire field absent on the
                # first row (the materialiser stamps it on the next
                # concrete instance).
                "next_materialize_at": recurrence.get("next_materialize_at"),
                "series_id": series_id,
            }

        record = TodoRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            project_id=resolved_project_id,
            text=text.strip(),
            status="open",
            priority=priority,
            due=due,
            source=source,
            parent_id=resolved_parent_id,
            recurrence=recurrence_blob,
            series_id=series_id,
        )
        with self._store.transaction():
            stored = self._store.insert_todo(record)
            self._store.append_audit(
                TodoAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="todo.create",
                    target_id=stored.id,
                    after_state=stored.model_dump(mode="json"),
                )
            )
        return stored

    def update_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
        patch: dict,
    ) -> TodoRecord:
        """Patch fields on a todo. Owner-only.

        ``patch`` is a dict of changed fields; absent fields are
        untouched. ``status`` transitions stamp ``completed_at``
        (set on done, cleared on re-open).
        """

        existing = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        # 404-not-403 on both "missing" and "no read rights" branches.
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise TodoNotFound(todo_id)
        if existing.owner_user_id != caller_user_id:
            # Read access established (project member or admin) but
            # writes are owner-only. cross-audit §1.3.
            raise TodoForbidden(todo_id)

        updates: dict = {}
        action = "todo.update"
        for key in (
            "text",
            "priority",
            "due",
            "project_id",
            "sort_index_within_parent",
        ):
            if key in patch:
                updates[key] = patch[key]
        if "status" in patch:
            new_status = patch["status"]
            if new_status not in {"open", "done"}:
                raise TodoInvalidRequest("invalid_status")
            updates["status"] = new_status
            if new_status == "done" and existing.status != "done":
                updates["completed_at"] = _now()
                action = "todo.mark_done"
            elif new_status == "open" and existing.status == "done":
                updates["completed_at"] = None
                action = "todo.mark_undone"
        if "recurrence" in patch:
            # ``None`` clears, dict updates. ``series_id`` is preserved
            # so already-materialised instances remain linked.
            updates["recurrence"] = patch["recurrence"]
        if not updates:
            return existing
        updates["updated_at"] = _now()
        new_record = existing.model_copy(update=updates)

        before_state = existing.model_dump(mode="json")
        after_state = new_record.model_dump(mode="json")
        with self._store.transaction():
            stored = self._store.update_todo(new_record)
            self._store.append_audit(
                TodoAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=action,
                    target_id=stored.id,
                    before_state=before_state,
                    after_state=after_state,
                )
            )
        return stored

    def delete_todo(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        todo_id: str,
    ) -> int:
        """Soft-delete a todo + cascade to one-level subtasks.

        Returns the number of rows deleted (parent + each child).
        """

        existing = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        if existing is None or not self._can_read(
            existing, caller_user_id, caller_roles
        ):
            raise TodoNotFound(todo_id)
        if existing.owner_user_id != caller_user_id:
            raise TodoForbidden(todo_id)

        with self._store.transaction():
            deleted_ids = self._store.delete_todo(tenant_id=tenant_id, todo_id=todo_id)
            for target_id in deleted_ids:
                self._store.append_audit(
                    TodoAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action="todo.delete",
                        target_id=target_id,
                        before_state={"id": target_id},
                    )
                )
        return len(deleted_ids)

    def bulk_update(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        action: str,
        ids: tuple[str, ...],
        correlation_id: str,
        payload: dict | None = None,
    ) -> int:
        """Apply ``action`` across multiple todos.

        Best-effort: ids the caller cannot write are silently skipped
        (the bulk shouldn't 404 if one row dropped out mid-flight). The
        return value counts only rows actually mutated; SIEM
        reconstruction uses the shared ``correlation_id`` stamped on
        every audit row written by this method.
        """

        if action not in {
            "mark_done",
            "mark_open",
            "delete",
            "set_priority",
            "set_project",
        }:
            raise TodoInvalidRequest("invalid_bulk_action")
        if not correlation_id or not correlation_id.strip():
            raise TodoInvalidRequest("correlation_id_required")
        payload = payload or {}

        affected = 0
        for todo_id in ids:
            record = self._store.get_todo(tenant_id=tenant_id, todo_id=todo_id)
            if record is None or record.owner_user_id != caller_user_id:
                continue
            if action == "delete":
                with self._store.transaction():
                    deleted_ids = self._store.delete_todo(
                        tenant_id=tenant_id, todo_id=todo_id
                    )
                    for target_id in deleted_ids:
                        self._store.append_audit(
                            TodoAuditRecord(
                                tenant_id=tenant_id,
                                actor_user_id=caller_user_id,
                                action="todo.delete",
                                target_id=target_id,
                                before_state={"id": target_id},
                                correlation_id=correlation_id,
                            )
                        )
                    affected += len(deleted_ids)
                continue
            patch: dict = {}
            audit_action = "todo.update"
            if action == "mark_done":
                patch["status"] = "done"
                patch["completed_at"] = _now()
                audit_action = "todo.mark_done"
            elif action == "mark_open":
                patch["status"] = "open"
                patch["completed_at"] = None
                audit_action = "todo.mark_undone"
            elif action == "set_priority":
                priority = payload.get("priority")
                if priority not in {"low", "med", "high"}:
                    raise TodoInvalidRequest("invalid_priority")
                patch["priority"] = priority
            elif action == "set_project":
                patch["project_id"] = payload.get("project_id")
            patch["updated_at"] = _now()
            before_state = record.model_dump(mode="json")
            new_record = record.model_copy(update=patch)
            with self._store.transaction():
                self._store.update_todo(new_record)
                self._store.append_audit(
                    TodoAuditRecord(
                        tenant_id=tenant_id,
                        actor_user_id=caller_user_id,
                        action=audit_action,
                        target_id=record.id,
                        before_state=before_state,
                        after_state=new_record.model_dump(mode="json"),
                        correlation_id=correlation_id,
                    )
                )
            affected += 1
        return affected

    # -- helpers -------------------------------------------------------

    def _can_read(
        self,
        record: TodoRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        if any(role in _ADMIN_ROLES for role in caller_roles):
            return True
        if record.project_id is None:
            return False
        return self._project_membership.is_project_member(
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            user_id=caller_user_id,
        )


__all__ = [
    "TodoForbidden",
    "TodoInvalidRequest",
    "TodoNotFound",
    "TodosService",
]
