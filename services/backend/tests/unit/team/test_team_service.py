"""Tests for the Team service ACL + invariants (P12-A2 §6.1)."""

from __future__ import annotations


import pytest

from backend_app.contracts import (
    OrganizationMemberRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity.invitation_store import InMemoryInvitationStore
from backend_app.identity.invitations import InvitationsService
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.team.service import (
    TeamConflict,
    TeamForbidden,
    TeamNotFound,
    TeamService,
)
from backend_app.team.store import InMemoryTeamStore


def _seeded(*, with_owner: bool = True) -> tuple[InMemoryIdentityStore, TeamService]:
    identity = InMemoryIdentityStore()
    identity.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    # Seed system roles. The team store maps system → team role names.
    for role_name, display in (
        ("owner", "Owner"),
        ("admin", "Admin"),
        ("employee", "Member"),
        ("auditor", "Guest"),
    ):
        identity.create_role(
            RoleRecord(
                role_id=f"role_{role_name}",
                org_id=None,
                name=role_name,
                display_name=display,
                is_system=True,
            )
        )

    def _add(uid: str, email: str, name: str, role_name: str) -> None:
        identity.create_user(
            UserRecord(
                user_id=uid,
                org_id="org_acme",
                primary_email=email,
                display_name=name,
            )
        )
        identity.add_member(OrganizationMemberRecord(org_id="org_acme", user_id=uid))
        role = identity.get_role_by_name(org_id=None, name=role_name)
        assert role is not None
        identity.assign_role(
            RoleAssignmentRecord(
                org_id="org_acme",
                user_id=uid,
                role_id=role.role_id,
            )
        )

    _add("usr_admin", "admin@acme.com", "Admin User", "admin")
    _add("usr_member", "member@acme.com", "Member User", "employee")
    if with_owner:
        _add("usr_owner", "owner@acme.com", "Owner User", "owner")

    team_store = InMemoryTeamStore(identity_store=identity)
    service = TeamService(
        store=team_store,
        identity_store=identity,
        invitations_service=InvitationsService(
            identity_store=identity,
            invitation_store=InMemoryInvitationStore(),
        ),
    )
    return identity, service


class TestListAcl:
    def test_non_tenant_member_sees_empty(self) -> None:
        _, service = _seeded()
        with pytest.raises(TeamNotFound):
            service.get_person(
                tenant_id="org_acme",
                caller_user_id="usr_outsider",
                caller_roles=(),
                user_id="usr_admin",
            )

    def test_member_can_list(self) -> None:
        _, service = _seeded()
        rows, _ = service.list_people(
            tenant_id="org_acme",
            caller_user_id="usr_member",
            caller_roles=(),
        )
        assert {r.id for r in rows} >= {"usr_admin", "usr_member"}


class TestInviteAcl:
    def test_non_admin_forbidden(self) -> None:
        _, service = _seeded()
        with pytest.raises(TeamForbidden):
            service.invite(
                tenant_id="org_acme",
                caller_user_id="usr_member",
                caller_roles=("member",),
                email="new@acme.com",
                role="member",
            )

    def test_admin_can_mint(self) -> None:
        _, service = _seeded()
        mint = service.invite(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            email="new@acme.com",
            role="member",
        )
        assert mint.invite_id.startswith("inv_")
        assert isinstance(mint.token_plaintext, str)
        # Round-trips through the existing pending list.
        pending = service.invitations_service.list_pending(org_id="org_acme")
        assert any(r.invite_id == mint.invite_id for r in pending)


class TestUpdateRoleInvariants:
    def test_non_admin_forbidden(self) -> None:
        _, service = _seeded()
        with pytest.raises(TeamForbidden):
            service.update_role(
                tenant_id="org_acme",
                caller_user_id="usr_member",
                caller_roles=("member",),
                target_user_id="usr_admin",
                new_role="member",
            )

    def test_cannot_demote_self(self) -> None:
        _, service = _seeded()
        with pytest.raises(TeamConflict) as exc:
            service.update_role(
                tenant_id="org_acme",
                caller_user_id="usr_admin",
                caller_roles=("admin",),
                target_user_id="usr_admin",
                new_role="member",
            )
        assert "demote_self" in str(exc.value)

    def test_cannot_demote_sole_owner(self) -> None:
        _, service = _seeded(with_owner=True)
        # Only one owner exists in the seed; demoting it must fail.
        with pytest.raises(TeamConflict) as exc:
            service.update_role(
                tenant_id="org_acme",
                caller_user_id="usr_admin",
                caller_roles=("admin",),
                target_user_id="usr_owner",
                new_role="member",
            )
        assert "sole_owner" in str(exc.value)

    def test_can_change_member_to_admin(self) -> None:
        _, service = _seeded()
        row = service.update_role(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_member",
            new_role="admin",
        )
        assert row.role == "admin"

    def test_unknown_target_returns_404(self) -> None:
        _, service = _seeded()
        with pytest.raises(TeamNotFound):
            service.update_role(
                tenant_id="org_acme",
                caller_user_id="usr_admin",
                caller_roles=("admin",),
                target_user_id="usr_ghost",
                new_role="admin",
            )

    def test_no_op_when_role_unchanged(self) -> None:
        identity, service = _seeded()
        before_audit = len(identity.identity_audit_events)
        row = service.update_role(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_admin",
            new_role="admin",
        )
        assert row.role == "admin"
        # No audit row written for a no-op (idempotency).
        assert len(identity.identity_audit_events) == before_audit


class TestRoleChangeAuditChain:
    def test_audit_appended_on_change(self) -> None:
        identity, service = _seeded()
        before = len(identity.identity_audit_events)
        service.update_role(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_member",
            new_role="admin",
        )
        after = identity.identity_audit_events[before:]
        assert any(
            evt.action == "team.role_changed"
            and evt.actor_user_id == "usr_admin"
            and evt.subject_user_id == "usr_member"
            for evt in after
        )
