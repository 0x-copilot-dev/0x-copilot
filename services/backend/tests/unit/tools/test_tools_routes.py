"""Tests for ``/v1/tools`` routes — Phase 10 P10-A2.

Coverage:

* CRUD happy path (create + get + list + patch + delete) via TestClient.
* 404-not-403 for non-readers of project-scoped tools (cross-audit §1.3).
* Tenant isolation (cross-tenant tool reads return 404).
* Owner-only writes; project member with read gets 403 on PATCH /
  DELETE.
* Multi-value ``filter[kind]`` OR semantics (cross-audit §1.5).
* Sort allowlist enforcement (§4.12) — bad sort = 400.
* Test-call route returns 501 (P10-A3 will wire the executor).
* Disable / enable flip status and audit.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.store import InMemoryProjectsStore
from backend_app.tools.store import InMemoryToolsStore


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
    tools_store: InMemoryToolsStore | None = None,
    projects_store: InMemoryProjectsStore | None = None,
) -> tuple[TestClient, InMemoryToolsStore, InMemoryProjectsStore]:
    tools = tools_store or InMemoryToolsStore()
    proj = projects_store or InMemoryProjectsStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        projects_store=proj,
        tools_store=tools,
    )
    return TestClient(app), tools, proj


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "mcp",
        "name": "Slack summarize",
        "description": "Reads recent channel messages.",
        "scope": "read",
        "transport": {"kind": "mcp"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


class TestCrud:
    def test_create_get_list_patch_delete(self) -> None:
        client, _, _ = _client()

        # Create.
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        tool_id = body["id"]
        assert tool_id.startswith("tool_")
        assert body["kind"] == "mcp"
        assert body["status"] == "enabled"
        assert body["owner_user_id"] == "usr_sarah"
        assert body["usage"]["calls_30d"] == 0

        # Get.
        resp = client.get(f"/v1/tools/{tool_id}", params=_q())
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["tool"]["id"] == tool_id
        assert detail["consumers"] == {
            "agents": [],
            "routines": [],
            "chats_with_grant": 0,
        }

        # List.
        resp = client.get("/v1/tools", params=_q())
        assert resp.status_code == 200
        page = resp.json()
        assert page["next_cursor"] is None
        assert len(page["tools"]) == 1

        # PATCH.
        resp = client.patch(
            f"/v1/tools/{tool_id}",
            params=_q(),
            json={"name": "Slack summarize v2", "tags": ["ops"]},
        )
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["name"] == "Slack summarize v2"
        assert patched["tags"] == ["ops"]

        # DELETE (soft).
        resp = client.delete(f"/v1/tools/{tool_id}", params=_q())
        assert resp.status_code == 204
        # Subsequent GET → 404.
        resp = client.get(f"/v1/tools/{tool_id}", params=_q())
        assert resp.status_code == 404

    def test_create_rejects_blank_name(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload(name="  "))
        assert resp.status_code == 400

    def test_create_rejects_invalid_kind(self) -> None:
        client, _, _ = _client()
        resp = client.post(
            "/v1/tools",
            params=_q(),
            json=_create_payload(kind="invalid"),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ACL — 404-not-403 + tenant isolation
# ---------------------------------------------------------------------------


class TestAcl:
    def test_project_scoped_tool_404_for_non_member(self) -> None:
        client, _, _ = _client()
        resp = client.post(
            "/v1/tools",
            params=_q(),
            json=_create_payload(project_id="proj_alpha"),
        )
        tool_id = resp.json()["id"]
        # carol is not a member of proj_alpha.
        resp = client.get(f"/v1/tools/{tool_id}", params=_q("usr_carol"))
        assert resp.status_code == 404

    def test_cross_tenant_404(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        resp = client.get(
            f"/v1/tools/{tool_id}",
            params={"org_id": "org_zeta", "user_id": "usr_alice_other"},
        )
        assert resp.status_code == 404

    def test_project_member_with_read_cannot_patch(self) -> None:
        # Provision a project and add bob as a member; sarah owns it.
        client, _, projects = _client()
        from backend_app.projects.store import (
            ProjectMembershipRecord,
            ProjectRecord,
        )

        projects.insert_project(
            ProjectRecord(
                id="proj_alpha",
                tenant_id="org_acme",
                name="Alpha",
                owner_user_id="usr_sarah",
            )
        )
        # Owner row + bob's editor row — the canonical
        # ``_StoreBackedMembershipAdapter`` reads from this dict.
        for user_id, role in (("usr_sarah", "owner"), ("usr_bob", "editor")):
            projects.insert_membership(
                ProjectMembershipRecord(
                    project_id="proj_alpha",
                    user_id=user_id,
                    tenant_id="org_acme",
                    role=role,
                    added_by="usr_sarah",
                )
            )

        resp = client.post(
            "/v1/tools",
            params=_q(),
            json=_create_payload(project_id="proj_alpha"),
        )
        tool_id = resp.json()["id"]

        # Bob can READ via project membership.
        resp = client.get(f"/v1/tools/{tool_id}", params=_q("usr_bob"))
        assert resp.status_code == 200

        # Bob cannot PATCH (owner-or-admin only).
        resp = client.patch(
            f"/v1/tools/{tool_id}",
            params=_q("usr_bob"),
            json={"name": "by bob"},
        )
        assert resp.status_code == 403

        # Bob cannot DELETE.
        resp = client.delete(f"/v1/tools/{tool_id}", params=_q("usr_bob"))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Filter + sort allowlist
# ---------------------------------------------------------------------------


class TestFiltersAndSort:
    def test_multi_value_kind_filter_or(self) -> None:
        client, _, _ = _client()
        client.post(
            "/v1/tools", params=_q(), json=_create_payload(name="A", kind="mcp")
        )
        client.post(
            "/v1/tools",
            params=_q(),
            json=_create_payload(
                name="B",
                kind="code",
                transport={"kind": "sandbox", "executor": "py"},
                code_ref={
                    "repo_ref": {"kind": "library_page", "id": "libpage_x"},
                    "env_ref": {"kind": "library_page", "id": "libpage_e"},
                    "entry": "main",
                },
            ),
        )
        client.post(
            "/v1/tools",
            params=_q(),
            json=_create_payload(name="C", kind="builtin"),
        )

        resp = client.get(
            "/v1/tools",
            params=[*_q().items(), ("filter[kind]", "mcp"), ("filter[kind]", "code")],
        )
        assert resp.status_code == 200
        names = sorted(t["name"] for t in resp.json()["tools"])
        assert names == ["A", "B"]

    def test_unknown_filter_axis_rejected(self) -> None:
        client, _, _ = _client()
        resp = client.get(
            "/v1/tools",
            params=[*_q().items(), ("filter[unknown_axis]", "x")],
        )
        assert resp.status_code == 400

    def test_bad_sort_rejected(self) -> None:
        client, _, _ = _client()
        resp = client.get(
            "/v1/tools",
            params={**_q(), "sort": "random_field"},
        )
        assert resp.status_code == 400

    def test_filter_kind_invalid_value_rejected(self) -> None:
        client, _, _ = _client()
        resp = client.get(
            "/v1/tools",
            params=[*_q().items(), ("filter[kind]", "not_a_kind")],
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Enable / disable + test-call stub
# ---------------------------------------------------------------------------


class TestStatusFlips:
    def test_disable_enable_round_trip(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        resp = client.post(
            f"/v1/tools/{tool_id}/disable",
            params=_q(),
            json={"reason": "user paused"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        resp = client.post(f"/v1/tools/{tool_id}/enable", params=_q(), json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "enabled"

    def test_test_call_returns_501_stub(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        resp = client.post(
            f"/v1/tools/{tool_id}/test",
            params=_q(),
            json={"args": {"channel": "general"}},
        )
        assert resp.status_code == 501

    def test_test_call_404_for_missing_tool(self) -> None:
        client, _, _ = _client()
        resp = client.post(
            "/v1/tools/tool_missing/test",
            params=_q(),
            json={"args": {}},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Usage + invocations endpoints
# ---------------------------------------------------------------------------


class TestUsageAndInvocations:
    def test_usage_endpoint_zero_state(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        resp = client.get(f"/v1/tools/{tool_id}/usage", params=_q())
        assert resp.status_code == 200
        body = resp.json()
        assert body["tool_id"] == tool_id
        for window in ("window_24h", "window_7d", "window_30d"):
            assert body["windows"][window]["calls"] == 0

    def test_invocations_endpoint_empty(self) -> None:
        client, _, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        resp = client.get(f"/v1/tools/{tool_id}/invocations", params=_q())
        assert resp.status_code == 200
        assert resp.json() == {"invocations": [], "next_cursor": None}

    def test_invocations_filter_caller_kind(self) -> None:
        client, store, _ = _client()
        resp = client.post("/v1/tools", params=_q(), json=_create_payload())
        tool_id = resp.json()["id"]
        # Seed two invocations directly through the store (the service-
        # token-gated internal route is exercised elsewhere).
        from backend_app.tools.store import ToolInvocationRecord

        store.insert_invocation(
            ToolInvocationRecord(
                tool_id=tool_id,
                tenant_id="org_acme",
                run_id="run_1",
                caller_kind="agent",
                caller_ref={"kind": "agent", "id": "agt_1"},
                args_summary="",
                status="ok",
                latency_ms=10,
            )
        )
        store.insert_invocation(
            ToolInvocationRecord(
                tool_id=tool_id,
                tenant_id="org_acme",
                run_id="run_2",
                caller_kind="chat",
                caller_ref={"kind": "chat", "id": "chat_1"},
                args_summary="",
                status="ok",
                latency_ms=10,
            )
        )
        resp = client.get(
            f"/v1/tools/{tool_id}/invocations",
            params=[*_q().items(), ("filter[caller_kind]", "agent")],
        )
        assert resp.status_code == 200
        rows = resp.json()["invocations"]
        assert [r["caller_kind"] for r in rows] == ["agent"]
