"""Tests for the in-memory Team store (P12-A2 §3.1 / §5.2).

Covers:

* List composes over the IdentityStore + members + role assignments.
* Tenant isolation — a user in tenant_a never appears under tenant_b.
* Asset-count projections fan out to the agents/projects stores.
* Presence comes from the in-process KV with offline fallback.
* Sort + filter axes match the api-types tokens.
* Cursor paging surfaces an opaque cursor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend_app.contracts import (
    OrganizationMemberRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.team.store import (
    InMemoryPresenceKv,
    InMemoryTeamStore,
    PersonRow,
    Presence,
    ZeroAssetCounts,
)


def _seed_tenant(
    *,
    org_id: str = "org_acme",
) -> InMemoryIdentityStore:
    identity = InMemoryIdentityStore()
    identity.create_organization(
        OrganizationRecord(org_id=org_id, display_name=org_id, slug=org_id)
    )
    # Seed the system roles the team store maps onto.
    identity.create_role(
        RoleRecord(
            role_id=f"role_owner_{org_id}",
            org_id=None,
            name="owner",
            display_name="Owner",
            is_system=True,
        )
    )
    identity.create_role(
        RoleRecord(
            role_id=f"role_admin_{org_id}",
            org_id=None,
            name="admin",
            display_name="Admin",
            is_system=True,
        )
    )
    identity.create_role(
        RoleRecord(
            role_id=f"role_employee_{org_id}",
            org_id=None,
            name="employee",
            display_name="Member",
            is_system=True,
        )
    )
    return identity


def _add_user(
    identity: InMemoryIdentityStore,
    *,
    user_id: str,
    org_id: str,
    email: str,
    display_name: str,
    role_name: str,
    joined_at: datetime | None = None,
) -> UserRecord:
    user = UserRecord(
        user_id=user_id,
        org_id=org_id,
        primary_email=email,
        display_name=display_name,
    )
    identity.create_user(user)
    identity.add_member(
        OrganizationMemberRecord(
            org_id=org_id,
            user_id=user_id,
            joined_at=joined_at or datetime.now(timezone.utc),
        )
    )
    role = identity.get_role_by_name(org_id=None, name=role_name)
    assert role is not None, f"seed role {role_name} missing"
    identity.assign_role(
        RoleAssignmentRecord(
            org_id=org_id,
            user_id=user_id,
            role_id=role.role_id,
        )
    )
    return user


class TestListPeople:
    def test_lists_members_with_role_projection(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="usr_sarah",
            org_id="org_acme",
            email="sarah@acme.com",
            display_name="Sarah Chen",
            role_name="employee",
        )
        _add_user(
            identity,
            user_id="usr_marcus",
            org_id="org_acme",
            email="marcus@acme.com",
            display_name="Marcus Lee",
            role_name="admin",
        )
        store = InMemoryTeamStore(identity_store=identity)
        rows, cursor = store.list_people(
            tenant_id="org_acme", caller_user_id="usr_sarah"
        )
        assert cursor is None
        names_to_role = {r.id: r.role for r in rows}
        assert names_to_role == {"usr_sarah": "member", "usr_marcus": "admin"}

    def test_excludes_removed_members(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="usr_sarah",
            org_id="org_acme",
            email="sarah@acme.com",
            display_name="Sarah",
            role_name="employee",
        )
        identity.remove_member(org_id="org_acme", user_id="usr_sarah")
        store = InMemoryTeamStore(identity_store=identity)
        rows, _ = store.list_people(tenant_id="org_acme", caller_user_id="usr_sarah")
        assert rows == ()

    def test_tenant_isolation_no_cross_org_bleed(self) -> None:
        identity = _seed_tenant(org_id="org_acme")
        identity.create_organization(
            OrganizationRecord(org_id="org_other", display_name="Other", slug="other")
        )
        # Seed system roles for org_other separately so its members
        # resolve their roles too — system roles are tenant-agnostic
        # (org_id None), but the per-tenant seed loop is what reuses
        # them; nothing extra to do here, just add the users.
        _add_user(
            identity,
            user_id="usr_a",
            org_id="org_acme",
            email="a@acme.com",
            display_name="A",
            role_name="employee",
        )
        _add_user(
            identity,
            user_id="usr_b",
            org_id="org_other",
            email="b@other.com",
            display_name="B",
            role_name="employee",
        )
        store = InMemoryTeamStore(identity_store=identity)
        rows_acme, _ = store.list_people(tenant_id="org_acme", caller_user_id="usr_a")
        rows_other, _ = store.list_people(tenant_id="org_other", caller_user_id="usr_b")
        assert {r.id for r in rows_acme} == {"usr_a"}
        assert {r.id for r in rows_other} == {"usr_b"}

    def test_filter_by_role(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="admin",
        )
        _add_user(
            identity,
            user_id="u2",
            org_id="org_acme",
            email="u2@acme.com",
            display_name="U2",
            role_name="employee",
        )
        store = InMemoryTeamStore(identity_store=identity)
        admins, _ = store.list_people(
            tenant_id="org_acme", caller_user_id="u1", role="admin"
        )
        assert [r.id for r in admins] == ["u1"]

    def test_filter_by_q_matches_email_or_display_name(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="sarah@acme.com",
            display_name="Sarah Chen",
            role_name="employee",
        )
        _add_user(
            identity,
            user_id="u2",
            org_id="org_acme",
            email="bob@example.com",
            display_name="Bob Smith",
            role_name="employee",
        )
        store = InMemoryTeamStore(identity_store=identity)
        rows, _ = store.list_people(
            tenant_id="org_acme", caller_user_id="u1", q="sarah"
        )
        assert [r.id for r in rows] == ["u1"]
        rows2, _ = store.list_people(
            tenant_id="org_acme", caller_user_id="u1", q="@example.com"
        )
        assert [r.id for r in rows2] == ["u2"]

    def test_cursor_paging(self) -> None:
        identity = _seed_tenant()
        for i in range(5):
            _add_user(
                identity,
                user_id=f"u{i}",
                org_id="org_acme",
                email=f"u{i}@acme.com",
                display_name=f"User {i:02d}",
                role_name="employee",
            )
        store = InMemoryTeamStore(identity_store=identity)
        page_1, cursor = store.list_people(
            tenant_id="org_acme", caller_user_id="u0", limit=2
        )
        assert len(page_1) == 2
        assert cursor is not None
        page_2, _ = store.list_people(
            tenant_id="org_acme",
            caller_user_id="u0",
            limit=2,
            cursor=cursor,
        )
        assert len(page_2) == 2
        # No duplicate rows across the two pages.
        assert {r.id for r in page_1}.isdisjoint({r.id for r in page_2})


class TestPresenceProjection:
    def test_offline_when_kv_empty(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="employee",
        )
        store = InMemoryTeamStore(identity_store=identity)
        rows, _ = store.list_people(tenant_id="org_acme", caller_user_id="u1")
        assert rows[0].presence == "offline"

    def test_presence_kv_overrides_offline(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="employee",
        )
        kv = InMemoryPresenceKv()
        kv.set(tenant_id="org_acme", user_id="u1", state="active")
        store = InMemoryTeamStore(identity_store=identity, presence_kv=kv)
        rows, _ = store.list_people(tenant_id="org_acme", caller_user_id="u1")
        assert rows[0].presence == "active"


class TestAssetCountsProjection:
    def test_zero_fallback_when_stores_absent(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="employee",
        )
        store = InMemoryTeamStore(
            identity_store=identity, asset_counts=ZeroAssetCounts()
        )
        rows, _ = store.list_people(tenant_id="org_acme", caller_user_id="u1")
        assert rows[0].agents_count == 0
        assert rows[0].projects_count == 0

    def test_counts_fan_out_to_stub_stores(self) -> None:
        from backend_app.team.store import StoreBackedAssetCounts

        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="employee",
        )

        class _StubStore:
            def __init__(self, n: int) -> None:
                self.n = n

            def list_agents(self, **_kwargs):
                return (tuple(_StubRow(i) for i in range(self.n)), None)

            def list_projects(self, **_kwargs):
                return (tuple(_StubRow(i) for i in range(self.n)), None)

        class _StubRow:
            def __init__(self, i: int) -> None:
                self.id = f"row_{i}"

        adapter = StoreBackedAssetCounts(
            agents_store=_StubStore(3), projects_store=_StubStore(2)
        )
        store = InMemoryTeamStore(identity_store=identity, asset_counts=adapter)
        rows, _ = store.list_people(tenant_id="org_acme", caller_user_id="u1")
        assert rows[0].agents_count == 3
        assert rows[0].projects_count == 2


class TestGetPerson:
    def test_returns_none_for_unknown_user(self) -> None:
        identity = _seed_tenant()
        store = InMemoryTeamStore(identity_store=identity)
        assert store.get_person(tenant_id="org_acme", user_id="usr_missing") is None

    def test_returns_none_for_removed_member(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="U1",
            role_name="employee",
        )
        identity.remove_member(org_id="org_acme", user_id="u1")
        store = InMemoryTeamStore(identity_store=identity)
        assert store.get_person(tenant_id="org_acme", user_id="u1") is None

    def test_returns_row_for_active_member(self) -> None:
        identity = _seed_tenant()
        _add_user(
            identity,
            user_id="u1",
            org_id="org_acme",
            email="u1@acme.com",
            display_name="Alice",
            role_name="admin",
        )
        store = InMemoryTeamStore(identity_store=identity)
        row = store.get_person(tenant_id="org_acme", user_id="u1")
        assert isinstance(row, PersonRow)
        assert row.role == "admin"
        assert row.display_name == "Alice"


def test_presence_state_round_trips() -> None:
    kv = InMemoryPresenceKv()
    row = kv.set(tenant_id="t", user_id="u", state="active")
    assert row.state == "active"
    assert row.last_seen_at is not None
    fetched = kv.get(tenant_id="t", user_id="u")
    assert fetched.state == "active"
    other: Presence = kv.get(tenant_id="t", user_id="u_other").state
    assert other == "offline"
