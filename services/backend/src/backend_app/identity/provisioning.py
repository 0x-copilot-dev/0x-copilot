"""Shared personal-org self-signup provisioning.

First login through a deployment-global entry ramp (the env-configured
Google provider, the SIWE wallet ramp) creates a *personal org*: org +
user + membership + system ``admin`` role assignment, committed in one
identity-store transaction, with the caller's audit events appended
inside the same transaction.

Extracted from ``OidcService._self_signup_provision`` so SIWE (and any
future global ramp) reuses the exact provisioning shape instead of
duplicating it. Policy checks (deployment ``allow_self_signup`` toggle,
email-verified refusals, login-attempt bookkeeping) stay with the caller
— this module only owns the transactional create.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import datetime

from backend_app.contracts import (
    IdentityAuditEventRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    RoleAssignmentRecord,
    UserRecord,
)
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

# Personal-org slug derivation: keep only slug-legal characters from the
# caller-supplied base; collision retries append a short random hex suffix.
_SLUG_STRIP_PATTERN = re.compile(r"[^a-z0-9_-]+")
_SLUG_MAX_COLLISION_RETRIES = 32


class PersonalOrgSlugExhausted(RuntimeError):
    """32 random-suffix collisions in a row — practically unreachable."""


def free_personal_org_slug(*, identity_store: IdentityStore, base: str) -> str:
    """Derive a free org slug from ``base`` (email local part, wallet stub).

    Lowercases, strips slug-illegal characters, and retries with a random
    hex suffix on collision.
    """

    candidate_base = _SLUG_STRIP_PATTERN.sub("-", base.lower()).strip("-_")
    if not candidate_base or not candidate_base[0].isalnum():
        candidate_base = "workspace"
    candidate = candidate_base
    for _ in range(_SLUG_MAX_COLLISION_RETRIES):
        if identity_store.get_organization_by_slug(slug=candidate) is None:
            return candidate
        candidate = f"{candidate_base}-{secrets.token_hex(2)}"
    raise PersonalOrgSlugExhausted(
        f"could not derive a free org slug from {base!r}"
    )  # pragma: no cover - 32 random collisions


def provision_personal_org(
    *,
    identity_store: IdentityStore,
    org_display_name: str,
    slug_base: str,
    primary_email: str,
    user_display_name: str,
    email_verified_at: datetime | None,
    member_source: OrganizationMemberSource,
    audit_events: Callable[
        [OrganizationRecord, UserRecord], Sequence[IdentityAuditEventRecord]
    ],
    slug: str | None = None,
) -> tuple[OrganizationRecord, UserRecord]:
    """Create org + user + membership + admin role in one transaction.

    The sole member of a personal org is its admin (system ``admin``
    role). ``audit_events`` is called with the freshly created org + user
    and its events are appended INSIDE the transaction so a rollback
    leaves no orphaned audit rows.
    """

    resolved_slug = slug or free_personal_org_slug(
        identity_store=identity_store, base=slug_base
    )
    with identity_store.transaction():
        org = identity_store.create_organization(
            OrganizationRecord(display_name=org_display_name, slug=resolved_slug)
        )
        user = identity_store.create_user(
            UserRecord(
                org_id=org.org_id,
                primary_email=primary_email,
                display_name=user_display_name,
                email_verified_at=email_verified_at,
            )
        )
        identity_store.add_member(
            OrganizationMemberRecord(
                org_id=org.org_id,
                user_id=user.user_id,
                source=member_source,
            )
        )
        admin_role = identity_store.get_role_by_name(org_id=None, name="admin")
        if admin_role is not None:
            identity_store.assign_role(
                RoleAssignmentRecord(
                    org_id=org.org_id,
                    user_id=user.user_id,
                    role_id=admin_role.role_id,
                )
            )
        else:  # pragma: no cover - system-role seed missing
            _LOGGER.warning(
                "self-signup: system role 'admin' missing; user %s falls "
                "back to default employee scopes",
                user.user_id,
            )
        for event in audit_events(org, user):
            identity_store.append_identity_audit(event)
    return org, user


__all__ = [
    "PersonalOrgSlugExhausted",
    "free_personal_org_slug",
    "provision_personal_org",
]
