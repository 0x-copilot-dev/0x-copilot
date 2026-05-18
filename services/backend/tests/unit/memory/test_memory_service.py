"""Unit tests for :class:`MemoryService` — Phase 12 P12-A3.

Coverage:

* ACL by scope: scope='user' is owner-only; scope='workspace' is any
  tenant member; project-scoped reads via the canonical
  ``is_member`` port.
* 404-not-403 for out-of-scope reads (cross-audit §1.3).
* Audit row per state change (created / updated / scope_changed /
  deleted).
* Soft-delete signals the indexer (a stub indexer records the call so
  we can assert the enqueue without standing up a worker).
* Admin compliance read across the tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from backend_app.memory.service import (
    MemoryForbidden,
    MemoryInvalidRequest,
    MemoryNotFound,
    MemoryService,
)
from backend_app.memory.store import InMemoryMemoryStore
from backend_app.projects.acl import InMemoryProjectMembershipAdapter


@dataclass
class _RecordingIndexer:
    """Stub indexer — records each enqueue so tests can assert it."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def enqueue(self, *, tenant_id: str, memory_id: str) -> None:
        self.calls.append((tenant_id, memory_id))


def _service(
    *,
    memberships: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[MemoryService, InMemoryMemoryStore, _RecordingIndexer]:
    store = InMemoryMemoryStore()
    indexer = _RecordingIndexer()
    membership_port = InMemoryProjectMembershipAdapter(memberships or {})
    svc = MemoryService(store=store, membership_port=membership_port, indexer=indexer)
    return svc, store, indexer


def test_create_emits_audit_and_indexer_enqueue() -> None:
    svc, store, indexer = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator={"kind": "user", "id": "usr_sarah"},
        scope="user",
        kind="skill",
        title="Python",
        body="loves Django",
        tags=["lang"],
        project_id=None,
    )
    # Audit row landed.
    audit_rows = store.list_audit_for_target(tenant_id="org_acme", target_id=record.id)
    assert any(a.action == "memory.created" for a in audit_rows)
    # Indexer was enqueued.
    assert indexer.calls == [("org_acme", record.id)]


def test_get_user_scoped_row_is_owner_only() -> None:
    svc, store, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="fact",
        title="CTO is X",
        body="",
        tags=None,
        project_id=None,
    )
    # Owner reads.
    fetched = svc.get_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        caller_roles=(),
        item_id=record.id,
    )
    assert fetched.id == record.id
    # Non-owner gets 404 (not 403).
    with pytest.raises(MemoryNotFound):
        svc.get_item(
            tenant_id="org_acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            item_id=record.id,
        )


def test_workspace_scope_is_readable_by_any_tenant_member() -> None:
    svc, _, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="workspace",
        kind="preference",
        title="TL;DR at top",
        body="",
        tags=None,
        project_id=None,
    )
    # Non-owner tenant member reads workspace-scoped row.
    fetched = svc.get_item(
        tenant_id="org_acme",
        caller_user_id="usr_bob",
        caller_roles=(),
        item_id=record.id,
    )
    assert fetched.id == record.id


def test_project_scope_uses_canonical_is_member() -> None:
    svc, _, _ = _service(memberships={("org_acme", "proj_acme"): {"usr_bob"}})
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",  # owner-only by scope...
        kind="fact",
        title="Acme launch is Q1",
        body="",
        tags=None,
        project_id="proj_acme",  # ...but filed under a project.
    )
    # usr_bob is NOT the owner but IS a project member — reads.
    fetched = svc.get_item(
        tenant_id="org_acme",
        caller_user_id="usr_bob",
        caller_roles=(),
        item_id=record.id,
    )
    assert fetched.id == record.id
    # usr_carol is neither — 404.
    with pytest.raises(MemoryNotFound):
        svc.get_item(
            tenant_id="org_acme",
            caller_user_id="usr_carol",
            caller_roles=(),
            item_id=record.id,
        )


def test_update_owner_only_enforced() -> None:
    svc, _, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="workspace",
        kind="fact",
        title="hello",
        body="",
        tags=None,
        project_id=None,
    )
    # Non-owner + non-admin can READ (workspace scope) but cannot WRITE
    # — should raise MemoryForbidden.
    with pytest.raises(MemoryForbidden):
        svc.update_item(
            tenant_id="org_acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            item_id=record.id,
            patch={"title": "tampered"},
        )


def test_admin_can_write_workspace_scoped_rows() -> None:
    svc, _, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="workspace",
        kind="fact",
        title="hello",
        body="",
        tags=None,
        project_id=None,
    )
    updated = svc.update_item(
        tenant_id="org_acme",
        caller_user_id="usr_dave_admin",
        caller_roles=("admin",),
        item_id=record.id,
        patch={"title": "admin edit"},
    )
    assert updated.title == "admin edit"


def test_scope_change_emits_explicit_audit() -> None:
    svc, store, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="skill",
        title="speaks python",
        body="",
        tags=None,
        project_id=None,
    )
    svc.update_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        caller_roles=(),
        item_id=record.id,
        patch={"scope": "workspace"},
    )
    audits = store.list_audit_for_target(tenant_id="org_acme", target_id=record.id)
    actions = {a.action for a in audits}
    assert "memory.scope_changed" in actions


def test_delete_soft_deletes_and_signals_indexer() -> None:
    svc, store, indexer = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="fact",
        title="hello",
        body="",
        tags=None,
        project_id=None,
    )
    indexer.calls.clear()
    deleted = svc.delete_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        caller_roles=(),
        item_id=record.id,
    )
    assert deleted.deleted_at is not None
    # Public get -> 404.
    with pytest.raises(MemoryNotFound):
        svc.get_item(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            item_id=record.id,
        )
    # Audit row.
    audits = store.list_audit_for_target(tenant_id="org_acme", target_id=record.id)
    assert any(a.action == "memory.deleted" for a in audits)
    # Indexer re-signaled so the embedding row is dropped.
    assert indexer.calls == [("org_acme", record.id)]


def test_invalid_kind_or_scope_rejected() -> None:
    svc, _, _ = _service()
    with pytest.raises(MemoryInvalidRequest):
        svc.create_item(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            creator=None,
            scope="user",
            kind="bogus",
            title="x",
            body="",
            tags=None,
            project_id=None,
        )
    with pytest.raises(MemoryInvalidRequest):
        svc.create_item(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            creator=None,
            scope="bogus",
            kind="skill",
            title="x",
            body="",
            tags=None,
            project_id=None,
        )


def test_list_admin_compliance_read() -> None:
    svc, _, _ = _service()
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="skill",
        title="sarah private",
        body="",
        tags=None,
        project_id=None,
    )
    svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_bob",
        creator=None,
        scope="user",
        kind="skill",
        title="bob private",
        body="",
        tags=None,
        project_id=None,
    )
    # Admin sees both.
    rows, _ = svc.list_items(
        tenant_id="org_acme",
        caller_user_id="usr_dave_admin",
        caller_roles=("admin",),
    )
    titles = {r.title for r in rows}
    assert "sarah private" in titles
    assert "bob private" in titles
    # Non-admin only sees own.
    rows, _ = svc.list_items(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        caller_roles=(),
    )
    titles = {r.title for r in rows}
    assert "sarah private" in titles
    assert "bob private" not in titles


def test_touch_internal_emits_audit() -> None:
    svc, store, _ = _service()
    record = svc.create_item(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        creator=None,
        scope="user",
        kind="skill",
        title="hello",
        body="",
        tags=None,
        project_id=None,
    )
    touched = svc.touch_item(tenant_id="org_acme", item_id=record.id)
    assert touched.last_used_at is not None
    audits = store.list_audit_for_target(tenant_id="org_acme", target_id=record.id)
    assert any(a.action == "memory.touched" for a in audits)
