"""PR B2 / 8.0.3f — privacy-settings snapshot consumed by the AI runtime."""

from __future__ import annotations

from agent_runtime.capabilities.tools.privacy import (
    DataResidencyRegion,
    PrivacySettingsSnapshot,
)


class TestSnapshotFromResponse:
    def test_parses_full_response(self) -> None:
        snap = PrivacySettingsSnapshot.from_response(
            {
                "scope": "user",
                "org_id": "org_acme",
                "user_id": "usr_sarah",
                "training_opt_out": False,
                "region": "eu-west-1",
                "retention_days": 30,
                "share_metadata": False,
                "memory_enabled": False,
                "updated_at": "2026-05-06T12:00:00+00:00",
            }
        )
        assert snap.org_id == "org_acme"
        assert snap.user_id == "usr_sarah"
        assert snap.training_opt_out is False
        assert snap.region is DataResidencyRegion.EU_WEST_1
        assert snap.retention_days == 30
        assert snap.share_metadata is False
        assert snap.memory_enabled is False

    def test_missing_user_id_means_workspace_scope(self) -> None:
        snap = PrivacySettingsSnapshot.from_response(
            {"org_id": "org_acme", "user_id": None}
        )
        assert snap.user_id is None
        # Defaults fall through.
        assert snap.training_opt_out is True
        assert snap.region is None
        assert snap.share_metadata is True
        assert snap.memory_enabled is True

    def test_invalid_region_drops_silently(self) -> None:
        snap = PrivacySettingsSnapshot.from_response(
            {"org_id": "org_acme", "region": "antarctica-1"}
        )
        assert snap.region is None

    def test_invalid_retention_days_drops_silently(self) -> None:
        for value in (-1, 0, "30", True):
            snap = PrivacySettingsSnapshot.from_response(
                {"org_id": "org_acme", "retention_days": value}
            )
            assert snap.retention_days is None


class TestSnapshotConvenienceAccessors:
    def test_default_snapshot_allows_memory_and_metadata(self) -> None:
        snap = PrivacySettingsSnapshot.deployment_default(org_id="org_acme")
        assert snap.memory_writes_allowed() is True
        assert snap.admin_visible_metadata_allowed() is True
        assert snap.provider_do_not_train() is True

    def test_overrides_propagate_through_accessors(self) -> None:
        snap = PrivacySettingsSnapshot.from_response(
            {
                "org_id": "org_acme",
                "user_id": "usr_sarah",
                "training_opt_out": False,
                "share_metadata": False,
                "memory_enabled": False,
            }
        )
        assert snap.memory_writes_allowed() is False
        assert snap.admin_visible_metadata_allowed() is False
        assert snap.provider_do_not_train() is False
