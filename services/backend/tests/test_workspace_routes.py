"""Tests for the PR 4.2 Workspace group routes.

Covers happy paths and the load-bearing guards (slug uniqueness, last-admin
guard, invitation-token mint-once, accept idempotency).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    OrganizationMemberRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity.invitation_store import InMemoryInvitationStore
from backend_app.identity.invitations import InvitationsService
from backend_app.identity.store import InMemoryIdentityStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seeded_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(
            org_id="org_acme",
            display_name="Acme",
            slug="acme",
        )
    )
    # Seed system roles in the in-memory store the same shape 0004b would
    # have written. The service service relies on get_role_by_name(org=None).
    for system_name, display in [
        ("admin", "Admin"),
        ("employee", "Member"),
        ("auditor", "Viewer"),
    ]:
        store.create_role(
            RoleRecord(
                role_id=f"role_system_{system_name}",
                org_id=None,
                name=system_name,
                display_name=display,
                is_system=True,
            )
        )
    return store


def _add_member(
    store: InMemoryIdentityStore,
    *,
    user_id: str,
    email: str,
    role_name: str = "employee",
) -> UserRecord:
    user = store.create_user(
        UserRecord(
            user_id=user_id,
            org_id="org_acme",
            primary_email=email,
            display_name=email.split("@")[0],
            last_seen_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        )
    )
    store.add_member(
        OrganizationMemberRecord(
            org_id="org_acme",
            user_id=user.user_id,
            source=OrganizationMemberSource.LOCAL,
        )
    )
    role = store.get_role_by_name(org_id=None, name=role_name)
    if role is not None:
        store.assign_role(
            RoleAssignmentRecord(
                org_id="org_acme",
                user_id=user.user_id,
                role_id=role.role_id,
            )
        )
    return user


def _client(store: InMemoryIdentityStore) -> TestClient:
    invitation_store = InMemoryInvitationStore()
    invitations_service = InvitationsService(
        identity_store=store, invitation_store=invitation_store
    )
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=store,
        invitation_store=invitation_store,
        invitations_service=invitations_service,
    )
    return TestClient(app)


def _admin_params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_admin"}


# ---------------------------------------------------------------------------
# Workspace branding
# ---------------------------------------------------------------------------


class TestWorkspaceBranding:
    def test_get_returns_org_record(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.get("/internal/v1/workspace", params=_admin_params())
        assert response.status_code == 200
        body = response.json()
        assert body["org_id"] == "org_acme"
        assert body["slug"] == "acme"
        assert body["display_name"] == "Acme"

    def test_patch_updates_name_and_slug(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.patch(
            "/internal/v1/workspace",
            params=_admin_params(),
            json={"display_name": "Acme — GTM", "slug": "acme-gtm"},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Acme — GTM"
        assert response.json()["slug"] == "acme-gtm"
        # Audit row written.
        audits = store.list_identity_audit(org_id="org_acme")
        assert any(a.action == "workspace.update" for a in audits)

    def test_patch_rejects_taken_slug(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        # Seed a competing org occupying the target slug.
        store.create_organization(
            OrganizationRecord(
                org_id="org_other",
                display_name="Other",
                slug="acme-gtm",
            )
        )
        client = _client(store)
        response = client.patch(
            "/internal/v1/workspace",
            params=_admin_params(),
            json={"slug": "acme-gtm"},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "slug_taken"

    def test_delete_returns_501_and_audits_attempt(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.delete(
            "/internal/v1/workspace",
            params={**_admin_params(), "confirm_slug": "acme"},
        )
        assert response.status_code == 501
        audits = [
            a
            for a in store.list_identity_audit(org_id="org_acme")
            if a.action == "workspace.delete_attempt"
        ]
        assert len(audits) == 1
        assert audits[0].metadata["typed_confirmation_correct"] is True


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


class TestMembersDirectory:
    def test_lists_members_with_role_aliases(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        _add_member(
            store, user_id="usr_priya", email="priya@acme.com", role_name="employee"
        )
        client = _client(store)
        response = client.get("/internal/v1/workspace/members", params=_admin_params())
        assert response.status_code == 200
        body = response.json()
        roles = {m["user_id"]: m["role"]["name"] for m in body["members"]}
        assert roles["usr_admin"] == "admin"
        # System "employee" projects to design alias "member".
        assert roles["usr_priya"] == "member"

    def test_change_role_revokes_and_assigns_new(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        _add_member(
            store, user_id="usr_priya", email="priya@acme.com", role_name="employee"
        )
        client = _client(store)
        response = client.patch(
            "/internal/v1/workspace/members/usr_priya",
            params=_admin_params(),
            json={"role": "admin"},
        )
        assert response.status_code == 200
        assert response.json()["role"]["name"] == "admin"
        audits = [
            a
            for a in store.list_identity_audit(org_id="org_acme")
            if a.action == "member.role.update"
        ]
        assert len(audits) == 1
        assert audits[0].metadata["before_role"] == "member"
        assert audits[0].metadata["after_role"] == "admin"

    def test_last_admin_guard_blocks_self_downgrade(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.patch(
            "/internal/v1/workspace/members/usr_admin",
            params=_admin_params(),
            json={"role": "member"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "cannot_remove_last_admin"

    def test_remove_self_blocked(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.delete(
            "/internal/v1/workspace/members/usr_admin",
            params=_admin_params(),
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "cannot_remove_self"

    def test_remove_member_softens(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        _add_member(
            store, user_id="usr_priya", email="priya@acme.com", role_name="employee"
        )
        client = _client(store)
        response = client.delete(
            "/internal/v1/workspace/members/usr_priya",
            params=_admin_params(),
        )
        assert response.status_code == 204
        # `removed_at` set; the user row still exists for audit purposes.
        members = store.list_members(org_id="org_acme")
        assert all(m.removed_at is None for m in members)
        # Removed members are filtered out of list_members in the in-memory
        # adapter, so check the raw dict.
        assert any(
            m.user_id == "usr_priya" and m.removed_at is not None
            for m in store.members.values()
        )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class TestInvitations:
    def test_create_returns_token_once_and_audits(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        response = client.post(
            "/internal/v1/workspace/invitations",
            params=_admin_params(),
            json={"email": "priya@acme.com", "role": "member"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "priya@acme.com"
        assert body["role"] == "member"
        assert body["token"]  # plaintext only here
        assert body["token_prefix"] == body["token"][:8]
        # Subsequent list does NOT echo plaintext.
        listed = client.get(
            "/internal/v1/workspace/invitations", params=_admin_params()
        ).json()["invitations"]
        assert len(listed) == 1
        assert "token" not in listed[0]
        # Audit emitted.
        audits = [
            a
            for a in store.list_identity_audit(org_id="org_acme")
            if a.action == "invitation.create"
        ]
        assert len(audits) == 1

    def test_double_invite_for_active_user_returns_409(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        _add_member(
            store, user_id="usr_priya", email="priya@acme.com", role_name="employee"
        )
        client = _client(store)
        response = client.post(
            "/internal/v1/workspace/invitations",
            params=_admin_params(),
            json={"email": "priya@acme.com", "role": "admin"},
        )
        assert response.status_code == 409

    def test_accept_creates_user_and_member_idempotently(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        token = client.post(
            "/internal/v1/workspace/invitations",
            params=_admin_params(),
            json={"email": "priya@acme.com", "role": "member"},
        ).json()["token"]
        # Accept (no auth headers needed).
        response = client.post(f"/internal/v1/auth/invitations/{token}/accept")
        assert response.status_code == 200
        body = response.json()
        assert body["org_id"] == "org_acme"
        assert body["role"] == "member"
        # Re-accept fails: already accepted.
        again = client.post(f"/internal/v1/auth/invitations/{token}/accept")
        assert again.status_code == 409
        # User + member rows exist.
        user = store.get_user_by_email(org_id="org_acme", email="priya@acme.com")
        assert user is not None
        members = store.list_members(org_id="org_acme")
        assert any(m.user_id == user.user_id for m in members)

    def test_revoke_invitation(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        client = _client(store)
        invite_id = client.post(
            "/internal/v1/workspace/invitations",
            params=_admin_params(),
            json={"email": "priya@acme.com", "role": "member"},
        ).json()["invite_id"]
        response = client.delete(
            f"/internal/v1/workspace/invitations/{invite_id}",
            params=_admin_params(),
        )
        assert response.status_code == 204
        # Pending list is now empty.
        listed = client.get(
            "/internal/v1/workspace/invitations", params=_admin_params()
        ).json()["invitations"]
        assert listed == []


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------


class TestBilling:
    def test_returns_seats_and_plan_stub(self) -> None:
        store = _seeded_store()
        _add_member(
            store, user_id="usr_admin", email="admin@acme.com", role_name="admin"
        )
        _add_member(
            store, user_id="usr_priya", email="priya@acme.com", role_name="employee"
        )
        client = _client(store)
        response = client.get("/internal/v1/workspace/billing", params=_admin_params())
        assert response.status_code == 200
        body = response.json()
        assert body["seats"]["used"] == 2
        assert body["plan"]["managed_externally"] is True
        assert body["invoices"] == []
