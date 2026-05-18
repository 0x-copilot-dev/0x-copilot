"""Tests for the offboarding cascade (P12-A2 §1.5 U-T5).

Sub-PRD §10 Q6 / cross-audit §9.8 Q1 — naive force-transfer is NOT
shipped. Project reassignments cascade through the existing
``ProjectsService.force_transfer_ownership`` admin path; agent / tool /
connector reassignments surface ``unsupported_asset_kind`` so the
admin can chase the owner manually.

Partial failure is intentional — successful reassignments commit even
when later ones fail.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
    OffboardingResult,
    TeamForbidden,
    TeamNotFound,
    TeamService,
)
from backend_app.team.store import InMemoryTeamStore


class _StubProjectsService:
    """Minimal stub mirroring the slice of ProjectsService we touch."""

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.calls: list[tuple[str, str, str]] = []

    def force_transfer_ownership(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles,
        project_id: str,
        new_owner_user_id: str,
        reason: str | None = None,
    ):
        _ = caller_user_id, caller_roles, reason
        self.calls.append((tenant_id, project_id, new_owner_user_id))
        if project_id in self.fail_for:
            raise RuntimeError(f"projects: refusing transfer {project_id}")
        return object()


def _seeded() -> tuple[InMemoryIdentityStore, TeamService, _StubProjectsService]:
    identity = InMemoryIdentityStore()
    identity.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for role_name in ("owner", "admin", "employee"):
        identity.create_role(
            RoleRecord(
                role_id=f"role_{role_name}",
                org_id=None,
                name=role_name,
                display_name=role_name.title(),
                is_system=True,
            )
        )

    def _add(uid: str, email: str, role_name: str) -> None:
        identity.create_user(
            UserRecord(
                user_id=uid,
                org_id="org_acme",
                primary_email=email,
                display_name=uid,
            )
        )
        identity.add_member(
            OrganizationMemberRecord(
                org_id="org_acme",
                user_id=uid,
                joined_at=datetime.now(timezone.utc),
            )
        )
        role = identity.get_role_by_name(org_id=None, name=role_name)
        assert role is not None
        identity.assign_role(
            RoleAssignmentRecord(org_id="org_acme", user_id=uid, role_id=role.role_id)
        )

    _add("usr_admin", "admin@acme.com", "admin")
    _add("usr_owner", "owner@acme.com", "owner")
    _add("usr_offboarded", "off@acme.com", "employee")
    _add("usr_successor", "succ@acme.com", "employee")

    projects_service = _StubProjectsService()
    service = TeamService(
        store=InMemoryTeamStore(identity_store=identity),
        identity_store=identity,
        invitations_service=InvitationsService(
            identity_store=identity,
            invitation_store=InMemoryInvitationStore(),
        ),
        projects_service=projects_service,
    )
    return identity, service, projects_service


class TestOffboardingCascade:
    def test_admin_required(self) -> None:
        _, service, _ = _seeded()
        with pytest.raises(TeamForbidden):
            service.offboard(
                tenant_id="org_acme",
                caller_user_id="usr_offboarded",
                caller_roles=("member",),
                target_user_id="usr_offboarded",
                reassignments=(),
            )

    def test_unknown_target_returns_not_found(self) -> None:
        _, service, _ = _seeded()
        with pytest.raises(TeamNotFound):
            service.offboard(
                tenant_id="org_acme",
                caller_user_id="usr_admin",
                caller_roles=("admin",),
                target_user_id="usr_ghost",
                reassignments=(),
            )

    def test_project_reassignment_calls_force_transfer(self) -> None:
        _, service, projects_service = _seeded()
        result = service.offboard(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_offboarded",
            reassignments=(("project", "proj_alpha", "usr_successor"),),
        )
        assert isinstance(result, OffboardingResult)
        assert len(result.outcomes) == 1
        assert result.outcomes[0].ok is True
        assert result.outcomes[0].asset_kind == "project"
        assert projects_service.calls == [("org_acme", "proj_alpha", "usr_successor")]

    def test_agent_reassignment_surfaces_unsupported(self) -> None:
        """Cross-audit §9.8 Q1: no admin force-transfer for agents.

        The wizard reassignment surface must surface this outcome so
        the admin learns that the agent transfer requires the owner."""
        _, service, _ = _seeded()
        result = service.offboard(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_offboarded",
            reassignments=(("agent", "agent_alpha", "usr_successor"),),
        )
        outcome = result.outcomes[0]
        assert outcome.ok is False
        assert outcome.reason == "unsupported_asset_kind"

    def test_partial_failure_does_not_roll_back_successes(self) -> None:
        """sub-PRD §1.5 U-T5: per-asset success; no cross-service atomicity."""
        _, service, projects_service = _seeded()
        projects_service.fail_for = {"proj_bad"}
        result = service.offboard(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_offboarded",
            reassignments=(
                ("project", "proj_good", "usr_successor"),
                ("project", "proj_bad", "usr_successor"),
            ),
        )
        # proj_good committed; proj_bad surfaced failure; successes
        # were NOT rolled back.
        outcomes_by_id = {o.asset_id: o for o in result.outcomes}
        assert outcomes_by_id["proj_good"].ok is True
        assert outcomes_by_id["proj_bad"].ok is False
        assert outcomes_by_id["proj_bad"].reason is not None
        # The failing call's reassignment was still attempted —
        # the stub recorded both invocations.
        assert ("org_acme", "proj_good", "usr_successor") in projects_service.calls
        assert ("org_acme", "proj_bad", "usr_successor") in projects_service.calls

    def test_audit_event_written_with_outcomes(self) -> None:
        identity, service, _ = _seeded()
        before = len(identity.identity_audit_events)
        service.offboard(
            tenant_id="org_acme",
            caller_user_id="usr_admin",
            caller_roles=("admin",),
            target_user_id="usr_offboarded",
            reassignments=(("project", "proj_a", "usr_successor"),),
        )
        new_events = identity.identity_audit_events[before:]
        offboarded = [e for e in new_events if e.action == "team.offboarded"]
        assert len(offboarded) == 1
        meta = offboarded[0].metadata
        assert meta["reassignments_count"] == 1
        assert isinstance(meta["outcomes"], list)
        assert meta["outcomes"][0]["asset_id"] == "proj_a"
