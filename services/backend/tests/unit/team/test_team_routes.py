"""Tests for the ``/v1/team/*`` HTTP routes (P12-A2 §4.1).

Happy paths + ACL guards:

* List + detail return the projection.
* PATCH /role rejects self-demote + sole-owner-demote.
* POST /invite forwards to InvitationsService (no parallel invite path).
* Non-admin caller on admin endpoints → 403.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    OrganizationMemberRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity.store import InMemoryIdentityStore


def _seeded_identity() -> InMemoryIdentityStore:
    identity = InMemoryIdentityStore()
    identity.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
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
        identity.add_member(
            OrganizationMemberRecord(
                org_id="org_acme",
                user_id=uid,
                joined_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        role = identity.get_role_by_name(org_id=None, name=role_name)
        assert role is not None
        identity.assign_role(
            RoleAssignmentRecord(org_id="org_acme", user_id=uid, role_id=role.role_id)
        )

    _add("usr_owner", "owner@acme.com", "Owner User", "owner")
    _add("usr_admin", "admin@acme.com", "Admin User", "admin")
    _add("usr_member", "member@acme.com", "Member User", "employee")
    return identity


def _client() -> TestClient:
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
    )
    return TestClient(app)


def _q(user: str = "usr_member") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


class TestListEndpoint:
    def test_list_returns_team_members(self) -> None:
        client = _client()
        resp = client.get("/v1/team", params=_q("usr_admin"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [p["id"] for p in body["people"]]
        assert {"usr_owner", "usr_admin", "usr_member"} <= set(ids)

    def test_list_marks_is_self_for_caller(self) -> None:
        client = _client()
        resp = client.get("/v1/team", params=_q("usr_admin"))
        body = resp.json()
        self_row = next(p for p in body["people"] if p["id"] == "usr_admin")
        other_row = next(p for p in body["people"] if p["id"] == "usr_member")
        assert self_row["is_self"] is True
        assert other_row["is_self"] is False

    def test_list_filter_by_role(self) -> None:
        client = _client()
        resp = client.get(
            "/v1/team",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_admin"),
                ("filter[role]", "admin"),
            ],
        )
        ids = [p["id"] for p in resp.json()["people"]]
        assert ids == ["usr_admin"]


class TestDetailEndpoint:
    def test_returns_person_detail(self) -> None:
        client = _client()
        resp = client.get("/v1/team/usr_admin", params=_q("usr_member"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["person"]["id"] == "usr_admin"
        assert body["person"]["role"] == "admin"
        # recent_activity is admin-only — non-admin caller sees [].
        assert body["recent_activity"] == []

    def test_unknown_person_returns_404(self) -> None:
        client = _client()
        resp = client.get("/v1/team/usr_ghost", params=_q("usr_admin"))
        assert resp.status_code == 404


class TestRoleChange:
    def test_rejects_demote_self(self) -> None:
        client = _client()
        resp = client.patch(
            "/v1/team/usr_admin/role",
            params=_q("usr_admin"),
            json={"role": "member"},
        )
        assert resp.status_code == 409
        assert "demote_self" in resp.text

    def test_rejects_demote_sole_owner(self) -> None:
        client = _client()
        resp = client.patch(
            "/v1/team/usr_owner/role",
            params=_q("usr_admin"),
            json={"role": "member"},
        )
        assert resp.status_code == 409
        assert "sole_owner" in resp.text

    def test_member_cannot_change_role(self) -> None:
        client = _client()
        resp = client.patch(
            "/v1/team/usr_admin/role",
            params=_q("usr_member"),
            json={"role": "member"},
        )
        # Member identity carries no admin role; the service raises
        # TeamForbidden → 403.
        assert resp.status_code == 403

    def test_admin_can_change_member_role(self) -> None:
        client = _client()
        resp = client.patch(
            "/v1/team/usr_member/role",
            params=_q("usr_admin"),
            json={"role": "admin"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"


class TestInviteForwardsToIdentity:
    def test_invite_forwards_to_identity_invitations(self) -> None:
        client = _client()
        resp = client.post(
            "/v1/team/invite",
            params=_q("usr_admin"),
            json={"email": "new@acme.com", "role": "member"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["email"] == "new@acme.com"
        assert body["role"] == "member"
        assert body["invite_id"].startswith("inv_")
        # Verify the existing identity pending list sees the row —
        # the wrapper delegates rather than maintaining a parallel
        # store (sub-PRD §1.5 + cross-audit §1.1).
        pending = client.app.state.invitations_service.list_pending(org_id="org_acme")
        assert any(r.invite_id == body["invite_id"] for r in pending)

    def test_invite_member_forbidden_for_non_admin(self) -> None:
        client = _client()
        resp = client.post(
            "/v1/team/invite",
            params=_q("usr_member"),
            json={"email": "x@acme.com", "role": "member"},
        )
        assert resp.status_code == 403
