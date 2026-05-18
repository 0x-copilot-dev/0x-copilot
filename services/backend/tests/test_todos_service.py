"""Tests for ``TodosService`` — subtask + audit invariants (P3-A1).

Service-layer tests exercise the business rules without going through
HTTP; the route tests in ``test_todos_routes.py`` cover the wire path.
"""

from __future__ import annotations

import pytest

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.todos.service import (
    TodoForbidden,
    TodoInvalidRequest,
    TodoNotFound,
    TodosService,
)
from backend_app.todos.store import InMemoryTodosStore


def _service() -> tuple[TodosService, InMemoryTodosStore]:
    store = InMemoryTodosStore()
    service = TodosService(store=store, identity_store=InMemoryIdentityStore())
    return service, store


class TestSubtaskInvariants:
    def test_one_level_nesting_only(self) -> None:
        service, _store = _service()
        parent = service.create_todo(tenant_id="t", caller_user_id="u", text="parent")
        child = service.create_todo(
            tenant_id="t", caller_user_id="u", text="child", parent_id=parent.id
        )
        with pytest.raises(TodoInvalidRequest):
            service.create_todo(
                tenant_id="t",
                caller_user_id="u",
                text="grandchild",
                parent_id=child.id,
            )

    def test_subtask_inherits_parent_project(self) -> None:
        service, _store = _service()
        parent = service.create_todo(
            tenant_id="t",
            caller_user_id="u",
            text="p",
            project_id="proj_alpha",
        )
        child = service.create_todo(
            tenant_id="t",
            caller_user_id="u",
            text="c",
            parent_id=parent.id,
            project_id="proj_other",  # ignored
        )
        assert child.project_id == "proj_alpha"

    def test_cascade_delete_parent_removes_children(self) -> None:
        service, store = _service()
        parent = service.create_todo(tenant_id="t", caller_user_id="u", text="p")
        child = service.create_todo(
            tenant_id="t", caller_user_id="u", text="c", parent_id=parent.id
        )
        deleted = service.delete_todo(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            todo_id=parent.id,
        )
        assert deleted == 2
        assert store.get_todo(tenant_id="t", todo_id=child.id) is None

    def test_only_parent_owner_can_attach_subtask(self) -> None:
        service, _store = _service()
        parent = service.create_todo(tenant_id="t", caller_user_id="alice", text="p")
        # Bob can't attach a subtask to Alice's parent (would let Bob
        # bypass owner-only writes via parent_id).
        with pytest.raises(TodoInvalidRequest):
            service.create_todo(
                tenant_id="t",
                caller_user_id="bob",
                text="c",
                parent_id=parent.id,
            )

    def test_recurring_subtask_rejected(self) -> None:
        service, _store = _service()
        parent = service.create_todo(tenant_id="t", caller_user_id="u", text="p")
        with pytest.raises(TodoInvalidRequest):
            service.create_todo(
                tenant_id="t",
                caller_user_id="u",
                text="c",
                parent_id=parent.id,
                recurrence={"rule": "every_weekday", "spec": ""},
            )


class TestAuditInvariants:
    def test_state_move_writes_one_audit_row(self) -> None:
        service, store = _service()
        todo = service.create_todo(tenant_id="t", caller_user_id="u", text="x")
        # Create wrote one row already; the patch writes one more.
        service.update_todo(
            tenant_id="t",
            caller_user_id="u",
            caller_roles=(),
            todo_id=todo.id,
            patch={"status": "done"},
        )
        actions = [
            r.action for r in store.list_audit_for_todo(tenant_id="t", todo_id=todo.id)
        ]
        assert actions.count("todo.mark_done") == 1
        assert "todo.create" in actions

    def test_bulk_correlation_id_required(self) -> None:
        service, _store = _service()
        todo = service.create_todo(tenant_id="t", caller_user_id="u", text="x")
        with pytest.raises(TodoInvalidRequest):
            service.bulk_update(
                tenant_id="t",
                caller_user_id="u",
                caller_roles=(),
                action="mark_done",
                ids=(todo.id,),
                correlation_id="",
            )

    def test_owner_write_only(self) -> None:
        service, _store = _service()
        todo = service.create_todo(
            tenant_id="t",
            caller_user_id="alice",
            text="x",
            project_id="proj_a",
        )
        # Bob is a project member (simulated by injecting a non-default
        # adapter), so he can READ but not WRITE.
        from backend_app.projects.acl import InMemoryProjectMembershipAdapter

        service_member = TodosService(
            store=service._store,  # type: ignore[attr-defined]
            identity_store=InMemoryIdentityStore(),
            project_membership=InMemoryProjectMembershipAdapter(
                {("t", "proj_a"): {"bob"}}
            ),
        )
        with pytest.raises(TodoForbidden):
            service_member.update_todo(
                tenant_id="t",
                caller_user_id="bob",
                caller_roles=(),
                todo_id=todo.id,
                patch={"status": "done"},
            )

    def test_unreadable_todo_raises_not_found_not_forbidden(self) -> None:
        """The 404-not-403 rule (cross-audit §1.3) collapses both branches."""

        service, _store = _service()
        todo = service.create_todo(
            tenant_id="t", caller_user_id="alice", text="private"
        )
        # Bob can't read it (not owner, not project member, not admin).
        with pytest.raises(TodoNotFound):
            service.update_todo(
                tenant_id="t",
                caller_user_id="bob",
                caller_roles=(),
                todo_id=todo.id,
                patch={"status": "done"},
            )
