"""Tests for ``backend_app.settings.service.SettingsService``.

Coverage (sub-PRD §6.4):

* Owner-only ACL on user namespaces — non-owner reads/writes raise
  ``SettingsAccessDenied``.
* Admin-only ACL on tenant namespaces — non-admin reads/writes raise
  ``SettingsAccessDenied``. Either ``admin:users`` permission scope or
  the coarse-grained ``admin`` role grants admin.
* Audit row appended on every PATCH (both user and tenant).
* Deep-merge preserves untouched namespaces in user_preferences (the
  existing ``home.*`` block survives a notifications PATCH).
"""

from __future__ import annotations

import pytest

from backend_app.contracts import OrganizationRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.settings.service import (
    CallerIdentity,
    SettingsAccessDenied,
    SettingsService,
)
from backend_app.settings.store import InMemorySettingsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    return store


def _caller_owner() -> CallerIdentity:
    return CallerIdentity(
        org_id="org_acme",
        user_id="usr_sarah",
        roles=("employee",),
        permission_scopes=("runtime:use",),
    )


def _caller_admin() -> CallerIdentity:
    return CallerIdentity(
        org_id="org_acme",
        user_id="usr_admin",
        roles=("admin",),
        permission_scopes=("runtime:use", "admin:users"),
    )


def _caller_non_admin() -> CallerIdentity:
    return CallerIdentity(
        org_id="org_acme",
        user_id="usr_bob",
        roles=("employee",),
        permission_scopes=("runtime:use",),
    )


def _make_service() -> tuple[
    SettingsService, InMemorySettingsStore, InMemoryIdentityStore
]:
    identity = _seeded_identity()
    store = InMemorySettingsStore()
    service = SettingsService(store=store, identity_store=identity)
    return service, store, identity


# ---------------------------------------------------------------------------
# User namespace ACL
# ---------------------------------------------------------------------------


class TestUserAcl:
    def test_owner_can_read_and_patch(self) -> None:
        service, _, _ = _make_service()
        caller = _caller_owner()
        service.patch_user_namespace(
            caller=caller,
            target_user_id=caller.user_id,
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": True}},
        )
        got = service.get_user_namespace(
            caller=caller,
            target_user_id=caller.user_id,
            namespace="notifications",
        )
        assert got is not None
        assert got.settings["destinations_enabled"] == {"inbox": True}

    def test_non_owner_read_forbidden(self) -> None:
        service, _, _ = _make_service()
        caller = _caller_non_admin()
        with pytest.raises(SettingsAccessDenied):
            service.get_user_namespace(
                caller=caller,
                target_user_id="usr_sarah",
                namespace="notifications",
            )

    def test_non_owner_patch_forbidden(self) -> None:
        service, _, _ = _make_service()
        caller = _caller_non_admin()
        with pytest.raises(SettingsAccessDenied):
            service.patch_user_namespace(
                caller=caller,
                target_user_id="usr_sarah",
                namespace="notifications",
                patch={"destinations_enabled": {"inbox": False}},
            )


# ---------------------------------------------------------------------------
# Tenant namespace ACL
# ---------------------------------------------------------------------------


class TestTenantAcl:
    def test_admin_scope_grants_access(self) -> None:
        service, _, _ = _make_service()
        admin = _caller_admin()
        service.patch_tenant_namespace(
            caller=admin,
            namespace="security.webhooks",
            patch={"default_hmac_on": False},
        )

    def test_owner_role_grants_access(self) -> None:
        # Roles-based admin path: even without ``admin:users`` scope,
        # the ``owner`` role lets the caller through (cross-audit §1.3).
        service, _, _ = _make_service()
        owner_role = CallerIdentity(
            org_id="org_acme",
            user_id="usr_owner",
            roles=("owner",),
            permission_scopes=("runtime:use",),
        )
        service.patch_tenant_namespace(
            caller=owner_role,
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": False}},
        )

    def test_non_admin_read_forbidden(self) -> None:
        service, _, _ = _make_service()
        with pytest.raises(SettingsAccessDenied):
            service.get_tenant_namespace(
                caller=_caller_non_admin(),
                namespace="notifications",
            )

    def test_non_admin_patch_forbidden(self) -> None:
        service, _, _ = _make_service()
        with pytest.raises(SettingsAccessDenied):
            service.patch_tenant_namespace(
                caller=_caller_non_admin(),
                namespace="security.webhooks",
                patch={"default_hmac_on": False},
            )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_appended_on_user_patch(self) -> None:
        service, _, identity = _make_service()
        service.patch_user_namespace(
            caller=_caller_owner(),
            target_user_id="usr_sarah",
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": True}},
        )
        events = identity.list_identity_audit(org_id="org_acme")
        kinds = [e.action for e in events]
        assert "settings.user.notifications.update" in kinds
        evt = next(
            e for e in events if e.action == "settings.user.notifications.update"
        )
        assert evt.actor_user_id == "usr_sarah"
        assert evt.subject_user_id == "usr_sarah"
        assert "destinations_enabled.inbox" in evt.metadata["diff_paths"]

    def test_audit_appended_on_tenant_patch(self) -> None:
        service, _, identity = _make_service()
        service.patch_tenant_namespace(
            caller=_caller_admin(),
            namespace="security.webhooks",
            patch={"default_hmac_on": False, "max_secret_age_days": 30},
        )
        events = identity.list_identity_audit(org_id="org_acme")
        evt = next(
            e
            for e in events
            if e.action == "settings.workspace.security.webhooks.update"
        )
        assert evt.actor_user_id == "usr_admin"
        assert evt.subject_user_id is None
        diff_paths = evt.metadata["diff_paths"]
        assert "default_hmac_on" in diff_paths
        assert "max_secret_age_days" in diff_paths


# ---------------------------------------------------------------------------
# Deep-merge preservation
# ---------------------------------------------------------------------------


class TestDeepMergePreservesHome:
    def test_home_preferences_survive_notifications_patch(self) -> None:
        """The Phase 2 + P9-A2 ``home.*`` block must not be touched.

        This is the single-source-of-truth invariant: the
        user_preferences JSONB blob is one row, namespaces are
        top-level keys, and Settings only writes its own key.
        """

        service, store, _ = _make_service()
        # Seed pre-existing home preferences (as Phase 2 + P9-A2 would).
        store.user_preferences[("org_acme", "usr_sarah")] = {
            "home": {
                "activity_window_hours": 72,
                "last_visit_iso": "2026-05-17T09:00:00+00:00",
            }
        }

        service.patch_user_namespace(
            caller=_caller_owner(),
            target_user_id="usr_sarah",
            namespace="notifications",
            patch={
                "destinations_enabled": {"inbox": True, "home": False},
            },
        )

        blob = store.user_preferences[("org_acme", "usr_sarah")]
        # Home preferences identical to seed — no clobber.
        assert blob["home"] == {
            "activity_window_hours": 72,
            "last_visit_iso": "2026-05-17T09:00:00+00:00",
        }
        # Notifications wrote alongside.
        assert blob["notifications"]["destinations_enabled"] == {
            "inbox": True,
            "home": False,
        }
