"""Tests for the in-memory tools store — Phase 10 P10-A2.

Coverage:

* CRUD happy path (insert + get + update + soft-delete) on the in-memory
  adapter.
* Tenant isolation (caller cannot read another tenant's tools).
* Multi-axis filter composition (kind / scope / status / project_id /
  owner_user_id / tag / q) — multi-value OR per cross-audit §1.5.
* Visibility predicate: project-scoped tools hide from non-members;
  tenant-scoped tools (project_id null) are tenant-readable.
* Cursor pagination on list_tools.
* Soft-delete hides from default reads; include_deleted=True returns it.
* Invocation list pagination (after_id + since) and per-status filter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend_app.tools.store import (
    InMemoryToolsStore,
    ToolInvocationRecord,
    ToolRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tool(
    *,
    tenant_id: str = "org_acme",
    owner: str = "usr_sarah",
    name: str = "Slack summarize",
    kind: str = "mcp",
    scope: str = "read",
    status: str = "enabled",
    project_id: str | None = None,
    tags: list[str] | None = None,
) -> ToolRecord:
    return ToolRecord(
        tenant_id=tenant_id,
        owner_user_id=owner,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        project_id=project_id,
        tags=tags or [],
        transport={"kind": "mcp"},
    )


class TestStoreCrud:
    def test_insert_get_update_soft_delete(self) -> None:
        store = InMemoryToolsStore()
        record = store.insert_tool(_tool())
        assert record.id.startswith("tool_")
        # get
        got = store.get_tool(tenant_id="org_acme", tool_id=record.id)
        assert got is not None
        assert got.id == record.id
        # update
        updated = record.model_copy(update={"name": "Slack v2", "updated_at": _now()})
        store.update_tool(updated)
        assert (
            store.get_tool(tenant_id="org_acme", tool_id=record.id).name == "Slack v2"
        )
        # soft delete
        assert store.soft_delete_tool(tenant_id="org_acme", tool_id=record.id)
        assert store.get_tool(tenant_id="org_acme", tool_id=record.id) is None
        # include_deleted=True surfaces it
        ghost = store.get_tool(
            tenant_id="org_acme", tool_id=record.id, include_deleted=True
        )
        assert ghost is not None
        assert ghost.deleted_at is not None

    def test_idempotent_soft_delete(self) -> None:
        store = InMemoryToolsStore()
        record = store.insert_tool(_tool())
        assert store.soft_delete_tool(tenant_id="org_acme", tool_id=record.id)
        # Second delete is a no-op but still returns True.
        assert store.soft_delete_tool(tenant_id="org_acme", tool_id=record.id)

    def test_cross_tenant_get_returns_none(self) -> None:
        store = InMemoryToolsStore()
        record = store.insert_tool(_tool(tenant_id="org_acme"))
        assert store.get_tool(tenant_id="org_zeta", tool_id=record.id) is None


class TestStoreList:
    def test_tenant_isolation(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(_tool(tenant_id="org_acme", name="A"))
        store.insert_tool(_tool(tenant_id="org_zeta", name="B"))
        rows, _ = store.list_tools(tenant_id="org_acme")
        assert {r.tenant_id for r in rows} == {"org_acme"}
        assert {r.name for r in rows} == {"A"}

    def test_filter_kind_or(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(_tool(name="A", kind="mcp"))
        store.insert_tool(_tool(name="B", kind="code"))
        store.insert_tool(_tool(name="C", kind="builtin"))
        rows, _ = store.list_tools(tenant_id="org_acme", kinds=("mcp", "code"))
        assert sorted(r.name for r in rows) == ["A", "B"]

    def test_filter_status_scope_tag(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(
            _tool(name="A", status="enabled", scope="read", tags=["prod"])
        )
        store.insert_tool(
            _tool(name="B", status="disabled", scope="read", tags=["dev"])
        )
        store.insert_tool(
            _tool(name="C", status="enabled", scope="write", tags=["prod"])
        )
        rows, _ = store.list_tools(
            tenant_id="org_acme",
            statuses=("enabled",),
            scopes=("read",),
            tags=("prod",),
        )
        assert [r.name for r in rows] == ["A"]

    def test_q_text_search(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(_tool(name="Slack summarize"))
        store.insert_tool(_tool(name="Salesforce update"))
        rows, _ = store.list_tools(tenant_id="org_acme", q="slack")
        assert [r.name for r in rows] == ["Slack summarize"]

    def test_pagination_cursor(self) -> None:
        store = InMemoryToolsStore()
        for i in range(5):
            store.insert_tool(_tool(name=f"tool-{i}"))
        rows, next_cursor = store.list_tools(tenant_id="org_acme", limit=2, sort="name")
        assert len(rows) == 2
        assert next_cursor is not None
        rows2, next_cursor2 = store.list_tools(
            tenant_id="org_acme", limit=2, sort="name", cursor=next_cursor
        )
        assert len(rows2) == 2
        assert rows[0].id != rows2[0].id

    def test_visibility_project_scoped_hidden_from_non_member(self) -> None:
        store = InMemoryToolsStore()
        # Tenant-scoped tool — readable by everyone in tenant.
        store.insert_tool(_tool(name="public-tool"))
        # Project-scoped tool.
        store.insert_tool(
            _tool(name="proj-tool", project_id="proj_alpha", owner="usr_other")
        )
        # Caller is NOT a member of proj_alpha; sees only the public tool.
        rows, _ = store.list_tools(
            tenant_id="org_acme",
            visible_to_user_id="usr_carol",
            readable_project_ids=(),
        )
        assert [r.name for r in rows] == ["public-tool"]

    def test_visibility_project_member_sees_project_tool(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(
            _tool(name="proj-tool", project_id="proj_alpha", owner="usr_other")
        )
        rows, _ = store.list_tools(
            tenant_id="org_acme",
            visible_to_user_id="usr_carol",
            readable_project_ids=("proj_alpha",),
        )
        assert [r.name for r in rows] == ["proj-tool"]

    def test_owner_always_sees_own_project_tool(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(
            _tool(name="proj-tool", project_id="proj_alpha", owner="usr_sarah")
        )
        rows, _ = store.list_tools(
            tenant_id="org_acme",
            visible_to_user_id="usr_sarah",
            readable_project_ids=(),
        )
        assert [r.name for r in rows] == ["proj-tool"]

    def test_admin_short_circuits_visibility(self) -> None:
        store = InMemoryToolsStore()
        store.insert_tool(
            _tool(name="proj-tool", project_id="proj_alpha", owner="usr_other")
        )
        rows, _ = store.list_tools(
            tenant_id="org_acme",
            visible_to_user_id="usr_dave_admin",
            readable_project_ids=(),
            admin=True,
        )
        assert [r.name for r in rows] == ["proj-tool"]


class TestStoreInvocations:
    def test_insert_and_list_per_tool(self) -> None:
        store = InMemoryToolsStore()
        store.insert_invocation(
            ToolInvocationRecord(
                tool_id="tool_a",
                tenant_id="org_acme",
                run_id="run_1",
                caller_kind="agent",
                caller_ref={"kind": "agent", "id": "agt_1"},
                args_summary="{}",
                status="ok",
                latency_ms=120,
            )
        )
        store.insert_invocation(
            ToolInvocationRecord(
                tool_id="tool_b",
                tenant_id="org_acme",
                run_id="run_2",
                caller_kind="chat",
                caller_ref={"kind": "chat", "id": "chat_1"},
                args_summary="{}",
                status="error",
                error_kind="timeout",
                latency_ms=30_000,
            )
        )
        rows, _ = store.list_invocations(tenant_id="org_acme", tool_id="tool_a")
        assert [r.tool_id for r in rows] == ["tool_a"]

    def test_filter_caller_kind_and_status(self) -> None:
        store = InMemoryToolsStore()
        for i, (kind, status) in enumerate(
            [("agent", "ok"), ("chat", "error"), ("routine", "ok")]
        ):
            store.insert_invocation(
                ToolInvocationRecord(
                    tool_id="tool_x",
                    tenant_id="org_acme",
                    run_id=f"run_{i}",
                    caller_kind=kind,  # type: ignore[arg-type]
                    caller_ref={"kind": kind, "id": f"id_{i}"},
                    args_summary="",
                    status=status,  # type: ignore[arg-type]
                    latency_ms=10,
                )
            )
        rows, _ = store.list_invocations(
            tenant_id="org_acme",
            tool_id="tool_x",
            caller_kinds=("agent", "routine"),
            statuses=("ok",),
        )
        assert {r.caller_kind for r in rows} == {"agent", "routine"}
        assert all(r.status == "ok" for r in rows)

    def test_since_filter(self) -> None:
        store = InMemoryToolsStore()
        now = _now()
        old = ToolInvocationRecord(
            tool_id="tool_x",
            tenant_id="org_acme",
            run_id="r_old",
            caller_kind="agent",
            caller_ref={"kind": "agent", "id": "a"},
            args_summary="",
            status="ok",
            latency_ms=1,
            started_at=now - timedelta(days=2),
            ended_at=now - timedelta(days=2),
        )
        fresh = ToolInvocationRecord(
            tool_id="tool_x",
            tenant_id="org_acme",
            run_id="r_new",
            caller_kind="agent",
            caller_ref={"kind": "agent", "id": "a"},
            args_summary="",
            status="ok",
            latency_ms=1,
        )
        store.insert_invocation(old)
        store.insert_invocation(fresh)
        rows, _ = store.list_invocations(
            tenant_id="org_acme",
            tool_id="tool_x",
            since=now - timedelta(days=1),
        )
        assert [r.run_id for r in rows] == ["r_new"]
