"""Resolve and validate the active deployment profile for backend-facade.

The profile is set once at process startup via ``ENTERPRISE_DEPLOYMENT_PROFILE``
and dictates the safety defaults for the rest of the process. Loader is a
classmethod surface so the result can be cached on ``app.state.deployment``.

The same module exists (with identical surface) in each of the three Python
services. They are not shared because monorepo rules forbid cross-service
imports — only the constants in ``enterprise_service_contracts`` are shared.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys

from enterprise_service_contracts.deployment_profile import (
    ALLOWED_PROFILES,
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SAAS_MULTI_TENANT,
)
from pydantic import BaseModel, ConfigDict


_SERVICE_NAME = "backend-facade"


class DeploymentProfileError(RuntimeError):
    """Raised when the deployment profile is missing or inconsistent.

    The app boot path catches this and exits the process with a non-zero
    status so the operator sees the misconfiguration immediately rather
    than discovering it via an obscure runtime failure.
    """


class DeploymentFeatureToggles(BaseModel):
    """Frozen safety toggles derived from the active profile.

    Each later PR consumes specific toggles; this PR only adds them.
    """

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
    """Resolved profile + toggles for one process."""

    model_config = ConfigDict(frozen=True)

    name: str
    toggles: DeploymentFeatureToggles

    def toggles_hash(self) -> str:
        """Stable short hash of the resolved toggles for ops dashboards."""

        canonical = json.dumps(
            self.toggles.model_dump(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


class DeploymentProfileLoader:
    """Load and validate the deployment profile from process environment."""

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
        # Local Fernet vault instead of KMS; RLS/SIEM off because the OS
        # user boundary is the tenant boundary. Self-signup ON — the user
        # creates their own workspace via Google/wallet at first launch.
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
        # Dev-only relaxations — only applied when the profile is unset AND
        # the environment self-identifies as development.
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
        environment = env.get("FACADE_ENVIRONMENT", "development").strip().lower()

        if not raw:
            # Production fails closed: an unset profile in production almost
            # certainly means the operator forgot to configure it. Every other
            # environment (development, staging, ci, ...) defaults to
            # ``saas_multi_tenant`` — but only ``development`` gets the
            # relaxed dev defaults that allow ``DEV_AUTH_BYPASS=true``.
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
        """Reject env combinations that contradict the profile defaults."""

        bypass_set = env.get("DEV_AUTH_BYPASS", "").strip().lower() == "true"
        if bypass_set and not toggles.dev_auth_bypass_allowed:
            raise DeploymentProfileError(
                f"DEV_AUTH_BYPASS=true is not allowed under "
                f"{ENV_DEPLOYMENT_PROFILE}={profile_name!r}; remove either the "
                f"profile or the bypass env var."
            )

    @classmethod
    def fail_closed_at_boot(cls, error: DeploymentProfileError) -> None:
        """Log the error and exit with code 78 (configuration error, sysexits.h)."""

        logger = logging.getLogger(_SERVICE_NAME)
        logger.error("deployment_profile_invalid: %s", error)
        sys.stderr.write(f"FATAL: {_SERVICE_NAME}: {error}\n")
        raise SystemExit(78)


def resolve_or_exit() -> DeploymentProfile:
    """Resolve the profile or terminate the process if invalid.

    Used at app startup; tests should call ``DeploymentProfileLoader.load``
    directly so they can assert the typed error.
    """

    try:
        return DeploymentProfileLoader.load()
    except DeploymentProfileError as exc:
        DeploymentProfileLoader.fail_closed_at_boot(exc)
        raise  # unreachable; satisfies type checker


def log_profile(profile: DeploymentProfile) -> None:
    """Emit the canonical startup line so logs are greppable."""

    logger = logging.getLogger(_SERVICE_NAME)
    logger.info(
        "deployment_profile=%s toggles_hash=%s",
        profile.name,
        profile.toggles_hash(),
    )
