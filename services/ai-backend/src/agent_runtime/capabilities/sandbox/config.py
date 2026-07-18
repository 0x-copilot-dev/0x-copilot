"""Deployment-trusted configuration and limit profiles for remote sandboxes.

Everything the model is forbidden to influence — whether the capability is
enabled at all, which provider is selected, region, and the hard lifecycle
ceilings — is resolved here, once, from the process environment. The model
never reaches this module.

Gating: the capability is OFF unless ``RUNTIME_ENABLE_REMOTE_SANDBOX`` is truthy
AND a supported provider is configured. A missing or unsupported provider has
no host fallback — the capability simply stays absent.
"""

from __future__ import annotations

from collections.abc import Mapping
import os

from pydantic import Field

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)
from agent_runtime.execution.contracts import RuntimeContract


class _EnvFields:
    """Environment variable names (single source of truth)."""

    ENABLE = "RUNTIME_ENABLE_REMOTE_SANDBOX"
    PROVIDER = "RUNTIME_SANDBOX_PROVIDER"
    REGION = "RUNTIME_SANDBOX_REGION"
    LIMIT_PROFILE = "RUNTIME_SANDBOX_LIMIT_PROFILE"

    _TRUTHY = frozenset({"1", "true", "yes", "on"})


class SandboxLimitProfile(RuntimeContract):
    """Hard lifecycle/resource ceilings applied to every session.

    Defaults and ceilings follow the AC7 PRD "Lifecycle and limits" table. The
    model cannot raise these; deployment policy may only lower them.
    """

    name: str = Field(min_length=1)
    provisioning_timeout_s: int = Field(default=60, ge=1, le=120)
    command_timeout_s: int = Field(default=120, ge=1, le=15 * 60)
    session_wall_time_s: int = Field(default=15 * 60, ge=1, le=60 * 60)
    idle_timeout_s: int = Field(default=5 * 60, ge=1, le=15 * 60)
    commands_per_session: int = Field(default=64, ge=1, le=256)
    combined_command_preview_bytes: int = Field(default=64 * 1024, ge=1, le=256 * 1024)
    download_file_count: int = Field(default=10_000, ge=1, le=25_000)
    download_changed_bytes: int = Field(
        default=512 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024
    )
    cleanup_confirmation_s: int = Field(default=30, ge=1, le=2 * 60)
    # Upload snapshot ceilings (PRD "Workspace snapshot").
    max_upload_files: int = Field(default=10_000, ge=1, le=25_000)
    max_upload_total_bytes: int = Field(
        default=512 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024
    )
    max_upload_file_bytes: int = Field(default=64 * 1024 * 1024, ge=1)


#: Named profiles resolvable by ``limit_profile``. ``desktop_v1`` is the only
#: profile AC7 ships; deployments add lower-ceiling profiles here.
_LIMIT_PROFILES: dict[str, SandboxLimitProfile] = {
    "desktop_v1": SandboxLimitProfile(name="desktop_v1"),
}


class SandboxLimitProfiles:
    """Resolver for named limit profiles."""

    @staticmethod
    def get(name: str) -> SandboxLimitProfile:
        """Return the named profile or raise a typed error for an unknown name."""

        profile = _LIMIT_PROFILES.get(name)
        if profile is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
                f"Sandbox limit profile '{name}' is not configured.",
            )
        return profile

    @staticmethod
    def names() -> tuple[str, ...]:
        """Return the registered profile names."""

        return tuple(_LIMIT_PROFILES)


class RemoteSandboxConfig(RuntimeContract):
    """Resolved, deployment-trusted sandbox configuration for one process.

    ``enabled=False`` means the capability is absent: no provider is
    constructed, no execute tool is registered, and the seam returns ``None``.
    """

    enabled: bool = False
    provider: SandboxProviderId | None = None
    region: str | None = None
    limit_profile: str = "desktop_v1"

    @property
    def is_active(self) -> bool:
        """Whether a provider-backed capability should be constructed."""

        return self.enabled and self.provider is not None

    def resolve_limits(self) -> SandboxLimitProfile:
        """Resolve the configured limit profile."""

        return SandboxLimitProfiles.get(self.limit_profile)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RemoteSandboxConfig:
        """Resolve config from the environment, failing closed on bad input.

        A truthy enable flag with a missing/unsupported provider yields a
        disabled config (there is no host fallback), so a misconfiguration can
        never silently enable an unintended execution path.
        """

        source = env if env is not None else os.environ
        enabled = _read_bool(source, _EnvFields.ENABLE)
        provider = _read_provider(source)
        region = (source.get(_EnvFields.REGION) or "").strip() or None
        limit_profile = (
            source.get(_EnvFields.LIMIT_PROFILE) or ""
        ).strip() or "desktop_v1"
        active = enabled and provider is not None
        return cls(
            enabled=active,
            provider=provider if active else None,
            region=region if active else None,
            limit_profile=limit_profile,
        )


def _read_bool(source: Mapping[str, str], key: str) -> bool:
    return (source.get(key) or "").strip().lower() in _EnvFields._TRUTHY


def _read_provider(source: Mapping[str, str]) -> SandboxProviderId | None:
    raw = (source.get(_EnvFields.PROVIDER) or "").strip().lower()
    if not raw:
        return None
    try:
        return SandboxProviderId(raw)
    except ValueError:
        # Unsupported provider name → fail closed (capability stays absent).
        return None
