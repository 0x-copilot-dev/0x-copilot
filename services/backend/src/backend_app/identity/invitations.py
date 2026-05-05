"""Invitations service (PR 4.2).

Workspace-scoped invitations: an admin mints a one-time bearer link, the
recipient clicks it, the accept endpoint creates a ``users`` row + active
``organization_members`` row + initial ``role_assignments`` row inside one
transaction. Soft revoke + soft accept; the row never goes away.

Token shape mirrors :class:`backend_app.identity.scim.ScimService` exactly —
``secrets.token_urlsafe(32)``, sha256 hash at rest, 8-char prefix shown in
the admin pending-list UI. The plaintext is in the response **once**.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend_app.contracts import (
    IdentityAuditEventRecord,
    InvitationMintResult,
    InvitationRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    RoleAssignmentRecord,
    UserRecord,
    UserStatus,
)
from backend_app.identity.invitation_store import InvitationStore
from backend_app.identity.store import IdentityStore


# Bound the TTL the admin can request. 30 days is the upper bound across the
# enterprise SaaS norm; 1 minute is the lower bound to keep round-tripping
# possible in CI / e2e tests. Defaults to 7 days.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 30 * 24 * 3600


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors → mapped to HTTP status codes by the route layer.
# ---------------------------------------------------------------------------


class InvitationError(RuntimeError):
    status_code = 400
    code = "invitation_error"


class InvitationConflict(InvitationError):
    status_code = 409
    code = "conflict"


class InvitationNotFound(InvitationError):
    status_code = 404
    code = "invitation_not_found"


class InvitationGone(InvitationError):
    status_code = 410
    code = "invitation_gone"


class InvitationBadRequest(InvitationError):
    status_code = 422
    code = "invalid_request"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvitationAcceptResult:
    """Returned by ``InvitationsService.accept``. Surface ``user_id`` and
    ``org_id`` so the FE can chain into the existing login flow with a
    confirmation banner."""

    invite_id: str
    org_id: str
    org_display_name: str
    user_id: str
    role_name: str


@dataclass
class InvitationsService:
    identity_store: IdentityStore
    invitation_store: InvitationStore

    # ----- Create / List / Revoke (admin) --------------------------------
    def create(
        self,
        *,
        org_id: str,
        email: str,
        role_name: str,
        created_by_user_id: str,
        ttl_seconds: int | None = None,
    ) -> InvitationMintResult:
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise InvitationBadRequest("invalid_email")

        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
        if ttl < MIN_TTL_SECONDS or ttl > MAX_TTL_SECONDS:
            raise InvitationBadRequest("invalid_ttl_seconds")

        role = self._require_role(role_name=role_name)

        # Reject if a user with this email is already an active member.
        existing_user = self.identity_store.get_user_by_email(
            org_id=org_id, email=normalized_email
        )
        if existing_user is not None:
            members = self.identity_store.list_members(org_id=org_id)
            if any(
                m.user_id == existing_user.user_id and m.removed_at is None
                for m in members
            ):
                raise InvitationConflict("already_a_member")

        # Reject if there's already an active invitation for this email.
        if self.invitation_store.get_active_for_email(
            org_id=org_id, email=normalized_email
        ):
            raise InvitationConflict("active_invitation_exists")

        plaintext = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        token_prefix = plaintext[:8]

        record = InvitationRecord(
            org_id=org_id,
            email=normalized_email,
            role_id=role.role_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            created_by_user_id=created_by_user_id,
            expires_at=_now() + timedelta(seconds=ttl),
        )

        with self.identity_store.transaction():
            self.invitation_store.create(record)
            self.identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=created_by_user_id,
                    action="invitation.create",
                    metadata={
                        "invite_id": record.invite_id,
                        "email": normalized_email,
                        "role": role.name,
                        "expires_at": record.expires_at.isoformat(),
                    },
                )
            )

        return InvitationMintResult(
            invite_id=record.invite_id,
            token_plaintext=plaintext,
            token_prefix=token_prefix,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )

    def list_pending(self, *, org_id: str) -> tuple[InvitationRecord, ...]:
        return self.invitation_store.list_pending(org_id=org_id)

    def revoke(
        self,
        *,
        org_id: str,
        invite_id: str,
        actor_user_id: str,
    ) -> bool:
        record = self.invitation_store.get(org_id=org_id, invite_id=invite_id)
        if record is None:
            return False
        if record.accepted_at is not None or record.revoked_at is not None:
            return False
        with self.identity_store.transaction():
            ok = self.invitation_store.revoke(
                invite_id=invite_id, revoked_by_user_id=actor_user_id
            )
            if ok:
                self.identity_store.append_identity_audit(
                    IdentityAuditEventRecord(
                        org_id=org_id,
                        actor_user_id=actor_user_id,
                        action="invitation.revoke",
                        metadata={"invite_id": invite_id},
                    )
                )
        return ok

    # ----- Accept (no auth) ----------------------------------------------
    def accept(
        self,
        *,
        token_plaintext: str,
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> InvitationAcceptResult:
        if not token_plaintext:
            raise InvitationBadRequest("missing token")

        token_hash = hashlib.sha256(token_plaintext.encode("utf-8")).hexdigest()
        invitation = self.invitation_store.get_by_token_hash(token_hash=token_hash)
        if invitation is None:
            raise InvitationNotFound("invitation_not_found")
        if invitation.revoked_at is not None:
            raise InvitationGone("invitation_revoked")
        if invitation.accepted_at is not None:
            raise InvitationConflict("invitation_already_accepted")
        if invitation.expires_at <= _now():
            raise InvitationGone("invitation_expired")

        org = self.identity_store.get_organization(org_id=invitation.org_id)
        if org is None or org.deleted_at is not None:
            # Edge: org soft-deleted between mint and accept. Treat as gone.
            raise InvitationGone("invitation_gone")

        role = self.identity_store.get_role(role_id=invitation.role_id)
        if role is None or role.deleted_at is not None:
            raise InvitationGone("role_unavailable")

        with self.identity_store.transaction():
            user = self.identity_store.get_user_by_email(
                org_id=invitation.org_id, email=invitation.email
            )
            if user is None:
                user = self.identity_store.create_user(
                    UserRecord(
                        org_id=invitation.org_id,
                        primary_email=invitation.email,
                        display_name=invitation.email,
                        # PENDING_INVITE so the first-login flow can promote
                        # the row to ACTIVE without re-checking identity here.
                        status=UserStatus.PENDING_INVITE,
                    )
                )

            # Idempotent on already-member: insert only if no active membership.
            members = self.identity_store.list_members(org_id=invitation.org_id)
            already_member = any(
                m.user_id == user.user_id and m.removed_at is None for m in members
            )
            if not already_member:
                self.identity_store.add_member(
                    OrganizationMemberRecord(
                        org_id=invitation.org_id,
                        user_id=user.user_id,
                        source=OrganizationMemberSource.INVITE,
                        invited_by_user_id=invitation.created_by_user_id,
                    )
                )

            # Idempotent on already-assigned: insert only if no active assignment.
            active_assignments = self.identity_store.list_role_assignments(
                org_id=invitation.org_id, user_id=user.user_id
            )
            if not any(a.role_id == role.role_id for a in active_assignments):
                self.identity_store.assign_role(
                    RoleAssignmentRecord(
                        org_id=invitation.org_id,
                        user_id=user.user_id,
                        role_id=role.role_id,
                        granted_by_user_id=invitation.created_by_user_id,
                    )
                )

            self.invitation_store.mark_accepted(
                invite_id=invitation.invite_id, accepted_user_id=user.user_id
            )
            self.identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=invitation.org_id,
                    actor_user_id=user.user_id,
                    subject_user_id=user.user_id,
                    action="invitation.accept",
                    metadata={
                        "invite_id": invitation.invite_id,
                        "accepted_user_id": user.user_id,
                        "role": role.name,
                    },
                    request_ip=request_ip,
                    user_agent=user_agent,
                )
            )

        return InvitationAcceptResult(
            invite_id=invitation.invite_id,
            org_id=invitation.org_id,
            org_display_name=org.display_name,
            user_id=user.user_id,
            role_name=role.name,
        )

    # ----- Helpers --------------------------------------------------------
    def _require_role(self, *, role_name: str):
        # System roles ('admin' | 'employee' | 'auditor' | 'service') are
        # mapped to the same names the design doc uses ('admin' | 'member' |
        # 'viewer'). PR 4.2 ships the system roles only; custom roles ride a
        # later PR. The display alias keeps the API ergonomic without a new
        # role row per workspace.
        canonical = _ROLE_ALIASES.get(role_name.lower(), role_name.lower())
        role = self.identity_store.get_role_by_name(org_id=None, name=canonical)
        if role is None or role.deleted_at is not None:
            raise InvitationBadRequest("unknown_role")
        return role


# Map the design-doc role names to the system-role names already seeded in
# 0004b_seed_system_roles.sql. No new SQL; we just translate.
_ROLE_ALIASES = {
    "admin": "admin",
    "member": "employee",
    "employee": "employee",
    "viewer": "auditor",
    "auditor": "auditor",
}


def design_role_alias_for(*, system_role_name: str) -> str:
    """Reverse mapping used by the routes when projecting a role for the FE."""
    if system_role_name == "employee":
        return "member"
    if system_role_name == "auditor":
        return "viewer"
    return system_role_name


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "InvitationAcceptResult",
    "InvitationBadRequest",
    "InvitationConflict",
    "InvitationError",
    "InvitationGone",
    "InvitationNotFound",
    "InvitationsService",
    "MAX_TTL_SECONDS",
    "MIN_TTL_SECONDS",
    "design_role_alias_for",
]
