"""The "Use locally, no account" entry method (device account).

One principal with NO external identity, entered by possession of the device.
The session is a REAL ``SessionService`` bearer — the same signed, verified
path wallet/Google get — never a dev bypass. The route that calls this is
gated by the per-install host secret (``ENTERPRISE_SERVICE_TOKEN``): only the
desktop main process can mint, never a browser page (localhost is reachable
by any open tab — CSRF/DNS-rebinding — so a proof-less mint endpoint is the
one door we must not build).

Find-or-create is DB-arbitrated (the ``local_accounts`` singleton edge), so
"Use locally" is idempotent by construction: reinstalls, lost client state,
and races all resolve to the ONE device account (D4-A). After the account
links a wallet/Google, this entry keeps opening the same account — on a
local-first app the data sits on disk, so a login-screen wallet was never
cryptographically gating it; pretending otherwise would be theater.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from backend_app.contracts import (
    IdentityAuditEventRecord,
    LocalAccountRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    SessionMintResult,
    UserRecord,
    UserStatus,
)
from backend_app.identity.provisioning import provision_personal_org
from backend_app.identity.sessions import SessionService

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend_app.identity.local_account_store import LocalAccountStore
    from backend_app.identity.store import IdentityStore

LOCAL_PLACEHOLDER_EMAIL_DOMAIN = "local.invalid"
LOCAL_AUTH_PROVIDER_ID = "local_device"
LOCAL_ACCOUNT_DISPLAY_NAME = "Local account"


class LocalAccountError(RuntimeError):
    detail = "local_account_error"


class LocalAccountDisabled(LocalAccountError):
    """The device account exists but its user is disabled (e.g. absorbed)."""

    detail = "local_account_disabled"


class LocalAccountService:
    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        local_store: LocalAccountStore,
        sessions: SessionService,
    ) -> None:
        self._identity = identity_store
        self._local = local_store
        self._sessions = sessions

    def ensure_device_session(self) -> tuple[SessionMintResult, UserRecord, bool]:
        """Resolve-or-provision the device account and mint its session.

        Returns ``(mint_result, user, created)``. Idempotent: the singleton
        edge arbitrates, so every call lands on the same account.
        """

        edge = self._local.get_singleton()
        if edge is not None:
            user = self._identity.get_user(org_id=edge.org_id, user_id=edge.user_id)
            if user is None or user.status != UserStatus.ACTIVE:
                raise LocalAccountDisabled(
                    "the device account is disabled; sign in with a linked "
                    "identity instead"
                )
            return self._mint(user), user, False

        user = self._provision()
        # A concurrent creator may have won the singleton race — the store
        # returns the WINNING row; re-resolve when it isn't ours.
        edge = self._local.create(
            LocalAccountRecord(org_id=user.org_id, user_id=user.user_id)
        )
        created = edge.user_id == user.user_id
        if not created:
            winner = self._identity.get_user(org_id=edge.org_id, user_id=edge.user_id)
            if winner is None:  # pragma: no cover - defensive
                raise LocalAccountError("device account race left no user")
            user = winner
        return self._mint(user), user, created

    def _provision(self) -> UserRecord:
        def _audit(
            org: OrganizationRecord, user: UserRecord
        ) -> list[IdentityAuditEventRecord]:
            return [
                IdentityAuditEventRecord(
                    org_id=org.org_id,
                    actor_user_id=user.user_id,
                    subject_user_id=user.user_id,
                    action="identity.local_account_provisioned",
                    metadata={"entry": "use_locally"},
                )
            ]

        # users.primary_email is NOT NULL: anchor a syntactically valid,
        # undeliverable placeholder on the reserved .invalid TLD (RFC 2606),
        # exactly like wallet accounts do.
        suffix = secrets.token_hex(4)
        _, user = provision_personal_org(
            identity_store=self._identity,
            org_display_name="This Device",
            slug_base=f"local-{suffix}",
            primary_email=f"device-{suffix}@{LOCAL_PLACEHOLDER_EMAIL_DOMAIN}",
            user_display_name=LOCAL_ACCOUNT_DISPLAY_NAME,
            email_verified_at=None,
            member_source=OrganizationMemberSource.LOCAL,
            audit_events=_audit,
        )
        return user

    # Mirrors SiweService._mint_session / OidcService._mint_session.
    def _mint(self, user: UserRecord) -> SessionMintResult:
        role_records = self._identity.list_role_assignments(
            org_id=user.org_id, user_id=user.user_id
        )
        role_names: list[str] = []
        permission_scopes: set[str] = set()
        for assignment in role_records:
            role = self._identity.get_role(role_id=assignment.role_id)
            if role is None:
                continue
            role_names.append(role.name)
            permission_scopes.update(role.permission_scopes)
        if not role_names:
            role_names = ["employee"]
            employee = self._identity.get_role_by_name(org_id=None, name="employee")
            if employee is not None:
                permission_scopes.update(employee.permission_scopes)
        return self._sessions.create(
            org_id=user.org_id,
            user_id=user.user_id,
            roles=tuple(role_names),
            permission_scopes=tuple(sorted(permission_scopes)),
            auth_provider_id=LOCAL_AUTH_PROVIDER_ID,
            device_label="local",
        )
