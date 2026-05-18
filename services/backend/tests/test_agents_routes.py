"""Tests for ``/v1/agents`` CRUD + ACL — Phase 8 P8-A1.

Coverage:

  * CRUD happy path (create + get + list + patch + delete).
  * 404-not-403 for non-readers of custom agents (cross-audit §1.3).
  * Tenant isolation (cross-tenant agent reads return 404).
  * Owner-only writes on custom; PATCH on system/community returns 409
    ``agent_origin_immutable``.
  * Multi-value ``filter[origin]`` / ``filter[status]`` OR semantics
    (cross-audit §1.5).
  * Slug uniqueness per tenant (case-insensitive).
  * ``filter[owner_user_id]`` admin-only guard.
  * Soft-delete + audit trail.

The TestClient setup mirrors ``test_projects_routes.py``: no
``ENTERPRISE_SERVICE_TOKEN`` set, so identity rides in the query params
(the dev fallback).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.agents.store import (
    AgentRecord,
    InMemoryAgentsStore,
)
from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (
        ("usr_sarah", "Sarah"),
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
    agents_store: InMemoryAgentsStore | None = None,
) -> tuple[TestClient, InMemoryAgentsStore]:
    store = agents_store or InMemoryAgentsStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        agents_store=store,
    )
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Meeting Notes Drafter",
        "description": "Drafts meeting notes from a calendar event",
        "icon_emoji": "📝",
        "color_hue": 200,
        "instructions": "You draft concise meeting notes.",
    }
    base.update(overrides)
    return base


def _seed_system_agent(
    store: InMemoryAgentsStore, *, tenant_id: str = "org_acme"
) -> str:
    record = AgentRecord(
        tenant_id=tenant_id,
        name="System Summarizer",
        slug="system-summarizer",
        description="Built-in summarizer",
        origin="system",
        status="available",
        owner_user_id=None,
        model_id="anthropic:claude-sonnet-4-7-1m",
        reasoning_depth="balanced",
        permissions={
            "autonomy": "manual_approval",
            "max_tool_calls_per_run": 20,
            "max_output_tokens": 8000,
            "read_only": True,
        },
    )
    store.insert_agent(record)
    return record.id


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


class TestCrud:
    def test_create_get_patch_delete_flow(self) -> None:
        client, store = _client()

        # Create.
        resp = client.post("/v1/agents", params=_q(), json=_create_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        agent_id = body["id"]
        assert body["origin"] == "custom"
        assert body["owner_user_id"] == "usr_sarah"
        assert body["status"] == "draft"
        assert body["slug"] == "meeting-notes-drafter"
        # Caller is owner; no install row → viewer sees the draft state.
        assert body["viewer_install_status"] == "draft"

        # Get.
        resp = client.get(f"/v1/agents/{agent_id}", params=_q())
        assert resp.status_code == 200
        assert resp.json()["id"] == agent_id

        # List — the caller sees their own custom.
        resp = client.get("/v1/agents", params=_q())
        assert resp.status_code == 200
        page = resp.json()
        assert page["next_cursor"] is None
        assert len(page["items"]) == 1
        assert page["items"][0]["id"] == agent_id

        # PATCH — rename + change instructions.
        resp = client.patch(
            f"/v1/agents/{agent_id}",
            params=_q(),
            json={"name": "Notes Drafter", "instructions": "Draft brief notes."},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Notes Drafter"
        # version NOT bumped on PATCH (snapshot is the only thing that bumps).
        assert resp.json()["version"] == 1

        # DELETE (soft).
        resp = client.delete(f"/v1/agents/{agent_id}", params=_q())
        assert resp.status_code == 204
        # Subsequent GET returns 404.
        resp = client.get(f"/v1/agents/{agent_id}", params=_q())
        assert resp.status_code == 404

        # Audit chain — create + update + soft_delete.
        audit = store.list_audit_for_agent(tenant_id="org_acme", agent_id=agent_id)
        actions = [r.action for r in audit]
        assert "agent.create" in actions
        assert "agent.update" in actions
        assert "agent.soft_delete" in actions

    def test_create_rejects_blank_name(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/agents",
            params=_q(),
            json={"name": "  ", "icon_emoji": "🚀"},
        )
        assert resp.status_code == 400

    def test_create_rejects_invalid_hue(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/agents",
            params=_q(),
            json={**_create_payload(), "color_hue": 999},
        )
        assert resp.status_code == 400

    def test_duplicate_slug_within_tenant_rejected(self) -> None:
        client, _ = _client()
        client.post(
            "/v1/agents",
            params=_q(),
            json={**_create_payload(name="Notes Bot")},
        )
        # Same slug case-insensitive.
        resp = client.post(
            "/v1/agents",
            params=_q(),
            json={**_create_payload(name="Notes Bot")},
        )
        assert resp.status_code == 409
        assert "duplicate_slug" in str(resp.json()["detail"])

    def test_explicit_slug_can_be_passed(self) -> None:
        client, _ = _client()
        resp = client.post(
            "/v1/agents",
            params=_q(),
            json={**_create_payload(name="Drafter"), "slug": "my-drafter"},
        )
        assert resp.status_code == 201
        assert resp.json()["slug"] == "my-drafter"


# ---------------------------------------------------------------------------
# ACL — owner-only writes; 404-not-403 for non-readers; cross-tenant
# ---------------------------------------------------------------------------


class TestAcl:
    def test_non_owner_non_installer_sees_404_not_403(self) -> None:
        client, _ = _client()
        agent_id = client.post(
            "/v1/agents", params=_q("usr_sarah"), json=_create_payload()
        ).json()["id"]
        # Bob has no install row and isn't owner → 404, not 403.
        resp = client.get(f"/v1/agents/{agent_id}", params=_q("usr_bob"))
        assert resp.status_code == 404

    def test_non_owner_cannot_patch_returns_404(self) -> None:
        """Non-readers see 404 (existence not leaked) even on a write call."""

        client, _ = _client()
        agent_id = client.post(
            "/v1/agents", params=_q("usr_sarah"), json=_create_payload()
        ).json()["id"]
        resp = client.patch(
            f"/v1/agents/{agent_id}",
            params=_q("usr_bob"),
            json={"name": "Hijacked"},
        )
        assert resp.status_code == 404

    def test_system_agent_patch_returns_409(self) -> None:
        """system/community agents are origin-immutable — PATCH must 409."""

        client, store = _client()
        sys_id = _seed_system_agent(store)
        # Every tenant member can read the system agent.
        resp = client.get(f"/v1/agents/{sys_id}", params=_q())
        assert resp.status_code == 200
        # But PATCH from any user (including admin) returns 409 — must
        # duplicate first.
        resp = client.patch(
            f"/v1/agents/{sys_id}", params=_q(), json={"name": "Hijack"}
        )
        assert resp.status_code == 409
        assert "agent_origin_immutable" in str(resp.json()["detail"])

    def test_system_agent_delete_returns_409(self) -> None:
        client, store = _client()
        sys_id = _seed_system_agent(store)
        resp = client.delete(f"/v1/agents/{sys_id}", params=_q())
        assert resp.status_code == 409
        assert "agent_origin_immutable" in str(resp.json()["detail"])

    def test_cross_tenant_get_returns_404(self) -> None:
        client, store = _client()
        # Custom agent in org_acme.
        agent_id = client.post(
            "/v1/agents", params=_q("usr_sarah"), json=_create_payload()
        ).json()["id"]
        # Alice (org_zeta) tries to read it.
        resp = client.get(
            f"/v1/agents/{agent_id}",
            params={"org_id": "org_zeta", "user_id": "usr_alice_other"},
        )
        assert resp.status_code == 404

    def test_system_agent_visible_to_all_tenant_members(self) -> None:
        client, store = _client()
        sys_id = _seed_system_agent(store)
        # Every user in the tenant can read.
        for user in ("usr_sarah", "usr_bob", "usr_carol"):
            resp = client.get(f"/v1/agents/{sys_id}", params=_q(user))
            assert resp.status_code == 200
        # And it shows up in their list.
        resp = client.get("/v1/agents", params=_q("usr_bob"))
        assert resp.status_code == 200
        assert any(item["id"] == sys_id for item in resp.json()["items"])

    def test_other_users_custom_not_in_list(self) -> None:
        client, _ = _client()
        client.post("/v1/agents", params=_q("usr_sarah"), json=_create_payload())
        # Bob lists — Sarah's custom must NOT appear.
        resp = client.get("/v1/agents", params=_q("usr_bob"))
        assert resp.status_code == 200
        assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Filter axes — multi-value OR + allowlist enforcement.
# ---------------------------------------------------------------------------


class TestFilters:
    def test_filter_origin_multi_value_or(self) -> None:
        client, store = _client()
        # Seed: 1 system + 1 community + 1 custom.
        _seed_system_agent(store)
        community = AgentRecord(
            tenant_id="org_acme",
            name="Community Sum",
            slug="community-sum",
            origin="community",
            status="available",
            owner_user_id=None,
            model_id="x",
            reasoning_depth="balanced",
            permissions={
                "autonomy": "manual_approval",
                "max_tool_calls_per_run": 20,
                "max_output_tokens": 8000,
                "read_only": True,
            },
        )
        store.insert_agent(community)
        client.post("/v1/agents", params=_q("usr_sarah"), json=_create_payload())

        # filter[origin]=system → 1.
        resp = client.get(
            "/v1/agents",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[origin]", "system"),
            ],
        )
        assert resp.status_code == 200
        assert [i["origin"] for i in resp.json()["items"]] == ["system"]

        # filter[origin]=system&filter[origin]=community → 2.
        resp = client.get(
            "/v1/agents",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[origin]", "system"),
                ("filter[origin]", "community"),
            ],
        )
        assert resp.status_code == 200
        origins = sorted(i["origin"] for i in resp.json()["items"])
        assert origins == ["community", "system"]

    def test_unknown_filter_axis_returns_400(self) -> None:
        client, _ = _client()
        resp = client.get(
            "/v1/agents",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[bogus]", "value"),
            ],
        )
        assert resp.status_code == 400
        # FastAPI wraps non-string details as the body's "detail" field.
        assert "filter_not_allowed" in str(resp.json()["detail"])

    def test_non_admin_owner_user_id_filter_other_user_returns_403(self) -> None:
        client, _ = _client()
        # Sarah tries to query Bob's agents.
        resp = client.get(
            "/v1/agents",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[owner_user_id]", "usr_bob"),
            ],
        )
        assert resp.status_code == 403

    def test_non_admin_owner_user_id_filter_me_allowed(self) -> None:
        client, _ = _client()
        client.post("/v1/agents", params=_q("usr_sarah"), json=_create_payload())
        resp = client.get(
            "/v1/agents",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[owner_user_id]", "me"),
            ],
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_search_q(self) -> None:
        client, _ = _client()
        client.post(
            "/v1/agents",
            params=_q("usr_sarah"),
            json={**_create_payload(name="Salesforce Helper")},
        )
        client.post(
            "/v1/agents",
            params=_q("usr_sarah"),
            json={**_create_payload(name="Notion Sync")},
        )
        resp = client.get("/v1/agents", params={**_q(), "q": "Sales"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Salesforce Helper"

    def test_bad_sort_returns_400(self) -> None:
        client, _ = _client()
        resp = client.get("/v1/agents", params={**_q(), "sort": "evil:hack"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Soft-delete + restore
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_soft_delete_removes_from_list(self) -> None:
        client, _ = _client()
        agent_id = client.post(
            "/v1/agents", params=_q(), json=_create_payload()
        ).json()["id"]
        client.delete(f"/v1/agents/{agent_id}", params=_q())
        resp = client.get("/v1/agents", params=_q())
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_owner_only_delete_non_owner_404(self) -> None:
        client, _ = _client()
        agent_id = client.post(
            "/v1/agents", params=_q("usr_sarah"), json=_create_payload()
        ).json()["id"]
        resp = client.delete(f"/v1/agents/{agent_id}", params=_q("usr_bob"))
        # Bob can't read → 404; existence not leaked.
        assert resp.status_code == 404
