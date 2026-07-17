"""Tests for backend-facade deployment profile loader."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.deployment_profile import (
    DeploymentProfileError,
    DeploymentProfileLoader,
)
from backend_facade.settings import FacadeSettings


class TestDeploymentProfileLoader:
    def test_default_in_dev_is_saas_multi_tenant_with_dev_bypass_allowed(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"FACADE_ENVIRONMENT": "development"}
        )

        assert profile.name == "saas_multi_tenant"
        assert profile.toggles.dev_auth_bypass_allowed is True
        assert profile.toggles.require_kms_token_vault is False

    def test_explicit_saas_profile_locks_down_dev_bypass(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant"}
        )

        assert profile.name == "saas_multi_tenant"
        assert profile.toggles.dev_auth_bypass_allowed is False
        assert profile.toggles.require_kms_token_vault is True
        assert profile.toggles.siem_export_required is True

    def test_single_tenant_managed_requires_kms_and_field_encryption(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_managed"}
        )

        assert profile.name == "single_tenant_managed"
        assert profile.toggles.require_kms_token_vault is True
        assert profile.toggles.require_field_level_encryption is True
        assert profile.toggles.allow_self_signup is False

    def test_single_tenant_self_hosted_disables_embedded_provider_keys(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_self_hosted"}
        )

        assert profile.name == "single_tenant_self_hosted"
        assert profile.toggles.allow_embedded_provider_keys is False
        assert profile.toggles.allow_vendor_telemetry is False

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

    def test_dev_bypass_with_desktop_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError) as exc:
            DeploymentProfileLoader.load(
                env={
                    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
                    "DEV_AUTH_BYPASS": "true",
                }
            )

        assert "single_user_desktop" in str(exc.value)

    def test_unknown_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError) as exc:
            DeploymentProfileLoader.load(
                env={"ENTERPRISE_DEPLOYMENT_PROFILE": "garbage"}
            )

        assert "Unknown" in str(exc.value)
        assert "saas_multi_tenant" in str(exc.value)

    def test_missing_profile_in_production_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError) as exc:
            DeploymentProfileLoader.load(env={"FACADE_ENVIRONMENT": "production"})

        assert "ENTERPRISE_DEPLOYMENT_PROFILE" in str(exc.value)
        assert "production" in str(exc.value)

    def test_dev_bypass_with_production_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError) as exc:
            DeploymentProfileLoader.load(
                env={
                    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_managed",
                    "DEV_AUTH_BYPASS": "true",
                    "FACADE_ENVIRONMENT": "development",
                }
            )

        assert "DEV_AUTH_BYPASS" in str(exc.value)
        assert "single_tenant_managed" in str(exc.value)

    def test_staging_without_profile_uses_locked_down_saas_defaults(self) -> None:
        """Staging is not production but it should not get dev relaxations."""

        profile = DeploymentProfileLoader.load(env={"FACADE_ENVIRONMENT": "staging"})

        assert profile.name == "saas_multi_tenant"
        assert profile.toggles.dev_auth_bypass_allowed is False

    def test_toggles_hash_is_stable_and_short(self) -> None:
        profile1 = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant"}
        )
        profile2 = DeploymentProfileLoader.load(
            env={"ENTERPRISE_DEPLOYMENT_PROFILE": "saas_multi_tenant"}
        )

        assert profile1.toggles_hash() == profile2.toggles_hash()
        assert len(profile1.toggles_hash()) == 8


class TestFacadeHealthExposesProfile:
    def test_health_returns_resolved_profile_and_hash(self) -> None:
        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "backend-facade"
        assert body["deployment_profile"] == "saas_multi_tenant"
        assert len(body["feature_toggles_hash"]) == 8


class TestFacadeAuthBypassRespectsProfile:
    def test_dev_bypass_rejected_when_profile_disallows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Profile takes precedence over DEV_AUTH_BYPASS even in dev env.

        In a managed/self-hosted deploy, the operator may have left
        ``FACADE_ENVIRONMENT=development`` by accident — RBAC must still
        refuse the bypass because the profile says no.
        """

        # A saas_multi_tenant profile (explicit) disables dev_auth_bypass even
        # when FACADE_ENVIRONMENT=development. With env+bypass set but the
        # explicit profile present, _enforce_consistency would actually raise
        # DeploymentProfileError at load — so app boot would fail closed.
        monkeypatch.setenv("ENTERPRISE_DEPLOYMENT_PROFILE", "saas_multi_tenant")
        monkeypatch.setenv("FACADE_ENVIRONMENT", "development")
        # No DEV_AUTH_BYPASS set; the auth path should not bypass.
        monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
        monkeypatch.delenv("ENTERPRISE_AUTH_SECRET", raising=False)

        client = TestClient(create_app(FacadeSettings()))

        response = client.get("/v1/skills")

        assert response.status_code == 401
