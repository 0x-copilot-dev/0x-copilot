"""Settings service layer — ACL, audit, deep-merge.

The store treats namespaces as opaque JSON. Authorization, audit, and
the shape of "what the FE sees on a fresh row" live here.

ACL (sub-PRD §6.4):

  * User namespace: owner only. The caller must be the user_id on the
    row. Audit row written on every PATCH.
  * Tenant namespace: admin only — caller must hold the ``admin:users``
    permission scope (the RBAC scope that gates every other workspace
    setting today). Audit row written on every PATCH.

Audit metadata: ``before_keys`` / ``after_keys`` / ``diff_paths`` so a
compliance reviewer can answer "who changed what, when". The same
shape Phase 4.1 ``user.preferences.update`` audit uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.store import IdentityStore
from backend_app.settings.store import (
    NamespaceRecord,
    SettingsStore,
    TENANT_NAMESPACES,
    USER_NAMESPACES,
)


class SettingsAccessDenied(Exception):
    """Raised when the caller is not permitted to read/write the namespace."""


class SettingsInvalidNamespace(Exception):
    """Raised when the requested namespace is not known to this service."""


@dataclass(frozen=True)
class CallerIdentity:
    """The verified caller, projected to just the fields the service uses."""

    org_id: str
    user_id: str
    roles: tuple[str, ...]
    permission_scopes: tuple[str, ...]

    @property
    def is_admin(self) -> bool:
        """Treat ``admin:users`` (the workspace-admin scope) OR the
        ``admin`` / ``owner`` role as admin-permitted.

        Roles are coarse-grained; permission scopes are the
        authoritative gate. Either path lets workspace-defaults flow.
        """

        if "admin:users" in self.permission_scopes:
            return True
        return any(role in {"admin", "owner"} for role in self.roles)


class SettingsService:
    """Authorize + deep-merge + audit. Storage stays opaque."""

    def __init__(
        self,
        *,
        store: SettingsStore,
        identity_store: IdentityStore,
    ) -> None:
        self._store = store
        self._identity_store = identity_store

    # ------------------------------------------------------------------
    # User namespace (owner-only)
    # ------------------------------------------------------------------

    def get_user_namespace(
        self,
        *,
        caller: CallerIdentity,
        target_user_id: str,
        namespace: str,
    ) -> NamespaceRecord | None:
        self._require_known_user_namespace(namespace)
        self._require_owner(caller=caller, target_user_id=target_user_id)
        return self._store.get_user_namespace(
            org_id=caller.org_id,
            user_id=target_user_id,
            namespace=namespace,
        )

    def patch_user_namespace(
        self,
        *,
        caller: CallerIdentity,
        target_user_id: str,
        namespace: str,
        patch: dict[str, Any],
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> NamespaceRecord:
        self._require_known_user_namespace(namespace)
        self._require_owner(caller=caller, target_user_id=target_user_id)

        before = self._store.get_user_namespace(
            org_id=caller.org_id,
            user_id=target_user_id,
            namespace=namespace,
        )
        before_keys = sorted(before.settings.keys()) if before is not None else []

        with self._store.transaction() as conn:
            saved = self._store.patch_user_namespace(
                org_id=caller.org_id,
                user_id=target_user_id,
                namespace=namespace,
                patch=patch,
                conn=conn,
            )
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=caller.org_id,
                    actor_user_id=caller.user_id,
                    subject_user_id=target_user_id,
                    action=f"settings.user.{namespace}.update",
                    metadata={
                        "namespace": namespace,
                        "before_keys": before_keys,
                        "after_keys": sorted(saved.settings.keys()),
                        "diff_paths": sorted(_paths(patch)),
                    },
                    request_ip=request_ip,
                    user_agent=user_agent,
                ),
                conn=conn,
            )
        return saved

    # ------------------------------------------------------------------
    # Tenant namespace (admin-only)
    # ------------------------------------------------------------------

    def get_tenant_namespace(
        self,
        *,
        caller: CallerIdentity,
        namespace: str,
    ) -> NamespaceRecord | None:
        self._require_known_tenant_namespace(namespace)
        self._require_admin(caller=caller)
        return self._store.get_tenant_namespace(
            tenant_id=caller.org_id,
            namespace=namespace,
        )

    def patch_tenant_namespace(
        self,
        *,
        caller: CallerIdentity,
        namespace: str,
        patch: dict[str, Any],
        request_ip: str | None = None,
        user_agent: str | None = None,
    ) -> NamespaceRecord:
        self._require_known_tenant_namespace(namespace)
        self._require_admin(caller=caller)

        before = self._store.get_tenant_namespace(
            tenant_id=caller.org_id,
            namespace=namespace,
        )
        before_keys = sorted(before.settings.keys()) if before is not None else []

        with self._store.transaction() as conn:
            saved = self._store.patch_tenant_namespace(
                tenant_id=caller.org_id,
                namespace=namespace,
                patch=patch,
                actor_user_id=caller.user_id,
                conn=conn,
            )
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=caller.org_id,
                    actor_user_id=caller.user_id,
                    action=f"settings.workspace.{namespace}.update",
                    metadata={
                        "namespace": namespace,
                        "before_keys": before_keys,
                        "after_keys": sorted(saved.settings.keys()),
                        "diff_paths": sorted(_paths(patch)),
                    },
                    request_ip=request_ip,
                    user_agent=user_agent,
                ),
                conn=conn,
            )
        return saved

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _require_known_user_namespace(namespace: str) -> None:
        if namespace not in USER_NAMESPACES:
            raise SettingsInvalidNamespace(namespace)

    @staticmethod
    def _require_known_tenant_namespace(namespace: str) -> None:
        if namespace not in TENANT_NAMESPACES:
            raise SettingsInvalidNamespace(namespace)

    @staticmethod
    def _require_owner(*, caller: CallerIdentity, target_user_id: str) -> None:
        if caller.user_id != target_user_id:
            raise SettingsAccessDenied("User-scoped settings are owner-only.")

    @staticmethod
    def _require_admin(*, caller: CallerIdentity) -> None:
        if not caller.is_admin:
            raise SettingsAccessDenied("Workspace-scoped settings require admin.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _paths(value: Any, prefix: str = "") -> list[str]:
    """Flatten a partial-update dict into dotted paths for audit metadata.

    Mirrors ``backend_app.routes.me_preferences._paths`` so audit
    consumers see the same shape across both surfaces.
    """

    if not isinstance(value, dict):
        return [prefix] if prefix else []
    result: list[str] = []
    for key, child in value.items():
        next_prefix = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.extend(_paths(child, next_prefix))
        else:
            result.append(next_prefix)
    return result


__all__ = [
    "CallerIdentity",
    "SettingsAccessDenied",
    "SettingsInvalidNamespace",
    "SettingsService",
]
