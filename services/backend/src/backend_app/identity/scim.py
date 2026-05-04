"""SCIM 2.0 service (A7): User CRUD, Group CRUD + role sync, token mint/revoke.

The route layer (``routes/scim.py`` + facade) talks to this service in
internal-record terms; the SCIM JSON serialization happens at the route
boundary via :mod:`backend_app.identity.scim_serializer`. Keeping JSON
parsing out of the service makes the unit tests pin behavior — not
wire shape — and lets the same service back both the public ``/scim/v2/*``
surface and any future admin CLI.

The service is the only thing in the codebase that:

- Mints SCIM bearer tokens (returns plaintext exactly once).
- Validates a presented bearer token to resolve ``(org_id, provider_id)``.
- Applies SCIM JSON-Patch operations against a user.
- Soft-deletes a user via ``active=false`` and reactivates via ``true``.
- Syncs group membership → role assignments when ``mapped_role_id`` is set.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    RoleAssignmentRecord,
    ScimExternalIdRecord,
    ScimGroupMemberRecord,
    ScimGroupRecord,
    ScimTokenMintResult,
    ScimTokenRecord,
    UserRecord,
    UserStatus,
)
from backend_app.identity.scim_filter import (
    ScimFilterError,
    filter_matches,
    parse_filter,
)
from backend_app.identity.scim_store import ScimStore
from backend_app.identity.store import IdentityStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors → mapped to SCIM HTTP status codes by the route layer.
# ---------------------------------------------------------------------------


class ScimError(RuntimeError):
    """Base for SCIM service errors."""

    status_code = 400
    scim_type: str | None = None


class ScimAuthError(ScimError):
    status_code = 401


class ScimNotFound(ScimError):
    status_code = 404


class ScimConflict(ScimError):
    status_code = 409
    scim_type = "uniqueness"


class ScimBadRequest(ScimError):
    status_code = 400
    scim_type = "invalidValue"


class ScimUnsupportedFilter(ScimError):
    status_code = 400
    scim_type = "invalidFilter"


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedScimToken:
    """Result of validating a presented SCIM bearer token."""

    token: ScimTokenRecord
    provider: AuthProviderRecord


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class ScimService:
    identity_store: IdentityStore
    scim_store: ScimStore

    # ----- Token mint / list / revoke / resolve --------------------------
    def mint_token(
        self,
        *,
        org_id: str,
        provider_id: str,
        created_by_user_id: str,
        expires_at: datetime | None = None,
    ) -> ScimTokenMintResult:
        provider = self._require_provider(org_id=org_id, provider_id=provider_id)
        plaintext = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        token_prefix = plaintext[:8]
        record = ScimTokenRecord(
            org_id=provider.org_id,
            provider_id=provider.provider_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            created_by_user_id=created_by_user_id,
            expires_at=expires_at,
        )
        self.scim_store.create_token(record)
        self._audit(
            provider=provider,
            action="scim.token.minted",
            metadata={"token_id": record.token_id, "prefix": token_prefix},
            actor_user_id=created_by_user_id,
        )
        return ScimTokenMintResult(
            token_id=record.token_id,
            plaintext=plaintext,
            token_prefix=token_prefix,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    def list_tokens(
        self, *, org_id: str, provider_id: str
    ) -> tuple[ScimTokenRecord, ...]:
        return self.scim_store.list_tokens(org_id=org_id, provider_id=provider_id)

    def revoke_token(self, *, org_id: str, provider_id: str, token_id: str) -> bool:
        provider = self._require_provider(org_id=org_id, provider_id=provider_id)
        existing = next(
            (
                t
                for t in self.list_tokens(org_id=org_id, provider_id=provider_id)
                if t.token_id == token_id
            ),
            None,
        )
        if existing is None:
            return False
        ok = self.scim_store.revoke_token(token_id=token_id)
        if ok:
            self._audit(
                provider=provider,
                action="scim.token.revoked",
                metadata={"token_id": token_id, "prefix": existing.token_prefix},
            )
        return ok

    def resolve_token(self, plaintext: str) -> ResolvedScimToken:
        if not plaintext:
            raise ScimAuthError("missing SCIM bearer token")
        token_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        record = self.scim_store.get_token_by_hash(token_hash=token_hash)
        if record is None:
            self._record_login_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                failure_reason="unknown SCIM token",
            )
            raise ScimAuthError("invalid SCIM bearer token")
        if record.revoked_at is not None:
            raise ScimAuthError("SCIM token revoked")
        if record.expires_at is not None and record.expires_at <= _now():
            raise ScimAuthError("SCIM token expired")
        provider = self.identity_store.get_auth_provider(
            org_id=record.org_id, provider_id=record.provider_id
        )
        if provider is None or provider.kind is not AuthProviderKind.SCIM:
            raise ScimAuthError("SCIM token references non-SCIM provider")
        # Best-effort touch — failure here doesn't block the request.
        self.scim_store.touch_token_last_used(token_id=record.token_id)
        return ResolvedScimToken(token=record, provider=provider)

    # ----- User CRUD -----------------------------------------------------
    def create_user(
        self,
        *,
        token: ResolvedScimToken,
        user_name: str,
        display_name: str | None,
        external_id: str | None,
        active: bool = True,
    ) -> tuple[UserRecord, ScimExternalIdRecord | None]:
        if not user_name:
            raise ScimBadRequest("userName is required")
        existing = self.identity_store.get_user_by_email(
            org_id=token.provider.org_id, email=user_name
        )
        if existing is not None and existing.deleted_at is None:
            raise ScimConflict(f"user already exists with userName={user_name}")

        with self.identity_store.transaction():
            user = self.identity_store.create_user(
                UserRecord(
                    org_id=token.provider.org_id,
                    primary_email=user_name,
                    display_name=display_name or user_name,
                    status=UserStatus.ACTIVE if active else UserStatus.DISABLED,
                )
            )
            self.identity_store.add_member(
                OrganizationMemberRecord(
                    org_id=token.provider.org_id,
                    user_id=user.user_id,
                    source=OrganizationMemberSource.SCIM,
                )
            )
            if not active:
                user = self.identity_store.update_user(
                    user.model_copy(update={"deleted_at": _now()})
                )

        mapping: ScimExternalIdRecord | None = None
        if external_id:
            mapping = self.scim_store.create_external_id(
                ScimExternalIdRecord(
                    org_id=token.provider.org_id,
                    user_id=user.user_id,
                    provider_id=token.provider.provider_id,
                    external_id=external_id,
                )
            )

        self._audit(
            provider=token.provider,
            action="scim.user.created",
            metadata={
                "user_id": user.user_id,
                "user_name": user_name,
                "external_id": external_id,
            },
        )
        return user, mapping

    def get_user(self, *, token: ResolvedScimToken, user_id: str) -> UserRecord:
        user = self._require_user(token=token, user_id=user_id)
        return user

    def list_users(
        self,
        *,
        token: ResolvedScimToken,
        filter_expr: str | None,
        start_index: int,
        count: int,
    ) -> tuple[tuple[UserRecord, ...], int]:
        try:
            parsed = parse_filter(filter_expr)
        except ScimFilterError as exc:
            raise ScimUnsupportedFilter(str(exc)) from exc

        # SCIM: include soft-deleted so the IdP can query
        # `active eq false` and reactivate. Deactivated state is
        # surfaced via the projected `active` attribute below.
        all_users = self.identity_store.list_users(
            org_id=token.provider.org_id, include_deleted=True
        )
        filtered: list[UserRecord] = []
        for user in all_users:
            attributes = {
                "userName": user.primary_email,
                "displayName": user.display_name,
                "active": user.deleted_at is None and user.status is UserStatus.ACTIVE,
            }
            if filter_matches(parsed, attributes):
                filtered.append(user)

        # SCIM uses 1-based indexing.
        start = max(1, start_index) - 1
        page = tuple(filtered[start : start + max(0, count)])
        return page, len(filtered)

    def replace_user(
        self,
        *,
        token: ResolvedScimToken,
        user_id: str,
        user_name: str | None,
        display_name: str | None,
        active: bool | None,
    ) -> UserRecord:
        user = self._require_user(token=token, user_id=user_id)
        updates: dict[str, Any] = {}
        if user_name is not None:
            updates["primary_email"] = user_name
        if display_name is not None:
            updates["display_name"] = display_name
        if active is not None:
            updates["deleted_at"] = None if active else _now()
            updates["status"] = UserStatus.ACTIVE if active else UserStatus.DISABLED
        if not updates:
            return user
        # Prevent userName collision under PUT.
        if (
            "primary_email" in updates
            and updates["primary_email"] != user.primary_email
        ):
            other = self.identity_store.get_user_by_email(
                org_id=token.provider.org_id, email=updates["primary_email"]
            )
            if other is not None and other.user_id != user.user_id:
                raise ScimConflict(
                    f"user already exists with userName={updates['primary_email']}"
                )
        result = self.identity_store.update_user(user.model_copy(update=updates))
        self._audit(
            provider=token.provider,
            action="scim.user.updated",
            metadata={
                "user_id": user_id,
                "fields": sorted(updates.keys()),
            },
        )
        return result

    def patch_user(
        self,
        *,
        token: ResolvedScimToken,
        user_id: str,
        operations: Iterable[Mapping[str, Any]],
    ) -> UserRecord:
        user = self._require_user(token=token, user_id=user_id)
        updates: dict[str, Any] = {}
        for op in operations:
            verb = str(op.get("op", "")).lower()
            path = str(op.get("path", ""))
            value = op.get("value")
            if verb not in {"add", "replace", "remove"}:
                raise ScimBadRequest(f"unsupported PATCH op: {verb!r}")
            if path == "active":
                if verb == "remove":
                    raise ScimBadRequest("cannot remove 'active'")
                if not isinstance(value, bool):
                    raise ScimBadRequest("'active' value must be boolean")
                updates["deleted_at"] = None if value else _now()
                updates["status"] = UserStatus.ACTIVE if value else UserStatus.DISABLED
            elif path == "displayName":
                if verb == "remove":
                    raise ScimBadRequest("displayName cannot be removed")
                if not isinstance(value, str):
                    raise ScimBadRequest("displayName value must be string")
                updates["display_name"] = value
            elif path == "userName":
                if verb == "remove":
                    raise ScimBadRequest("userName cannot be removed")
                if not isinstance(value, str):
                    raise ScimBadRequest("userName value must be string")
                # Collision check.
                other = self.identity_store.get_user_by_email(
                    org_id=token.provider.org_id, email=value
                )
                if other is not None and other.user_id != user.user_id:
                    raise ScimConflict(f"user already exists with userName={value}")
                updates["primary_email"] = value
            else:
                # Unknown attributes are silently dropped per RFC 7644 §3.5.2;
                # log them via audit so an admin can see what the IdP sent.
                self._audit(
                    provider=token.provider,
                    action="scim.user.patch_unknown_attr",
                    metadata={
                        "user_id": user_id,
                        "path": path,
                        "op": verb,
                    },
                )
        if not updates:
            return user
        result = self.identity_store.update_user(user.model_copy(update=updates))
        if "deleted_at" in updates:
            audit_action = (
                "scim.user.deactivated"
                if updates["deleted_at"] is not None
                else "scim.user.reactivated"
            )
            self._audit(
                provider=token.provider,
                action=audit_action,
                metadata={"user_id": user_id},
            )
        else:
            self._audit(
                provider=token.provider,
                action="scim.user.updated",
                metadata={
                    "user_id": user_id,
                    "fields": sorted(updates.keys()),
                },
            )
        return result

    def delete_user(self, *, token: ResolvedScimToken, user_id: str) -> None:
        user = self._require_user(token=token, user_id=user_id)
        if user.deleted_at is not None:
            return
        self.identity_store.update_user(
            user.model_copy(
                update={"deleted_at": _now(), "status": UserStatus.DISABLED}
            )
        )
        self._audit(
            provider=token.provider,
            action="scim.user.deactivated",
            metadata={"user_id": user_id, "via": "DELETE"},
        )

    # ----- Group CRUD ----------------------------------------------------
    def create_group(
        self,
        *,
        token: ResolvedScimToken,
        display_name: str,
        external_id: str | None,
        member_user_ids: Iterable[str] = (),
        mapped_role_name: str | None = None,
    ) -> ScimGroupRecord:
        mapped_role_id: str | None = None
        if mapped_role_name:
            role = self.identity_store.get_role_by_name(
                org_id=token.provider.org_id, name=mapped_role_name
            )
            if role is None:
                role = self.identity_store.get_role_by_name(
                    org_id=None, name=mapped_role_name
                )
            if role is not None:
                mapped_role_id = role.role_id
        try:
            group = self.scim_store.create_group(
                ScimGroupRecord(
                    org_id=token.provider.org_id,
                    display_name=display_name,
                    external_id=external_id,
                    mapped_role_id=mapped_role_id,
                )
            )
        except ValueError as exc:
            raise ScimConflict(str(exc)) from exc
        self._audit(
            provider=token.provider,
            action="scim.group.created",
            metadata={
                "group_id": group.group_id,
                "display_name": display_name,
                "mapped_role_id": mapped_role_id,
            },
        )
        for user_id in member_user_ids:
            self.add_group_member(token=token, group_id=group.group_id, user_id=user_id)
        return group

    def get_group(self, *, token: ResolvedScimToken, group_id: str) -> ScimGroupRecord:
        group = self.scim_store.get_group(
            org_id=token.provider.org_id, group_id=group_id
        )
        if group is None:
            raise ScimNotFound(f"group {group_id} not found")
        return group

    def list_groups(
        self,
        *,
        token: ResolvedScimToken,
        filter_expr: str | None,
        start_index: int,
        count: int,
    ) -> tuple[tuple[ScimGroupRecord, ...], int]:
        try:
            parsed = parse_filter(filter_expr)
        except ScimFilterError as exc:
            raise ScimUnsupportedFilter(str(exc)) from exc

        all_groups = self.scim_store.list_groups(org_id=token.provider.org_id)
        filtered: list[ScimGroupRecord] = []
        for group in all_groups:
            attributes = {"displayName": group.display_name}
            if filter_matches(parsed, attributes):
                filtered.append(group)
        start = max(1, start_index) - 1
        page = tuple(filtered[start : start + max(0, count)])
        return page, len(filtered)

    def soft_delete_group(self, *, token: ResolvedScimToken, group_id: str) -> None:
        group = self.get_group(token=token, group_id=group_id)
        # Revoke any role assignments granted by this group's mapping.
        if group.mapped_role_id is not None:
            for member in self.scim_store.list_members(group_id=group_id):
                self._revoke_member_role(
                    org_id=token.provider.org_id,
                    user_id=member.user_id,
                    role_id=group.mapped_role_id,
                    reason=f"scim.group.deleted:{group_id}",
                )
                self.scim_store.remove_member(group_id=group_id, user_id=member.user_id)
        ok = self.scim_store.soft_delete_group(
            org_id=token.provider.org_id, group_id=group_id
        )
        if ok:
            self._audit(
                provider=token.provider,
                action="scim.group.deleted",
                metadata={"group_id": group_id},
            )

    def add_group_member(
        self, *, token: ResolvedScimToken, group_id: str, user_id: str
    ) -> ScimGroupMemberRecord:
        group = self.get_group(token=token, group_id=group_id)
        # Validate the user belongs to the same org.
        user = self.identity_store.get_user(
            org_id=token.provider.org_id, user_id=user_id
        )
        if user is None:
            raise ScimNotFound(f"user {user_id} not found")
        record = self.scim_store.add_member(
            ScimGroupMemberRecord(
                org_id=token.provider.org_id,
                group_id=group_id,
                user_id=user_id,
            )
        )
        if group.mapped_role_id is not None:
            self.identity_store.assign_role(
                RoleAssignmentRecord(
                    org_id=token.provider.org_id,
                    user_id=user_id,
                    role_id=group.mapped_role_id,
                )
            )
        self._audit(
            provider=token.provider,
            action="scim.group.member_added",
            metadata={"group_id": group_id, "user_id": user_id},
        )
        return record

    def remove_group_member(
        self, *, token: ResolvedScimToken, group_id: str, user_id: str
    ) -> bool:
        group = self.get_group(token=token, group_id=group_id)
        ok = self.scim_store.remove_member(group_id=group_id, user_id=user_id)
        if ok and group.mapped_role_id is not None:
            self._revoke_member_role(
                org_id=token.provider.org_id,
                user_id=user_id,
                role_id=group.mapped_role_id,
                reason=f"scim.group.member_removed:{group_id}",
            )
        if ok:
            self._audit(
                provider=token.provider,
                action="scim.group.member_removed",
                metadata={"group_id": group_id, "user_id": user_id},
            )
        return ok

    # ----- Lookup helpers exposed to the route layer ----------------------
    def get_user_external_id(
        self, *, token: ResolvedScimToken, user_id: str
    ) -> str | None:
        for mapping in self.scim_store.list_external_ids_for_user(user_id=user_id):
            if mapping.provider_id == token.provider.provider_id:
                return mapping.external_id
        return None

    def list_user_groups(
        self, *, token: ResolvedScimToken, user_id: str
    ) -> tuple[ScimGroupRecord, ...]:
        del token  # currently the same store across orgs; partition by membership
        return self.scim_store.list_active_groups_for_user(user_id=user_id)

    def list_group_members(
        self, *, token: ResolvedScimToken, group_id: str
    ) -> tuple[ScimGroupMemberRecord, ...]:
        del token
        return self.scim_store.list_members(group_id=group_id)

    # ----- Internals -----------------------------------------------------
    def _require_provider(self, *, org_id: str, provider_id: str) -> AuthProviderRecord:
        provider = self.identity_store.get_auth_provider(
            org_id=org_id, provider_id=provider_id
        )
        if provider is None:
            raise ScimNotFound(f"no SCIM provider {provider_id} for org {org_id}")
        if provider.kind is not AuthProviderKind.SCIM:
            raise ScimBadRequest(f"provider {provider_id} is not a SCIM provider")
        return provider

    def _require_user(self, *, token: ResolvedScimToken, user_id: str) -> UserRecord:
        # SCIM PATCH active=true must be able to reactivate a soft-deleted
        # user, so include_deleted=True here. The IdP is the source of truth
        # for active state — we expose every row it provisioned regardless
        # of local soft-delete state.
        user = self.identity_store.get_user(
            org_id=token.provider.org_id,
            user_id=user_id,
            include_deleted=True,
        )
        if user is None:
            raise ScimNotFound(f"user {user_id} not found")
        return user

    def _revoke_member_role(
        self, *, org_id: str, user_id: str, role_id: str, reason: str
    ) -> None:
        for assignment in self.identity_store.list_role_assignments(
            org_id=org_id, user_id=user_id
        ):
            if assignment.role_id == role_id and assignment.revoked_at is None:
                self.identity_store.revoke_role(
                    org_id=org_id,
                    user_id=user_id,
                    role_id=role_id,
                    reason=reason,
                )
                return

    def _audit(
        self,
        *,
        provider: AuthProviderRecord,
        action: str,
        metadata: Mapping[str, Any],
        actor_user_id: str | None = None,
    ) -> None:
        self.identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=provider.org_id,
                actor_user_id=actor_user_id,
                action=action,
                metadata={
                    **dict(metadata),
                    "provider_id": provider.provider_id,
                    "provider_kind": provider.kind.value,
                },
            )
        )

    def _record_login_attempt(
        self,
        *,
        org_id: str | None,
        outcome: LoginAttemptOutcome,
        failure_reason: str | None = None,
    ) -> None:
        self.identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                auth_kind=LoginAttemptKind.SCIM_TOKEN,
                outcome=outcome,
                failure_reason=failure_reason,
            )
        )


__all__ = [
    "ResolvedScimToken",
    "ScimAuthError",
    "ScimBadRequest",
    "ScimConflict",
    "ScimError",
    "ScimNotFound",
    "ScimService",
    "ScimUnsupportedFilter",
]
