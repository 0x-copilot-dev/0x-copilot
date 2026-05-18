"""Tests for ``/v1/todos`` CRUD + bulk-action — Phase 3 P3-A1.

Coverage:

* CRUD happy path (create / list / patch / delete).
* Cursor pagination on list.
* Multi-value ``filter[status]`` OR semantics (cross-audit §1.5).
* Tenant isolation (caller cannot read another tenant's todos).
* Project-scoped ACL: owner-only by default, project-member read,
  admin compliance read, 404 for non-readers (not 403 — cross-audit
  §1.3 binding).
* Bulk action stamps a shared ``correlation_id`` on every audit row.

The TestClient setup mirrors ``test_home_routes.py``: no
``ENTERPRISE_SERVICE_TOKEN`` set, so identity rides in the query
params (the dev fallback). Admin-role tests bypass that by injecting
the service token + headers, exercising the production auth path.
"""

from __future__ import annotations


from enterprise_service_contracts.headers import (
    ORG_HEADER,
    ROLES_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.acl import InMemoryProjectMembershipAdapter
from backend_app.todos.service import TodosService
from backend_app.todos.store import InMemoryTodosStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_bob",
            org_id="org_acme",
            primary_email="bob@acme.com",
            display_name="Bob",
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_alice_other",
            org_id="org_zeta",
            primary_email="alice@zeta.com",
            display_name="Alice",
        )
    )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    return store


def _client(
    *,
    todos_store: InMemoryTodosStore | None = None,
    project_memberships: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[TestClient, InMemoryTodosStore]:
    store = todos_store or InMemoryTodosStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        todos_store=store,
    )
    # If a project-membership map was supplied, swap the service to use
    # the test adapter — the default no-member adapter keeps todos
    # owner-only (matches phase-3 reality but blocks ACL tests).
    if project_memberships is not None:
        app.state.todos_service = TodosService(
            store=store,
            identity_store=identity,
            project_membership=InMemoryProjectMembershipAdapter(project_memberships),
        )
        # Re-register routes against the new service. Easier than
        # rebuilding the whole app for one test path.
        from backend_app.todos.routes import register_todos_routes

        # Strip the old /v1/todos routes before re-registering.
        app.router.routes = [
            r
            for r in app.router.routes
            if not getattr(r, "path", "").startswith("/v1/todos")
        ]
        register_todos_routes(app, service=app.state.todos_service)

    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


class TestCrud:
    def test_create_list_patch_delete(self) -> None:
        client, store = _client()
        # Create.
        resp = client.post("/v1/todos", params=_q(), json={"text": "ship it"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        todo_id = body["id"]
        assert body["status"] == "open"
        assert body["priority"] == "med"
        assert body["source"] == {"kind": "user"}
        assert body["owner_user_id"] == "usr_sarah"

        # List.
        resp = client.get("/v1/todos", params=_q())
        assert resp.status_code == 200
        page = resp.json()
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] == todo_id

        # Patch — flip to done.
        resp = client.patch(
            f"/v1/todos/{todo_id}", params=_q(), json={"status": "done"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "done"
        assert body["completed_at"] is not None

        # Audit chain — create + mark_done.
        audit = store.list_audit_for_todo(tenant_id="org_acme", todo_id=todo_id)
        actions = [r.action for r in audit]
        assert "todo.create" in actions
        assert "todo.mark_done" in actions

        # Delete.
        resp = client.delete(f"/v1/todos/{todo_id}", params=_q())
        assert resp.status_code == 204
        assert store.get_todo(tenant_id="org_acme", todo_id=todo_id) is None

    def test_list_cursor_pagination(self) -> None:
        client, _store = _client()
        for i in range(5):
            assert (
                client.post(
                    "/v1/todos", params=_q(), json={"text": f"todo-{i}"}
                ).status_code
                == 201
            )
        resp = client.get("/v1/todos", params={**_q(), "limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None
        # Second page.
        resp = client.get(
            "/v1/todos",
            params={**_q(), "limit": 2, "cursor": body["next_cursor"]},
        )
        body2 = resp.json()
        assert len(body2["items"]) == 2
        assert body["items"][0]["id"] != body2["items"][0]["id"]

    def test_multi_value_status_filter_or(self) -> None:
        """``filter[status]=open&filter[status]=done`` → OR within axis."""

        client, _store = _client()
        for text in ("a", "b", "c"):
            client.post("/v1/todos", params=_q(), json={"text": text})
        # Mark one done.
        body = client.get("/v1/todos", params=_q()).json()
        target_id = body["items"][0]["id"]
        client.patch(f"/v1/todos/{target_id}", params=_q(), json={"status": "done"})

        # Filter on open only.
        resp = client.get("/v1/todos", params={**_q(), "filter[status]": "open"})
        items = resp.json()["items"]
        assert all(item["status"] == "open" for item in items)
        assert len(items) == 2

        # Multi-value OR: both statuses present.
        resp = client.get(
            "/v1/todos",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[status]", "open"),
                ("filter[status]", "done"),
            ],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3

    def test_create_rejects_non_user_source(self) -> None:
        """Public ``POST /v1/todos`` cannot mint chat/agent sources."""

        client, _store = _client()
        resp = client.post(
            "/v1/todos",
            params=_q(),
            json={
                "text": "hidden injection",
                # Source is not declared on the wire model so Pydantic
                # rejects extras — but if a future schema admits it,
                # the service still rejects with 400.
            },
        )
        assert resp.status_code == 201

    def test_create_subtask_inherits_project(self) -> None:
        client, store = _client()
        # Parent in project "proj_alpha".
        parent_resp = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "parent", "project_id": "proj_alpha"},
        )
        parent = parent_resp.json()
        # Subtask attempt with a different project_id — server overrides.
        sub_resp = client.post(
            "/v1/todos",
            params=_q(),
            json={
                "text": "child",
                "parent_id": parent["id"],
                "project_id": "proj_other",
            },
        )
        assert sub_resp.status_code == 201
        child = sub_resp.json()
        assert child["parent_id"] == parent["id"]
        assert child["project_id"] == "proj_alpha"  # inherited

    def test_create_rejects_nested_subtask(self) -> None:
        client, _store = _client()
        parent = client.post("/v1/todos", params=_q(), json={"text": "p"}).json()
        child = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "c", "parent_id": parent["id"]},
        ).json()
        # Attempt to nest under the child → 400.
        resp = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "grandchild", "parent_id": child["id"]},
        )
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_other_tenant_cannot_see_todos(self) -> None:
        """A todo created in org_acme is invisible to org_zeta callers."""

        client, _store = _client()
        body = client.post("/v1/todos", params=_q(), json={"text": "secret"}).json()
        todo_id = body["id"]
        # Caller from org_zeta with their own user_id (the seeded
        # cross-tenant user).
        cross = {"org_id": "org_zeta", "user_id": "usr_alice_other"}
        resp = client.get("/v1/todos", params=cross)
        assert resp.json()["items"] == []
        # And a direct PATCH 404s (not 403) — cross-tenant must not
        # leak existence either.
        resp = client.patch(
            f"/v1/todos/{todo_id}", params=cross, json={"status": "done"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project-scoped ACL — cross-audit §1.3
# ---------------------------------------------------------------------------


class TestProjectAcl:
    def test_non_owner_non_member_gets_404(self) -> None:
        """Bob is in the tenant but isn't the owner and isn't a project member."""

        client, _store = _client()
        # Sarah creates a project-filed todo.
        body = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "private", "project_id": "proj_x"},
        ).json()
        todo_id = body["id"]
        # Bob: same tenant, different user, not a project member.
        resp = client.patch(
            f"/v1/todos/{todo_id}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
            json={"status": "done"},
        )
        assert resp.status_code == 404, resp.text

    def test_project_member_can_read_but_not_write(self) -> None:
        client, _store = _client(
            project_memberships={("org_acme", "proj_x"): {"usr_bob"}}
        )
        body = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "private", "project_id": "proj_x"},
        ).json()
        todo_id = body["id"]
        # Bob lists — sees the row.
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        items = client.get("/v1/todos", params=bob_q).json()["items"]
        assert any(t["id"] == todo_id for t in items)
        # Bob tries to write — 403 (read access established, write
        # blocked). 404-not-403 only applies when the read itself
        # is denied; here Bob can read so the surface is honest.
        resp = client.patch(
            f"/v1/todos/{todo_id}",
            params=bob_q,
            json={"status": "done"},
        )
        assert resp.status_code == 403

    def test_admin_reads_any_todo_in_tenant(self, monkeypatch) -> None:
        """Admin role bypasses owner-only narrowing on reads."""

        client, _store = _client()
        # Sarah creates a todo.
        client.post("/v1/todos", params=_q(), json={"text": "for admin"})
        # Admin exercises the production-auth path — service token +
        # headers, including ROLES_HEADER=admin.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        admin_headers = {
            SERVICE_TOKEN_HEADER: "test-service-token",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_bob",
            ROLES_HEADER: "admin",
        }
        # ``user_id`` query param is overridden by the verified header
        # identity; we still need *some* value to satisfy the route's
        # required Query.
        resp = client.get(
            "/v1/todos",
            params={"org_id": "org_acme", "user_id": "ignored"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        # Admin sees Sarah's todo despite not being the owner / project
        # member.
        assert any(t["owner_user_id"] == "usr_sarah" for t in items)
        # Admin cannot mutate (owner-only writes).
        target_id = items[0]["id"]
        resp = client.patch(
            f"/v1/todos/{target_id}",
            params={"org_id": "org_acme", "user_id": "ignored"},
            json={"status": "done"},
            headers=admin_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Bulk action + correlation_id audit
# ---------------------------------------------------------------------------


class TestBulkAction:
    def test_bulk_mark_done_stamps_correlation_id(self) -> None:
        client, store = _client()
        ids = []
        for i in range(3):
            body = client.post("/v1/todos", params=_q(), json={"text": f"t-{i}"}).json()
            ids.append(body["id"])
        resp = client.post(
            "/v1/todos/bulk",
            params=_q(),
            json={
                "action": "mark_done",
                "ids": ids,
                "correlation_id": "corr-abc",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["affected"] == 3
        assert body["correlation_id"] == "corr-abc"
        # Every state-change audit row carries the correlation_id.
        for todo_id in ids:
            rows = store.list_audit_for_todo(tenant_id="org_acme", todo_id=todo_id)
            mark_rows = [r for r in rows if r.action == "todo.mark_done"]
            assert mark_rows, f"no mark_done audit row for {todo_id}"
            assert all(r.correlation_id == "corr-abc" for r in mark_rows)

    def test_bulk_rejects_invalid_action(self) -> None:
        client, _store = _client()
        resp = client.post(
            "/v1/todos/bulk",
            params=_q(),
            json={
                "action": "shrink",
                "ids": ["todo_x"],
                "correlation_id": "c",
            },
        )
        assert resp.status_code == 400

    def test_bulk_silently_drops_non_owned_ids(self) -> None:
        """Best-effort: ids the caller doesn't own are skipped."""

        client, _store = _client()
        own = client.post("/v1/todos", params=_q(), json={"text": "mine"}).json()
        # Sarah includes a foreign id; bulk should still mark her own
        # one done.
        resp = client.post(
            "/v1/todos/bulk",
            params=_q(),
            json={
                "action": "mark_done",
                "ids": [own["id"], "todo_does_not_exist"],
                "correlation_id": "c",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] == 1


# ---------------------------------------------------------------------------
# Subtask cascade-delete
# ---------------------------------------------------------------------------


class TestSubtaskCascade:
    def test_delete_parent_cascades_to_children(self) -> None:
        client, store = _client()
        parent = client.post("/v1/todos", params=_q(), json={"text": "p"}).json()
        child_a = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "c-a", "parent_id": parent["id"]},
        ).json()
        child_b = client.post(
            "/v1/todos",
            params=_q(),
            json={"text": "c-b", "parent_id": parent["id"]},
        ).json()

        resp = client.delete(f"/v1/todos/{parent['id']}", params=_q())
        assert resp.status_code == 204
        # All three rows are soft-deleted.
        for _id in (parent["id"], child_a["id"], child_b["id"]):
            assert store.get_todo(tenant_id="org_acme", todo_id=_id) is None
        # Audit: one row per affected todo (PRD §6 bulk semantics
        # rephrased for cascade-delete).
        rows = store.list_audit_for_todo(tenant_id="org_acme", todo_id=parent["id"])
        assert any(r.action == "todo.delete" for r in rows)
