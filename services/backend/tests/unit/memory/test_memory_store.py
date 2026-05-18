"""Unit tests for :class:`InMemoryMemoryStore` — Phase 12 P12-A3.

Coverage:

* CRUD: insert / get / update / soft-delete / touch.
* Soft-delete leaves rows out of public reads but visible under
  ``include_deleted=True``.
* Tenant isolation — a get / list scoped to tenant_a never returns
  tenant_b rows.
* Sort + cursor pagination on list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend_app.memory.store import (
    InMemoryMemoryStore,
    MemoryAuditRecord,
    MemoryItemRecord,
    MemoryProposalRecord,
)


def _record(
    *,
    tenant_id: str = "org_acme",
    owner: str = "usr_sarah",
    scope: str = "user",
    kind: str = "skill",
    title: str = "Speaks Python",
    body: str = "",
    project_id: str | None = None,
) -> MemoryItemRecord:
    return MemoryItemRecord(
        tenant_id=tenant_id,
        owner_user_id=owner,
        scope=scope,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        title=title,
        body=body,
        created_by={"kind": "user", "id": owner},
        project_id=project_id,
    )


def test_insert_get_round_trip() -> None:
    store = InMemoryMemoryStore()
    inserted = store.insert_item(_record(title="speaks python"))
    fetched = store.get_item(tenant_id="org_acme", item_id=inserted.id)
    assert fetched is not None
    assert fetched.id == inserted.id
    assert fetched.title == "speaks python"


def test_soft_delete_hides_from_public_get() -> None:
    store = InMemoryMemoryStore()
    inserted = store.insert_item(_record())
    deleted = store.soft_delete_item(tenant_id="org_acme", item_id=inserted.id)
    assert deleted is not None
    assert deleted.deleted_at is not None
    # Public path: deleted -> None.
    assert store.get_item(tenant_id="org_acme", item_id=inserted.id) is None
    # Compliance read returns it.
    visible = store.get_item(
        tenant_id="org_acme", item_id=inserted.id, include_deleted=True
    )
    assert visible is not None
    assert visible.deleted_at is not None


def test_tenant_isolation_get() -> None:
    store = InMemoryMemoryStore()
    inserted = store.insert_item(_record(tenant_id="org_acme"))
    # Cross-tenant get returns None.
    assert store.get_item(tenant_id="org_zeta", item_id=inserted.id) is None


def test_tenant_isolation_list() -> None:
    store = InMemoryMemoryStore()
    store.insert_item(_record(tenant_id="org_acme", title="acme-row"))
    store.insert_item(_record(tenant_id="org_zeta", title="zeta-row"))
    rows, _ = store.list_items(tenant_id="org_acme")
    titles = {r.title for r in rows}
    assert "acme-row" in titles
    assert "zeta-row" not in titles


def test_list_filters_by_scope_and_kind() -> None:
    store = InMemoryMemoryStore()
    store.insert_item(_record(kind="skill", scope="user", title="a"))
    store.insert_item(_record(kind="fact", scope="user", title="b"))
    store.insert_item(_record(kind="skill", scope="workspace", title="c"))
    rows, _ = store.list_items(tenant_id="org_acme", kinds=("skill",))
    titles = {r.title for r in rows}
    assert titles == {"a", "c"}
    rows, _ = store.list_items(tenant_id="org_acme", scopes=("workspace",))
    assert {r.title for r in rows} == {"c"}


def test_list_q_filter_matches_title_body_tags() -> None:
    store = InMemoryMemoryStore()
    store.insert_item(_record(title="Python expert", body="loves Django"))
    store.insert_item(_record(title="Java expert"))
    rows, _ = store.list_items(tenant_id="org_acme", q="django")
    assert len(rows) == 1
    assert rows[0].title == "Python expert"


def test_list_sort_last_used_desc_nulls_last() -> None:
    store = InMemoryMemoryStore()
    store.insert_item(_record(title="cold"))
    r2 = store.insert_item(_record(title="hot"))
    # Touch r2 (warm it).
    store.touch_item(tenant_id="org_acme", item_id=r2.id)
    rows, _ = store.list_items(tenant_id="org_acme", sort="last_used:desc")
    assert rows[0].title == "hot"
    assert rows[1].title == "cold"


def test_list_cursor_pagination() -> None:
    store = InMemoryMemoryStore()
    for i in range(5):
        store.insert_item(_record(title=f"r{i}"))
    page1, cursor = store.list_items(tenant_id="org_acme", limit=2)
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = store.list_items(tenant_id="org_acme", limit=2, cursor=cursor)
    assert len(page2) == 2
    # Disjoint pages.
    seen = {r.id for r in page1}
    assert all(r.id not in seen for r in page2)


def test_touch_bumps_last_used_at() -> None:
    store = InMemoryMemoryStore()
    inserted = store.insert_item(_record())
    assert inserted.last_used_at is None
    when = datetime.now(timezone.utc) + timedelta(seconds=1)
    touched = store.touch_item(tenant_id="org_acme", item_id=inserted.id, now=when)
    assert touched is not None
    assert touched.last_used_at == when


def test_proposal_round_trip_and_status_filter() -> None:
    store = InMemoryMemoryStore()
    proposal = store.insert_proposal(
        MemoryProposalRecord(
            tenant_id="org_acme",
            user_id="usr_sarah",
            proposed_kind="preference",
            proposed_title="Sign off with 'Best, Parth'",
            proposed_body="",
            source={"kind": "chat", "id": "chat_x"},
        )
    )
    rows, _ = store.list_proposals(
        tenant_id="org_acme",
        user_id="usr_sarah",
        statuses=("pending",),
    )
    assert any(r.id == proposal.id for r in rows)
    # Cross-user proposal list returns nothing.
    rows, _ = store.list_proposals(
        tenant_id="org_acme", user_id="usr_bob", statuses=("pending",)
    )
    assert rows == ()


def test_audit_append_and_query() -> None:
    store = InMemoryMemoryStore()
    audit = store.append_audit(
        MemoryAuditRecord(
            tenant_id="org_acme",
            actor_user_id="usr_sarah",
            action="memory.created",
            target_id="mem_test",
        )
    )
    rows = store.list_audit_for_target(tenant_id="org_acme", target_id="mem_test")
    assert audit in rows
