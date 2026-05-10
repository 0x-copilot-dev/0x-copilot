"""Tests for the caller-scoped /internal/v1/me/* surface (PR 2.2)."""

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


def _seeded_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(
            org_id="org_acme",
            display_name="Acme",
            slug="acme",
        )
    )
    store.create_organization(
        OrganizationRecord(
            org_id="org_other",
            display_name="Other",
            slug="other",
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            last_seen_at=datetime(2026, 5, 5, 15, 51, tzinfo=timezone.utc),
        )
    )
    # Three members, one removed — only the active two should be counted.
    for tag in ("sarah", "marcus", "removed"):
        store.add_member(
            OrganizationMemberRecord(
                org_id="org_acme",
                user_id=f"usr_{tag}",
                removed_at=(
                    datetime(2026, 4, 1, tzinfo=timezone.utc)
                    if tag == "removed"
                    else None
                ),
            )
        )
    role = RoleRecord(
        org_id="org_acme",
        name="admin",
        display_name="Admin",
    )
    store.create_role(role)
    store.assign_role(
        RoleAssignmentRecord(
            org_id="org_acme",
            user_id="usr_sarah",
            role_id=role.role_id,
            granted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
    )
    return store


def _client(store: InMemoryIdentityStore) -> TestClient:
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=store,
    )
    return TestClient(app)


class TestListMyWorkspaces:
    def test_returns_current_workspace_with_role_and_count(self) -> None:
        client = _client(_seeded_store())
        response = client.get(
            "/internal/v1/me/workspaces",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["workspaces"]) == 1
        ws = body["workspaces"][0]
        assert ws == {
            "org_id": "org_acme",
            "display_name": "Acme",
            "slug": "acme",
            "role": "Admin",
            "member_count": 2,  # 'removed' member excluded
            "last_active_at": "2026-05-05T15:51:00+00:00",
            "is_current": True,
        }

    def test_no_role_assignment_returns_null_role(self) -> None:
        store = _seeded_store()
        store.create_user(
            UserRecord(
                user_id="usr_lurker",
                org_id="org_acme",
                primary_email="lurker@acme.com",
                display_name="Lurker",
            )
        )
        client = _client(store)
        response = client.get(
            "/internal/v1/me/workspaces",
            params={"org_id": "org_acme", "user_id": "usr_lurker"},
        )
        assert response.status_code == 200
        ws = response.json()["workspaces"][0]
        assert ws["role"] is None
        assert ws["last_active_at"] is None

    def test_unknown_org_returns_404(self) -> None:
        client = _client(_seeded_store())
        response = client.get(
            "/internal/v1/me/workspaces",
            params={"org_id": "org_does_not_exist", "user_id": "usr_sarah"},
        )
        assert response.status_code == 404

    def test_missing_query_params_rejected(self) -> None:
        client = _client(_seeded_store())
        response = client.get("/internal/v1/me/workspaces")
        assert response.status_code == 422

    def test_picks_most_recent_role_assignment(self) -> None:
        store = _seeded_store()
        # A second, more-recently-granted role should win.
        member_role = RoleRecord(
            org_id="org_acme",
            name="member",
            display_name="Member",
        )
        store.create_role(member_role)
        store.assign_role(
            RoleAssignmentRecord(
                org_id="org_acme",
                user_id="usr_sarah",
                role_id=member_role.role_id,
                granted_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
            )
        )
        client = _client(store)
        response = client.get(
            "/internal/v1/me/workspaces",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
        )
        assert response.status_code == 200
        assert response.json()["workspaces"][0]["role"] == "Member"
