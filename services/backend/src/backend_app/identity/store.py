"""Identity store (A1): user / org / role / auth-provider / audit / login-attempt.

Schema-only PR. The interface and both adapters land so subsequent PRs (A2
sessions, A3 OIDC, A4 local password, etc.) compose against a stable seam.
``identity_audit_events`` and ``login_attempts`` are append-only at this
layer (no update / delete methods exposed).

The Postgres adapter shares the existing ``PostgresConnectionPool`` (see
backend_app.store) so tests can inject the same singleton fixture used by
the MCP and skill stores.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    AuthProviderRecord,
    IdentityAuditEventRecord,
    IdentityPolicyRecord,
    LoginAttemptRecord,
    OrganizationMemberRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    UserRecord,
)


_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IdentityStore(Protocol):
    """Contract every adapter implements.

    Methods are organised by entity. Soft-delete is exposed via dedicated
    ``delete_*`` methods that flip ``deleted_at`` rather than removing rows;
    a re-create with the same business key (``slug`` for orgs,
    ``(org_id, primary_email)`` for users, etc.) succeeds because all
    uniqueness indexes are partial on ``WHERE deleted_at IS NULL``.
    """

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Yield a transactional scope. In-memory adapter is a no-op."""
        ...  # pragma: no cover

    # Organizations -----------------------------------------------------
    def create_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord: ...
    def get_organization(self, *, org_id: str) -> OrganizationRecord | None: ...
    def get_organization_by_slug(self, *, slug: str) -> OrganizationRecord | None: ...
    def update_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord: ...
    def delete_organization(self, *, org_id: str, conn: Any | None = None) -> bool: ...

    # Users -------------------------------------------------------------
    def create_user(
        self, record: UserRecord, *, conn: Any | None = None
    ) -> UserRecord: ...
    def get_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_deleted: bool = False,
    ) -> UserRecord | None: ...
    def get_user_by_email(self, *, org_id: str, email: str) -> UserRecord | None: ...
    def list_users(
        self, *, org_id: str, include_deleted: bool = False
    ) -> tuple[UserRecord, ...]: ...
    def update_user(
        self, record: UserRecord, *, conn: Any | None = None
    ) -> UserRecord: ...
    def delete_user(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool: ...

    # Organization members ---------------------------------------------
    def add_member(
        self, record: OrganizationMemberRecord, *, conn: Any | None = None
    ) -> OrganizationMemberRecord: ...
    def remove_member(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool: ...
    def list_members(self, *, org_id: str) -> tuple[OrganizationMemberRecord, ...]: ...

    # Roles -------------------------------------------------------------
    def create_role(
        self, record: RoleRecord, *, conn: Any | None = None
    ) -> RoleRecord: ...
    def get_role(self, *, role_id: str) -> RoleRecord | None: ...
    def get_role_by_name(
        self, *, org_id: str | None, name: str
    ) -> RoleRecord | None: ...
    def list_roles(self, *, org_id: str | None) -> tuple[RoleRecord, ...]: ...
    def update_role(
        self, record: RoleRecord, *, conn: Any | None = None
    ) -> RoleRecord: ...
    def delete_role(self, *, role_id: str, conn: Any | None = None) -> bool: ...

    # Role assignments --------------------------------------------------
    def assign_role(
        self, record: RoleAssignmentRecord, *, conn: Any | None = None
    ) -> RoleAssignmentRecord: ...
    def revoke_role(
        self,
        *,
        org_id: str,
        user_id: str,
        role_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool: ...
    def list_role_assignments(
        self, *, org_id: str, user_id: str
    ) -> tuple[RoleAssignmentRecord, ...]: ...

    # Auth providers ----------------------------------------------------
    def create_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord: ...
    def get_auth_provider(
        self, *, org_id: str, provider_id: str
    ) -> AuthProviderRecord | None: ...
    def get_auth_provider_by_id(self, provider_id: str) -> AuthProviderRecord | None:
        """Lookup without requiring org_id.

        Used by anonymous SSO callback endpoints (SAML ACS, SCIM token
        validation) where the org is recovered from the provider row
        itself. Cross-tenant attacks remain impossible because every
        downstream lookup is scoped by ``(provider_id, identity_key)`` —
        an assertion signed by org_a's IdP cert cannot link to org_b
        because the linking row is per-provider, not per-org.
        """

    def list_auth_providers(
        self, *, org_id: str, enabled_only: bool = False
    ) -> tuple[AuthProviderRecord, ...]: ...
    def update_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord: ...
    def delete_auth_provider(
        self, *, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool: ...

    # Audit -------------------------------------------------------------
    def append_identity_audit(
        self, record: IdentityAuditEventRecord, *, conn: Any | None = None
    ) -> IdentityAuditEventRecord: ...
    def list_identity_audit(
        self, *, org_id: str, limit: int = 100
    ) -> tuple[IdentityAuditEventRecord, ...]: ...

    # Login attempts ----------------------------------------------------
    def append_login_attempt(
        self, record: LoginAttemptRecord, *, conn: Any | None = None
    ) -> LoginAttemptRecord: ...
    def list_login_attempts(
        self,
        *,
        org_id: str | None,
        email: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LoginAttemptRecord, ...]: ...

    # Identity policy (auth-method toggles) -----------------------------
    def get_identity_policy(self, *, org_id: str) -> IdentityPolicyRecord | None: ...
    def upsert_identity_policy(
        self, record: IdentityPolicyRecord, *, conn: Any | None = None
    ) -> IdentityPolicyRecord: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryIdentityStore:
    """Dict-backed adapter for tests and dev. Mirrors Postgres semantics."""

    organizations: dict[str, OrganizationRecord] = field(default_factory=dict)
    users: dict[str, UserRecord] = field(default_factory=dict)
    members: dict[str, OrganizationMemberRecord] = field(default_factory=dict)
    roles: dict[str, RoleRecord] = field(default_factory=dict)
    role_assignments: dict[str, RoleAssignmentRecord] = field(default_factory=dict)
    auth_providers: dict[str, AuthProviderRecord] = field(default_factory=dict)
    identity_audit_events: list[IdentityAuditEventRecord] = field(default_factory=list)
    login_attempts: list[LoginAttemptRecord] = field(default_factory=list)
    identity_policies: dict[str, IdentityPolicyRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Organizations -----------------------------------------------------
    def create_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord:
        del conn
        if record.slug in {
            o.slug for o in self.organizations.values() if o.deleted_at is None
        }:
            raise ValueError(f"organization slug already exists: {record.slug}")
        self.organizations[record.org_id] = record
        _log_write("organizations", record.org_id, "insert")
        return record

    def get_organization(self, *, org_id: str) -> OrganizationRecord | None:
        record = self.organizations.get(org_id)
        if record is None or record.deleted_at is not None:
            return None
        return record

    def get_organization_by_slug(self, *, slug: str) -> OrganizationRecord | None:
        for record in self.organizations.values():
            if record.slug == slug and record.deleted_at is None:
                return record
        return None

    def update_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord:
        del conn
        if record.org_id not in self.organizations:
            raise ValueError(f"organization not found: {record.org_id}")
        updated = record.model_copy(update={"updated_at": _now()})
        self.organizations[record.org_id] = updated
        _log_write("organizations", record.org_id, "update")
        return updated

    def delete_organization(self, *, org_id: str, conn: Any | None = None) -> bool:
        del conn
        existing = self.organizations.get(org_id)
        if existing is None or existing.deleted_at is not None:
            return False
        self.organizations[org_id] = existing.model_copy(
            update={"deleted_at": _now(), "status": "deleted"}
        )
        _log_write("organizations", org_id, "delete")
        return True

    # Users -------------------------------------------------------------
    def create_user(self, record: UserRecord, *, conn: Any | None = None) -> UserRecord:
        del conn
        if self._active_user_email_exists(record.org_id, record.primary_email):
            raise ValueError(
                f"user with email already exists in org: {record.primary_email}"
            )
        self.users[record.user_id] = record
        _log_write("users", record.org_id, "insert")
        return record

    def get_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_deleted: bool = False,
    ) -> UserRecord | None:
        record = self.users.get(user_id)
        if record is None or record.org_id != org_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def get_user_by_email(self, *, org_id: str, email: str) -> UserRecord | None:
        normalized = email.strip().lower()
        for record in self.users.values():
            if (
                record.org_id == org_id
                and record.deleted_at is None
                and record.primary_email == normalized
            ):
                return record
        return None

    def list_users(
        self, *, org_id: str, include_deleted: bool = False
    ) -> tuple[UserRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.users.values()
                    if record.org_id == org_id
                    and (include_deleted or record.deleted_at is None)
                ),
                key=lambda r: r.created_at,
            )
        )

    def update_user(self, record: UserRecord, *, conn: Any | None = None) -> UserRecord:
        del conn
        if record.user_id not in self.users:
            raise ValueError(f"user not found: {record.user_id}")
        updated = record.model_copy(update={"updated_at": _now()})
        self.users[record.user_id] = updated
        _log_write("users", record.org_id, "update")
        return updated

    def delete_user(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        existing = self.users.get(user_id)
        if (
            existing is None
            or existing.org_id != org_id
            or existing.deleted_at is not None
        ):
            return False
        self.users[user_id] = existing.model_copy(
            update={"deleted_at": _now(), "status": "disabled"}
        )
        _log_write("users", org_id, "delete")
        return True

    def _active_user_email_exists(self, org_id: str, email: str) -> bool:
        normalized = email.strip().lower()
        return any(
            r.org_id == org_id
            and r.deleted_at is None
            and r.primary_email == normalized
            for r in self.users.values()
        )

    # Members -----------------------------------------------------------
    def add_member(
        self, record: OrganizationMemberRecord, *, conn: Any | None = None
    ) -> OrganizationMemberRecord:
        del conn
        if any(
            m.org_id == record.org_id
            and m.user_id == record.user_id
            and m.removed_at is None
            for m in self.members.values()
        ):
            raise ValueError("user is already an active member of this org")
        self.members[record.member_id] = record
        return record

    def remove_member(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        for member_id, record in list(self.members.items()):
            if (
                record.org_id == org_id
                and record.user_id == user_id
                and record.removed_at is None
            ):
                self.members[member_id] = record.model_copy(
                    update={"removed_at": _now()}
                )
                return True
        return False

    def list_members(self, *, org_id: str) -> tuple[OrganizationMemberRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.members.values()
                    if record.org_id == org_id and record.removed_at is None
                ),
                key=lambda r: r.joined_at,
            )
        )

    # Roles -------------------------------------------------------------
    def create_role(self, record: RoleRecord, *, conn: Any | None = None) -> RoleRecord:
        del conn
        if self._active_role_name_exists(record.org_id, record.name):
            raise ValueError(f"role name already exists: {record.name}")
        self.roles[record.role_id] = record
        return record

    def get_role(self, *, role_id: str) -> RoleRecord | None:
        record = self.roles.get(role_id)
        if record is None or record.deleted_at is not None:
            return None
        return record

    def get_role_by_name(self, *, org_id: str | None, name: str) -> RoleRecord | None:
        for record in self.roles.values():
            if (
                record.org_id == org_id
                and record.name == name
                and record.deleted_at is None
            ):
                return record
        return None

    def list_roles(self, *, org_id: str | None) -> tuple[RoleRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.roles.values()
                    if record.org_id == org_id and record.deleted_at is None
                ),
                key=lambda r: r.name,
            )
        )

    def update_role(self, record: RoleRecord, *, conn: Any | None = None) -> RoleRecord:
        del conn
        if record.role_id not in self.roles:
            raise ValueError(f"role not found: {record.role_id}")
        updated = record.model_copy(update={"updated_at": _now()})
        self.roles[record.role_id] = updated
        return updated

    def delete_role(self, *, role_id: str, conn: Any | None = None) -> bool:
        del conn
        existing = self.roles.get(role_id)
        if existing is None or existing.deleted_at is not None:
            return False
        if existing.is_system:
            # Defense in depth: system roles must not be soft-deleted via repo.
            raise ValueError("system roles cannot be deleted")
        self.roles[role_id] = existing.model_copy(update={"deleted_at": _now()})
        return True

    def _active_role_name_exists(self, org_id: str | None, name: str) -> bool:
        return any(
            r.org_id == org_id and r.name == name and r.deleted_at is None
            for r in self.roles.values()
        )

    # Role assignments --------------------------------------------------
    def assign_role(
        self, record: RoleAssignmentRecord, *, conn: Any | None = None
    ) -> RoleAssignmentRecord:
        del conn
        if any(
            r.org_id == record.org_id
            and r.user_id == record.user_id
            and r.role_id == record.role_id
            and r.revoked_at is None
            for r in self.role_assignments.values()
        ):
            raise ValueError("role already assigned and active")
        self.role_assignments[record.assignment_id] = record
        return record

    def revoke_role(
        self,
        *,
        org_id: str,
        user_id: str,
        role_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        for assignment_id, record in list(self.role_assignments.items()):
            if (
                record.org_id == org_id
                and record.user_id == user_id
                and record.role_id == role_id
                and record.revoked_at is None
            ):
                self.role_assignments[assignment_id] = record.model_copy(
                    update={"revoked_at": _now(), "reason": reason}
                )
                return True
        return False

    def list_role_assignments(
        self, *, org_id: str, user_id: str
    ) -> tuple[RoleAssignmentRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.role_assignments.values()
                    if record.org_id == org_id
                    and record.user_id == user_id
                    and record.revoked_at is None
                ),
                key=lambda r: r.granted_at,
            )
        )

    # Auth providers ----------------------------------------------------
    def create_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord:
        del conn
        if any(
            p.org_id == record.org_id
            and p.kind == record.kind
            and p.display_name == record.display_name
            and p.deleted_at is None
            for p in self.auth_providers.values()
        ):
            raise ValueError("auth provider with same kind/display_name already exists")
        self.auth_providers[record.provider_id] = record
        return record

    def get_auth_provider(
        self, *, org_id: str, provider_id: str
    ) -> AuthProviderRecord | None:
        record = self.auth_providers.get(provider_id)
        if record is None or record.org_id != org_id or record.deleted_at is not None:
            return None
        return record

    def get_auth_provider_by_id(self, provider_id: str) -> AuthProviderRecord | None:
        record = self.auth_providers.get(provider_id)
        if record is None or record.deleted_at is not None:
            return None
        return record

    def list_auth_providers(
        self, *, org_id: str, enabled_only: bool = False
    ) -> tuple[AuthProviderRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.auth_providers.values()
                    if record.org_id == org_id
                    and record.deleted_at is None
                    and (not enabled_only or record.enabled)
                ),
                key=lambda r: r.created_at,
            )
        )

    def update_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord:
        del conn
        if record.provider_id not in self.auth_providers:
            raise ValueError(f"auth provider not found: {record.provider_id}")
        updated = record.model_copy(update={"updated_at": _now()})
        self.auth_providers[record.provider_id] = updated
        return updated

    def delete_auth_provider(
        self, *, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        existing = self.auth_providers.get(provider_id)
        if (
            existing is None
            or existing.org_id != org_id
            or existing.deleted_at is not None
        ):
            return False
        self.auth_providers[provider_id] = existing.model_copy(
            update={"deleted_at": _now(), "enabled": False}
        )
        return True

    # Audit -------------------------------------------------------------
    def append_identity_audit(
        self, record: IdentityAuditEventRecord, *, conn: Any | None = None
    ) -> IdentityAuditEventRecord:
        del conn
        self.identity_audit_events.append(record)
        return record

    def list_identity_audit(
        self, *, org_id: str, limit: int = 100
    ) -> tuple[IdentityAuditEventRecord, ...]:
        rows = sorted(
            (r for r in self.identity_audit_events if r.org_id == org_id),
            key=lambda r: r.created_at,
            reverse=True,
        )
        return tuple(rows[:limit])

    # Login attempts ----------------------------------------------------
    def append_login_attempt(
        self, record: LoginAttemptRecord, *, conn: Any | None = None
    ) -> LoginAttemptRecord:
        del conn
        self.login_attempts.append(record)
        return record

    def list_login_attempts(
        self,
        *,
        org_id: str | None,
        email: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LoginAttemptRecord, ...]:
        normalized_email = email.strip().lower() if email else None
        rows = [
            r
            for r in self.login_attempts
            if (org_id is None or r.org_id == org_id)
            and (normalized_email is None or r.email_attempted == normalized_email)
            and (user_id is None or r.user_id == user_id)
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return tuple(rows[:limit])

    # Identity policy ---------------------------------------------------
    def get_identity_policy(self, *, org_id: str) -> IdentityPolicyRecord | None:
        return self.identity_policies.get(org_id)

    def upsert_identity_policy(
        self, record: IdentityPolicyRecord, *, conn: Any | None = None
    ) -> IdentityPolicyRecord:
        del conn
        updated = record.model_copy(update={"updated_at": _now()})
        self.identity_policies[record.org_id] = updated
        _log_write("identity_policies", record.org_id, "upsert")
        return updated


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresIdentityStore:
    """Postgres-backed identity store. Uses the shared connection pool.

    Methods accept an optional ``conn`` so the service layer can wrap a
    primary write + audit append in one transaction (matching the existing
    pattern in PostgresMcpStore / PostgresSkillStore).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    # Helpers -----------------------------------------------------------
    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    @staticmethod
    def _maybe(value: Any) -> Any:
        return value

    # Organizations -----------------------------------------------------
    def create_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO organizations (
                    org_id, display_name, slug, deployment_kind, status,
                    metadata, created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.org_id,
                    record.display_name,
                    record.slug,
                    record.deployment_kind.value,
                    record.status.value,
                    json.dumps(record.metadata),
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        _log_write("organizations", record.org_id, "insert")
        return record

    def get_organization(self, *, org_id: str) -> OrganizationRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM organizations WHERE org_id = %s AND deleted_at IS NULL",
                (org_id,),
            )
            row = cur.fetchone()
        return _row_to_org(row) if row else None

    def get_organization_by_slug(self, *, slug: str) -> OrganizationRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM organizations WHERE slug = %s AND deleted_at IS NULL",
                (slug,),
            )
            row = cur.fetchone()
        return _row_to_org(row) if row else None

    def update_organization(
        self, record: OrganizationRecord, *, conn: Any | None = None
    ) -> OrganizationRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE organizations SET
                    display_name = %s, slug = %s, deployment_kind = %s,
                    status = %s, metadata = %s, updated_at = %s
                WHERE org_id = %s AND deleted_at IS NULL
                """,
                (
                    updated.display_name,
                    updated.slug,
                    updated.deployment_kind.value,
                    updated.status.value,
                    json.dumps(updated.metadata),
                    updated.updated_at,
                    updated.org_id,
                ),
            )
        _log_write("organizations", record.org_id, "update")
        return updated

    def delete_organization(self, *, org_id: str, conn: Any | None = None) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE organizations
                SET deleted_at = %s, status = 'deleted', updated_at = %s
                WHERE org_id = %s AND deleted_at IS NULL
                """,
                (_now(), _now(), org_id),
            )
            count = cur.rowcount
        if count:
            _log_write("organizations", org_id, "delete")
        return bool(count)

    # Users -------------------------------------------------------------
    def create_user(self, record: UserRecord, *, conn: Any | None = None) -> UserRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO users (
                    user_id, org_id, primary_email, email_verified_at,
                    display_name, status, is_service_account, last_seen_at,
                    metadata, created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.user_id,
                    record.org_id,
                    record.primary_email,
                    record.email_verified_at,
                    record.display_name,
                    record.status.value,
                    record.is_service_account,
                    record.last_seen_at,
                    json.dumps(record.metadata),
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        _log_write("users", record.org_id, "insert")
        return record

    def get_user(
        self,
        *,
        org_id: str,
        user_id: str,
        include_deleted: bool = False,
    ) -> UserRecord | None:
        sql = "SELECT * FROM users WHERE user_id = %s AND org_id = %s"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._cursor(None) as cur:
            cur.execute(sql, (user_id, org_id))
            row = cur.fetchone()
        return _row_to_user(row) if row else None

    def get_user_by_email(self, *, org_id: str, email: str) -> UserRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM users
                WHERE org_id = %s AND lower(primary_email) = lower(%s)
                  AND deleted_at IS NULL
                """,
                (org_id, email),
            )
            row = cur.fetchone()
        return _row_to_user(row) if row else None

    def list_users(
        self, *, org_id: str, include_deleted: bool = False
    ) -> tuple[UserRecord, ...]:
        sql = "SELECT * FROM users WHERE org_id = %s"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY created_at"
        with self._cursor(None) as cur:
            cur.execute(sql, (org_id,))
            rows = cur.fetchall()
        return tuple(_row_to_user(row) for row in rows)

    def update_user(self, record: UserRecord, *, conn: Any | None = None) -> UserRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE users SET
                    primary_email = %s, email_verified_at = %s, display_name = %s,
                    status = %s, is_service_account = %s, last_seen_at = %s,
                    metadata = %s, updated_at = %s
                WHERE user_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (
                    updated.primary_email,
                    updated.email_verified_at,
                    updated.display_name,
                    updated.status.value,
                    updated.is_service_account,
                    updated.last_seen_at,
                    json.dumps(updated.metadata),
                    updated.updated_at,
                    updated.user_id,
                    updated.org_id,
                ),
            )
        _log_write("users", record.org_id, "update")
        return updated

    def delete_user(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE users SET
                    deleted_at = %s, status = 'disabled', updated_at = %s
                WHERE user_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (_now(), _now(), user_id, org_id),
            )
            count = cur.rowcount
        if count:
            _log_write("users", org_id, "delete")
        return bool(count)

    # Members -----------------------------------------------------------
    def add_member(
        self, record: OrganizationMemberRecord, *, conn: Any | None = None
    ) -> OrganizationMemberRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO organization_members (
                    member_id, org_id, user_id, joined_at,
                    invited_by_user_id, removed_at, source
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.member_id,
                    record.org_id,
                    record.user_id,
                    record.joined_at,
                    record.invited_by_user_id,
                    record.removed_at,
                    record.source.value,
                ),
            )
        return record

    def remove_member(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE organization_members SET removed_at = %s
                WHERE org_id = %s AND user_id = %s AND removed_at IS NULL
                """,
                (_now(), org_id, user_id),
            )
            return bool(cur.rowcount)

    def list_members(self, *, org_id: str) -> tuple[OrganizationMemberRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM organization_members
                WHERE org_id = %s AND removed_at IS NULL
                ORDER BY joined_at
                """,
                (org_id,),
            )
            rows = cur.fetchall()
        return tuple(_row_to_member(row) for row in rows)

    # Roles -------------------------------------------------------------
    def create_role(self, record: RoleRecord, *, conn: Any | None = None) -> RoleRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO roles (
                    role_id, org_id, name, display_name, description,
                    is_system, permission_scopes, created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.role_id,
                    record.org_id,
                    record.name,
                    record.display_name,
                    record.description,
                    record.is_system,
                    json.dumps(list(record.permission_scopes)),
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        return record

    def get_role(self, *, role_id: str) -> RoleRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM roles WHERE role_id = %s AND deleted_at IS NULL",
                (role_id,),
            )
            row = cur.fetchone()
        return _row_to_role(row) if row else None

    def get_role_by_name(self, *, org_id: str | None, name: str) -> RoleRecord | None:
        with self._cursor(None) as cur:
            if org_id is None:
                cur.execute(
                    """
                    SELECT * FROM roles
                    WHERE org_id IS NULL AND name = %s AND deleted_at IS NULL
                    """,
                    (name,),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM roles
                    WHERE org_id = %s AND name = %s AND deleted_at IS NULL
                    """,
                    (org_id, name),
                )
            row = cur.fetchone()
        return _row_to_role(row) if row else None

    def list_roles(self, *, org_id: str | None) -> tuple[RoleRecord, ...]:
        with self._cursor(None) as cur:
            if org_id is None:
                cur.execute(
                    """
                    SELECT * FROM roles
                    WHERE org_id IS NULL AND deleted_at IS NULL
                    ORDER BY name
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM roles
                    WHERE org_id = %s AND deleted_at IS NULL
                    ORDER BY name
                    """,
                    (org_id,),
                )
            rows = cur.fetchall()
        return tuple(_row_to_role(row) for row in rows)

    def update_role(self, record: RoleRecord, *, conn: Any | None = None) -> RoleRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE roles SET
                    name = %s, display_name = %s, description = %s,
                    permission_scopes = %s, updated_at = %s
                WHERE role_id = %s AND deleted_at IS NULL
                """,
                (
                    updated.name,
                    updated.display_name,
                    updated.description,
                    json.dumps(list(updated.permission_scopes)),
                    updated.updated_at,
                    updated.role_id,
                ),
            )
        return updated

    def delete_role(self, *, role_id: str, conn: Any | None = None) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE roles SET deleted_at = %s, updated_at = %s
                WHERE role_id = %s AND deleted_at IS NULL AND is_system = FALSE
                """,
                (_now(), _now(), role_id),
            )
            return bool(cur.rowcount)

    # Role assignments --------------------------------------------------
    def assign_role(
        self, record: RoleAssignmentRecord, *, conn: Any | None = None
    ) -> RoleAssignmentRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO role_assignments (
                    assignment_id, org_id, user_id, role_id,
                    granted_by_user_id, granted_at, revoked_at, reason
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.assignment_id,
                    record.org_id,
                    record.user_id,
                    record.role_id,
                    record.granted_by_user_id,
                    record.granted_at,
                    record.revoked_at,
                    record.reason,
                ),
            )
        return record

    def revoke_role(
        self,
        *,
        org_id: str,
        user_id: str,
        role_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE role_assignments SET revoked_at = %s, reason = %s
                WHERE org_id = %s AND user_id = %s AND role_id = %s
                  AND revoked_at IS NULL
                """,
                (_now(), reason, org_id, user_id, role_id),
            )
            return bool(cur.rowcount)

    def list_role_assignments(
        self, *, org_id: str, user_id: str
    ) -> tuple[RoleAssignmentRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM role_assignments
                WHERE org_id = %s AND user_id = %s AND revoked_at IS NULL
                ORDER BY granted_at
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(_row_to_role_assignment(row) for row in rows)

    # Auth providers ----------------------------------------------------
    def create_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO auth_providers (
                    provider_id, org_id, kind, display_name, enabled,
                    config, encrypted_client_secret,
                    created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.provider_id,
                    record.org_id,
                    record.kind.value,
                    record.display_name,
                    record.enabled,
                    json.dumps(record.config),
                    record.encrypted_client_secret,
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        return record

    def get_auth_provider(
        self, *, org_id: str, provider_id: str
    ) -> AuthProviderRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM auth_providers
                WHERE provider_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (provider_id, org_id),
            )
            row = cur.fetchone()
        return _row_to_auth_provider(row) if row else None

    def get_auth_provider_by_id(self, provider_id: str) -> AuthProviderRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM auth_providers
                WHERE provider_id = %s AND deleted_at IS NULL
                """,
                (provider_id,),
            )
            row = cur.fetchone()
        return _row_to_auth_provider(row) if row else None

    def list_auth_providers(
        self, *, org_id: str, enabled_only: bool = False
    ) -> tuple[AuthProviderRecord, ...]:
        sql = """
            SELECT * FROM auth_providers
            WHERE org_id = %s AND deleted_at IS NULL
        """
        if enabled_only:
            sql += " AND enabled = TRUE"
        sql += " ORDER BY created_at"
        with self._cursor(None) as cur:
            cur.execute(sql, (org_id,))
            rows = cur.fetchall()
        return tuple(_row_to_auth_provider(row) for row in rows)

    def update_auth_provider(
        self, record: AuthProviderRecord, *, conn: Any | None = None
    ) -> AuthProviderRecord:
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE auth_providers SET
                    display_name = %s, enabled = %s, config = %s,
                    encrypted_client_secret = %s, updated_at = %s
                WHERE provider_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (
                    updated.display_name,
                    updated.enabled,
                    json.dumps(updated.config),
                    updated.encrypted_client_secret,
                    updated.updated_at,
                    updated.provider_id,
                    updated.org_id,
                ),
            )
        return updated

    def delete_auth_provider(
        self, *, org_id: str, provider_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE auth_providers SET
                    deleted_at = %s, enabled = FALSE, updated_at = %s
                WHERE provider_id = %s AND org_id = %s AND deleted_at IS NULL
                """,
                (_now(), _now(), provider_id, org_id),
            )
            return bool(cur.rowcount)

    # Audit -------------------------------------------------------------
    def append_identity_audit(
        self, record: IdentityAuditEventRecord, *, conn: Any | None = None
    ) -> IdentityAuditEventRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO identity_audit_events (
                    audit_id, org_id, actor_user_id, subject_user_id,
                    action, metadata, request_ip, user_agent, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.audit_id,
                    record.org_id,
                    record.actor_user_id,
                    record.subject_user_id,
                    record.action,
                    json.dumps(record.metadata),
                    record.request_ip,
                    record.user_agent,
                    record.created_at,
                ),
            )
        return record

    def list_identity_audit(
        self, *, org_id: str, limit: int = 100
    ) -> tuple[IdentityAuditEventRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM identity_audit_events
                WHERE org_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (org_id, limit),
            )
            rows = cur.fetchall()
        return tuple(_row_to_identity_audit(row) for row in rows)

    # Login attempts ----------------------------------------------------
    def append_login_attempt(
        self, record: LoginAttemptRecord, *, conn: Any | None = None
    ) -> LoginAttemptRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO login_attempts (
                    attempt_id, org_id, email_attempted, user_id,
                    auth_kind, outcome, ip, user_agent, failure_reason, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.attempt_id,
                    record.org_id,
                    record.email_attempted,
                    record.user_id,
                    record.auth_kind.value,
                    record.outcome.value,
                    record.ip,
                    record.user_agent,
                    record.failure_reason,
                    record.created_at,
                ),
            )
        return record

    def list_login_attempts(
        self,
        *,
        org_id: str | None,
        email: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LoginAttemptRecord, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if org_id is not None:
            clauses.append("org_id = %s")
            params.append(org_id)
        if email is not None:
            clauses.append("lower(email_attempted) = lower(%s)")
            params.append(email)
        if user_id is not None:
            clauses.append("user_id = %s")
            params.append(user_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM login_attempts"
            + where
            + " ORDER BY created_at DESC LIMIT %s"
        )
        params.append(limit)
        with self._cursor(None) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return tuple(_row_to_login_attempt(row) for row in rows)

    # Identity policy ---------------------------------------------------
    def get_identity_policy(self, *, org_id: str) -> IdentityPolicyRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT org_id, local_password_enabled, mfa_required, "
                "step_up_window_seconds, updated_at "
                "FROM identity_policies WHERE org_id = %s",
                (org_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return IdentityPolicyRecord.model_validate(dict(row))

    def upsert_identity_policy(
        self, record: IdentityPolicyRecord, *, conn: Any | None = None
    ) -> IdentityPolicyRecord:
        # Atomic insert-or-update keyed on PK; ``updated_at`` always refreshed
        # on conflict so callers can rely on the column.
        updated = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO identity_policies (
                    org_id, local_password_enabled, mfa_required,
                    step_up_window_seconds, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (org_id) DO UPDATE SET
                    local_password_enabled = EXCLUDED.local_password_enabled,
                    mfa_required = EXCLUDED.mfa_required,
                    step_up_window_seconds = EXCLUDED.step_up_window_seconds,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    updated.org_id,
                    updated.local_password_enabled,
                    updated.mfa_required,
                    updated.step_up_window_seconds,
                    updated.updated_at,
                ),
            )
        return updated


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------


def _row_to_org(row: dict[str, Any]) -> OrganizationRecord:
    return OrganizationRecord.model_validate(
        {**row, "metadata": _coerce_json(row.get("metadata"))}
    )


def _row_to_user(row: dict[str, Any]) -> UserRecord:
    return UserRecord.model_validate(
        {**row, "metadata": _coerce_json(row.get("metadata"))}
    )


def _row_to_member(row: dict[str, Any]) -> OrganizationMemberRecord:
    return OrganizationMemberRecord.model_validate(row)


def _row_to_role(row: dict[str, Any]) -> RoleRecord:
    return RoleRecord.model_validate(
        {
            **row,
            "permission_scopes": tuple(
                _coerce_json(row.get("permission_scopes")) or ()
            ),
        }
    )


def _row_to_role_assignment(row: dict[str, Any]) -> RoleAssignmentRecord:
    return RoleAssignmentRecord.model_validate(row)


def _row_to_auth_provider(row: dict[str, Any]) -> AuthProviderRecord:
    return AuthProviderRecord.model_validate(
        {**row, "config": _coerce_json(row.get("config"))}
    )


def _row_to_identity_audit(row: dict[str, Any]) -> IdentityAuditEventRecord:
    return IdentityAuditEventRecord.model_validate(
        {**row, "metadata": _coerce_json(row.get("metadata"))}
    )


def _row_to_login_attempt(row: dict[str, Any]) -> LoginAttemptRecord:
    return LoginAttemptRecord.model_validate(row)


def _coerce_json(value: Any) -> Any:
    """psycopg returns JSONB as native objects; tolerate strings too for safety."""

    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else {}


def _log_write(table: str, org_id: str, op: str) -> None:
    _LOGGER.info("identity_write table=%s org_id=%s op=%s", table, org_id, op)
