"""Shared test helpers for the sandbox capability tests."""

from __future__ import annotations

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig


def active_config() -> RemoteSandboxConfig:
    """An enabled, langsmith-selected config for lifecycle/registry tests."""

    return RemoteSandboxConfig.from_env(
        {
            "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
            "RUNTIME_SANDBOX_PROVIDER": "langsmith",
            "RUNTIME_SANDBOX_REGION": "test-region",
        }
    )
