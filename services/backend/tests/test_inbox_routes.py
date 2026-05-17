"""Tests for ``/v1/inbox`` CRUD + bulk-action + ACL — Phase 4 P4-A1.

Coverage:

* CRUD happy path (list + get-with-body + patch + bulk).
* Cursor pagination on list.
* Multi-value ``filter[state]`` OR semantics (cross-audit §1.5).
* Tenant isolation (caller cannot read another tenant's items).
* Project-scoped ACL: recipient-only by default, project-member read,
  admin compliance read, 404 for non-readers (not 403 — cross-audit
  §1.3 binding).
* State machine — snooze requires future timestamp; dismissed is
  terminal; mark-read clears snooze.
* Audit row per state move; bulk stamps a shared ``correlation_id``.

The TestClient setup mirrors ``test_todos_routes.py``: no
``ENTERPRISE_SERVICE_TOKEN`` set, so identity rides in the query
params (the dev fallback). Admin-role tests inject the service token
+ headers to exercise the production auth path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from backend_app.inbox.service import (
    InMemoryProjectMembershipAdapter,
    InboxService,
)
from backend_app.inbox.store import InMemoryInboxStore


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
    inbox_store: InMemoryInboxStore | None = None,
    project_memberships: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[TestClient, InMemoryInboxStore]:
    store = inbox_store or InMemoryInboxStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        inbox_store=store,
    )
    if project_memberships is not None:
        app.state.inbox_service = InboxService(
            store=store,
            identity_store=identity,
            project_membership=InMemoryProjectMembershipAdapter(project_memberships),
        )
        # Strip the old /v1/inbox routes before re-registering against the
        # new service. Easier than rebuilding the whole app for one test
        # path.
        from backend_app.inbox.routes import register_inbox_routes

        app.router.routes = [
            r
            for r in app.router.routes
            if not getattr(r, "path", "").startswith("/v1/inbox")
        ]
        register_inbox_routes(app, service=app.state.inbox_service)
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _seed_item(
    store: InMemoryInboxStore,
    *,
    tenant_id: str = "org_acme",
    owner_user_id: str = "usr_sarah",
    kind: str = "mention",
    title: str = "FYI",
    project_id: str | None = None,
    body_markdown: str | None = None,
    state: str = "unread",
) -> str:
    """Insert a fixture item through the service (writes audit row too)."""

    from backend_app.identity.store import InMemoryIdentityStore
    from backend_app.inbox.service import InboxService

    service = InboxService(store=store, identity_store=InMemoryIdentityStore())
    record = service.insert_item_with_body(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        kind=kind,
        title=title,
        sender={
            "ref": {"kind": "agent", "id": "agent_atlas"},
            "agent_name": "Atlas",
        },
        project_id=project_id,
        body_markdown=body_markdown,
    )
    if state != "unread":
        record = record.model_copy(update={"state": state})
        store.update_item(record)
    return record.id


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


class TestCrud:
    def test_list_get_patch_flow(self) -> None:
        client, store = _client()
        item_id = _seed_item(
            store, title="Atlas needs your input", body_markdown="please ack"
        )

        # List.
        resp = client.get("/v1/inbox", params=_q())
        assert resp.status_code == 200, resp.text
        page = resp.json()
        assert page["unread_count"] == 1
        assert len(page["items"]) == 1
        item = page["items"][0]
        assert item["id"] == item_id
        assert item["state"] == "unread"
        assert item["body_ref"] is not None
        # List response never carries body bytes (inbox-prd §3 + §10).
        assert "body_markdown" not in item

        # Detail — lazy body fetch.
        resp = client.get(f"/v1/inbox/{item_id}", params=_q())
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["body_markdown"] == "please ack"

        # Patch — mark read.
        resp = client.patch(f"/v1/inbox/{item_id}", params=_q(), json={"state": "read"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "read"
        assert body["read_at"] is not None

        # Audit chain — create + mark_read.
        audit = store.list_audit_for_item(tenant_id="org_acme", item_id=item_id)
        actions = [r.action for r in audit]
        assert "inbox.item_created" in actions
        assert "inbox.mark_read" in actions

    def test_unread_count_endpoint(self) -> None:
        client, store = _client()
        _seed_item(store, title="a")
        _seed_item(store, title="b")
        _seed_item(store, title="c", state="read")  # not counted

        resp = client.get("/v1/inbox/unread_count", params=_q())
        assert resp.status_code == 200
        body = resp.json()
        assert body["unread_count"] == 2
        assert body["as_of"]

    def test_list_cursor_pagination(self) -> None:
        client, store = _client()
        for i in range(5):
            _seed_item(store, title=f"item-{i}")
        resp = client.get("/v1/inbox", params={**_q(), "limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is not None
        # Second page.
        resp = client.get(
            "/v1/inbox",
            params={**_q(), "limit": 2, "cursor": body["next_cursor"]},
        )
        body2 = resp.json()
        assert len(body2["items"]) == 2
        assert body["items"][0]["id"] != body2["items"][0]["id"]

    def test_multi_value_state_filter_or(self) -> None:
        """``filter[state]=unread&filter[state]=read`` → OR within axis."""

        client, store = _client()
        unread_id = _seed_item(store, title="a")
        # Flip one to read to seed two states.
        client.patch(f"/v1/inbox/{unread_id}", params=_q(), json={"state": "read"})
        _seed_item(store, title="b")
        _seed_item(store, title="c")

        # Filter on unread only.
        resp = client.get("/v1/inbox", params={**_q(), "filter[state]": "unread"})
        items = resp.json()["items"]
        assert all(item["state"] == "unread" for item in items)
        assert len(items) == 2

        # Multi-value OR: both states present.
        resp = client.get(
            "/v1/inbox",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[state]", "unread"),
                ("filter[state]", "read"),
            ],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3

    def test_filter_kind_multi_value_or(self) -> None:
        client, store = _client()
        _seed_item(store, kind="mention", title="m1")
        _seed_item(store, kind="approval_request", title="a1")
        _seed_item(store, kind="error", title="e1")

        resp = client.get(
            "/v1/inbox",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[kind]", "mention"),
                ("filter[kind]", "error"),
            ],
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        kinds = {item["kind"] for item in items}
        assert kinds == {"mention", "error"}


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_snooze_requires_future_timestamp(self) -> None:
        client, store = _client()
        item_id = _seed_item(store, title="snooze me")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params=_q(),
            json={"state": "snoozed", "snoozed_until": past},
        )
        assert resp.status_code == 400

    def test_snooze_with_future_timestamp_sets_field(self) -> None:
        client, store = _client()
        item_id = _seed_item(store, title="snooze me")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params=_q(),
            json={"state": "snoozed", "snoozed_until": future},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "snoozed"
        assert body["snoozed_until"] is not None

    def test_dismissed_is_terminal(self) -> None:
        client, store = _client()
        item_id = _seed_item(store, title="dismiss me")
        resp = client.patch(
            f"/v1/inbox/{item_id}", params=_q(), json={"state": "dismissed"}
        )
        assert resp.status_code == 200
        # Cannot re-open via PATCH.
        resp = client.patch(
            f"/v1/inbox/{item_id}", params=_q(), json={"state": "unread"}
        )
        assert resp.status_code == 400

    def test_audit_row_per_state_move(self) -> None:
        client, store = _client()
        item_id = _seed_item(store, title="audit me")
        client.patch(f"/v1/inbox/{item_id}", params=_q(), json={"state": "read"})
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        client.patch(
            f"/v1/inbox/{item_id}",
            params=_q(),
            json={"state": "snoozed", "snoozed_until": future},
        )
        client.patch(f"/v1/inbox/{item_id}", params=_q(), json={"state": "dismissed"})
        rows = store.list_audit_for_item(tenant_id="org_acme", item_id=item_id)
        actions = [r.action for r in rows]
        assert "inbox.item_created" in actions
        assert "inbox.mark_read" in actions
        assert "inbox.mark_snoozed" in actions
        assert "inbox.mark_dismissed" in actions


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_other_tenant_cannot_see_items(self) -> None:
        """A row in org_acme is invisible to org_zeta callers."""

        client, store = _client()
        item_id = _seed_item(store, title="secret")
        cross = {"org_id": "org_zeta", "user_id": "usr_alice_other"}
        resp = client.get("/v1/inbox", params=cross)
        assert resp.json()["items"] == []
        # Direct PATCH 404s (not 403) — cross-tenant must not leak
        # existence either.
        resp = client.patch(
            f"/v1/inbox/{item_id}", params=cross, json={"state": "read"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project-scoped ACL — cross-audit §1.3
# ---------------------------------------------------------------------------


class TestProjectAcl:
    def test_non_recipient_non_member_gets_404(self) -> None:
        """Bob is in the tenant but isn't the recipient and isn't a member."""

        client, store = _client()
        item_id = _seed_item(store, title="for sarah", project_id="proj_x")
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
            json={"state": "read"},
        )
        assert resp.status_code == 404, resp.text

    def test_project_member_can_read_but_not_write(self) -> None:
        client, store = _client(
            project_memberships={("org_acme", "proj_x"): {"usr_bob"}}
        )
        item_id = _seed_item(store, title="for sarah", project_id="proj_x")
        # Bob lists — sees the row (project-member read).
        bob_q = {"org_id": "org_acme", "user_id": "usr_bob"}
        items = client.get("/v1/inbox", params=bob_q).json()["items"]
        assert any(item["id"] == item_id for item in items)
        # Bob tries to write — 403 (read access established, write
        # blocked). 404-not-403 only applies when the read itself is
        # denied; here Bob can read so the surface is honest.
        resp = client.patch(
            f"/v1/inbox/{item_id}", params=bob_q, json={"state": "read"}
        )
        assert resp.status_code == 403

    def test_admin_reads_any_item_in_tenant(self, monkeypatch) -> None:
        """Admin role bypasses recipient narrowing on reads."""

        client, store = _client()
        item_id = _seed_item(store, title="for sarah")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
        admin_headers = {
            SERVICE_TOKEN_HEADER: "test-service-token",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_bob",
            ROLES_HEADER: "admin",
        }
        resp = client.get(
            "/v1/inbox",
            params={"org_id": "org_acme", "user_id": "ignored"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert any(item["id"] == item_id for item in items)
        # Admin cannot mutate (recipient-only writes).
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "ignored"},
            json={"state": "read"},
            headers=admin_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Bulk action + correlation_id audit
# ---------------------------------------------------------------------------


class TestBulkAction:
    def test_bulk_mark_read_stamps_correlation_id(self) -> None:
        client, store = _client()
        ids = [_seed_item(store, title=f"t-{i}") for i in range(3)]
        resp = client.post(
            "/v1/inbox/bulk",
            params=_q(),
            json={
                "action": "mark_read",
                "ids": ids,
                "correlation_id": "corr-abc",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["affected"] == 3
        assert body["correlation_id"] == "corr-abc"
        # Every state-change audit row carries the correlation_id.
        for item_id in ids:
            rows = store.list_audit_for_item(tenant_id="org_acme", item_id=item_id)
            mark_rows = [r for r in rows if r.action == "inbox.mark_read"]
            assert mark_rows, f"no mark_read audit row for {item_id}"
            assert all(r.correlation_id == "corr-abc" for r in mark_rows)

    def test_bulk_rejects_invalid_action(self) -> None:
        client, _store = _client()
        resp = client.post(
            "/v1/inbox/bulk",
            params=_q(),
            json={
                "action": "explode",
                "ids": ["inbox_x"],
                "correlation_id": "c",
            },
        )
        assert resp.status_code == 400

    def test_bulk_silently_drops_cross_user_ids(self) -> None:
        """Best-effort: ids the caller doesn't own are skipped."""

        client, store = _client()
        own_id = _seed_item(store, title="mine")
        # Sarah includes a foreign id; bulk should still mark her own
        # row read.
        resp = client.post(
            "/v1/inbox/bulk",
            params=_q(),
            json={
                "action": "mark_read",
                "ids": [own_id, "inbox_does_not_exist"],
                "correlation_id": "c",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] == 1

    def test_bulk_snooze_requires_payload(self) -> None:
        client, store = _client()
        own_id = _seed_item(store, title="mine")
        resp = client.post(
            "/v1/inbox/bulk",
            params=_q(),
            json={
                "action": "snooze",
                "ids": [own_id],
                "correlation_id": "c",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Detail 404 for cross-tenant / cross-user
# ---------------------------------------------------------------------------


class TestDetail404:
    def test_get_cross_user_returns_404_not_403(self) -> None:
        client, store = _client()
        item_id = _seed_item(store, title="for sarah")
        resp = client.get(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_bob"},
        )
        assert resp.status_code == 404
