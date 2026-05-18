"""Tests for ``/v1/projects`` CRUD + members + transfer + ACL — Phase 6 P6-A1.

Coverage:

* CRUD happy path (list + get + create + patch + delete + restore).
* Cursor pagination on list.
* Multi-value ``filter[status]`` OR semantics (cross-audit §1.5).
* Search via ``q=…``.
* Tenant isolation (caller cannot read another tenant's projects).
* Project-scoped ACL: owner writes; project-member reads (200); non-
  member non-admin gets 404 (NOT 403, cross-audit §1.3); admin
  compliance read.
* Member management — add / change-role / remove; cross-tenant guard.
* Ownership transfer — owner-only; new owner must be member; previous
  owner demoted to editor (default) or viewer / removed (none).
* Admin force-transfer — admins only; bypasses "must be current owner".
* Archive flow — mutations after archive return 409 (must activate).
* Soft-delete + restore.
* PARTIAL UNIQUE owner invariant — atomic two-step transfer.

The TestClient setup mirrors ``test_routines_routes.py``: no
``ENTERPRISE_SERVICE_TOKEN`` set, so identity rides in the query
params (the dev fallback). Admin-role tests inject the service token
+ headers to exercise the production auth path.
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
from backend_app.projects.store import InMemoryProjectsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (
        ("usr_sarah", "Sarah Chen"),
        ("usr_bob", "Bob"),
        ("usr_carol", "Carol"),
        ("usr_dave_admin", "Dave (admin)"),
    ):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=display,
            )
        )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    store.create_user(
        UserRecord(
            user_id="usr_alice_other",
            org_id="org_zeta",
            primary_email="alice@zeta.com",
            display_name="Alice",
        )
    )
    return store


def _client(
    *,
    projects_store: InMemoryProjectsStore | None = None,
) -> tuple[TestClient, InMemoryProjectsStore]:
    store = projects_store or InMemoryProjectsStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        projects_store=store,
    )
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Acme renewal",
        "description": "Q3 renewal work for Acme",
        "icon_emoji": "🚀",
        "color_hue": 210,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


class TestCrud:
    def test_create_get_patch_delete_flow(self) -> None:
        client, store = _client()

        # Create.
        resp = client.post("/v1/projects", params=_q(), json=_create_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        project_id = body["id"]
        assert body["status"] == "active"
        assert body["owner_user_id"] == "usr_sarah"
        assert body["viewer_role"] == "owner"
        assert body["viewer_starred"] is False
        assert body["counts"]["members"] == 1  # owner is the only member

        # Get.
        resp = client.get(f"/v1/projects/{project_id}", params=_q())
        assert resp.status_code == 200
        assert resp.json()["id"] == project_id

        # List.
        resp = client.get("/v1/projects", params=_q())
        assert resp.status_code == 200
        page = resp.json()
        assert page["next_cursor"] is None
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] == project_id

        # PATCH — rename.
        resp = client.patch(
            f"/v1/projects/{project_id}",
            params=_q(),
            json={"name": "Acme renewal Q3"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Acme renewal Q3"

        # DELETE (soft).
        resp = client.delete(f"/v1/projects/{project_id}", params=_q())
        assert resp.status_code == 204
        # Subsequent GET returns 404.
        resp = client.get(f"/v1/projects/{project_id}", params=_q())
        assert resp.status_code == 404

        # Audit chain — created + updated + deleted.
        audit = store.list_audit_for_project(
            tenant_id="org_acme", project_id=project_id
        )
        actions = [r.action for r in audit]
        assert "project.created" in actions
        assert "project.updated" in actions
        assert "project.deleted" in actions

    def test_create_rejects_blank_name(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/projects",
            params=_q(),
            json={"name": "  ", "icon_emoji": "🚀", "color_hue": 210},
        )
        assert resp.status_code == 400

    def test_create_rejects_invalid_hue(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/projects",
            params=_q(),
            json={**_create_payload(), "color_hue": 999},
        )
        assert resp.status_code == 400

    def test_duplicate_name_within_tenant_rejected(self) -> None:
        client, _ = _client()
        client.post("/v1/projects", params=_q(), json=_create_payload(name="Acme"))
        resp = client.post(
            "/v1/projects", params=_q(), json=_create_payload(name="acme")
        )
        # Case-insensitive collision.
        assert resp.status_code == 409
        assert "duplicate_name" in resp.json()["detail"]

    def test_restore_undeletes(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.delete(f"/v1/projects/{pid}", params=_q())
        resp = client.post(f"/v1/projects/{pid}/restore", params=_q())
        assert resp.status_code == 200, resp.text
        # GET works again.
        resp = client.get(f"/v1/projects/{pid}", params=_q())
        assert resp.status_code == 200

    def test_list_cursor_pagination(self) -> None:
        client, _ = _client()
        for i in range(5):
            client.post(
                "/v1/projects",
                params=_q(),
                json=_create_payload(name=f"proj-{i}"),
            )
        resp = client.get("/v1/projects", params={**_q(), "limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None
        # Second page.
        resp = client.get(
            "/v1/projects",
            params={**_q(), "limit": 2, "cursor": body["next_cursor"]},
        )
        body2 = resp.json()
        assert len(body2["items"]) == 2
        assert body["items"][0]["id"] != body2["items"][0]["id"]

    def test_multi_value_status_filter_or(self) -> None:
        client, _ = _client()
        a = client.post(
            "/v1/projects", params=_q(), json=_create_payload(name="a")
        ).json()["id"]
        b = client.post(
            "/v1/projects", params=_q(), json=_create_payload(name="b")
        ).json()["id"]
        client.post("/v1/projects", params=_q(), json=_create_payload(name="c"))
        # Archive b.
        client.patch(f"/v1/projects/{b}", params=_q(), json={"status": "archived"})

        # Filter archived only.
        resp = client.get("/v1/projects", params={**_q(), "filter[status]": "archived"})
        items = resp.json()["items"]
        assert {item["id"] for item in items} == {b}

        # Multi-value OR.
        resp = client.get(
            "/v1/projects",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[status]", "active"),
                ("filter[status]", "archived"),
            ],
        )
        ids = {item["id"] for item in resp.json()["items"]}
        assert b in ids
        assert a in ids

    def test_search_q(self) -> None:
        client, _ = _client()
        a = client.post(
            "/v1/projects",
            params=_q(),
            json=_create_payload(name="Acme renewal", description="Q3"),
        ).json()["id"]
        client.post(
            "/v1/projects",
            params=_q(),
            json=_create_payload(name="Internal hiring", description="OKRs"),
        )
        resp = client.get("/v1/projects", params={**_q(), "q": "acme"})
        ids = {item["id"] for item in resp.json()["items"]}
        assert a in ids
        assert len(ids) == 1


# ---------------------------------------------------------------------------
# Archive state machine
# ---------------------------------------------------------------------------


class TestArchive:
    def test_archive_then_activate(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.patch(
            f"/v1/projects/{pid}", params=_q(), json={"status": "archived"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "archived"
        assert body["archived_at"] is not None
        # Activate.
        resp = client.patch(
            f"/v1/projects/{pid}", params=_q(), json={"status": "active"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert resp.json()["archived_at"] is None

    def test_mutation_on_archived_returns_409(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.patch(f"/v1/projects/{pid}", params=_q(), json={"status": "archived"})
        # Rename rejected.
        resp = client.patch(
            f"/v1/projects/{pid}", params=_q(), json={"name": "renamed"}
        )
        assert resp.status_code == 409
        assert "project_archived" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_other_tenant_cannot_see_projects(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        cross = {"org_id": "org_zeta", "user_id": "usr_alice_other"}
        resp = client.get("/v1/projects", params=cross)
        assert resp.json()["items"] == []
        resp = client.get(f"/v1/projects/{pid}", params=cross)
        assert resp.status_code == 404
        resp = client.patch(
            f"/v1/projects/{pid}", params=cross, json={"name": "leaked"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project ACL — the 4-case matrix (cross-audit §1.3, projects-prd §7.4)
# ---------------------------------------------------------------------------


class TestProjectAclMatrix:
    """4-case fixture exported by projects-prd §13.1 for cross-destination
    consumers. Each case appears here AND will be replayed by inbox /
    todos / routines / library / memory in their own test suites."""

    def test_owner_reads_200(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.get(f"/v1/projects/{pid}", params=_q())
        assert resp.status_code == 200
        assert resp.json()["viewer_role"] == "owner"

    def test_project_member_reads_200(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Sarah (owner) adds Bob as editor.
        resp = client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        assert resp.status_code == 201, resp.text
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.get(f"/v1/projects/{pid}", params=bob_q)
        assert resp.status_code == 200
        assert resp.json()["viewer_role"] == "editor"

    def test_admin_reads_200_with_compliance(self) -> None:
        """Tenant admin (non-member) gets 200 — compliance read.

        The admin path uses the production service-token + role header
        injection (test_routines_routes pattern)."""

        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Admin call — service token in headers, role marked admin.
        import os

        os.environ["ENTERPRISE_SERVICE_TOKEN"] = "test-service-token"
        try:
            resp = client.get(
                f"/v1/projects/{pid}",
                headers={
                    SERVICE_TOKEN_HEADER: "test-service-token",
                    ORG_HEADER: "org_acme",
                    USER_HEADER: "usr_dave_admin",
                    ROLES_HEADER: "admin",
                },
                params={"org_id": "org_acme", "user_id": "usr_dave_admin"},
            )
            assert resp.status_code == 200
            # Admin non-member sees viewer_role=None (per projects-prd
            # §4.1 — null signals "admin-compliance, not a member").
            assert resp.json()["viewer_role"] is None
        finally:
            os.environ.pop("ENTERPRISE_SERVICE_TOKEN", None)

    def test_non_member_non_admin_gets_404_not_403(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Bob is in the tenant but NOT a member of the project.
        resp = client.get(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
        )
        assert resp.status_code == 404, resp.text  # NEVER 403.
        # PATCH also 404.
        resp = client.patch(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
            json={"name": "leaked"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Mutation ACL — owner-only writes (cross-audit §1.3)
# ---------------------------------------------------------------------------


class TestMutationAcl:
    def test_editor_cannot_patch_project(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.patch(f"/v1/projects/{pid}", params=bob_q, json={"name": "boops"})
        # Read was established (member) → 403 (NOT 404).
        assert resp.status_code == 403

    def test_viewer_cannot_patch_project(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "viewer"},
        )
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.patch(f"/v1/projects/{pid}", params=bob_q, json={"name": "boops"})
        assert resp.status_code == 403

    def test_non_member_cannot_delete_project(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.delete(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
        )
        # Non-member → 404 (existence not leaked).
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Membership management
# ---------------------------------------------------------------------------


class TestMembership:
    def test_add_then_list_then_remove(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Add Bob as editor.
        resp = client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["role"] == "editor"
        # List members.
        resp = client.get(f"/v1/projects/{pid}/members", params=_q())
        rows = resp.json()["items"]
        # 2 rows: owner Sarah + editor Bob.
        roles = {r["user_id"]: r["role"] for r in rows}
        assert roles == {"usr_sarah": "owner", "usr_bob": "editor"}
        # Remove Bob.
        resp = client.delete(f"/v1/projects/{pid}/members/usr_bob", params=_q())
        assert resp.status_code == 204

    def test_add_duplicate_returns_409(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        resp = client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "viewer"},
        )
        assert resp.status_code == 409
        assert "membership_exists" in resp.json()["detail"]

    def test_cross_tenant_user_rejected(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_alice_other", "role": "editor"},
        )
        assert resp.status_code == 422
        assert "cross_tenant_user" in resp.json()["detail"]

    def test_cannot_remove_owner(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.delete(f"/v1/projects/{pid}/members/usr_sarah", params=_q())
        assert resp.status_code == 409
        assert "owner_cannot_be_removed" in resp.json()["detail"]

    def test_change_member_role(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "viewer"},
        )
        resp = client.patch(
            f"/v1/projects/{pid}/members/usr_bob",
            params=_q(),
            json={"role": "editor"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "editor"

    def test_cannot_set_role_owner_via_patch(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "viewer"},
        )
        resp = client.patch(
            f"/v1/projects/{pid}/members/usr_bob",
            params=_q(),
            json={"role": "owner"},
        )
        # owner can only be assigned via the transfer endpoint.
        assert resp.status_code == 400

    def test_self_remove_via_me_shortcut(self) -> None:
        """A member can leave a project they're a member of via
        ``DELETE …/members/me`` even though they aren't the owner."""

        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.delete(f"/v1/projects/{pid}/members/me", params=bob_q)
        assert resp.status_code == 204
        # Subsequent reads → 404.
        resp = client.get(f"/v1/projects/{pid}", params=bob_q)
        assert resp.status_code == 404

    def test_non_owner_cannot_add_member(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Bob is added as editor by Sarah.
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        # Bob tries to add Carol.
        resp = client.post(
            f"/v1/projects/{pid}/members",
            params=bob_q,
            json={"user_id": "usr_carol", "role": "viewer"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Ownership transfer
# ---------------------------------------------------------------------------


class TestTransfer:
    def test_owner_transfer_to_existing_member(self) -> None:
        client, store = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        resp = client.post(
            f"/v1/projects/{pid}/transfer",
            params=_q(),
            json={"new_owner_user_id": "usr_bob"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["owner_user_id"] == "usr_bob"

        # Sarah (old owner) demoted to editor (default).
        rows = store.list_memberships_for_project(
            tenant_id="org_acme", project_id=pid, limit=10
        )[0]
        roles = {r.user_id: r.role for r in rows}
        assert roles["usr_sarah"] == "editor"
        assert roles["usr_bob"] == "owner"

        # PARTIAL UNIQUE on owner — exactly one owner row.
        assert sum(1 for r in rows if r.role == "owner") == 1

        # Audit row with both ids.
        audits = store.list_audit_for_project(tenant_id="org_acme", project_id=pid)
        transfer_rows = [
            r for r in audits if r.action == "project.ownership_transferred"
        ]
        assert len(transfer_rows) == 1
        ctx = transfer_rows[0].context or {}
        assert ctx["from_user_id"] == "usr_sarah"
        assert ctx["to_user_id"] == "usr_bob"

    def test_transfer_to_non_member_rejected(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Bob is in the tenant but NOT a member — must be added first.
        resp = client.post(
            f"/v1/projects/{pid}/transfer",
            params=_q(),
            json={"new_owner_user_id": "usr_bob"},
        )
        assert resp.status_code == 422
        assert "new_owner_not_member" in resp.json()["detail"]

    def test_non_owner_transfer_404(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        # Carol (non-member) tries.
        carol_q = {"org_id": "org_acme", "user_id": "usr_carol"}
        resp = client.post(
            f"/v1/projects/{pid}/transfer",
            params=carol_q,
            json={"new_owner_user_id": "usr_bob"},
        )
        assert resp.status_code == 404

    def test_transfer_member_non_owner_403(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        # Bob (editor) tries to transfer.
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.post(
            f"/v1/projects/{pid}/transfer",
            params=bob_q,
            json={"new_owner_user_id": "usr_carol"},
        )
        # Bob has read but not write → 403.
        assert resp.status_code == 403

    def test_transfer_with_remove_previous_owner(self) -> None:
        """``previous_owner_new_role=none`` removes the old owner."""

        client, store = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        resp = client.post(
            f"/v1/projects/{pid}/transfer",
            params=_q(),
            json={
                "new_owner_user_id": "usr_bob",
                "previous_owner_new_role": "none",
            },
        )
        assert resp.status_code == 200, resp.text
        rows = store.list_memberships_for_project(
            tenant_id="org_acme", project_id=pid, limit=10
        )[0]
        ids = {r.user_id for r in rows}
        assert ids == {"usr_bob"}  # Sarah removed entirely

    def test_admin_force_transfer(self) -> None:
        """Tenant admin force-transfer bypasses the current-owner check."""

        client, store = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Admin adds Carol as editor via Sarah (owner) first — admin
        # force-transfer requires the new owner to already be a member
        # per the same invariant as owner transfer.
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_carol", "role": "editor"},
        )

        import os

        os.environ["ENTERPRISE_SERVICE_TOKEN"] = "test-service-token"
        try:
            resp = client.post(
                f"/v1/admin/projects/{pid}/force-transfer",
                headers={
                    SERVICE_TOKEN_HEADER: "test-service-token",
                    ORG_HEADER: "org_acme",
                    USER_HEADER: "usr_dave_admin",
                    ROLES_HEADER: "admin",
                },
                params={"org_id": "org_acme", "user_id": "usr_dave_admin"},
                json={
                    "new_owner_user_id": "usr_carol",
                    "reason": "owner_offboarded",
                },
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["owner_user_id"] == "usr_carol"

            audits = store.list_audit_for_project(tenant_id="org_acme", project_id=pid)
            force = [r for r in audits if r.action == "project.admin_force_transferred"]
            assert len(force) == 1
            ctx = force[0].context or {}
            assert ctx["admin_force"] is True
            assert ctx["reason"] == "owner_offboarded"
            assert ctx["from_user_id"] == "usr_sarah"
            assert ctx["to_user_id"] == "usr_carol"
        finally:
            os.environ.pop("ENTERPRISE_SERVICE_TOKEN", None)

    def test_force_transfer_non_admin_rejected(self) -> None:
        """Non-admin caller on the admin force-transfer endpoint → 403."""

        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(
            f"/v1/projects/{pid}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        # Sarah is the owner but not an admin → still rejected on this
        # endpoint (force-transfer is the admin path; she should use the
        # regular owner /transfer endpoint).
        resp = client.post(
            f"/v1/admin/projects/{pid}/force-transfer",
            params=_q(),
            json={"new_owner_user_id": "usr_bob"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------


class TestStars:
    def test_star_toggle(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.post(f"/v1/projects/{pid}/star", params=_q())
        resp = client.get(f"/v1/projects/{pid}", params=_q())
        assert resp.json()["viewer_starred"] is True
        client.post(f"/v1/projects/{pid}/unstar", params=_q())
        resp = client.get(f"/v1/projects/{pid}", params=_q())
        assert resp.json()["viewer_starred"] is False

    def test_non_member_cannot_star(self) -> None:
        client, _ = _client()
        pid = client.post("/v1/projects", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        resp = client.post(f"/v1/projects/{pid}/star", params=bob_q)
        # Non-member → 404 (existence not leaked).
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Membership-graph filter guard
# ---------------------------------------------------------------------------


class TestMemberFilterGuard:
    def test_non_admin_cross_user_filter_rejected(self) -> None:
        client, _ = _client()
        client.post("/v1/projects", params=_q(), json=_create_payload())
        # Sarah tries to filter to Bob's memberships.
        resp = client.get(
            "/v1/projects",
            params={
                **_q(),
                "filter[member_user_id]": "usr_bob",
            },
        )
        assert resp.status_code == 403
        assert "admin_only" in resp.json()["detail"]

    def test_non_admin_self_filter_allowed(self) -> None:
        client, _ = _client()
        client.post("/v1/projects", params=_q(), json=_create_payload())
        resp = client.get(
            "/v1/projects",
            params={
                **_q(),
                "filter[member_user_id]": "me",
            },
        )
        assert resp.status_code == 200
