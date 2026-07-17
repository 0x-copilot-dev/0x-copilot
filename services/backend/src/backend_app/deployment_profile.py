"""Resolve and validate the active deployment profile for backend.

The profile is set once at process startup via ``ENTERPRISE_DEPLOYMENT_PROFILE``
and dictates the safety defaults for the rest of the process.

Mirrors the surface in backend-facade and ai-backend; the modules are not
shared because monorepo rules forbid cross-service Python imports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys

from copilot_service_contracts.deployment_profile import (
    ALLOWED_PROFILES,
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SAAS_MULTI_TENANT,
)
from pydantic import BaseModel, ConfigDict


_SERVICE_NAME = "backend"


class DeploymentProfileError(RuntimeError):
    """Raised when the deployment profile is missing or inconsistent."""


class DeploymentFeatureToggles(BaseModel):
    model_config = ConfigDict(frozen=True)

    allow_embedded_provider_keys: bool
    allow_self_signup: bool
    allow_vendor_telemetry: bool
    default_retention_days: int
    dev_auth_bypass_allowed: bool
    enforce_rls: bool
    require_field_level_encryption: bool
    require_kms_token_vault: bool
    siem_export_required: bool


class DeploymentProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    toggles: DeploymentFeatureToggles

    def toggles_hash(self) -> str:
        canonical = json.dumps(
            self.toggles.model_dump(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


class DeploymentProfileLoader:
    _DEFAULTS_BY_PROFILE: dict[str, dict[str, object]] = {
        "saas_multi_tenant": {
            "allow_embedded_provider_keys": True,
            "allow_self_signup": True,
            "allow_vendor_telemetry": True,
            "default_retention_days": 365,
            "dev_auth_bypass_allowed": False,
            "enforce_rls": True,
            "require_field_level_encryption": False,
            "require_kms_token_vault": True,
            "siem_export_required": True,
        },
        "single_tenant_managed": {
            "allow_embedded_provider_keys": True,
            "allow_self_signup": False,
            "allow_vendor_telemetry": False,
            "default_retention_days": 365,
            "dev_auth_bypass_allowed": False,
            "enforce_rls": True,
            "require_field_level_encryption": True,
            "require_kms_token_vault": True,
            "siem_export_required": True,
        },
        "single_tenant_self_hosted": {
            "allow_embedded_provider_keys": False,
            "allow_self_signup": False,
            "allow_vendor_telemetry": False,
            "default_retention_days": 365,
            "dev_auth_bypass_allowed": False,
            "enforce_rls": True,
            "require_field_level_encryption": True,
            "require_kms_token_vault": True,
            "siem_export_required": True,
        },
        # Desktop app: one person, one workspace, bundled local Postgres.
        # Local Fernet vault instead of KMS (no cloud KMS on a laptop);
        # RLS/SIEM off because the OS user boundary is the tenant boundary.
        # Self-signup ON — the user creates their own workspace via
        # Google/wallet at first launch. Magic-link/email is disabled by
        # the desktop composition root (``backend_app.desktop_app``), not
        # by a toggle here.
        "single_user_desktop": {
            "allow_embedded_provider_keys": True,
            "allow_self_signup": True,
            "allow_vendor_telemetry": False,
            "default_retention_days": 365,
            "dev_auth_bypass_allowed": False,
            "enforce_rls": False,
            "require_field_level_encryption": False,
            "require_kms_token_vault": False,
            "siem_export_required": False,
        },
    }

    _DEV_DEFAULT = {
        "allow_embedded_provider_keys": True,
        "allow_self_signup": True,
        "allow_vendor_telemetry": True,
        "default_retention_days": 365,
        "dev_auth_bypass_allowed": True,
        "enforce_rls": False,
        "require_field_level_encryption": False,
        "require_kms_token_vault": False,
        "siem_export_required": False,
    }

    @classmethod
    def load(cls, env: dict[str, str] | None = None) -> DeploymentProfile:
        env = env if env is not None else dict(os.environ)
        raw = env.get(ENV_DEPLOYMENT_PROFILE, "").strip().lower()
        environment = env.get("BACKEND_ENVIRONMENT", "development").strip().lower()

        if not raw:
            if environment == "production":
                raise DeploymentProfileError(
                    f"{ENV_DEPLOYMENT_PROFILE} is required in production; "
                    f"valid values: {sorted(ALLOWED_PROFILES)}"
                )
            defaults = (
                cls._DEV_DEFAULT
                if environment == "development"
                else cls._DEFAULTS_BY_PROFILE[PROFILE_SAAS_MULTI_TENANT]
            )
            return DeploymentProfile(
                name=PROFILE_SAAS_MULTI_TENANT,
                toggles=DeploymentFeatureToggles(**defaults),
            )

        if raw not in ALLOWED_PROFILES:
            raise DeploymentProfileError(
                f"Unknown {ENV_DEPLOYMENT_PROFILE}={raw!r}; valid values: "
                f"{sorted(ALLOWED_PROFILES)}"
            )

        toggles = DeploymentFeatureToggles(**cls._DEFAULTS_BY_PROFILE[raw])
        cls._enforce_consistency(raw, env, toggles)
        return DeploymentProfile(name=raw, toggles=toggles)

    @classmethod
    def _enforce_consistency(
        cls,
        profile_name: str,
        env: dict[str, str],
        toggles: DeploymentFeatureToggles,
    ) -> None:
        bypass_set = env.get("DEV_AUTH_BYPASS", "").strip().lower() == "true"
        if bypass_set and not toggles.dev_auth_bypass_allowed:
            raise DeploymentProfileError(
                f"DEV_AUTH_BYPASS=true is not allowed under "
                f"{ENV_DEPLOYMENT_PROFILE}={profile_name!r}; remove either the "
                f"profile or the bypass env var."
            )

    @classmethod
    def fail_closed_at_boot(cls, error: DeploymentProfileError) -> None:
        logger = logging.getLogger(_SERVICE_NAME)
        logger.error("deployment_profile_invalid: %s", error)
        sys.stderr.write(f"FATAL: {_SERVICE_NAME}: {error}\n")
        raise SystemExit(78)


def resolve_or_exit() -> DeploymentProfile:
    try:
        return DeploymentProfileLoader.load()
    except DeploymentProfileError as exc:
        DeploymentProfileLoader.fail_closed_at_boot(exc)
        raise


def log_profile(profile: DeploymentProfile) -> None:
    logger = logging.getLogger(_SERVICE_NAME)
    logger.info(
        "deployment_profile=%s toggles_hash=%s",
        profile.name,
        profile.toggles_hash(),
    )
