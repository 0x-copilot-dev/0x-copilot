"""Gating + limit-profile resolution tests."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.sandbox.config import (
    RemoteSandboxConfig,
    SandboxLimitProfiles,
)
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)


class TestGating:
    def test_disabled_by_default(self) -> None:
        config = RemoteSandboxConfig.from_env({})
        assert config.is_active is False
        assert config.provider is None

    def test_enabled_without_provider_stays_disabled(self) -> None:
        config = RemoteSandboxConfig.from_env({"RUNTIME_ENABLE_REMOTE_SANDBOX": "true"})
        assert config.is_active is False

    def test_unsupported_provider_fails_closed(self) -> None:
        config = RemoteSandboxConfig.from_env(
            {
                "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
                "RUNTIME_SANDBOX_PROVIDER": "e2b",
            }
        )
        assert config.is_active is False
        assert config.provider is None

    def test_active_when_enabled_and_supported(self) -> None:
        config = RemoteSandboxConfig.from_env(
            {
                "RUNTIME_ENABLE_REMOTE_SANDBOX": "1",
                "RUNTIME_SANDBOX_PROVIDER": "langsmith",
                "RUNTIME_SANDBOX_REGION": "us-east-1",
            }
        )
        assert config.is_active is True
        assert config.provider is SandboxProviderId.LANGSMITH
        assert config.region == "us-east-1"

    def test_region_dropped_when_disabled(self) -> None:
        config = RemoteSandboxConfig.from_env({"RUNTIME_SANDBOX_REGION": "us-east-1"})
        assert config.region is None


class TestLimitProfiles:
    def test_desktop_v1_present(self) -> None:
        profile = SandboxLimitProfiles.get("desktop_v1")
        assert profile.name == "desktop_v1"
        assert profile.commands_per_session == 64
        assert profile.max_upload_total_bytes == 512 * 1024 * 1024

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(SandboxError) as excinfo:
            SandboxLimitProfiles.get("nope")
        assert excinfo.value.code is SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED

    def test_config_resolves_limits(self) -> None:
        config = RemoteSandboxConfig.from_env(
            {
                "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
                "RUNTIME_SANDBOX_PROVIDER": "langsmith",
            }
        )
        assert config.resolve_limits().name == "desktop_v1"
