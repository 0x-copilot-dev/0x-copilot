"""Unit tests for the in-memory identity store (A1).

The Postgres-backed adapter is exercised in integration tests against a real
DB. The in-memory adapter must mirror the same semantics so service-layer
tests are interchangeable. Postgres-only invariants (CHECK constraint on
roles, partial unique indexes) are mirrored at the Pydantic / repo layer.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OrganizationDeploymentKind,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity import InMemoryIdentityStore


class IdentityStoreFixtureMixin:
    """Common factories so test classes contain only test_* methods."""

    def store(self) -> InMemoryIdentityStore:
        return InMemoryIdentityStore()

    def org(
        self,
        store: InMemoryIdentityStore,
        *,
        slug: str = "acme",
        display_name: str = "Acme",
    ) -> OrganizationRecord:
        return store.create_organization(
            OrganizationRecord(
                display_name=display_name,
                slug=slug,
                deployment_kind=OrganizationDeploymentKind.SAAS,
            )
        )

    def user(
        self,
        store: InMemoryIdentityStore,
        org_id: str,
        *,
        email: str = "alice@example.com",
        display_name: str = "Alice",
    ) -> UserRecord:
        return store.create_user(
            UserRecord(
                org_id=org_id,
                primary_email=email,
                display_name=display_name,
            )
        )


class TestOrganizationCrud(IdentityStoreFixtureMixin):
    def test_create_and_get_by_id_and_slug(self) -> None:
        store = self.store()
        org = self.org(store)

        assert store.get_organization(org_id=org.org_id) == org
        assert store.get_organization_by_slug(slug="acme") == org

    def test_duplicate_active_slug_rejected(self) -> None:
        store = self.store()
        self.org(store, slug="dup")
        with pytest.raises(ValueError, match="slug already exists"):
            self.org(store, slug="dup")

    def test_soft_delete_then_recreate_with_same_slug(self) -> None:
        store = self.store()
        org1 = self.org(store, slug="renew")
        assert store.delete_organization(org_id=org1.org_id) is True

        # The deleted row no longer satisfies "active" so the slug is free.
        org2 = self.org(store, slug="renew")
        assert org2.org_id != org1.org_id
        assert store.get_organization(org_id=org1.org_id) is None
        assert store.get_organization(org_id=org2.org_id) is not None

    def test_update_bumps_updated_at(self) -> None:
        store = self.store()
        org = self.org(store)
        renamed = store.update_organization(
            org.model_copy(update={"display_name": "Acme Inc."})
        )
        assert renamed.display_name == "Acme Inc."
        assert renamed.updated_at >= org.updated_at


class TestUserCrud(IdentityStoreFixtureMixin):
    def test_email_normalized_to_lower(self) -> None:
        store = self.store()
        org = self.org(store)
        u = self.user(store, org.org_id, email="Alice@Example.COM")
        assert u.primary_email == "alice@example.com"

    def test_duplicate_email_in_same_org_rejected(self) -> None:
        store = self.store()
        org = self.org(store)
        self.user(store, org.org_id, email="dup@example.com")
        with pytest.raises(ValueError, match="email already exists"):
            self.user(store, org.org_id, email="DUP@example.com")

    def test_get_by_email_is_case_insensitive(self) -> None:
        store = self.store()
        org = self.org(store)
        original = self.user(store, org.org_id, email="Mixed@Case.com")
        looked_up = store.get_user_by_email(org_id=org.org_id, email="MIXED@case.COM")
        assert looked_up == original

    def test_soft_delete_then_recreate_with_same_email(self) -> None:
        store = self.store()
        org = self.org(store)
        u1 = self.user(store, org.org_id, email="recur@x.com")
        assert store.delete_user(org_id=org.org_id, user_id=u1.user_id)
        u2 = self.user(store, org.org_id, email="recur@x.com")
        assert u2.user_id != u1.user_id
        assert store.get_user(org_id=org.org_id, user_id=u1.user_id) is None
        assert store.get_user(org_id=org.org_id, user_id=u2.user_id) is not None

    def test_invalid_email_rejected_by_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            UserRecord(org_id="org_x", primary_email="not-an-email", display_name="Bob")


class TestRoleCrud(IdentityStoreFixtureMixin):
    def test_system_role_requires_no_org_id(self) -> None:
        # Pydantic validator mirrors the Postgres CHECK constraint.
        with pytest.raises(ValidationError):
            RoleRecord(
                org_id="org_x",
                name="admin",
                display_name="Admin",
                is_system=True,
            )
        with pytest.raises(ValidationError):
            RoleRecord(name="employee", display_name="Employee", is_system=False)

    def test_org_role_lookup_by_name(self) -> None:
        store = self.store()
        org = self.org(store)
        role = store.create_role(
            RoleRecord(
                org_id=org.org_id,
                name="editor",
                display_name="Editor",
                permission_scopes=("skills:write",),
            )
        )
        found = store.get_role_by_name(org_id=org.org_id, name="editor")
        assert found == role

    def test_system_role_cannot_be_deleted(self) -> None:
        store = self.store()
        sys_role = store.create_role(
            RoleRecord(name="auditor", display_name="Auditor", is_system=True)
        )
        with pytest.raises(ValueError, match="system roles"):
            store.delete_role(role_id=sys_role.role_id)

    def test_role_assignment_uniqueness(self) -> None:
        store = self.store()
        org = self.org(store)
        user = self.user(store, org.org_id)
        role = store.create_role(
            RoleRecord(org_id=org.org_id, name="r1", display_name="Role 1")
        )
        store.assign_role(
            RoleAssignmentRecord(
                org_id=org.org_id, user_id=user.user_id, role_id=role.role_id
            )
        )
        with pytest.raises(ValueError, match="already assigned"):
            store.assign_role(
                RoleAssignmentRecord(
                    org_id=org.org_id, user_id=user.user_id, role_id=role.role_id
                )
            )

    def test_role_revoke_then_reassign_succeeds(self) -> None:
        store = self.store()
        org = self.org(store)
        user = self.user(store, org.org_id)
        role = store.create_role(
            RoleRecord(org_id=org.org_id, name="r2", display_name="Role 2")
        )
        store.assign_role(
            RoleAssignmentRecord(
                org_id=org.org_id, user_id=user.user_id, role_id=role.role_id
            )
        )
        assert store.revoke_role(
            org_id=org.org_id,
            user_id=user.user_id,
            role_id=role.role_id,
            reason="quit",
        )
        # Revoked → re-assignment is allowed.
        store.assign_role(
            RoleAssignmentRecord(
                org_id=org.org_id, user_id=user.user_id, role_id=role.role_id
            )
        )
        active = store.list_role_assignments(org_id=org.org_id, user_id=user.user_id)
        assert len(active) == 1


class TestAuthProviderCrud(IdentityStoreFixtureMixin):
    def test_unique_per_kind_and_display_name(self) -> None:
        store = self.store()
        org = self.org(store)
        store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.OIDC,
                display_name="Google",
            )
        )
        with pytest.raises(ValueError, match="already exists"):
            store.create_auth_provider(
                AuthProviderRecord(
                    org_id=org.org_id,
                    kind=AuthProviderKind.OIDC,
                    display_name="Google",
                )
            )

    def test_enabled_only_filter(self) -> None:
        store = self.store()
        org = self.org(store)
        enabled = store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.OIDC,
                display_name="Okta",
                enabled=True,
            )
        )
        store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.SAML,
                display_name="ADFS",
                enabled=False,
            )
        )
        only_enabled = store.list_auth_providers(org_id=org.org_id, enabled_only=True)
        assert only_enabled == (enabled,)

    def test_soft_delete_clears_active(self) -> None:
        store = self.store()
        org = self.org(store)
        prov = store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.LOCAL,
                display_name="passwords",
            )
        )
        assert store.delete_auth_provider(
            org_id=org.org_id, provider_id=prov.provider_id
        )
        assert (
            store.get_auth_provider(org_id=org.org_id, provider_id=prov.provider_id)
            is None
        )


class TestAuditAndLoginAttempts(IdentityStoreFixtureMixin):
    def test_audit_append_and_list_in_reverse_chronological_order(self) -> None:
        store = self.store()
        org = self.org(store)
        first = store.append_identity_audit(
            IdentityAuditEventRecord(org_id=org.org_id, action="user.created")
        )
        # Ensure the second event is strictly later.
        later = first.created_at + timedelta(seconds=1)
        store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org.org_id,
                action="user.deleted",
                created_at=later,
            )
        )
        rows = store.list_identity_audit(org_id=org.org_id)
        assert [r.action for r in rows] == ["user.deleted", "user.created"]

    def test_login_attempt_email_filter_is_case_insensitive(self) -> None:
        store = self.store()
        org = self.org(store)
        store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org.org_id,
                email_attempted="Foo@Bar.com",
                auth_kind=LoginAttemptKind.LOCAL,
                outcome=LoginAttemptOutcome.SUCCESS,
            )
        )
        matches = store.list_login_attempts(org_id=org.org_id, email="FOO@BAR.com")
        assert len(matches) == 1
        assert matches[0].email_attempted == "foo@bar.com"


class TestMembership(IdentityStoreFixtureMixin):
    def test_add_then_remove_member(self) -> None:
        store = self.store()
        org = self.org(store)
        user = self.user(store, org.org_id)
        store.add_member(
            OrganizationMemberRecord(
                org_id=org.org_id,
                user_id=user.user_id,
                source=OrganizationMemberSource.LOCAL,
            )
        )
        assert {m.user_id for m in store.list_members(org_id=org.org_id)} == {
            user.user_id
        }
        assert store.remove_member(org_id=org.org_id, user_id=user.user_id)
        assert store.list_members(org_id=org.org_id) == ()

    def test_duplicate_active_membership_rejected(self) -> None:
        store = self.store()
        org = self.org(store)
        user = self.user(store, org.org_id)
        store.add_member(
            OrganizationMemberRecord(
                org_id=org.org_id, user_id=user.user_id, source="local"
            )
        )
        with pytest.raises(ValueError, match="already an active member"):
            store.add_member(
                OrganizationMemberRecord(
                    org_id=org.org_id, user_id=user.user_id, source="local"
                )
            )
