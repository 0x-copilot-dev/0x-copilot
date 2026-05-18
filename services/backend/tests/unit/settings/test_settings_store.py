"""Tests for ``backend_app.settings.store.InMemorySettingsStore``.

Coverage:

* get/patch round-trip on user + tenant namespaces.
* Deep-merge preserves untouched sibling keys.
* The existing ``home.*`` JSONB layout (Phase 2 + P9-A2) survives a
  PATCH against ``notifications`` — the core single-source-of-truth
  guarantee for Phase 12.
* Tenant + user namespace isolation: writes against one row never
  leak across tenant or user.
"""

from __future__ import annotations

from backend_app.settings.store import InMemorySettingsStore


class TestUserNamespace:
    def test_patch_then_get_round_trip(self) -> None:
        store = InMemorySettingsStore()
        record = store.patch_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
            patch={
                "destinations_enabled": {"inbox": True, "home": False},
                "quiet_hours": {
                    "enabled": True,
                    "from_local": "22:00",
                    "to_local": "07:00",
                    "tz": "America/Los_Angeles",
                },
            },
        )
        assert record.namespace == "notifications"
        assert record.settings["destinations_enabled"]["inbox"] is True

        fetched = store.get_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
        )
        assert fetched is not None
        assert fetched.settings == record.settings

    def test_patch_deep_merges_siblings(self) -> None:
        store = InMemorySettingsStore()
        store.patch_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
            patch={
                "destinations_enabled": {"inbox": True, "home": True},
                "quiet_hours": {
                    "enabled": False,
                    "from_local": "20:00",
                    "to_local": "08:00",
                    "tz": "UTC",
                },
            },
        )
        # Second patch only flips one destination — the rest of
        # ``destinations_enabled`` survives.
        updated = store.patch_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
            patch={"destinations_enabled": {"home": False}},
        )
        assert updated.settings["destinations_enabled"] == {
            "inbox": True,
            "home": False,
        }
        # quiet_hours untouched.
        assert updated.settings["quiet_hours"]["tz"] == "UTC"

    def test_existing_home_prefs_preserved_across_notifications_patch(self) -> None:
        """Phase 2 ``home.activity_window_hours`` + P9-A2
        ``home.last_visit_iso`` must survive a notifications PATCH.

        Single source of truth: the user_preferences JSONB blob is one
        row; namespace = top-level dict key. Patching one key MUST NOT
        clobber the others.
        """

        store = InMemorySettingsStore()
        # Simulate the existing ``home.*`` block written by Phase 2 +
        # P9-A2.
        store.user_preferences[("org_acme", "usr_sarah")] = {
            "home": {
                "activity_window_hours": 72,
                "last_visit_iso": "2026-05-17T09:00:00+00:00",
            }
        }

        store.patch_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": False}},
        )

        # ``home.*`` survives untouched.
        blob = store.user_preferences[("org_acme", "usr_sarah")]
        assert blob["home"] == {
            "activity_window_hours": 72,
            "last_visit_iso": "2026-05-17T09:00:00+00:00",
        }
        # notifications now lives alongside home.
        assert blob["notifications"]["destinations_enabled"] == {"inbox": False}

    def test_user_isolation(self) -> None:
        store = InMemorySettingsStore()
        store.patch_user_namespace(
            org_id="org_acme",
            user_id="usr_sarah",
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": True}},
        )
        # Different user, same org — no read.
        assert (
            store.get_user_namespace(
                org_id="org_acme",
                user_id="usr_bob",
                namespace="notifications",
            )
            is None
        )

    def test_get_missing_returns_none(self) -> None:
        store = InMemorySettingsStore()
        assert (
            store.get_user_namespace(
                org_id="org_acme",
                user_id="usr_sarah",
                namespace="notifications",
            )
            is None
        )


class TestTenantNamespace:
    def test_patch_then_get_round_trip(self) -> None:
        store = InMemorySettingsStore()
        record = store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
            patch={
                "default_hmac_on": False,
                "require_ip_allowlist": True,
                "max_secret_age_days": 90,
            },
            actor_user_id="usr_admin",
        )
        assert record.namespace == "security.webhooks"
        assert record.settings["max_secret_age_days"] == 90
        assert record.updated_by_user_id == "usr_admin"

        fetched = store.get_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
        )
        assert fetched is not None
        assert fetched.updated_by_user_id == "usr_admin"

    def test_patch_deep_merges(self) -> None:
        store = InMemorySettingsStore()
        store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
            patch={
                "default_hmac_on": True,
                "require_ip_allowlist": False,
                "max_secret_age_days": 0,
            },
            actor_user_id="usr_admin",
        )
        updated = store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
            patch={"max_secret_age_days": 45},
            actor_user_id="usr_admin",
        )
        # default_hmac_on untouched.
        assert updated.settings == {
            "default_hmac_on": True,
            "require_ip_allowlist": False,
            "max_secret_age_days": 45,
        }

    def test_tenant_isolation(self) -> None:
        store = InMemorySettingsStore()
        store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
            patch={"default_hmac_on": False},
            actor_user_id="usr_admin",
        )
        # Different tenant — no read.
        assert (
            store.get_tenant_namespace(
                tenant_id="org_zeta",
                namespace="security.webhooks",
            )
            is None
        )

    def test_namespace_isolation_within_tenant(self) -> None:
        store = InMemorySettingsStore()
        store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="security.webhooks",
            patch={"default_hmac_on": False},
            actor_user_id="usr_admin",
        )
        store.patch_tenant_namespace(
            tenant_id="org_acme",
            namespace="notifications",
            patch={"destinations_enabled": {"inbox": False}},
            actor_user_id="usr_admin",
        )
        # Each namespace has its own row.
        webhooks = store.get_tenant_namespace(
            tenant_id="org_acme", namespace="security.webhooks"
        )
        notifs = store.get_tenant_namespace(
            tenant_id="org_acme", namespace="notifications"
        )
        assert webhooks is not None
        assert notifs is not None
        assert webhooks.settings == {"default_hmac_on": False}
        assert notifs.settings == {"destinations_enabled": {"inbox": False}}
