"""Team destination service layer.

Owns:

* List / detail with tenant-isolation ACL (sub-PRD §6.1).
* Invite — delegates to the existing :class:`InvitationsService` (no
  parallel invite path; cross-audit §1.1 "no parallel identity").
* Role change — wraps the existing identity role-assignment path with
  the team-specific invariants: cannot demote self, cannot demote sole
  owner (sub-PRD §6.1).
* Offboarding — orchestrates the per-asset reassignment cascade by
  delegating to the existing per-destination services
  (``ProjectsService.force_transfer_ownership`` etc). NO new
  force-transfer endpoint (Routines §9.7 Q12 STAYS DEFERRED per
  cross-audit §9.8 Q1; sub-PRD §1.5 + §10 Q6).

Audit lives on the existing identity audit chain
(``IdentityAuditEventRecord``) for role / invite / offboard writes;
every write happens inside ``with identity_store.transaction()`` so the
primary mutation and audit row are atomic (C3 invariant — same rule
the canonical service.py guard enforces).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from backend_app.contracts import (
    IdentityAuditEventRecord,
    RoleAssignmentRecord,
)
from backend_app.identity.invitations import (
    InvitationsService,
    InvitationConflict,
    InvitationBadRequest,
)
from backend_app.team.store import (
    PersonRow,
    Presence,
    TeamRole,
    TeamStore,
    team_role_to_system_role,
)


# ---------------------------------------------------------------------------
# Exceptions — mapped to HTTP status codes at the route boundary.
# Status codes mirror the projects/agents convention so the FE error
# adapter stays uniform.
# ---------------------------------------------------------------------------


class TeamError(RuntimeError):
    status_code = 400
    code = "team_error"


class TeamNotFound(TeamError):
    status_code = 404
    code = "person_not_found"


class TeamForbidden(TeamError):
    status_code = 403
    code = "forbidden"


class TeamConflict(TeamError):
    status_code = 409
    code = "conflict"


class TeamInvalidRequest(TeamError):
    status_code = 422
    code = "invalid_request"


_ADMIN_ROLES = frozenset({"admin", "owner"})


def _is_admin(roles: Iterable[str]) -> bool:
    return any(r in _ADMIN_ROLES for r in roles)


# ---------------------------------------------------------------------------
# Offboarding cascade — per-asset summary. The wire shape is bound to
# OffboardingRequest (api-types/team.ts §3.1); the response is a
# per-asset success/failure summary so the FE wizard can render the
# partial-success state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OffboardingAssetOutcome:
    """One row in the offboarding-cascade response.

    ``ok=True`` when the underlying service accepted the reassignment;
    ``ok=False`` carries a short ``reason`` token (e.g.
    ``"unsupported_asset_kind"``, ``"projects_service_unavailable"``,
    ``"not_found"``). Per spec the cascade does NOT roll back successful
    reassignments on partial failure — sub-PRD §1.5 U-T5 + cross-audit
    §9.8 Q1.
    """

    asset_kind: str
    asset_id: str
    new_owner_user_id: str
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class OffboardingResult:
    target_user_id: str
    outcomes: tuple[OffboardingAssetOutcome, ...]

    @property
    def reassignments_count(self) -> int:
        """Successful reassignments only — used in the SSE envelope."""
        return sum(1 for o in self.outcomes if o.ok)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class TeamService:
    """Team destination domain service.

    Composition (Protocol-shaped — every dep is substitutable):

    * ``store`` — :class:`TeamStore` for read projections.
    * ``identity_store`` — the canonical :class:`IdentityStore`. Role
      change + offboard mutations write through this; audit appends ride
      on the same transaction.
    * ``invitations_service`` — the existing
      :class:`InvitationsService`. ``invite()`` is a thin wrapper.
    * ``projects_service`` — optional; when present, used for the
      project reassignment leg of the offboarding cascade
      (``force_transfer_ownership``). Absence surfaces as
      ``projects_service_unavailable`` on the per-asset outcome.
    """

    store: TeamStore
    identity_store: Any  # IdentityStore Protocol
    invitations_service: InvitationsService
    projects_service: Any | None = None  # ProjectsService — optional

    # ---------- list / detail ---------------------------------------

    def list_people(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        role: TeamRole | None = None,
        presence: Presence | None = None,
        q: str | None = None,
        sort: str = "display_name:asc",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[PersonRow, ...], str | None]:
        """Tenant-member read. Caller must already be a tenant member;
        non-members see 404 on detail and an empty list on list (the
        list path is bound by ``tenant_id`` from the verified session
        so a non-tenant-member can never reach a sibling tenant's rows
        even with a crafted query)."""

        _ = caller_roles  # admin-only `recent_activity` lives in get_person
        self._require_tenant_member(tenant_id=tenant_id, caller_user_id=caller_user_id)
        return self.store.list_people(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            role=role,
            presence=presence,
            q=q,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )

    def get_person(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        user_id: str,
    ) -> PersonRow:
        """Tenant-member read for the person row itself. Returns 404
        when the caller is not a tenant member or the target user does
        not exist on this tenant — never 403 (existence-not-leaked,
        cross-audit §1.3)."""

        _ = caller_roles
        self._require_tenant_member(tenant_id=tenant_id, caller_user_id=caller_user_id)
        row = self.store.get_person(tenant_id=tenant_id, user_id=user_id)
        if row is None:
            raise TeamNotFound(user_id)
        return row

    # ---------- invite (admin) --------------------------------------

    def invite(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        email: str,
        role: TeamRole,
        note: str | None = None,
    ) -> Any:
        """Mint a workspace invite via the existing IdP path.

        Delegates to :class:`InvitationsService.create` — there is
        intentionally no parallel invite store, no parallel email
        dispatcher, and no new audit row written here. The identity
        invite path already audits ``invitation.created`` and the
        admin pending-list reads through ``InvitationsService``.
        """

        if not _is_admin(caller_roles):
            raise TeamForbidden("admin_required")
        # Surface 422/409 from the existing service unchanged. The
        # ``note`` field is wire-only for now (sub-PRD §7.1 — the email
        # body lives in the identity dispatcher; future work routes
        # the note through the existing template instead of inventing
        # a parallel template).
        _ = note
        try:
            return self.invitations_service.create(
                org_id=tenant_id,
                email=email,
                role_name=team_role_to_system_role(role),
                created_by_user_id=caller_user_id,
            )
        except InvitationConflict as exc:
            raise TeamConflict(str(exc) or "conflict") from exc
        except InvitationBadRequest as exc:
            raise TeamInvalidRequest(str(exc) or "invalid_request") from exc

    # ---------- role change (admin) ---------------------------------

    def update_role(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        target_user_id: str,
        new_role: TeamRole,
    ) -> PersonRow:
        """Change a member's role with the team-specific invariants.

        Invariants (sub-PRD §6.1):
        * Caller must be admin → 403 ``admin_required``.
        * Caller cannot demote self → 409 ``cannot_demote_self``.
        * Cannot demote the sole owner → 409 ``cannot_demote_sole_owner``.

        Writes assign + revoke role rows inside one
        :meth:`IdentityStore.transaction` block alongside the audit
        append. The C3 atomicity invariant is preserved here just as it
        is in :func:`backend_app.routes.members.patch_member`.
        """

        if not _is_admin(caller_roles):
            raise TeamForbidden("admin_required")

        current = self.store.get_person(tenant_id=tenant_id, user_id=target_user_id)
        if current is None:
            raise TeamNotFound(target_user_id)

        # Demote-self guard. Even if the target user is the caller and
        # the new_role is still admin, we accept a no-op; the guard
        # only fires when the role would actually change downward.
        if target_user_id == caller_user_id and current.role != new_role:
            raise TeamConflict("cannot_demote_self")

        # Sole-owner guard. "owner" is the founding admin role; demoting
        # the only ``owner`` would leave the tenant with no owner —
        # reject. This mirrors the identity ``cannot_remove_last_admin``
        # guard in members.py (PR 4.2).
        if current.role == "owner" and new_role != "owner":
            if self._count_owners(tenant_id=tenant_id) <= 1:
                raise TeamConflict("cannot_demote_sole_owner")

        if current.role == new_role:
            return current

        new_system_role = team_role_to_system_role(new_role)
        new_role_record = self.identity_store.get_role_by_name(
            org_id=None, name=new_system_role
        )
        if new_role_record is None:
            raise TeamInvalidRequest("role_unavailable")

        prev_assignments = self.identity_store.list_role_assignments(
            org_id=tenant_id, user_id=target_user_id
        )

        with self.identity_store.transaction():
            for asn in prev_assignments:
                self.identity_store.revoke_role(
                    org_id=tenant_id,
                    user_id=target_user_id,
                    role_id=asn.role_id,
                    reason="team.role_change",
                )
            self.identity_store.assign_role(
                RoleAssignmentRecord(
                    org_id=tenant_id,
                    user_id=target_user_id,
                    role_id=new_role_record.role_id,
                    granted_by_user_id=caller_user_id,
                    reason="team.role_change",
                )
            )
            self.identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=tenant_id,
                    actor_user_id=caller_user_id,
                    subject_user_id=target_user_id,
                    action="team.role_changed",
                    metadata={
                        "before_role": current.role,
                        "after_role": new_role,
                    },
                )
            )

        refreshed = self.store.get_person(tenant_id=tenant_id, user_id=target_user_id)
        if refreshed is None:
            # Shouldn't happen — we just wrote a row. Surface as 404 so
            # the FE doesn't see a 500 on a benign race.
            raise TeamNotFound(target_user_id)
        return refreshed

    # ---------- offboarding (admin) ---------------------------------

    def offboard(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        target_user_id: str,
        reassignments: Iterable[tuple[str, str, str]],
    ) -> OffboardingResult:
        """Per-asset reassignment cascade — NO force-transfer endpoint.

        Each ``reassignments[i]`` is ``(asset_kind, asset_id,
        new_owner_user_id)``. The cascade dispatches per ``asset_kind``:

        * ``"project"`` → :meth:`ProjectsService.force_transfer_ownership`.
        * ``"agent"`` / ``"tool"`` / ``"connector"`` — no admin
          reassign path exists on the source services
          (cross-audit §9.8 Q1 — naive force-transfer remains a
          security hazard). The outcome surfaces
          ``unsupported_asset_kind`` so the wizard can ask the owner
          to perform the handoff (or the asset retires with the user).

        Partial failure is intentional: successful reassignments
        commit; failed ones surface in the outcome list. The whole
        cascade is NOT atomic across services (sub-PRD §1.5 U-T5).

        Writes a single ``team.offboarded`` audit row at the end with
        the per-asset outcome summary for the compliance trail.
        """

        if not _is_admin(caller_roles):
            raise TeamForbidden("admin_required")
        target = self.store.get_person(tenant_id=tenant_id, user_id=target_user_id)
        if target is None:
            raise TeamNotFound(target_user_id)

        outcomes: list[OffboardingAssetOutcome] = []
        for asset_kind, asset_id, new_owner in reassignments:
            outcomes.append(
                self._reassign_one(
                    tenant_id=tenant_id,
                    caller_user_id=caller_user_id,
                    caller_roles=caller_roles,
                    asset_kind=asset_kind,
                    asset_id=asset_id,
                    new_owner_user_id=new_owner,
                )
            )

        # One audit row summarising the cascade, written through the
        # identity audit path so the trail is uniform with role-change
        # / member-remove. The metadata carries enough to replay the
        # admin's decision in a SIEM query.
        with self.identity_store.transaction():
            self.identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=tenant_id,
                    actor_user_id=caller_user_id,
                    subject_user_id=target_user_id,
                    action="team.offboarded",
                    metadata={
                        "reassignments_count": sum(1 for o in outcomes if o.ok),
                        "outcomes": [
                            {
                                "asset_kind": o.asset_kind,
                                "asset_id": o.asset_id,
                                "new_owner_user_id": o.new_owner_user_id,
                                "ok": o.ok,
                                "reason": o.reason,
                            }
                            for o in outcomes
                        ],
                    },
                )
            )

        return OffboardingResult(
            target_user_id=target_user_id, outcomes=tuple(outcomes)
        )

    def _reassign_one(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        asset_kind: str,
        asset_id: str,
        new_owner_user_id: str,
    ) -> OffboardingAssetOutcome:
        if asset_kind == "project":
            if self.projects_service is None:
                return OffboardingAssetOutcome(
                    asset_kind=asset_kind,
                    asset_id=asset_id,
                    new_owner_user_id=new_owner_user_id,
                    ok=False,
                    reason="projects_service_unavailable",
                )
            try:
                self.projects_service.force_transfer_ownership(
                    tenant_id=tenant_id,
                    caller_user_id=caller_user_id,
                    caller_roles=caller_roles,
                    project_id=asset_id,
                    new_owner_user_id=new_owner_user_id,
                    reason="team.offboarding",
                )
            except Exception as exc:  # noqa: BLE001 — surface the source error verbatim
                return OffboardingAssetOutcome(
                    asset_kind=asset_kind,
                    asset_id=asset_id,
                    new_owner_user_id=new_owner_user_id,
                    ok=False,
                    reason=_short_reason(exc),
                )
            return OffboardingAssetOutcome(
                asset_kind=asset_kind,
                asset_id=asset_id,
                new_owner_user_id=new_owner_user_id,
                ok=True,
            )

        # cross-audit §9.8 Q1 + sub-PRD §10 Q6 — Routines §9.7 Q12
        # STAYS DEFERRED. The agent/tool/connector handoff requires
        # the owner to perform the transfer themselves; no admin
        # force-transfer ships in P12. The wizard surfaces this
        # outcome so the admin can chase the owner manually.
        if asset_kind in ("agent", "tool", "connector"):
            return OffboardingAssetOutcome(
                asset_kind=asset_kind,
                asset_id=asset_id,
                new_owner_user_id=new_owner_user_id,
                ok=False,
                reason="unsupported_asset_kind",
            )

        return OffboardingAssetOutcome(
            asset_kind=asset_kind,
            asset_id=asset_id,
            new_owner_user_id=new_owner_user_id,
            ok=False,
            reason="unknown_asset_kind",
        )

    # ---------- helpers ---------------------------------------------

    def _require_tenant_member(self, *, tenant_id: str, caller_user_id: str) -> None:
        """Caller must be an active member of ``tenant_id``. Otherwise
        404 — existence-not-leaked."""

        members = self.identity_store.list_members(org_id=tenant_id)
        for m in members:
            if m.user_id == caller_user_id and m.removed_at is None:
                return
        raise TeamNotFound("tenant")

    def _count_owners(self, *, tenant_id: str) -> int:
        owner_role = self.identity_store.get_role_by_name(org_id=None, name="owner")
        if owner_role is None:
            return 0
        count = 0
        for m in self.identity_store.list_members(org_id=tenant_id):
            if m.removed_at is not None:
                continue
            asns = self.identity_store.list_role_assignments(
                org_id=tenant_id, user_id=m.user_id
            )
            if any(a.role_id == owner_role.role_id for a in asns):
                count += 1
        return count


def _short_reason(exc: BaseException) -> str:
    """Normalize an exception to a short token for the outcome row.

    The class name + first ``args[0]`` (when string-like) keeps the
    compliance trail searchable without leaking a full traceback to
    the wire (cross-audit §3.2 "never an exception trace").
    """

    arg0 = exc.args[0] if exc.args and isinstance(exc.args[0], str) else None
    if arg0:
        return arg0
    return type(exc).__name__


__all__ = [
    "OffboardingAssetOutcome",
    "OffboardingResult",
    "TeamConflict",
    "TeamError",
    "TeamForbidden",
    "TeamInvalidRequest",
    "TeamNotFound",
    "TeamService",
]
