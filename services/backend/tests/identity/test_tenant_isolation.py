"""Tenant-isolation tests for the identity store (A1).

Mirrors services/backend/tests/test_tenant_isolation_skills_mcp.py: every
store method must scope by org_id. C5 (RLS) will add a DB-level backstop
later; this test ensures the application-layer guarantee is in place from
A1 onwards.
"""

from __future__ import annotations


from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity import InMemoryIdentityStore


class TenantIsolationMixin:
    def two_orgs(
        self,
    ) -> tuple[InMemoryIdentityStore, OrganizationRecord, OrganizationRecord]:
        store = InMemoryIdentityStore()
        org_a = store.create_organization(
            OrganizationRecord(display_name="Org A", slug="org-a")
        )
        org_b = store.create_organization(
            OrganizationRecord(display_name="Org B", slug="org-b")
        )
        return store, org_a, org_b


class TestUserIsolation(TenantIsolationMixin):
    def test_same_email_in_two_orgs_creates_two_users(self) -> None:
        store, org_a, org_b = self.two_orgs()

        user_a = store.create_user(
            UserRecord(
                org_id=org_a.org_id,
                primary_email="shared@example.com",
                display_name="Shared",
            )
        )
        user_b = store.create_user(
            UserRecord(
                org_id=org_b.org_id,
                primary_email="shared@example.com",
                display_name="Shared",
            )
        )

        assert user_a.user_id != user_b.user_id
        assert (
            store.get_user_by_email(org_id=org_a.org_id, email="shared@example.com")
            == user_a
        )
        assert (
            store.get_user_by_email(org_id=org_b.org_id, email="shared@example.com")
            == user_b
        )

    def test_list_users_is_scoped_to_org(self) -> None:
        store, org_a, org_b = self.two_orgs()
        a = store.create_user(
            UserRecord(org_id=org_a.org_id, primary_email="a@x.com", display_name="A")
        )
        b = store.create_user(
            UserRecord(org_id=org_b.org_id, primary_email="b@x.com", display_name="B")
        )

        assert store.list_users(org_id=org_a.org_id) == (a,)
        assert store.list_users(org_id=org_b.org_id) == (b,)

    def test_get_user_with_wrong_org_returns_none(self) -> None:
        store, org_a, org_b = self.two_orgs()
        user = store.create_user(
            UserRecord(
                org_id=org_a.org_id, primary_email="cross@x.com", display_name="X"
            )
        )

        assert store.get_user(org_id=org_a.org_id, user_id=user.user_id) == user
        # Same user_id but wrong org → 404 semantic (None).
        assert store.get_user(org_id=org_b.org_id, user_id=user.user_id) is None

    def test_delete_user_with_wrong_org_is_no_op(self) -> None:
        store, org_a, org_b = self.two_orgs()
        user = store.create_user(
            UserRecord(org_id=org_a.org_id, primary_email="del@x.com", display_name="X")
        )

        assert store.delete_user(org_id=org_b.org_id, user_id=user.user_id) is False
        # Original user still active.
        assert store.get_user(org_id=org_a.org_id, user_id=user.user_id) is not None


class TestAuthProviderIsolation(TenantIsolationMixin):
    def test_provider_in_org_a_invisible_to_org_b(self) -> None:
        store, org_a, org_b = self.two_orgs()
        prov = store.create_auth_provider(
            AuthProviderRecord(
                org_id=org_a.org_id,
                kind=AuthProviderKind.OIDC,
                display_name="Google",
            )
        )

        assert (
            store.get_auth_provider(org_id=org_a.org_id, provider_id=prov.provider_id)
            is not None
        )
        assert (
            store.get_auth_provider(org_id=org_b.org_id, provider_id=prov.provider_id)
            is None
        )
        assert store.list_auth_providers(org_id=org_b.org_id) == ()


class TestRoleIsolation(TenantIsolationMixin):
    def test_per_org_role_with_same_name_can_coexist(self) -> None:
        store, org_a, org_b = self.two_orgs()
        ra = store.create_role(
            RoleRecord(org_id=org_a.org_id, name="custom", display_name="A custom")
        )
        rb = store.create_role(
            RoleRecord(org_id=org_b.org_id, name="custom", display_name="B custom")
        )

        assert ra.role_id != rb.role_id
        assert store.get_role_by_name(org_id=org_a.org_id, name="custom") == ra
        assert store.get_role_by_name(org_id=org_b.org_id, name="custom") == rb

    def test_role_assignments_are_scoped(self) -> None:
        store, org_a, org_b = self.two_orgs()
        user_a = store.create_user(
            UserRecord(org_id=org_a.org_id, primary_email="u@a.com", display_name="A")
        )
        role_a = store.create_role(
            RoleRecord(org_id=org_a.org_id, name="rolea", display_name="A")
        )
        store.assign_role(
            RoleAssignmentRecord(
                org_id=org_a.org_id, user_id=user_a.user_id, role_id=role_a.role_id
            )
        )

        assert (
            store.list_role_assignments(org_id=org_a.org_id, user_id=user_a.user_id)
            != ()
        )
        # Same user_id queried against the wrong org returns nothing.
        assert (
            store.list_role_assignments(org_id=org_b.org_id, user_id=user_a.user_id)
            == ()
        )

    def test_system_role_visible_via_null_org_namespace_only(self) -> None:
        store, org_a, _ = self.two_orgs()
        sys = store.create_role(
            RoleRecord(name="ops", display_name="Ops", is_system=True)
        )

        assert store.get_role_by_name(org_id=None, name="ops") == sys
        # System role is NOT in any org's per-org list.
        assert sys not in store.list_roles(org_id=org_a.org_id)
