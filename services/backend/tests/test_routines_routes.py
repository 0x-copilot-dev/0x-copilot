"""Tests for ``/v1/routines`` CRUD + manual-fire + ACL — Phase 5 P5-A1.

Coverage:

* CRUD happy path (list + get + create + patch + delete).
* Cursor pagination on list.
* Multi-value ``filter[status]`` OR semantics (cross-audit §1.5).
* Tenant isolation (caller cannot read another tenant's routines).
* Project-scoped ACL: owner-only writes, project-member read,
  admin compliance read, 404 for non-readers (not 403 — cross-audit
  §1.3 binding).
* State machine — invalid transitions return 409; errored requires
  draft reset before re-activation.
* Quota enforcement — 101st active routine rejected; activating from
  draft is gated.
* Manual-fire ACL variants — owner / project_members / tenant.

The TestClient setup mirrors ``test_inbox_routes.py``: no
``ENTERPRISE_SERVICE_TOKEN`` set, so identity rides in the query
params (the dev fallback). Admin-role tests inject the service token
+ headers to exercise the production auth path.
"""

from __future__ import annotations

from copilot_service_contracts.headers import (
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
from backend_app.routines.service import RoutinesService
from backend_app.routines.store import InMemoryRoutinesStore


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
            user_id="usr_carol",
            org_id="org_acme",
            primary_email="carol@acme.com",
            display_name="Carol",
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
    routines_store: InMemoryRoutinesStore | None = None,
    project_memberships: dict[tuple[str, str], set[str]] | None = None,
    active_quota: int | None = None,
) -> tuple[TestClient, InMemoryRoutinesStore]:
    store = routines_store or InMemoryRoutinesStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        routines_store=store,
    )
    if project_memberships is not None or active_quota is not None:
        kwargs: dict[str, object] = {}
        if project_memberships is not None:
            kwargs["project_membership"] = InMemoryProjectMembershipAdapter(
                project_memberships
            )
        if active_quota is not None:
            kwargs["active_quota_per_user"] = active_quota
        app.state.routines_service = RoutinesService(
            store=store,
            identity_store=identity,
            **kwargs,  # type: ignore[arg-type]
        )
        # Strip the old /v1/routines routes before re-registering.
        from backend_app.routines.routes import register_routines_routes

        app.router.routes = [
            r
            for r in app.router.routes
            if not getattr(r, "path", "").startswith("/v1/routines")
        ]
        register_routines_routes(app, service=app.state.routines_service)
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Daily standup digest",
        "instructions": "Summarise yesterday's standup transcripts.",
        "agent_id": "agent_atlas",
        "triggers": [{"kind": "cron", "spec": "0 9 * * 1-5"}],
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
        resp = client.post("/v1/routines", params=_q(), json=_create_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        routine_id = body["id"]
        assert body["status"] == "draft"
        assert body["owner_user_id"] == "usr_sarah"
        assert body["permissions"]["manual_fire"] == "owner"
        assert body["missed_fire_policy"] == "fire_once"

        # Get.
        resp = client.get(f"/v1/routines/{routine_id}", params=_q())
        assert resp.status_code == 200
        assert resp.json()["id"] == routine_id

        # List.
        resp = client.get("/v1/routines", params=_q())
        assert resp.status_code == 200
        page = resp.json()
        assert page["next_cursor"] is None
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] == routine_id

        # PATCH — activate.
        resp = client.patch(
            f"/v1/routines/{routine_id}",
            params=_q(),
            json={"status": "active"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        # PATCH — change name.
        resp = client.patch(
            f"/v1/routines/{routine_id}",
            params=_q(),
            json={"name": "Daily standup digest (renamed)"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Daily standup digest (renamed)"

        # DELETE.
        resp = client.delete(f"/v1/routines/{routine_id}", params=_q())
        assert resp.status_code == 204
        # Subsequent GET returns 404 (soft-deleted).
        resp = client.get(f"/v1/routines/{routine_id}", params=_q())
        assert resp.status_code == 404

        # Audit chain — at least created + activated + updated + deleted.
        audit = store.list_audit_for_routine(
            tenant_id="org_acme", routine_id=routine_id
        )
        actions = [r.action for r in audit]
        assert "routine.created" in actions
        assert "routine.activated" in actions
        assert "routine.deleted" in actions

    def test_create_rejects_missing_name(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/routines",
            params=_q(),
            json={
                "name": "",
                "instructions": "x",
                "agent_id": "agent_atlas",
            },
        )
        assert resp.status_code == 400

    def test_create_rejects_unknown_status(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/routines",
            params=_q(),
            json={**_create_payload(), "status": "bogus"},
        )
        assert resp.status_code == 400

    def test_list_cursor_pagination(self) -> None:
        client, _ = _client()
        for i in range(5):
            client.post(
                "/v1/routines",
                params=_q(),
                json=_create_payload(name=f"routine-{i}"),
            )
        resp = client.get("/v1/routines", params={**_q(), "limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None
        # Second page.
        resp = client.get(
            "/v1/routines",
            params={**_q(), "limit": 2, "cursor": body["next_cursor"]},
        )
        body2 = resp.json()
        assert len(body2["items"]) == 2
        assert body["items"][0]["id"] != body2["items"][0]["id"]

    def test_multi_value_status_filter_or(self) -> None:
        """``filter[status]=active&filter[status]=paused`` → OR within axis."""

        client, store = _client()
        a = client.post(
            "/v1/routines", params=_q(), json=_create_payload(name="a")
        ).json()["id"]
        b = client.post(
            "/v1/routines", params=_q(), json=_create_payload(name="b")
        ).json()["id"]
        c = client.post(
            "/v1/routines", params=_q(), json=_create_payload(name="c")
        ).json()["id"]
        # Activate a, pause b (draft -> active -> paused), leave c draft.
        client.patch(f"/v1/routines/{a}", params=_q(), json={"status": "active"})
        client.patch(f"/v1/routines/{b}", params=_q(), json={"status": "active"})
        client.patch(
            f"/v1/routines/{b}",
            params=_q(),
            json={"status": "paused", "pause_reason": "manual"},
        )

        # Filter on draft only.
        resp = client.get("/v1/routines", params={**_q(), "filter[status]": "draft"})
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == c

        # Multi-value OR.
        resp = client.get(
            "/v1/routines",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[status]", "active"),
                ("filter[status]", "paused"),
            ],
        )
        ids = {item["id"] for item in resp.json()["items"]}
        assert ids == {a, b}


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_invalid_transition_returns_409(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # draft → paused is not allowed (must go through active first).
        resp = client.patch(
            f"/v1/routines/{rid}", params=_q(), json={"status": "paused"}
        )
        assert resp.status_code == 409

    def test_errored_must_reset_to_draft_before_active(self) -> None:
        client, store = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # draft → active → errored
        client.patch(f"/v1/routines/{rid}", params=_q(), json={"status": "active"})
        resp = client.patch(
            f"/v1/routines/{rid}",
            params=_q(),
            json={"status": "errored", "pause_reason": "error"},
        )
        assert resp.status_code == 200, resp.text
        # errored → active rejected.
        resp = client.patch(
            f"/v1/routines/{rid}", params=_q(), json={"status": "active"}
        )
        assert resp.status_code == 409
        # errored → draft allowed.
        resp = client.patch(
            f"/v1/routines/{rid}", params=_q(), json={"status": "draft"}
        )
        assert resp.status_code == 200
        # And now draft → active is allowed.
        resp = client.patch(
            f"/v1/routines/{rid}", params=_q(), json={"status": "active"}
        )
        assert resp.status_code == 200

    def test_pause_reason_cleared_on_resume(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        client.patch(f"/v1/routines/{rid}", params=_q(), json={"status": "active"})
        client.patch(
            f"/v1/routines/{rid}",
            params=_q(),
            json={"status": "paused", "pause_reason": "manual"},
        )
        resp = client.patch(
            f"/v1/routines/{rid}", params=_q(), json={"status": "active"}
        )
        assert resp.status_code == 200
        # pause_reason cleared on resume.
        assert resp.json()["pause_reason"] is None

    def test_pause_reason_must_match_status(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Cannot set pause_reason while staying in draft.
        resp = client.patch(
            f"/v1/routines/{rid}",
            params=_q(),
            json={"pause_reason": "manual"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Quota — cross-audit §9.7 Q8 (100 ACTIVE routines per USER)
# ---------------------------------------------------------------------------


class TestQuota:
    def test_101st_active_create_rejected(self) -> None:
        client, store = _client(active_quota=3)
        # Three active routines fit.
        for i in range(3):
            resp = client.post(
                "/v1/routines",
                params=_q(),
                json={**_create_payload(name=f"r-{i}"), "status": "active"},
            )
            assert resp.status_code == 201, resp.text
        # Fourth → 409 quota_exceeded.
        resp = client.post(
            "/v1/routines",
            params=_q(),
            json={**_create_payload(name="r-4"), "status": "active"},
        )
        assert resp.status_code == 409
        assert "quota" in resp.json()["detail"].lower()

    def test_activate_via_patch_quota_gated(self) -> None:
        client, _ = _client(active_quota=2)
        ids = []
        for i in range(3):
            ids.append(
                client.post(
                    "/v1/routines",
                    params=_q(),
                    json=_create_payload(name=f"r-{i}"),
                ).json()["id"]
            )
        # First two activate fine.
        for rid in ids[:2]:
            resp = client.patch(
                f"/v1/routines/{rid}", params=_q(), json={"status": "active"}
            )
            assert resp.status_code == 200
        # Third activation hits the cap.
        resp = client.patch(
            f"/v1/routines/{ids[2]}", params=_q(), json={"status": "active"}
        )
        assert resp.status_code == 409

    def test_quota_is_per_user_not_per_tenant(self) -> None:
        """Two users in the same tenant each get the full quota."""

        client, _ = _client(active_quota=2)
        # Sarah maxes out.
        for i in range(2):
            client.post(
                "/v1/routines",
                params=_q(),
                json={**_create_payload(name=f"sarah-{i}"), "status": "active"},
            )
        # Bob is unaffected.
        resp = client.post(
            "/v1/routines",
            params=_q(user="usr_bob"),
            json={**_create_payload(name="bob-1"), "status": "active"},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_other_tenant_cannot_see_routines(self) -> None:
        client, store = _client()
        routine_id = client.post(
            "/v1/routines", params=_q(), json=_create_payload()
        ).json()["id"]
        cross = {"org_id": "org_zeta", "user_id": "usr_alice_other"}
        # List empty.
        resp = client.get("/v1/routines", params=cross)
        assert resp.json()["items"] == []
        # Direct GET 404s (not 403) — cross-tenant must not leak
        # existence either.
        resp = client.get(f"/v1/routines/{routine_id}", params=cross)
        assert resp.status_code == 404
        # PATCH also 404.
        resp = client.patch(
            f"/v1/routines/{routine_id}", params=cross, json={"status": "active"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project-scoped ACL — cross-audit §1.3
# ---------------------------------------------------------------------------


class TestProjectAcl:
    def test_non_owner_non_member_gets_404(self) -> None:
        client, _ = _client()
        routine_id = client.post(
            "/v1/routines",
            params=_q(),
            json={**_create_payload(), "project_id": "proj_x"},
        ).json()["id"]
        resp = client.get(
            f"/v1/routines/{routine_id}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
        )
        assert resp.status_code == 404, resp.text

    def test_project_member_can_read_but_not_write(self) -> None:
        client, _ = _client(project_memberships={("org_acme", "proj_x"): {"usr_bob"}})
        routine_id = client.post(
            "/v1/routines",
            params=_q(),
            json={**_create_payload(), "project_id": "proj_x"},
        ).json()["id"]
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        # Bob lists — sees the row (project-member read).
        items = client.get("/v1/routines", params=bob_q).json()["items"]
        assert any(item["id"] == routine_id for item in items)
        # Bob tries to write — 403 (read access established, write blocked).
        resp = client.patch(
            f"/v1/routines/{routine_id}", params=bob_q, json={"status": "active"}
        )
        assert resp.status_code == 403
        # Bob tries to delete — same 403.
        resp = client.delete(f"/v1/routines/{routine_id}", params=bob_q)
        assert resp.status_code == 403

    def test_admin_reads_any_routine_in_tenant(self, monkeypatch) -> None:
        """Admin role bypasses owner narrowing on reads."""

        client, _ = _client()
        routine_id = client.post(
            "/v1/routines", params=_q(), json=_create_payload()
        ).json()["id"]
        # Service-token + admin role for the cross-user read.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-tok")
        admin_headers = {
            SERVICE_TOKEN_HEADER: "test-tok",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_bob",
            ROLES_HEADER: "admin",
        }
        resp = client.get(
            f"/v1/routines/{routine_id}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == routine_id


# ---------------------------------------------------------------------------
# Manual fire ACL — cross-audit §9.7 Q2
# ---------------------------------------------------------------------------


class TestManualFireAcl:
    def test_owner_can_always_fire(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        resp = client.post(f"/v1/routines/{rid}/run", params=_q())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fire_id"]
        assert body["run_id"] is None  # P5-A2 deliverable

    def test_default_manual_fire_owner_only(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # Bob has no read access (no project_id) → 404.
        resp = client.post(f"/v1/routines/{rid}/run", params=_q(user="usr_bob"))
        assert resp.status_code == 404

    def test_manual_fire_project_members(self) -> None:
        client, _ = _client(project_memberships={("org_acme", "proj_x"): {"usr_bob"}})
        rid = client.post(
            "/v1/routines",
            params=_q(),
            json={
                **_create_payload(),
                "project_id": "proj_x",
                "permissions": {"manual_fire": "project_members"},
            },
        ).json()["id"]
        # Bob (project member) CAN manual-fire.
        resp = client.post(f"/v1/routines/{rid}/run", params=_q(user="usr_bob"))
        assert resp.status_code == 200
        # Carol (non-member) → 404.
        resp = client.post(f"/v1/routines/{rid}/run", params=_q(user="usr_carol"))
        assert resp.status_code == 404

    def test_manual_fire_tenant_wide(self, monkeypatch) -> None:
        """Tenant-scope override lets any tenant member fire (with read access)."""

        client, _ = _client()
        rid = client.post(
            "/v1/routines",
            params=_q(),
            json={
                **_create_payload(),
                "permissions": {"manual_fire": "tenant"},
            },
        ).json()["id"]
        # Bob has no read access at the routine level (not owner, no
        # project_id, not admin) — manual-fire is gated by read first,
        # so this returns 404. Adding tenant-wide visibility requires
        # the project filing (or admin); the test below confirms the
        # admin path.
        resp = client.post(f"/v1/routines/{rid}/run", params=_q(user="usr_bob"))
        assert resp.status_code == 404

        # With admin role (tenant-wide read), Bob CAN fire.
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-tok")
        admin_headers = {
            SERVICE_TOKEN_HEADER: "test-tok",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_bob",
            ROLES_HEADER: "admin",
        }
        resp = client.post(
            f"/v1/routines/{rid}/run",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_create_rejects_project_members_without_project_id(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/routines",
            params=_q(),
            json={
                **_create_payload(),
                "permissions": {"manual_fire": "project_members"},
            },
        )
        assert resp.status_code == 400

    def test_manual_fire_errored_routine_returns_409(self) -> None:
        client, _ = _client()
        rid = client.post("/v1/routines", params=_q(), json=_create_payload()).json()[
            "id"
        ]
        # draft → active → errored
        client.patch(f"/v1/routines/{rid}", params=_q(), json={"status": "active"})
        client.patch(
            f"/v1/routines/{rid}",
            params=_q(),
            json={"status": "errored", "pause_reason": "error"},
        )
        resp = client.post(f"/v1/routines/{rid}/run", params=_q())
        assert resp.status_code == 409
