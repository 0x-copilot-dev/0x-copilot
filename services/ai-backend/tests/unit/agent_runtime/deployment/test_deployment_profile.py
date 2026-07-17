"""Tests for ai-backend deployment profile loader."""

from __future__ import annotations

import pytest

from agent_runtime.deployment import (
    DeploymentProfileError,
    DeploymentProfileLoader,
)


class TestDeploymentProfileLoader:
    def test_default_in_dev_is_saas_multi_tenant(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"RUNTIME_ENVIRONMENT": "development"}
        )

        assert profile.name == "saas_multi_tenant"
        assert profile.toggles.dev_auth_bypass_allowed is True
        assert profile.toggles.enforce_rls is False

    def test_each_explicit_profile_loads_with_documented_defaults(self) -> None:
        for profile_name in (
            "saas_multi_tenant",
            "single_tenant_managed",
            "single_tenant_self_hosted",
        ):
            profile = DeploymentProfileLoader.load(
                env={"ENTERPRISE_DEPLOYMENT_PROFILE": profile_name}
            )

            assert profile.name == profile_name
            assert profile.toggles.dev_auth_bypass_allowed is False
            assert profile.toggles.require_kms_token_vault is True
            assert profile.toggles.enforce_rls is True

    def test_single_tenant_managed_requires_field_level_encryption(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_managed"}
        )

        assert profile.toggles.require_field_level_encryption is True

    def test_single_user_desktop_relaxes_kms_but_not_dev_bypass(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop"}
        )

        assert profile.name == "single_user_desktop"
        assert profile.toggles.require_kms_token_vault is False
        assert profile.toggles.dev_auth_bypass_allowed is False
        assert profile.toggles.allow_self_signup is True
        assert profile.toggles.enforce_rls is False
        assert profile.toggles.siem_export_required is False
        assert profile.toggles.allow_vendor_telemetry is False
        assert profile.toggles.pricing_primary_source == "litellm"

    def test_dev_bypass_with_desktop_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(
                env={
                    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
                    "DEV_AUTH_BYPASS": "true",
                }
            )

    def test_unknown_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(
                env={"ENTERPRISE_DEPLOYMENT_PROFILE": "garbage"}
            )

    def test_missing_profile_in_production_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(env={"RUNTIME_ENVIRONMENT": "production"})

    def test_dev_bypass_with_production_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(
                env={
                    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_managed",
                    "DEV_AUTH_BYPASS": "true",
                    "RUNTIME_ENVIRONMENT": "development",
                }
            )

    def test_toggles_hash_is_stable(self) -> None:
        profile1 = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant"}
        )
        profile2 = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant"}
        )

        assert profile1.toggles_hash() == profile2.toggles_hash()
        assert len(profile1.toggles_hash()) == 8
