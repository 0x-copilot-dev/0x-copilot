"""Tests for backend service deployment profile loader."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.deployment_profile import (
    DeploymentProfileError,
    DeploymentProfileLoader,
)


class TestDeploymentProfileLoader:
    def test_default_in_dev_is_saas_multi_tenant(self) -> None:
        profile = DeploymentProfileLoader.load(
            env={"BACKEND_ENVIRONMENT": "development"}
        )

        assert profile.name == "saas_multi_tenant"
        assert profile.toggles.dev_auth_bypass_allowed is True

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
            assert profile.toggles.siem_export_required is True

    def test_unknown_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(
                env={"ENTERPRISE_DEPLOYMENT_PROFILE": "garbage"}
            )

    def test_missing_profile_in_production_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(env={"BACKEND_ENVIRONMENT": "production"})

    def test_dev_bypass_with_production_profile_fails_closed(self) -> None:
        with pytest.raises(DeploymentProfileError):
            DeploymentProfileLoader.load(
                env={
                    "ENTERPRISE_DEPLOYMENT_PROFILE": "single_tenant_self_hosted",
                    "DEV_AUTH_BYPASS": "true",
                }
            )


class TestBackendHealthExposesProfile:
    def test_health_returns_resolved_profile_and_hash(self) -> None:
        client = TestClient(create_app())

        response = client.get("/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "backend"
        assert body["deployment_profile"] == "saas_multi_tenant"
        assert len(body["feature_toggles_hash"]) == 8
